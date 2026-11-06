# coding=utf-8
from __future__ import absolute_import

__author__ = "Shell M. Shrader, originally Marc Hannappel <salandora@gmail.com>"
__license__ = "GNU Affero General Public License http://www.gnu.org/licenses/agpl.html"

import copy
import io
import os
import time

import yaml

import octoprint.plugin

from .convert import command_count, convert


class SystemCommandEditorPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.AssetPlugin,
):
    # Commands live only in OctoPrint's own system.actions, which the settings dialog saves through the core
    # settings API. The plugin keeps no copy: versions before 2.0 did, and copying it back over system.actions
    # on startup is what made commands vanish.

    def get_settings_defaults(self):
        # Icons and headings for OctoPrint's own System menu entries, which its config has no place for. These are
        # decoration only; what the entries run stays in OctoPrint's server.commands.
        return {
            "core": {
                "shutdown": {"icon": "fa-solid fa-power-off", "heading": "System"},
                "reboot": {"icon": "fa-solid fa-rotate-right", "heading": ""},
                "restart": {"icon": "fa-solid fa-arrows-rotate", "heading": "OctoPrint"},
                "restart_safe": {"icon": "fa-solid fa-shield-halved", "heading": ""},
            }
        }

    def get_settings_version(self):
        return 2

    def on_settings_migrate(self, target, current):
        if current is not None and current >= 2:
            return

        system_actions = copy.deepcopy(self._settings.global_get(["system", "actions"]) or [])
        legacy_actions = copy.deepcopy(self._settings.get(["actions"]) or [])

        self._backup(system_actions, legacy_actions)

        actions, report = convert(system_actions, legacy_actions)

        if command_count(actions) < command_count(system_actions):
            # convert() only ever adds commands; never write a shorter list over a longer one
            self._logger.error(
                "Not converting system commands: the result would hold fewer commands than system.actions"
            )
            return

        if actions != system_actions:
            self._settings.global_set(["system", "actions"], actions)
        self._settings.remove(["actions"])

        self._logger.info(
            "Converted system commands: %d commands, %d duplicates removed %s, %d restored from the plugin's "
            "old copy %s, %d invalid entries skipped",
            command_count(actions),
            len(report["duplicates"]),
            report["duplicates"],
            len(report["restored"]),
            report["restored"],
            report["invalid"],
        )

    def _backup(self, system_actions, legacy_actions):
        if not system_actions and not legacy_actions:
            return
        path = os.path.join(
            self.get_plugin_data_folder(), "pre-2.0-backup-{}.yaml".format(time.strftime("%Y%m%d-%H%M%S"))
        )
        try:
            with io.open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    {"system_actions": system_actions, "plugin_actions": legacy_actions},
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                )
            self._logger.info("Saved the system commands as they were before conversion to %s", path)
        except Exception:
            self._logger.exception("Could not save a backup of the system commands to %s", path)

    def is_template_autoescaped(self):
        return True

    def get_template_configs(self):
        return [{"type": "settings", "custom_bindings": True}]

    def get_assets(self):
        return {
            "js": ["js/systemcommandeditor.js", "js/systemcommandeditor_menu.js"],
            "css": ["css/systemcommandeditor.css"],
        }

    def get_update_information(self):
        return {
            "systemcommandeditor": {
                "displayName": "System Command Editor Plugin",
                "displayVersion": self._plugin_version,
                "type": "github_release",
                "user": "synman",
                "repo": "OctoPrint-SystemCommandEditor",
                "current": self._plugin_version,
                "pip": "https://github.com/synman/OctoPrint-SystemCommandEditor/archive/{target_version}.zip",
            }
        }


__plugin_name__ = "System Command Editor"
__plugin_pythoncompat__ = ">=3.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = SystemCommandEditorPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }

    global __plugin_license__
    __plugin_license__ = "AGPLv3"
