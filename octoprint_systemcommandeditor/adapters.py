# coding=utf-8
"""Calls this plugin makes into other OctoPrint plugins, for a command's ``run`` and a control's ``read``.

Three kinds are run here, each naming its plugin:

- ``{"plugin", "helper", "args"}``: a helper the plugin exports in ``__plugin_helpers__``.
- ``{"plugin", "api", "data"}``: the plugin's ``on_api_command(command, data)``.
- ``{"plugin", "method", "args"}``: a method on the plugin's implementation.

A call may carry ``"field"``: when its answer is a dict, the answer becomes that key's value. A ``read`` may also be
``{"mqtt": "<topic>"}``, the last payload seen on the topic (see MqttCache); with ``"field"``, a JSON object payload
becomes that key's value.

Every call returns ``(output_text, error)`` and never raises. Kept free of OctoPrint imports, so the plugin manager
is passed in.
"""

import concurrent.futures
import json
import threading

import flask

PLUGIN_KINDS = ("helper", "api", "method")


def to_text(value):
    # a plugin answer as the text a shell command would print
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def parsed(value):
    # a flask Response carries its answer as a JSON body; a non-JSON body is its text; anything else is the answer
    if hasattr(value, "get_data"):
        body = value.get_data(as_text=True)
        try:
            return json.loads(body)
        except ValueError:
            return body
    return value


def api_text(value):
    return to_text(parsed(value))


def answer_text(spec, value):
    # a call's "field" takes one key out of a dict answer; a missing key gives "" (to_text of None)
    value = parsed(value)
    field = spec.get("field")
    if field is not None and isinstance(value, dict):
        value = value.get(field)
    return to_text(value)


def is_plugin_run(spec):
    return isinstance(spec, dict) and "plugin" in spec and any(kind in spec for kind in PLUGIN_KINDS)


def is_mqtt_read(spec):
    return isinstance(spec, dict) and "mqtt" in spec


def _first_line(text):
    lines = text.splitlines()
    return lines[0] if lines else text


def run_call(spec, plugin_manager):
    """Run one plugin call. Returns ``(output_text, error)``; ``error`` is None on success."""
    plugin_id = spec.get("plugin", "")
    try:
        info = plugin_manager.get_plugin_info(plugin_id)
        if info is None:
            return "", "{} is not installed".format(plugin_id)
        implementation = info.implementation
        if "helper" in spec:
            name = spec["helper"]
            helpers = plugin_manager.get_helpers(plugin_id, name) or {}
            if name not in helpers:
                return "", "{} has no {}".format(plugin_id, name)
            return answer_text(spec, helpers[name](**(spec.get("args") or {}))), None
        if "method" in spec:
            name = spec["method"]
            method = getattr(implementation, name, None)
            if method is None:
                return "", "{} has no {}".format(plugin_id, name)
            return answer_text(spec, method(**(spec.get("args") or {}))), None
        if "api" in spec:
            handler = getattr(implementation, "on_api_command", None)
            if handler is None:
                return "", "{} has no {}".format(plugin_id, spec["api"])
            return answer_text(spec, handler(spec["api"], spec.get("data") or {})), None
        return "", "unsupported run kind"
    except Exception as e:
        return "", _first_line("{}: {}: {}".format(plugin_id, type(e).__name__, e))


def carried(call):
    """``call`` bound to the request context active now, so a worker thread sees what the request sees.

    A plugin's api handler answers with flask.jsonify and may read flask.request, which need that context. Call this
    on the request's own thread: the copy captures whatever is active when it is made. Outside a request it is
    ``call`` itself.
    """
    if flask.has_request_context():
        return flask.copy_current_request_context(call)
    return call


def run_with_timeout(spec, plugin_manager, timeout=10, call=run_call):
    """``call`` (``run_call``, or ``carried(run_call)`` from a request) with a deadline.

    A call that overruns keeps its thread, but the caller gets "timed out" now.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(call, spec, plugin_manager).result(timeout)
    except concurrent.futures.TimeoutError:
        return "", "timed out"
    finally:
        pool.shutdown(wait=False)


class MqttCache:
    """The last payload of each MQTT topic read, subscribed once per topic through the mqtt plugin's helper."""

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribed = set()
        self._payloads = {}

    def read(self, topic, plugin_manager):
        """``(text, error)``: "" until a message has arrived on the topic."""
        with self._lock:
            if topic not in self._subscribed:
                helpers = plugin_manager.get_helpers("mqtt", "mqtt_subscribe") or {}
                subscribe = helpers.get("mqtt_subscribe")
                if subscribe is None:
                    return "", "mqtt is not installed"
                try:
                    subscribe(topic, self._store(topic))
                except Exception as e:
                    return "", _first_line("mqtt: {}: {}".format(type(e).__name__, e))
                self._subscribed.add(topic)
            return self._payloads.get(topic, ""), None

    def _store(self, topic):
        # the mqtt plugin calls a subscriber as callback(topic, payload, *args, retained=..., qos=...)
        def store(_topic, payload, *args, **kwargs):
            text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
            with self._lock:
                self._payloads[topic] = text

        return store


def mqtt_field(payload, field):
    # one key of a JSON object payload; a payload that is not a JSON object is kept as it came
    try:
        value = json.loads(payload)
    except ValueError:
        return payload
    if not isinstance(value, dict):
        return payload
    return answer_text({"field": field}, value)


def read_outcome(spec, plugin_manager, timeout=10, call=run_call, mqtt=None):
    """A ``read`` as the ``(exit, output, error)`` triple a shell status command produces. Exit 0 means no error.

    A plugin call runs as ``run_with_timeout`` does. An MQTT read answers from ``mqtt``, the plugin's MqttCache.
    """
    if is_mqtt_read(spec):
        if mqtt is None:
            return None, "", "mqtt read has no cache"
        output, error = mqtt.read(spec["mqtt"], plugin_manager)
        if not error and spec.get("field") is not None:
            output = mqtt_field(output, spec["field"])
        return (None if error else 0), output, error
    output, error = run_with_timeout(spec, plugin_manager, timeout, call)
    return (None if error else 0), output, error


def state_word(text):
    """A control's state word: the first line of its read or status text, cut at the first ":" and trimmed.

    None for no text, or when the word is empty.
    """
    if text is None or not text.strip():
        return None
    word = text.strip().splitlines()[0].split(":", 1)[0].strip()
    return word or None


def _effect(when, listed):
    # the effect a condition gives when the value is (or is not) in its states; None when it does not block
    if not when:
        return None
    effect = "disable" if when.get("effect") == "disable" else "hide"
    if when.get("mode") == "never" and listed:
        return effect
    if when.get("mode") == "only" and not listed:
        return effect
    return None


def condition_effect(when, state):
    """What a command's ``when`` does in ``state``: "hide" or "disable", or None when it does not apply."""
    if not when:
        return None
    return _effect(when, state in (when.get("states") or []))


def _printer_reason(when):
    states = " or ".join(when.get("states") or [])
    if when.get("mode") == "only":
        return "Only available while {}".format(states)
    return "Not available while {}".format(states)


def command_block(entry, printer_state, control_word, control_name):
    """Why a command is blocked, as ``{"effect": "hide" | "disable", "reason": str}``, or None when it is not.

    The printer ``when`` is judged first, then the ``when_control`` of the entry's group, against the control's
    state word. An unknown word (None) never blocks, and a ``when_control`` on an entry with no group is ignored.
    The word matches case-insensitively. The effect is "hide" when any blocking condition hides, else "disable";
    the reason is the first blocking condition's.
    """
    blocks = []
    printer_effect = condition_effect(entry.get("when"), printer_state)
    if printer_effect:
        blocks.append((printer_effect, _printer_reason(entry["when"])))
    control = entry.get("when_control") if entry.get("group") else None
    if control and control_word is not None:
        listed = control_word.lower() in [str(state).lower() for state in control.get("states") or []]
        control_effect = _effect(control, listed)
        if control_effect:
            blocks.append((control_effect, "Not available while {} is {}".format(control_name, control_word)))
    if not blocks:
        return None
    effect = "hide" if any(kind == "hide" for kind, _reason in blocks) else "disable"
    return {"effect": effect, "reason": blocks[0][1]}


# Built-in kinds run against OctoPrint itself: G-code, a printer call, a custom event; and the printer readouts.
NOT_CONNECTED = "The printer is not connected"
HOME_AXES = ["x", "y", "z"]
BUILTIN_RUN_KEYS = ("gcode", "printer", "event")


def is_builtin_run(spec):
    return isinstance(spec, dict) and any(key in spec for key in BUILTIN_RUN_KEYS)


def is_run_spec(spec):
    return is_plugin_run(spec) or is_builtin_run(spec)


def is_builtin_read(spec):
    return isinstance(spec, dict) and "printer" in spec


def _cool_down(printer):
    for heater in printer.get_current_temperatures():
        printer.set_temperature(heater, 0)


# a printer call and what it does; the ones not in UNCONNECTED_OK need a connected printer first
PRINTER_CALLS = {
    "connect": lambda printer: printer.connect(),
    "disconnect": lambda printer: printer.disconnect(),
    "emergency_stop": lambda printer: printer.commands(["M112"], force=True),
    "pause": lambda printer: printer.pause_print(),
    "resume": lambda printer: printer.resume_print(),
    "cancel": lambda printer: printer.cancel_print(),
    "home": lambda printer: printer.home(list(HOME_AXES)),
    "cool_down": _cool_down,
}
UNCONNECTED_OK = ("connect", "disconnect", "emergency_stop")


def _send_gcode(gcode, printer):
    if not printer.is_operational():
        return "", NOT_CONNECTED
    lines = [line for line in ([gcode] if isinstance(gcode, str) else gcode or []) if line.strip()]
    if not lines:
        return "", "No G-code to send"
    printer.commands(lines)
    return "", None


def _printer_call(call, printer):
    if call not in PRINTER_CALLS:
        return "", "Unknown printer call {}".format(call)
    if call not in UNCONNECTED_OK and not printer.is_operational():
        return "", NOT_CONNECTED
    PRINTER_CALLS[call](printer)
    return "", None


def run_builtin(spec, printer, event_manager):
    """Run one built-in run kind. Returns ``(output_text, error)``; ``error`` is None on success, and it never raises.

    ``{"gcode": [...]}`` sends the non-blank lines; ``{"printer": call}`` makes one printer call; ``{"event": name,
    "payload": {...}}`` fires the event and does not wait for its listeners.
    """
    try:
        if "gcode" in spec:
            return _send_gcode(spec["gcode"], printer)
        if "event" in spec:
            event_manager.fire(spec["event"], spec.get("payload") or {})
            return "", None
        return _printer_call(spec["printer"], printer)
    except Exception as e:
        return "", _first_line("{}: {}".format(type(e).__name__, e))


def _heater_text(label, reading):
    actual = round(reading["actual"])
    target = reading.get("target")
    return "{} {}".format(label, actual) if target is None else "{} {}/{}".format(label, actual, round(target))


def _temperatures_text(temperatures):
    # tools in name order, then the bed; a heater with no target shows its actual only
    tools = sorted(name for name in temperatures if name.startswith("tool"))
    parts = [_heater_text("T" + name[len("tool"):], temperatures[name]) for name in tools]
    if "bed" in temperatures:
        parts.append(_heater_text("Bed", temperatures["bed"]))
    return " · ".join(parts)


def _number_text(value):
    # a bare number as text, for the control's format to shape; None is blank
    return "" if value is None else str(value)


def _progress_text(data):
    # the job's completion as a bare number of percent; no job means no completion to show
    return _number_text(((data or {}).get("progress") or {}).get("completion"))


TEMPERATURE_FIELDS = ("actual", "target")


def _temperature_text(temperatures, item, field):
    """One item of OctoPrint's current temperatures, by the name OctoPrint holds it under.

    With ``field`` "actual" or "target" the text is that bare number. With no field it is the pair as one JSON
    object, ``{"actual": n, "target": n or null}``, which the control's format reads as {actual} and {target}
    (see status.evaluate). An item the printer does not report, or one with no actual, is blank.
    """
    reading = temperatures.get(item) if isinstance(temperatures, dict) else None
    if not isinstance(reading, dict):
        return ""
    if field is not None:
        return _number_text(reading.get(field))
    if reading.get("actual") is None:
        return ""
    return json.dumps({"actual": reading["actual"], "target": reading.get("target")})


def read_builtin(spec, printer):
    """Run one built-in read live. Returns ``(text, error)``; it never raises."""
    kind = spec.get("printer")
    try:
        if kind == "state":
            return printer.get_state_string(), None
        if kind == "temperature":
            field = spec.get("field")
            if field is not None and field not in TEMPERATURE_FIELDS:
                return "", "Unknown temperature field {}".format(field)
            return _temperature_text(printer.get_current_temperatures(), spec.get("item"), field), None
        if kind == "temperatures":
            # the joined line of 2.1's Printer preset, kept for controls saved with it
            return _temperatures_text(printer.get_current_temperatures()), None
        if kind == "progress":
            return _progress_text(printer.get_current_data()), None
        return "", "Unknown printer read {}".format(kind)
    except Exception as e:
        return "", _first_line("{}: {}".format(type(e).__name__, e))
