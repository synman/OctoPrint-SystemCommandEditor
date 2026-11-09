"use strict";
// Node test for SceModel.isLink and SceModel.linkText: a link command is a system.actions entry with a url and no
// command or run. Run: node --test tests/ (or node tests/link_command.test.js).

const path = require("path");
const assert = require("assert");
const {test} = require("node:test");

const M = require(path.join(__dirname, "..", "octoprint_systemcommandeditor", "static", "js", "systemcommandeditor_menu.js"));

const URL = "http://printer.local/webcam/?action=snapshot";

test("a standalone link command is a link and its run text is Link: <url>", () => {
    const entry = {action: "webcam_snapshot", name: "Webcam snapshot", url: URL, icon: "fa-solid fa-camera"};
    assert.strictEqual(M.isLink(entry), true);
    assert.strictEqual(M.linkText(entry), "Link: " + URL);
});

test("a link command inside a control (role run, a group) is still a link", () => {
    const entry = {action: "open", name: "Open", url: URL, group: "cam", role: "run"};
    assert.strictEqual(M.isLink(entry), true);
    assert.strictEqual(M.linkText(entry), "Link: " + URL);
});

test("a shell command, a run call and a url beside a command are not links", () => {
    assert.strictEqual(M.isLink({action: "a", name: "A", command: "echo a"}), false);
    assert.strictEqual(M.isLink({action: "b", name: "B", run: {printer: "pause"}}), false);
    assert.strictEqual(M.isLink({action: "c", name: "C", url: URL, command: "echo c"}), false);
    assert.strictEqual(M.isLink({action: "d", name: "D", url: URL, run: {printer: "pause"}}), false);
    assert.strictEqual(M.linkText({action: "a", name: "A", command: "echo a"}), "");
});

test("an empty or missing url is not a link, and no entry is a link", () => {
    assert.strictEqual(M.isLink({action: "e", name: "E", url: ""}), false);
    assert.strictEqual(M.isLink({action: "f", name: "F"}), false);
    assert.strictEqual(M.isLink(undefined), false);
    assert.strictEqual(M.isLink(null), false);
    assert.strictEqual(M.linkText(undefined), "");
});
