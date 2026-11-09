import pytest

pytest.importorskip("octoprint")

from octoprint_systemcommandeditor.presets import (  # noqa: E402
    CATALOG,
    detect,
    detect_maintenance,
    device_of,
    entry_of,
    maintenance,
    sample_devices,
)

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
        assert item["section"] == "Status"


def test_device_of_builds_readout_record_with_six_keys():
    record = device_of(CATALOG[0])
    assert record == {
        "id": "disk",
        "type": "readout",
        "name": "Free disk",
        "icon": "fa-solid fa-hard-drive",
        "status": CATALOG[0]["status"],
        "section": "Status",
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
        assert set(record) == {"id", "type", "name", "icon", "status", "section"}
        assert record["section"] == "Status"


ENV = {
    "octoprint": "/opt/my venv/bin/octoprint",
    "basedir": "/home/pi/my octo",
    "timelapse": "/home/pi/my octo/timelapse",
    "logs": "/home/pi/my octo/logs",
}
MAINTENANCE_ACTIONS = [
    "backup_now",
    "webcam_restart",
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


def test_maintenance_returns_seven_actions_in_order_with_quoted_paths():
    presets = maintenance(ENV)
    assert [preset["action"] for preset in presets] == MAINTENANCE_ACTIONS
    for preset in presets:
        assert set(preset) == {"action", "name", "icon", "command", "confirm", "async", "sample", "needs_sudo", "detect"}
    backup = presets[0]
    assert backup["command"] == "'/opt/my venv/bin/octoprint' --basedir '/home/pi/my octo' plugins backup:backup"
    assert backup["detect"] == "test -x '/opt/my venv/bin/octoprint'"
    assert backup["async"] is True and backup["sample"] is True and backup["needs_sudo"] is False
    assert presets[2]["command"] == "find '/home/pi/my octo/timelapse' -maxdepth 1 -type f -mtime +30 -delete"


def test_maintenance_samples_are_backup_and_webcam_only():
    assert [preset["action"] for preset in maintenance(ENV) if preset["sample"]] == ["backup_now", "webcam_restart"]


def test_detect_maintenance_marks_availability_from_detect_exit_code():
    listed = detect_maintenance(ENV, runner=maintenance_runner({"sync_clock": 1, "os_update": None}))
    by_action = {item["action"]: item for item in listed}
    assert [item["action"] for item in listed] == MAINTENANCE_ACTIONS
    assert by_action["sync_clock"]["available"] is False
    assert by_action["os_update"]["available"] is False
    assert by_action["backup_now"]["available"] is True
    assert set(listed[0]) == {"action", "name", "icon", "command", "confirm", "async", "sample", "available", "note"}


def test_detect_maintenance_sudo_note_only_when_sudo_fails_and_preset_needs_it():
    calls = []
    listed = detect_maintenance(ENV, runner=maintenance_runner({}, sudo_exit=1, calls=calls))
    by_action = {item["action"]: item for item in listed}
    assert by_action["webcam_restart"]["note"] == "Needs passwordless sudo"
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

