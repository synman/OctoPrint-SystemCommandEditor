import os
import shlex
import sys
import time

from octoprint_systemcommandeditor.status import check_devices, evaluate, run_status


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


def test_run_status_cuts_output_to_200_characters():
    command = " ".join(shlex.quote(part) for part in [sys.executable, "-c", "print('x' * 300)"])
    exit_code, output, error = run_status(command)
    assert (exit_code, error) == (0, None)
    assert output == "x" * 200


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
