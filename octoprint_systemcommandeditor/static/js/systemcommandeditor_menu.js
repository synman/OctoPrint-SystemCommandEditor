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

    function members(deviceId, entries, core) {
        const m = {source: null, on: null, off: null, restart: null, choices: []};
        const tables = [["custom", entries || {}], ["core", core || {}]];
        for (const pair of tables) {
            const source = pair[0];
            const table = pair[1];
            for (const action of Object.keys(table)) {
                const e = table[action];
                if (!e || e.group !== deviceId) continue;
                if (m.source === null) m.source = source;
                const name = e.name || action;
                if (e.role === "on" && !m.on) m.on = action;
                else if (e.role === "off" && !m.off) m.off = action;
                else if (e.role === "restart" && !m.restart) m.restart = action;
                else if (e.role === "choice") m.choices.push({action: action, value: e.value, name: name});
            }
        }
        return m;
    }

    function switchView(m, state) {
        const buttons = [];
        if (m.restart) buttons.push({kind: "restart", action: m.restart});
        if (state !== "off" && m.off) buttons.push({kind: "off", action: m.off});
        if (state !== "on" && m.on) buttons.push({kind: "on", action: m.on});
        let rowClick = null;
        if (state === "on" && m.off) rowClick = "off";
        else if (state === "off" && m.on) rowClick = "on";
        return {buttons: buttons, rowClick: rowClick};
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

    // what a device row shows. nostatus: the device has no status command, so no dot and no word. pending: its
    // status command has not answered yet. unknown: a check ran and failed or matched nothing. Otherwise the state.
    function statusKind(hasStatus, result) {
        if (!hasStatus) return "nostatus";
        if (!result) return "pending";
        if (result.state === null || result.state === undefined || result.state === "unknown") return "unknown";
        return result.state;
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

    const SceModel = {
        printerState: printerState,
        statusKind: statusKind,
        conditionEffect: conditionEffect,
        members: members,
        switchView: switchView,
        StatusTracker: StatusTracker,
        RunTracker: RunTracker,
        escapeHtml: escapeHtml,
        consentHtml: consentHtml
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
    const BUTTON_ICON = {restart: "fa-solid fa-rotate-right", off: "fa-solid fa-power-off", on: "fa-solid fa-play"};

    // core rejects with ["error", args] or ["denied", args] on failure and denial, and with a plain string on cancel
    function rejectOutcome(first) {
        const kind = Array.isArray(first) ? first[0] : first;
        return kind === "error" ? "failed" : "cancelled";
    }

    // every element this file creates carries ADDED, so one selector removes them all
    function node(tag, classes) {
        return $("<" + tag + ">").addClass([ADDED].concat(classes || []).join(" "));
    }

    function icon(cls) {
        return node("i", [cls || BLANK, "fa-fw", "sce-menu-icon"]);
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

        self._kindOf = (d) => SceModel.statusKind(!!d.status, self._status.get(d.id));

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
            const spec = self._spec(source, action);
            if (!spec) return;
            const entry = self._entry(source, action) || {};
            const command = source === "core" ? (self._data.core_commands || {})[action] : entry.command;
            const isAsync = !!entry.async;
            const token = self._runs.start(action, isAsync);

            const settle = (outcome) => {
                self._runs.finish(action, token, outcome);
                if (!isAsync) window.setTimeout(self.schedule, FLASH_MS + 50);
                if (entry.group) self.checkStatuses([entry.group]);
                self.schedule();
            };

            self.schedule();
            let pending;
            try {
                pending = self.systemViewModel.triggerCommand({
                    ...spec,
                    async: isAsync,
                    confirm: SceModel.consentHtml(entry.confirm || spec.confirm, command) || undefined
                });
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

        self.checkStatuses = (ids) => {
            const want = self._devices()
                .filter((d) => d.status && ids.indexOf(d.id) !== -1)
                .map((d) => d.id);
            if (!want.length) return;

            const seqs = {};
            want.forEach((id) => {
                seqs[id] = self._status.begin(id);
                self._checking[id] = seqs[id];
            });
            self.schedule();

            OctoPrint.simpleApiCommand("systemcommandeditor", "status", {ids: want})
                .done((response) => {
                    const results = (response && response.results) || {};
                    want.forEach((id) => {
                        if (results[id] !== undefined) self._status.accept(id, seqs[id], results[id]);
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
                self.render();
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

            let rowAction = view.rowClick ? m[view.rowClick] : null;
            let rowCond = self._condition(d.when);
            if (!rowCond.effect && rowAction) rowCond = self._condition(self._entryOf(rowAction).when);
            if (rowCond.effect === "hide") li.addClass(HIDDEN);
            if (rowCond.effect === "disable") li.addClass("disabled");

            const row = node("a", ["sce-device-row"]).attr("href", "javascript:void(0)");
            row.append(icon(d.icon), node("span", ["sce-device-name"]).text(d.name));
            if (self._checking[d.id]) {
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
                if (!rowAction || rowCond.effect === "disable") return;
                self._run(self._sourceOf(rowAction), rowAction);
            });

            const buttons = node("span", ["sce-buttons"]);
            view.buttons.forEach((b) => {
                const entry = self._entryOf(b.action);
                const cond = self._condition(entry.when);
                const btn = node("button", ["btn", "btn-mini", "sce-btn"]).attr({
                    type: "button",
                    title: entry.name || b.action
                });
                if (cond.effect === "hide") btn.addClass(HIDDEN);
                if (cond.effect === "disable") {
                    btn.addClass("disabled");
                    btn.attr("title", cond.reason);
                }
                const spinning = self._runs.spinning(b.action);
                const cls = (!spinning && self._flash(b.action, entry)) || BUTTON_ICON[b.kind];
                btn.append(node("i", [cls + (spinning ? " fa-spin" : ""), "fa-fw"]));
                btn.on("click", (ev) => {
                    ev.stopPropagation();
                    if (btn.hasClass("disabled")) return;
                    self._run(self._sourceOf(b.action), b.action);
                });
                buttons.append(btn);
            });
            li.append(row, buttons);
            return li;
        };

        self._buildSelector = (d, li, members) => {
            const kind = self._kindOf(d);
            const shown = kind !== "nostatus" && kind !== "pending";
            const current = members.choices.find((c) => shown && String(c.value) === String(kind));

            li.addClass("dropdown-submenu");
            const row = node("a", ["sce-device-row"]).attr("href", "javascript:void(0)");
            row.append(
                icon(d.icon),
                node("span", ["sce-device-name"]).text(d.name),
                node("span", ["sce-state"]).text(current ? current.name : (shown ? "Unknown" : ""))
            );
            row.on("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); });
            const sub = node("ul", ["dropdown-menu"]);
            members.choices.forEach((c) => {
                const entry = self._entryOf(c.action);
                const cond = self._condition(entry.when);
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

            items.forEach((item) => {
                const el = item.el;
                const data = item.data;
                if (!data || !data.actionSource) return;
                const source = data.actionSource;
                const action = data.action;

                if (source !== "custom") {
                    if (action === "divider") return;
                    const c = core[action];
                    if (c && c.heading) {
                        node("li", ["dropdown-header"]).text(c.heading).insertBefore(el);
                    }
                    icon(c && c.icon).prependTo(el.children("a").first());
                } else if (action === "divider") {
                    // core puts its own divider before the custom commands; don't draw a second line
                    if (el.prevAll("li").first().hasClass("divider")) el.addClass(DOUBLED);
                    if (data.name) {
                        node("li", ["dropdown-header"]).text(data.name).insertAfter(el);
                    }
                    return;
                } else {
                    icon((entries[action] || {}).icon).prependTo(el.children("a").first());
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
                    iconEl.addClass("fa-spin");
                } else {
                    const flash = self._flash(action, e);
                    if (flash) iconEl.attr("class", ADDED + " " + flash + " fa-fw sce-menu-icon");
                }
            });

            const place = sectionPlacer(items, menu);
            devices.forEach((d) => {
                const li = node("li", [DEVICE]).attr("data-device-id", d.id);
                if (d.type === "switch" || d.type === "selector") {
                    if (!self._hasPresentMember(d.id)) return;
                    const m = SceModel.members(d.id, entries, core);
                    if (d.type === "switch") self._buildSwitch(d, li, m);
                    else self._buildSelector(d, li, m);
                    if (firstMember[d.id]) li.insertBefore(firstMember[d.id]);
                    else place(d.section, li);
                    return;
                }
                if (d.type === "readout") {
                    const kind = self._kindOf(d);
                    li.addClass("sce-readout");
                    const text = self._checking[d.id]
                        ? node("span", ["sce-state"]).append(node("i", ["fa-solid fa-spinner fa-spin", "fa-fw"]))
                        : node("span", ["sce-state"]).text(
                            kind === "nostatus" || kind === "pending" ? "" : kind === "unknown" ? "Unknown" : String(kind));
                    li.append(icon(d.icon), node("span", ["sce-device-name"]).text(d.name), text);
                    place(d.section, li);
                    return;
                }
                if (d.type === "link") {
                    const cond = self._condition(d.when);
                    if (cond.effect === "hide") li.addClass(HIDDEN);
                    const a = node("a", ["sce-device-row"]).attr({href: d.url || "#", target: "_blank", rel: "noopener"});
                    if (cond.effect === "disable") {
                        a.addClass("disabled");
                        a.attr("title", cond.reason);
                    }
                    a.append(icon(d.icon), node("span", ["sce-device-name"]).text(d.name));
                    a.on("click", (ev) => {
                        ev.stopPropagation();
                        if (a.hasClass("disabled")) ev.preventDefault();
                    });
                    li.append(a);
                    place(d.section, li);
                }
            });
        };

        self._bindMenu = () => {
            const menu = $(MENU);
            if (!menu.length || menu[0].dataset.sceBound) return;
            menu[0].dataset.sceBound = "1";
            // capture phase: runs before core's own handler on standalone items, so only this path runs them
            menu[0].addEventListener("click", (event) => {
                const target = $(event.target);
                const li = target.closest("li");
                if (!li.length || li.hasClass(ADDED)) return;
                if (!target.closest("a").length) return;
                const data = ko.dataFor(li[0]);
                if (!data || !data.actionSource || data.action === "divider") return;
                if (self._groupOf(data.actionSource, data.action)) return;
                if (!self._entry(data.actionSource, data.action)) return;
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
