# coding=utf-8
from __future__ import absolute_import

__author__ = "Marc Hannappel <salandora@gmail.com>"
__license__ = 'GNU Affero General Public License http://www.gnu.org/licenses/agpl.html'

import octoprint.plugin

class SystemCommandEditorPlugin(octoprint.plugin.SettingsPlugin,
								octoprint.plugin.StartupPlugin,
								octoprint.plugin.TemplatePlugin,
								octoprint.plugin.BlueprintPlugin,
								octoprint.plugin.AssetPlugin):
	def get_settings_defaults(self):
		return dict(actions=[])

	def on_settings_save(self, data):
		self._settings.global_set(["system"], data)
		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

	def get_template_configs(self):
		if "editorcollection" in self._plugin_manager.enabled_plugins:
			return [
				dict(type="plugin_editorcollection_EditorCollection", template="systemcommandeditor_hookedsettings.jinja2", custom_bindings=True)
			]
		else:
			return [
				dict(type="settings", template="systemcommandeditor_hookedsettings.jinja2", custom_bindings=True)
			]

	def get_assets(self):
		return dict(
			js=["js/jquery.ui.sortable.js",
				"js/systemcommandeditor.js",
				"js/systemcommandeditorDialog.js"],
			css=["css/systemcommandeditor.css"]
		)

	def is_blueprint_csrf_protected(self):
		return True

	def on_after_startup(self):
		self.actions = self._settings.get(["actions"])
		if not self.actions is None:
			self._settings.global_set(["system", "actions"], self.actions)


	def get_update_information(self):
		return dict(
			systemcommandeditor=dict(
				displayName="System Command Editor Plugin",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="synman",
				repo="OctoPrint-SystemCommandEditor",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/synman/OctoPrint-SystemCommandEditor/archive/{target_version}.zip"
			)
		)


__plugin_name__ = "System Command Editor"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = SystemCommandEditorPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}

	global __plugin_license__
	__plugin_license__ = "AGPLv3"
