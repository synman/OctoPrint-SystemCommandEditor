/*
 * System Command Editor: edits OctoPrint's system.actions, the commands shown in the System menu, and the devices
 * those commands can drive.
 *
 * Commands are saved through the settings dialog like any core setting. Devices are kept in this plugin's settings.
 */
$(function () {
    const DIVIDER = "divider";
    const ACTION_PATTERN = /^[a-z0-9_-]+$/;
    const FLAGS = ["async", "fresh_credentials", "ignore"];
    const STATES = ["printing", "paused", "ready", "offline", "error"];

    const isSection = (entry) => entry.action === DIVIDER;

    // Icon suggestions under an icon input. The command and device editors each call this once with their icon
    // observable and bind the result to their input and list (see the icon_field macro in the settings template).
    function iconSuggest(icon) {
        const s = {};
        s.focused = ko.observable(false);
        s.open = ko.observable(true);
        s.index = ko.observable(0);
        s.swallowEscape = false;
        s.result = ko.pureComputed(() => window.SceIconSearch.search(window.SceIcons, icon() || "", 40));
        s.items = ko.pureComputed(() => s.result().items);
        s.more = ko.pureComputed(() => s.result().more);
        s.visible = ko.pureComputed(() => {
            if (!s.focused() || !s.open() || s.items().length === 0) return false;
            const text = (icon() || "").trim();
            return !s.items().some((item) => item.cls === text);
        });
        icon.subscribe(() => {
            s.index(0);
            s.open(true);
        });
        s.pick = (item) => {
            icon(item.cls);
            s.open(false);
            return false;
        };
        s.keydown = (data, event) => {
            if (!s.visible()) return true;
            const n = s.items().length;
            switch (event.key) {
                case "ArrowDown":
                    s.index((s.index() + 1) % n);
                    return false;
                case "ArrowUp":
                    s.index((s.index() - 1 + n) % n);
                    return false;
                case "Enter":
                case "Tab":
                    s.pick(s.items()[s.index()]);
                    return false;
                case "Escape":
                    // hide the list only; the keyup that follows must not close the modal
                    s.open(false);
                    s.swallowEscape = true;
                    return false;
            }
            return true;
        };
        s.keyup = (data, event) => {
            if (s.swallowEscape && event.key === "Escape") {
                s.swallowEscape = false;
                event.stopPropagation();   // Bootstrap's modal closes on an Escape keyup that reaches it
                return false;
            }
            return true;
        };
        return s;
    }

    // the color input needs a valid color; an empty icon color shows as gray there and stays empty until picked
    const colorPick = (color) => ko.pureComputed({
        read: () => color() || "#888888",
        write: (value) => color(value)
    });

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
            } else if (["name", "confirm", "icon", "icon_color", "group", "role", "value"].includes(key) && !value) {
                // empty, leave out
            } else if (FLAGS.includes(key) && !value) {
                // false, leave out
            } else if (value !== undefined && value !== null) {
                result[key] = value;
            }
        });
        return result;
    };

    // Copy a device, dropping every empty value
    const dropEmpty = (object) => {
        const result = {};
        _.each(object, (value, key) => {
            if (value !== undefined && value !== null && value !== "") result[key] = value;
        });
        return result;
    };

    const setField = (object, key, value) => {
        if (ko.isObservable(object[key])) {
            object[key](value);
        } else {
            object[key] = value;
        }
    };

    const ROLE_LABELS = {
        on: () => gettext("On"),
        off: () => gettext("Off"),
        restart: () => gettext("Restart"),
        safe_restart: () => gettext("Safe mode restart"),
        choice: () => gettext("Choice")
    };
    const TYPE_LABELS = {
        switch: () => gettext("Switch"),
        selector: () => gettext("Selector"),
        readout: () => gettext("Readout"),
        link: () => gettext("Link")
    };

    // A printer-state condition: shared by commands, built-in commands and links
    function Condition() {
        const self = this;

        self.enabled = ko.observable(false);
        self.mode = ko.observable("only");
        self.effect = ko.observable("disable");
        self.states = ko.observableArray([]);

        self.error = ko.pureComputed(() => {
            if (self.enabled() && !self.states().length) return gettext("Choose at least one printer state.");
            return "";
        });

        self.load = (when) => {
            self.enabled(!!when);
            self.mode(when && when.mode === "never" ? "never" : "only");
            self.effect(when && when.effect === "hide" ? "hide" : "disable");
            self.states(when && Array.isArray(when.states) ? when.states.slice() : []);
        };

        self.toWhen = () => {
            if (!self.enabled()) return null;
            return {
                mode: self.mode(),
                states: STATES.filter((state) => self.states().includes(state)),
                effect: self.effect()
            };
        };
    }

    function SystemCommandEditorEditor(owner) {
        const self = this;

        self._deferred = undefined;
        // observable, so validation re-runs when the dialog opens on another entry with the same identifier
        self._original = ko.observable(undefined);
        self._actionTouched = false;

        self.isSection = ko.observable(false);
        // one of OctoPrint's own entries: only its icon, heading and device can change
        self.isCore = ko.observable(false);
        self.editing = ko.observable(false);
        self.heading = ko.observable("");

        self.name = ko.observable("");
        self.action = ko.observable("");
        self.command = ko.observable("");
        self.icon = ko.observable("");
        self.iconColor = ko.observable("");
        self.iconColorPick = colorPick(self.iconColor);
        self.iconSuggest = iconSuggest(self.icon);
        self.useConfirm = ko.observable(false);
        self.confirmMessage = ko.observable("");
        self.async = ko.observable(false);
        self.freshCredentials = ko.observable(false);
        self.ignore = ko.observable(false);

        self.device = ko.observable("");
        self.role = ko.observable("");
        self.value = ko.observable("");
        self.condition = new Condition();

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

        // --- device assignment ------------------------------------------------------------------------------------

        self.deviceChoices = ko.pureComputed(() => {
            const choices = [{id: "", name: gettext("None")}];
            return choices.concat(owner.devices().filter((d) => d.type === "switch" || d.type === "selector"));
        });
        self.selectedDevice = ko.pureComputed(() => _.find(owner.devices(), (d) => d.id === self.device()));
        self.roleChoices = ko.pureComputed(() => {
            const d = self.selectedDevice();
            const choose = {value: "", label: gettext("Choose a role")};
            if (!d) return [];
            if (d.type === "switch") {
                return [choose,
                    {value: "on", label: ROLE_LABELS.on()},
                    {value: "off", label: ROLE_LABELS.off()},
                    {value: "restart", label: ROLE_LABELS.restart()},
                    {value: "safe_restart", label: ROLE_LABELS.safe_restart()}];
            }
            return [choose, {value: "choice", label: ROLE_LABELS.choice()}];
        });
        self.roleError = ko.pureComputed(() => {
            if (!self.device() || self.isSection()) return "";
            const known = self.roleChoices().some((choice) => choice.value && choice.value === self.role());
            return known ? "" : gettext("Choose a role for this device.");
        });
        self.valueError = ko.pureComputed(() => {
            if (!self.device() || self.role() !== "choice") return "";
            return self.value().trim() ? "" : gettext("A value is required for a choice.");
        });
        self.deviceError = ko.pureComputed(() => {
            const id = self.device();
            if (!id || self.isSection()) return "";
            if (!self.selectedDevice()) return gettext("Choose a device that exists.");
            const others = owner.membersOf(id).filter((m) => {
                if (m.core) return !(self.isCore() && m.key === self.action());
                return !(!self.isCore() && m.key === self._original());
            });
            if (others.some((m) => m.core !== self.isCore())) {
                return self.isCore()
                    ? gettext("This device already has custom commands, so a built-in command cannot use it.")
                    : gettext("A built-in command already uses this device, so a custom command cannot use it.");
            }
            if (self.selectedDevice().type === "switch" && self.role() &&
                others.some((m) => m.role === self.role())) {
                return gettext("This switch already has that role on another entry.");
            }
            return "";
        });

        self.valid = ko.pureComputed(
            () => !self.nameError() && !self.actionError() && !self.commandError() && !self.deviceError() &&
                !self.roleError() && !self.valueError() && !self.condition.error()
        );

        // device, role, value and condition as the entry stores them
        const deviceFields = () => {
            const device = self.isSection() ? "" : self.device();
            return {
                group: device,
                role: device ? self.role() : "",
                value: device && self.role() === "choice" ? self.value().trim() : "",
                when: self.isSection() ? null : self.condition.toWhen()
            };
        };

        self.toEntry = () => {
            if (self.isCore()) {
                return _.extend(
                    {action: self.action(), name: self.name(), icon: self.icon().trim(), icon_color: self.iconColor(),
                     heading: self.heading().trim()},
                    deviceFields()
                );
            }
            if (self.isSection()) {
                return clean(_.extend({}, self._original() || {}, {action: DIVIDER, name: self.name().trim()}));
            }
            const entry = _.extend({}, self._original() || {}, {
                action: self.action().trim(),
                name: self.name().trim(),
                command: self.command().trim(),
                icon: self.icon().trim(),
                icon_color: self.iconColor(),
                confirm: self.useConfirm() ? self.confirmMessage().trim() || gettext("Are you sure?") : "",
                async: self.async(),
                fresh_credentials: self.freshCredentials(),
                ignore: self.ignore()
            }, deviceFields());
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
            self.iconColor(entry.icon_color || "");
            self.useConfirm(!!entry.confirm);
            self.confirmMessage(entry.confirm || "");
            self.async(!!entry.async);
            self.freshCredentials(!!entry.fresh_credentials);
            self.ignore(!!entry.ignore);

            self.device(!section && entry.group ? entry.group : "");
            self.role(!section && entry.role ? entry.role : "");
            self.value(!section && entry.value ? String(entry.value) : "");
            self.condition.load(section ? null : entry.when);

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

    function DeviceEditor(owner) {
        const self = this;

        self._deferred = undefined;
        self._original = ko.observable(undefined);
        self._idTouched = false;

        self.editing = ko.observable(false);
        self.type = ko.observable("switch");
        self.name = ko.observable("");
        self.identifier = ko.observable("");
        self.icon = ko.observable("");
        self.iconColor = ko.observable("");
        self.iconColorPick = colorPick(self.iconColor);
        self.iconSuggest = iconSuggest(self.icon);
        self.status = ko.observable("");
        self.onPattern = ko.observable("");
        self.offPattern = ko.observable("");
        self.url = ko.observable("");
        self.section = ko.observable("");
        self.condition = new Condition();

        self.typeChoices = [
            {value: "switch", label: TYPE_LABELS.switch()},
            {value: "selector", label: TYPE_LABELS.selector()},
            {value: "readout", label: TYPE_LABELS.readout()},
            {value: "link", label: TYPE_LABELS.link()}
        ];
        self.typeLabel = (type) => (TYPE_LABELS[type] ? TYPE_LABELS[type]() : "");

        self.isSwitch = ko.pureComputed(() => self.type() === "switch");
        self.isLink = ko.pureComputed(() => self.type() === "link");
        self.hasStatus = ko.pureComputed(() => self.type() !== "link");
        self.hasSection = ko.pureComputed(() => self.type() === "readout" || self.type() === "link");

        self.title = ko.pureComputed(() => (self.editing() ? gettext("Edit device") : gettext("Add device")));

        self.sectionChoices = ko.pureComputed(() => {
            const names = owner.dividerNames().map((name) => ({value: name, label: name}));
            return [{value: "", label: gettext("End of menu")}].concat(names);
        });

        // Until the identifier is edited by hand, it follows the name
        self.name.subscribe((value) => {
            if (!self._idTouched) self.identifier(slugify(value));
        });
        self.identifierEdited = () => {
            self._idTouched = true;
            return true;
        };

        self.nameError = ko.pureComputed(() => (self.name().trim() ? "" : gettext("A name is required.")));
        self.identifierError = ko.pureComputed(() => {
            const id = self.identifier().trim();
            if (!id) return gettext("An identifier is required.");
            if (!ACTION_PATTERN.test(id)) return gettext("Use lowercase letters, digits, - and _ only.");
            const original = self._original();
            const taken = owner.devices().some((d) => d !== original && d.id === id);
            return taken ? gettext("Another device already uses this identifier.") : "";
        });
        self.statusError = ko.pureComputed(() => {
            if (self.type() !== "readout") return "";
            return self.status().trim() ? "" : gettext("A status command is required for a readout.");
        });
        self.urlError = ko.pureComputed(() => {
            if (self.type() !== "link") return "";
            return self.url().trim() ? "" : gettext("A URL is required for a link.");
        });
        self.valid = ko.pureComputed(
            () => !self.nameError() && !self.identifierError() && !self.statusError() && !self.urlError() &&
                !(self.isLink() && self.condition.error())
        );

        self.toDevice = () => {
            const type = self.type();
            const device = {
                id: self.identifier().trim(),
                type: type,
                name: self.name().trim(),
                icon: self.icon().trim(),
                icon_color: self.iconColor()
            };
            if (type !== "link") device.status = self.status().trim();
            if (type === "switch") {
                device.on_pattern = self.onPattern().trim();
                device.off_pattern = self.offPattern().trim();
            }
            if (type === "link") device.url = self.url().trim();
            if (type === "readout" || type === "link") device.section = self.section();
            if (type === "link") device.when = self.condition.toWhen();
            return dropEmpty(device);
        };

        self.show = (device) => {
            if (self._deferred) self._deferred.reject();
            self._deferred = $.Deferred();

            device = device || {};
            const editing = !_.isEmpty(device);
            self._original(editing ? device : undefined);
            self._idTouched = editing;
            self.editing(editing);

            self.name(device.name || "");
            self.identifier(device.id || "");
            self.type(device.type || "switch");
            self.icon(device.icon || "");
            self.iconColor(device.icon_color || "");
            self.status(device.status || "");
            self.onPattern(device.on_pattern || "");
            self.offPattern(device.off_pattern || "");
            self.url(device.url || "");
            self.section(device.section || "");
            self.condition.load(device.when);

            owner.deviceDialog.modal("show");
            window.setTimeout(() => {
                owner.deviceDialog.find("input:visible:first").focus();
            }, 300);
            return self._deferred.promise();
        };

        self.done = () => {
            if (!self.valid()) return;
            const deferred = self._deferred;
            self._deferred = undefined;
            owner.deviceDialog.modal("hide");
            if (deferred) deferred.resolve(self.toDevice());
        };
        self.cancel = () => {
            owner.deviceDialog.modal("hide");
        };

        self.onHidden = () => {
            if (self._deferred) {
                self._deferred.reject();
                self._deferred = undefined;
            }
        };
    }

    // "Add from presets": asks the server which readouts this system offers, and adds the ticked ones as devices
    function PresetPicker(owner) {
        const self = this;

        self.loading = ko.observable(false);
        self.failed = ko.observable(false);
        self.rows = ko.observableArray([]);
        self.canAdd = ko.pureComputed(() => self.rows().some((row) => row.checkable && row.checked()));

        self.open = () => {
            const existing = owner.devices().map((device) => device.id);
            self.rows([]);
            self.failed(false);
            self.loading(true);
            owner.presetDialog.modal("show");
            OctoPrint.simpleApiCommand("systemcommandeditor", "presets", {})
                .done((response) => {
                    self.rows(((response && response.presets) || []).map((preset) => {
                        const added = existing.includes(preset.id);
                        return {
                            preset: preset,
                            added: added,
                            checkable: !added && !!preset.available,
                            checked: ko.observable(added)
                        };
                    }));
                })
                .fail(() => self.failed(true))
                .always(() => self.loading(false));
        };

        self.label = (row) => {
            if (row.added) return gettext("Added");
            if (!row.preset.available) return gettext("Not available on this system");
            return "";
        };

        self.add = () => {
            self.rows().forEach((row) => {
                if (!row.checkable || !row.checked()) return;
                const preset = row.preset;
                owner.devices.push({
                    id: preset.id,
                    type: preset.type,
                    name: preset.name,
                    icon: preset.icon,
                    status: preset.status,
                    section: preset.section
                });
            });
            owner.presetDialog.modal("hide");
        };

        self.cancel = () => {
            owner.presetDialog.modal("hide");
        };
    }

    function SystemCommandEditorViewModel(parameters) {
        const self = this;

        self.loginState = parameters[0];
        self.access = parameters[1];
        self.settingsViewModel = parameters[2];
        self.systemViewModel = parameters[3];

        self.entries = ko.observableArray([]);
        self.devices = ko.observableArray([]);
        // what the server has, observable so Run now updates once a save lands
        self._saved = ko.observable([]);
        self._dirty = false;
        self._devicesJson = "[]";

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
            "fa-solid fa-house",
            "fa-solid fa-ruler",
            "fa-solid fa-circle"
        ];

        self.editor = new SystemCommandEditorEditor(self);
        self.deviceEditor = new DeviceEditor(self);
        self.presets = new PresetPicker(self);

        // icons and headings for OctoPrint's own entries, kept in this plugin's settings
        self.coreSettings = ko.observable({});
        self.coreEntries = ko.pureComputed(() => {
            const settings = self.coreSettings();
            return _.filter(self.systemViewModel.systemActions(), (item) => item.actionSource === "core").map((item) =>
                _.extend({action: item.action, name: item.name, icon: "", icon_color: "", heading: ""}, settings[item.action] || {})
            );
        });
        self.editCore = (entry) => {
            self.editor.show(entry, false, true).done((edited) => {
                const settings = _.extend({}, self.coreSettings());
                settings[edited.action] = {
                    icon: edited.icon,
                    icon_color: edited.icon_color,
                    heading: edited.heading,
                    group: edited.group,
                    role: edited.role,
                    value: edited.value,
                    when: edited.when
                };
                self.coreSettings(settings);
                self._coreDirty = true;
            });
        };
        self.showServerSettings = () => {
            self.settingsViewModel.selectTab("#settings_server");
        };
        self._coreDirty = false;

        self.isSection = isSection;

        // --- devices and their members ----------------------------------------------------------------------------

        self.dividerNames = ko.pureComputed(() =>
            _.uniq(_.compact(self.entries().filter(isSection).map((entry) => entry.name)))
        );

        // every entry that a device drives: custom commands (core: false) and built-in commands (core: true)
        self.membersOf = (deviceId) => {
            const members = self.entries()
                .filter((entry) => !isSection(entry) && entry.group === deviceId)
                .map((entry) => ({core: false, key: entry, role: entry.role || ""}));
            _.each(self.coreSettings(), (value, action) => {
                if (value && value.group === deviceId) members.push({core: true, key: action, role: value.role || ""});
            });
            return members;
        };

        self.badgeFor = (entry) => {
            if (!entry || !entry.group) return "";
            const device = _.find(self.devices(), (d) => d.id === entry.group);
            if (!device) return "";
            let role = "";
            if (entry.role) {
                role = " · " + (ROLE_LABELS[entry.role] ? ROLE_LABELS[entry.role]() : entry.role);
                if (entry.role === "choice" && entry.value) role += ": " + entry.value;
            }
            return device.name + role;
        };

        self.typeLabel = (type) => self.deviceEditor.typeLabel(type);

        self.addDevice = () => {
            self.deviceEditor.show(null).done((device) => {
                self.devices.push(device);
            });
        };

        self.editDevice = (device) => {
            self.deviceEditor.show(device).done((edited) => {
                const index = self.devices.indexOf(device);
                if (index < 0) return;
                self.devices.splice(index, 1, edited);
                if (edited.id !== device.id || edited.type !== device.type) {
                    // members follow a new identifier; a changed type clears their roles, which no longer fit
                    reassign(device.id, edited.id, edited.type === device.type);
                }
            });
        };

        self.removeDevice = (device) => {
            showConfirmationDialog({
                message: _.sprintf(gettext('This removes the device "%(name)s" and its role on each command.'), {
                    name: _.escape(device.name)
                }),
                onproceed: () => {
                    self.devices.remove(device);
                    reassign(device.id, "", false);
                }
            });
        };

        // moves every member of one device to another identifier; "" detaches them, and keepRoles false clears roles
        const reassign = (fromId, toId, keepRoles) => {
            const roleFields = keepRoles ? {} : {role: "", value: ""};
            self.entries(self.entries().map((entry) => {
                if (isSection(entry) || entry.group !== fromId) return entry;
                return _.extend({}, entry, {group: toId}, roleFields);
            }));
            const settings = _.extend({}, self.coreSettings());
            _.each(settings, (value, action) => {
                if (value && value.group === fromId) {
                    settings[action] = _.extend({}, value, {group: toId}, roleFields);
                }
            });
            self.coreSettings(settings);
            self._coreDirty = true;
            self.changed();
        };

        // --- loading and saving ----------------------------------------------------------------------------------

        self._fromSettings = () => {
            // core saves system.actions from its own system_actions observable, so read and write that
            const actions = ko.toJS(self.settingsViewModel.system_actions) || [];
            return actions.filter((entry) => entry && entry.action).map(clean);
        };

        self._pluginSettings = () => self.settingsViewModel.settings.plugins.systemcommandeditor;

        self._coreFromSettings = () => {
            const plugin = self._pluginSettings();
            return (plugin && ko.toJS(plugin.core)) || {};
        };

        self._devicesFromSettings = () => {
            const plugin = self._pluginSettings();
            return (plugin && ko.toJS(plugin.devices)) || [];
        };

        // what the devices look like once saved; compared against it to see whether they changed
        const devicesSnapshot = () => JSON.stringify(self.devices().map(dropEmpty));

        self.devicesChanged = () => devicesSnapshot() !== self._devicesJson;

        self.load = () => {
            self.coreSettings(self._coreFromSettings());
            self._coreDirty = false;
            self._saved(self._fromSettings());
            self.entries(self._saved().map((entry) => _.extend({}, entry)));
            self.devices(self._devicesFromSettings().map((device) => _.extend({}, device)));
            self._devicesJson = devicesSnapshot();
            self._dirty = false;
        };

        self.changed = () => {
            self._dirty = true;
        };

        self.onBeforeBinding = () => {
            self.dialog = $("#sce-editor");
            self.dialog.on("hidden", self.editor.onHidden);
            self.deviceDialog = $("#sce-device-editor");
            self.deviceDialog.on("hidden", self.deviceEditor.onHidden);
            self.presetDialog = $("#sce-presets");
        };

        self.onSettingsShown = self.load;

        self.onSettingsBeforeSave = () => {
            if (!self.loginState.hasPermission(self.access.permissions.SETTINGS)) return;
            const plugin = self._pluginSettings();
            if (self._coreDirty) {
                const core = plugin.core;
                _.each(self.coreSettings(), (value, action) => {
                    if (core[action]) {
                        setField(core[action], "icon", value.icon || "");
                        setField(core[action], "icon_color", value.icon_color || "");
                        setField(core[action], "heading", value.heading || "");
                        setField(core[action], "group", value.group || "");
                        setField(core[action], "role", value.role || "");
                        setField(core[action], "value", value.value || "");
                        setField(core[action], "when", value.when || null);
                    }
                });
                self._coreDirty = false;
            }
            if (self.devicesChanged()) {
                setField(plugin, "devices", self.devices().map(dropEmpty));
                self._devicesJson = devicesSnapshot();
            }
            if (!self._dirty) return;
            self.settingsViewModel.system_actions(self.entries().map(clean));
            self._dirty = false;
        };

        self.onEventSettingsUpdated = () => {
            // a save from this or another browser: what is saved is what Run now runs
            self._saved(self._fromSettings());
            if (!self._coreDirty) self.coreSettings(self._coreFromSettings());
            if (!self.devicesChanged()) {
                self.devices(self._devicesFromSettings().map((device) => _.extend({}, device)));
                self._devicesJson = devicesSnapshot();
            }
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
