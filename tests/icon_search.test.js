// Plain Node test for the icon suggestion search. Loads the real generated icon list and the search module.
// Run: node tests/icon_search.test.js   (exits non-zero on any failure)
"use strict";

const path = require("path");
const assert = require("assert");

const root = path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js");

global.window = {};
require(path.join(root, "systemcommandeditor_icons.js"));
const icons = global.window.SceIcons;
const {search} = require(path.join(root, "systemcommandeditor_iconsearch.js"));

const cls = (text, limit) => search(icons, text, limit).items.map((i) => i.cls);

const failures = [];
function check(name, fn) {
    try {
        fn();
    } catch (err) {
        failures.push(name + ": " + err.message);
    }
}

const started = Date.now();

check("icon list loaded", () => {
    assert.ok(Array.isArray(icons) && icons.length > 1000, "expected the generated icon list");
});

check("trash ranks solid trash before solid trash-can", () => {
    const list = cls("trash");
    assert.ok(list.indexOf("fa-solid fa-trash") !== -1, "fa-solid fa-trash missing");
    assert.ok(list.indexOf("fa-solid fa-trash-can") !== -1, "fa-solid fa-trash-can missing");
    assert.ok(list.indexOf("fa-solid fa-trash") < list.indexOf("fa-solid fa-trash-can"), "wrong order");
});

check("trash includes regular trash-can", () => {
    assert.ok(cls("trash").indexOf("fa-regular fa-trash-can") !== -1);
});

check("fa-brands git returns only brands and includes github", () => {
    const list = cls("fa-brands git");
    assert.ok(list.length > 0);
    for (const c of list) assert.ok(c.indexOf("fa-brands ") === 0, "non-brands item: " + c);
    assert.ok(list.indexOf("fa-brands fa-github") !== -1, "fa-brands fa-github missing");
});

check("fa-solid fa-rotate ranks fa-solid fa-rotate first", () => {
    assert.strictEqual(cls("fa-solid fa-rotate")[0], "fa-solid fa-rotate");
});

check("search word garbage finds trash-can", () => {
    const names = search(icons, "garbage", 200).items.map((i) => i.name);
    assert.ok(names.indexOf("trash-can") !== -1, "trash-can not found by label/word");
});

check("empty text returns no items", () => {
    const r = search(icons, "", 40);
    assert.deepStrictEqual(r.items, []);
    assert.strictEqual(r.more, 0);
    assert.deepStrictEqual(search(icons, "   ", 40).items, []);
});

check("limit caps items and more counts the rest", () => {
    const all = search(icons, "a", 100000);
    const capped = search(icons, "a", 5);
    assert.strictEqual(capped.items.length, 5);
    assert.strictEqual(capped.more, all.items.length - 5);
    assert.deepStrictEqual(capped.items, all.items.slice(0, 5));
});

check("default limit is 40", () => {
    const r = search(icons, "a");
    assert.strictEqual(r.items.length, 40);
    assert.ok(r.more > 0);
});

check("every cls is well formed", () => {
    const re = /^fa-(solid|regular|brands) fa-[a-z0-9-]+$/;
    for (const c of cls("a", 100000)) assert.ok(re.test(c), "bad cls: " + c);
});

check("a style an icon lacks is never offered", () => {
    const list = cls("github", 100000);
    assert.ok(list.indexOf("fa-brands fa-github") !== -1);
    assert.strictEqual(list.indexOf("fa-regular fa-github"), -1, "github has no regular style");
    assert.strictEqual(list.indexOf("fa-solid fa-github"), -1, "github has no solid style");
});

check("bare style word is a filter only as the first of two or more tokens", () => {
    const first = search(icons, "solid trash", 100000).items;
    assert.ok(first.length > 0, "solid trash should find icons");
    for (const i of first) assert.strictEqual(i.style, "solid");
    // as a second token "solid" is part of the query (trash-solid), which matches nothing
    assert.strictEqual(search(icons, "trash solid", 100000).items.length, 0);
    // alone, "solid" is a query and not a filter; it has no name or label match here
    assert.strictEqual(search(icons, "solid", 100000).items.length, 0);
});

check("style-only query lists that style by name", () => {
    const list = cls("fa-brands", 100000);
    assert.ok(list.length > 0);
    for (const c of list) assert.ok(c.indexOf("fa-brands ") === 0);
});

check("a name that starts with the query ranks above a shorter one that only contains it", () => {
    const list = cls("bell", 100000);
    const prefix = list.indexOf("fa-solid fa-bell-concierge");
    const inside = list.indexOf("fa-solid fa-dumbbell");
    assert.ok(prefix !== -1 && inside !== -1, "both bell-concierge and dumbbell should match");
    assert.ok(prefix < inside, "bell-concierge at " + prefix + ", dumbbell at " + inside);
});

const elapsed = Date.now() - started;
check("runs under 2 seconds", () => {
    assert.ok(elapsed < 2000, "took " + elapsed + " ms");
});

if (failures.length) {
    console.error("FAIL " + failures.length + " check(s):");
    for (const f of failures) console.error("  - " + f);
    process.exit(1);
}
console.log("PASS icon_search (" + elapsed + " ms)");
