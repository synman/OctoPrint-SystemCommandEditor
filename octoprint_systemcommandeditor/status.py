import concurrent.futures
import json
import os
import re
import signal
import string
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
        first = (output.splitlines() or [""])[0]
        named = _temperature_pair(device, first)
        if named is not None:
            # the pair shows as its actual; a format may also name {actual} and {target}
            first = str(named["actual"])
        fmt = device.get("format")
        if isinstance(fmt, str) and fmt:
            return format_value(fmt, first, named)
        return first
    return None


def _temperature_pair(device, text):
    # the {"actual", "target"} a printer temperature read with no field answers, or None for any other read or text
    read = device.get("read")
    if not isinstance(read, dict) or read.get("printer") != "temperature" or read.get("field") is not None:
        return None
    try:
        pair = json.loads(text)
    except ValueError:
        return None
    if not isinstance(pair, dict) or pair.get("actual") is None:
        return None
    return {"actual": pair["actual"], "target": pair.get("target")}


FORMAT_INT_TYPES = set("dboxXcn")


def format_value(pattern, text, named=None):
    # A readout's optional format: {value} fields in Python's format syntax. ``named`` adds number fields by name,
    # such as a temperature's {actual} and {target}. Any other field, or a named number that is None, leaves the text.
    named = named or {}
    try:
        fields = [field for field in string.Formatter().parse(pattern) if field[1] is not None]
        if not fields:
            return text
        for _literal, name, spec, conversion in fields:
            if (name != "value" and name not in named) or conversion is not None or "{" in spec or "}" in spec:
                return text
            if name != "value" and named[name] is None:
                return text
        # a number is used only when some field has a format spec; a bare {value} keeps the text. With mixed fields the
        # number is used for all of them.
        value = text
        specs = [spec for _l, name, spec, _c in fields if spec and name == "value"]
        if specs:
            try:
                value = float(text.strip())
            except ValueError:
                value = text
        values = dict(named, value=value)
        for name, number in values.items():
            wants_int = any(spec and spec[-1] in FORMAT_INT_TYPES for _l, n, spec, _c in fields if n == name)
            if isinstance(number, float) and number.is_integer() and wants_int:
                values[name] = int(number)
        return pattern.format(**values)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return text


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
