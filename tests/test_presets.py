import pytest

pytest.importorskip("octoprint")

from octoprint_systemcommandeditor.presets import CATALOG, detect, device_of, sample_devices  # noqa: E402

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


def test_sample_devices_keeps_only_sample_and_available_in_catalog_order():
    detected = detect(
        runner=fake_runner(
            {
                "memory": (1, "x", None),  # sample, unavailable
                "ip": (0, "10.0.0.2", None),  # available, not a sample
                "throttle": (0, "OK", None),  # sample, available
            }
        )
    )
    samples = sample_devices(detected)
    assert [record["id"] for record in samples] == ["disk", "load", "uptime", "cpu_temp", "throttle"]
    for record in samples:
        assert set(record) == {"id", "type", "name", "icon", "status", "section"}
        assert record["section"] == "Status"
