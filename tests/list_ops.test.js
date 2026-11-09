"use strict";
// Node test for the custom list's operations: SceModel.members with a saved order, the section-block helpers on
// hand-made lists, and a seeded fuzz harness over random lists. The oracle below is written separately from the model
// and checks invariants 1, 2, 4 and 5 of the list model. Run: node --test tests/ (or node tests/list_ops.test.js).
// A failing seed is printed so the case can be replayed.

const path = require("path");
const assert = require("assert");
const {test} = require("node:test");

const M = require(path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js", "systemcommandeditor_menu.js"));

const FUZZ_SEEDS = 600;
const NONE = () => "";

// a small seeded generator (mulberry32), so every seed replays the same list
function generator(seed) {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

// a random list: dividers, plain commands, members of the listed controls, and readout placements
function randomList(rand, controlIds) {
    const size = 2 + Math.floor(rand() * 13);
    const list = [];
    for (let i = 0; i < size; i++) {
        const r = rand();
        if (r < 0.25) list.push({action: "divider", name: "S" + i});
        else if (r < 0.55) {
            const group = controlIds[Math.floor(rand() * controlIds.length)];
            list.push({action: "m" + i, name: "Member " + i, group: group, role: "run"});
        } else if (r < 0.7) list.push({action: "p" + i, name: "Placement " + i, device: "r" + i});
        else list.push({action: "c" + i, name: "Command " + i});
    }
    return list;
}

// the control an entry drives, for the controls the list defines
function controlOf(controlIds) {
    return (entry) => (entry.group && controlIds.indexOf(entry.group) >= 0 ? entry.group : "");
}

// ORACLE (independent of SceModel): the list's move units in first-appearance order, with a control's members together
function oracleUnits(list, controlIds) {
    const out = [];
    const byControl = {};
    for (const entry of list) {
        const id = entry.group && controlIds.indexOf(entry.group) >= 0 ? entry.group : null;
        if (id === null) {
            out.push([entry]);
        } else if (byControl[id]) {
            byControl[id].push(entry);
        } else {
            byControl[id] = [entry];
            out.push(byControl[id]);
        }
    }
    return out;
}

const isHeading = (unit) => unit.length === 1 && unit[0].action === "divider";

// ORACLE: each span is one block: a heading with the units after it up to the next heading, or a lone unit before any
function oracleSpans(units) {
    const spans = [];
    let i = 0;
    while (i < units.length) {
        if (isHeading(units[i])) {
            let j = i + 1;
            while (j < units.length && !isHeading(units[j])) j++;
            spans.push([i, j]);
            i = j;
        } else {
            spans.push([i, i + 1]);
            i++;
        }
    }
    return spans;
}

const flat = (units) => units.reduce((all, unit) => all.concat(unit), []);

// ORACLE: the list after a heading's block trades places with its neighbour (step -1 up, +1 down)
function oracleMoveHeading(list, controlIds, heading, step) {
    const units = oracleUnits(list, controlIds);
    const spans = oracleSpans(units);
    const h = units.findIndex((unit) => unit[0] === heading && unit.length === 1);
    const b = spans.findIndex((span) => span[0] === h);
    const j = b + step;
    if (j < 0 || j >= spans.length) return flat(units);
    const order = spans.map((_, k) => k);
    order[b] = j;
    order[j] = b;
    return flat(order.reduce((acc, k) => acc.concat(units.slice(spans[k][0], spans[k][1])), []));
}

// ORACLE: the heading's block lands before the target's block when it was below it, after when above
function oracleDropHeading(list, controlIds, heading, target) {
    const units = oracleUnits(list, controlIds);
    const spans = oracleSpans(units);
    const h = units.findIndex((unit) => unit[0] === heading && unit.length === 1);
    const t = units.findIndex((unit) => unit.indexOf(target) >= 0);
    const b = spans.findIndex((span) => span[0] === h);
    const g = spans.findIndex((span) => span[0] <= t && t < span[1]);
    if (b === g) return flat(units);
    const order = spans.map((_, k) => k).filter((k) => k !== b);
    const at = order.indexOf(g);
    order.splice(b > g ? at : at + 1, 0, b);
    return flat(order.reduce((acc, k) => acc.concat(units.slice(spans[k][0], spans[k][1])), []));
}

// invariant 1: the result holds exactly the input's entries, each once
function samePermutation(input, output) {
    if (input.length !== output.length) return false;
    const want = new Set(input);
    return output.every((entry) => want.delete(entry)) && want.size === 0;
}

// invariant 5: every control's members sit in one run
function controlsContiguous(output, controlIds) {
    return controlIds.every((id) => {
        const at = output.map((entry, i) => (entry.group === id ? i : -1)).filter((i) => i >= 0);
        return at.length === 0 || at[at.length - 1] - at[0] + 1 === at.length;
    });
}

// invariant 2: every block of the input is still one run in the same internal order
function blocksIntact(input, output, controlIds) {
    const spans = oracleSpans(oracleUnits(input, controlIds));
    const units = oracleUnits(input, controlIds);
    return spans.every((span) => {
        const run = flat(units.slice(span[0], span[1]));
        const first = output.indexOf(run[0]);
        return first >= 0 && run.every((entry, k) => output[first + k] === entry);
    });
}

function describe(seed, what, list) {
    return "seed " + seed + " (" + what + "): " + list.map((e) => e.action).join(" ");
}

test("members walks custom entries in the saved order, then core", () => {
    const entries = {
        b: {action: "b", name: "B", group: "d", role: "run"},
        a: {action: "a", name: "A", group: "d", role: "run"},
        z: {action: "z", name: "Z", group: "d", role: "run"}
    };
    const core = {k: {action: "k", name: "K", group: "d", role: "run"}};
    assert.deepStrictEqual(M.members("d", entries, core, ["z", "b", "a"]).runs.map((r) => r.action), ["z", "b", "a", "k"]);
    assert.deepStrictEqual(M.members("d", entries, core).runs.map((r) => r.action), ["b", "a", "z", "k"]);
    // a custom key the saved order leaves out follows in table order
    assert.deepStrictEqual(M.members("d", entries, core, ["a"]).runs.map((r) => r.action), ["a", "b", "z", "k"]);
});

const sample = () => [
    {action: "divider", name: "A"}, {action: "x"},
    {action: "divider", name: "B"}, {action: "y"},
    {action: "divider", name: "C"}, {action: "z"}
];

test("heading up swaps its block with the block above", () => {
    const list = sample();
    const out = M.moveBlock(list, NONE, list[2], -1);
    assert.deepStrictEqual(out.map((e) => e.name || e.action), ["B", "y", "A", "x", "C", "z"]);
});

test("heading down swaps its block with the block below", () => {
    const list = sample();
    const out = M.moveBlock(list, NONE, list[0], 1);
    assert.deepStrictEqual(out.map((e) => e.name || e.action), ["B", "y", "A", "x", "C", "z"]);
});

test("the first heading moving up and the last moving down change nothing", () => {
    const list = sample();
    assert.deepStrictEqual(M.moveBlock(list, NONE, list[0], -1), list);
    assert.deepStrictEqual(M.moveBlock(list, NONE, list[4], 1), list);
    assert.strictEqual(M.canMoveBlock(list, NONE, list[0], -1), false);
    assert.strictEqual(M.canMoveBlock(list, NONE, list[4], 1), false);
    assert.strictEqual(M.canMoveBlock(list, NONE, list[2], -1), true);
});

test("dragging a heading up lands its block before the target's block", () => {
    const list = sample();
    const out = M.dropBlock(list, NONE, list[4], list[1]);
    assert.deepStrictEqual(out.map((e) => e.name || e.action), ["C", "z", "A", "x", "B", "y"]);
});

test("dragging a heading down lands its block after the target's block", () => {
    const list = sample();
    const out = M.dropBlock(list, NONE, list[0], list[3]);
    assert.deepStrictEqual(out.map((e) => e.name || e.action), ["B", "y", "A", "x", "C", "z"]);
});

test("a control's members move with their heading as one unit", () => {
    const controlIds = ["c1"];
    const list = [
        {action: "divider", name: "A"},
        {action: "m1", name: "M1", group: "c1", role: "run"},
        {action: "x", name: "X"},
        {action: "m2", name: "M2", group: "c1", role: "run"},
        {action: "divider", name: "B"},
        {action: "y", name: "Y"}
    ];
    const out = M.moveBlock(list, controlOf(controlIds), list[4], -1);
    assert.deepStrictEqual(out.map((e) => e.name), ["B", "Y", "A", "M1", "M2", "X"]);
});

test("fuzz: block moves and drags keep the list a permutation with its blocks and controls intact (" + FUZZ_SEEDS * 2 + " cases)", () => {
    const failures = [];
    let cases = 0;
    for (let seed = 1; seed <= FUZZ_SEEDS; seed++) {
        const rand = generator(seed);
        const controlIds = ["c1", "c2", "c3"];
        const list = randomList(rand, controlIds);
        const headings = list.filter((e) => e.action === "divider");
        const deviceIdOf = controlOf(controlIds);
        // one heading move, up or down
        if (headings.length) {
            const heading = headings[Math.floor(rand() * headings.length)];
            const step = rand() < 0.5 ? -1 : 1;
            const out = M.moveBlock(list, deviceIdOf, heading, step);
            const want = oracleMoveHeading(list, controlIds, heading, step);
            cases++;
            const ok = samePermutation(list, out) && blocksIntact(list, out, controlIds) &&
                controlsContiguous(out, controlIds) && out.every((e, i) => e === want[i]) && out.length === want.length;
            if (!ok) failures.push(describe(seed, "move " + step, list));
        }
        // one heading drag onto a random entry
        if (headings.length) {
            const heading = headings[Math.floor(rand() * headings.length)];
            const target = list[Math.floor(rand() * list.length)];
            const out = M.dropBlock(list, deviceIdOf, heading, target);
            const want = oracleDropHeading(list, controlIds, heading, target);
            cases++;
            const ok = samePermutation(list, out) && blocksIntact(list, out, controlIds) &&
                controlsContiguous(out, controlIds) && out.every((e, i) => e === want[i]) && out.length === want.length;
            if (!ok) failures.push(describe(seed, "drag", list));
        }
    }
    assert.ok(cases >= 500, "fuzz ran " + cases + " cases");
    assert.deepStrictEqual(failures, [], "failing seeds: " + failures.join("; "));
});
