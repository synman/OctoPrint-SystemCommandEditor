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
    assert.deepStrictEqual(m, {source: null, on: null, off: null, restart: null, safe_restart: null, choices: []});
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
    assert.deepStrictEqual(M.switchView(m, "unknown").buttons.map((b) => b.kind), ["restart", "safe_restart", "off", "on"]);
    assert.deepStrictEqual(M.switchView({restart: "r", choices: []}, "on").buttons.map((b) => b.kind), ["restart"]);
});

// ---- switchView ----
test("switchView: buttons in order restart, off, on, each only when the member exists", () => {
    const m = {on: "a_on", off: "a_off", restart: "a_restart", choices: []};
    const v = M.switchView(m, "unknown");
    assert.deepStrictEqual(v.buttons.map((b) => b.kind), ["restart", "off", "on"]);
});

test("switchView: state on hides the off button, state off hides the on button", () => {
    const m = {on: "a_on", off: "a_off", restart: "a_restart", choices: []};
    assert.deepStrictEqual(M.switchView(m, "on").buttons.map((b) => b.kind), ["restart", "off"]);
    assert.deepStrictEqual(M.switchView(m, "off").buttons.map((b) => b.kind), ["restart", "on"]);
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

test("switchView: nostatus and unknown give both the on and the off button", () => {
    const m = {on: "a_on", off: "a_off", restart: null, choices: []};
    assert.deepStrictEqual(M.switchView(m, "nostatus").buttons.map((b) => b.kind), ["off", "on"]);
    assert.deepStrictEqual(M.switchView(m, "unknown").buttons.map((b) => b.kind), ["off", "on"]);
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
            assert.strictEqual(m[b.kind], b.action, "seed " + SEED + " case " + i);
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

const elapsed = Date.now() - STARTED;
if (failures.length) {
    console.log("FAIL: " + failures.length + " of " + count + " tests");
    failures.forEach((f) => console.log("  - " + f));
    process.exit(1);
}
console.log("PASS: " + count + " tests (seed " + SEED + ", " + FUZZ_CASES + " fuzz cases per property, " + elapsed + " ms)");
