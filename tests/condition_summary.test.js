"use strict";
// Plain Node test for the condition sentence under the editor. Run: node tests/condition_summary.test.js
// Loads the pure model that holds conditionSummary (the editor's Condition calls it through window.SceModel).

const path = require("path");
const assert = require("assert");

const M = require(path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js", "systemcommandeditor_menu.js"));
const summary = M.conditionSummary;

// both modes x both effects, one state
assert.strictEqual(summary("only", ["ready"], "disable"), "Grayed out unless the printer is Ready.");
assert.strictEqual(summary("only", ["ready"], "hide"), "Hidden unless the printer is Ready.");
assert.strictEqual(summary("never", ["printing"], "disable"), "Grayed out while the printer is Printing.");
assert.strictEqual(summary("never", ["printing"], "hide"), "Hidden while the printer is Printing.");

// two states: "A or B", named in STATES order whatever order they were picked in
assert.strictEqual(summary("only", ["ready", "paused"], "disable"), "Grayed out unless the printer is Paused or Ready.");
assert.strictEqual(summary("never", ["error", "printing"], "hide"), "Hidden while the printer is Printing or Error.");

// three states: "A, B or C"
assert.strictEqual(summary("only", ["error", "ready", "offline"], "disable"),
    "Grayed out unless the printer is Ready, Offline or Error.");
assert.strictEqual(summary("never", ["paused", "printing", "ready"], "hide"),
    "Hidden while the printer is Printing, Paused or Ready.");

// all five, and duplicates in the stored list do not repeat a name
assert.strictEqual(summary("only", ["printing", "paused", "ready", "offline", "error"], "disable"),
    "Grayed out unless the printer is Printing, Paused, Ready, Offline or Error.");
assert.strictEqual(summary("only", ["ready", "ready"], "disable"), "Grayed out unless the printer is Ready.");

// no states picked: empty, the error line already asks for one
assert.strictEqual(summary("only", [], "disable"), "");
assert.strictEqual(summary("never", [], "hide"), "");
assert.strictEqual(summary("only", undefined, "disable"), "");

console.log("condition_summary: all assertions passed");
