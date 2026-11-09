import concurrent.futures
import os
import re
import signal
import subprocess

OUTPUT_LIMIT = 200


def _kill_group(proc):
    # The command runs in its own session, so its whole process tree shares this group id.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def run_status(command, timeout=10):
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as e:
        return None, "", str(e)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return None, "", "timeout"
    return proc.returncode, stdout.strip(), None


def evaluate(device, choice_values, exit_code, output, error):
    if error:
        return "unknown"
    kind = device.get("type")
    if kind == "switch":
        return _switch(device, exit_code, output)
    if kind == "selector":
        value = output.strip()
        return value if value in choice_values else "unknown"
    if kind in ("readout", "action"):
        return (output.splitlines() or [""])[0]
    return None


def _switch(device, exit_code, output):
    on_pattern = device.get("on_pattern")
    off_pattern = device.get("off_pattern")
    try:
        if on_pattern and re.search(on_pattern, output):
            return "on"
        if off_pattern and re.search(off_pattern, output):
            return "off"
    except re.error:
        return "unknown"
    if on_pattern or off_pattern:
        return "unknown"
    return "on" if exit_code == 0 else "off"


def check_devices(devices, entries, ids, runner=run_status, timeout=10):
    by_id = {device.get("id"): device for device in devices}
    results = {}
    pending = []
    for device_id in ids:
        device = by_id.get(device_id)
        if device is None:
            results[device_id] = {"state": "unknown", "output": "", "exit": None, "error": "unknown device"}
        elif not device.get("status"):
            results[device_id] = {"state": None, "output": "", "exit": None, "error": None}
        else:
            pending.append(device)

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
            outcomes = list(pool.map(lambda device: runner(device["status"], timeout), pending))
        for device, (exit_code, output, error) in zip(pending, outcomes):
            choices = [
                entry.get("value")
                for entry in entries
                if entry.get("group") == device["id"] and entry.get("role") == "choice"
            ]
            state = evaluate(device, choices, exit_code, output, error)
            results[device["id"]] = {
                "state": state,
                "output": output[:OUTPUT_LIMIT],
                "exit": exit_code,
                "error": error,
            }

    return results
