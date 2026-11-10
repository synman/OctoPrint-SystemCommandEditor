# coding=utf-8
from __future__ import absolute_import

__author__ = "Shell M. Shrader, originally Marc Hannappel <salandora@gmail.com>"
__license__ = "GNU Affero General Public License http://www.gnu.org/licenses/agpl.html"

import copy
import io
import json
import os
import socket
import sys
import threading
import time

import flask
import yaml

import octoprint.events
import octoprint.plugin
from octoprint.access.permissions import Permissions

from .adapters import (
    MqttCache,
    carried,
    command_block,
    condition_effect,
    is_builtin_read,
    is_builtin_run,
    is_run_spec,
    read_builtin,
    read_outcome,
    run_builtin,
    run_call,
    run_with_timeout,
    state_word,
)
from .convert import command_count, convert, link_devices, live_plug_reads, place_readouts, power_icons, role_icons
from .presets import (
    SYSTEM_SWITCHES,
    action_of,
    add_plugin_preset,
    detect,
    detect_maintenance,
    first_start_presets,
    harvest,
    key_for,
    mark_presets,
    maintenance_actions,
    octoprint_presets,
    sample_devices,
    state_list,
    system_switches,
    webcam_presets,
)
from .status import check_devices, run_status

API_ENTRY_KEYS = (
    "name",
    "command",
    "url",
    "confirm",
    "async",
    "fresh_credentials",
    "ignore",
    "icon",
    "icon_color",
    "icon_invert",
    "group",
    "role",
    "value",
    "when",
    "when_control",
    "device",
    "run",
)

# the divider a sample readout's placement goes under on first start
STATUS_SECTION = "Status"

CORE_COMMAND_SETTINGS = {
    "shutdown": "systemShutdownCommand",
    "reboot": "systemRestartCommand",
    "restart": "serverRestartCommand",
    "restart_safe": "serverRestartCommand",
}


# On first start, OctoPrint's own System menu entries are grouped under the host and OctoPrint switches
SYSTEM_GROUPS = {
    action: (switch["id"], role) for switch in SYSTEM_SWITCHES for action, role in switch["members"].items()
}


def printer_state(printer):
    # the printer's state as the menu names it; the first of these that holds wins
    if printer.is_printing():
        return "printing"
    if printer.is_paused():
        return "paused"
    if printer.is_error():
        return "error"
    if printer.is_operational():
        return "ready"
    return "offline"


class SystemCommandEditorPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.StartupPlugin,
):
    # Commands live only in OctoPrint's own system.actions, which the settings dialog saves through the core
    # settings API. The plugin keeps no copy: versions before 2.0 did, and copying it back over system.actions
    # on startup is what made commands vanish.

    def __init__(self):
        # one MQTT cache per plugin instance: each topic is subscribed once for the life of the plugin
        self._mqtt = MqttCache()

    def get_settings_defaults(self):
        # Icons and headings for OctoPrint's own System menu entries, which its config has no place for. These are
        # decoration only; what the entries run stays in OctoPrint's server.commands.
        return {
            "core": {
                "shutdown": {"icon": "fa-solid fa-power-off", "heading": "System", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
                "reboot": {"icon": "fa-solid fa-rotate-right", "heading": "", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
                "restart": {"icon": "fa-solid fa-arrows-rotate", "heading": "", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
                "restart_safe": {"icon": "fa-solid fa-shield-halved", "heading": "", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
            },
            "devices": [],
            "success_toast": False,
            "live_updates": False,
            "live_interval": 5,
            "busy_icon": "fa-solid fa-spinner",
            "presets_loaded": False,
        }

    def get_settings_version(self):
        return 8

    def get_api_commands(self):
        return {"status": ["ids"], "presets": [], "run": ["action"], "states": []}

    def on_api_command(self, command, data):
        if not Permissions.SYSTEM.can():
            flask.abort(403)
        if command == "status":
            return flask.jsonify(results=self._status(data["ids"], data.get("live") is True))
        if command == "run":
            return self._run(data["action"])
        if command == "states":
            # a status line names no state list, so it answers free
            return flask.jsonify(state_list(data.get("read"), self._plugin_manager))
        if command == "presets":
            return flask.jsonify(
                presets=detect(),
                maintenance=detect_maintenance(self._maintenance_env()),
                switches=system_switches(socket.gethostname()),
                plugins=self._plugin_presets() + [webcam_presets(), octoprint_presets(self._snapshot_url())],
            )

    def _status(self, ids, live):
        devices = []
        reads = {}
        skipped = {}
        for device in self._settings.get(["devices"]) or []:
            spec = device.get("read")
            if spec is None:
                devices.append(device)
                continue
            if live and spec.get("live") is False:
                skipped[device.get("id")] = {"state": None, "output": "", "exit": None, "error": None, "skipped": True}
                continue
            # check_devices runs a status string; a read is keyed by its device and its own JSON so the runner can tell
            # them apart, and two devices reading the same thing still get a request context each
            token = json.dumps({"device": device.get("id"), "read": spec}, sort_keys=True)
            reads[token] = spec
            devices.append({**device, "status": token})

        # made here, on the request's thread: check_devices runs the reads on worker threads that have no request. One
        # copy per read, since one copied context pushed on two threads at once fails
        calls = {command: carried(run_call) for command in reads}

        def runner(command, timeout):
            if command in reads:
                spec = reads[command]
                if is_builtin_read(spec):
                    text, error = read_builtin(spec, self._printer)
                    return (None if error else 0), text, error
                return read_outcome(spec, self._plugin_manager, timeout, calls[command], self._mqtt)
            return run_status(command, timeout)

        entries = self._action_entries() + self._core_entries()
        results = check_devices(devices, entries, [i for i in ids if i not in skipped], runner=runner)
        results.update({i: skipped[i] for i in ids if i in skipped})
        return results

    def _run(self, action):
        entry = next(
            (item for item in self._settings.global_get(["system", "actions"]) or [] if item.get("action") == action),
            None,
        )
        if entry is None or entry.get("run") is None:
            flask.abort(404)
        spec = entry["run"]
        if not is_run_spec(spec):
            flask.abort(400, description="unsupported run kind")
        state = printer_state(self._printer)
        if condition_effect(entry.get("when"), state):
            response = flask.jsonify(ok=False, error="Not available while the printer is {}".format(state))
            response.status_code = 409
            return response
        if entry.get("group") and entry.get("when_control"):
            name, word = self._control_state(entry["group"])
            block = command_block(entry, state, word, name)
            if block:
                response = flask.jsonify(ok=False, error=block["reason"])
                response.status_code = 409
                return response
        if is_builtin_run(spec):
            output, error = run_builtin(spec, self._printer, octoprint.events.eventManager())
        else:
            output, error = run_with_timeout(spec, self._plugin_manager, call=carried(run_call))
        return flask.jsonify(ok=error is None, output=output, error=error)

    def _control_state(self, group):
        # the control's name and its state word from one live read; the word is None when unknown
        name = group
        for device in self._settings.get(["devices"]) or []:
            if device.get("id") == group:
                name = device.get("name") or group
        result = self._status([group], True).get(group) or {}
        if result.get("error") or result.get("skipped"):
            return name, None
        return name, state_word(result.get("output"))

    def _plugin_presets(self):
        return harvest(
            self._plugin_manager,
            lambda plugin, key: self._settings.global_get(["plugins", plugin, key]),
        )

    def _snapshot_url(self):
        # the classic webcam plugin's snapshot first, then OctoPrint's own webcam setting; None when neither is set
        for path in (["plugins", "classicwebcam", "snapshot"], ["webcam", "snapshot"]):
            url = self._settings.global_get(path)
            if isinstance(url, str) and url.strip():
                return url.strip()
        return None

    def on_after_startup(self):
        if self._settings.get_boolean(["presets_loaded"]):
            return
        threading.Thread(target=self._load_sample_presets, daemon=True).start()

    def _maintenance_env(self):
        import octoprint.settings

        getter = getattr(self._settings, "getBaseFolder", None) or octoprint.settings.settings().getBaseFolder
        return {
            "octoprint": os.path.join(os.path.dirname(sys.executable), "octoprint"),
            "basedir": getter("base"),
            "timelapse": getter("timelapse"),
            "logs": getter("logs"),
        }

    def _skin_system_entries(self, devices):
        # returns the host and OctoPrint devices to put first, or nothing when the operator has already set this up
        core = self._settings.get(["core"], merged=True) or {}
        if any(device.get("id") in ("host", "octoprint") for device in devices):
            return []
        if any((core.get(action) or {}).get("group") for action in SYSTEM_GROUPS):
            return []
        for action, (group, role) in SYSTEM_GROUPS.items():
            self._settings.set(["core", action, "group"], group)
            self._settings.set(["core", action, "role"], role)
        return [
            {**{key: switch[key] for key in ("id", "type", "name", "icon")}, "preset": switch["id"]}
            for switch in system_switches(socket.gethostname())
        ]

    def _load_sample_presets(self, detector=detect, maintenance_detector=None, plugin_groups=None):
        devices = self._settings.get(["devices"]) or []
        fresh = not devices
        skin = self._skin_system_entries(devices)
        if fresh:
            added = sample_devices(detector())
            self._settings.set(["devices"], skin + added)
            self._logger.info("Added sample devices: %s", [device["id"] for device in added])
            if added:
                # a sample readout is shown in the menu only where its placement is, so place each one under Status
                actions = self._settings.global_get(["system", "actions"]) or []
                _, actions, _ = place_readouts([dict(device, section=STATUS_SECTION) for device in added], actions)
                # the placements carry their readouts' markers, which place_readouts does not write
                _, actions, _ = mark_presets(added, actions, [])
                self._settings.global_set(["system", "actions"], actions)
        elif skin:
            self._settings.set(["devices"], skin + devices)
        if maintenance_detector is None:
            def maintenance_detector():
                return detect_maintenance(self._maintenance_env())
        actions = self._settings.global_get(["system", "actions"]) or []
        if not any(item.get("action") != "divider" and "device" not in item for item in actions):
            detected = [preset for preset in maintenance_detector() if preset["sample"] and preset["available"]]
            if detected:
                # each maintenance command is an action control of its own, with its command as the one run member
                devices = self._settings.get(["devices"]) or []
                device_ids = {device.get("id") for device in devices}
                new_devices, samples = [], []
                for preset in detected:
                    device, entry = action_of(preset, device_ids)
                    device_ids.add(device["id"])
                    new_devices.append(device)
                    samples.append(entry)
                self._settings.set(["devices"], devices + new_devices)
                divider = {"action": "divider", "name": "Maintenance"}
                self._settings.global_set(["system", "actions"], actions + [divider] + samples)
                self._logger.info("Added maintenance commands: %s", [entry["action"] for entry in samples])
        if fresh:
            self._add_ready_made(plugin_groups)
        self._settings.set(["presets_loaded"], True)
        self._settings.save()

    def _add_ready_made(self, plugin_groups=None):
        # after maintenance, which is skipped once any custom command exists, and these commands are custom commands
        if plugin_groups is None:
            plugin_groups = self._plugin_presets() + [webcam_presets(), octoprint_presets()]
        devices = self._settings.get(["devices"]) or []
        actions = self._settings.global_get(["system", "actions"]) or []
        added = []
        for preset in first_start_presets(plugin_groups):
            if any(device.get("preset") == preset["preset"] for device in devices):
                continue
            devices, actions = add_plugin_preset(preset, devices, actions)
            # the control is appended, so it is the last device
            added.append(devices[-1]["id"])
        if not added:
            return
        self._settings.set(["devices"], devices)
        self._settings.global_set(["system", "actions"], actions)
        self._logger.info("Added ready-made controls: %s", added)

    def on_api_get(self, request):
        if not Permissions.SYSTEM.can():
            flask.abort(403)
        listed = self._action_entries()
        entries = {entry["action"]: entry for entry in listed}
        return flask.jsonify(
            devices=self._settings.get(["devices"]) or [],
            core=self._settings.get(["core"], merged=True) or {},
            entries=entries,
            order=[entry["action"] for entry in listed],
            core_commands=self._core_commands(),
            success_toast=self._settings.get(["success_toast"]) is True,
            live_updates=self._settings.get(["live_updates"]) is True,
            live_interval=self._settings.get(["live_interval"]) or 5,
            busy_icon=self._settings.get(["busy_icon"]) or "fa-solid fa-spinner",
        )

    def is_api_protected(self):
        return True

    def _action_entries(self):
        entries = []
        for item in self._settings.global_get(["system", "actions"]) or []:
            action = item.get("action")
            if not action or action == "divider":
                continue
            entry = {key: item[key] for key in API_ENTRY_KEYS if key in item}
            entry["action"] = action
            entries.append(entry)
        return entries

    def _core_entries(self):
        core = self._settings.get(["core"], merged=True) or {}
        return [
            {"action": key, "group": "", "role": "", "value": "", **value}
            for key, value in core.items()
        ]

    def _core_commands(self):
        commands = self._settings.global_get(["server", "commands"]) or {}
        return {key: commands[setting] for key, setting in CORE_COMMAND_SETTINGS.items() if commands.get(setting)}

    def on_settings_migrate(self, target, current):
        if current is None or current < 2:
            self._convert_legacy_commands()
        if current is None or current < 3:
            self._place_readouts()
        if current is None or current < 4:
            self._mark_presets()
        if current is None or current < 5:
            self._link_devices()
        if current is None or current < 6:
            self._live_plug_reads()
        if current is None or current < 7:
            self._role_icons()
        if current is None or current < 8:
            self._power_icons()

    def _role_icons(self):
        actions = copy.deepcopy(self._settings.global_get(["system", "actions"]) or [])
        new_actions, changed = role_icons(actions)
        if not changed:
            return
        self._settings.global_set(["system", "actions"], new_actions)
        self._logger.info("Gave the On and Off switch commands the toggle icons")

    def _power_icons(self):
        actions = copy.deepcopy(self._settings.global_get(["system", "actions"]) or [])
        new_actions, changed = power_icons(actions)
        if not changed:
            return
        self._settings.global_set(["system", "actions"], new_actions)
        self._logger.info("Gave the On and Off switch commands the power icon in green and red")

    def _live_plug_reads(self):
        devices = copy.deepcopy(self._settings.get(["devices"]) or [])
        new_devices, changed = live_plug_reads(devices)
        if not changed:
            return
        self._settings.set(["devices"], new_devices)
        self._logger.info("Put the TP-Link and Tasmota plug reads back in live updates")

    def _link_devices(self):
        devices = copy.deepcopy(self._settings.get(["devices"]) or [])
        actions = copy.deepcopy(self._settings.global_get(["system", "actions"]) or [])
        new_devices, new_actions, changed = link_devices(devices, actions, key_for)
        if not changed:
            return
        self._settings.global_set(["system", "actions"], new_actions)
        self._settings.set(["devices"], new_devices)
        self._logger.info("Turned the link controls into standalone link commands")

    def _mark_presets(self):
        devices = copy.deepcopy(self._settings.get(["devices"]) or [])
        actions = copy.deepcopy(self._settings.global_get(["system", "actions"]) or [])
        new_devices, new_actions, changed = mark_presets(devices, actions, maintenance_actions())
        if not changed:
            return
        if new_actions != actions:
            self._settings.global_set(["system", "actions"], new_actions)
        if new_devices != devices:
            self._settings.set(["devices"], new_devices)
        self._logger.info("Marked the presets already in use, so the picker can track them by id")

    def _place_readouts(self):
        devices = self._settings.get(["devices"]) or []
        actions = copy.deepcopy(self._settings.global_get(["system", "actions"]) or [])
        new_devices, new_actions, changed = place_readouts(devices, actions)
        if not changed:
            return
        if new_actions != actions:
            self._settings.global_set(["system", "actions"], new_actions)
        if new_devices != devices:
            self._settings.set(["devices"], new_devices)
        self._logger.info("Placed readouts in the custom commands list")

    def _convert_legacy_commands(self):
        system_actions = copy.deepcopy(self._settings.global_get(["system", "actions"]) or [])
        legacy_actions = copy.deepcopy(self._settings.get(["actions"]) or [])

        self._backup(system_actions, legacy_actions)

        actions, report = convert(system_actions, legacy_actions)

        if command_count(actions) < command_count(system_actions):
            # convert() only ever adds commands; never write a shorter list over a longer one
            self._logger.error(
                "Not converting system commands: the result would hold fewer commands than system.actions"
            )
            return

        if actions != system_actions:
            self._settings.global_set(["system", "actions"], actions)
        self._settings.remove(["actions"])

        self._logger.info(
            "Converted system commands: %d commands, %d duplicates removed %s, %d restored from the plugin's "
            "old copy %s, %d invalid entries skipped",
            command_count(actions),
            len(report["duplicates"]),
            report["duplicates"],
            len(report["restored"]),
            report["restored"],
            report["invalid"],
        )

    def _backup(self, system_actions, legacy_actions):
        if not system_actions and not legacy_actions:
            return
        path = os.path.join(
            self.get_plugin_data_folder(), "pre-2.0-backup-{}.yaml".format(time.strftime("%Y%m%d-%H%M%S"))
        )
        try:
            with io.open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    {"system_actions": system_actions, "plugin_actions": legacy_actions},
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                )
            self._logger.info("Saved the system commands as they were before conversion to %s", path)
        except Exception:
            self._logger.exception("Could not save a backup of the system commands to %s", path)

    def is_template_autoescaped(self):
        return True

    def get_template_configs(self):
        return [{"type": "settings", "custom_bindings": True}]

    def get_assets(self):
        return {
            "js": [
                "js/systemcommandeditor_icons.js",
                "js/systemcommandeditor_iconsearch.js",
                "js/systemcommandeditor.js",
                "js/systemcommandeditor_menu.js",
            ],
            "css": ["css/systemcommandeditor.css"],
        }

    def get_update_information(self):
        return {
            "systemcommandeditor": {
                "displayName": "System Command Editor Plugin",
                "displayVersion": self._plugin_version,
                "type": "github_release",
                "user": "synman",
                "repo": "OctoPrint-SystemCommandEditor",
                "current": self._plugin_version,
                "pip": "https://github.com/synman/OctoPrint-SystemCommandEditor/archive/{target_version}.zip",
            }
        }


__plugin_name__ = "System Command Editor"
__plugin_pythoncompat__ = ">=3.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = SystemCommandEditorPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }

    global __plugin_license__
    __plugin_license__ = "AGPLv3"
