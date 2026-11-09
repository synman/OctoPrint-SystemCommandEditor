import logging

import pytest

pytest.importorskip("octoprint")

import flask  # noqa: E402
from werkzeug.exceptions import Forbidden  # noqa: E402

from octoprint.access.permissions import Permissions  # noqa: E402

import octoprint_systemcommandeditor as plugin_module  # noqa: E402
from octoprint_systemcommandeditor import SystemCommandEditorPlugin  # noqa: E402
from octoprint_systemcommandeditor.presets import CATALOG, action_of, device_of, entry_of, maintenance  # noqa: E402


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

    def get(self, path, merged=False):
        value = self.config["plugins"]["systemcommandeditor"].get(path[0])
        if merged and isinstance(value, dict):
            # OctoPrint's merged read: the saved values over the plugin's defaults, one level per key
            defaults = SystemCommandEditorPlugin().get_settings_defaults().get(path[0]) or {}
            value = {key: {**(defaults.get(key) or {}), **(value.get(key) or {})} for key in set(defaults) | set(value)}
        return value

    def get_boolean(self, path):
        return bool(self.get(path))

    def set(self, path, value):
        node = self.config["plugins"]["systemcommandeditor"]
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
        self.calls.append(("set", path[0], value))

    def global_set(self, path, value):
        node = self.config
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
        self.calls.append(("global_set", list(path), value))

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


def test_settings_default_busy_icon_is_spinner():
    assert SystemCommandEditorPlugin().get_settings_defaults()["busy_icon"] == "fa-solid fa-spinner"


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
    maintenance_listed = [{"action": "backup_now", "available": True, "sample": True, "note": ""}]
    monkeypatch.setattr(plugin_module, "detect", lambda: detected)
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: maintenance_listed)
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {"octoprint": "x", "basedir": "b", "timelapse": "t", "logs": "l"}
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    assert body == {"presets": detected, "maintenance": maintenance_listed}


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


def test_load_sample_presets_fills_empty_devices_and_marks_done(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    detected = [
        {**device_of(CATALOG[0]), "sample": True, "available": True, "value": "5G free"},
        {**device_of(CATALOG[7]), "sample": False, "available": True, "value": "-50 dBm"},
    ]
    plugin = make_plugin()
    plugin._load_sample_presets(detector=lambda: detected, maintenance_detector=lambda: [])
    devices = plugin._settings.config["plugins"]["systemcommandeditor"]["devices"]
    assert devices[:2] == [
        {"id": "host", "type": "switch", "name": "atom-octo", "icon": "fa-solid fa-server"},
        {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print"},
    ]
    assert devices[2:] == [device_of(CATALOG[0])]
    assert ("set", "presets_loaded", True) in plugin._settings.calls
    assert plugin._settings.calls[-1] == ("save",)


def test_load_sample_presets_keeps_existing_devices(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    existing = [{"id": "mine", "type": "switch"}]
    plugin = make_plugin(devices=existing)
    plugin._load_sample_presets(detector=lambda: [], maintenance_detector=lambda: [])
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"] == [
        {"id": "host", "type": "switch", "name": "atom-octo", "icon": "fa-solid fa-server"},
        {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print"},
    ] + existing
    assert ("set", "presets_loaded", True) in plugin._settings.calls
    assert plugin._settings.calls[-1] == ("save",)


def test_get_returns_entries_and_core_commands(monkeypatch):
    allow(monkeypatch)
    actions = [
        {"action": "a", "name": "A", "command": "echo a", "secret_field": "x", "when": "yes", "icon_color": "#ff0000"},
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

    assert set(body) == {"devices", "core", "entries", "core_commands", "success_toast", "live_updates", "live_interval", "busy_icon"}
    assert body["busy_icon"] == "fa-solid fa-spinner"
    assert body["live_updates"] is False
    assert body["live_interval"] == 5
    assert body["success_toast"] is False
    assert body["devices"] == [{"id": "d", "type": "switch"}]
    # the saved icon wins; every other key comes from the defaults
    assert body["core"]["shutdown"]["icon"] == "i"
    assert body["core"]["shutdown"]["heading"] == "System"
    assert set(body["core"]) == {"shutdown", "reboot", "restart", "restart_safe"}
    assert body["entries"] == {
        "a": {"action": "a", "name": "A", "command": "echo a", "when": "yes", "icon_color": "#ff0000"},
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


MAINTENANCE_ENV = {"octoprint": "/opt/octo/bin/octoprint", "basedir": "/b", "timelapse": "/t", "logs": "/l"}


def maintenance_listing(exit_by_action):
    """What detect_maintenance returns for the fake outcomes: each action available unless its exit is nonzero."""
    listed = []
    for preset in maintenance(MAINTENANCE_ENV):
        exit_code = exit_by_action.get(preset["action"], 0)
        listed.append({**{k: preset[k] for k in ("action", "name", "icon", "command", "confirm", "async", "sample")},
                       "status": preset.get("status", ""), "available": exit_code == 0, "note": ""})
    return listed


def test_load_sample_presets_adds_maintenance_divider_when_no_custom_command():
    plugin = make_plugin(system_actions=[{"action": "divider", "name": "Webcam"}])
    plugin._load_sample_presets(
        detector=lambda: [],
        maintenance_detector=lambda: maintenance_listing({"webcam_restart": 1}),
    )
    actions = plugin._settings.config["system"]["actions"]
    backup = [preset for preset in maintenance(MAINTENANCE_ENV) if preset["action"] == "backup_now"][0]
    device, run = action_of(backup, set())
    assert actions == [
        {"action": "divider", "name": "Webcam"},
        {"action": "divider", "name": "Maintenance"},
        run,
    ]
    assert run["group"] == device["id"] == "backup_now" and run["role"] == "run"
    assert device in plugin._settings.config["plugins"]["systemcommandeditor"]["devices"]
    assert ("save",) == plugin._settings.calls[-1]


def test_load_sample_presets_adds_no_maintenance_when_a_custom_command_exists():
    existing = [{"action": "laser_on_cmd", "name": "Laser on", "command": "echo on"}]
    plugin = make_plugin(system_actions=existing)
    plugin._load_sample_presets(detector=lambda: [], maintenance_detector=lambda: maintenance_listing({}))
    assert plugin._settings.config["system"]["actions"] == existing
    assert not [call for call in plugin._settings.calls if call[0] == "global_set"]


def sample_disk(available=True):
    return [{**device_of(CATALOG[0]), "sample": True, "available": available, "value": "5G free"}]


def test_load_sample_presets_does_not_load_unavailable_maintenance_sample():
    plugin = make_plugin()
    plugin._load_sample_presets(
        detector=lambda: [],
        maintenance_detector=lambda: maintenance_listing({"backup_now": 1, "webcam_restart": 1}),
    )
    assert plugin._settings.config["system"]["actions"] == []
    assert not [call for call in plugin._settings.calls if call[0] == "global_set"]


def test_first_start_skins_host_and_octoprint_devices_and_core_entries(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    plugin = make_plugin(core=SystemCommandEditorPlugin().get_settings_defaults()["core"])
    plugin._load_sample_presets(detector=sample_disk, maintenance_detector=lambda: [])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert [device["id"] for device in config["devices"]] == ["host", "octoprint", "disk"]
    assert config["devices"][0] == {"id": "host", "type": "switch", "name": "atom-octo", "icon": "fa-solid fa-server"}
    assert config["devices"][1] == {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print"}
    core = config["core"]
    assert (core["reboot"]["group"], core["reboot"]["role"]) == ("host", "restart")
    assert (core["shutdown"]["group"], core["shutdown"]["role"]) == ("host", "off")
    assert (core["restart"]["group"], core["restart"]["role"]) == ("octoprint", "restart")
    assert (core["restart_safe"]["group"], core["restart_safe"]["role"]) == ("octoprint", "safe_restart")
    assert core["reboot"]["heading"] == "" and core["shutdown"]["heading"] == "System"


def test_first_start_leaves_core_alone_when_an_entry_already_has_a_group(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    core = SystemCommandEditorPlugin().get_settings_defaults()["core"]
    core["restart"]["group"] = "mine"
    plugin = make_plugin(core=core)
    plugin._load_sample_presets(detector=sample_disk, maintenance_detector=lambda: [])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert [device["id"] for device in config["devices"]] == ["disk"]
    assert config["core"]["restart"]["group"] == "mine"
    assert config["core"]["reboot"]["group"] == ""
    assert config["core"]["reboot"]["role"] == ""


def test_first_start_skips_skin_when_host_device_exists(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    core = SystemCommandEditorPlugin().get_settings_defaults()["core"]
    existing = [{"id": "host", "type": "switch", "name": "Mine", "icon": "fa-solid fa-star"}]
    plugin = make_plugin(devices=existing, core=core)
    plugin._load_sample_presets(detector=sample_disk, maintenance_detector=lambda: [])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert config["devices"] == existing
    assert config["core"]["reboot"]["group"] == ""
    assert config["core"]["restart"]["group"] == ""



def test_get_merges_saved_core_entries_over_their_defaults(monkeypatch):
    # first start saves only group and role on a core entry; its default heading must still reach the menu
    monkeypatch.setattr(Permissions.SYSTEM, "can", lambda: True)
    plugin = make_plugin(core={"shutdown": {"group": "host", "role": "off"}})
    with flask.Flask(__name__).test_request_context():
        core = plugin.on_api_get(None).get_json()["core"]
    assert core["shutdown"]["heading"] == "System"
    assert core["shutdown"]["group"] == "host"
