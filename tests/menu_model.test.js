"use strict";
// Plain Node test for the pure model in the System menu script. Run: node tests/menu_model.test.js

const path = require("path");
const assert = require("assert");

const M = require(path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js", "systemcommandeditor_menu.js"));

const STARTED = Date.now();
const failures = [];
let count = 0;

function test(name, fn) {
    count += 1;
    try {
        fn();
    } catch (err) {
        failures.push(name + ": " + err.message);
    }
}

// seeded LCG: the seed is printed on failure so a failing fuzz case can be replayed
function makeRng(seed) {
    let s = seed >>> 0;
    return function () {
        s = (Math.imul(s, 1103515245) + 12345) >>> 0;
        return s / 4294967296;
    };
}

const SEED = 20260601;
const FUZZ_CASES = 500;
const STATES = ["printing", "paused", "ready", "offline", "error"];

function pick(rng, list) {
    return list[Math.floor(rng() * list.length)];
}

// ---- printerState ----
test("printerState checks printing, paused, error, operational, in that order", () => {
    assert.strictEqual(M.printerState({printing: true, paused: true, error: true, operational: true}), "printing");
    assert.strictEqual(M.printerState({paused: true, error: true, operational: true}), "paused");
    assert.strictEqual(M.printerState({error: true, operational: true}), "error");
    assert.strictEqual(M.printerState({operational: true}), "ready");
    assert.strictEqual(M.printerState({}), "offline");
    assert.strictEqual(M.printerState(undefined), "offline");
});

// ---- conditionEffect ----
test("conditionEffect: no when gives null", () => {
    assert.strictEqual(M.conditionEffect(undefined, "printing"), null);
    assert.strictEqual(M.conditionEffect(null, "printing"), null);
});

test("conditionEffect: never + listed state gives the effect; never + unlisted gives null", () => {
    const when = {mode: "never", states: ["printing"], effect: "disable"};
    assert.strictEqual(M.conditionEffect(when, "printing"), "disable");
    assert.strictEqual(M.conditionEffect(when, "ready"), null);
});

test("conditionEffect: only + unlisted state gives the effect; only + listed gives null", () => {
    const when = {mode: "only", states: ["paused"], effect: "hide"};
    assert.strictEqual(M.conditionEffect(when, "ready"), "hide");
    assert.strictEqual(M.conditionEffect(when, "paused"), null);
});

// ---- members ----
test("members: reads custom entries and core entries by role", () => {
    const entries = {
        laser_on: {group: "laser", role: "on", name: "Laser on"},
        laser_off: {group: "laser", role: "off"},
        laser_restart: {group: "laser", role: "restart"},
        res_1080: {group: "res", role: "choice", value: "1080", name: "1080p"},
        res_720: {group: "res", role: "choice", value: "720"},
        other: {group: "", role: ""}
    };
    const core = {restart: {group: "laser", role: "off"}};
    const m = M.members("laser", entries, core);
    assert.strictEqual(m.source, "custom");
    assert.strictEqual(m.on, "laser_on");
    assert.strictEqual(m.off, "laser_off");
    assert.strictEqual(m.restart, "laser_restart");
    assert.deepStrictEqual(m.choices, []);
    const r = M.members("res", entries, {});
    assert.deepStrictEqual(r.choices.map((c) => c.action), ["res_1080", "res_720"]);
    assert.strictEqual(r.choices[0].name, "1080p");
    assert.strictEqual(r.choices[1].name, "res_720");
});

test("members: a device with only core members has source core", () => {
    const core = {shutdown: {group: "psu", role: "off"}};
    const m = M.members("psu", {}, core);
    assert.strictEqual(m.source, "core");
    assert.strictEqual(m.off, "shutdown");
    assert.strictEqual(m.on, null);
});

test("members: no matches gives empty members", () => {
    const m = M.members("nothing", {}, {});
    assert.deepStrictEqual(m, {source: null, on: null, off: null, restart: null, safe_restart: null, choices: [], runs: []});
});

// ---- units: a control's members form one unit at its first member's place; anything else stands alone ----
test("units: a control's members gather at its first member, in list order", () => {
    const idOf = (e) => e.group || "";
    const list = [{action: "a", group: "x"}, {action: "div"}, {action: "b", group: "y"}, {action: "c", group: "x"}];
    const got = M.units(list, idOf).map((u) => u.map((e) => e.action));
    assert.deepStrictEqual(got, [["a", "c"], ["div"], ["b"]]);
});

test("units: ungrouped entries are each a unit of their own, and empty input gives none", () => {
    const idOf = () => "";
    assert.deepStrictEqual(M.units([{action: "p"}, {action: "q"}], idOf).map((u) => u.length), [1, 1]);
    assert.deepStrictEqual(M.units(undefined, idOf), []);
});

// ---- the first entry with a role wins (a later entry must not overwrite it) ----
test("members: the first entry with role on wins", () => {
    const entries = {d_on_first: {group: "d", role: "on"}, d_on_second: {group: "d", role: "on"}};
    assert.strictEqual(M.members("d", entries, {}).on, "d_on_first");
});

test("members: the first entry with role off wins", () => {
    const entries = {d_off_first: {group: "d", role: "off"}, d_off_second: {group: "d", role: "off"}};
    assert.strictEqual(M.members("d", entries, {}).off, "d_off_first");
});

test("members: the first entry with role restart wins", () => {
    const entries = {d_rs_first: {group: "d", role: "restart"}, d_rs_second: {group: "d", role: "restart"}};
    assert.strictEqual(M.members("d", entries, {}).restart, "d_rs_first");
});

test("members: a safe_restart role fills its own slot, first one wins", () => {
    const core = {restart: {group: "op", role: "restart"}, restart_safe: {group: "op", role: "safe_restart"}};
    const entries = {other_safe: {group: "op", role: "safe_restart"}};
    const m = M.members("op", entries, core);
    assert.strictEqual(m.restart, "restart");
    assert.strictEqual(m.safe_restart, "other_safe");
});

test("switchView: a safe restart button follows restart, before off and on", () => {
    const m = {on: "a_on", off: "a_off", restart: "a_restart", safe_restart: "a_safe", choices: []};
    assert.deepStrictEqual(M.switchView(m, "unknown").buttons.map((b) => b.kind), ["restart", "safe_restart", "power"]);
    assert.deepStrictEqual(M.switchView({restart: "r", choices: []}, "on").buttons.map((b) => b.kind), ["restart"]);
});

// ---- switchView ----
test("switchView R1: restart and safe_restart come first, in every state", () => {
    const m = {on: "a_on", off: "a_off", restart: "a_restart", safe_restart: "a_safe", choices: []};
    ["nostatus", "pending", "unknown", "on", "off", "other"].forEach((state) => {
        assert.deepStrictEqual(M.switchView(m, state).buttons.slice(0, 2).map((b) => b.kind), ["restart", "safe_restart"], state);
    });
});

test("switchView R2: nostatus keeps an off button then an on button, each with no icon field", () => {
    const m = {on: "a_on", off: "a_off", restart: null, choices: []};
    const v = M.switchView(m, "nostatus");
    assert.deepStrictEqual(v.buttons, [{kind: "off", action: "a_off"}, {kind: "on", action: "a_on"}]);
    assert.ok(v.buttons.every((b) => !("icon" in b) && !("disabled" in b)));
});

test("switchView R3: state on gives one power off button, and none without an off member", () => {
    const withOff = M.switchView({on: "a_on", off: "a_off", restart: null, choices: []}, "on");
    assert.deepStrictEqual(withOff.buttons, [{kind: "off", action: "a_off", icon: "fa-solid fa-power-off"}]);
    assert.deepStrictEqual(M.switchView({on: "a_on", off: null, restart: null, choices: []}, "on").buttons, []);
});

test("switchView R4: state off gives one power on button, and none without an on member", () => {
    const withOn = M.switchView({on: "a_on", off: "a_off", restart: null, choices: []}, "off");
    assert.deepStrictEqual(withOn.buttons, [{kind: "on", action: "a_on", icon: "fa-solid fa-power-off"}]);
    assert.deepStrictEqual(M.switchView({on: null, off: "a_off", restart: null, choices: []}, "off").buttons, []);
});

test("switchView R5: pending, unknown and any other state give one disabled power button for on or off", () => {
    const both = {on: "a_on", off: "a_off", restart: null, choices: []};
    ["pending", "unknown", "some_word"].forEach((state) => {
        assert.deepStrictEqual(M.switchView(both, state).buttons,
            [{kind: "power", action: "a_on", icon: "fa-solid fa-power-off", disabled: true}], state);
    });
    assert.deepStrictEqual(M.switchView({on: null, off: "a_off", restart: null, choices: []}, "unknown").buttons,
        [{kind: "power", action: "a_off", icon: "fa-solid fa-power-off", disabled: true}]);
    assert.deepStrictEqual(M.switchView({on: null, off: null, restart: null, choices: []}, "unknown").buttons, []);
});

test("switchView: a switch with only an on member shows its power button in on, off and unknown", () => {
    const m = {on: "a_on", off: null, restart: null, choices: []};
    assert.deepStrictEqual(M.switchView(m, "on").buttons, []);
    assert.deepStrictEqual(M.switchView(m, "off").buttons.map((b) => b.kind), ["on"]);
    assert.deepStrictEqual(M.switchView(m, "unknown").buttons.map((b) => b.disabled), [true]);
});

test("switchView: disabled is true only on the R5 power button", () => {
    const m = {on: "a_on", off: "a_off", restart: "a_restart", safe_restart: "a_safe", choices: []};
    ["nostatus", "on", "off"].forEach((state) => {
        assert.ok(M.switchView(m, state).buttons.every((b) => b.disabled !== true), state);
    });
    assert.deepStrictEqual(M.switchView(m, "unknown").buttons.filter((b) => b.disabled).map((b) => b.kind), ["power"]);
});

test("switchView: the row itself runs nothing, in any state", () => {
    const m = {on: "a_on", off: "a_off", restart: "a_restart", choices: []};
    ["on", "off", "unknown", "nostatus"].forEach((state) => {
        assert.deepStrictEqual(Object.keys(M.switchView(m, state)), ["buttons"]);
    });
});

test("switchView: a button has the member action it runs", () => {
    const m = {on: "a_on", off: null, restart: "a_restart", choices: []};
    const v = M.switchView(m, "on");
    assert.deepStrictEqual(v.buttons, [{kind: "restart", action: "a_restart"}]);
});

// ---- escapeHtml, consentHtml ----
test("escapeHtml escapes the five characters", () => {
    assert.strictEqual(M.escapeHtml("<a href=\"x\">&'</a>"), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;");
    assert.strictEqual(M.escapeHtml(undefined), "");
});

test("consentHtml: empty confirm gives empty string", () => {
    assert.strictEqual(M.consentHtml("", "reboot"), "");
    assert.strictEqual(M.consentHtml(undefined, "reboot"), "");
});

test("consentHtml: confirm plus the escaped command inside pre", () => {
    assert.strictEqual(M.consentHtml("Run it?", "cmd <x> && y"), "Run it?<pre>cmd &lt;x&gt; &amp;&amp; y</pre>");
    assert.strictEqual(M.consentHtml("Run it?", undefined), "Run it?<pre></pre>");
});

// ---- RunTracker ----
test("RunTracker: a sync start spins until its newest token finishes", () => {
    const r = new M.RunTracker();
    const t = r.start("a", false);
    assert.strictEqual(r.spinning("a"), true);
    assert.strictEqual(r.finish("a", t, "ok"), true);
    assert.strictEqual(r.spinning("a"), false);
    assert.strictEqual(r.last("a").outcome, "ok");
});

test("RunTracker: an async start never spins", () => {
    const r = new M.RunTracker();
    r.start("a", true);
    assert.strictEqual(r.spinning("a"), false);
});

test("RunTracker: a stale token changes nothing", () => {
    const r = new M.RunTracker();
    const t1 = r.start("a", false);
    const t2 = r.start("a", false);
    assert.strictEqual(r.finish("a", t1, "ok"), false);
    assert.strictEqual(r.spinning("a"), true);
    assert.strictEqual(r.last("a"), undefined);
    r.finish("a", t2, "failed");
    assert.strictEqual(r.spinning("a"), false);
    assert.strictEqual(r.last("a").outcome, "failed");
});

test("RunTracker: cancelled stores nothing as the last result", () => {
    const r = new M.RunTracker();
    const t = r.start("a", false);
    r.finish("a", t, "cancelled");
    assert.strictEqual(r.spinning("a"), false);
    assert.strictEqual(r.last("a"), undefined);
});

// ---- StatusTracker ----
test("StatusTracker: only the newest begun sequence is accepted", () => {
    const s = new M.StatusTracker();
    const a = s.begin("laser");
    const b = s.begin("laser");
    assert.ok(b > a);
    assert.strictEqual(s.accept("laser", a, {state: "on"}), false);
    assert.strictEqual(s.get("laser"), undefined);
    assert.strictEqual(s.accept("laser", b, {state: "off"}), true);
    assert.deepStrictEqual(s.get("laser"), {state: "off"});
});

test("StatusTracker: an older accepted result is never stored over a newer one", () => {
    const s = new M.StatusTracker();
    const a = s.begin("x");
    s.accept("x", a, {state: "one"});
    const b = s.begin("x");
    s.accept("x", b, {state: "two"});
    assert.strictEqual(s.accept("x", a, {state: "stale"}), false);
    assert.deepStrictEqual(s.get("x"), {state: "two"});
});

// ---- statusKind: what a device row shows ----
test("statusKind: a device with no status command is nostatus whatever the result", () => {
    assert.strictEqual(M.statusKind(false, undefined), "nostatus");
    assert.strictEqual(M.statusKind(false, {state: "on"}), "nostatus");
});

test("statusKind: a status command not yet answered is pending", () => {
    assert.strictEqual(M.statusKind(true, undefined), "pending");
});

test("statusKind: a check that failed or matched nothing is unknown", () => {
    assert.strictEqual(M.statusKind(true, {state: null, error: "boom"}), "unknown");
    assert.strictEqual(M.statusKind(true, {state: "unknown"}), "unknown");
    assert.strictEqual(M.statusKind(true, {}), "unknown");
});

test("statusKind: a check that answered gives its state", () => {
    assert.strictEqual(M.statusKind(true, {state: "on"}), "on");
    assert.strictEqual(M.statusKind(true, {state: "off"}), "off");
    assert.strictEqual(M.statusKind(true, {state: "1080"}), "1080");
});

test("switchView: nostatus gives both the off and the on button, unknown gives one power button", () => {
    const m = {on: "a_on", off: "a_off", restart: null, choices: []};
    assert.deepStrictEqual(M.switchView(m, "nostatus").buttons.map((b) => b.kind), ["off", "on"]);
    assert.deepStrictEqual(M.switchView(m, "unknown").buttons.map((b) => b.kind), ["power"]);
});

// ---- seeded fuzz ----
test("fuzz: switchView never returns a button whose member is null", () => {
    const rng = makeRng(SEED);
    for (let i = 0; i < FUZZ_CASES; i++) {
        const pickMember = () => (rng() < 0.5 ? null : "act_" + Math.floor(rng() * 9));
        const m = {on: pickMember(), off: pickMember(), restart: pickMember(), choices: []};
        const state = pick(rng, ["on", "off", "unknown", null]);
        const v = M.switchView(m, state);
        v.buttons.forEach((b) => {
            assert.ok(b.action, "seed " + SEED + " case " + i);
            const member = b.kind === "power" ? (m.on || m.off) : m[b.kind];
            assert.strictEqual(member, b.action, "seed " + SEED + " case " + i);
        });
    }
});

test("fuzz: conditionEffect is null whenever when is absent", () => {
    const rng = makeRng(SEED + 1);
    for (let i = 0; i < FUZZ_CASES; i++) {
        const state = pick(rng, STATES);
        assert.strictEqual(M.conditionEffect(undefined, state), null, "seed " + (SEED + 1) + " case " + i);
    }
});

test("fuzz: RunTracker.spinning is never true for an async start", () => {
    const rng = makeRng(SEED + 2);
    const r = new M.RunTracker();
    for (let i = 0; i < FUZZ_CASES; i++) {
        const action = pick(rng, ["a", "b"]);
        const isAsync = rng() < 0.5;
        r.start(action, isAsync);
        if (isAsync) assert.strictEqual(r.spinning(action), false, "seed " + (SEED + 2) + " case " + i);
    }
});

test("fuzz: spinning is true exactly between a sync start and its newest-token finish", () => {
    const rng = makeRng(SEED + 3);
    const r = new M.RunTracker();
    const cur = {};   // model: action -> {token, isAsync} for the newest start
    const tokens = [];
    for (let i = 0; i < FUZZ_CASES; i++) {
        const action = pick(rng, ["a", "b"]);
        const op = rng();
        if (op < 0.5) {
            const isAsync = rng() < 0.5;
            const token = r.start(action, isAsync);
            tokens.push({action: action, token: token});
            cur[action] = {token: token, isAsync: isAsync};
        } else if (tokens.length) {
            const pickedTok = tokens[Math.floor(rng() * tokens.length)];
            r.finish(pickedTok.action, pickedTok.token, pick(rng, ["ok", "failed", "cancelled"]));
            if (cur[pickedTok.action] && cur[pickedTok.action].token === pickedTok.token) {
                delete cur[pickedTok.action];
            }
        }
        ["a", "b"].forEach((a) => {
            const expected = !!cur[a] && !cur[a].isAsync;
            assert.strictEqual(r.spinning(a), expected, "seed " + (SEED + 3) + " case " + i + " action " + a);
        });
    }
});

test("fuzz: StatusTracker.get holds the newest accepted result", () => {
    const rng = makeRng(SEED + 4);
    const s = new M.StatusTracker();
    const begun = {};
    const seqs = [];
    const model = {};
    for (let i = 0; i < FUZZ_CASES; i++) {
        const id = pick(rng, ["x", "y"]);
        if (rng() < 0.5) {
            begun[id] = (begun[id] || 0) + 1;
            const seq = s.begin(id);
            assert.strictEqual(seq, begun[id], "seed " + (SEED + 4) + " case " + i);
            seqs.push({id: id, seq: seq});
        } else if (seqs.length) {
            const pickedSeq = seqs[Math.floor(rng() * seqs.length)];
            const result = {state: "r" + i};
            const accepted = s.accept(pickedSeq.id, pickedSeq.seq, result);
            const isNewest = pickedSeq.seq === begun[pickedSeq.id];
            assert.strictEqual(accepted, isNewest, "seed " + (SEED + 4) + " case " + i);
            if (isNewest) model[pickedSeq.id] = result;
        }
        ["x", "y"].forEach((id) => {
            assert.deepStrictEqual(s.get(id), model[id], "seed " + (SEED + 4) + " case " + i + " id " + id);
        });
    }
});

test("placementOf: a readout is drawn at the placement entry that names its device", () => {
    const entries = {
        disk: {action: "disk", name: "Free disk", device: "disk"},
        backup_now: {action: "backup_now", command: "true", name: "Back up"}
    };
    assert.strictEqual(M.placementOf("disk", entries), "disk");
});

test("placementOf: an unplaced readout has no placement, and a command never counts as one", () => {
    const entries = {backup_now: {action: "backup_now", command: "true", name: "Back up"}};
    assert.strictEqual(M.placementOf("disk", entries), "");
    assert.strictEqual(M.placementOf("disk", {}), "");
    assert.strictEqual(M.placementOf("disk", undefined), "");
});

test("members: run members gather in member order with their own icon", () => {
    const entries = {
        first: {group: "backup", role: "run", name: "Back up now", icon: "fa-solid fa-box-archive"},
        other: {group: "backup", role: "run", name: "Other"},
        verb: {group: "backup", role: "on", name: "On"}
    };
    const m = M.members("backup", entries, {});
    assert.deepStrictEqual(m.runs, [
        {action: "first", source: "custom", name: "Back up now", icon: "fa-solid fa-box-archive"},
        {action: "other", source: "custom", name: "Other", icon: undefined}
    ]);
    assert.strictEqual(m.on, "verb");
});

test("actionView: one button per run member core lists, in order, using the member icon", () => {
    const entries = {
        a: {group: "d", role: "run", name: "A", icon: "fa-solid fa-film"},
        b: {group: "d", role: "run", name: "B"},
        c: {group: "d", role: "run", name: "C", icon: "fa-solid fa-broom"}
    };
    const m = M.members("d", entries, {});
    const listed = (source, action) => action !== "b";
    const view = M.actionView(m, listed);
    assert.deepStrictEqual(view.buttons, [
        {kind: "run", action: "a", icon: "fa-solid fa-film"},
        {kind: "run", action: "c", icon: "fa-solid fa-broom"}
    ]);
});

test("actionView: a run member without an icon falls back to play", () => {
    const m = M.members("d", {a: {group: "d", role: "run", name: "A"}}, {});
    assert.deepStrictEqual(M.actionView(m, () => true).buttons, [{kind: "run", action: "a", icon: "fa-solid fa-play"}]);
});

test("actionView: a row with no run member has no buttons and runs nothing itself", () => {
    const m = M.members("d", {a: {group: "d", role: "on", name: "A"}}, {});
    assert.deepStrictEqual(M.actionView(m, () => true).buttons, []);
});

test("actionView: core run members count when core lists them", () => {
    const m = M.members("d", {}, {reboot: {group: "d", role: "run", name: "Reboot", icon: "fa-solid fa-rotate-right"}});
    assert.deepStrictEqual(M.actionView(m, (source, action) => source === "core" && action === "reboot").buttons,
        [{kind: "run", action: "reboot", icon: "fa-solid fa-rotate-right"}]);
    assert.deepStrictEqual(M.actionView(m, () => false).buttons, []);
});

test("copyName: the first copy is Copy of the name", () => {
    assert.strictEqual(M.copyName("Restart", []), "Copy of Restart");
});

test("copyName: Copy of is taken, so Copy 2 of, then Copy 3 of", () => {
    assert.strictEqual(M.copyName("Restart", ["Restart", "Copy of Restart"]), "Copy 2 of Restart");
    assert.strictEqual(M.copyName("Restart", ["Copy of Restart", "Copy 2 of Restart"]), "Copy 3 of Restart");
});

test("copyName: names that are not copies of this name do not block it", () => {
    assert.strictEqual(M.copyName("Restart", ["Copy of Reboot", "Restart 2"]), "Copy of Restart");
});

test("dedupeKeys: the first item with a key keeps it, later ones get _2 and _3", () => {
    const out = M.dedupeKeys([{action: "a"}, {action: "a"}, {action: "a"}], "action");
    assert.deepStrictEqual(out.map((e) => e.action), ["a", "a_2", "a_3"]);
});

test("dedupeKeys: an existing key x_2 is respected, so the copy of x takes x_3", () => {
    const out = M.dedupeKeys([{action: "x"}, {action: "x"}, {action: "x_2"}], "action");
    assert.deepStrictEqual(out.map((e) => e.action), ["x", "x_3", "x_2"]);
});

test("dedupeKeys: a divider is never renamed or chosen as a new key", () => {
    const out = M.dedupeKeys([{action: "divider", name: "A"}, {action: "divider", name: "B"}], "action");
    assert.deepStrictEqual(out.map((e) => e.action), ["divider", "divider"]);
    assert.deepStrictEqual(out.map((e) => e.name), ["A", "B"]);
});

test("dedupeKeys: the input is not modified and unchanged items are returned as they were", () => {
    const input = [{action: "a", name: "1"}, {action: "a", name: "2"}, {action: "b"}];
    const before = JSON.stringify(input);
    const out = M.dedupeKeys(input, "action");
    assert.strictEqual(JSON.stringify(input), before);
    assert.strictEqual(out[0], input[0]);
    assert.strictEqual(out[2], input[2]);
    assert.deepStrictEqual(out[1], {action: "a_2", name: "2"});
});

test("dedupeKeys: works on controls by id, and an empty list gives an empty list", () => {
    const out = M.dedupeKeys([{id: "sw"}, {id: "sw"}], "id");
    assert.deepStrictEqual(out.map((d) => d.id), ["sw", "sw_2"]);
    assert.deepStrictEqual(M.dedupeKeys([], "id"), []);
});

// ---- plugin-backed run and read ----
test("describeRun: plugin and helper name, then the args JSON with keys sorted", () => {
    const run = {plugin: "tplinksmartplug", helper: "turn_on", args: {plugip: "10.0.0.5"}};
    assert.strictEqual(M.describeRun(run), "tplinksmartplug: turn_on {\"plugip\":\"10.0.0.5\"}");
    const sorted = {plugin: "p", api: "set", args: {b: 1, a: {d: 2, c: [3, {z: 1, y: 2}]}}};
    assert.strictEqual(M.describeRun(sorted), "p: set {\"a\":{\"c\":[3,{\"y\":2,\"z\":1}],\"d\":2},\"b\":1}");
});

test("describeRun: no args gives just the plugin and helper; data is used when there are no args", () => {
    assert.strictEqual(M.describeRun({plugin: "psucontrol", helper: "turn_psu_on"}), "psucontrol: turn_psu_on");
    assert.strictEqual(M.describeRun({plugin: "p", method: "go", data: {x: "y"}}), "p: go {\"x\":\"y\"}");
});

test("describeRun: no run gives empty text", () => {
    assert.strictEqual(M.describeRun(undefined), "");
    assert.strictEqual(M.describeRun(null), "");
});

test("describeRun: G-code lists its lines one per line", () => {
    assert.strictEqual(M.describeRun({gcode: ["G28", "M117 Hi"]}), "G-code: G28\nM117 Hi");
    assert.strictEqual(M.describeRun({gcode: "G28"}), "G-code: G28");
});

test("describeRun: an OctoPrint printer call names the call", () => {
    assert.strictEqual(M.describeRun({printer: "emergency_stop"}), "OctoPrint: emergency_stop");
});

test("describeRun: an event names the event, with its payload's sorted JSON only when it has keys", () => {
    assert.strictEqual(M.describeRun({event: "e", payload: {}}), "Event: e");
    assert.strictEqual(M.describeRun({event: "e"}), "Event: e");
    assert.strictEqual(M.describeRun({event: "e", payload: {b: 2, a: "x"}}), "Event: e {\"a\":\"x\",\"b\":2}");
});

test("hasStatusCheck: a shell status or a plugin read gives a check; neither gives none", () => {
    assert.strictEqual(M.hasStatusCheck({status: "laser-state"}), true);
    assert.strictEqual(M.hasStatusCheck({read: {plugin: "psucontrol", helper: "get_psu_state"}}), true);
    assert.strictEqual(M.hasStatusCheck({name: "Plain"}), false);
    assert.strictEqual(M.hasStatusCheck(undefined), false);
});

test("placementOf: a run entry that names a device is never a readout placement", () => {
    const entries = {disk_run: {action: "disk_run", run: {plugin: "p", helper: "h"}, device: "disk"}};
    assert.strictEqual(M.placementOf("disk", entries), "");
    assert.strictEqual(M.isPlacement(entries.disk_run), false);
    assert.strictEqual(M.isPlacement({device: "disk"}), true);
});

const elapsed = Date.now() - STARTED;
if (failures.length) {
    console.log("FAIL: " + failures.length + " of " + count + " tests");
    failures.forEach((f) => console.log("  - " + f));
    process.exit(1);
}
console.log("PASS: " + count + " tests (seed " + SEED + ", " + FUZZ_CASES + " fuzz cases per property, " + elapsed + " ms)");
