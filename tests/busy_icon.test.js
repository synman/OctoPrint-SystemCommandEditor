// Plain Node test for the busy-icon list and helper. Loads the real generated icon list and the System menu model.
// Run: node tests/busy_icon.test.js   (exits non-zero on any failure)
"use strict";

const path = require("path");
const assert = require("assert");

const root = path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js");

global.window = {};
require(path.join(root, "systemcommandeditor_icons.js"));
const icons = global.window.SceIcons;
const SceModel = require(path.join(root, "systemcommandeditor_menu.js"));

const failures = [];
function check(name, fn) {
    try {
        fn();
    } catch (err) {
        failures.push(name + ": " + err.message);
    }
}

check("BUSY_ICONS is a non-empty list", () => {
    assert.ok(Array.isArray(SceModel.BUSY_ICONS));
    assert.ok(SceModel.BUSY_ICONS.length > 0);
});

check("every busy icon is a solid class string 'fa-solid fa-<name>'", () => {
    for (const cls of SceModel.BUSY_ICONS) {
        assert.ok(/^fa-solid fa-[a-z0-9-]+$/.test(cls), "not solid: " + cls);
    }
});

check("every busy icon exists as a solid entry in the Font Awesome list", () => {
    const solid = new Map(icons.map((i) => [i[0], i[1]]));
    for (const cls of SceModel.BUSY_ICONS) {
        const name = cls.split(" ")[1].slice(3);
        assert.ok(solid.has(name), "missing from FA list: " + name);
        assert.ok(solid.get(name).indexOf("s") !== -1, "no solid style: " + name);
    }
});

check("busyClass adds the spin class to the chosen icon", () => {
    assert.strictEqual(SceModel.busyClass("fa-solid fa-gear"), "fa-solid fa-gear fa-spin");
});

check("busyClass does not repeat fa-spin", () => {
    assert.strictEqual(SceModel.busyClass("fa-solid fa-gear fa-spin"), "fa-solid fa-gear fa-spin");
});

check("busyClass falls back to the default spinner when unset or empty", () => {
    assert.strictEqual(SceModel.busyClass(undefined), "fa-solid fa-spinner fa-spin");
    assert.strictEqual(SceModel.busyClass(""), "fa-solid fa-spinner fa-spin");
});

if (failures.length) {
    console.error(failures.join("\n"));
    process.exit(1);
}
console.log("busy_icon: all checks passed");
