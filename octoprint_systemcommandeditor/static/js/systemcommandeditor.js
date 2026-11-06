/*
 * System Command Editor: edits OctoPrint's system.actions, the commands shown in the System menu.
 *
 * Commands are saved through the settings dialog like any core setting. The plugin keeps no copy of its own.
 */
$(function () {
    const DIVIDER = "divider";
    const ACTION_PATTERN = /^[a-z0-9_-]+$/;
    const FLAGS = ["async", "fresh_credentials", "ignore"];

    const isSection = (entry) => entry.action === DIVIDER;

    const slugify = (text) =>
        (text || "")
            .toLowerCase()
            .normalize("NFKD")
            .replace(/[̀-ͯ]/g, "")
            .replace(/[^a-z0-9]+/g, "_")
            .replace(/^_+|_+$/g, "");

    // Copy an entry, dropping empty optional values, keeping keys this editor does not know about
    const clean = (entry) => {
        const result = {};
        _.each(entry, (value, key) => {
            if (key === "action") {
                result[key] = value;
            } else if (["name", "confirm", "icon"].includes(key) && !value) {
                // empty, leave out
            } else if (FLAGS.includes(key) && !value) {
                // false, leave out
            } else if (value !== undefined && value !== null) {
                result[key] = value;
            }
        });
        return result;
    };

    function SystemCommandEditorEditor(owner) {
        const self = this;

        self._deferred = undefined;
        // observable, so validation re-runs when the dialog opens on another entry with the same identifier
        self._original = ko.observable(undefined);
        self._actionTouched = false;

        self.isSection = ko.observable(false);
        // one of OctoPrint's own entries: only its icon and heading can change
        self.isCore = ko.observable(false);
        self.editing = ko.observable(false);
        self.heading = ko.observable("");

        self.name = ko.observable("");
        self.action = ko.observable("");
        self.command = ko.observable("");
        self.icon = ko.observable("");
        self.useConfirm = ko.observable(false);
        self.confirmMessage = ko.observable("");
        self.async = ko.observable(false);
        self.freshCredentials = ko.observable(false);
        self.ignore = ko.observable(false);

        self.title = ko.pureComputed(() => {
            if (self.isCore()) return gettext("Edit built-in command");
            if (self.isSection()) {
                return self.editing() ? gettext("Edit section") : gettext("Add section");
            }
            return self.editing() ? gettext("Edit command") : gettext("Add command");
        });

        // Until the identifier is edited by hand, it follows the name
        self.name.subscribe((value) => {
            if (!self.isSection() && !self.isCore() && !self._actionTouched) {
                self.action(slugify(value));
            }
        });
        self.actionEdited = () => {
            self._actionTouched = true;
            return true;
        };

        self.nameError = ko.pureComputed(() => {
            if (self.isSection() || self.isCore()) return "";
            return self.name().trim() ? "" : gettext("A name is required.");
        });
        self.actionError = ko.pureComputed(() => {
            if (self.isSection() || self.isCore()) return "";
            const action = self.action().trim();
            if (!action) return gettext("An identifier is required.");
            if (action === DIVIDER) return gettext("This identifier is reserved.");
            // OctoPrint accepts any identifier for these commands and older versions of this editor did not check
            // it, so an existing one is left alone; only a new or changed identifier has to follow the pattern
            const original = self._original();
            const unchanged = original && original.action === action;
            if (!unchanged && !ACTION_PATTERN.test(action))
                return gettext("Use lowercase letters, digits, - and _ only.");
            if (owner.actionInUse(action, self._original())) {
                return gettext("Another command already uses this identifier.");
            }
            return "";
        });
        self.commandError = ko.pureComputed(() => {
            if (self.isSection() || self.isCore()) return "";
            return self.command().trim() ? "" : gettext("A command is required.");
        });
        self.valid = ko.pureComputed(
            () => !self.nameError() && !self.actionError() && !self.commandError()
        );

        self.toEntry = () => {
            if (self.isCore()) {
                return {action: self.action(), name: self.name(), icon: self.icon().trim(), heading: self.heading().trim()};
            }
            if (self.isSection()) {
                return clean(_.extend({}, self._original() || {}, {action: DIVIDER, name: self.name().trim()}));
            }
            const entry = _.extend({}, self._original() || {}, {
                action: self.action().trim(),
                name: self.name().trim(),
                command: self.command().trim(),
                icon: self.icon().trim(),
                confirm: self.useConfirm() ? self.confirmMessage().trim() || gettext("Are you sure?") : "",
                async: self.async(),
                fresh_credentials: self.freshCredentials(),
                ignore: self.ignore()
            });
            return clean(entry);
        };

        // Only a saved, unchanged command can be run: the server runs what is saved, not what is in this dialog
        self.canRun = ko.pureComputed(() => {
            if (self.isSection() || self.isCore() || !self.editing() || !self.valid()) return false;
            return owner.isSavedAsIs(self.toEntry());
        });
        self.runTitle = ko.pureComputed(() =>
            self.canRun() ? gettext("Run the saved command now") : gettext("Save your changes first")
        );
        self.runNow = () => {
            owner.run(self.toEntry());
        };

        self.show = (entry, section, core) => {
            if (self._deferred) {
                self._deferred.reject();
            }
            self._deferred = $.Deferred();

            entry = entry || {};
            self._original(_.isEmpty(entry) ? undefined : entry);
            self._actionTouched = !!entry.action;
            self.isSection(!!section);
            self.isCore(!!core);
            self.editing(!_.isEmpty(entry));
            self.heading(entry.heading || "");

            self.name(entry.name || "");
            self.action(entry.action && entry.action !== DIVIDER ? entry.action : "");
            self.command(entry.command || "");
            self.icon(entry.icon || "");
            self.useConfirm(!!entry.confirm);
            self.confirmMessage(entry.confirm || "");
            self.async(!!entry.async);
            self.freshCredentials(!!entry.fresh_credentials);
            self.ignore(!!entry.ignore);

            owner.dialog.modal("show");
            window.setTimeout(() => {
                owner.dialog.find("input:visible:first").focus();
            }, 300);
            return self._deferred.promise();
        };

        self.done = () => {
            if (!self.valid()) return;
            const deferred = self._deferred;
            self._deferred = undefined;
            owner.dialog.modal("hide");
            if (deferred) deferred.resolve(self.toEntry());
        };
        self.cancel = () => {
            owner.dialog.modal("hide");
        };

        self.onHidden = () => {
            if (self._deferred) {
                self._deferred.reject();
                self._deferred = undefined;
            }
        };
    }

    function SystemCommandEditorViewModel(parameters) {
        const self = this;

        self.loginState = parameters[0];
        self.access = parameters[1];
        self.settingsViewModel = parameters[2];
        self.systemViewModel = parameters[3];

        self.entries = ko.observableArray([]);
        // what the server has, observable so Run now updates once a save lands
        self._saved = ko.observable([]);
        self._dirty = false;

        self.iconChoices = [
            "fa-solid fa-power-off",
            "fa-solid fa-rotate-right",
            "fa-solid fa-arrows-rotate",
            "fa-solid fa-plug",
            "fa-solid fa-bolt",
            "fa-solid fa-lightbulb",
            "fa-solid fa-fan",
            "fa-solid fa-fire",
            "fa-solid fa-temperature-half",
            "fa-solid fa-video",
            "fa-solid fa-camera",
            "fa-solid fa-terminal",
            "fa-solid fa-code",
            "fa-solid fa-gear",
            "fa-solid fa-wrench",
            "fa-solid fa-server",
            "fa-solid fa-hard-drive",
            "fa-solid fa-wifi",
            "fa-solid fa-network-wired",
            "fa-solid fa-download",
            "fa-solid fa-upload",
            "fa-solid fa-broom",
            "fa-solid fa-play",
            "fa-solid fa-stop",
            "fa-solid fa-moon",
            "fa-solid fa-house"
        ];

        self.editor = new SystemCommandEditorEditor(self);

        // icons and headings for OctoPrint's own entries, kept in this plugin's settings
        self.coreSettings = ko.observable({});
        self.coreEntries = ko.pureComputed(() => {
            const settings = self.coreSettings();
            return _.filter(self.systemViewModel.systemActions(), (item) => item.actionSource === "core").map((item) =>
                _.extend({action: item.action, name: item.name, icon: "", heading: ""}, settings[item.action] || {})
            );
        });
        self.editCore = (entry) => {
            self.editor.show(entry, false, true).done((edited) => {
                const settings = _.extend({}, self.coreSettings());
                settings[edited.action] = {icon: edited.icon, heading: edited.heading};
                self.coreSettings(settings);
                self._coreDirty = true;
            });
        };
        self.showServerSettings = () => {
            self.settingsViewModel.selectTab("#settings_server");
        };
        self._coreDirty = false;

        self.isSection = isSection;

        // --- loading and saving ----------------------------------------------------------------------------------

        self._fromSettings = () => {
            // core saves system.actions from its own system_actions observable, so read and write that
            const actions = ko.toJS(self.settingsViewModel.system_actions) || [];
            return actions.filter((entry) => entry && entry.action).map(clean);
        };

        self._coreFromSettings = () => {
            const plugin = self.settingsViewModel.settings.plugins.systemcommandeditor;
            return (plugin && ko.toJS(plugin.core)) || {};
        };

        self.load = () => {
            self.coreSettings(self._coreFromSettings());
            self._coreDirty = false;
            self._saved(self._fromSettings());
            self.entries(self._saved().map((entry) => _.extend({}, entry)));
            self._dirty = false;
        };

        self.changed = () => {
            self._dirty = true;
        };

        self.onBeforeBinding = () => {
            self.dialog = $("#sce-editor");
            self.dialog.on("hidden", self.editor.onHidden);
        };

        self.onSettingsShown = self.load;

        self.onSettingsBeforeSave = () => {
            if (!self.loginState.hasPermission(self.access.permissions.SETTINGS)) return;
            if (self._coreDirty) {
                const core = self.settingsViewModel.settings.plugins.systemcommandeditor.core;
                _.each(self.coreSettings(), (value, action) => {
                    if (core[action]) {
                        core[action].icon(value.icon || "");
                        core[action].heading(value.heading || "");
                    }
                });
                self._coreDirty = false;
            }
            if (!self._dirty) return;
            self.settingsViewModel.system_actions(self.entries().map(clean));
            self._dirty = false;
        };

        self.onEventSettingsUpdated = () => {
            // a save from this or another browser: what is saved is what Run now runs
            self._saved(self._fromSettings());
            if (!self._coreDirty) self.coreSettings(self._coreFromSettings());
            if (!self._dirty) {
                self.entries(self._saved().map((entry) => _.extend({}, entry)));
            }
        };

        // --- queries the editor uses ------------------------------------------------------------------------------

        self.actionInUse = (action, except) =>
            _.some(self.entries(), (entry) => entry !== except && !isSection(entry) && entry.action === action);

        self.isSavedAsIs = (entry) =>
            _.some(self._saved(), (saved) => _.isEqual(clean(saved), clean(entry)));

        self.run = (entry) => {
            const spec = _.find(
                self.systemViewModel.systemActions(),
                (item) => item.actionSource === "custom" && item.action === entry.action
            );
            if (!spec) {
                new PNotify({
                    title: gettext("Not available yet"),
                    text: gettext("Save your changes, then try again."),
                    type: "info"
                });
                return;
            }
            self.systemViewModel.triggerCommand(spec);
        };

        // --- list operations --------------------------------------------------------------------------------------

        const replace = (oldEntry, newEntry) => {
            const index = self.entries.indexOf(oldEntry);
            if (index >= 0) {
                self.entries.splice(index, 1, newEntry);
                self.changed();
            }
        };

        const uniqueAction = (base) => {
            let candidate = base;
            let counter = 2;
            while (self.actionInUse(candidate)) {
                candidate = base + "_" + counter++;
            }
            return candidate;
        };

        self.addCommand = () => {
            self.editor.show(null, false).done((entry) => {
                self.entries.push(entry);
                self.changed();
            });
        };

        self.addSection = () => {
            self.editor.show(null, true).done((entry) => {
                self.entries.push(entry);
                self.changed();
            });
        };

        self.edit = (entry) => {
            self.editor.show(entry, isSection(entry)).done((edited) => replace(entry, edited));
        };

        self.duplicate = (entry) => {
            const copy = _.extend({}, entry, {
                name: _.sprintf(gettext("%(name)s (copy)"), {name: entry.name}),
                action: uniqueAction(entry.action + "_copy")
            });
            self.entries.splice(self.entries.indexOf(entry) + 1, 0, copy);
            self.changed();
        };

        self.remove = (entry) => {
            const doRemove = () => {
                self.entries.remove(entry);
                self.changed();
            };
            if (isSection(entry)) {
                doRemove();
                return;
            }
            showConfirmationDialog({
                message: _.sprintf(gettext('This removes the command "%(name)s".'), {
                    name: _.escape(entry.name)
                }),
                onproceed: doRemove
            });
        };

        const move = (from, to) => {
            if (from === to || to < 0 || to >= self.entries().length) return;
            const list = self.entries().slice();
            const [entry] = list.splice(from, 1);
            list.splice(to, 0, entry);
            self.entries(list);
            self.changed();
        };

        self.moveUp = (entry) => move(self.entries.indexOf(entry), self.entries.indexOf(entry) - 1);
        self.moveDown = (entry) => move(self.entries.indexOf(entry), self.entries.indexOf(entry) + 1);

        // --- drag and drop ----------------------------------------------------------------------------------------

        self._dragFrom = undefined;
        self.dragOver = ko.observable(undefined);

        self.dragStart = (entry, event) => {
            self._dragFrom = self.entries.indexOf(entry);
            const dt = event.originalEvent && event.originalEvent.dataTransfer;
            if (dt) {
                dt.effectAllowed = "move";
                dt.setData("text/plain", String(self._dragFrom));
            }
            return true;
        };
        // OctoPrint before 1.10 opens its file upload overlay on any dragenter that reaches the document,
        // which covers the list and cancels the drag. Keep our own drags from reaching it.
        self.dragEnterRow = (entry, event) => {
            if (self._dragFrom !== undefined) event.stopPropagation();
            return true;
        };
        self.dragOverRow = (entry, event) => {
            if (self._dragFrom === undefined) return true;
            event.preventDefault();
            self.dragOver(self.entries.indexOf(entry));
            return false;
        };
        self.dragLeave = () => {
            self.dragOver(undefined);
            return true;
        };
        self.dropRow = (entry, event) => {
            event.preventDefault();
            if (self._dragFrom !== undefined) {
                move(self._dragFrom, self.entries.indexOf(entry));
            }
            self.dragEnd();
            return false;
        };
        self.dragEnd = () => {
            self._dragFrom = undefined;
            self.dragOver(undefined);
            return true;
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: SystemCommandEditorViewModel,
        dependencies: ["loginStateViewModel", "accessViewModel", "settingsViewModel", "systemViewModel"],
        elements: ["#settings_plugin_systemcommandeditor"]
    });
});
