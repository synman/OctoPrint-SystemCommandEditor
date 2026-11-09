import logging
import types

import pytest

pytest.importorskip("octoprint")

import flask  # noqa: E402
from werkzeug.exceptions import BadRequest, Forbidden, NotFound  # noqa: E402

import octoprint.events  # noqa: E402
from octoprint.access.permissions import Permissions  # noqa: E402

import octoprint_systemcommandeditor as plugin_module  # noqa: E402
from octoprint_systemcommandeditor import SystemCommandEditorPlugin  # noqa: E402
from octoprint_systemcommandeditor import condition_effect, printer_state  # noqa: E402
from octoprint_systemcommandeditor.presets import CATALOG, action_of, device_of, entry_of, maintenance  # noqa: E402
from octoprint_systemcommandeditor.presets import octoprint_presets  # noqa: E402
from octoprint_systemcommandeditor.presets import webcam_presets  # noqa: E402


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


class FakePlugins:
    """The parts of the plugin manager the API uses: plugins given as keyword arguments are installed."""

    def __init__(self, **impls):
        self.impls = impls

    def get_plugin_info(self, identifier, require_enabled=True):
        impl = self.impls.get(identifier)
        return None if impl is None else types.SimpleNamespace(implementation=impl, enabled=True)

    def get_helpers(self, name, *helpers):
        impl = self.impls.get(name)
        if impl is None:
            return None
        exported = getattr(impl, "exported", {})
        return {helper: exported[helper] for helper in helpers if helper in exported}


class FakePrinter:
    """The printer's state flags; the defaults are a connected, idle printer."""

    def __init__(self, printing=False, paused=False, error=False, operational=True):
        self.flags = dict(printing=printing, paused=paused, error=error, operational=operational)

    def is_printing(self):
        return self.flags["printing"]

    def is_paused(self):
        return self.flags["paused"]

    def is_error(self):
        return self.flags["error"]

    def is_operational(self):
        return self.flags["operational"]


def make_plugin(system_actions=(), devices=(), core=None, server_commands=None, plugins=None, printer=None):
    plugin = SystemCommandEditorPlugin()
    plugin._settings = FakeSettings(
        list(system_actions), list(devices), core or {}, server_commands or {}
    )
    plugin._logger = logging.getLogger("test")
    plugin._plugin_manager = FakePlugins(**(plugins or {}))
    plugin._printer = printer or FakePrinter()
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
    assert SystemCommandEditorPlugin().get_api_commands() == {"status": ["ids"], "presets": [], "run": ["action"]}


def test_settings_default_presets_loaded_off():
    assert SystemCommandEditorPlugin().get_settings_defaults()["presets_loaded"] is False


def test_presets_command_returns_detected_list(monkeypatch):
    allow(monkeypatch)
    detected = [{"id": "disk", "available": True, "value": "5G free"}]
    maintenance_listed = [{"action": "backup_now", "available": True, "sample": True, "note": ""}]
    monkeypatch.setattr(plugin_module, "detect", lambda: detected)
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: maintenance_listed)
    webcam = {"plugin": "webcam", "name": "Webcam services", "presets": []}
    monkeypatch.setattr(plugin_module, "webcam_presets", lambda: webcam)
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {"octoprint": "x", "basedir": "b", "timelapse": "t", "logs": "l"}
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    assert body["presets"] == detected and body["maintenance"] == maintenance_listed
    # harvested groups, then the webcam services group, then OctoPrint's own
    assert [group["plugin"] for group in body["plugins"]] == ["webcam", "octoprint"]
    # the host and OctoPrint switches, with the roles OctoPrint's own commands take on them, as first start uses
    assert body["switches"] == [
        {"id": "host", "type": "switch", "name": "atom-octo", "icon": "fa-solid fa-server",
         "members": {"reboot": "restart", "shutdown": "off"}, "available": True},
        {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print",
         "members": {"restart": "restart", "restart_safe": "safe_restart"}, "available": True},
    ]
    assert plugin_module.SYSTEM_GROUPS == {
        "reboot": ("host", "restart"), "shutdown": ("host", "off"),
        "restart": ("octoprint", "restart"), "restart_safe": ("octoprint", "safe_restart"),
    }


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
    plugin._load_sample_presets(plugin_groups=[], detector=lambda: detected, maintenance_detector=lambda: [])
    devices = plugin._settings.config["plugins"]["systemcommandeditor"]["devices"]
    assert devices[:2] == [
        {"id": "host", "type": "switch", "name": "atom-octo", "icon": "fa-solid fa-server", "preset": "host"},
        {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print", "preset": "octoprint"},
    ]
    assert devices[2:] == [{**device_of(CATALOG[0]), "preset": "disk"}]
    assert ("set", "presets_loaded", True) in plugin._settings.calls
    assert plugin._settings.calls[-1] == ("save",)


def test_load_sample_presets_keeps_existing_devices(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    existing = [{"id": "mine", "type": "switch"}]
    plugin = make_plugin(devices=existing)
    plugin._load_sample_presets(plugin_groups=[], detector=lambda: [], maintenance_detector=lambda: [])
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"] == [
        {"id": "host", "type": "switch", "name": "atom-octo", "icon": "fa-solid fa-server", "preset": "host"},
        {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print", "preset": "octoprint"},
    ] + existing
    assert ("set", "presets_loaded", True) in plugin._settings.calls
    assert plugin._settings.calls[-1] == ("save",)


def test_get_passes_a_plugin_commands_run_to_the_menu(monkeypatch):
    # a plugin-backed command reaches the menu with its run, so the menu can send it to this plugin's run command
    allow(monkeypatch)
    run = {"plugin": "psucontrol", "helper": "turn_psu_on"}
    plugin = make_plugin(system_actions=[{"action": "c", "name": "C", "run": run, "group": "g", "role": "on"}])
    with request_context():
        body = plugin.on_api_get(None).get_json()
    assert body["entries"]["c"]["run"] == run


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
    # OctoPrint's own restart entries carry no heading by default
    assert body["core"]["restart"]["heading"] == "" and body["core"]["restart_safe"]["heading"] == ""
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
    plugin._load_sample_presets(plugin_groups=[], 
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
    plugin._load_sample_presets(plugin_groups=[], detector=lambda: [], maintenance_detector=lambda: maintenance_listing({}))
    assert plugin._settings.config["system"]["actions"] == existing
    assert not [call for call in plugin._settings.calls if call[0] == "global_set"]


def sample_disk(available=True):
    return [{**device_of(CATALOG[0]), "sample": True, "available": available, "value": "5G free"}]


def test_load_sample_presets_does_not_load_unavailable_maintenance_sample():
    plugin = make_plugin()
    plugin._load_sample_presets(plugin_groups=[], 
        detector=lambda: [],
        maintenance_detector=lambda: maintenance_listing({"backup_now": 1, "webcam_restart": 1}),
    )
    assert plugin._settings.config["system"]["actions"] == []
    assert not [call for call in plugin._settings.calls if call[0] == "global_set"]


def test_first_start_skins_host_and_octoprint_devices_and_core_entries(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    plugin = make_plugin(core=SystemCommandEditorPlugin().get_settings_defaults()["core"])
    plugin._load_sample_presets(plugin_groups=[], detector=sample_disk, maintenance_detector=lambda: [])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert [device["id"] for device in config["devices"]] == ["host", "octoprint", "disk"]
    assert config["devices"][0] == {"id": "host", "type": "switch", "name": "atom-octo", "icon": "fa-solid fa-server",
                                    "preset": "host"}
    assert config["devices"][1] == {"id": "octoprint", "type": "switch", "name": "OctoPrint",
                                    "icon": "fa-solid fa-print", "preset": "octoprint"}
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
    plugin._load_sample_presets(plugin_groups=[], detector=sample_disk, maintenance_detector=lambda: [])
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
    plugin._load_sample_presets(plugin_groups=[], detector=sample_disk, maintenance_detector=lambda: [])
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


def test_first_start_marks_maintenance_control_and_command_with_their_action(tmp_path):
    plugin = make_plugin()
    plugin._load_sample_presets(plugin_groups=[], detector=lambda: [], maintenance_detector=lambda: maintenance_listing({"webcam_restart": 1}))
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    control = [device for device in config["devices"] if device["id"] == "backup_now"][0]
    command = [entry for entry in plugin._settings.config["system"]["actions"] if entry.get("action") == "backup_now"][0]
    assert control["preset"] == "backup_now" and command["preset"] == "backup_now"


def test_first_start_marks_readout_placement_with_its_readout(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    plugin = make_plugin(core=SystemCommandEditorPlugin().get_settings_defaults()["core"])
    plugin._load_sample_presets(plugin_groups=[], detector=sample_disk, maintenance_detector=lambda: [])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    placement = [entry for entry in plugin._settings.config["system"]["actions"] if entry.get("device") == "disk"][0]
    assert placement["preset"] == "disk"
    assert [d for d in config["devices"] if d["id"] == "disk"][0]["preset"] == "disk"


def test_settings_version_is_4():
    assert SystemCommandEditorPlugin().get_settings_version() == 4


def printer_in(state):
    return FakePrinter(
        printing=state == "printing",
        paused=state == "paused",
        error=state == "error",
        operational=state == "ready",
    )


STATES = ("printing", "paused", "error", "ready", "offline")


def test_printer_state_names_each_state():
    for state in STATES:
        assert printer_state(printer_in(state)) == state


def test_printer_state_takes_printing_then_paused_then_error_then_ready():
    assert printer_state(FakePrinter(printing=True, paused=True, error=True)) == "printing"
    assert printer_state(FakePrinter(paused=True, error=True)) == "paused"
    assert printer_state(FakePrinter(error=True, operational=True)) == "error"


def test_condition_effect_is_none_without_a_when():
    assert condition_effect(None, "ready") is None
    assert condition_effect({}, "ready") is None


def test_condition_effect_for_every_state_mode_and_effect():
    for effect in ("hide", "disable"):
        for state in STATES:
            never = {"mode": "never", "states": ["printing"], "effect": effect}
            only = {"mode": "only", "states": ["printing"], "effect": effect}
            assert condition_effect(never, state) == (effect if state == "printing" else None)
            assert condition_effect(only, state) == (effect if state != "printing" else None)


def test_condition_effect_defaults_to_hide_and_ignores_an_unknown_mode():
    assert condition_effect({"mode": "never", "states": ["printing"]}, "printing") == "hide"
    assert condition_effect({"mode": "sometimes", "states": ["printing"], "effect": "disable"}, "ready") is None


def test_condition_effect_lists_several_states():
    when = {"mode": "only", "states": ["paused", "error"], "effect": "hide"}
    assert condition_effect(when, "ready") == "hide"
    assert condition_effect(when, "paused") is None


def run_entry(**extra):
    return {"action": "boot", "name": "Boot", "run": {"plugin": "p", "helper": "boot"}, **extra}


def helper_plugin(helper):
    return types.SimpleNamespace(exported={"boot": helper})


def test_run_is_404_for_an_unknown_action(monkeypatch):
    allow(monkeypatch)
    plugin = make_plugin(system_actions=[run_entry()])
    with request_context(), pytest.raises(NotFound):
        plugin.on_api_command("run", {"action": "missing"})


def test_run_is_404_for_a_command_with_no_run(monkeypatch):
    allow(monkeypatch)
    plugin = make_plugin(system_actions=[{"action": "ls", "name": "ls", "command": "ls"}])
    with request_context(), pytest.raises(NotFound):
        plugin.on_api_command("run", {"action": "ls"})


def test_run_is_400_when_run_is_no_run_kind(monkeypatch):
    allow(monkeypatch)
    plugin = make_plugin(system_actions=[run_entry(run={"helper": "boot"})])
    with request_context(), pytest.raises(BadRequest):
        plugin.on_api_command("run", {"action": "boot"})


def test_run_is_409_and_does_not_run_when_the_when_hides_it(monkeypatch):
    allow(monkeypatch)
    calls = []

    def boot():
        calls.append(1)
        return "ran"

    when = {"mode": "never", "states": ["printing"], "effect": "hide"}
    plugin = make_plugin(
        system_actions=[run_entry(when=when)],
        plugins={"p": helper_plugin(boot)},
        printer=FakePrinter(printing=True),
    )
    with request_context():
        response = plugin.on_api_command("run", {"action": "boot"})
    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "Not available while the printer is printing"}
    assert calls == []


def test_run_is_409_when_the_when_disables_it_offline(monkeypatch):
    allow(monkeypatch)
    when = {"mode": "only", "states": ["ready"], "effect": "disable"}
    plugin = make_plugin(
        system_actions=[run_entry(when=when)],
        plugins={"p": helper_plugin(lambda: "ran")},
        printer=FakePrinter(operational=False),
    )
    with request_context():
        response = plugin.on_api_command("run", {"action": "boot"})
    assert response.status_code == 409
    assert response.get_json()["error"] == "Not available while the printer is offline"


def test_run_goes_ahead_when_the_when_does_not_apply(monkeypatch):
    allow(monkeypatch)
    when = {"mode": "never", "states": ["printing"], "effect": "hide"}
    plugin = make_plugin(system_actions=[run_entry(when=when)], plugins={"p": helper_plugin(lambda: "ran")})
    with request_context():
        response = plugin.on_api_command("run", {"action": "boot"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "output": "ran", "error": None}


def test_run_answers_ok_false_with_the_error_when_the_call_fails(monkeypatch):
    allow(monkeypatch)

    def boot():
        raise RuntimeError("boom")

    plugin = make_plugin(system_actions=[run_entry()], plugins={"p": helper_plugin(boot)})
    with request_context():
        body = plugin.on_api_command("run", {"action": "boot"}).get_json()
    assert body == {"ok": False, "output": "", "error": "p: RuntimeError: boom"}


def read_devices(read, **extra):
    return [{"id": "d", "type": "switch", "read": read, "on_pattern": "^on$", "off_pattern": "^off$", **extra}]


def test_status_reads_a_control_through_its_read(monkeypatch):
    allow(monkeypatch)
    plugin = make_plugin(
        devices=read_devices({"plugin": "p", "helper": "get"}),
        plugins={"p": types.SimpleNamespace(exported={"get": lambda: "on"})},
    )
    with request_context():
        body = plugin.on_api_command("status", {"ids": ["d"]}).get_json()
    assert body["results"]["d"] == {"state": "on", "output": "on", "exit": 0, "error": None}


def test_status_runs_a_plain_status_beside_a_read(monkeypatch):
    allow(monkeypatch)
    devices = [
        {"id": "s", "type": "readout", "status": "echo hello"},
        {"id": "r", "type": "readout", "read": {"plugin": "p", "helper": "get"}},
    ]
    plugin = make_plugin(devices=devices, plugins={"p": types.SimpleNamespace(exported={"get": lambda: "x"})})
    with request_context():
        results = plugin.on_api_command("status", {"ids": ["s", "r"]}).get_json()["results"]
    assert results["s"]["state"] == "hello"
    assert results["r"]["state"] == "x"


def test_status_skips_a_live_false_read_only_when_live(monkeypatch):
    allow(monkeypatch)
    calls = []

    def get():
        calls.append(1)
        return "on"

    devices = read_devices({"plugin": "p", "helper": "get", "live": False})
    plugin = make_plugin(devices=devices, plugins={"p": types.SimpleNamespace(exported={"get": get})})
    with request_context():
        live = plugin.on_api_command("status", {"ids": ["d"], "live": True}).get_json()["results"]
        assert live["d"] == {"state": None, "output": "", "exit": None, "error": None, "skipped": True}
        assert calls == []
        polled = plugin.on_api_command("status", {"ids": ["d"]}).get_json()["results"]
    assert polled["d"]["state"] == "on"
    assert calls == [1]


def test_status_reads_an_mqtt_topic_and_keeps_its_subscription_on_the_plugin(monkeypatch):
    allow(monkeypatch)
    subscriptions = []

    def subscribe(topic, callback):
        subscriptions.append((topic, callback))

    plugin = make_plugin(
        devices=read_devices({"mqtt": "printer/power"}),
        plugins={"mqtt": types.SimpleNamespace(exported={"mqtt_subscribe": subscribe})},
    )
    with request_context():
        before = plugin.on_api_command("status", {"ids": ["d"]}).get_json()["results"]["d"]
        subscriptions[0][1]("printer/power", b"on", retained=False, qos=0)
        after = plugin.on_api_command("status", {"ids": ["d"]}).get_json()["results"]["d"]
    assert before["state"] == "unknown"
    assert after["state"] == "on"
    assert [topic for topic, _ in subscriptions] == ["printer/power"]


class Recording(FakePrinter):
    """A FakePrinter that also records the commands the run API sends it."""

    def __init__(self, **flags):
        super().__init__(**flags)
        self.calls = []

    def commands(self, lines, force=False):
        self.calls.append(("commands", list(lines), force))


class Events:
    def __init__(self):
        self.fired = []

    def fire(self, name, payload):
        self.fired.append((name, payload))


def test_run_sends_gcode_and_answers_ok(monkeypatch):
    allow(monkeypatch)
    printer = Recording()
    plugin = make_plugin(system_actions=[run_entry(run={"gcode": ["G28", ""]})], printer=printer)
    with request_context():
        body = plugin.on_api_command("run", {"action": "boot"}).get_json()
    assert body == {"ok": True, "output": "", "error": None}
    assert printer.calls == [("commands", ["G28"], False)]


def test_run_refuses_gcode_while_offline_and_sends_nothing(monkeypatch):
    allow(monkeypatch)
    printer = Recording(operational=False)
    plugin = make_plugin(system_actions=[run_entry(run={"gcode": ["G28"]})], printer=printer)
    with request_context():
        response = plugin.on_api_command("run", {"action": "boot"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": False, "output": "", "error": "The printer is not connected"}
    assert printer.calls == []


def test_run_checks_the_when_first_for_a_builtin_run(monkeypatch):
    allow(monkeypatch)
    printer = Recording(printing=True)
    when = {"mode": "never", "states": ["printing"], "effect": "hide"}
    plugin = make_plugin(system_actions=[run_entry(run={"gcode": ["G28"]}, when=when)], printer=printer)
    with request_context():
        response = plugin.on_api_command("run", {"action": "boot"})
    assert response.status_code == 409
    assert printer.calls == []


def test_run_fires_a_custom_event_through_the_event_manager(monkeypatch):
    allow(monkeypatch)
    events = Events()
    monkeypatch.setattr(octoprint.events, "eventManager", lambda: events)
    entry = run_entry(run={"event": "systemcommandeditor_custom", "payload": {"a": 1}})
    plugin = make_plugin(system_actions=[entry])
    with request_context():
        body = plugin.on_api_command("run", {"action": "boot"}).get_json()
    assert body == {"ok": True, "output": "", "error": None}
    assert events.fired == [("systemcommandeditor_custom", {"a": 1})]


def test_status_reads_the_printer_state_live(monkeypatch):
    allow(monkeypatch)

    class Operating(FakePrinter):
        def get_state_string(self):
            return "Operating"

    plugin = make_plugin(devices=read_devices({"printer": "state"}), printer=Operating())
    with request_context():
        body = plugin.on_api_command("status", {"ids": ["d"], "live": True}).get_json()
    assert body["results"]["d"]["output"] == "Operating"
    assert body["results"]["d"]["exit"] == 0 and body["results"]["d"]["error"] is None


def test_status_reads_temperatures_and_progress(monkeypatch):
    allow(monkeypatch)

    class Hot(FakePrinter):
        def get_current_temperatures(self):
            return {"tool0": {"actual": 210.2, "target": 215.0}, "bed": {"actual": 60, "target": 60}}

        def get_current_data(self):
            return {"progress": {"completion": 42.3}}

    devices = [
        {"id": "t", "type": "readout", "read": {"printer": "temperatures"}},
        {"id": "p", "type": "readout", "read": {"printer": "progress"}},
    ]
    plugin = make_plugin(devices=devices, printer=Hot())
    with request_context():
        results = plugin.on_api_command("status", {"ids": ["t", "p"]}).get_json()["results"]
    assert results["t"]["state"] == "T0 210/215 · Bed 60/60"
    assert results["p"]["state"] == "42%"


def test_presets_api_lists_the_octoprint_group_with_its_five_presets(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setattr(plugin_module, "detect", lambda: [])
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: [])
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {}
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    group = body["plugins"][-1]
    assert group["plugin"] == "octoprint" and group["name"] == "OctoPrint"
    assert [preset["preset"] for preset in group["presets"]] == [
        "octoprint:connection", "octoprint:job", "octoprint:printer", "octoprint:gcode", "octoprint:event",
    ]


def test_presets_api_offers_the_snapshot_from_classicwebcam_first(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setattr(plugin_module, "detect", lambda: [])
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: [])
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {}
    plugin._settings.config["webcam"] = {"snapshot": "http://printer.local/own"}
    plugin._settings.config["plugins"]["classicwebcam"] = {"snapshot": "http://printer.local/classic"}
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    presets = body["plugins"][-1]["presets"]
    assert presets[-1]["preset"] == "octoprint:snapshot"
    assert presets[-1]["control"]["url"] == "http://printer.local/classic"


def test_presets_api_offers_the_snapshot_from_the_webcam_setting_when_classicwebcam_is_unset(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setattr(plugin_module, "detect", lambda: [])
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: [])
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {}
    plugin._settings.config["webcam"] = {"snapshot": "http://printer.local/own"}
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    assert body["plugins"][-1]["presets"][-1]["control"]["url"] == "http://printer.local/own"


def test_presets_api_has_no_snapshot_preset_without_either_setting(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setattr(plugin_module, "detect", lambda: [])
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: [])
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {}
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    assert "octoprint:snapshot" not in [p["preset"] for p in body["plugins"][-1]["presets"]]


def test_presets_api_skips_a_blank_classicwebcam_snapshot_and_trims_the_one_it_uses(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setattr(plugin_module, "detect", lambda: [])
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: [])
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {}
    plugin._settings.config.setdefault("plugins", {})["classicwebcam"] = {"snapshot": "   "}
    plugin._settings.config["webcam"] = {"snapshot": " http://printer.local/own "}
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    assert body["plugins"][-1]["presets"][-1]["control"]["url"] == "http://printer.local/own"


def psu_group():
    # the PSU Control plugin's group as harvest builds it: one switch with On and Off
    control = {"type": "switch", "name": "Printer power", "icon": "fa-solid fa-plug",
               "read": {"plugin": "psucontrol", "helper": "get_psu_state"}, "on_pattern": "^true$",
               "off_pattern": "^false$"}
    commands = [{"name": "On", "run": {"plugin": "psucontrol", "helper": "turn_psu_on"}, "role": "on"},
                {"name": "Off", "run": {"plugin": "psucontrol", "helper": "turn_psu_off"}, "role": "off"}]
    return {"plugin": "psucontrol", "name": "PSU Control", "presets": [
        {"preset": "psucontrol:psu", "name": "PSU Control", "control": control, "commands": commands}]}


def test_first_start_adds_ready_made_switches_and_commands_after_maintenance(monkeypatch, caplog):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    plugin = make_plugin(core=SystemCommandEditorPlugin().get_settings_defaults()["core"])
    with caplog.at_level(logging.INFO, logger="test"):
        plugin._load_sample_presets(
            detector=sample_disk,
            maintenance_detector=lambda: maintenance_listing({"webcam_restart": 1}),
            plugin_groups=[psu_group(), octoprint_presets()],
        )
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert [device["id"] for device in config["devices"]] == [
        "host", "octoprint", "disk", "backup_now", "printer_connection", "printer_power",
    ]
    actions = plugin._settings.config["system"]["actions"]
    backup = [preset for preset in maintenance(MAINTENANCE_ENV) if preset["action"] == "backup_now"][0]
    _, backup_run = action_of(backup, set())
    assert actions[2:4] == [{"action": "divider", "name": "Maintenance"}, backup_run]
    assert actions[4:] == [
        {"action": "divider", "name": "Plugin devices"},
        {"action": "printer_connection_connect", "name": "Connect", "run": {"printer": "connect"}, "role": "on",
         "icon": "fa-solid fa-play", "group": "printer_connection", "preset": "octoprint:connection"},
        {"action": "printer_connection_disconnect", "name": "Disconnect", "run": {"printer": "disconnect"},
         "role": "off", "icon": "fa-solid fa-power-off", "group": "printer_connection", "preset": "octoprint:connection"},
        {"action": "psu_control_on", "name": "On", "run": {"plugin": "psucontrol", "helper": "turn_psu_on"},
         "role": "on", "group": "printer_power", "preset": "psucontrol:psu"},
        {"action": "psu_control_off", "name": "Off", "run": {"plugin": "psucontrol", "helper": "turn_psu_off"},
         "role": "off", "group": "printer_power", "preset": "psucontrol:psu"},
    ]
    assert ("set", "devices", config["devices"]) in plugin._settings.calls
    assert ("global_set", ["system", "actions"], actions) in plugin._settings.calls
    assert "Added ready-made controls: ['printer_connection', 'printer_power']" in caplog.text


def test_first_start_with_existing_controls_adds_no_ready_made_switches_or_commands(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    existing = [{"id": "mine", "type": "switch", "name": "Mine", "icon": "fa-solid fa-star"}]
    plugin = make_plugin(devices=existing)
    plugin._load_sample_presets(detector=lambda: [], maintenance_detector=lambda: [],
                                plugin_groups=[psu_group(), octoprint_presets()])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    ids = [device["id"] for device in config["devices"]]
    assert "printer_connection" not in ids and "printer_power" not in ids
    assert ids == ["host", "octoprint", "mine"]
    assert plugin._settings.config["system"]["actions"] == []


def test_first_start_never_adds_the_connection_twice_when_its_marker_is_already_on_a_control(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    plugin = make_plugin(core=SystemCommandEditorPlugin().get_settings_defaults()["core"])
    # the same OctoPrint group twice: the second connection finds its marker already on the first one's control
    plugin._load_sample_presets(detector=lambda: [], maintenance_detector=lambda: [],
                                plugin_groups=[octoprint_presets(), octoprint_presets()])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    connections = [device for device in config["devices"] if device.get("preset") == "octoprint:connection"]
    assert [device["id"] for device in connections] == ["printer_connection"]
    actions = plugin._settings.config["system"]["actions"]
    assert [entry["action"] for entry in actions if entry.get("group") == "printer_connection"] == [
        "printer_connection_connect", "printer_connection_disconnect",
    ]


def test_presets_answer_puts_the_webcam_group_between_harvested_groups_and_octoprint(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setattr(plugin_module, "detect", lambda: [])
    monkeypatch.setattr(plugin_module, "detect_maintenance", lambda env: [])
    monkeypatch.setattr(plugin_module, "webcam_presets", lambda: {"plugin": "webcam", "name": "Webcam services",
                                                                  "presets": []})
    plugin = make_plugin()
    plugin._maintenance_env = lambda: {"octoprint": "x", "basedir": "b", "timelapse": "t", "logs": "l"}
    plugin._plugin_presets = lambda: [psu_group()]
    with request_context():
        body = plugin.on_api_command("presets", {}).get_json()
    assert [group["plugin"] for group in body["plugins"]] == ["psucontrol", "webcam", "octoprint"]


def webcam_one_up(command, timeout=10):
    """Fake runner: only webcamd is installed, and sudo works."""
    if command == "systemctl cat webcamd.service >/dev/null 2>&1" or command == "sudo -n true":
        return 0, "", None
    return 1, "", None


def test_first_start_adds_only_the_available_webcam_switch_with_shell_commands(monkeypatch):
    monkeypatch.setattr(plugin_module.socket, "gethostname", lambda: "atom-octo.local")
    group = webcam_presets(runner=webcam_one_up, cameras_file="/nonexistent/octoprint_cameras")
    group["presets"] = [p for p in group["presets"] if p["preset"] in ("webcam:webcamd", "webcam:ustreamer")]
    assert [p["available"] for p in group["presets"]] == [True, False]
    plugin = make_plugin(core=SystemCommandEditorPlugin().get_settings_defaults()["core"])
    plugin._load_sample_presets(detector=lambda: [], maintenance_detector=lambda: [], plugin_groups=[group])
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert [device["id"] for device in config["devices"]] == ["host", "octoprint", "webcam_webcamd"]
    device = config["devices"][-1]
    assert device["preset"] == "webcam:webcamd"
    assert device["status"] == "systemctl is-active webcamd.service"
    assert device["on_pattern"] == "^active$" and device["off_pattern"] == "^(inactive|failed)$"
    actions = plugin._settings.config["system"]["actions"]
    assert actions == [
        {"action": "divider", "name": "Plugin devices"},
        {"action": "webcam_webcamd_start", "name": "Start", "command": "sudo systemctl start webcamd", "role": "on",
         "icon": "fa-solid fa-play", "group": "webcam_webcamd", "preset": "webcam:webcamd"},
        {"action": "webcam_webcamd_stop", "name": "Stop", "command": "sudo systemctl stop webcamd", "role": "off",
         "icon": "fa-solid fa-power-off", "group": "webcam_webcamd", "preset": "webcam:webcamd"},
        {"action": "webcam_webcamd_restart", "name": "Restart", "command": "sudo systemctl restart webcamd",
         "role": "restart", "icon": "fa-solid fa-rotate-right", "group": "webcam_webcamd", "preset": "webcam:webcamd"},
    ]


def test_first_start_default_groups_offer_the_webcam_group(monkeypatch):
    # no groups passed: the default list must include the webcam group, so a real first start offers its switch
    webcam = {"plugin": "webcam", "name": "Webcam services", "presets": [
        {"preset": "webcam:webcamd", "name": "Webcam (webcamd)", "available": True, "note": "",
         "control": {"type": "switch", "name": "Webcam (webcamd)", "icon": "fa-solid fa-video",
                     "status": "systemctl is-active webcamd.service", "on_pattern": "^active$",
                     "off_pattern": "^(inactive|failed)$"},
         "commands": [{"name": "Start", "command": "sudo systemctl start webcamd", "role": "on",
                       "icon": "fa-solid fa-play"}]}]}
    monkeypatch.setattr(plugin_module, "webcam_presets", lambda: webcam)
    monkeypatch.setattr(plugin_module, "octoprint_presets", lambda *args, **kwargs: {"plugin": "octoprint",
                                                                                    "name": "OctoPrint",
                                                                                    "presets": []})
    plugin = make_plugin(core=SystemCommandEditorPlugin().get_settings_defaults()["core"])
    plugin._plugin_presets = lambda: []
    plugin._add_ready_made()
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert [device["id"] for device in config["devices"]] == ["webcam_webcamd"]
    assert config["devices"][0]["status"] == "systemctl is-active webcamd.service"

