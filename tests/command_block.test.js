"use strict";
// Node test for SceModel.commandBlock and SceModel.stateWord: the frozen 14-case table and a seeded fuzz harness that
// checks invariants 1 to 6 against an oracle written separately in this file. Run: node --test tests/
// (or node tests/command_block.test.js). A failing fuzz seed is printed so the case can be replayed.

const path = require("path");
const assert = require("assert");
const {test} = require("node:test");

const M = require(path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js", "systemcommandeditor_menu.js"));

const PRINTER_STATES = ["printing", "paused", "ready", "offline", "error"];
const CONTROL_WORDS = ["Idle", "Run", "Hold", "Jog", "Alarm", "Door", "Check", "Home", "Sleep", "Reset", "N/A"];
const CONTROL_NAME = "Grbl";

// ---- the frozen table (packets 20 to 24 frozen shape, control-state condition) ----
const ONLY_PRINTING_DISABLE = {mode: "only", states: ["printing"], effect: "disable"};
const ONLY_PRINTING_HIDE = {mode: "only", states: ["printing"], effect: "hide"};
const ONLY_ALARM_DISABLE = {mode: "only", states: ["Alarm"], effect: "disable"};
const ONLY_ALARM_HIDE = {mode: "only", states: ["Alarm"], effect: "hide"};
const NEVER_HOLD_DISABLE = {mode: "never", states: ["Hold"], effect: "disable"};
const NEVER_SLEEP_DISABLE = {mode: "never", states: ["Sleep"], effect: "disable"};

function entry(when, whenControl, group) {
    const e = {name: "Command"};
    if (when !== undefined) e.when = when;
    if (whenControl !== undefined) e.when_control = whenControl;
    if (group !== false) e.group = "grbl";
    return e;
}

const FROZEN = [
    // [#, entry, printer state, control word (null = unknown), expected {effect, reason} or null]
    [1, entry(), "ready", null, null],
    [2, entry(ONLY_PRINTING_DISABLE), "printing", "Idle", null],
    [3, entry(ONLY_PRINTING_DISABLE), "ready", "Idle", {effect: "disable", reason: "Only available while printing"}],
    [4, entry(undefined, ONLY_ALARM_DISABLE), "ready", "Alarm", null],
    [5, entry(undefined, ONLY_ALARM_DISABLE), "ready", "Idle", {effect: "disable", reason: "Not available while Grbl is Idle"}],
    [6, entry(undefined, ONLY_ALARM_DISABLE), "ready", null, null],
    [7, entry(undefined, ONLY_ALARM_DISABLE), "ready", "alarm", null],
    [8, entry(undefined, NEVER_HOLD_DISABLE), "ready", "Hold", {effect: "disable", reason: "Not available while Grbl is Hold"}],
    [9, entry(undefined, NEVER_SLEEP_DISABLE), "ready", "Idle", null],
    [10, entry(ONLY_PRINTING_HIDE, ONLY_ALARM_DISABLE), "ready", "Idle", {effect: "hide", reason: "Only available while printing"}],
    [11, entry(ONLY_PRINTING_DISABLE, ONLY_ALARM_HIDE), "ready", "Idle", {effect: "hide", reason: "Only available while printing"}],
    [12, entry(undefined, ONLY_ALARM_DISABLE, false), "ready", "Idle", null],
    [13, entry(undefined, NEVER_SLEEP_DISABLE), "ready", null, null],
    [14, entry(undefined, ONLY_ALARM_DISABLE), "offline", "N/A", {effect: "disable", reason: "Not available while Grbl is N/A"}],
];

test("the 14 frozen cases", () => {
    for (const row of FROZEN) {
        const [n, e, ps, word, expected] = row;
        const got = M.commandBlock(e, ps, word, CONTROL_NAME);
        assert.deepStrictEqual(got, expected, "case " + n);
    }
});

test("stateWord cuts at the first colon, trims, and reads empty as unknown", () => {
    assert.strictEqual(M.stateWord("Hold:0"), "Hold");
    assert.strictEqual(M.stateWord("  Idle  "), "Idle");
    assert.strictEqual(M.stateWord("Alarm: 3 extra: text"), "Alarm");
    assert.strictEqual(M.stateWord("N/A"), "N/A");
    assert.strictEqual(M.stateWord(""), null);
    assert.strictEqual(M.stateWord(":0"), null);
    assert.strictEqual(M.stateWord(null), null);
    assert.strictEqual(M.stateWord(undefined), null);
    assert.strictEqual(M.stateWord("Idle\nRun"), "Idle");
});

test("a built-in (core) command is never judged by its control", () => {
    const e = entry(undefined, ONLY_ALARM_DISABLE);
    assert.strictEqual(M.controlWordOf("core", {output: "Idle", error: null}), null);
    assert.strictEqual(M.controlWordOf("custom", {output: "Idle", error: null}), "Idle");
    assert.strictEqual(M.commandBlock(e, "ready", M.controlWordOf("core", {output: "Idle", error: null}), CONTROL_NAME), null);
    assert.deepStrictEqual(M.commandBlock(e, "ready", M.controlWordOf("custom", {output: "Idle", error: null}), CONTROL_NAME),
        {effect: "disable", reason: "Not available while Grbl is Idle"});
});

// ---- seeded fuzz harness ----
const SEED_BASE = 20261109;
const FUZZ_CASES = 600;

function makeRng(seed) {
    let s = seed >>> 0;
    return function () {
        s = (Math.imul(s, 1103515245) + 12345) >>> 0;
        return s / 4294967296;
    };
}

function pick(rng, list) {
    return list[Math.floor(rng() * list.length)];
}

function subset(rng, list) {
    return list.filter(() => rng() < 0.4);
}

function randomCase(rng) {
    const ps = pick(rng, PRINTER_STATES);
    const e = {name: "Command"};
    if (rng() < 0.7) {
        const when = {mode: pick(rng, ["only", "never"]), states: subset(rng, PRINTER_STATES)};
        const eff = pick(rng, ["disable", "hide", undefined]);
        if (eff !== undefined) when.effect = eff;
        e.when = when;
    }
    if (rng() < 0.7) {
        const wc = {mode: pick(rng, ["only", "never"]), states: subset(rng, CONTROL_WORDS)};
        const eff = pick(rng, ["disable", "hide", undefined]);
        if (eff !== undefined) wc.effect = eff;
        e.when_control = wc;
    }
    if (rng() < 0.5) e.group = "grbl";

    // the control word: a canonical word, a case change, a ":0" suffix, null, or empty
    const kind = pick(rng, ["canon", "canon", "upper", "lower", "suffix", "null", "empty"]);
    const canon = pick(rng, CONTROL_WORDS);
    let text;
    if (kind === "canon") text = canon;
    else if (kind === "upper") text = canon.toUpperCase();
    else if (kind === "lower") text = canon.toLowerCase();
    else if (kind === "suffix") text = canon + ":0";
    else if (kind === "null") text = null;
    else text = "";
    const word = M.stateWord(text);
    const canonical = kind === "null" || kind === "empty" ? null : canon;
    return {e: e, ps: ps, text: text, word: word, canonical: canonical};
}

// the oracle: written from the frozen rules, not from the implementation
function oracle(e, ps, canonical, shown) {
    const blocks = [];
    if (e.when) {
        const listed = e.when.states.indexOf(ps) !== -1;
        const blocked = e.when.mode === "only" ? !listed : listed;
        if (blocked) {
            const reason = e.when.mode === "only"
                ? "Only available while " + e.when.states.join(" or ")
                : "Not available while " + e.when.states.join(" or ");
            blocks.push({src: "printer", effect: e.when.effect === "disable" ? "disable" : "hide", reason: reason});
        }
    }
    if (e.group && e.when_control && canonical) {
        const lower = canonical.toLowerCase();
        const listed = e.when_control.states.some((s) => s.toLowerCase() === lower);
        const blocked = e.when_control.mode === "only" ? !listed : listed;
        if (blocked) {
            blocks.push({
                src: "control",
                effect: e.when_control.effect === "disable" ? "disable" : "hide",
                reason: "Not available while " + CONTROL_NAME + " is " + shown
            });
        }
    }
    if (blocks.length === 0) return null;
    const effect = blocks.some((b) => b.effect === "hide") ? "hide" : "disable";
    const first = blocks.find((b) => b.src === "printer") || blocks[0];
    return {effect: effect, reason: first.reason};
}

function withoutControl(e) {
    const copy = Object.assign({}, e);
    delete copy.when_control;
    return copy;
}

function checkCase(c) {
    const problems = [];
    const got = M.commandBlock(c.e, c.ps, c.word, CONTROL_NAME);
    const want = oracle(c.e, c.ps, c.canonical, c.word);
    // invariants 2 to 4, 6: the block and its effect and reason match the oracle, with word matching case-insensitive
    if (JSON.stringify(got) !== JSON.stringify(want)) {
        problems.push("block " + JSON.stringify(got) + " != oracle " + JSON.stringify(want));
    }
    // invariant 1: an unknown word never blocks through when_control
    if (c.canonical === null) {
        const printerOnly = M.commandBlock(withoutControl(c.e), c.ps, null, CONTROL_NAME);
        if (JSON.stringify(got) !== JSON.stringify(printerOnly)) problems.push("inv1: unknown word changed the block");
    }
    // invariant 5: when_control on an entry with no group is ignored
    if (!c.e.group) {
        const ungrouped = M.commandBlock(c.e, c.ps, c.word, CONTROL_NAME);
        const bare = M.commandBlock(withoutControl(c.e), c.ps, c.word, CONTROL_NAME);
        if (JSON.stringify(ungrouped) !== JSON.stringify(bare)) problems.push("inv5: ungrouped entry read when_control");
    }
    // invariant 6: the text form of a word judges the same as the bare word
    if (c.canonical !== null) {
        const colon = M.commandBlock(c.e, c.ps, M.stateWord(c.canonical + ":0"), CONTROL_NAME);
        const bare = M.commandBlock(c.e, c.ps, c.canonical, CONTROL_NAME);
        if (JSON.stringify(colon) !== JSON.stringify(bare)) problems.push("inv6: ':0' suffix changed the judgment");
    }
    return problems;
}

test("fuzz: at least 500 seeded cases, zero failing seeds", () => {
    const failing = [];
    let ran = 0;
    for (let i = 0; i < FUZZ_CASES; i++) {
        const seed = SEED_BASE + i;
        const rng = makeRng(seed);
        const c = randomCase(rng);
        ran += 1;
        const problems = checkCase(c);
        if (problems.length) failing.push("seed " + seed + ": " + problems.join("; "));
    }
    assert.ok(ran >= 500, "fuzz ran " + ran + " cases");
    assert.deepStrictEqual(failing, [], "failing seeds (" + failing.length + " of " + ran + ")");
});
