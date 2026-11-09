import copy
import json
import os
import subprocess
import time
import types

import pytest

pytest.importorskip("octoprint")

from octoprint_systemcommandeditor.presets import (  # noqa: E402
    CATALOG,
    action_of,
    add_plugin_preset,
    first_start_presets,
    key_for,
    detect,
    detect_maintenance,
    device_of,
    entry_of,
    harvest,
    maintenance,
    maintenance_actions,
    mark_presets,
    PRINTER_STATE_WORDS,
    STATE_LISTS,
    sample_devices,
    state_list,
    WEBCAM_SERVICES,
    webcam_presets,
)
from octoprint_systemcommandeditor.status import run_status  # noqa: E402
from octoprint_systemcommandeditor.presets import octoprint_presets  # noqa: E402

IDS = ["disk", "memory", "load", "uptime", "cpu_temp", "throttle", "ip", "wifi"]


def fake_runner(results):
    """Return a runner that answers each catalog status command from results, keyed by preset id."""
    by_command = {preset["status"]: results.get(preset["id"], (0, "value", None)) for preset in CATALOG}

    def runner(command, timeout=10):
        return by_command[command]

    return runner


def detected_by_id(runner):
    return {item["id"]: item for item in detect(runner=runner)}


def test_catalog_has_eight_presets_in_order_with_required_keys():
    assert [preset["id"] for preset in CATALOG] == IDS
    for preset in CATALOG:
        for key in ("id", "name", "icon", "status", "sample"):
            assert key in preset


def test_detect_marks_nonzero_exit_unavailable():
    detected = detected_by_id(fake_runner({"disk": (1, "x", None)}))
    assert detected["disk"]["available"] is False
    assert detected["disk"]["value"] == ""


def test_detect_marks_error_unavailable():
    detected = detected_by_id(fake_runner({"memory": (None, "", "timeout")}))
    assert detected["memory"]["available"] is False
    assert detected["memory"]["value"] == ""


def test_detect_marks_empty_output_unavailable():
    detected = detected_by_id(fake_runner({"load": (0, "", None)}))
    assert detected["load"]["available"] is False
    assert detected["load"]["value"] == ""


def test_detect_available_with_first_line_as_value():
    detected = detected_by_id(fake_runner({"uptime": (0, "3d 4h\nsecond line", None)}))
    assert detected["uptime"]["available"] is True
    assert detected["uptime"]["value"] == "3d 4h"


def test_detect_returns_catalog_order_with_device_and_sample_keys():
    detected = detect(runner=fake_runner({}))
    assert [item["id"] for item in detected] == IDS
    for item, preset in zip(detected, CATALOG):
        assert item["sample"] is preset["sample"]
        assert item["available"] is True
        assert "section" not in item


def test_device_of_builds_readout_record_with_six_keys():
    record = device_of(CATALOG[0])
    assert record == {
        "id": "disk",
        "type": "readout",
        "name": "Free disk",
        "icon": "fa-solid fa-hard-drive",
        "status": CATALOG[0]["status"],
    }


def test_readout_catalog_samples_are_exactly_disk_and_cpu_temp():
    assert [preset["id"] for preset in CATALOG if preset["sample"]] == ["disk", "cpu_temp"]


def test_sample_devices_keeps_only_sample_and_available_in_catalog_order():
    detected = detect(
        runner=fake_runner(
            {
                "memory": (0, "2G free", None),  # available, not a sample now
                "ip": (0, "10.0.0.2", None),  # available, not a sample
                "throttle": (0, "OK", None),  # available, not a sample now
                "cpu_temp": (1, "x", None),  # sample, unavailable
            }
        )
    )
    samples = sample_devices(detected)
    assert [record["id"] for record in samples] == ["disk"]
    for record in samples:
        assert set(record) == {"id", "type", "name", "icon", "status", "preset"}
        assert record["preset"] == record["id"]


ENV = {
    "octoprint": "/opt/my venv/bin/octoprint",
    "basedir": "/home/pi/my octo",
    "timelapse": "/home/pi/my octo/timelapse",
    "logs": "/home/pi/my octo/logs",
}
MAINTENANCE_ACTIONS = [
    "backup_now",
    "purge_timelapses",
    "purge_logs",
    "sync_clock",
    "wifi_powersave_off",
    "os_update",
]


def maintenance_runner(exit_by_action, sudo_exit=0, calls=None):
    """Fake runner: each preset's detect command exits as exit_by_action says (default 0); sudo -n true exits sudo_exit."""
    answers = {preset["detect"]: (exit_by_action.get(preset["action"], 0), "", None) for preset in maintenance(ENV)}
    answers["sudo -n true"] = (sudo_exit, "", None)

    def runner(command, timeout=10):
        if calls is not None:
            calls.append(command)
        return answers[command]

    return runner


def test_maintenance_returns_six_actions_in_order_with_quoted_paths():
    presets = maintenance(ENV)
    assert [preset["action"] for preset in presets] == MAINTENANCE_ACTIONS
    for preset in presets:
        assert set(preset) - {"status"} == {"action", "name", "icon", "command", "confirm", "async", "sample", "needs_sudo", "detect"}
    backup = presets[0]
    assert backup["command"] == "'/opt/my venv/bin/octoprint' --basedir '/home/pi/my octo' plugins backup:backup"
    assert backup["detect"] == "test -x '/opt/my venv/bin/octoprint'"
    assert backup["async"] is True and backup["sample"] is True and backup["needs_sudo"] is False
    assert presets[1]["command"] == "find '/home/pi/my octo/timelapse' -maxdepth 1 -type f -mtime +30 -delete"


def test_maintenance_sample_is_backup_only():
    assert [preset["action"] for preset in maintenance(ENV) if preset["sample"]] == ["backup_now"]


def test_detect_maintenance_marks_availability_from_detect_exit_code():
    listed = detect_maintenance(ENV, runner=maintenance_runner({"sync_clock": 1, "os_update": None}))
    by_action = {item["action"]: item for item in listed}
    assert [item["action"] for item in listed] == MAINTENANCE_ACTIONS
    assert by_action["sync_clock"]["available"] is False
    assert by_action["os_update"]["available"] is False
    assert by_action["backup_now"]["available"] is True
    assert set(listed[0]) == {"action", "name", "icon", "command", "confirm", "async", "sample", "status", "available", "note"}


def test_detect_maintenance_sudo_note_only_when_sudo_fails_and_preset_needs_it():
    calls = []
    listed = detect_maintenance(ENV, runner=maintenance_runner({}, sudo_exit=1, calls=calls))
    by_action = {item["action"]: item for item in listed}
    assert by_action["os_update"]["note"] == "Needs passwordless sudo"
    assert by_action["sync_clock"]["note"] == "Needs passwordless sudo"
    assert by_action["backup_now"]["note"] == ""
    assert by_action["purge_timelapses"]["note"] == ""
    assert calls.count("sudo -n true") == 1


def test_detect_maintenance_has_no_sudo_note_when_sudo_works():
    listed = detect_maintenance(ENV, runner=maintenance_runner({}, sudo_exit=0))
    assert all(item["note"] == "" for item in listed)


def test_entry_of_keeps_confirm_and_async_only_when_set():
    by_action = {preset["action"]: preset for preset in maintenance(ENV)}
    backup = entry_of(by_action["backup_now"])
    assert backup == {
        "action": "backup_now",
        "name": "Back up OctoPrint now",
        "command": by_action["backup_now"]["command"],
        "icon": "fa-solid fa-box-archive",
        "async": True,
    }
    purge = entry_of(by_action["purge_timelapses"])
    assert purge["confirm"] == "This deletes timelapse videos older than 30 days."
    assert "async" not in purge
    plain = entry_of(by_action["wifi_powersave_off"])
    assert "confirm" not in plain and "async" not in plain



def test_action_of_builds_an_action_control_and_its_run_command():
    preset = [p for p in maintenance(ENV) if p["action"] == "backup_now"][0]
    device, entry = action_of(preset, set())
    assert device == {
        "id": "backup_now",
        "type": "action",
        "name": preset["name"],
        "icon": preset["icon"],
        "status": preset["status"],
        "preset": "backup_now",
    }
    assert entry["group"] == "backup_now" and entry["role"] == "run"
    assert entry["preset"] == "backup_now"
    assert entry["command"] == preset["command"] and entry["async"] is True


def test_action_of_takes_the_next_free_device_id_and_skips_status_when_none():
    preset = [p for p in maintenance(ENV) if p["action"] == "sync_clock"][0]
    device, entry = action_of(preset, {"sync_clock", "sync_clock_2"})
    assert device["id"] == "sync_clock_3"
    assert "status" not in device
    assert entry["group"] == "sync_clock_3"


def test_backup_age_and_folder_size_status_commands_run_against_a_temp_folder(tmp_path):
    base = tmp_path / "octo"
    backups = base / "data" / "backup"
    backups.mkdir(parents=True)
    newest = backups / "backup.zip"
    newest.write_bytes(b"x" * 100)
    two_hours_ago = time.time() - 2 * 3600 - 60
    os.utime(newest, (two_hours_ago, two_hours_ago))
    timelapse = base / "timelapse"
    timelapse.mkdir()
    (timelapse / "clip.mp4").write_bytes(b"y" * 1536)
    env = {**ENV, "basedir": str(base), "timelapse": str(timelapse), "logs": str(base / "logs")}
    by_action = {preset["action"]: preset for preset in maintenance(env)}

    assert run_status(by_action["backup_now"]["status"]) == (0, "Last backup 2 h ago", None)
    # a new clip is not old enough to delete
    assert run_status(by_action["purge_timelapses"]["status"]) == (0, "No old timelapses", None)
    # a missing logs folder has nothing to delete
    assert run_status(by_action["purge_logs"]["status"]) == (0, "No old logs", None)


def test_old_logs_status_counts_exactly_what_the_command_deletes(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    now = time.time()

    def log(name, days, size):
        path = logs / name
        path.write_bytes(b"z" * size)
        os.utime(path, (now - days * 86400 - 60, now - days * 86400 - 60))

    log("octoprint.log", 30, 5000)               # live log: never counted, never deleted
    log("octoprint.log.2026-10-01", 9, 1024)     # old rotated log
    log("serial.log.2026-10-02_10-00-00", 8, 1024)  # old rotated log
    log("octoprint.log.2026-11-08", 7, 4096)     # 7 whole days: find -mtime +7 keeps it
    (logs / "nested").mkdir()
    env = {**ENV, "logs": str(logs)}
    preset = [p for p in maintenance(env) if p["action"] == "purge_logs"][0]
    assert preset["name"] == "Delete logs older than 7 days"

    assert run_status(preset["status"]) == (0, "2 old logs, 2.0 KB", None)
    subprocess.run(preset["command"], shell=True, check=True)
    assert sorted(os.listdir(logs)) == ["nested", "octoprint.log", "octoprint.log.2026-11-08"]
    assert run_status(preset["status"]) == (0, "No old logs", None)


def test_old_timelapses_status_counts_exactly_what_the_command_deletes(tmp_path):
    folder = tmp_path / "my timelapse"
    folder.mkdir()
    now = time.time()

    def clip(name, days, size):
        path = folder / name
        path.write_bytes(b"v" * size)
        os.utime(path, (now - days * 86400 - 60, now - days * 86400 - 60))

    clip("print_a.mp4", 45, 2048)   # old: counted and deleted
    clip("print_b.mp4", 30, 4096)   # 30 whole days: find -mtime +30 keeps it
    clip("print_c.mp4", 1, 8192)    # new
    env = {**ENV, "timelapse": str(folder)}
    preset = [p for p in maintenance(env) if p["action"] == "purge_timelapses"][0]
    assert preset["name"] == "Delete timelapses older than 30 days"

    assert run_status(preset["status"]) == (0, "1 old timelapse, 2.0 KB", None)
    subprocess.run(preset["command"], shell=True, check=True)
    assert sorted(os.listdir(folder)) == ["print_b.mp4", "print_c.mp4"]
    assert run_status(preset["status"]) == (0, "No old timelapses", None)


def test_backup_age_says_none_when_the_backup_folder_is_empty(tmp_path):
    env = {**ENV, "basedir": str(tmp_path)}
    status = [p for p in maintenance(env) if p["action"] == "backup_now"][0]["status"]
    assert run_status(status) == (0, "Last backup none", None)


def test_mark_presets_marks_catalog_readouts_and_their_placements_by_id():
    devices = [{"id": "disk", "type": "readout", "name": "Disk", "icon": "", "status": "df"}]
    actions = [{"action": "disk", "name": "Disk", "device": "disk"}]
    new_devices, new_actions, changed = mark_presets(devices, actions, [])
    assert changed
    assert new_devices[0]["preset"] == "disk"
    assert new_actions[0]["preset"] == "disk"
    assert "preset" not in devices[0] and "preset" not in actions[0]


def test_mark_presets_marks_the_host_and_octoprint_switches_only_as_switches():
    devices = [
        {"id": "host", "type": "switch", "name": "Host"},
        {"id": "octoprint", "type": "readout", "name": "OctoPrint"},
        {"id": "custom", "type": "switch", "name": "Custom"},
    ]
    new_devices, _, changed = mark_presets(devices, [], [])
    assert changed
    assert [d.get("preset") for d in new_devices] == ["host", None, None]


def test_mark_presets_marks_maintenance_command_and_its_action_control():
    devices = [{"id": "backup_now", "type": "action", "name": "Back up", "icon": ""}]
    actions = [
        {"action": "divider", "name": "Maintenance"},
        {"action": "backup_now", "name": "Back up", "command": "x", "group": "backup_now", "role": "run"},
        {"action": "mine", "name": "Mine", "command": "y", "group": "mine"},
    ]
    new_devices, new_actions, changed = mark_presets(devices, actions, ["backup_now"])
    assert changed
    assert new_devices[0]["preset"] == "backup_now"
    assert new_actions[1]["preset"] == "backup_now"
    assert "preset" not in new_actions[0] and "preset" not in new_actions[2]


def test_mark_presets_never_overwrites_an_existing_marker():
    devices = [{"id": "disk", "type": "readout", "name": "Disk", "icon": "", "preset": "mine"}]
    actions = [{"action": "disk", "name": "Disk", "device": "disk", "preset": "theirs"}]
    new_devices, new_actions, changed = mark_presets(devices, actions, [])
    assert not changed
    assert new_devices[0]["preset"] == "mine"
    assert new_actions[0]["preset"] == "theirs"


def test_mark_presets_reports_no_change_for_custom_records():
    devices = [{"id": "fan", "type": "switch", "name": "Fan"}, {"id": "t", "type": "action", "name": "T"}]
    actions = [{"action": "fan_on", "name": "On", "command": "x", "group": "fan", "role": "on"}]
    new_devices, new_actions, changed = mark_presets(devices, actions, ["backup_now"])
    assert not changed
    assert new_devices == devices and new_actions == actions


def test_maintenance_actions_lists_the_six_keys_without_detection():
    assert maintenance_actions() == [
        "backup_now", "purge_timelapses", "purge_logs", "sync_clock", "wifi_powersave_off", "os_update",
    ]


# Plugin presets (harvest). Each fake plugin settings dict is shaped like that plugin's real settings.


class Plugins:
    """A fake plugin manager: only the plugins passed in are installed and enabled."""

    def __init__(self, *installed, disabled=()):
        self.installed = set(installed) | set(disabled)
        self.disabled = set(disabled)

    def get_plugin_info(self, identifier, require_enabled=True):
        if identifier not in self.installed:
            return None
        return types.SimpleNamespace(enabled=identifier not in self.disabled)


def settings_of(**plugins):
    def get(plugin, key):
        return plugins.get(plugin, {}).get(key)

    return get


def presets_of(groups, plugin_id):
    return [preset for group in groups if group["plugin"] == plugin_id for preset in group["presets"]]


def only_preset(groups, preset_id):
    matches = [preset for group in groups for preset in group["presets"] if preset["preset"] == preset_id]
    assert len(matches) == 1, preset_id
    return matches[0]


def assert_unkeyed(preset):
    # the editor assigns every key: no control id, no command action, no command group
    assert "id" not in preset["control"]
    for command in preset["commands"]:
        assert "action" not in command and "group" not in command


def test_harvest_offers_nothing_for_plugins_not_installed():
    assert harvest(Plugins(), settings_of(psucontrol={})) == []


def test_harvest_offers_nothing_for_an_installed_but_disabled_plugin():
    # OctoPrint returns info for a disabled plugin too; only installed AND enabled plugins are offered
    settings = settings_of(psucontrol={}, tasmota={"arrSmartplugs": [{"ip": "10.0.0.6", "idx": 1, "label": "B"}]})
    assert harvest(Plugins("psucontrol", "tasmota", disabled=("psucontrol", "tasmota")), settings) == []
    groups = harvest(Plugins("psucontrol", "tasmota", disabled=("psucontrol",)), settings)
    assert [group["plugin"] for group in groups] == ["tasmota"]


def test_harvest_offers_nothing_for_a_plugin_with_no_devices():
    plugins = Plugins("tplinksmartplug")
    assert harvest(plugins, settings_of(tplinksmartplug={"arrSmartplugs": []})) == []


def test_psucontrol_is_one_switch_with_on_and_off_commands():
    # psu.py:878-884 helpers get_psu_state / turn_psu_on / turn_psu_off; get_psu_state returns the bool (psu.py:581-582)
    groups = harvest(Plugins("psucontrol"), settings_of())
    assert groups == [
        {
            "plugin": "psucontrol",
            "name": "PSU Control",
            "presets": [
                {
                    "preset": "psucontrol:psu",
                    "name": "PSU Control",
                    "control": {
                        "type": "switch",
                        "name": "Printer power",
                        "icon": "fa-solid fa-power-off",
                        "read": {"plugin": "psucontrol", "helper": "get_psu_state"},
                        "on_pattern": "^true$",
                        "off_pattern": "^false$",
                    },
                    "commands": [
                        {"name": "On", "role": "on", "run": {"plugin": "psucontrol", "helper": "turn_psu_on"},
                         "icon": "fa-solid fa-play"},
                        {"name": "Off", "role": "off", "run": {"plugin": "psucontrol", "helper": "turn_psu_off"},
                         "icon": "fa-solid fa-power-off"},
                    ],
                }
            ],
        }
    ]
    assert_unkeyed(groups[0]["presets"][0])


def test_tplink_has_one_preset_per_plug_named_by_label_or_address():
    # tplink.py:1395-1399 helpers; plug["label"] (tplink.py:1190); check_status returns currentState (tplink.py:589)
    settings = settings_of(
        tplinksmartplug={"arrSmartplugs": [{"ip": "10.0.0.5", "label": "Printer"}, {"ip": "10.0.0.7", "label": ""}]}
    )
    groups = harvest(Plugins("tplinksmartplug"), settings)
    assert [p["preset"] for p in presets_of(groups, "tplinksmartplug")] == [
        "tplinksmartplug:10.0.0.5",
        "tplinksmartplug:10.0.0.7",
    ]
    first = only_preset(groups, "tplinksmartplug:10.0.0.5")
    args = {"plugip": "10.0.0.5"}
    assert first["name"] == "Printer"
    assert first["control"] == {
        "type": "switch",
        "name": "Printer",
        "icon": "fa-solid fa-plug",
        "read": {"plugin": "tplinksmartplug", "helper": "check_status", "args": args, "live": False},
        "on_pattern": '"currentState": "on"',
        "off_pattern": '"currentState": "off"',
    }
    assert first["commands"][0]["run"] == {"plugin": "tplinksmartplug", "helper": "turn_on", "args": args}
    assert first["commands"][1]["run"] == {"plugin": "tplinksmartplug", "helper": "turn_off", "args": args}
    assert only_preset(groups, "tplinksmartplug:10.0.0.7")["control"]["name"] == "10.0.0.7"
    assert_unkeyed(first)


def test_tplink_and_tasmota_reads_are_live_false_and_the_others_are_not():
    settings = settings_of(
        tplinksmartplug={"arrSmartplugs": [{"ip": "10.0.0.5", "label": "A"}]},
        tasmota={"arrSmartplugs": [{"ip": "10.0.0.6", "idx": 1, "label": "B"}]},
        octorelay={"r1": {"active": True, "label_text": "C"}},
        gpiocontrol={"gpio_configurations": [{"name": "D"}]},
        usbrelaycontrol={"usbrelay_configurations": [{"name": "E"}]},
    )
    plugins = Plugins("psucontrol", "tplinksmartplug", "tasmota", "octorelay", "gpiocontrol", "usbrelaycontrol",
                      "bettergrblsupport")
    groups = harvest(plugins, settings)
    live_false = set()
    for group in groups:
        for preset in group["presets"]:
            read = preset["control"].get("read") or {}
            if read.get("live") is False:
                live_false.add(group["plugin"])
    assert live_false == {"tplinksmartplug", "tasmota"}


def test_tasmota_uses_the_method_kind_with_ip_and_idx():
    # tasmota.py:467 turn_on(plugip, plugidx), :512 turn_off, :557 check_status(plugip, plugidx); plug fields ip, idx, label
    settings = settings_of(tasmota={"arrSmartplugs": [{"ip": "10.0.0.6", "idx": 1, "label": "Chamber"}]})
    preset = only_preset(harvest(Plugins("tasmota"), settings), "tasmota:10.0.0.6/1")
    args = {"plugip": "10.0.0.6", "plugidx": 1}
    assert preset["name"] == "Chamber"
    assert preset["control"]["read"] == {"plugin": "tasmota", "method": "check_status", "args": args, "live": False}
    assert preset["commands"][0]["run"] == {"plugin": "tasmota", "method": "turn_on", "args": args}
    assert preset["commands"][1]["run"] == {"plugin": "tasmota", "method": "turn_off", "args": args}


def test_octorelay_offers_only_active_relays_named_by_label_or_key():
    # octorelay_const.py:41 settings r1..r8 with active and label_text; UPDATE "update", GET_STATUS "getStatus" (:329-330)
    settings = settings_of(
        octorelay={
            "r1": {"active": True, "label_text": "Printer"},
            "r2": {"active": False, "label_text": "Unused"},
            "r3": {"active": True, "label_text": ""},
        }
    )
    groups = harvest(Plugins("octorelay"), settings)
    assert [p["preset"] for p in presets_of(groups, "octorelay")] == ["octorelay:r1", "octorelay:r3"]
    first = only_preset(groups, "octorelay:r1")
    assert first["control"]["read"] == {"plugin": "octorelay", "api": "getStatus", "data": {"subject": "r1"}}
    assert (first["control"]["on_pattern"], first["control"]["off_pattern"]) == ('"status": true', '"status": false')
    assert first["commands"][0]["run"] == {"plugin": "octorelay", "api": "update", "data": {"subject": "r1", "target": True}}
    assert first["commands"][1]["run"] == {"plugin": "octorelay", "api": "update", "data": {"subject": "r1", "target": False}}
    assert only_preset(groups, "octorelay:r3")["name"] == "R3"


def test_gpiocontrol_uses_the_list_index_as_id_and_names_unnamed_ones():
    # gpiocontrol.py:53 gpio_configurations, name; ids are list indexes (gpiocontrol.py:130); getGpioState answers
    # on/off as a JSON string (gpiocontrol.py:133-139)
    settings = settings_of(gpiocontrol={"gpio_configurations": [{"name": "Light", "pin": 17}, {"pin": 22}]})
    groups = harvest(Plugins("gpiocontrol"), settings)
    first = only_preset(groups, "gpiocontrol:0")
    assert first["control"]["read"] == {"plugin": "gpiocontrol", "api": "getGpioState", "data": {"id": 0}}
    assert (first["control"]["on_pattern"], first["control"]["off_pattern"]) == ("^on$", "^off$")
    assert first["commands"][0]["run"] == {"plugin": "gpiocontrol", "api": "turnGpioOn", "data": {"id": 0}}
    assert first["commands"][1]["run"] == {"plugin": "gpiocontrol", "api": "turnGpioOff", "data": {"id": 0}}
    assert only_preset(groups, "gpiocontrol:1")["name"] == "gpiocontrol 1"


def test_usbrelaycontrol_uses_the_list_index_as_id():
    # usbrelaycontrol.py:43 usbrelay_configurations; turnUSBRelayOn/Off and getUSBRelayState take id (:130-152)
    settings = settings_of(usbrelaycontrol={"usbrelay_configurations": [{"name": "Fan"}, {"name": "Lamp"}]})
    preset = only_preset(harvest(Plugins("usbrelaycontrol"), settings), "usbrelaycontrol:1")
    assert preset["control"]["read"] == {"plugin": "usbrelaycontrol", "api": "getUSBRelayState", "data": {"id": 1}}
    assert preset["commands"][0]["run"] == {"plugin": "usbrelaycontrol", "api": "turnUSBRelayOn", "data": {"id": 1}}
    assert preset["commands"][1]["run"] == {"plugin": "usbrelaycontrol", "api": "turnUSBRelayOff", "data": {"id": 1}}


def test_bettergrbl_is_one_action_control_with_four_run_commands_and_confirm_on_reset():
    # __init__.py:1926-1934 exported helpers sleep, unlock, reset, home, state; helper_state returns a dict (:1835)
    groups = harvest(Plugins("bettergrblsupport"), settings_of())
    preset = only_preset(groups, "bettergrblsupport:grbl")
    assert groups[0]["name"] == "Better Grbl Support"
    assert preset["control"] == {
        "type": "action",
        "name": "Grbl",
        "icon": "fa-solid fa-crosshairs",
        "read": {"plugin": "bettergrblsupport", "helper": "state", "field": "state"},
    }
    home, unlock, sleep, reset = preset["commands"]
    assert [c["name"] for c in preset["commands"]] == ["Home", "Unlock", "Sleep", "Reset"]
    assert home["run"] == {"plugin": "bettergrblsupport", "helper": "home"}
    assert unlock["run"] == {"plugin": "bettergrblsupport", "helper": "unlock"}
    assert sleep["run"] == {"plugin": "bettergrblsupport", "helper": "sleep"}
    assert reset["run"] == {"plugin": "bettergrblsupport", "helper": "reset"}
    assert reset["confirm"] == "This sends a reset to the Grbl controller."
    assert all(c["role"] == "run" for c in preset["commands"])
    assert "confirm" not in home
    assert_unkeyed(preset)


def test_harvest_never_copies_a_credential_field():
    settings = settings_of(
        tplinksmartplug={
            "arrSmartplugs": [{"ip": "10.0.0.5", "label": "Printer", "username": "hunter", "password": "s3cret",
                               "token": "tok123", "key": "k456"}]
        }
    )
    text = json.dumps(harvest(Plugins("tplinksmartplug"), settings))
    for secret in ("hunter", "s3cret", "tok123", "k456"):
        assert secret not in text


def octoprint_by_id():
    group = octoprint_presets()
    return {preset["preset"]: preset for preset in group["presets"]}


def test_octoprint_group_is_one_group_named_octoprint_with_five_presets_in_order():
    group = octoprint_presets()
    assert group["plugin"] == "octoprint" and group["name"] == "OctoPrint"
    assert [preset["preset"] for preset in group["presets"]] == [
        "octoprint:connection", "octoprint:job", "octoprint:printer", "octoprint:gcode", "octoprint:event",
    ]


def test_octoprint_connection_is_a_state_switch_with_connect_and_disconnect():
    preset = octoprint_by_id()["octoprint:connection"]
    control = preset["control"]
    assert preset["name"] == "Printer connection"
    assert control == {
        "type": "switch", "name": "Printer connection", "icon": "fa-solid fa-link",
        "read": {"printer": "state"},
        "on_pattern": "^(Operational|Printing|Paused|Pausing|Resuming|Cancelling|Finishing)",
        "off_pattern": "^(Offline|Closed|Error)",
    }
    assert preset["commands"] == [
        {"name": "Connect", "run": {"printer": "connect"}, "role": "on", "icon": "fa-solid fa-play"},
        {"name": "Disconnect", "run": {"printer": "disconnect"}, "role": "off", "icon": "fa-solid fa-power-off"},
    ]


def test_octoprint_job_reads_progress_and_runs_pause_resume_and_a_confirmed_cancel():
    preset = octoprint_by_id()["octoprint:job"]
    assert preset["control"] == {"type": "action", "name": "Print job", "icon": "fa-solid fa-hourglass-half",
                                 "read": {"printer": "progress"}}
    assert preset["commands"] == [
        {"name": "Pause", "run": {"printer": "pause"}, "role": "run", "icon": "fa-solid fa-pause",
         "when": {"mode": "only", "states": ["printing"], "effect": "disable"}},
        {"name": "Resume", "run": {"printer": "resume"}, "role": "run", "icon": "fa-solid fa-play",
         "when": {"mode": "only", "states": ["paused"], "effect": "disable"}},
        {"name": "Cancel", "run": {"printer": "cancel"}, "role": "run", "icon": "fa-solid fa-stop",
         "confirm": "Cancel the print?", "when": {"mode": "only", "states": ["printing", "paused"],
                                                  "effect": "disable"}},
    ]


def test_octoprint_printer_reads_temperatures_and_confirms_only_the_emergency_stop():
    preset = octoprint_by_id()["octoprint:printer"]
    assert preset["control"]["read"] == {"printer": "temperatures"}
    assert [command["name"] for command in preset["commands"]] == ["Home", "Cool down", "Emergency stop"]
    assert preset["commands"][2] == {
        "name": "Emergency stop", "run": {"printer": "emergency_stop"}, "role": "run", "icon": "fa-solid fa-hand",
        "confirm": "This sends M112, which halts the printer at once.",
    }
    assert all("confirm" not in command for command in preset["commands"][:2])


def test_octoprint_gcode_and_event_have_no_read_and_one_run_each():
    by_id = octoprint_by_id()
    gcode = by_id["octoprint:gcode"]
    assert "read" not in gcode["control"] and gcode["control"]["icon"] == "fa-solid fa-terminal"
    assert gcode["commands"] == [{"name": "Send", "run": {"gcode": ["M117 Hello from System Commands"]},
                                  "role": "run", "icon": "fa-solid fa-paper-plane", "confirm": "Send this G-code?"}]
    event = by_id["octoprint:event"]
    assert "read" not in event["control"] and event["control"]["icon"] == "fa-solid fa-bullhorn"
    assert event["commands"] == [{"name": "Fire", "run": {"event": "systemcommandeditor_custom", "payload": {}},
                                  "role": "run", "icon": "fa-solid fa-bullhorn"}]


def test_octoprint_presets_carry_no_id_or_action_and_no_action_on_commands():
    for preset in octoprint_presets()["presets"]:
        assert "id" not in preset["control"] and "action" not in preset["control"]
        assert all("action" not in command and "group" not in command for command in preset["commands"])


def test_octoprint_snapshot_preset_is_absent_without_a_url():
    assert "octoprint:snapshot" not in octoprint_by_id()
    assert "octoprint:snapshot" not in {p["preset"] for p in octoprint_presets("")["presets"]}


def test_octoprint_snapshot_preset_is_a_link_with_its_url_and_no_commands_and_comes_last():
    group = octoprint_presets("http://printer.local/webcam/?action=snapshot")
    preset = group["presets"][-1]
    assert preset == {
        "preset": "octoprint:snapshot", "name": "Webcam snapshot",
        "control": {"type": "link", "name": "Webcam snapshot", "icon": "fa-solid fa-camera",
                    "url": "http://printer.local/webcam/?action=snapshot"},
        "commands": [],
    }
    assert [p["preset"] for p in group["presets"][:-1]] == [
        "octoprint:connection", "octoprint:job", "octoprint:printer", "octoprint:gcode", "octoprint:event",
    ]


def test_key_for_slugs_names_and_takes_the_fallback_when_empty():
    assert key_for("Printer connection", "control") == "printer_connection"
    assert key_for("Café Lights!", "control") == "cafe_lights"
    assert key_for("***", "control") == "control"


def test_key_for_never_returns_divider_and_counts_past_taken_keys():
    assert key_for("divider", "command") == "divider_2"
    assert key_for("PSU", "control", {"psu"}) == "psu_2"
    assert key_for("PSU", "control", {"psu", "psu_2"}) == "psu_3"


def plugin_switch(name="PSU Control", control_name="Printer power", preset="psucontrol:psu"):
    return {
        "preset": preset, "name": name,
        "control": {"type": "switch", "name": control_name, "icon": "fa-solid fa-plug",
                    "read": {"plugin": "psucontrol", "helper": "get_psu_state"},
                    "on_pattern": "^true$", "off_pattern": "^false$"},
        "commands": [
            {"name": "On", "run": {"plugin": "psucontrol", "helper": "turn_psu_on"}, "role": "on"},
            {"name": "Off", "run": {"plugin": "psucontrol", "helper": "turn_psu_off"}, "role": "off",
             "confirm": "Turn it off?"},
        ],
    }


def test_add_plugin_preset_adds_a_switch_with_its_commands_under_one_divider():
    preset = plugin_switch()
    before = copy.deepcopy(preset)
    devices, actions = add_plugin_preset(preset, [], [])
    assert preset == before
    assert devices == [{
        "id": "printer_power", "type": "switch", "name": "Printer power", "icon": "fa-solid fa-plug",
        "read": {"plugin": "psucontrol", "helper": "get_psu_state"},
        "on_pattern": "^true$", "off_pattern": "^false$", "preset": "psucontrol:psu",
    }]
    assert actions == [
        {"action": "divider", "name": "Plugin devices"},
        {"action": "psu_control_on", "name": "On", "run": {"plugin": "psucontrol", "helper": "turn_psu_on"},
         "role": "on", "group": "printer_power", "preset": "psucontrol:psu"},
        {"action": "psu_control_off", "name": "Off", "run": {"plugin": "psucontrol", "helper": "turn_psu_off"},
         "role": "off", "group": "printer_power", "preset": "psucontrol:psu", "confirm": "Turn it off?"},
    ]


def test_add_plugin_preset_reuses_the_plugin_divider_and_does_not_modify_its_inputs():
    first_devices, first_actions = add_plugin_preset(plugin_switch(), [], [])
    device_snapshot, action_snapshot = copy.deepcopy(first_devices), copy.deepcopy(first_actions)
    second = plugin_switch(name="Other", control_name="Other", preset="x:other")
    devices, actions = add_plugin_preset(second, first_devices, first_actions)
    assert first_devices == device_snapshot and first_actions == action_snapshot
    assert [a["action"] for a in actions].count("divider") == 1
    assert [d["id"] for d in devices] == ["printer_power", "other"]
    assert [a["action"] for a in actions[len(first_actions):]] == ["other_on", "other_off"]
    assert actions[len(first_actions)]["group"] == "other"


def test_add_plugin_preset_takes_a_control_id_already_used_by_a_device_or_a_command():
    existing_device = [{"id": "printer_power", "type": "switch", "name": "Mine"}]
    devices, _ = add_plugin_preset(plugin_switch(), existing_device, [])
    assert devices[-1]["id"] == "printer_power_2"
    existing_action = [{"action": "printer_power", "name": "Mine", "command": "true"}]
    devices, _ = add_plugin_preset(plugin_switch(), [], existing_action)
    assert devices[-1]["id"] == "printer_power_2"


def test_add_plugin_preset_for_a_link_with_no_commands_adds_no_divider():
    link = {
        "preset": "octoprint:snapshot", "name": "Webcam snapshot",
        "control": {"type": "link", "name": "Webcam snapshot", "icon": "fa-solid fa-camera",
                    "url": "http://printer.local/snap"},
        "commands": [],
    }
    devices, actions = add_plugin_preset(link, [], [])
    assert devices == [{"id": "webcam_snapshot", "type": "link", "name": "Webcam snapshot",
                        "icon": "fa-solid fa-camera", "preset": "octoprint:snapshot",
                        "url": "http://printer.local/snap"}]
    assert actions == []


def test_first_start_presets_picks_connection_first_then_plugin_switches_only():
    psu = {"plugin": "psucontrol", "name": "PSU Control", "presets": [
        plugin_switch(),
        {"preset": "psucontrol:job", "name": "Job", "control": {"type": "action", "name": "Job", "icon": "i"},
         "commands": []},
    ]}
    picked = [p["preset"] for p in first_start_presets([psu, octoprint_presets()])]
    assert picked == ["octoprint:connection", "psucontrol:psu"]


def test_first_start_presets_offers_only_the_connection_without_a_plugin_switch():
    assert [p["preset"] for p in first_start_presets([octoprint_presets()])] == ["octoprint:connection"]


# Webcam services. A fake runner: a systemd unit's detect (systemctl cat) exits 0 when the unit is installed, the
# Docker detect exits 0 when docker is set, and sudo -n true exits sudo_ok's code. Other commands exit 1.
def webcam_runner(installed=(), sudo_ok=True, docker=False, calls=None):
    def runner(command, timeout=10):
        if calls is not None:
            calls.append(command)
        if command == "sudo -n true":
            return (0 if sudo_ok else 1), "", None
        if command.startswith("systemctl cat "):
            unit = command.split()[2][: -len(".service")]
            return (0 if unit in installed else 1), "", None
        if command.startswith("test -d /var/run/s6/"):
            return (0 if docker else 1), "", None
        return 1, "", None

    return runner


NO_CAMERAS = "/nonexistent/octoprint_cameras"


def webcam_by_id(group):
    return {preset["preset"]: preset for preset in group["presets"]}


def test_webcam_group_lists_the_systemd_units_in_order_then_docker():
    group = webcam_presets(runner=webcam_runner(), cameras_file=NO_CAMERAS)
    assert group["plugin"] == "webcam" and group["name"] == "Webcam services"
    ids = [preset["preset"] for preset in group["presets"]]
    assert ids == ["webcam:" + unit for unit, _name in WEBCAM_SERVICES] + ["webcam:docker-mjpg-streamer"]
    names = {unit: name for unit, name in WEBCAM_SERVICES}
    assert names["webcamd"] == "Webcam (webcamd)" and names["rtsp-simple-server"] == "Streams (rtsp-simple-server)"
    assert group["presets"][0]["name"] == "Webcam (webcamd)"


def test_webcam_systemd_entry_has_status_patterns_three_commands_and_detect():
    calls = []
    group = webcam_presets(runner=webcam_runner(installed={"motion"}, calls=calls), cameras_file=NO_CAMERAS)
    by_id = webcam_by_id(group)
    for unit, name in WEBCAM_SERVICES:
        preset = by_id["webcam:" + unit]
        control = preset["control"]
        assert preset["name"] == name
        assert control["type"] == "switch" and control["icon"] == "fa-solid fa-video"
        if unit != "camera-streamer":
            assert control["status"] == "systemctl is-active {}.service".format(unit)
        assert control["on_pattern"] == "^active$" and control["off_pattern"] == "^(inactive|failed)$"
        start, stop, restart = preset["commands"]
        assert start == {"name": "Start", "command": "sudo systemctl start {}".format(unit), "role": "on",
                         "icon": "fa-solid fa-play"}
        assert stop == {"name": "Stop", "command": "sudo systemctl stop {}".format(unit), "role": "off",
                        "icon": "fa-solid fa-power-off"}
        assert restart == {"name": "Restart", "command": "sudo systemctl restart {}".format(unit),
                           "role": "restart", "icon": "fa-solid fa-rotate-right"}
        assert "run" not in start and "run" not in stop and "run" not in restart
        assert "systemctl cat {}.service >/dev/null 2>&1".format(unit) in calls
    assert by_id["webcam:motion"]["available"] is True
    assert by_id["webcam:webcamd"]["available"] is False


def test_camera_streamer_status_reads_its_started_units_and_prints_inactive_when_none_match():
    preset = webcam_by_id(webcam_presets(runner=webcam_runner(), cameras_file=NO_CAMERAS))["webcam:camera-streamer"]
    assert preset["control"]["status"] == (
        "systemctl is-active camera-streamer-libcamera.service 'camera-streamer-usb@*.service' "
        "| grep -qx active && echo active || echo inactive"
    )
    assert preset["control"]["on_pattern"] == "^active$"


def test_webcam_sudo_note_only_when_sudo_fails_and_the_unit_is_available():
    failing = webcam_by_id(webcam_presets(runner=webcam_runner(installed={"webcamd"}, sudo_ok=False),
                                          cameras_file=NO_CAMERAS))
    assert failing["webcam:webcamd"]["note"] == "Needs passwordless sudo"
    assert failing["webcam:motion"]["note"] == ""
    working = webcam_by_id(webcam_presets(runner=webcam_runner(installed={"webcamd"}, sudo_ok=True),
                                          cameras_file=NO_CAMERAS))
    assert working["webcam:webcamd"]["note"] == ""
    unavailable = webcam_by_id(webcam_presets(runner=webcam_runner(sudo_ok=False), cameras_file=NO_CAMERAS))
    assert unavailable["webcam:webcamd"]["note"] == ""


def test_webcam_deploy_cameras_come_after_systemd_units_and_take_the_first_word(tmp_path):
    cameras = tmp_path / "octoprint_cameras"
    cameras.write_text("camera:cam_octo port:8000 udev:true\nnot_a_camera line\ncamera:cam_two port:8001\n")
    group = webcam_presets(runner=webcam_runner(installed={"cam_octo"}), cameras_file=str(cameras))
    ids = [preset["preset"] for preset in group["presets"]]
    assert ids[len(WEBCAM_SERVICES):] == ["webcam:cam_octo", "webcam:cam_two", "webcam:docker-mjpg-streamer"]
    by_id = webcam_by_id(group)
    cam = by_id["webcam:cam_octo"]
    assert cam["name"] == "Webcam (cam_octo)" and cam["available"] is True
    assert cam["control"]["status"] == "systemctl is-active cam_octo.service"
    assert cam["commands"][0]["command"] == "sudo systemctl start cam_octo"
    assert by_id["webcam:cam_two"]["available"] is False


def test_webcam_missing_or_unreadable_cameras_file_adds_no_camera(tmp_path):
    group = webcam_presets(runner=webcam_runner(), cameras_file=NO_CAMERAS)
    assert len(group["presets"]) == len(WEBCAM_SERVICES) + 1
    assert webcam_presets(runner=webcam_runner(), cameras_file=str(tmp_path))["presets"] == group["presets"]


def test_webcam_deploy_camera_already_listed_is_skipped(tmp_path):
    cameras = tmp_path / "octoprint_cameras"
    cameras.write_text("camera:webcamd port:8000\n")
    group = webcam_presets(runner=webcam_runner(), cameras_file=str(cameras))
    assert len(group["presets"]) == len(WEBCAM_SERVICES) + 1


def test_webcam_docker_preset_is_last_s6_with_no_sudo():
    calls = []
    group = webcam_presets(runner=webcam_runner(docker=True, calls=calls), cameras_file=NO_CAMERAS)
    docker = group["presets"][-1]
    assert docker["preset"] == "webcam:docker-mjpg-streamer" and docker["name"] == "Webcam (OctoPrint Docker)"
    assert docker["control"]["status"] == "s6-svstat /var/run/s6/services/mjpg-streamer"
    assert docker["control"]["on_pattern"] == "^up" and docker["control"]["off_pattern"] == "^down"
    assert [c["command"] for c in docker["commands"]] == [
        "s6-svc -u /var/run/s6/services/mjpg-streamer",
        "s6-svc -d /var/run/s6/services/mjpg-streamer",
        "s6-svc -r /var/run/s6/services/mjpg-streamer",
    ]
    assert [c["role"] for c in docker["commands"]] == ["on", "off", "restart"]
    assert docker["available"] is True and docker["note"] == ""
    assert "test -d /var/run/s6/services/mjpg-streamer" in calls


def test_webcam_every_command_carries_its_role_icon_and_no_run():
    group = webcam_presets(runner=webcam_runner(), cameras_file=NO_CAMERAS)
    icons = {"on": "fa-solid fa-play", "off": "fa-solid fa-power-off", "restart": "fa-solid fa-rotate-right"}
    for preset in group["presets"]:
        for command in preset["commands"]:
            assert command["icon"] == icons[command["role"]]
            assert "run" not in command


def test_add_plugin_preset_copies_a_shell_command_with_its_icon_and_async_never_a_run():
    preset = {
        "preset": "webcam:webcamd", "name": "Webcam (webcamd)",
        "control": {"type": "switch", "name": "Webcam (webcamd)", "icon": "fa-solid fa-video",
                    "status": "systemctl is-active webcamd.service", "on_pattern": "^active$",
                    "off_pattern": "^(inactive|failed)$"},
        "commands": [
            {"name": "Start", "command": "sudo systemctl start webcamd", "role": "on", "icon": "fa-solid fa-play",
             "async": True},
            {"name": "Stop", "command": "sudo systemctl stop webcamd", "role": "off", "icon": "fa-solid fa-power-off"},
        ],
    }
    devices, actions = add_plugin_preset(preset, [], [])
    assert devices[0]["status"] == "systemctl is-active webcamd.service"
    start, stop = actions[1], actions[2]
    assert start == {"action": "webcam_webcamd_start", "name": "Start", "command": "sudo systemctl start webcamd",
                     "role": "on", "group": "webcam_webcamd", "preset": "webcam:webcamd", "icon": "fa-solid fa-play",
                     "async": True}
    assert stop["command"] == "sudo systemctl stop webcamd" and "async" not in stop
    assert all("run" not in entry for entry in (start, stop))


def test_add_plugin_preset_still_copies_a_run_call_and_no_status_for_a_plugin_control():
    devices, actions = add_plugin_preset(plugin_switch(), [], [])
    assert "status" not in devices[0]
    assert "command" not in actions[1] and actions[1]["run"] == {"plugin": "psucontrol", "helper": "turn_psu_on"}


def test_first_start_presets_skips_an_unavailable_preset_and_keeps_one_without_the_key():
    group = {"plugin": "webcam", "name": "Webcam services", "presets": [
        {**plugin_switch(name="Up", preset="webcam:up"), "available": True},
        {**plugin_switch(name="Down", preset="webcam:down"), "available": False},
        plugin_switch(name="Harvested", preset="x:harvested"),
    ]}
    picked = [p["preset"] for p in first_start_presets([group])]
    assert picked == ["webcam:up", "x:harvested"]


def test_every_plugin_and_octoprint_control_and_command_has_its_table_icon():
    settings = settings_of(
        tplinksmartplug={"arrSmartplugs": [{"ip": "10.0.0.5", "label": "Printer"}]},
        tasmota={"arrSmartplugs": [{"ip": "10.0.0.6", "idx": 1, "label": "Fan"}]},
        octorelay={"r1": {"active": True, "label_text": "Light"}},
        gpiocontrol={"gpio_configurations": [{"name": "Fan"}]},
        usbrelaycontrol={"usbrelay_configurations": [{"name": "Lamp"}]},
    )
    groups = harvest(Plugins("psucontrol", "tplinksmartplug", "tasmota", "octorelay", "gpiocontrol",
                             "usbrelaycontrol", "bettergrblsupport"), settings)
    groups.append(octoprint_presets("http://printer.local/snap"))
    expected = {
        "psucontrol:psu": ("fa-solid fa-power-off", ["fa-solid fa-play", "fa-solid fa-power-off"]),
        "tplinksmartplug:10.0.0.5": ("fa-solid fa-plug", ["fa-solid fa-play", "fa-solid fa-power-off"]),
        "tasmota:10.0.0.6/1": ("fa-solid fa-plug", ["fa-solid fa-play", "fa-solid fa-power-off"]),
        "octorelay:r1": ("fa-solid fa-toggle-on", ["fa-solid fa-play", "fa-solid fa-power-off"]),
        "gpiocontrol:0": ("fa-solid fa-microchip", ["fa-solid fa-play", "fa-solid fa-power-off"]),
        "usbrelaycontrol:0": ("fa-brands fa-usb", ["fa-solid fa-play", "fa-solid fa-power-off"]),
        "bettergrblsupport:grbl": ("fa-solid fa-crosshairs", [
            "fa-solid fa-house", "fa-solid fa-lock-open", "fa-solid fa-moon", "fa-solid fa-rotate-left"]),
        "octoprint:connection": ("fa-solid fa-link", ["fa-solid fa-play", "fa-solid fa-power-off"]),
        "octoprint:job": ("fa-solid fa-hourglass-half", [
            "fa-solid fa-pause", "fa-solid fa-play", "fa-solid fa-stop"]),
        "octoprint:printer": ("fa-solid fa-temperature-half", [
            "fa-solid fa-house", "fa-solid fa-snowflake", "fa-solid fa-hand"]),
        "octoprint:gcode": ("fa-solid fa-terminal", ["fa-solid fa-paper-plane"]),
        "octoprint:event": ("fa-solid fa-bullhorn", ["fa-solid fa-bullhorn"]),
        "octoprint:snapshot": ("fa-solid fa-camera", []),
    }
    seen = {}
    for group in groups:
        for preset in group["presets"]:
            icons = [c["icon"] for c in preset["commands"]]
            seen[preset["preset"]] = (preset["control"]["icon"], icons)
            assert preset["control"]["icon"], preset["preset"]
            assert all(icons), preset["preset"]
    assert seen == expected


def test_no_shared_icon_constant_remains_in_presets():
    import octoprint_systemcommandeditor.presets as presets_module

    assert not hasattr(presets_module, "ICON")


def test_docker_webcam_preset_never_shows_the_sudo_note_even_when_sudo_fails():
    # Docker runs with no sudo, so its note stays empty when sudo fails and its directory exists
    group = webcam_presets(runner=webcam_runner(docker=True, sudo_ok=False), cameras_file=NO_CAMERAS)
    docker = group["presets"][-1]
    assert docker["available"] is True
    assert docker["note"] == ""



def test_bettergrbl_preset_defaults_gate_unlock_sleep_and_home_by_control_state():
    preset = only_preset(harvest(Plugins("bettergrblsupport"), settings_of()), "bettergrblsupport:grbl")
    home, unlock, sleep, reset = preset["commands"]
    assert home["when_control"] == {"mode": "only", "states": ["Idle", "Alarm"], "effect": "disable"}
    assert unlock["when_control"] == {"mode": "only", "states": ["Alarm"], "effect": "disable"}
    assert sleep["when_control"] == {"mode": "never", "states": ["Sleep"], "effect": "disable"}
    assert "when_control" not in reset
    assert all("when" not in command for command in preset["commands"])


def test_octoprint_preset_defaults_gate_print_job_and_printer_home_by_printer_state():
    presets = octoprint_by_id()
    pause, resume, cancel = presets["octoprint:job"]["commands"]
    assert pause["when"] == {"mode": "only", "states": ["printing"], "effect": "disable"}
    assert resume["when"] == {"mode": "only", "states": ["paused"], "effect": "disable"}
    assert cancel["when"] == {"mode": "only", "states": ["printing", "paused"], "effect": "disable"}
    home, cool_down, emergency = presets["octoprint:printer"]["commands"]
    assert home["when"] == {"mode": "never", "states": ["printing"], "effect": "disable"}
    assert "when" not in cool_down and "when" not in emergency


def test_octoprint_connection_gcode_and_event_carry_no_condition():
    presets = octoprint_by_id()
    for key in ("octoprint:connection", "octoprint:gcode", "octoprint:event"):
        for command in presets[key]["commands"]:
            assert "when" not in command and "when_control" not in command


def test_add_plugin_preset_copies_a_commands_when_and_when_control():
    preset = {
        "preset": "p:x", "name": "X", "control": {"type": "action", "name": "X", "icon": "i"},
        "commands": [
            {"name": "Go", "run": {"plugin": "p", "helper": "go"}, "role": "run", "icon": "i",
             "when": {"mode": "only", "states": ["ready"], "effect": "disable"},
             "when_control": {"mode": "never", "states": ["Hold"], "effect": "hide"}},
            {"name": "Plain", "run": {"plugin": "p", "helper": "plain"}, "role": "run", "icon": "i"},
        ],
    }
    _devices, actions = add_plugin_preset(preset, [], [])
    go, plain = actions[1], actions[2]
    assert go["when"] == {"mode": "only", "states": ["ready"], "effect": "disable"}
    assert go["when_control"] == {"mode": "never", "states": ["Hold"], "effect": "hide"}
    assert "when" not in plain and "when_control" not in plain


def test_preset_commands_without_a_condition_carry_none():
    presets = octoprint_by_id()
    for key in ("octoprint:printer",):
        cool_down, emergency = presets[key]["commands"][1], presets[key]["commands"][2]
        assert "when" not in cool_down and "when" not in emergency


class Helpers:
    """A fake plugin manager whose get_helpers answers the given helpers for one plugin only."""

    def __init__(self, plugin_id=None, **helpers):
        self.plugin_id = plugin_id
        self.helpers = helpers

    def get_helpers(self, name, *names):
        if name != self.plugin_id:
            return None
        return {helper: self.helpers[helper] for helper in names if helper in self.helpers}


def boom():
    raise RuntimeError("no state")


GRBL_CATALOG = ["Idle", "Run", "Hold", "Jog", "Alarm", "Door", "Check", "Home", "Sleep", "Reset", "N/A"]


def test_state_list_for_a_printer_read_is_the_nineteen_printer_words_in_order():
    result = state_list({"printer": "state"}, Helpers())
    assert result == {"source": "printer", "states": PRINTER_STATE_WORDS}
    assert len(PRINTER_STATE_WORDS) == 19
    assert PRINTER_STATE_WORDS[0] == "Offline" and PRINTER_STATE_WORDS[-1] == "Error"


def test_state_list_takes_a_plugin_helper_list_first():
    manager = Helpers("bettergrblsupport", state_values=lambda: ["Idle", "Hold"])
    result = state_list({"plugin": "bettergrblsupport", "helper": "state", "field": "state"}, manager)
    assert result == {"source": "helper", "states": ["Idle", "Hold"]}


def test_state_list_falls_back_to_the_catalog_when_the_helper_raises():
    manager = Helpers("bettergrblsupport", state_values=boom)
    assert state_list({"plugin": "bettergrblsupport", "helper": "state"}, manager) == {
        "source": "catalog", "states": GRBL_CATALOG,
    }


def test_state_list_falls_back_to_the_catalog_when_the_helper_is_absent_or_returns_no_list():
    absent = {"plugin": "bettergrblsupport", "helper": "state"}
    assert state_list(absent, Helpers()) == {"source": "catalog", "states": GRBL_CATALOG}
    not_a_list = Helpers("bettergrblsupport", state_values=lambda: "Idle")
    assert state_list(absent, not_a_list) == {"source": "catalog", "states": GRBL_CATALOG}


def test_state_list_is_free_for_an_unknown_plugin_mqtt_and_a_status_line():
    assert state_list({"plugin": "nope", "helper": "x"}, Helpers()) == {"source": "free", "states": []}
    assert state_list({"mqtt": "a/b"}, Helpers()) == {"source": "free", "states": []}
    assert state_list("echo Idle", Helpers()) == {"source": "free", "states": []}
    assert state_list(None, Helpers()) == {"source": "free", "states": []}


def test_state_list_catalog_lists_only_bettergrbl():
    assert set(STATE_LISTS) == {"bettergrblsupport"}
    assert STATE_LISTS["bettergrblsupport"] == GRBL_CATALOG
