"use strict";
// Node test for SceModel.PressGate: a redraw requested while a press is down is held until the release, and the
// release reports the held draw once. Run: node --test tests/ (or node --test tests/press_gate.test.js).

const path = require("path");
const assert = require("assert");
const {test} = require("node:test");

const M = require(path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js", "systemcommandeditor_menu.js"));

test("a request while not down draws now and owes nothing", () => {
    const g = new M.PressGate();
    assert.strictEqual(g.request(), true);
    assert.strictEqual(g.owed, false);
    assert.strictEqual(g.release(), false);
});

test("a request while down is held, and the release reports it once", () => {
    const g = new M.PressGate();
    g.press();
    assert.strictEqual(g.request(), false);
    assert.strictEqual(g.request(), false);
    assert.strictEqual(g.release(), true);
    assert.strictEqual(g.release(), false);
});

test("a press with no request releases with no owed draw", () => {
    const g = new M.PressGate();
    g.press();
    assert.strictEqual(g.release(), false);
    assert.strictEqual(g.request(), true);
});

test("release when not down returns false and changes nothing", () => {
    const g = new M.PressGate();
    assert.strictEqual(g.release(), false);
    assert.strictEqual(g.down, false);
    assert.strictEqual(g.owed, false);
    g.press();
    g.request();
    assert.strictEqual(g.release(), true);
    assert.strictEqual(g.release(), false);
    assert.strictEqual(g.owed, false);
});

test("a second press while down changes nothing: still one owed draw", () => {
    const g = new M.PressGate();
    g.press();
    g.request();
    g.press();
    assert.strictEqual(g.down, true);
    assert.strictEqual(g.release(), true);
});

test("a press held across a request leaves no owed draw for the next press", () => {
    const g = new M.PressGate();
    g.press();
    g.request();
    g.release();
    g.press();
    assert.strictEqual(g.release(), false);
});

// reference model written from the contract, not from the implementation
function makeRef() {
    return {down: false, requested: false};
}

function refApply(ref, op) {
    if (op === "press") {
        ref.down = true;
        return undefined;
    }
    if (op === "release") {
        if (!ref.down) return false;
        ref.down = false;
        const r = ref.requested;
        ref.requested = false;
        return r;
    }
    // request
    if (ref.down) {
        ref.requested = true;
        return false;
    }
    return true;
}

function makeRng(seed) {
    let s = seed >>> 0;
    return function () {
        s = (Math.imul(s, 1103515245) + 12345) >>> 0;
        return s / 4294967296;
    };
}

const OPS = ["press", "release", "request"];
const SEEDS = 500;

test("fuzz: PressGate matches the reference model on I1-I4 after every call (500 seeds)", () => {
    for (let seed = 1; seed <= SEEDS; seed++) {
        const rng = makeRng(seed);
        const g = new M.PressGate();
        const ref = makeRef();
        const len = 1 + Math.floor(rng() * 40);
        for (let i = 0; i < len; i++) {
            const op = OPS[Math.floor(rng() * OPS.length)];
            const wasDown = ref.down;
            const wasRequested = ref.requested;
            let got;
            if (op === "press") g[op]();
            else got = g[op]();
            const want = refApply(ref, op);
            const where = "seed " + seed + " call " + i + " op " + op;
            // I1: while down, request never returns true
            if (op === "request" && wasDown) assert.notStrictEqual(got, true, where + " I1");
            // I2: release is true exactly when a request happened since the matching press
            if (op === "release") assert.strictEqual(got, want, where + " I2");
            // I3: while not down, request returns true and owed stays false
            if (op === "request" && !wasDown) {
                assert.strictEqual(got, true, where + " I3");
                assert.strictEqual(g.owed, false, where + " I3 owed");
            }
            // I4: a press while down changes nothing
            if (op === "press" && wasDown) assert.strictEqual(wasRequested, ref.requested, where + " I4");
            if (op === "request") assert.strictEqual(got, want, where + " request result");
            assert.strictEqual(g.down, ref.down, where + " down");
            assert.strictEqual(g.owed, ref.requested, where + " owed");
        }
    }
});
