import logging

import pytest

pytest.importorskip("octoprint")

import flask  # noqa: E402
from werkzeug.exceptions import Forbidden  # noqa: E402

from octoprint.access.permissions import Permissions  # noqa: E402

import octoprint_systemcommandeditor as plugin_module  # noqa: E402
from octoprint_systemcommandeditor import SystemCommandEditorPlugin  # noqa: E402
from octoprint_systemcommandeditor.presets import CATALOG, device_of  # noqa: E402


class FakeSettings:
    """The parts of PluginSettings the API uses, over a plain config dict."""

    def __init__(self, system_actions, devices, core, server_commands):
        self.config = {
            "system": {"actions": system_actions},
            "server": {"commands": server_commands},
            "plugins": {"systemcommandeditor": {"devices": devices, "core": core}},
        }
        self.calls = []

    def global_get(self, path):
        node = self.config
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node

    def get(self, path):
        return self.config["plugins"]["systemcommandeditor"].get(path[0])

    def get_boolean(self, path):
        return bool(self.get(path))

    def set(self, path, value):
        self.config["plugins"]["systemcommandeditor"][path[0]] = value
        self.calls.append(("set", path[0], value))

    def save(self):
        self.calls.append(("save",))


def make_plugin(system_actions=(), devices=(), core=None, server_commands=None):
    plugin = SystemCommandEditorPlugin()
    plugin._settings = FakeSettings(
        list(system_actions), list(devices), core or {}, server_commands or {}
    )
    plugin._logger = logging.getLogger("test")
    return plugin


def request_context():
    return flask.Flask(__name__).test_request_context()


def deny(monkeypatch):
    monkeypatch.setattr(Permissions.SYSTEM, "can", lambda: False)


def allow(monkeypatch):
    monkeypatch.setattr(Permissions.SYSTEM, "can", lambda: True)


def test_get_denied_without_system_permission(monkeypatch):
    deny(monkeypatch)
    plugin = make_plugin()
    with request_context(), pytest.raises(Forbidden):
        plugin.on_api_get(None)


def test_command_denied_without_system_permission(monkeypatch):
    deny(monkeypatch)
    plugin = make_plugin()
    with request_context(), pytest.raises(Forbidden):
        plugin.on_api_command("status", {"ids": []})


def test_settings_defaults_include_devices():
    assert SystemCommandEditorPlugin().get_settings_defaults()["devices"] == []


def test_settings_default_live_updates_off_every_5_seconds():
    defaults = SystemCommandEditorPlugin().get_settings_defaults()
    assert defaults["live_updates"] is False
    assert defaults["live_interval"] == 5


def test_settings_default_success_toast_off():
    assert SystemCommandEditorPlugin().get_settings_defaults()["success_toast"] is False


def test_get_reports_success_toast_when_turned_on(monkeypatch):
    monkeypatch.setattr(Permissions.SYSTEM, "can", lambda: True)
    plugin = make_plugin()
    plugin._settings.config["plugins"]["systemcommandeditor"]["success_toast"] = True
    app = flask.Flask(__name__)
    with app.test_request_context():
        body = plugin.on_api_get(None).get_json()
    assert body["success_toast"] is True


def test_api_commands_declare_status_ids_and_presets():
    assert SystemCommandEditorPlugin().get_api_commands() == {"status": ["ids"], "presets": []}


def test_settings_default_presets_loaded_off():
    assert SystemCommandEditorPlugin().get_settings_defaults()["presets_loaded"] is False


def test_presets_command_returns_detected_list(monkeypatch):
    allow(monkeypatch)
    detected = [{"id": "disk", "available": True, "value": "5G free"}]
    monkeypatch.setattr(plugin_module, "detect", lambda: detected)
    plugin = make_plugin()
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    assert body == {"presets": detected}


def test_startup_loads_presets_in_background_until_done(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, daemon):
            started.append((target, daemon))

        def start(self):
            pass

    monkeypatch.setattr(plugin_module.threading, "Thread", FakeThread)
    plugin = make_plugin()

    plugin.on_after_startup()
    assert started == [(plugin._load_sample_presets, True)]

    plugin._settings.config["plugins"]["systemcommandeditor"]["presets_loaded"] = True
    plugin.on_after_startup()
    assert len(started) == 1


def test_load_sample_presets_fills_empty_devices_and_marks_done():
    detected = [
        {**device_of(CATALOG[0]), "sample": True, "available": True, "value": "5G free"},
        {**device_of(CATALOG[7]), "sample": False, "available": True, "value": "-50 dBm"},
    ]
    plugin = make_plugin()
    plugin._load_sample_presets(detector=lambda: detected)
    devices = plugin._settings.config["plugins"]["systemcommandeditor"]["devices"]
    assert devices == [device_of(CATALOG[0])]
    assert ("set", "presets_loaded", True) in plugin._settings.calls
    assert plugin._settings.calls[-1] == ("save",)


def test_load_sample_presets_keeps_existing_devices():
    existing = [{"id": "mine", "type": "switch"}]
    plugin = make_plugin(devices=existing)
    plugin._load_sample_presets(detector=lambda: [])
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"] == existing
    assert ("set", "presets_loaded", True) in plugin._settings.calls
    assert plugin._settings.calls[-1] == ("save",)


def test_get_returns_entries_and_core_commands(monkeypatch):
    allow(monkeypatch)
    actions = [
        {"action": "a", "name": "A", "command": "echo a", "secret_field": "x", "when": "yes"},
        {"action": "divider"},
        {"name": "no action"},
        {"action": "b", "name": "B", "command": "echo b", "group": "g", "role": "choice", "value": "v"},
    ]
    server_commands = {
        "systemShutdownCommand": "shutdown now",
        "systemRestartCommand": "",
        "serverRestartCommand": "restart octoprint",
    }
    plugin = make_plugin(
        system_actions=actions,
        devices=[{"id": "d", "type": "switch"}],
        core={"shutdown": {"icon": "i"}},
        server_commands=server_commands,
    )

    with request_context():
        body = plugin.on_api_get(None).get_json()

    assert set(body) == {"devices", "core", "entries", "core_commands", "success_toast", "live_updates", "live_interval"}
    assert body["live_updates"] is False
    assert body["live_interval"] == 5
    assert body["success_toast"] is False
    assert body["devices"] == [{"id": "d", "type": "switch"}]
    assert body["core"] == {"shutdown": {"icon": "i"}}
    assert body["entries"] == {
        "a": {"action": "a", "name": "A", "command": "echo a", "when": "yes"},
        "b": {"action": "b", "name": "B", "command": "echo b", "group": "g", "role": "choice", "value": "v"},
    }
    assert body["core_commands"] == {
        "shutdown": "shutdown now",
        "restart": "restart octoprint",
        "restart_safe": "restart octoprint",
    }


def test_command_status_selector_choices_include_core_entries(monkeypatch):
    allow(monkeypatch)
    devices = [{"id": "sel", "type": "selector", "status": "echo fast"}]
    core = {"speed": {"group": "sel", "role": "choice", "value": "fast"}}
    plugin = make_plugin(devices=devices, core=core)
    with request_context():
        body = plugin.on_api_command("status", {"ids": ["sel"]}).get_json()
    assert body["results"]["sel"]["state"] == "fast"


def test_command_status_without_status_device(monkeypatch):
    allow(monkeypatch)
    plugin = make_plugin(devices=[{"id": "d", "type": "switch"}])
    with request_context():
        body = plugin.on_api_command("status", {"ids": ["d", "missing"]}).get_json()
    assert body == {
        "results": {
            "d": {"state": None, "output": "", "exit": None, "error": None},
            "missing": {"state": "unknown", "output": "", "exit": None, "error": "unknown device"},
        }
    }


def test_api_is_declared_protected():
    assert make_plugin().is_api_protected() is True
