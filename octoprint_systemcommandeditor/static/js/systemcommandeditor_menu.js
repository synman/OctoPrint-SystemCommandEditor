/*
 * System Command Editor: draws the System menu from the plugin's own data.
 *
 * The menu is core's. This only adds nodes to it, each marked "sce-added", and removes its own nodes before drawing
 * them again. Icons, headings and device rows come from GET /api/plugin/systemcommandeditor. If that request fails
 * (no permission, or the plugin is unavailable), core's plain menu is left alone.
 *
 * The model at the top is pure: no DOM, no jQuery, no Knockout. It is exported for Node tests and set on window in
 * the browser.
 */
(function () {
    "use strict";

    function printerState(flags) {
        const f = flags || {};
        if (f.printing) return "printing";
        if (f.paused) return "paused";
        if (f.error) return "error";
        if (f.operational) return "ready";
        return "offline";
    }

    function conditionEffect(when, state) {
        if (!when) return null;
        const listed = (when.states || []).indexOf(state) !== -1;
        const effect = when.effect === "disable" ? "disable" : "hide";
        if (when.mode === "never" && listed) return effect;
        if (when.mode === "only" && !listed) return effect;
        return null;
    }

    // the state word of a control's status or read text: its first line, cut at the first ":" and trimmed. Empty is
    // unknown (null); so is a missing text. Callers pass null for an errored read.
    function stateWord(text) {
        if (text === undefined || text === null) return null;
        const word = String(text).split("\n")[0].split(":")[0].trim();
        return word === "" ? null : word;
    }

    // whether a word is one of when_control's states: case-insensitive and exact
    function stateListed(states, word) {
        const w = String(word).toLowerCase();
        return (states || []).some((s) => String(s).toLowerCase() === w);
    }

    // The state word a command judges its when_control by. Only a custom command has one: a built-in (core) command
    // is never judged by its control, since the server cannot check it. Unknown is null: no result yet, an error,
    // or an empty word.
    function controlWordOf(source, result) {
        if (source === "core") return null;
        if (!result || result.error) return null;
        return stateWord(result.output);
    }

    // The block one command gets from its printer when and its control's state, or null. effect is "hide" when any
    // blocking condition says hide, else "disable". The reason is the printer's when when it blocks, else the
    // control's. when_control counts only on an entry with a group, and only when the control word is known.
    function commandBlock(entry, printerState, controlWord, controlName) {
        const e = entry || {};
        const printer = conditionEffect(e.when, printerState);
        let control = null;
        const wc = e.when_control;
        const known = controlWord !== null && controlWord !== undefined && controlWord !== "";
        if (e.group && wc && known) {
            const listed = stateListed(wc.states, controlWord);
            const blocks = (wc.mode === "never" && listed) || (wc.mode === "only" && !listed);
            if (blocks) control = wc.effect === "disable" ? "disable" : "hide";
        }
        if (!printer && !control) return null;
        const effect = printer === "hide" || control === "hide" ? "hide" : "disable";
        let reason;
        if (printer) {
            const states = (e.when && e.when.states) || [];
            reason = e.when.mode === "only"
                ? "Only available while " + states.join(" or ")
                : "Not available while " + states.join(" or ");
        } else {
            reason = "Not available while " + controlName + " is " + controlWord;
        }
        return {effect: effect, reason: reason};
    }

    // One sentence under the condition editor: when the command or device is grayed out or hidden.
    // States are named in STATES order, joined "A", "A or B", "A, B or C". No states picked gives "".
    const CONDITION_STATES = [["printing", "Printing"], ["paused", "Paused"], ["ready", "Ready"],
        ["offline", "Offline"], ["error", "Error"]];

    // t translates each fragment; the browser passes gettext, Node tests pass nothing
    function conditionSummary(mode, states, effect, t) {
        const tr = t || ((text) => text);
        const picked = CONDITION_STATES.filter((s) => (states || []).includes(s[0])).map((s) => tr(s[1]));
        if (picked.length === 0) return "";
        const joined = picked.length === 1 ? picked[0]
            : picked.slice(0, -1).join(", ") + " " + tr("or") + " " + picked[picked.length - 1];
        const verb = effect !== "disable" ? tr("Hidden") : tr("Grayed out");
        const clause = mode === "never" ? tr("while the printer is") : tr("unless the printer is");
        return verb + " " + clause + " " + joined + ".";
    }

    // a control's members: custom ones in the saved order (order: the custom action keys in list order, optional; a
    // custom key it leaves out follows in table order), then core ones in table order
    function members(deviceId, entries, core, order) {
        const m = {source: null, on: null, off: null, restart: null, safe_restart: null, choices: [], runs: []};
        const custom = entries || {};
        const listed = Array.isArray(order) ? order.filter((k) => Object.prototype.hasOwnProperty.call(custom, k)) : [];
        const customKeys = listed.concat(Object.keys(custom).filter((k) => listed.indexOf(k) === -1));
        const tables = [["custom", custom, customKeys], ["core", core || {}, null]];
        for (const pair of tables) {
            const source = pair[0];
            const table = pair[1];
            for (const action of (pair[2] || Object.keys(table))) {
                const e = table[action];
                if (!e || e.group !== deviceId) continue;
                if (m.source === null) m.source = source;
                const name = e.name || action;
                if (e.role === "on" && !m.on) m.on = action;
                else if (e.role === "off" && !m.off) m.off = action;
                else if (e.role === "restart" && !m.restart) m.restart = action;
                else if (e.role === "safe_restart" && !m.safe_restart) m.safe_restart = action;
                else if (e.role === "choice") m.choices.push({action: action, value: e.value, name: name});
                else if (e.role === "run") m.runs.push({action: action, source: source, name: name, icon: e.icon});
            }
        }
        return m;
    }

    const POWER = "fa-solid fa-power-off";

    // a binary switch shows one power button: turn it off while on, on while off, and a disabled one while the
    // state is not known. A control with no status check keeps the separate off and on buttons.
    function switchView(m, state) {
        const buttons = [];
        if (m.restart) buttons.push({kind: "restart", action: m.restart});
        if (m.safe_restart) buttons.push({kind: "safe_restart", action: m.safe_restart});
        if (state === "nostatus") {
            if (m.off) buttons.push({kind: "off", action: m.off});
            if (m.on) buttons.push({kind: "on", action: m.on});
        } else if (state === "on") {
            if (m.off) buttons.push({kind: "off", action: m.off, icon: POWER});
        } else if (state === "off") {
            if (m.on) buttons.push({kind: "on", action: m.on, icon: POWER});
        } else if (m.on || m.off) {
            buttons.push({kind: "power", action: m.on || m.off, icon: POWER, disabled: true});
        }
        // the row itself runs nothing; only its buttons do
        return {buttons: buttons};
    }

    // one button per run member core lists (present(source, action)), in member order, with the member's own icon;
    // the row itself runs nothing
    function actionView(m, present) {
        return {buttons: m.runs.filter((r) => present(r.source, r.action))
            .map((r) => ({kind: "run", action: r.action, icon: r.icon || "fa-solid fa-play"}))};
    }

    function StatusTracker() {
        this._begun = {};
        this._results = {};
    }

    StatusTracker.prototype.begin = function (id) {
        this._begun[id] = (this._begun[id] || 0) + 1;
        return this._begun[id];
    };

    StatusTracker.prototype.accept = function (id, seq, result) {
        if (seq !== this._begun[id]) return false;
        this._results[id] = result;
        return true;
    };

    StatusTracker.prototype.get = function (id) {
        return this._results[id];
    };

    function RunTracker() {
        this._seq = 0;
        this._current = {};
        this._last = {};
    }

    RunTracker.prototype.start = function (action, isAsync) {
        this._seq += 1;
        this._current[action] = {token: this._seq, isAsync: !!isAsync};
        return this._seq;
    };

    RunTracker.prototype.spinning = function (action) {
        const c = this._current[action];
        return !!c && !c.isAsync;
    };

    RunTracker.prototype.finish = function (action, token, outcome) {
        const c = this._current[action];
        if (!c || c.token !== token) return false;
        delete this._current[action];
        if (outcome === "ok" || outcome === "failed") {
            this._last[action] = {outcome: outcome, time: Date.now()};
        }
        return true;
    };

    RunTracker.prototype.last = function (action) {
        return this._last[action];
    };

    // holds a redraw while a press is down, so a button is never replaced between its pointerdown and pointerup.
    // request() asks to draw now; release() reports whether a draw was owed during the press.
    function PressGate() {
        this.down = false;
        this.owed = false;
    }

    PressGate.prototype.press = function () {
        this.down = true;
    };

    PressGate.prototype.request = function () {
        if (this.down) {
            this.owed = true;
            return false;
        }
        return true;
    };

    PressGate.prototype.release = function () {
        if (!this.down) return false;
        this.down = false;
        const owed = this.owed;
        this.owed = false;
        return owed;
    };

    // what a device row shows. nostatus: the device has no status command, so no dot and no word. pending: its
    // status command has not answered yet. unknown: a check ran and failed or matched nothing. Otherwise the state.
    function statusKind(hasStatus, result) {
        if (!hasStatus) return "nostatus";
        if (!result) return "pending";
        if (result.state === null || result.state === undefined || result.state === "unknown") return "unknown";
        return result.state;
    }

    // a control gets status checks when it has a shell status command or a plugin read
    function hasStatusCheck(d) {
        return !!d && !!(d.status || d.read);
    }

    // canonical JSON with object keys sorted at every level, so the consent text reads the same whatever the key order
    function sortedJson(v) {
        if (Array.isArray(v)) return "[" + v.map(sortedJson).join(",") + "]";
        if (v !== null && typeof v === "object") {
            return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + sortedJson(v[k])).join(",") + "}";
        }
        return JSON.stringify(v);
    }

    // the consent text for a plugin run: "<plugin>: <helper, api or method>", then the JSON of its args or data when present
    function describeRun(run) {
        if (!run) return "";
        if (run.gcode !== undefined) return "G-code: " + [].concat(run.gcode).join("\n");
        if (run.printer !== undefined) return "OctoPrint: " + run.printer;
        if (run.event !== undefined) {
            const keys = run.payload ? Object.keys(run.payload) : [];
            return "Event: " + run.event + (keys.length ? " " + sortedJson(run.payload) : "");
        }
        const name = run.helper || run.api || run.method || "";
        const payload = run.args !== undefined && run.args !== null ? run.args : run.data;
        const head = (run.plugin || "") + ": " + name;
        return payload === undefined || payload === null ? head : head + " " + sortedJson(payload);
    }

    // a link command: a system.actions entry with a url and no command or run. Core lists it but cannot run it, so the
    // menu opens the url itself; standing alone it is a menu item, inside a control a run button
    function isLink(entry) {
        return !!entry && typeof entry.url === "string" && entry.url !== "" && !entry.command && !entry.run;
    }

    // the text a command's run text shows for a link: "Link: <url>"; "" for any entry that is not a link
    function linkText(entry) {
        return isLink(entry) ? "Link: " + entry.url : "";
    }

    const ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"};

    function escapeHtml(s) {
        const text = s === undefined || s === null ? "" : String(s);
        return text.replace(/[&<>"']/g, function (c) { return ESCAPES[c]; });
    }

    function consentHtml(confirm, command) {
        if (!confirm) return "";
        return confirm + "<pre>" + escapeHtml(command || "") + "</pre>";
    }

    // a placement entry names the readout it draws in the list: it has a device, and it is never a plugin run
    function isPlacement(e) {
        return !!e && !!e.device && !e.run;
    }

    // the action key of the placement entry that draws readout deviceId in the menu, or "" when it has none
    function placementOf(deviceId, entries) {
        for (const key of Object.keys(entries || {})) {
            const e = entries[key];
            if (isPlacement(e) && e.device === deviceId) return key;
        }
        return "";
    }

    const BUTTON_ICON = {restart: "fa-solid fa-rotate-right", safe_restart: "fa-solid fa-shield-halved", off: "fa-solid fa-power-off", on: "fa-solid fa-play"};

    // the icon a switch's role button draws when its command has none of its own
    const ROLE_ICON = {on: "fa-solid fa-toggle-on", off: "fa-solid fa-toggle-off", restart: "fa-solid fa-rotate-right", safe_restart: "fa-solid fa-shield-halved"};

    // every button a control's row can show, in the menu's order, whatever its state: the settings list draws them all
    function rowButtons(m) {
        const buttons = [];
        ["restart", "safe_restart", "off", "on"].forEach((kind) => {
            if (m[kind]) buttons.push({kind: kind, action: m[kind], icon: ROLE_ICON[kind]});
        });
        (m.runs || []).forEach((r) => buttons.push({kind: "run", action: r.action, icon: r.icon || "fa-solid fa-play"}));
        (m.choices || []).forEach((c) => buttons.push({kind: "choice", action: c.action, icon: c.icon || "fa-solid fa-circle"}));
        return buttons;
    }

    // a list as move units: each entry alone, except a control's members, which form one unit at the place of its
    // first member, in list order. deviceIdOf(entry) gives the control id an entry drives, or "" for none.
    function units(entries, deviceIdOf) {
        const out = [];
        const byId = new Map();
        (entries || []).forEach(function (entry) {
            const id = deviceIdOf(entry) || "";
            if (!id) {
                out.push([entry]);
                return;
            }
            if (!byId.has(id)) {
                byId.set(id, []);
                out.push(byId.get(id));
            }
            byId.get(id).push(entry);
        });
        return out;
    }

    // a heading's unit: the one-entry unit that is a divider
    const isHeadingUnit = (unit) => unit.length === 1 && unit[0].action === "divider";

    // the list as blocks: a heading unit with every following unit up to the next heading; each unit before the first
    // heading is a block of its own. Every block is an array of units.
    function blocks(entries, deviceIdOf) {
        const out = [];
        let current = null;
        units(entries, deviceIdOf).forEach(function (unit) {
            if (isHeadingUnit(unit)) {
                current = [unit];
                out.push(current);
            } else if (current) {
                current.push(unit);
            } else {
                out.push([unit]);
            }
        });
        return out;
    }

    // the index of the block whose heading is heading, or -1
    function headingBlockIndex(bs, heading) {
        return bs.findIndex((b) => b[0].length === 1 && b[0][0] === heading);
    }

    // the blocks flattened back to one list of entries, in order
    function flattenBlocks(bs) {
        return bs.reduce((all, block) => block.reduce((acc, unit) => acc.concat(unit), all), []);
    }

    // true when the heading's block can move one block up (step -1) or down (step 1)
    function canMoveBlock(entries, deviceIdOf, heading, step) {
        const bs = blocks(entries, deviceIdOf);
        const i = headingBlockIndex(bs, heading);
        return i >= 0 && i + step >= 0 && i + step < bs.length;
    }

    // the list with the heading's block swapped with its neighbouring block; at the first or last block it is unchanged
    function moveBlock(entries, deviceIdOf, heading, step) {
        const bs = blocks(entries, deviceIdOf);
        const i = headingBlockIndex(bs, heading);
        const j = i + step;
        if (i >= 0 && j >= 0 && j < bs.length) {
            const other = bs[j];
            bs[j] = bs[i];
            bs[i] = other;
        }
        return flattenBlocks(bs);
    }

    // the list with the heading's block moved to the target's block place: before the target block when it sits above
    // it, after when below, so the block never lands inside another block
    function dropBlock(entries, deviceIdOf, heading, target) {
        const bs = blocks(entries, deviceIdOf);
        const from = headingBlockIndex(bs, heading);
        const to = bs.findIndex((b) => b.some((unit) => unit.indexOf(target) >= 0));
        if (from >= 0 && to >= 0 && from !== to) {
            const moved = bs.splice(from, 1)[0];
            bs.splice(to, 0, moved);
        }
        return flattenBlocks(bs);
    }

    // the icons a sync command's busy indicator may use: Font Awesome 6 Free solid glyphs that read well turning
    const BUSY_ICONS = ["fa-solid fa-spinner", "fa-solid fa-circle-notch", "fa-solid fa-arrows-rotate",
        "fa-solid fa-rotate", "fa-solid fa-rotate-right", "fa-solid fa-gear", "fa-solid fa-fan",
        "fa-solid fa-compact-disc", "fa-solid fa-sun", "fa-solid fa-asterisk", "fa-solid fa-life-ring",
        "fa-solid fa-snowflake", "fa-solid fa-yin-yang", "fa-solid fa-dharmachakra", "fa-solid fa-hurricane"];

    // the classes that show a running sync command: the chosen busy icon, spinning. An unset or empty icon falls back
    // to the first allowed one.
    function busyClass(icon) {
        const base = String(icon || "").trim() || BUSY_ICONS[0];
        return base.split(/\s+/).filter((t) => t !== "fa-spin").concat(["fa-spin"]).join(" ");
    }

    // The name of a copy: "Copy of <name>", then "Copy 2 of <name>", "Copy 3 of <name>"... The first one no entry in
    // takenNames has. takenNames are the names already in use by the list the copy joins.
    function copyName(name, takenNames) {
        const taken = new Set(takenNames || []);
        for (let n = 1; ; n++) {
            const candidate = n === 1 ? "Copy of " + name : "Copy " + n + " of " + name;
            if (!taken.has(candidate)) return candidate;
        }
    }

    // Renames the second and later items that share a key, so every key in the list is unique. The first item with a
    // key keeps it; the others take the first free <key>_2, <key>_3... free against every key in the list and never
    // "divider". Dividers and items with no key are left alone. The input is not modified; renamed items are copies.
    function dedupeKeys(list, keyField) {
        const items = list || [];
        const taken = new Set();
        items.forEach((item) => {
            if (item && item[keyField] !== undefined) taken.add(item[keyField]);
        });
        const seen = new Set();
        return items.map((item) => {
            if (!item || item[keyField] === undefined || item[keyField] === "divider") return item;
            const key = item[keyField];
            if (!seen.has(key)) {
                seen.add(key);
                return item;
            }
            let n = 2;
            while (taken.has(key + "_" + n) || key + "_" + n === "divider") n++;
            const free = key + "_" + n;
            taken.add(free);
            seen.add(free);
            return Object.assign({}, item, {[keyField]: free});
        });
    }

    const SceModel = {
        BUTTON_ICON: BUTTON_ICON,
        ROLE_ICON: ROLE_ICON,
        BUSY_ICONS: BUSY_ICONS,
        busyClass: busyClass,
        rowButtons: rowButtons,
        units: units,
        placementOf: placementOf,
        printerState: printerState,
        statusKind: statusKind,
        hasStatusCheck: hasStatusCheck,
        describeRun: describeRun,
        isLink: isLink,
        linkText: linkText,
        isPlacement: isPlacement,
        conditionEffect: conditionEffect,
        stateWord: stateWord,
        controlWordOf: controlWordOf,
        commandBlock: commandBlock,
        conditionSummary: conditionSummary,
        members: members,
        blocks: blocks,
        canMoveBlock: canMoveBlock,
        moveBlock: moveBlock,
        dropBlock: dropBlock,
        switchView: switchView,
        actionView: actionView,
        StatusTracker: StatusTracker,
        RunTracker: RunTracker,
        PressGate: PressGate,
        escapeHtml: escapeHtml,
        consentHtml: consentHtml,
        copyName: copyName,
        dedupeKeys: dedupeKeys
    };

    if (typeof module !== "undefined" && module.exports) module.exports = SceModel;
    if (typeof window !== "undefined") window.SceModel = SceModel;
})();

if (typeof OCTOPRINT_VIEWMODELS !== "undefined" && typeof $ === "function") {
$(function () {
    const SceModel = window.SceModel;
    const ADDED = "sce-added";
    const DOUBLED = "sce-doubled";
    const HIDDEN = "sce-hidden";
    const COND = "sce-cond";
    const DEVICE = "sce-device";
    const MENU = "#navbar_systemmenu > ul.dropdown-menu";
    const BLANK = "sce-menu-icon-blank";
    const FLASH_MS = 3000;
    const BUTTON_ICON = SceModel.BUTTON_ICON;

    // core rejects with ["error", args] or ["denied", args] on failure and denial, and with a plain string on cancel
    function rejectOutcome(first) {
        const kind = Array.isArray(first) ? first[0] : first;
        return kind === "error" ? "failed" : "cancelled";
    }

    // the error a plugin run answered with (a 409 carries one); "" when the answer has none
    function replyError(jqXHR) {
        let body = jqXHR.responseJSON;
        if (!body) {
            try {
                body = JSON.parse(jqXHR.responseText);
            } catch (err) {
                body = {};
            }
        }
        return (body && body.error) || "";
    }

    // every element this file creates carries ADDED, so one selector removes them all
    function node(tag, classes) {
        return $("<" + tag + ">").addClass([ADDED].concat(classes || []).join(" "));
    }

    // inverted: the glyph is drawn light on a badge filled with the icon's color (or the text color without one)
    function icon(cls, color, invert) {
        const i = node("i", [cls || BLANK, "fa-fw", "sce-menu-icon"]);
        const valid = /^#[0-9a-f]{6}$/i.test(color || "");
        if (invert) {
            i.addClass("sce-icon-inverted");
            if (valid) i.css("background-color", color);
        } else if (valid) {
            i.css("color", color);
        }
        return i;
    }

    // core-rendered elements we change are marked COND so the next render can undo exactly that
    function markCore(el, cls, title) {
        el.addClass(cls + " " + COND);
        if (title) el.children("a").first().attr("title", title).addClass(COND);
    }

    // returns place(section, li): after the last item of the named custom section, else at the end of the menu
    function sectionPlacer(items, menu) {
        const refs = {};
        function anchorOf(section) {
            if (!section) return null;
            let anchor = null;
            let inside = false;
            items.forEach(function (item) {
                const data = item.data;
                if (!data || !data.actionSource) return;
                if (data.action === "divider") {
                    inside = data.actionSource === "custom" && data.name === section;
                    if (inside) anchor = item.el;
                    return;
                }
                if (inside) anchor = item.el;
            });
            if (anchor) {
                // the section's heading sits right after its divider; rows go after it
                while (anchor.next().is("li.dropdown-header." + ADDED)) anchor = anchor.next();
            }
            return anchor;
        }
        return function (section, li) {
            if (!(section in refs)) refs[section] = anchorOf(section);
            const ref = refs[section];
            if (ref) {
                ref.after(li);
                refs[section] = li;
            } else if (section) {
                // no custom divider names this section: draw one heading for it, once per render
                if (!menu.children("li").last().hasClass("divider")) menu.append(node("li", ["divider"]));
                menu.append(node("li", ["dropdown-header"]).text(section));
                menu.append(li);
                refs[section] = li;
            } else {
                menu.append(li);
            }
        };
    }

    function SystemCommandEditorMenuViewModel(parameters) {
        const self = this;

        self.settingsViewModel = parameters[0];
        self.systemViewModel = parameters[1];
        self.printerStateViewModel = parameters[2];
        self.loginStateViewModel = parameters[3];
        self.accessViewModel = parameters[4];

        self._data = null;
        self._loaded = false;
        self._timer = undefined;
        self._checking = {};
        self._status = new SceModel.StatusTracker();
        self._runs = new SceModel.RunTracker();
        self._press = new SceModel.PressGate();

        self._devices = () => (self._data && self._data.devices) || [];

        self._entry = (source, action) => {
            if (!self._data) return undefined;
            const table = source === "core" ? self._data.core : self._data.entries;
            return (table || {})[action];
        };

        self._entryOf = (action) => self._entry(self._sourceOf(action), action) || {};

        self._sourceOf = (action) => (self._data && self._data.entries && self._data.entries[action] ? "custom" : "core");

        self._groupOf = (source, action) => {
            const e = self._entry(source, action);
            return (e && e.group) || "";
        };

        self._kindOf = (d) => SceModel.statusKind(SceModel.hasStatusCheck(d), self._status.get(d.id));

        // a device row is drawn only when at least one of its members is in core's command list
        self._hasPresentMember = (id) => {
            const tables = [["custom", self._data.entries || {}], ["core", self._data.core || {}]];
            return tables.some((table) => Object.keys(table[1]).some((action) => {
                const e = table[1][action];
                return !!e && e.group === id && !!self._spec(table[0], action);
            }));
        };

        self._spec = (source, action) => {
            const list = self.systemViewModel.systemActions() || [];
            return list.find((a) => a && a.actionSource === source && a.action === action);
        };

        self._printer = () => {
            const ps = self.printerStateViewModel;
            const v = (name) => !!(ps && ko.isObservable(ps[name]) && ps[name]());
            return SceModel.printerState({
                operational: v("isOperational"),
                printing: v("isPrinting"),
                paused: v("isPaused"),
                error: v("isError")
            });
        };

        // a control's state word from its last status result: null (unknown) until a result arrives, or when it errored
        self._controlWord = (source, id) => SceModel.controlWordOf(source, id ? self._status.get(id) : undefined);

        self._controlName = (id) => {
            const d = self._devices().find((x) => x.id === id);
            return d ? d.name : id;
        };

        // a member command's block, judged on its printer when and on its control's state (see commandBlock)
        self._judge = (source, entry, controlId) => SceModel.commandBlock(entry, self._printer(),
            self._controlWord(source, controlId), self._controlName(controlId)) || {effect: null, reason: ""};

        self._condition = (when) => {
            const effect = SceModel.conditionEffect(when, self._printer());
            const states = (when && when.states) || [];
            let reason = "";
            if (effect) {
                reason = when.mode === "only"
                    ? "Only available while " + states.join(" or ")
                    : "Not available while " + states.join(" or ");
            }
            return {effect: effect, reason: reason};
        };

        self._run = (source, action) => {
            const entry = self._entry(source, action) || {};
            // core cannot run a link command: it answers 500 to an entry with no command. The page opens in a new tab
            // and nothing is posted to OctoPrint
            // a link with a confirmation asks first, showing its "Link: <url>" text as the command would; only proceed opens it
            if (SceModel.isLink(entry)) {
                const open = () => window.open(entry.url, "_blank", "noopener");
                if (!entry.confirm) {
                    open();
                    return;
                }
                showConfirmationDialog({
                    message: SceModel.consentHtml(entry.confirm, SceModel.linkText(entry)),
                    onproceed: open
                });
                return;
            }
            // a sync run still spinning is not run again by a second tap
            if (self._runs.spinning(action)) return;
            const spec = self._spec(source, action);
            if (!spec) return;
            const isRun = !!entry.run;
            const command = isRun ? SceModel.describeRun(entry.run)
                : (source === "core" ? (self._data.core_commands || {})[action] : entry.command);
            const isAsync = !!entry.async;
            const token = self._runs.start(action, isAsync);

            const settle = (outcome) => {
                self._runs.finish(action, token, outcome);
                if (!isAsync) window.setTimeout(self.schedule, FLASH_MS + 50);
                if (entry.group) self.checkStatuses([entry.group]);
                self.schedule();
            };

            self.schedule();
            const runSpec = {
                ...spec,
                async: isAsync,
                confirm: SceModel.consentHtml(entry.confirm || spec.confirm, command) || undefined
            };
            // a sync run already shows its spinner and result on its own button, so its success toast is
            // optional; async runs and failures always toast
            const quiet = !isAsync && !(self._data && self._data.success_toast);
            let pending;
            try {
                if (isRun) pending = self._triggerRun(runSpec, quiet);
                else pending = quiet ? self._triggerQuietly(runSpec) : self.systemViewModel.triggerCommand(runSpec);
            } catch (err) {
                settle("failed");
                return;
            }
            if (!pending || typeof pending.then !== "function") {
                settle("ok");
                return;
            }
            pending.then(
                () => settle("ok"),
                (first) => settle(rejectOutcome(first))
            );
        };

        // core's triggerCommand without its success toast: the same consent dialog, fresh login check,
        // error toast and promise outcomes (see OctoPrint's static/js/app/viewmodels/system.js)
        // the login check, the consent dialog and fresh credentials, shared by every run path; execute(deferred) makes
        // the call and settles deferred
        self._runGated = (spec, execute) => {
            const vm = self.systemViewModel;
            if (!vm.loginState.hasPermission(vm.access.permissions.SYSTEM)) {
                return $.Deferred().reject().promise();
            }
            const deferred = $.Deferred();
            const proceed = () => {
                if (spec.fresh_credentials) {
                    vm.loginState.reauthenticateIfNecessary(() => execute(deferred)).failed(function () {
                        deferred.reject(["denied", arguments]);
                    });
                } else {
                    execute(deferred);
                }
            };
            if (spec.confirm) {
                showConfirmationDialog({
                    message: spec.confirm,
                    onproceed: proceed,
                    oncancel: () => deferred.reject("cancelled")
                });
            } else {
                proceed();
            }
            return deferred.promise();
        };

        // core's triggerCommand without its success toast: the same consent dialog, fresh login check,
        // error toast and promise outcomes (see OctoPrint's static/js/app/viewmodels/system.js)
        self._triggerQuietly = (spec) => self._runGated(spec, (deferred) => {
            OctoPrint.system.executeCommand(spec.actionSource, spec.action)
                .done(function () {
                    deferred.resolve(["success", arguments]);
                })
                .fail(function (jqXHR) {
                    if (spec.ignore) {
                        deferred.resolve(["ignored", arguments]);
                        return;
                    }
                    let error = "<p>" + _.sprintf(gettext("The command \"%(command)s\" could not be executed."),
                        {command: _.escape(spec.name)}) + "</p>";
                    error += pnotifyAdditionalInfo("<pre>" + _.escape(jqXHR.responseText) + "</pre>");
                    new PNotify({title: gettext("Error"), text: error, type: "error", hide: false});
                    deferred.reject(["error", arguments]);
                });
        });

        // a plugin-backed run goes to this plugin's run command, never to OctoPrint's execute. The answer is
        // {ok, error} at 200, or 409 with an error when the plugin's condition blocks it; both failures toast the error
        self._triggerRun = (spec, quiet) => self._runGated(spec, (deferred) => {
            const failed = (message) => {
                if (spec.ignore) {
                    deferred.resolve(["ignored", []]);
                    return;
                }
                let error = "<p>" + _.sprintf(gettext("The command \"%(command)s\" could not be executed."),
                    {command: _.escape(spec.name)}) + "</p>";
                // the plugin's error is a short sentence: it goes in the visible text, not behind the "more" link
                if (message) error += "<p>" + _.escape(message) + "</p>";
                new PNotify({title: gettext("Error"), text: error, type: "error", hide: false});
                deferred.reject(["error", []]);
            };
            OctoPrint.simpleApiCommand("systemcommandeditor", "run", {action: spec.action})
                .done(function (response) {
                    if (!response || !response.ok) {
                        failed(response && response.error);
                        return;
                    }
                    if (!quiet) {
                        new PNotify({
                            title: gettext("Command executed"),
                            text: _.sprintf(gettext("The command \"%(command)s\" was executed successfully."),
                                {command: _.escape(spec.name)}),
                            type: "success"
                        });
                    }
                    deferred.resolve(["success", arguments]);
                })
                .fail(function (jqXHR) {
                    failed(jqXHR.status === 409 ? replyError(jqXHR) : "");
                });
        });

        // optional: while the System menu stays open, re-check every device with a status on an interval
        self._startLive = () => {
            if (self._liveTimer !== undefined || !(self._data && self._data.live_updates)) return;
            const seconds = Math.max(1, Number(self._data.live_interval) || 5);
            self._liveTimer = window.setInterval(() => {
                if (!$("#navbar_systemmenu").hasClass("open") || !(self._data && self._data.live_updates)) {
                    window.clearInterval(self._liveTimer);
                    self._liveTimer = undefined;
                    return;
                }
                if (Object.keys(self._checking).length) return;   // the last check is still running
                self.checkStatuses(self._devices().map((d) => d.id), true);
            }, seconds * 1000);
        };

        // one status check for the named devices. live asks for live answers: a control whose read is not live answers
        // skipped, and then keeps the result it had
        self.checkStatuses = (ids, live) => {
            const want = self._devices()
                .filter((d) => SceModel.hasStatusCheck(d) && ids.indexOf(d.id) !== -1)
                .map((d) => d.id);
            if (!want.length) return;

            const seqs = {};
            want.forEach((id) => {
                seqs[id] = self._status.begin(id);
                self._checking[id] = seqs[id];
            });
            self.schedule();

            const payload = {ids: want};
            if (live) payload.live = true;
            OctoPrint.simpleApiCommand("systemcommandeditor", "status", payload)
                .done((response) => {
                    const results = (response && response.results) || {};
                    want.forEach((id) => {
                        const r = results[id];
                        if (r !== undefined && !r.skipped) self._status.accept(id, seqs[id], r);
                    });
                })
                .always(() => {
                    want.forEach((id) => {
                        if (self._checking[id] === seqs[id]) delete self._checking[id];
                    });
                    self.schedule();
                });
        };

        self.refresh = () => {
            OctoPrint.simpleApiGet("systemcommandeditor")
                .done((data) => {
                    self._data = data;
                    self.schedule();
                    if (!self._loaded) {
                        self._loaded = true;
                        self.checkStatuses(self._devices().map((d) => d.id));
                    }
                })
                .fail(() => {
                    self._data = null;
                    self.schedule();
                });
        };

        self.schedule = () => {
            // one pending render at a time; knockout re-renders the menu first, so draw after it
            if (self._timer !== undefined) return;
            self._timer = window.setTimeout(() => {
                self._timer = undefined;
                // a press in progress holds the draw; release() schedules it again
                if (self._press.request()) self.render();
            }, 0);
        };

        self._flash = (action, entry) => {
            if (!entry || entry.async) return null;
            const last = self._runs.last(action);
            if (!last || Date.now() - last.time >= FLASH_MS) return null;
            return last.outcome === "ok" ? "fa-solid fa-check" : "fa-solid fa-xmark";
        };

        self._buildSwitch = (d, li, members, ps) => {
            const m = members;
            const kind = self._kindOf(d);
            const view = SceModel.switchView(m, kind);

            const rowCond = self._condition(d.when);
            if (rowCond.effect === "hide") li.addClass(HIDDEN);
            if (rowCond.effect === "disable") li.addClass("disabled");

            const row = node("a", ["sce-device-row"]).attr("href", "javascript:void(0)");
            row.append(icon(d.icon, d.icon_color, d.icon_invert), node("span", ["sce-device-name"]).text(d.name));
            // a re-check keeps the last known state on screen, so opening the menu does not reflow it
            if (self._checking[d.id] && kind === "pending") {
                row.append(node("span", ["sce-state"]).append(node("i", ["fa-solid fa-spinner fa-spin", "fa-fw"])));
            } else if (kind !== "nostatus" && kind !== "pending") {
                const s = kind === "on" || kind === "off" ? kind : "unknown";
                const word = {on: "On", off: "Off", unknown: "Unknown"}[s];
                row.append(node("span", ["sce-state"]).append(node("span", ["sce-dot", "sce-" + s]), word));
            }
            if (rowCond.effect === "disable") row.attr("title", rowCond.reason);
            row.on("click", (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
            });

            li.append(row, self._runButtons(view.buttons, (b) => b.icon || BUTTON_ICON[b.kind]));
            return li;
        };

        // one button per run: the same consent, run, spin and flash path for switch verbs and action runs
        self._runButtons = (buttons, iconOf) => {
            const box = node("span", ["sce-buttons"]);
            buttons.forEach((b) => {
                const entry = self._entryOf(b.action);
                const cond = self._judge(self._sourceOf(b.action), entry, entry.group);
                const btn = node("button", ["btn", "btn-mini", "sce-btn"]).attr({
                    type: "button",
                    title: entry.name || b.action
                });
                if (cond.effect === "hide") btn.addClass(HIDDEN);
                if (cond.effect === "disable") {
                    btn.addClass("disabled");
                    btn.attr("title", cond.reason);
                }
                if (b.disabled) {
                    btn.addClass("disabled");
                    btn.attr("title", gettext("State unknown"));
                }
                // a running sync button shows the busy icon in place of its own; the check or cross then replaces it
                const spinning = self._runs.spinning(b.action);
                const cls = spinning ? SceModel.busyClass(self._data.busy_icon)
                    : (self._flash(b.action, entry) || iconOf(b, entry));
                btn.append(node("i", [cls, "fa-fw"]));
                btn.on("click", (ev) => {
                    ev.stopPropagation();
                    if (btn.hasClass("disabled")) return;
                    self._run(self._sourceOf(b.action), b.action);
                });
                box.append(btn);
            });
            return box;
        };

        // an action row: icon, name, the status text as a readout shows it, then one button per run member
        self._buildAction = (d, li, members) => {
            const kind = self._kindOf(d);
            const view = SceModel.actionView(members, (source, action) => !!self._spec(source, action));

            const rowCond = self._condition(d.when);
            if (rowCond.effect === "hide") li.addClass(HIDDEN);
            if (rowCond.effect === "disable") li.addClass("disabled");

            // the status sits under the name, so a long one never crowds the buttons
            const row = node("a", ["sce-device-row"]).attr("href", "javascript:void(0)");
            const label = node("span", ["sce-device-name", "sce-stack"]).append(node("span").text(d.name));
            row.append(icon(d.icon, d.icon_color, d.icon_invert), label);
            if (self._checking[d.id] && kind === "pending") {
                label.append(node("span", ["sce-state", "sce-under"]).append(node("i", ["fa-solid fa-spinner fa-spin", "fa-fw"])));
            } else if (kind !== "nostatus" && kind !== "pending") {
                label.append(node("span", ["sce-state", "sce-under"]).text(kind === "unknown" ? "Unknown" : String(kind)));
            }
            if (rowCond.effect === "disable") row.attr("title", rowCond.reason);
            // the row itself runs nothing; only its buttons do
            row.on("click", (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
            });

            li.append(row, self._runButtons(view.buttons, (b) => b.icon));
            return li;
        };

        self._buildSelector = (d, li, members) => {
            const kind = self._kindOf(d);
            const shown = kind !== "nostatus" && kind !== "pending";
            const current = members.choices.find((c) => shown && String(c.value) === String(kind));

            li.addClass("dropdown-submenu");
            const row = node("a", ["sce-device-row"]).attr("href", "javascript:void(0)");
            row.append(
                icon(d.icon, d.icon_color, d.icon_invert),
                node("span", ["sce-device-name"]).text(d.name),
                node("span", ["sce-state"]).text(current ? current.name : (shown ? "Unknown" : ""))
            );
            row.on("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); });
            const sub = node("ul", ["dropdown-menu"]);
            members.choices.forEach((c) => {
                const entry = self._entryOf(c.action);
                const cond = self._judge(self._sourceOf(c.action), entry, d.id);
                const item = node("li");
                const a = node("a", ["sce-choice"]).attr("href", "javascript:void(0)");
                const isCurrent = current !== undefined && current.action === c.action;
                a.append(icon(isCurrent ? "fa-solid fa-check" : BLANK), document.createTextNode(c.name));
                if (cond.effect === "hide") item.addClass(HIDDEN);
                if (cond.effect === "disable") {
                    a.addClass("disabled");
                    a.attr("title", cond.reason);
                }
                a.on("click", (ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    if (a.hasClass("disabled")) return;
                    self._run(self._sourceOf(c.action), c.action);
                });
                item.append(a);
                sub.append(item);
            });
            li.append(row, sub);
            return li;
        };

        self.render = () => {
            const menu = $(MENU);
            if (!menu.length) return;

            menu.find("." + ADDED).remove();
            menu.find("." + DOUBLED).removeClass(DOUBLED);
            menu.find("." + COND).removeClass([HIDDEN, "disabled", COND].join(" ")).removeAttr("title");
            menu.addClass("sce-menu");
            if (!self._data) return;

            const entries = self._data.entries || {};
            const core = self._data.core || {};
            const devices = self._devices();
            const deviceIds = {};
            devices.forEach((d) => { deviceIds[d.id] = d; });
            const items = menu.children("li").toArray().map((el) => ({el: $(el), data: ko.dataFor(el)}));
            const firstMember = {};
            // placement entries (a readout's place in the custom list) keyed by action, and the rows they are drawn at
            const placeKeys = Object.create(null);
            Object.keys(entries).forEach((key) => { if (SceModel.isPlacement(entries[key])) placeKeys[key] = true; });
            const placedAt = Object.create(null);

            items.forEach((item) => {
                const el = item.el;
                const data = item.data;
                if (!data || !data.actionSource) return;
                const source = data.actionSource;
                const action = data.action;

                if (source === "custom" && action in placeKeys) {
                    // a readout's placement: never drawn or run itself; its readout row is drawn in its place
                    markCore(el, HIDDEN);
                    placedAt[action] = el;
                    return;
                }

                if (source !== "custom") {
                    if (action === "divider") return;
                    const c = core[action];
                    if (c && c.heading) {
                        node("li", ["dropdown-header"]).text(c.heading).insertBefore(el);
                    }
                    icon(c && c.icon, c && c.icon_color, c && c.icon_invert).prependTo(el.children("a").first());
                } else if (action === "divider") {
                    // core puts its own divider before the custom commands; don't draw a second line
                    if (el.prevAll("li").first().hasClass("divider")) el.addClass(DOUBLED);
                    if (data.name) {
                        node("li", ["dropdown-header"]).text(data.name).insertAfter(el);
                    }
                    return;
                } else {
                    icon((entries[action] || {}).icon, (entries[action] || {}).icon_color, (entries[action] || {}).icon_invert).prependTo(el.children("a").first());
                }

                const group = self._groupOf(source, action);
                if (group) {
                    // a device member: hidden here, drawn by its device row
                    if (deviceIds[group]) {
                        markCore(el, HIDDEN);
                        if (!firstMember[group]) firstMember[group] = el;
                    }
                    return;
                }

                // standalone item
                const e = source === "core" ? core[action] : entries[action];
                if (!e) return;
                const cond = self._condition(e.when);
                if (cond.effect === "hide") markCore(el, HIDDEN);
                if (cond.effect === "disable") markCore(el, "disabled", cond.reason);
                const iconEl = el.children("a").first().children("i.sce-menu-icon");
                if (self._runs.spinning(action)) {
                    iconEl.attr("class", ADDED + " " + SceModel.busyClass(self._data.busy_icon) + " fa-fw sce-menu-icon");
                } else {
                    const flash = self._flash(action, e);
                    if (flash) iconEl.attr("class", ADDED + " " + flash + " fa-fw sce-menu-icon");
                }
            });

            const place = sectionPlacer(items, menu);
            devices.forEach((d) => {
                const li = node("li", [DEVICE]).attr("data-device-id", d.id);
                if (d.type === "switch" || d.type === "selector" || d.type === "action") {
                    if (!self._hasPresentMember(d.id)) return;
                    const m = SceModel.members(d.id, entries, core, self._data.order);
                    if (d.type === "switch") self._buildSwitch(d, li, m);
                    else if (d.type === "selector") self._buildSelector(d, li, m);
                    else self._buildAction(d, li, m);
                    if (firstMember[d.id]) li.insertBefore(firstMember[d.id]);
                    else place(d.section, li);
                    return;
                }
                if (d.type === "readout") {
                    const kind = self._kindOf(d);
                    li.addClass("sce-readout");
                    const text = self._checking[d.id] && kind === "pending"
                        ? node("span", ["sce-state"]).append(node("i", ["fa-solid fa-spinner fa-spin", "fa-fw"]))
                        : node("span", ["sce-state"]).text(
                            kind === "nostatus" || kind === "pending" ? "" : kind === "unknown" ? "Unknown" : String(kind));
                    li.append(icon(d.icon, d.icon_color, d.icon_invert), node("span", ["sce-device-name"]).text(d.name), text);
                    // drawn only where its placement entry is; a readout with no placement is not in the menu
                    const anchor = placedAt[SceModel.placementOf(d.id, entries)];
                    if (anchor) anchor.before(li);
                    return;
                }
            });
        };

        self._bindMenu = () => {
            const menu = $(MENU);
            if (!menu.length || menu[0].dataset.sceBound) return;
            menu[0].dataset.sceBound = "1";
            // a press holds redraws until it ends; a press ended outside the page (blur) ends too
            const press = self._press;
            const endPress = () => {
                if (press.release()) self.schedule();
            };
            menu[0].addEventListener("pointerdown", () => press.press(), true);
            window.addEventListener("pointerup", endPress, true);
            window.addEventListener("pointercancel", endPress, true);
            // blur fires on each element losing focus too; only the window itself losing focus ends the press
            window.addEventListener("blur", (event) => {
                if (event.target === window) endPress();
            }, true);
            // capture phase: runs before core's own handler on standalone items, so only this path runs them
            menu[0].addEventListener("click", (event) => {
                const target = $(event.target);
                const li = target.closest("li");
                if (!li.length || li.hasClass(ADDED)) return;
                if (!target.closest("a").length) return;
                const data = ko.dataFor(li[0]);
                if (!data || !data.actionSource || data.action === "divider") return;
                if (self._groupOf(data.actionSource, data.action)) return;
                const entry = self._entry(data.actionSource, data.action);
                if (!entry || entry.device) return;
                event.preventDefault();
                event.stopPropagation();
                if (li.hasClass("disabled")) return;
                self._run(data.actionSource, data.action);
            }, true);
        };

        self.onStartupComplete = () => {
            self.systemViewModel.systemActions.subscribe(self.schedule);

            const ps = self.printerStateViewModel;
            ["isOperational", "isPrinting", "isPaused", "isError"].forEach((name) => {
                if (ps && ko.isObservable(ps[name])) ps[name].subscribe(self.schedule);
            });
            const ls = self.loginStateViewModel;
            if (ls && ko.isObservable(ls.currentUser)) ls.currentUser.subscribe(self.refresh);

            self._bindMenu();
            $(document).on("click", "#navbar_systemmenu > a", () => {
                self.checkStatuses(self._devices().map((d) => d.id));
                self._startLive();
            });
            self.refresh();
        };

        self.onEventSettingsUpdated = self.refresh;
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: SystemCommandEditorMenuViewModel,
        dependencies: ["settingsViewModel", "systemViewModel", "printerStateViewModel", "loginStateViewModel", "accessViewModel"]
    });
});
}
