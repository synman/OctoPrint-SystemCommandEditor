# coding=utf-8
"""Calls this plugin makes into other OctoPrint plugins, for a command's ``run`` and a control's ``read``.

Three kinds are run here, each naming its plugin:

- ``{"plugin", "helper", "args"}``: a helper the plugin exports in ``__plugin_helpers__``.
- ``{"plugin", "api", "data"}``: the plugin's ``on_api_command(command, data)``.
- ``{"plugin", "method", "args"}``: a method on the plugin's implementation.

A call may carry ``"field"``: when its answer is a dict, the answer becomes that key's value. A ``read`` may also be
``{"mqtt": "<topic>"}``, the last payload seen on the topic (see MqttCache).

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


def read_outcome(spec, plugin_manager, timeout=10, call=run_call, mqtt=None):
    """A ``read`` as the ``(exit, output, error)`` triple a shell status command produces. Exit 0 means no error.

    A plugin call runs as ``run_with_timeout`` does. An MQTT read answers from ``mqtt``, the plugin's MqttCache.
    """
    if is_mqtt_read(spec):
        if mqtt is None:
            return None, "", "mqtt read has no cache"
        output, error = mqtt.read(spec["mqtt"], plugin_manager)
        return (None if error else 0), output, error
    output, error = run_with_timeout(spec, plugin_manager, timeout, call)
    return (None if error else 0), output, error
