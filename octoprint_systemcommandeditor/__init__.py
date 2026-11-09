# coding=utf-8
from __future__ import absolute_import

__author__ = "Shell M. Shrader, originally Marc Hannappel <salandora@gmail.com>"
__license__ = "GNU Affero General Public License http://www.gnu.org/licenses/agpl.html"

import copy
import io
import os
import socket
import sys
import threading
import time

import flask
import yaml

import octoprint.plugin
from octoprint.access.permissions import Permissions

from .convert import command_count, convert
from .presets import detect, detect_maintenance, entry_of, sample_devices
from .status import check_devices

API_ENTRY_KEYS = (
    "name",
    "command",
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
)

CORE_COMMAND_SETTINGS = {
    "shutdown": "systemShutdownCommand",
    "reboot": "systemRestartCommand",
    "restart": "serverRestartCommand",
    "restart_safe": "serverRestartCommand",
}


# On first start, OctoPrint's own System menu entries are grouped under the host and OctoPrint devices
SYSTEM_GROUPS = {
    "reboot": ("host", "restart"),
    "shutdown": ("host", "off"),
    "restart": ("octoprint", "restart"),
    "restart_safe": ("octoprint", "safe_restart"),
}


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

    def get_settings_defaults(self):
        # Icons and headings for OctoPrint's own System menu entries, which its config has no place for. These are
        # decoration only; what the entries run stays in OctoPrint's server.commands.
        return {
            "core": {
                "shutdown": {"icon": "fa-solid fa-power-off", "heading": "System", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
                "reboot": {"icon": "fa-solid fa-rotate-right", "heading": "", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
                "restart": {"icon": "fa-solid fa-arrows-rotate", "heading": "OctoPrint", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
                "restart_safe": {"icon": "fa-solid fa-shield-halved", "heading": "", "icon_color": "", "icon_invert": False, "group": "", "role": "", "value": "", "when": None},
            },
            "devices": [],
            "success_toast": False,
            "live_updates": False,
            "live_interval": 5,
            "presets_loaded": False,
        }

    def get_settings_version(self):
        return 2

    def get_api_commands(self):
        return {"status": ["ids"], "presets": []}

    def on_api_command(self, command, data):
        if not Permissions.SYSTEM.can():
            flask.abort(403)
        if command == "status":
            devices = self._settings.get(["devices"]) or []
            entries = self._action_entries() + self._core_entries()
            return flask.jsonify(results=check_devices(devices, entries, data["ids"]))
        if command == "presets":
            return flask.jsonify(presets=detect(), maintenance=detect_maintenance(self._maintenance_env()))

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
        host = socket.gethostname().split(".")[0] or "Host"
        return [
            {"id": "host", "type": "switch", "name": host, "icon": "fa-solid fa-server"},
            {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print"},
        ]

    def _load_sample_presets(self, detector=detect, maintenance_detector=None):
        devices = self._settings.get(["devices"]) or []
        fresh = not devices
        skin = self._skin_system_entries(devices)
        if fresh:
            added = sample_devices(detector())
            self._settings.set(["devices"], skin + added)
            self._logger.info("Added sample devices: %s", [device["id"] for device in added])
        elif skin:
            self._settings.set(["devices"], skin + devices)
        if maintenance_detector is None:
            def maintenance_detector():
                return detect_maintenance(self._maintenance_env())
        actions = self._settings.global_get(["system", "actions"]) or []
        if not any(item.get("action") != "divider" for item in actions):
            samples = [
                entry_of(preset) for preset in maintenance_detector() if preset["sample"] and preset["available"]
            ]
            if samples:
                divider = {"action": "divider", "name": "Maintenance"}
                self._settings.global_set(["system", "actions"], actions + [divider] + samples)
                self._logger.info("Added maintenance commands: %s", [entry["action"] for entry in samples])
        self._settings.set(["presets_loaded"], True)
        self._settings.save()

    def on_api_get(self, request):
        if not Permissions.SYSTEM.can():
            flask.abort(403)
        entries = {entry["action"]: entry for entry in self._action_entries()}
        return flask.jsonify(
            devices=self._settings.get(["devices"]) or [],
            core=self._settings.get(["core"], merged=True) or {},
            entries=entries,
            core_commands=self._core_commands(),
            success_toast=self._settings.get(["success_toast"]) is True,
            live_updates=self._settings.get(["live_updates"]) is True,
            live_interval=self._settings.get(["live_interval"]) or 5,
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
        if current is not None and current >= 2:
            return

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
