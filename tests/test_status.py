import os
import shlex
import sys
import time

import pytest

from octoprint_systemcommandeditor.status import check_devices, evaluate, format_value, run_status


def switch(**extra):
    return dict({"id": "dev", "type": "switch", "status": "x"}, **extra)


def test_switch_on_pattern_matches():
    assert evaluate(switch(on_pattern="ON"), [], 0, "state ON", None) == "on"


def test_switch_off_pattern_matches():
    assert evaluate(switch(off_pattern="OFF"), [], 0, "state OFF", None) == "off"


def test_switch_pattern_set_but_no_match_is_unknown():
    assert evaluate(switch(on_pattern="ON"), [], 0, "nothing", None) == "unknown"


def test_switch_off_pattern_set_but_no_match_is_unknown():
    assert evaluate(switch(off_pattern="OFF"), [], 0, "nothing", None) == "unknown"


def test_switch_without_patterns_exit_zero_is_on():
    assert evaluate(switch(), [], 0, "", None) == "on"


def test_switch_without_patterns_other_exit_is_off():
    assert evaluate(switch(), [], 3, "", None) == "off"


def test_switch_invalid_regex_is_unknown():
    assert evaluate(switch(on_pattern="(["), [], 0, "anything", None) == "unknown"


def test_error_is_unknown():
    assert evaluate(switch(), [], 0, "", "boom") == "unknown"


def test_selector_matching_choice_returns_it():
    device = {"id": "sel", "type": "selector"}
    assert evaluate(device, ["slow", "fast"], 0, "  fast \n", None) == "fast"


def test_selector_not_in_choices_is_unknown():
    device = {"id": "sel", "type": "selector"}
    assert evaluate(device, ["slow", "fast"], 0, "medium", None) == "unknown"


def test_readout_returns_first_line():
    device = {"id": "ro", "type": "readout"}
    assert evaluate(device, [], 0, "first\nsecond", None) == "first"


def test_readout_empty_output_is_empty_string():
    device = {"id": "ro", "type": "readout"}
    assert evaluate(device, [], 0, "", None) == ""


def test_unknown_type_is_none():
    assert evaluate({"id": "x", "type": "other"}, [], 0, "out", None) is None


def test_run_status_success():
    assert run_status("echo hi") == (0, "hi", None)


def test_run_status_exit_code():
    assert run_status("exit 3") == (3, "", None)


def test_run_status_timeout():
    assert run_status("sleep 2", timeout=0.3) == (None, "", "timeout")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_run_status_timeout_leaves_no_process_behind(tmp_path):
    # The background sleep's pid is written to a file, so the check tracks that exact process
    # rather than searching the process table, which would also match this test's own shell.
    pidfile = tmp_path / "pid"
    command = f"sleep 7.7713 & echo $! > {shlex.quote(str(pidfile))}; wait"
    assert run_status(command, timeout=0.5) == (None, "", "timeout")
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 1.0
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_run_status_returns_full_output():
    command = " ".join(shlex.quote(part) for part in [sys.executable, "-c", "print('x' * 300)"])
    exit_code, output, error = run_status(command)
    assert (exit_code, error) == (0, None)
    assert output == "x" * 300


def test_check_devices_unknown_id():
    result = check_devices([], [], ["nope"], runner=lambda c, t: (0, "", None))
    assert result == {"nope": {"state": "unknown", "output": "", "exit": None, "error": "unknown device"}}


def test_check_devices_device_without_status_never_calls_runner():
    calls = []

    def runner(command, timeout):
        calls.append(command)
        return 0, "", None

    result = check_devices([{"id": "d", "type": "switch"}], [], ["d"], runner=runner)
    assert calls == []
    assert result == {"d": {"state": None, "output": "", "exit": None, "error": None}}


def test_check_devices_selector_choices_come_from_entries():
    devices = [{"id": "sel", "type": "selector", "status": "cmd"}]
    entries = [
        {"group": "sel", "role": "choice", "value": "slow"},
        {"group": "sel", "role": "choice", "value": "fast"},
        {"group": "other", "role": "choice", "value": "medium"},
        {"group": "sel", "role": "label", "value": "ignored"},
    ]
    result = check_devices(devices, entries, ["sel"], runner=lambda c, t: (0, "fast", None))
    assert result["sel"]["state"] == "fast"

    result = check_devices(devices, entries, ["sel"], runner=lambda c, t: (0, "medium", None))
    assert result["sel"]["state"] == "unknown"


def test_check_devices_choice_ignores_entries_with_other_roles():
    devices = [{"id": "sel", "type": "selector", "status": "cmd"}]
    entries = [
        {"group": "sel", "role": "on", "value": "ghost"},
        {"group": "sel", "role": "choice", "value": "fast"},
    ]
    result = check_devices(devices, entries, ["sel"], runner=lambda c, t: (0, "ghost", None))
    assert result["sel"]["state"] == "unknown"

    result = check_devices(devices, entries, ["sel"], runner=lambda c, t: (0, "fast", None))
    assert result["sel"]["state"] == "fast"


def test_check_devices_runs_in_parallel():
    devices = [{"id": f"d{i}", "type": "readout", "status": "cmd"} for i in range(3)]

    def slow_runner(command, timeout):
        time.sleep(0.3)
        return 0, "ok", None

    start = time.monotonic()
    result = check_devices(devices, [], ["d0", "d1", "d2"], runner=slow_runner)
    elapsed = time.monotonic() - start

    assert elapsed < 0.8
    assert {key: value["state"] for key, value in result.items()} == {"d0": "ok", "d1": "ok", "d2": "ok"}


def test_check_devices_stores_output_cut_to_200_characters():
    devices = [{"id": "ro", "type": "readout", "status": "cmd"}]
    result = check_devices(devices, [], ["ro"], runner=lambda c, t: (0, "x" * 300, None))
    assert result["ro"]["output"] == "x" * 200


def test_check_devices_switch_pattern_past_200_characters():
    devices = [
        {
            "id": "sw",
            "type": "switch",
            "status": "cmd",
            "on_pattern": '"relay_state": ?1',
            "off_pattern": '"relay_state": ?0',
        }
    ]
    result = check_devices(
        devices, [], ["sw"], runner=lambda c, t: (0, "a" * 250 + '"relay_state":1', None)
    )
    assert result["sw"]["state"] == "on"


def test_check_devices_readout_uses_full_first_line():
    devices = [{"id": "ro", "type": "readout", "status": "cmd"}]
    result = check_devices(devices, [], ["ro"], runner=lambda c, t: (0, "y" * 250, None))
    assert result["ro"]["state"] == "y" * 250
    assert result["ro"]["output"] == "y" * 200


def test_check_devices_real_command_pattern_past_200_characters():
    script = "print('a' * 250 + '\"relay_state\":0')"
    command = " ".join(shlex.quote(part) for part in [sys.executable, "-c", script])
    devices = [
        {
            "id": "plug",
            "type": "switch",
            "status": command,
            "on_pattern": '"relay_state": ?1',
            "off_pattern": '"relay_state": ?0',
        }
    ]
    result = check_devices(devices, [], ["plug"])
    assert result["plug"]["state"] == "off"
    assert len(result["plug"]["output"]) == 200


def test_action_returns_first_line_of_output_as_readout_does():
    assert evaluate({"type": "action"}, [], 0, "Last backup 2 h ago\nmore", None) == "Last backup 2 h ago"


def test_action_error_is_unknown():
    assert evaluate({"type": "action"}, [], None, "", "timeout") == "unknown"


@pytest.mark.parametrize("pattern, text, expected", [
    ("{value:.1f}%", "38", "38.0%"),
    ("${value:,.2f}", "1234.5", "$1,234.50"),
    ("{value:.0f}°F", "82.5", "82°F"),
    ("{value:d} items", "3", "3 items"),
    ("{value:.1f}%", "n/a", "n/a"),
    ("{value.__class__}", "1", "1"),
    ("{}", "1", "1"),
    ("Up {value}", "3 days", "Up 3 days"),
    ("{value}%", "38", "38%"),
    ("{value} = {value:.1f}", "38", "38.0 = 38.0"),
    ("{value:.1f", "1", "1"),
    ("{0}", "1", "1"),
    ("{value!r}", "1", "1"),
    ("{other}", "1", "1"),
    ("{value[0]}", "1", "1"),
    ("{value:{x}}", "1", "1"),
    ("no field", "1", "1"),
    ("{value:d}", "3.5", "3.5"),
])
def test_format_value_pattern_contract(pattern, text, expected):
    assert format_value(pattern, text) == expected


def readout(**extra):
    return dict({"id": "dev", "type": "readout", "status": "x"}, **extra)


def test_readout_uses_format_on_first_line():
    assert evaluate(readout(format="{value:.1f}%"), [], 0, "38\nignored", None) == "38.0%"


def test_action_uses_format_on_first_line():
    device = {"id": "dev", "type": "action", "status": "x", "format": "{value:,.2f} kW"}
    assert evaluate(device, [], 0, "1234.5", None) == "1,234.50 kW"


def test_readout_without_format_is_unchanged():
    assert evaluate(readout(), [], 0, "38\nignored", None) == "38"


def test_switch_ignores_format():
    assert evaluate(switch(on_pattern="ON", format="{value}!"), [], 0, "ON", None) == "on"


def test_selector_ignores_format():
    device = {"id": "dev", "type": "selector", "status": "x", "format": "[{value}]"}
    assert evaluate(device, ["a"], 0, "a", None) == "a"
