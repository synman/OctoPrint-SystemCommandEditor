/*
 * System Command Editor: edits OctoPrint's system.actions, the commands shown in the System menu, and the devices
 * those commands can drive.
 *
 * Commands are saved through the settings dialog like any core setting. Devices are kept in this plugin's settings.
 */
$(function () {
    const DIVIDER = "divider";
    // the markers a rename keeps: the host and OctoPrint switches draw OctoPrint's own commands
    const KEPT_MARKERS = ["host", "octoprint"];
    const PLUGIN_DIVIDER = "Plugin devices";
    const ACTION_PATTERN = /^[a-z0-9_-]+$/;
    const FLAGS = ["async", "fresh_credentials", "ignore", "icon_invert"];
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

    // an entry's text as the menu's confirmation shows it: its shell command, else its run text
    const textOf = (command, run) => command || (run ? window.SceModel.describeRun(run) : "");

    // an entry's run text, as the menu's confirmation and the row show it: its command or run, else "Link: <url>"
    const runTextOf = (entry) => textOf(entry.command, entry.run) || window.SceModel.linkText(entry);

    // The hidden key of a command or control: its name with every run of other characters turned into _, then _2, _3...
    // until no other entry or control has it. An unchanged name keeps the key it has, so renaming is what changes it.
    const keyFor = (name, original, fallback, taken) => {
        if (original !== undefined && original.name === name) return original.key;
        const base = slugify(name) || fallback;
        let candidate = base;
        let counter = 2;
        while (candidate === DIVIDER || taken(candidate)) {
            candidate = base + "_" + counter++;
        }
        return candidate;
    };

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
        choice: () => gettext("Choice"),
        run: () => gettext("Run")
    };
    const TYPE_LABELS = {
        switch: () => gettext("Switch"),
        selector: () => gettext("Selector"),
        action: () => gettext("Action"),
        readout: () => gettext("Readout"),
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

        // live sentence under the editor; the wording lives in the pure model (systemcommandeditor_menu.js)
        self.summary = ko.pureComputed(() => {
            if (!self.enabled()) return "";
            return window.SceModel.conditionSummary(self.mode(), self.states(), self.effect(), gettext);
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

    // the words typed in a free-text box: split at commas, trimmed, blanks and repeats dropped
    function parseWords(text) {
        const words = [];
        String(text || "").split(",").map((w) => w.trim()).forEach((w) => {
            if (w && !words.includes(w)) words.push(w);
        });
        return words;
    }

    // "Available when <control> is:" on a command in a control: limits it to states of the control it is grouped under.
    // attach(device) asks the states call for the control's words; a control with neither a read nor a status has none.
    function ControlCondition() {
        const self = this;

        self.visible = ko.observable(false);
        self.name = ko.observable("");
        self.enabled = ko.observable(false);
        self.mode = ko.observable("only");
        self.effect = ko.observable("disable");
        self.choices = ko.observableArray([]);
        self.picked = ko.observableArray([]);
        self.typed = ko.observable("");
        self.loading = ko.observable(false);
        self._seq = 0;

        // one checkbox per word when the control lists its states; otherwise a text box takes the words
        self.hasChoices = ko.pureComputed(() => self.choices().length > 0);
        self.words = ko.pureComputed(() => {
            if (!self.hasChoices()) return parseWords(self.typed());
            const known = self.choices().filter((w) => self.picked().includes(w));
            return known.concat(self.picked().filter((w) => !self.choices().includes(w)));
        });

        self.error = ko.pureComputed(() => {
            if (self.visible() && self.enabled() && !self.words().length) return gettext("Pick at least one state.");
            return "";
        });
        self.enableLabel = ko.pureComputed(() => gettext("Limit to") + " " + self.name() + " " + gettext("states"));
        self.availableLabel = ko.pureComputed(() => gettext("Available when") + " " + self.name() + " " + gettext("is:"));

        self.clear = () => {
            self.enabled(false);
            self.mode("only");
            self.effect("disable");
            self.picked([]);
            self.typed("");
        };

        self.load = (when) => {
            self.enabled(!!when);
            self.mode(when && when.mode === "never" ? "never" : "only");
            self.effect(when && when.effect === "hide" ? "hide" : "disable");
            const saved = when && Array.isArray(when.states) ? when.states.slice() : [];
            self.picked(saved);
            self.typed(saved.join(", "));
        };

        self.toWhen = () => {
            if (!self.visible() || !self.enabled()) return null;
            return {mode: self.mode(), states: self.words(), effect: self.effect()};
        };

        // a new control clears the condition; a late states answer for an earlier control is dropped
        self.attach = (device) => {
            const seq = ++self._seq;
            self.clear();
            self.choices([]);
            const spec = device && (device.read ? {read: device.read} : (device.status ? {status: device.status} : null));
            self.name(device ? device.name || device.id : "");
            self.visible(!!spec);
            self.loading(!!spec);
            if (!spec) return;
            OctoPrint.simpleApiCommand("systemcommandeditor", "states", spec)
                .done((response) => {
                    if (seq === self._seq) self.choices((response && response.states) || []);
                })
                .always(() => {
                    if (seq === self._seq) self.loading(false);
                });
        };
    }

    function SystemCommandEditorEditor(owner) {
        const self = this;

        self._deferred = undefined;
        // observable, so validation re-runs when the dialog opens on another entry with the same identifier
        self._original = ko.observable(undefined);

        self.isSection = ko.observable(false);
        // one of OctoPrint's own entries: only its icon, heading and device can change
        self.isCore = ko.observable(false);
        self.editing = ko.observable(false);
        self.heading = ko.observable("");

        self.name = ko.observable("");
        self.action = ko.observable("");
        self.command = ko.observable("");
        // a custom command is a shell command or a link to a page; the kind decides which one the entry stores
        self.kind = ko.observable("shell");
        self.isLinkKind = ko.pureComputed(() => self.kind() === "link");
        self.url = ko.observable("");
        self.canChooseKind = ko.pureComputed(() => !self.isSection() && !self.isCore() && !self.runCall());
        self.icon = ko.observable("");
        self.iconColor = ko.observable("");
        self.iconColorPick = colorPick(self.iconColor);
        self.iconInvert = ko.observable(false);
        self.iconSuggest = iconSuggest(self.icon);
        self.useConfirm = ko.observable(false);
        self.confirmMessage = ko.observable("");
        self.async = ko.observable(false);
        self.freshCredentials = ko.observable(false);
        self.ignore = ko.observable(false);
        // a plugin command's run call, kept as saved; such a command has no shell command
        self.runCall = ko.observable(undefined);
        self.runText = ko.pureComputed(() => window.SceModel.describeRun(self.runCall()));
        // a G-code run is editable: one line per G-code line; any other run kind stays read-only
        self.isGcode = ko.pureComputed(() => {
            const run = self.runCall();
            return !!run && Array.isArray(run.gcode);
        });
        self.isRunOnly = ko.pureComputed(() => !!self.runCall() && !self.isGcode());
        // the confirmation preview shows what the menu will show: a run's text, a G-code run from the box as typed now
        self.previewText = ko.pureComputed(() => {
            if (self.isLinkKind()) return window.SceModel.linkText({url: self.url().trim()});
            if (self.isGcode()) return textOf("", _.extend({}, self.runCall(), {gcode: gcodeLines()}));
            return textOf(self.runCall() ? "" : self.command(), self.runCall());
        });
        self.gcodeText = ko.observable("");
        const gcodeLines = () => self.gcodeText().split("\n").map((line) => line.trim()).filter((line) => line);

        self.device = ko.observable("");
        self.role = ko.observable("");
        self.value = ko.observable("");
        self.condition = new Condition();
        self.control = new ControlCondition();
        // looked up from the new id: selectedDevice is a computed that is notified after this subscriber runs, so reading it here would give the previous control
        self.device.subscribe((id) => self.control.attach(_.find(owner.devices(), (d) => d.id === id)));

        self.title = ko.pureComputed(() => {
            if (self.isCore()) return gettext("Edit built-in command");
            if (self.isSection()) {
                return self.editing() ? gettext("Edit section") : gettext("Add section");
            }
            return self.editing() ? gettext("Edit command") : gettext("Add command");
        });

        // a command's key is hidden: it comes from the name, and a rename makes a new one
        self.key = () => {
            const original = self._original();
            return keyFor(self.name().trim(), original && {name: (original.name || "").trim(), key: original.action},
                "command", (key) => owner.actionInUse(key, original));
        };

        self.nameError = ko.pureComputed(() => {
            if (self.isSection() || self.isCore()) return "";
            return self.name().trim() ? "" : gettext("A name is required.");
        });
        self.commandError = ko.pureComputed(() => {
            if (self.isSection() || self.isCore()) return "";
            if (self.isLinkKind()) return "";
            if (self.isGcode()) return gcodeLines().length ? "" : gettext("Enter at least one G-code line.");
            if (self.runCall()) return "";
            return self.command().trim() ? "" : gettext("A command is required.");
        });
        self.urlError = ko.pureComputed(() => {
            if (!self.isLinkKind()) return "";
            return self.url().trim() ? "" : gettext("A URL is required for a link.");
        });

        // --- device assignment ------------------------------------------------------------------------------------

        self.deviceChoices = ko.pureComputed(() => {
            const choices = [{id: "", name: gettext("None")}];
            // a link inside a control is a run button, which only an action control draws
            const types = self.isLinkKind() ? ["action"] : ["switch", "selector", "action"];
            return choices.concat(owner.devices().filter((d) => types.includes(d.type)));
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
            if (d.type === "action") return [choose, {value: "run", label: ROLE_LABELS.run()}];
            return [choose, {value: "choice", label: ROLE_LABELS.choice()}];
        });
        self.roleError = ko.pureComputed(() => {
            if (!self.device() || self.isSection()) return "";
            const known = self.roleChoices().some((choice) => choice.value && choice.value === self.role());
            return known ? "" : gettext("Choose a role for this control.");
        });
        self.valueError = ko.pureComputed(() => {
            if (!self.device() || self.role() !== "choice") return "";
            return self.value().trim() ? "" : gettext("A value is required for a choice.");
        });
        self.deviceError = ko.pureComputed(() => {
            const id = self.device();
            if (!id || self.isSection()) return "";
            if (!self.selectedDevice()) return gettext("Choose a control that exists.");
            const others = owner.membersOf(id).filter((m) => {
                if (m.core) return !(self.isCore() && m.key === self.action());
                return !(!self.isCore() && m.key === self._original());
            });
            if (others.some((m) => m.core !== self.isCore())) {
                return self.isCore()
                    ? gettext("This control already has custom commands, so a built-in command cannot use it.")
                    : gettext("A built-in command already uses this control, so a custom command cannot use it.");
            }
            if (self.selectedDevice().type === "switch" && self.role() &&
                others.some((m) => m.role === self.role())) {
                return gettext("This switch already has that role on another entry.");
            }
            return "";
        });

        self.valid = ko.pureComputed(
            () => !self.nameError() && !self.commandError() && !self.urlError() && !self.deviceError() &&
                !self.roleError() && !self.valueError() && !self.condition.error() && !self.control.error()
        );

        // device, role, value and condition as the entry stores them
        const deviceFields = () => {
            const device = self.isSection() ? "" : self.device();
            return {
                group: device,
                role: device ? self.role() : "",
                value: device && self.role() === "choice" ? self.value().trim() : "",
                when: self.isSection() ? null : self.condition.toWhen(),
                // the template never offers the control block on a built-in command, so toWhen stays null there
                when_control: self.isSection() ? null : self.control.toWhen()
            };
        };

        self.toEntry = () => {
            if (self.isCore()) {
                return _.extend(
                    {action: self.action(), name: self.name(), icon: self.icon().trim(), icon_color: self.iconColor(), icon_invert: self.iconInvert(),
                     heading: self.heading().trim()},
                    deviceFields()
                );
            }
            if (self.isSection()) {
                return clean(_.extend({}, self._original() || {}, {action: DIVIDER, name: self.name().trim()}));
            }
            const entry = _.extend({}, self._original() || {}, {
                action: self.key(),
                name: self.name().trim(),
                icon: self.icon().trim(),
                icon_color: self.iconColor(),
                icon_invert: self.iconInvert(),
                confirm: self.useConfirm() ? self.confirmMessage().trim() || gettext("Are you sure?") : "",
                async: self.async(),
                fresh_credentials: self.freshCredentials(),
                ignore: self.ignore()
            }, deviceFields());
            if (self.isLinkKind()) {
                // a link stores its url and no command or run
                entry.url = self.url().trim();
                delete entry.command;
                delete entry.run;
                return clean(entry);
            }
            delete entry.url;
            if (self.isGcode()) {
                // the other keys of the run are kept; the lines are trimmed and blank ones dropped
                entry.run = _.extend({}, self.runCall(), {gcode: gcodeLines()});
            } else if (self.runCall()) {
                entry.run = self.runCall();
            } else {
                entry.command = self.command().trim();
            }
            return clean(entry);
        };

        // Only a saved, unchanged command can be run: the server runs what is saved, not what is in this dialog
        self.canRun = ko.pureComputed(() => {
            if (self.isSection() || self.isCore() || self.isLinkKind() || !self.editing() || !self.valid()) return false;
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
            self.isSection(!!section);
            self.isCore(!!core);
            self.editing(!_.isEmpty(entry));
            self.heading(entry.heading || "");

            self.name(entry.name || "");
            self.action(entry.action && entry.action !== DIVIDER ? entry.action : "");
            self.command(entry.command || "");
            self.kind(entry.url ? "link" : "shell");
            self.url(entry.url || "");
            self.runCall(entry.run);
            self.gcodeText(entry.run && Array.isArray(entry.run.gcode) ? entry.run.gcode.join("\n") : "");
            self.icon(entry.icon || "");
            self.iconColor(entry.icon_color || "");
            self.iconInvert(!!entry.icon_invert);
            self.useConfirm(!!entry.confirm);
            self.confirmMessage(entry.confirm || "");
            self.async(!!entry.async);
            self.freshCredentials(!!entry.fresh_credentials);
            self.ignore(!!entry.ignore);

            self.device(!section && entry.group ? entry.group : "");
            self.role(!section && entry.role ? entry.role : "");
            self.value(!section && entry.value ? String(entry.value) : "");
            self.condition.load(section ? null : entry.when);
            self.control.load(section || core ? null : entry.when_control);

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

        // closes without saving this dialog's edits; the owner then adds a copy of the saved record
        self.canDuplicate = ko.pureComputed(() => self.editing() && !self.isSection() && !self.isCore());
        self.duplicate = () => {
            const original = self._original();
            owner.dialog.modal("hide");
            if (original) owner.duplicate(original);
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

        self.editing = ko.observable(false);
        self.type = ko.observable("switch");
        self.name = ko.observable("");
        self.icon = ko.observable("");
        self.iconColor = ko.observable("");
        self.iconColorPick = colorPick(self.iconColor);
        self.iconInvert = ko.observable(false);
        self.iconSuggest = iconSuggest(self.icon);
        self.status = ko.observable("");
        self.onPattern = ko.observable("");
        self.offPattern = ko.observable("");

        self.typeChoices = [
            {value: "switch", label: TYPE_LABELS.switch()},
            {value: "selector", label: TYPE_LABELS.selector()},
            {value: "action", label: TYPE_LABELS.action()},
            {value: "readout", label: TYPE_LABELS.readout()}
        ];
        self.typeLabel = (type) => (TYPE_LABELS[type] ? TYPE_LABELS[type]() : "");

        self.isSwitch = ko.pureComputed(() => self.type() === "switch");

        // a control that reads through another plugin or MQTT keeps its read as saved; its status is not edited
        self.readCall = ko.observable(undefined);
        self.readText = ko.pureComputed(() => {
            const read = self.readCall();
            if (!read) return "";
            return read.mqtt ? "MQTT: " + read.mqtt : window.SceModel.describeRun(read);
        });
        self.hasRead = ko.pureComputed(() => !!self.readCall());

        self.title = ko.pureComputed(() => (self.editing() ? gettext("Edit control") : gettext("Add control")));

        // a control's key is hidden: it comes from the name, and a rename makes a new one; it must not match another
        // control or any entry but this control's own readout placements
        self.key = () => {
            const original = self._original();
            return keyFor(self.name().trim(), original && {name: (original.name || "").trim(), key: original.id},
                "control", (key) => owner.devices().some((d) => d !== original && d.id === key) ||
                    owner.entries().some((e) => !isSection(e) && e.action === key && !(original && e.device === original.id)));
        };

        self.nameError = ko.pureComputed(() => (self.name().trim() ? "" : gettext("A name is required.")));
        self.statusError = ko.pureComputed(() => {
            if (self.type() !== "readout" || self.readCall()) return "";
            return self.status().trim() ? "" : gettext("A status command is required for a readout.");
        });
        self.valid = ko.pureComputed(() => !self.nameError() && !self.statusError());

        self.toDevice = () => {
            const type = self.type();
            const device = {
                id: self.key(),
                type: type,
                name: self.name().trim(),
                icon: self.icon().trim(),
                icon_color: self.iconColor(),
                icon_invert: self.iconInvert() || undefined
            };
            if (self.readCall()) {
                device.read = self.readCall();
            } else {
                device.status = self.status().trim();
            }
            if (type === "switch") {
                device.on_pattern = self.onPattern().trim();
                device.off_pattern = self.offPattern().trim();
            }
            const original = self._original();
            if (original && original.preset) device.preset = original.preset;
            if (original && original.live !== undefined) device.live = original.live;
            return dropEmpty(device);
        };

        self.show = (device) => {
            if (self._deferred) self._deferred.reject();
            self._deferred = $.Deferred();

            device = device || {};
            const editing = !_.isEmpty(device);
            self._original(editing ? device : undefined);
            self.editing(editing);

            self.name(device.name || "");
            self.type(device.type || "switch");
            self.icon(device.icon || "");
            self.iconColor(device.icon_color || "");
            self.iconInvert(!!device.icon_invert);
            self.status(device.status || "");
            self.readCall(device.read);
            self.onPattern(device.on_pattern || "");
            self.offPattern(device.off_pattern || "");

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

        self.canDuplicate = ko.pureComputed(() => self.editing());
        self.duplicate = () => {
            const original = self._original();
            owner.deviceDialog.modal("hide");
            if (original) owner.duplicateDevice(original);
        };

        self.onHidden = () => {
            if (self._deferred) {
                self._deferred.reject();
                self._deferred = undefined;
            }
        };
    }

    // "Add from presets": asks the server which switches, readouts and maintenance commands this system offers. A
    // ticked switch joins OctoPrint's own commands by role; ticked readouts become controls; ticked maintenance
    // commands become entries under a Maintenance divider.
    function PresetPicker(owner) {
        const self = this;

        // A preset is present when a control or a command carries its marker, wherever it sits. The marker is the
        // preset's id (readout, switch) or action (maintenance), so a renamed or moved record is still found.
        const presentBy = (presetId) =>
            owner.devices().some((device) => device.preset === presetId) ||
            owner.entries().some((entry) => !isSection(entry) && entry.preset === presetId);

        // the first of base, base_2, base_3... that taken() does not refuse
        const freeAmong = (base, taken) => {
            let candidate = base;
            let counter = 2;
            while (candidate === DIVIDER || taken(candidate)) {
                candidate = base + "_" + counter++;
            }
            return candidate;
        };
        // a control's identifier is free of every control and every command or placement key
        const controlTaken = (key) =>
            owner.devices().some((device) => device.id === key) ||
            owner.entries().some((entry) => !isSection(entry) && entry.action === key);

        // a system.actions entry for a maintenance command, built the way the server's entry_of builds it; its
        // device is the action control that runs it (group and role "run"), and it carries the preset's marker
        const entryOf = (preset, deviceId, action) => {
            const entry = {action: action, name: preset.name, command: preset.command, icon: preset.icon};
            if (preset.confirm) entry.confirm = preset.confirm;
            if (preset.async) entry.async = true;
            entry.group = deviceId;
            entry.role = "run";
            entry.preset = preset.action;
            return entry;
        };

        self.loading = ko.observable(false);
        self.failed = ko.observable(false);
        self.switches = ko.observableArray([]);
        self.readouts = ko.observableArray([]);
        self.maintenance = ko.observableArray([]);
        // one group per plugin: {name, rows}; a group has a row per preset the plugin offers
        self.plugins = ko.observableArray([]);
        // a row differs when its tick no longer matches whether it is present
        const differs = (row) => row.checked() !== row.added;
        self.canApply = ko.pureComputed(() =>
            self.switches().some(differs) || self.readouts().some(differs) || self.maintenance().some(differs) ||
            self.plugins().some((group) => group.rows.some(differs)));

        // the picker is a checklist of what is present: present rows start ticked, every other row starts unticked
        const rowOf = (item, added) => ({
            item: item,
            added: added,
            checked: ko.observable(added),
            note: item.note || ""
        });

        self.open = () => {
            self.switches([]);
            self.readouts([]);
            self.maintenance([]);
            self.failed(false);
            self.plugins([]);
            self.loading(true);
            owner.presetDialog.modal("show");
            OctoPrint.simpleApiCommand("systemcommandeditor", "presets", {})
                .done((response) => {
                    self.switches(((response && response.switches) || []).map((preset) =>
                        rowOf(preset, presentBy(preset.id))
                    ));
                    self.readouts(((response && response.presets) || []).map((preset) =>
                        rowOf(preset, presentBy(preset.id))
                    ));
                    self.maintenance(((response && response.maintenance) || []).map((preset) =>
                        rowOf(preset, presentBy(preset.action))
                    ));
                    self.plugins(((response && response.plugins) || []).map((group) => ({
                        name: group.name || group.plugin || "",
                        rows: (group.presets || []).map(pluginRow)
                    })));
                })
                .fail(() => self.failed(true))
                .always(() => self.loading(false));
        };

        // an undetected row stays pickable; it shows its note, or the generic line when it has none
        self.label = (row) => {
            if (row.item.available) return "";
            return row.note || gettext("Not detected on this system");
        };

        // removes the named divider when the removals left it with nothing under it
        const pruneEmptyDivider = (name) => {
            const entries = owner.entries();
            const kept = entries.filter((entry, i) => !(
                entry.action === DIVIDER && entry.name === name &&
                (i + 1 === entries.length || isSection(entries[i + 1]))
            ));
            if (kept.length !== entries.length) owner.entries(kept);
        };

        // a plugin row's label: only a preset that says available === false is undetected; a harvested preset has no
        // available key, so the readout label rule (a missing key reads as not detected) must not apply to it
        const pluginLabel = (preset) => {
            if (preset.available === false) return preset.note || gettext("Not detected on this system");
            return preset.note || "";
        };
        // a plugin preset's row, labelled with its control's type
        // a command-only preset has no control: its row takes the icon of its first command
        const pluginRow = (preset) => _.extend(rowOf(preset, presentBy(preset.preset)), {
            type: preset.control && TYPE_LABELS[preset.control.type] ? TYPE_LABELS[preset.control.type]() : "",
            icon: preset.control ? preset.control.icon || "" : ((preset.commands || [])[0] || {}).icon || "",
            label: pluginLabel(preset)
        });

        // the picked entries go right after the last item of the named section; the divider is made at the end of the
        // list only when the section is missing
        const placeInSection = (name, items) => {
            const list = owner.entries().slice();
            const at = list.findIndex((e) => isSection(e) && e.name === name);
            if (at < 0) {
                list.push({action: DIVIDER, name: name});
                items.forEach((item) => list.push(item));
            } else {
                let end = at + 1;
                while (end < list.length && !isSection(list[end])) end++;
                list.splice(end, 0, ...items);
            }
            owner.entries(list);
        };

        // a plugin preset's control, then its commands in the Plugin devices section (made when missing);
        // each command keeps its run call or shell command, its role and icon, and its group is the control's key
        // a command-only preset (no control) adds standalone commands: no group, no role, and a link keeps its url
        const addPlugin = (preset) => {
            const control = preset.control;
            let deviceId = null;
            if (control) {
                deviceId = keyFor(control.name, undefined, "control", controlTaken);
                const device = {
                    id: deviceId, type: control.type, name: control.name, icon: control.icon,
                    read: control.read, preset: preset.preset
                };
                if (control.status) device.status = control.status;
                if (control.on_pattern) device.on_pattern = control.on_pattern;
                if (control.off_pattern) device.off_pattern = control.off_pattern;
                owner.devices.push(device);
            }
            const commands = preset.commands || [];
            if (!commands.length) return;
            const items = commands.map((command) => {
                const label = control ? `${preset.name} ${command.name}` : command.name;
                const action = keyFor(label, undefined, "command", (key) => owner.actionInUse(key));
                const entry = {action: action, name: command.name, preset: preset.preset};
                if (deviceId) {
                    entry.role = command.role;
                    entry.group = deviceId;
                }
                if (command.url) {
                    entry.url = command.url;
                } else if (command.command) {
                    // a shell line is the command; anything else is a run call. Never both.
                    entry.command = command.command;
                } else {
                    entry.run = command.run;
                }
                if (command.when) entry.when = command.when;
                if (command.when_control) entry.when_control = command.when_control;
                if (command.icon) entry.icon = command.icon;
                if (command.async) entry.async = true;
                if (command.confirm) entry.confirm = command.confirm;
                return entry;
            });
            placeInSection(PLUGIN_DIVIDER, items);
        };

        // every control and every command carrying the marker goes; a removed control's other members are detached,
        // so core entries are freed the same way Delete frees them
        const removePreset = (presetId) => {
            const ids = owner.devices().filter((device) => device.preset === presetId).map((device) => device.id);
            owner.devices.remove((device) => device.preset === presetId);
            owner.entries.remove((entry) => !isSection(entry) && entry.preset === presetId);
            ids.forEach((id) => owner.detachControl(id));
        };

        self.apply = () => {
            const pending = self.canApply();
            // switches go first, in the server's order, as first start puts them
            const addedSwitches = [];
            self.switches().forEach((row) => {
                const preset = row.item;
                if (row.checked() && !row.added) {
                    const id = freeAmong(preset.id, controlTaken);
                    addedSwitches.push({id: id, type: "switch", name: preset.name, icon: preset.icon, preset: preset.id});
                    owner.joinCore(id, preset.members);
                } else if (!row.checked() && row.added) {
                    removePreset(preset.id);
                }
            });
            if (addedSwitches.length) owner.devices(addedSwitches.concat(owner.devices()));
            self.readouts().forEach((row) => {
                if (row.checked() && !row.added) {
                    const preset = row.item;
                    const device = {
                        id: freeAmong(preset.id, controlTaken),
                        type: preset.type,
                        name: preset.name,
                        icon: preset.icon,
                        status: preset.status,
                        preset: preset.id
                    };
                    owner.devices.push(device);
                    owner.placeReadout(device, "Status");
                } else if (!row.checked() && row.added) {
                    removePreset(row.item.id);
                }
            });
            const removed = self.maintenance().filter((row) => row.added && !row.checked());
            removed.forEach((row) => removePreset(row.item.action));
            if (removed.length) pruneEmptyDivider("Maintenance");
            const picked = [];
            self.maintenance().filter((row) => row.checked() && !row.added).forEach((row) => {
                const preset = row.item;
                const id = freeAmong(preset.action, controlTaken);
                const action = freeAmong(preset.action, (key) => owner.actionInUse(key));
                const device = {id: id, type: "action", name: preset.name, icon: preset.icon, preset: preset.action};
                if (preset.status) device.status = preset.status;
                owner.devices.push(device);
                picked.push(entryOf(preset, id, action));
            });
            if (picked.length) placeInSection("Maintenance", picked);
            const pluginRows = self.plugins().reduce((all, group) => all.concat(group.rows), []);
            const pluginRemoved = pluginRows.filter((row) => row.added && !row.checked());
            pluginRemoved.forEach((row) => removePreset(row.item.preset));
            if (pluginRemoved.length) pruneEmptyDivider(PLUGIN_DIVIDER);
            pluginRows.filter((row) => row.checked() && !row.added).forEach((row) => addPlugin(row.item));
            if (pending) owner.changed();
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

        // the Settings busy-icon picker: the allowed list lives in SceModel; the saved value is one full class string
        self.busyIcons = window.SceModel.BUSY_ICONS;
        self.busyIcon = ko.pureComputed(() => ko.unwrap(self._pluginSettings().busy_icon));
        self.pickBusyIcon = (cls) => {
            const setting = self._pluginSettings().busy_icon;
            if (ko.isObservable(setting)) setting(cls);
        };

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
                _.extend({action: item.action, name: item.name, icon: "", icon_color: "", icon_invert: false, heading: ""}, settings[item.action] || {})
            );
        });
        self.editCore = (entry) => {
            self.editor.show(entry, false, true).done((edited) => {
                const settings = _.extend({}, self.coreSettings());
                settings[edited.action] = {
                    icon: edited.icon,
                    icon_color: edited.icon_color,
                    icon_invert: !!edited.icon_invert,
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

        // --- readout placements: a custom-list entry that names a readout by device; it runs nothing -------------

        self.isPlacement = (entry) => !isSection(entry) && !!entry.device;
        self.isCommand = (entry) => !isSection(entry) && !entry.device && !self.isControl(entry);
        self.deviceOf = (entry) => _.find(self.devices(), (d) => d.id === entry.device);
        self.isPlaced = (device) => _.some(self.entries(), (entry) => entry.device === device.id);

        // a placement's action key: its readout id if free, else id_2, id_3...
        const placementKey = (base) => {
            let candidate = base;
            let counter = 2;
            while (candidate === DIVIDER || self.actionInUse(candidate)) {
                candidate = base + "_" + counter++;
            }
            return candidate;
        };

        // adds a readout's placement at the end of the list, or (section given) right after the last entry under
        // that divider, creating the divider at the end when it is missing
        self.placeReadout = (device, section) => {
            const entry = {action: placementKey(device.id), name: device.name, device: device.id};
            if (device.preset) entry.preset = device.preset;
            const list = self.entries().slice();
            if (!section) {
                list.push(entry);
            } else {
                const divider = list.findIndex((e) => isSection(e) && e.name === section);
                if (divider < 0) {
                    list.push({action: DIVIDER, name: section}, entry);
                } else {
                    let end = divider;
                    while (end + 1 < list.length && !isSection(list[end + 1])) end++;
                    list.splice(end + 1, 0, entry);
                }
            }
            self.entries(list);
            self.changed();
        };
        self.placeInMenu = (device) => self.placeReadout(device, "");

        self.editPlacement = (entry) => {
            const device = self.deviceOf(entry);
            if (device) self.editDevice(device);
        };
        self.removePlacement = (entry) => {
            self.entries.remove(entry);
            self.changed();
        };

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

        // --- control rows: a control's members draw as one row, where its first member sits -----------------------

        // the control (device) an entry drives, when it names one that exists; sections and readouts drive none
        self.controlOf = (entry) => {
            if (!entry || isSection(entry) || !entry.group) return undefined;
            return _.find(self.devices(), (d) => d.id === entry.group);
        };
        self.isControl = (entry) => !!self.controlOf(entry);
        const controlIdOf = (entry) => {
            const device = self.controlOf(entry);
            return device ? device.id : "";
        };

        // the buttons of one control's row: every member, in menu order, titled with its name. A member the menu
        // does not draw (no role, or a second On or Off) still gets a button, so every command stays reachable.
        const controlButtons = (deviceId, table, isCore) => {
            const m = isCore ? window.SceModel.members(deviceId, {}, table) : window.SceModel.members(deviceId, table, {});
            const buttons = window.SceModel.rowButtons(m);
            const shown = buttons.map((b) => b.action);
            _.each(table, (entry, action) => {
                if (entry && entry.group === deviceId && shown.indexOf(action) < 0) {
                    buttons.push({kind: "plain", action: action, icon: entry.icon || "fa-solid fa-ellipsis"});
                }
            });
            return buttons.map((b) => _.extend({}, b, {name: (table[b.action] || {}).name || b.action}));
        };

        // the custom list: one row per move unit (see SceModel.units); a control's row is its first member
        self.customRows = ko.pureComputed(() => window.SceModel.units(self.entries(), controlIdOf).map((unit) => unit[0]));
        self.buttonsOf = (entry) => {
            const device = self.controlOf(entry);
            if (!device) return [];
            const table = {};
            self.entries().forEach((e) => {
                if (!isSection(e)) table[e.action] = e;
            });
            return controlButtons(device.id, table, false);
        };
        self.editMemberAction = (button) => {
            const entry = _.find(self.entries(), (e) => !isSection(e) && e.action === button.action);
            if (entry) self.edit(entry);
        };

        // the built-in list: a row per ungrouped command, and one row per control drawn at its first member
        self.coreRows = ko.pureComputed(() => {
            const entries = self.coreEntries();
            const table = {};
            entries.forEach((e) => {
                table[e.action] = e;
            });
            const drawn = {};
            const rows = [];
            entries.forEach((entry) => {
                const device = self.controlOf(entry);
                if (!device) {
                    rows.push(_.extend({kind: "command"}, entry));
                    return;
                }
                if (drawn[device.id]) return;
                drawn[device.id] = true;
                rows.push({kind: "control", device: device, heading: entry.heading || "",
                    buttons: controlButtons(device.id, table, true)});
            });
            return rows;
        });
        self.editCoreAction = (button) => {
            const entry = _.find(self.coreEntries(), (e) => e.action === button.action);
            if (entry) self.editCore(entry);
        };

        self.typeLabel = (type) => self.deviceEditor.typeLabel(type);

        self.addDevice = () => {
            self.deviceEditor.show(null).done((device) => {
                self.devices.push(device);
                if (device.type === "readout") self.placeReadout(device, "");
            });
        };

        self.editDevice = (device) => {
            self.deviceEditor.show(device).done((edited) => {
                const index = self.devices.indexOf(device);
                if (index < 0) return;
                // a renamed control makes its preset the operator's own; a host or OctoPrint switch keeps its link
                const renamed = !!device.preset && (device.name || "").trim() !== edited.name.trim() &&
                    !KEPT_MARKERS.includes(device.preset);
                if (renamed) unlinkPreset(device.preset);
                const next = renamed ? _.omit(edited, "preset") : edited;
                self.devices.splice(index, 1, next);
                if (next.type === "readout") {
                    self.entries(self.entries().map((entry) => (entry.device === device.id
                        ? _.extend({}, entry, {name: next.name, device: next.id}) : entry)));
                    self.changed();
                }
                if (next.id !== device.id || next.type !== device.type) {
                    // members follow a new identifier; a changed type clears their roles, which no longer fit
                    reassign(device.id, next.id, next.type === device.type);
                }
                if (renamed) self.changed();
            });
        };

        // the presets picker's switches: join OctoPrint's own commands to a switch by role, or detach every member
        self.joinCore = (deviceId, members) => {
            const settings = _.extend({}, self.coreSettings());
            _.each(members, (role, action) => {
                settings[action] = _.extend({}, settings[action], {group: deviceId, role: role, value: ""});
            });
            self.coreSettings(settings);
            self._coreDirty = true;
        };
        self.detachControl = (deviceId) => reassign(deviceId, "", false);

        self.removeDevice = (device) => {
            showConfirmationDialog({
                message: _.sprintf(gettext('This removes the control "%(name)s" and its role on each command.'), {
                    name: _.escape(device.name)
                }),
                onproceed: () => {
                    self.devices.remove(device);
                    if (device.type === "readout") self.entries.remove((entry) => entry.device === device.id);
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
            // a doubled key in the saved config is renamed here, so every command and control has its own key
            const savedEntries = self._saved().map((entry) => _.extend({}, entry));
            const entries = window.SceModel.dedupeKeys(savedEntries, "action");
            self.entries(entries.map((entry) => _.extend({}, entry)));
            const loadedDevices = self._devicesFromSettings().map((device) => _.extend({}, device));
            const devices = window.SceModel.dedupeKeys(loadedDevices, "id");
            self.devices(devices.map((device) => _.extend({}, device)));
            self._devicesJson = JSON.stringify(loadedDevices.map(dropEmpty));
            self._dirty = JSON.stringify(entries) !== JSON.stringify(savedEntries);
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
                        setField(core[action], "icon_invert", !!value.icon_invert);
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
            // a control's members are saved together, right after its first member's place
            const current = self.entries();
            const kept = flatten(unitsNow());
            if (kept.some((entry, i) => entry !== current[i])) {
                self.entries(kept);
                self._dirty = true;
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
            // a plugin command runs through this plugin's run command, which runs the saved entry
            if (entry.run) {
                OctoPrint.simpleApiCommand("systemcommandeditor", "run", {action: entry.action})
                    .done((response) => {
                        if (response && response.ok) {
                            // PNotify renders text as HTML; the output came from another plugin, so it is escaped
                            new PNotify({title: gettext("Done"),
                                text: _.escape(response.output || gettext("The command finished.")), type: "success"});
                        } else {
                            new PNotify({title: gettext("The command failed"),
                                text: _.escape((response && response.error) || gettext("The command failed.")), type: "error"});
                        }
                    })
                    .fail(() => {
                        new PNotify({title: gettext("The command failed"), text: gettext("The command failed."), type: "error"});
                    });
                return;
            }
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

        // drops a preset marker from every control and entry carrying it: the records are the operator's own from then on
        const unlinkPreset = (marker) => {
            self.devices(self.devices().map((d) => (d.preset === marker ? _.omit(d, "preset") : d)));
            self.entries(self.entries().map((e) => (e.preset === marker ? _.omit(e, "preset") : e)));
        };

        // saving an edit: a renamed command that is linked to a preset unlinks it, and a command whose control was
        // cleared drops its own marker only
        const replace = (oldEntry, newEntry) => {
            const index = self.entries.indexOf(oldEntry);
            if (index < 0) return;
            const renamed = !!oldEntry.preset && (oldEntry.name || "").trim() !== (newEntry.name || "").trim();
            const detached = !!oldEntry.preset && !!oldEntry.group && !newEntry.group;
            if (renamed) unlinkPreset(oldEntry.preset);
            const next = renamed || detached ? _.omit(newEntry, "preset") : newEntry;
            self.entries.splice(index, 1, next);
            self.changed();
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

        // --- copies --------------------------------------------------------------------------------------------------

        // the context of one copy: a fresh hidden key for each copy, unique among every entry and control and among the
        // keys this copy has taken so far, and the names the copy must not repeat
        const copyContext = () => {
            // used: every entry and control key taken; entryKeys: the entry keys only, which a readout's placement must avoid
            const used = new Set();
            const entryKeys = new Set();
            self.entries().forEach((e) => { if (!isSection(e)) { used.add(e.action); entryKeys.add(e.action); } });
            self.devices().forEach((d) => used.add(d.id));
            return {
                fresh: (name, fallback) => {
                    const key = keyFor(name, undefined, fallback, (k) => used.has(k));
                    used.add(key);
                    return key;
                },
                // a command's or member's key: fresh, and recorded as an entry key
                freshEntry: (name, fallback) => {
                    const key = keyFor(name, undefined, fallback, (k) => used.has(k));
                    used.add(key);
                    entryKeys.add(key);
                    return key;
                },
                // a readout's placement key, as placementKey gives it: the base when no entry or divider has it, else
                // base_2, base_3...; a key an earlier part of this copy took counts as taken
                placementKey: (base) => {
                    let candidate = base;
                    let counter = 2;
                    while (candidate === DIVIDER || entryKeys.has(candidate)) candidate = base + "_" + counter++;
                    entryKeys.add(candidate);
                    used.add(candidate);
                    return candidate;
                },
                commandNames: self.entries().filter((e) => !isSection(e)).map((e) => e.name),
                deviceNames: self.devices().map((d) => d.name)
            };
        };
        const customMembers = (controlId) => self.entries().filter((e) => !isSection(e) && e.group === controlId);

        // a command's copy: "Copy of" name and a fresh key, no preset; a run copy keeps its control and role, any other
        // role drops group, role and value
        const copyCommand = (entry, ctx) => {
            let copy = _.omit(entry, "preset");
            if (copy.role !== "run") copy = _.omit(copy, "group", "role", "value");
            copy.name = window.SceModel.copyName(entry.name, ctx.commandNames);
            ctx.commandNames.push(copy.name);
            copy.action = ctx.freshEntry(copy.name, "command");
            return copy;
        };
        // a control member's copy: its own name and a fresh key, joined to the copied control, no preset
        const copyMember = (entry, controlId, ctx) => {
            const copy = _.extend(_.omit(entry, "preset"), {group: controlId});
            copy.action = ctx.freshEntry(copy.name, "command");
            return copy;
        };
        // a control's copy: "Copy of" name and a fresh id, no preset
        const copyControl = (device, ctx) => {
            const copy = _.omit(device, "preset");
            copy.name = window.SceModel.copyName(device.name, ctx.deviceNames);
            ctx.deviceNames.push(copy.name);
            copy.id = ctx.fresh(copy.name, "control");
            return copy;
        };
        // the copies one list unit makes, for a section copy: a readout with its placement, a control with its custom
        // members, or a command
        const copyUnit = (unit, ctx) => {
            const first = unit[0];
            if (self.isPlacement(first)) {
                const device = self.deviceOf(first);
                if (!device) return {devices: [], entries: [_.extend(_.omit(first, "preset"), {action: ctx.placementKey(first.name || first.action)})]};
                const copy = copyControl(device, ctx);
                return {devices: [copy], entries: [{action: ctx.placementKey(copy.id), name: copy.name, device: copy.id}]};
            }
            const control = self.controlOf(first);
            if (control) {
                const copy = copyControl(control, ctx);
                return {devices: [copy], entries: customMembers(control.id).map((m) => copyMember(m, copy.id, ctx))};
            }
            return {devices: [], entries: [copyCommand(first, ctx)]};
        };
        // the last index of the unit that holds the entry
        const unitEndOf = (entry) => {
            const unit = unitsNow().find((u) => u.indexOf(entry) >= 0) || [entry];
            return self.entries().indexOf(unit[unit.length - 1]);
        };

        // a command's copy: a run copy stays on its control, right after the original; any other copy stands alone after
        // the original's unit, so a control's members stay one unit
        self.duplicate = (entry) => {
            const ctx = copyContext();
            const copy = copyCommand(entry, ctx);
            const list = self.entries().slice();
            const at = list.indexOf(entry);
            const end = copy.group ? at : unitEndOf(entry);
            list.splice(end < 0 ? list.length : end + 1, 0, copy);
            self.entries(list);
            self.changed();
        };

        // a control's copy: the control alone with no preset, its custom members copied after the original's unit, and a
        // readout's copy gets its own placement right after the original's, or at the end when the original has none
        self.duplicateDevice = (device) => {
            const ctx = copyContext();
            const copy = copyControl(device, ctx);
            const members = customMembers(device.id).map((m) => copyMember(m, copy.id, ctx));
            const list = self.entries().slice();
            if (members.length) {
                const unit = unitsNow().find((u) => controlIdOf(u[0]) === device.id);
                list.splice(list.indexOf(unit[unit.length - 1]) + 1, 0, ...members);
            }
            if (copy.type === "readout") {
                const placed = list.findIndex((e) => self.isPlacement(e) && e.device === device.id);
                list.splice(placed < 0 ? list.length : placed + 1, 0,
                    {action: ctx.placementKey(copy.id), name: copy.name, device: copy.id});
            }
            self.devices.push(copy);
            self.entries(list);
            self.changed();
        };

        // a section's copy: a "Copy of" divider, then a copy of each item as that item's own Duplicate makes it, all placed
        // right after the original section's last item
        self.duplicateSection = (heading) => {
            const bs = window.SceModel.blocks(self.entries(), controlIdOf);
            const block = bs.find((b) => b[0].length === 1 && b[0][0] === heading);
            if (!block) return;
            const ctx = copyContext();
            const copies = [{action: DIVIDER, name: window.SceModel.copyName(heading.name || "", self.dividerNames())}];
            const newDevices = [];
            block.slice(1).forEach((unit) => {
                const made = copyUnit(unit, ctx);
                made.devices.forEach((d) => newDevices.push(d));
                made.entries.forEach((e) => copies.push(e));
            });
            const list = self.entries().slice();
            const end = list.indexOf(heading) + block.reduce((n, unit) => n + unit.length, 0);
            list.splice(end, 0, ...copies);
            newDevices.forEach((d) => self.devices.push(d));
            self.entries(list);
            self.changed();
        };

        // the row's copy button on a control: the same copy the control editor's Duplicate makes
        self.duplicateControlRow = (entry) => {
            const device = self.controlOf(entry);
            if (device) self.duplicateDevice(device);
        };

        // the run text of a list entry, as the menu's confirmation and the row's code column show it
        self.runTextOf = runTextOf;

        // a control's row removes all its member commands, not the control itself (that stays in Controls)
        self.remove = (entry) => {
            const device = self.controlOf(entry);
            const gone = device ? self.entries().filter((e) => !isSection(e) && e.group === device.id) : [entry];
            const doRemove = () => {
                self.entries(self.entries().filter((e) => gone.indexOf(e) < 0));
                self.changed();
            };
            if (isSection(entry)) {
                doRemove();
                return;
            }
            showConfirmationDialog({
                message: _.sprintf(gettext('This removes the command "%(name)s".'), {
                    name: _.escape(device ? device.name : entry.name)
                }),
                onproceed: doRemove
            });
        };

        // a control's members move as one unit (SceModel.units); any other entry is a unit of its own
        const unitsNow = () => window.SceModel.units(self.entries(), controlIdOf);
        const flatten = (units) => units.reduce((all, unit) => all.concat(unit), []);
        const unitIndex = (units, entry) => units.findIndex((unit) => unit.indexOf(entry) >= 0);

        self.canMove = (entry, step) => {
            if (isSection(entry)) return window.SceModel.canMoveBlock(self.entries(), controlIdOf, entry, step);
            const units = unitsNow();
            const i = unitIndex(units, entry);
            return i >= 0 && i + step >= 0 && i + step < units.length;
        };

        const moveUnit = (entry, step) => {
            // a heading moves with its whole block; an item moves alone, as before
            if (isSection(entry)) {
                self.entries(window.SceModel.moveBlock(self.entries(), controlIdOf, entry, step));
                self.changed();
                return;
            }
            const units = unitsNow();
            const i = unitIndex(units, entry);
            const j = i + step;
            if (i < 0 || j < 0 || j >= units.length) return;
            const other = units[j];
            units[j] = units[i];
            units[i] = other;
            self.entries(flatten(units));
            self.changed();
        };

        self.moveUp = (entry) => moveUnit(entry, -1);
        self.moveDown = (entry) => moveUnit(entry, 1);

        // --- drag and drop ----------------------------------------------------------------------------------------

        self._dragging = undefined;
        self.dragOver = ko.observable(undefined);

        self.dragStart = (entry, event) => {
            self._dragging = entry;
            const dt = event.originalEvent && event.originalEvent.dataTransfer;
            if (dt) {
                dt.effectAllowed = "move";
                dt.setData("text/plain", entry.action || "");
            }
            return true;
        };
        // OctoPrint before 1.10 opens its file upload overlay on any dragenter that reaches the document,
        // which covers the list and cancels the drag. Keep our own drags from reaching it.
        self.dragEnterRow = (entry, event) => {
            if (self._dragging !== undefined) event.stopPropagation();
            return true;
        };
        self.dragOverRow = (entry, event) => {
            if (self._dragging === undefined) return true;
            event.preventDefault();
            self.dragOver(entry);
            return false;
        };
        self.dragLeave = () => {
            self.dragOver(undefined);
            return true;
        };
        // the dragged unit takes the target unit's place, as a single-entry move does
        self.dropRow = (entry, event) => {
            event.preventDefault();
            if (self._dragging !== undefined && isSection(self._dragging)) {
                // a dragged heading moves its whole block to the target's block place
                self.entries(window.SceModel.dropBlock(self.entries(), controlIdOf, self._dragging, entry));
                self.changed();
            } else if (self._dragging !== undefined) {
                const units = unitsNow();
                const from = unitIndex(units, self._dragging);
                const to = unitIndex(units, entry);
                if (from >= 0 && to >= 0 && from !== to) {
                    const [unit] = units.splice(from, 1);
                    units.splice(to, 0, unit);
                    self.entries(flatten(units));
                    self.changed();
                }
            }
            self.dragEnd();
            return false;
        };
        self.dragEnd = () => {
            self._dragging = undefined;
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
