import time

import flask

from octoprint_systemcommandeditor.adapters import (
    MqttCache,
    api_text,
    carried,
    is_mqtt_read,
    is_plugin_run,
    read_outcome,
    run_call,
    run_with_timeout,
    to_text,
)


class Impl:
    """A fake plugin implementation. ``exported`` plays __plugin_helpers__; keyword arguments become attributes."""

    def __init__(self, exported=None, **attributes):
        self.exported = exported or {}
        self.__dict__.update(attributes)


class Info:
    def __init__(self, implementation):
        self.implementation = implementation


class Plugins:
    """A fake plugin manager: only the plugins passed in are installed and enabled."""

    def __init__(self, **installed):
        self.installed = installed

    def get_plugin_info(self, identifier, require_enabled=True):
        impl = self.installed.get(identifier)
        return None if impl is None else Info(impl)

    def get_helpers(self, name, *helpers):
        impl = self.installed.get(name)
        if impl is None:
            return None
        return {helper: impl.exported[helper] for helper in helpers if helper in impl.exported}


class Body:
    """Stands in for a flask Response: only get_data is used."""

    def __init__(self, text):
        self.text = text

    def get_data(self, as_text=False):
        return self.text


def test_to_text_renders_bool_none_containers_and_text():
    assert to_text(True) == "true"
    assert to_text(False) == "false"
    assert to_text(None) == ""
    assert to_text({"b": 1, "a": True}) == '{"a": true, "b": 1}'
    assert to_text([2, "x"]) == '[2, "x"]'
    assert to_text(3) == "3"
    assert to_text("on") == "on"


def test_api_text_parses_a_response_body_and_keeps_plain_text():
    assert api_text(Body('{"status": true}')) == '{"status": true}'
    assert api_text(Body('"on"')) == "on"
    assert api_text(Body("not json")) == "not json"
    assert api_text(Body("")) == ""


def test_api_text_takes_a_dict_and_none_directly():
    assert api_text({"status": False}) == '{"status": false}'
    assert api_text(None) == ""


def test_helper_call_passes_args_and_renders_its_answer():
    plugins = Plugins(p=Impl(exported={"get": lambda x, y: x + y}))
    assert run_call({"plugin": "p", "helper": "get", "args": {"x": "1", "y": "2"}}, plugins) == ("12", None)


def test_helper_call_without_args_takes_none():
    plugins = Plugins(p=Impl(exported={"on": lambda: True}))
    assert run_call({"plugin": "p", "helper": "on"}, plugins) == ("true", None)


def test_api_call_with_a_response_parses_its_body():
    class Api:
        def on_api_command(self, command, data):
            assert (command, data) == ("getStatus", {"subject": "r1"})
            return Body('{"status": false}')

    assert run_call({"plugin": "p", "api": "getStatus", "data": {"subject": "r1"}}, Plugins(p=Api())) == (
        '{"status": false}',
        None,
    )


def test_api_call_with_a_dict_renders_it_as_json():
    class Api:
        def on_api_command(self, command, data):
            return {"status": True}

    assert run_call({"plugin": "p", "api": "update", "data": {}}, Plugins(p=Api())) == ('{"status": true}', None)


def test_api_call_returning_none_is_an_empty_answer():
    class Api:
        def on_api_command(self, command, data):
            return None

    assert run_call({"plugin": "p", "api": "turnGpioOn"}, Plugins(p=Api())) == ("", None)


def test_method_call_passes_args_to_the_implementation():
    impl = Impl(turn_on=lambda plugip, plugidx: {"currentState": "on", "ip": plugip, "idx": plugidx})
    output, error = run_call({"plugin": "p", "method": "turn_on", "args": {"plugip": "10.0.0.6", "plugidx": 1}}, Plugins(p=impl))
    assert error is None
    assert output == '{"currentState": "on", "idx": 1, "ip": "10.0.0.6"}'


def test_missing_plugin_is_reported_as_not_installed():
    assert run_call({"plugin": "absent", "helper": "x"}, Plugins()) == ("", "absent is not installed")


def test_missing_helper_is_reported_by_name():
    assert run_call({"plugin": "p", "helper": "nope"}, Plugins(p=Impl())) == ("", "p has no nope")


def test_missing_method_is_reported_by_name():
    assert run_call({"plugin": "p", "method": "nope"}, Plugins(p=Impl())) == ("", "p has no nope")


def test_api_call_on_a_plugin_without_on_api_command_is_reported():
    assert run_call({"plugin": "p", "api": "x"}, Plugins(p=Impl())) == ("", "p has no x")


def test_exception_is_reported_as_first_line_only():
    def boom():
        raise RuntimeError("first line\nsecond line")

    assert run_call({"plugin": "p", "helper": "boom"}, Plugins(p=Impl(exported={"boom": boom}))) == (
        "",
        "p: RuntimeError: first line",
    )


def test_spec_with_no_plugin_kind_is_refused():
    assert run_call({"plugin": "p"}, Plugins(p=Impl())) == ("", "unsupported run kind")


def test_run_with_timeout_reports_timed_out_when_the_call_overruns():
    def slow():
        time.sleep(0.5)
        return "late"

    started = time.time()
    result = run_with_timeout({"plugin": "p", "helper": "slow"}, Plugins(p=Impl(exported={"slow": slow})), timeout=0.05)
    assert result == ("", "timed out")
    assert time.time() - started < 0.4


def test_run_with_timeout_returns_the_answer_in_time():
    plugins = Plugins(p=Impl(exported={"fast": lambda: "done"}))
    assert run_with_timeout({"plugin": "p", "helper": "fast"}, plugins, timeout=5) == ("done", None)


def test_read_outcome_is_exit_zero_without_error_and_none_with_one():
    plugins = Plugins(p=Impl(exported={"ok": lambda: "x"}))
    assert read_outcome({"plugin": "p", "helper": "ok"}, plugins) == (0, "x", None)
    assert read_outcome({"plugin": "p", "helper": "gone"}, plugins) == (None, "", "p has no gone")


def test_is_plugin_run_accepts_only_the_three_plugin_kinds():
    assert is_plugin_run({"plugin": "p", "helper": "h"})
    assert is_plugin_run({"plugin": "p", "api": "a", "data": {}})
    assert is_plugin_run({"plugin": "p", "method": "m"})
    assert not is_plugin_run({"plugin": "p"})
    assert not is_plugin_run({"gcode": "G28"})
    assert not is_plugin_run("helper")


def test_field_takes_one_key_from_a_dict_answer():
    plugins = Plugins(p=Impl(exported={"state": lambda: {"state": "Idle", "x": 1}}))
    assert run_call({"plugin": "p", "helper": "state", "field": "state"}, plugins) == ("Idle", None)


def test_field_missing_from_a_dict_answer_gives_empty_text():
    plugins = Plugins(p=Impl(exported={"state": lambda: {"x": 1}}))
    assert run_call({"plugin": "p", "helper": "state", "field": "state"}, plugins) == ("", None)


def test_field_picks_a_key_from_a_json_body():
    class Api:
        def on_api_command(self, command, data):
            return Body('{"state": "on", "other": 2}')

    assert run_call({"plugin": "p", "api": "get", "field": "state"}, Plugins(p=Api())) == ("on", None)


def test_field_is_ignored_when_the_answer_is_not_a_dict():
    plugins = Plugins(p=Impl(exported={"state": lambda: "plain"}))
    assert run_call({"plugin": "p", "helper": "state", "field": "state"}, plugins) == ("plain", None)


def test_mqtt_read_is_unknown_until_a_payload_arrives():
    callbacks = []
    plugins = Plugins(mqtt=Impl(exported={"mqtt_subscribe": lambda topic, callback: callbacks.append(callback)}))
    cache = MqttCache()
    assert cache.read("printer/state", plugins) == ("", None)
    callbacks[0]("printer/state", b"on", retained=False, qos=0)
    assert cache.read("printer/state", plugins) == ("on", None)


def test_mqtt_payload_is_decoded_as_utf8_with_bad_bytes_replaced():
    callbacks = []
    plugins = Plugins(mqtt=Impl(exported={"mqtt_subscribe": lambda topic, callback: callbacks.append(callback)}))
    cache = MqttCache()
    cache.read("t", plugins)
    callbacks[0]("t", b"\xff ok", retained=False, qos=0)
    assert cache.read("t", plugins) == ("\ufffd ok", None)
    callbacks[0]("t", "text", retained=False, qos=0)
    assert cache.read("t", plugins) == ("text", None)


def test_mqtt_each_topic_is_subscribed_once():
    subscribed = []
    plugins = Plugins(mqtt=Impl(exported={"mqtt_subscribe": lambda topic, callback: subscribed.append(topic)}))
    cache = MqttCache()
    cache.read("one", plugins)
    cache.read("one", plugins)
    cache.read("two", plugins)
    assert subscribed == ["one", "two"]


def test_mqtt_without_the_plugin_is_reported():
    assert MqttCache().read("t", Plugins()) == ("", "mqtt is not installed")


def test_mqtt_subscribe_failure_is_reported_and_tried_again():
    attempts = []

    def subscribe(topic, callback):
        attempts.append(topic)
        raise RuntimeError("no broker")

    plugins = Plugins(mqtt=Impl(exported={"mqtt_subscribe": subscribe}))
    cache = MqttCache()
    assert cache.read("t", plugins) == ("", "mqtt: RuntimeError: no broker")
    assert cache.read("t", plugins) == ("", "mqtt: RuntimeError: no broker")
    assert attempts == ["t", "t"]


def test_read_outcome_for_an_mqtt_read_uses_the_cache():
    callbacks = []
    plugins = Plugins(mqtt=Impl(exported={"mqtt_subscribe": lambda topic, callback: callbacks.append(callback)}))
    cache = MqttCache()
    assert read_outcome({"mqtt": "t"}, plugins, mqtt=cache) == (0, "", None)
    callbacks[0]("t", b"on", retained=True, qos=1)
    assert read_outcome({"mqtt": "t"}, plugins, mqtt=cache) == (0, "on", None)


def test_read_outcome_reports_a_missing_mqtt_plugin_as_an_error():
    assert read_outcome({"mqtt": "t"}, Plugins(), mqtt=MqttCache()) == (None, "", "mqtt is not installed")


def test_read_outcome_refuses_an_mqtt_read_with_no_cache():
    assert read_outcome({"mqtt": "t"}, Plugins()) == (None, "", "mqtt read has no cache")


def test_an_mqtt_read_is_a_read_not_a_run():
    assert is_mqtt_read({"mqtt": "t"})
    assert not is_mqtt_read({"plugin": "p", "helper": "h"})
    assert not is_plugin_run({"mqtt": "t"})
