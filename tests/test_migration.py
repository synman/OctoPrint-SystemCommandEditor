import logging

import pytest
import yaml

pytest.importorskip("octoprint")

from octoprint_systemcommandeditor import SystemCommandEditorPlugin  # noqa: E402


class FakeSettings:
    """The parts of PluginSettings the migration uses, over a plain config dict."""

    def __init__(self, system_actions, plugin_actions, devices=None):
        self.config = {"system": {"actions": system_actions}, "plugins": {"systemcommandeditor": {}}}
        if plugin_actions is not None:
            self.config["plugins"]["systemcommandeditor"]["actions"] = plugin_actions
        if devices is not None:
            self.config["plugins"]["systemcommandeditor"]["devices"] = devices
        self.global_sets = []
        self.saved = 0

    def global_get(self, path):
        node = self.config
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node

    def global_set(self, path, value):
        self.global_sets.append(path)
        node = self.config
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    def get(self, path, merged=False):
        node = self.config["plugins"]["systemcommandeditor"]
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node

    def set(self, path, value):
        node = self.config["plugins"]["systemcommandeditor"]
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    def remove(self, path):
        self.config["plugins"]["systemcommandeditor"].pop(path[0], None)

    def save(self):
        self.saved += 1


def make_plugin(tmp_path, system_actions, plugin_actions, devices=None):
    plugin = SystemCommandEditorPlugin()
    plugin._settings = FakeSettings(system_actions, plugin_actions, devices)
    plugin._logger = logging.getLogger("test")
    plugin.get_plugin_data_folder = lambda: str(tmp_path)
    return plugin


def cmd(action):
    return {"action": action, "name": action, "command": "true"}


def readout(id_, section=None, name=None):
    device = {"id": id_, "type": "readout", "name": name or id_.title(), "icon": "", "status": "true"}
    if section is not None:
        device["section"] = section
    return device


def placement(id_, name=None, preset=None):
    entry = {"action": id_, "name": name or id_.title(), "device": id_}
    if preset is not None:
        entry["preset"] = preset
    return entry


def test_legacy_copy_is_merged_and_dropped(tmp_path):
    plugin = make_plugin(tmp_path, [cmd("a"), cmd("a")], [cmd("a"), cmd("b")])
    plugin.on_settings_migrate(2, None)

    config = plugin._settings.config
    assert config["system"]["actions"] == [cmd("a"), {"action": "divider"}, cmd("b")]
    assert "actions" not in config["plugins"]["systemcommandeditor"]

    backups = list(tmp_path.iterdir())
    assert len(backups) == 1
    saved = yaml.safe_load(backups[0].read_text())
    assert saved == {"system_actions": [cmd("a"), cmd("a")], "plugin_actions": [cmd("a"), cmd("b")]}


def test_clean_install_writes_nothing(tmp_path):
    plugin = make_plugin(tmp_path, [], None)
    plugin.on_settings_migrate(2, None)
    assert plugin._settings.global_sets == []
    assert list(tmp_path.iterdir()) == []


def test_already_clean_list_is_not_rewritten(tmp_path):
    plugin = make_plugin(tmp_path, [cmd("a")], [cmd("a")])
    plugin.on_settings_migrate(2, 1)
    assert plugin._settings.global_sets == []
    assert "actions" not in plugin._settings.config["plugins"]["systemcommandeditor"]


def test_current_version_is_left_alone(tmp_path):
    plugin = make_plugin(tmp_path, [cmd("a"), cmd("a")], [cmd("b")])
    plugin.on_settings_migrate(3, 3)
    assert plugin._settings.config["system"]["actions"] == [cmd("a"), cmd("a")]
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["actions"] == [cmd("b")]
    assert list(tmp_path.iterdir()) == []


# version 3: readouts get placements


def test_v3_places_readout_after_last_entry_of_its_section(tmp_path):
    actions = [cmd("a"), {"action": "divider", "name": "Status"}, cmd("b"), cmd("c"), {"action": "divider"}, cmd("d")]
    plugin = make_plugin(tmp_path, actions, None, devices=[readout("disk", "Status")])
    plugin.on_settings_migrate(3, 2)

    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert config["devices"] == [dict(readout("disk"), preset="disk")]
    assert plugin._settings.config["system"]["actions"] == [
        cmd("a"),
        {"action": "divider", "name": "Status"},
        cmd("b"),
        cmd("c"),
        placement("disk", preset="disk"),
        {"action": "divider"},
        cmd("d"),
    ]


def test_v3_creates_missing_divider_at_end(tmp_path):
    plugin = make_plugin(tmp_path, [cmd("a")], None, devices=[readout("disk", "Status")])
    plugin.on_settings_migrate(3, 2)
    assert plugin._settings.config["system"]["actions"] == [
        cmd("a"),
        {"action": "divider", "name": "Status"},
        placement("disk", preset="disk"),
    ]


def test_v3_readout_without_section_goes_to_end(tmp_path):
    plugin = make_plugin(tmp_path, [cmd("a"), {"action": "divider", "name": "X"}, cmd("b")], None, devices=[readout("ip")])
    plugin.on_settings_migrate(3, 2)
    assert plugin._settings.config["system"]["actions"][-1] == placement("ip", preset="ip")
    assert plugin._settings.config["system"]["actions"][:3] == [cmd("a"), {"action": "divider", "name": "X"}, cmd("b")]


def test_v3_keeps_readout_order_and_strips_section_only_from_readouts(tmp_path):
    devices = [
        readout("one", "Status"),
        {"id": "docs", "type": "switch", "name": "Docs", "section": "Status"},
        readout("two", "Status"),
    ]
    plugin = make_plugin(tmp_path, [{"action": "divider", "name": "Status"}], None, devices=devices)
    plugin.on_settings_migrate(3, 2)

    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert config["devices"] == [
        readout("one"),
        {"id": "docs", "type": "switch", "name": "Docs", "section": "Status"},
        readout("two"),
    ]
    assert plugin._settings.config["system"]["actions"] == [
        {"action": "divider", "name": "Status"},
        placement("one"),
        placement("two"),
    ]


def test_v3_leaves_readout_that_already_has_placement_alone(tmp_path):
    actions = [{"action": "divider", "name": "Status"}, placement("disk", "Disk")]
    plugin = make_plugin(tmp_path, actions, None, devices=[readout("disk", "Status")])
    plugin.on_settings_migrate(3, 2)
    assert plugin._settings.config["system"]["actions"] == [
        {"action": "divider", "name": "Status"},
        placement("disk", "Disk", preset="disk"),
    ]
    assert "section" not in plugin._settings.config["plugins"]["systemcommandeditor"]["devices"][0]


def test_v3_without_readouts_writes_nothing(tmp_path):
    devices = [{"id": "sw", "type": "switch", "name": "Sw", "section": "X"}]
    plugin = make_plugin(tmp_path, [cmd("a")], None, devices=devices)
    plugin.on_settings_migrate(3, 2)
    assert plugin._settings.global_sets == []
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"] == devices


# first-start loader


def test_loader_places_sample_readouts_under_status(tmp_path):
    plugin = make_plugin(tmp_path, [cmd("a")], None)

    def detector():
        return [
            {"id": "disk", "type": "readout", "name": "Free disk", "icon": "", "status": "df", "sample": True,
             "available": True, "value": "1"},
            {"id": "memory", "type": "readout", "name": "Free memory", "icon": "", "status": "free", "sample": False,
             "available": True, "value": "2"},
        ]

    plugin._load_sample_presets(detector=detector, maintenance_detector=lambda: [], plugin_groups=[])

    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    readouts = [d for d in config["devices"] if d.get("type") == "readout"]
    assert [d["id"] for d in readouts] == ["disk"]
    assert "section" not in readouts[0]
    assert plugin._settings.config["system"]["actions"] == [
        cmd("a"),
        {"action": "divider", "name": "Status"},
        {"action": "disk", "name": "Free disk", "device": "disk", "preset": "disk"},
    ]
    assert config["presets_loaded"] is True


# version 4: presets carry a marker


def test_v4_marks_readout_placement_and_switches_by_the_old_rule(tmp_path):
    devices = [
        {"id": "host", "type": "switch", "name": "Host", "icon": ""},
        {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": ""},
        readout("disk", "Status"),
        readout("mine"),
    ]
    actions = [{"action": "divider", "name": "Status"}, placement("disk")]
    plugin = make_plugin(tmp_path, actions, None, devices=devices)
    plugin.on_settings_migrate(4, 3)
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert [d.get("preset") for d in config["devices"]] == ["host", "octoprint", "disk", None]
    assert plugin._settings.config["system"]["actions"] == [
        {"action": "divider", "name": "Status"},
        placement("disk", preset="disk"),
    ]


def test_v4_marks_maintenance_command_and_its_action_control(tmp_path):
    devices = [{"id": "backup_now", "type": "action", "name": "Back up", "icon": ""}]
    actions = [cmd("a"), {"action": "divider", "name": "Maintenance"},
               {"action": "backup_now", "name": "Back up", "command": "x", "group": "backup_now", "role": "run"}]
    plugin = make_plugin(tmp_path, actions, None, devices=devices)
    plugin.on_settings_migrate(4, 3)
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert config["devices"][0]["preset"] == "backup_now"
    assert plugin._settings.config["system"]["actions"][2]["preset"] == "backup_now"
    assert "preset" not in plugin._settings.config["system"]["actions"][0]


def test_v4_keeps_an_existing_marker(tmp_path):
    devices = [{"id": "disk", "type": "readout", "name": "Disk", "icon": "", "status": "df", "preset": "mine"}]
    plugin = make_plugin(tmp_path, [], None, devices=devices)
    plugin.on_settings_migrate(4, 3)
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"][0]["preset"] == "mine"
    assert plugin._settings.global_sets == []


def test_v4_writes_nothing_when_nothing_matches(tmp_path):
    devices = [{"id": "fan", "type": "switch", "name": "Fan", "icon": ""}]
    plugin = make_plugin(tmp_path, [cmd("a")], None, devices=devices)
    plugin.on_settings_migrate(4, 3)
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"] == devices
    assert plugin._settings.global_sets == []


def test_v4_skipped_when_already_at_4(tmp_path):
    devices = [{"id": "disk", "type": "readout", "name": "Disk", "icon": "", "status": "df"}]
    plugin = make_plugin(tmp_path, [], None, devices=devices)
    plugin.on_settings_migrate(4, 4)
    assert "preset" not in plugin._settings.config["plugins"]["systemcommandeditor"]["devices"][0]


def link_device(id_, name, **extra):
    return {"id": id_, "type": "link", "name": name, "icon": "fa-solid fa-camera", "url": "http://cam/" + id_, **extra}


def test_v5_turns_each_link_device_into_a_standalone_link_command_at_the_end(tmp_path):
    when = {"mode": "only", "states": ["ready"], "effect": "hide"}
    devices = [
        {"id": "host", "type": "switch", "name": "Host", "icon": "", "preset": "host"},
        link_device("webcam_snapshot", "Webcam snapshot", icon_color="#00ff00", icon_invert=True, when=when,
                    preset="octoprint:snapshot"),
        {"id": "fan", "type": "switch", "name": "Fan", "icon": "", "status": "x"},
        link_device("cam", "Cam"),
    ]
    actions = [cmd("a"), {"action": "divider", "name": "Status"}]
    plugin = make_plugin(tmp_path, actions, None, devices=devices)
    plugin.on_settings_migrate(5, 4)
    config = plugin._settings.config["plugins"]["systemcommandeditor"]
    assert config["devices"] == [devices[0], devices[2]]
    assert plugin._settings.config["system"]["actions"] == [
        cmd("a"),
        {"action": "divider", "name": "Status"},
        {"action": "webcam_snapshot", "name": "Webcam snapshot", "url": "http://cam/webcam_snapshot",
         "icon": "fa-solid fa-camera", "icon_color": "#00ff00", "icon_invert": True, "when": when,
         "preset": "octoprint:snapshot"},
        {"action": "cam", "name": "Cam", "url": "http://cam/cam", "icon": "fa-solid fa-camera"},
    ]


def test_v5_two_link_devices_with_the_same_name_get_different_keys(tmp_path):
    devices = [link_device("first", "Cam"), link_device("second", "Cam")]
    plugin = make_plugin(tmp_path, [], None, devices=devices)
    plugin.on_settings_migrate(5, 4)
    assert [a["action"] for a in plugin._settings.config["system"]["actions"]] == ["cam", "cam_2"]


def test_v5_key_for_a_link_takes_a_key_already_in_use(tmp_path):
    devices = [link_device("x", "A")]
    plugin = make_plugin(tmp_path, [cmd("a")], None, devices=devices)
    plugin.on_settings_migrate(5, 4)
    assert plugin._settings.config["system"]["actions"][-1]["action"] == "a_2"


def test_v5_leaves_a_config_with_no_link_device_untouched(tmp_path):
    devices = [{"id": "fan", "type": "switch", "name": "Fan", "icon": ""}]
    plugin = make_plugin(tmp_path, [cmd("a")], None, devices=devices)
    plugin.on_settings_migrate(5, 4)
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"] == devices
    assert plugin._settings.global_sets == []


def test_v5_skipped_when_already_at_5(tmp_path):
    devices = [link_device("cam", "Cam")]
    plugin = make_plugin(tmp_path, [], None, devices=devices)
    plugin.on_settings_migrate(5, 5)
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["devices"] == devices
    assert plugin._settings.global_sets == []
