import logging

import pytest
import yaml

pytest.importorskip("octoprint")

from octoprint_systemcommandeditor import SystemCommandEditorPlugin  # noqa: E402


class FakeSettings:
    """The parts of PluginSettings the migration uses, over a plain config dict."""

    def __init__(self, system_actions, plugin_actions):
        self.config = {"system": {"actions": system_actions}, "plugins": {"systemcommandeditor": {}}}
        if plugin_actions is not None:
            self.config["plugins"]["systemcommandeditor"]["actions"] = plugin_actions
        self.global_sets = []

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

    def get(self, path):
        return self.config["plugins"]["systemcommandeditor"].get(path[0])

    def remove(self, path):
        self.config["plugins"]["systemcommandeditor"].pop(path[0], None)


def make_plugin(tmp_path, system_actions, plugin_actions):
    plugin = SystemCommandEditorPlugin()
    plugin._settings = FakeSettings(system_actions, plugin_actions)
    plugin._logger = logging.getLogger("test")
    plugin.get_plugin_data_folder = lambda: str(tmp_path)
    return plugin


def cmd(action):
    return {"action": action, "name": action, "command": "true"}


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
    plugin.on_settings_migrate(2, 2)
    assert plugin._settings.config["system"]["actions"] == [cmd("a"), cmd("a")]
    assert plugin._settings.config["plugins"]["systemcommandeditor"]["actions"] == [cmd("b")]
    assert list(tmp_path.iterdir()) == []
