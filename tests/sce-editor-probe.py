#!/usr/bin/env python3
"""Browser probe for the System Command Editor settings editor (devices, roles, conditions, consent preview).

Run: ~/.virtualenvs/main/bin/python tests/sce-editor-probe.py   (env SCE_BASE, default http://127.0.0.1:5097)

GET /api/settings is fetched once through the route and answered with a fixture (two custom commands, a divider
named Webcam, no devices). POST /api/settings is captured and answered 200 with the posted settings merged into the
fixture; it is never forwarded, so no server setting changes. Prints one PASS or FAIL line per check and exits
non-zero on any FAIL.
"""
import copy
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SCE_BASE", "http://127.0.0.1:5097")
SETTINGS_URL = "**/api/settings"
REAUTH_URL = "**/api/settings/reauthReq"
COMMANDS_URL = "**/api/system/commands"
# built-in commands the core editor needs; a rig whose server.commands does not set them lists none
CORE_FIXTURE = [
    {"action": "shutdown", "name": "Shutdown system", "confirm": "Shutdown the system?"},
    {"action": "reboot", "name": "Reboot system", "confirm": "Reboot the system?"},
]
STATE_PICK = "label:has-text('{}') input"

FIXTURE_ACTIONS = [
    {"action": "divider", "name": "Webcam"},
    {"action": "laser_on_cmd", "name": "Laser on", "command": "echo on", "icon": "fa-solid fa-lightbulb"},
    {"action": "laser_off_cmd", "name": "Laser off", "command": "echo off", "icon": "fa-solid fa-circle"},
]

results = []
posts = []
state = {"base": None, "current": None}
page = None


def check(name, ok, detail=""):
    results.append(bool(ok))
    suffix = "" if ok or not detail else " -- " + detail
    print(("PASS: " if ok else "FAIL: ") + name + suffix, flush=True)


def deep_merge(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def handle_settings(route, request):
    if request.method == "POST":
        body = request.post_data_json or {}
        posts.append(copy.deepcopy(body))
        deep_merge(state["current"], body)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(state["current"]))
        return
    if state["base"] is None:
        # the real response, once; only the parts the editor reads are replaced
        real = route.fetch().json()
        real.setdefault("system", {})["actions"] = copy.deepcopy(FIXTURE_ACTIONS)
        real.setdefault("plugins", {}).setdefault("systemcommandeditor", {})["devices"] = []
        state["base"] = real
        state["current"] = copy.deepcopy(real)
    route.fulfill(status=200, content_type="application/json", body=json.dumps(state["current"]))


def handle_commands(route, request):
    if request.method != "GET":
        route.continue_()
        return
    real = route.fetch().json()
    core = list(real.get("core") or [])
    present = {c.get("action") for c in core}
    core += [c for c in CORE_FIXTURE if c["action"] not in present]
    real["core"] = core
    route.fulfill(status=200, content_type="application/json", body=json.dumps(real))


def visible(selector):
    return page.locator(selector).first.is_visible()


def wait_visible(selector, timeout=8000):
    page.locator(selector).first.wait_for(state="visible", timeout=timeout)


def wait_hidden(selector, timeout=8000):
    page.locator(selector).first.wait_for(state="hidden", timeout=timeout)


def command_row(name):
    return page.locator("#settings_plugin_systemcommandeditor .sce-command", has_text=name).first


def core_row(action):
    index = page.evaluate(
        """(action) => [...document.querySelectorAll('#settings_plugin_systemcommandeditor .sce-core-list .sce-command')]
            .findIndex(row => ko.dataFor(row).action === action)""", action)
    if index < 0:
        return None
    return page.locator("#settings_plugin_systemcommandeditor .sce-core-list .sce-command").nth(index)


def open_settings():
    tab = 'a[href="#settings_plugin_systemcommandeditor"]'
    if not visible(tab):
        page.click("#navbar_show_settings")
        wait_visible(tab)
    page.click(tab)
    wait_visible("#settings_plugin_systemcommandeditor .sce-add-buttons")


def save_settings():
    if os.environ.get("SCE_DEBUG"):
        print("debug state: %s" % page.evaluate("""() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const p = vm.settingsViewModel.settings.plugins.systemcommandeditor;
            return {perm: vm.loginState.hasPermission(vm.access.permissions.SETTINGS),
                    devices: ko.toJS(p.devices), dirty: vm._dirty, coreDirty: vm._coreDirty};
        }"""), flush=True)
    before = len(posts)
    page.click('[data-test-id="settings-save"]')
    end = time.time() + 8
    while len(posts) == before and time.time() < end:
        page.wait_for_timeout(100)
    page.wait_for_timeout(800)  # the settings dialog closes after a successful save
    return posts[-1] if len(posts) > before else None


def modal_done(modal):
    page.locator(modal + " .modal-footer .btn-primary").click()
    wait_hidden(modal)


def add_switch_device(name):
    page.get_by_role("button", name="Add device").click()
    wait_visible("#sce-device-editor")
    page.fill("#sce-device-name", name)
    modal_done("#sce-device-editor")


def edit_command(name, device=None, role=None):
    command_row(name).locator('button[title="Edit"]').click()
    wait_visible("#sce-editor")
    if device is not None:
        page.select_option("#sce-editor-device", label=device)
    if role is not None:
        page.select_option("#sce-editor .sce-role", value=role)


def core_actions():
    return page.evaluate(
        """() => [...document.querySelectorAll('#settings_plugin_systemcommandeditor .sce-core-list .sce-command')]
            .map(row => ko.dataFor(row).action)""")


def core_edit(action, device, role, condition=False):
    row = core_row(action)
    if row is None:
        print("core actions on this rig: %s" % core_actions(), flush=True)
        return False
    row.locator('button[title^="Edit icon"]').click()
    wait_visible("#sce-editor")
    page.select_option("#sce-editor-device", label=device)
    page.select_option("#sce-editor .sce-role", value=role)
    if condition:
        page.check("#sce-editor label:has-text('Only allow in some printer states') input")
        page.select_option("#sce-editor .sce-condition select >> nth=0", value="never")
        page.check("#sce-editor .sce-state-picks " + STATE_PICK.format("Printing"))
    modal_done("#sce-editor")
    return True


def first_actions(body):
    return {a.get("action"): a for a in ((body.get("system") or {}).get("actions") or []) if isinstance(a, dict)}


def run_probe():
    global page
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context().new_page()
        page.on("pageerror", lambda error: print("page error: %s" % error, flush=True))
        page.on("console", lambda msg: print("console %s: %s" % (msg.type, msg.text), flush=True)
                if msg.type == "error" else None)
        page.route(SETTINGS_URL, handle_settings)
        page.route(COMMANDS_URL, handle_commands)
        # OctoPrint asks for a fresh login before it posts system.actions and opens a password dialog. The probe has
        # no password, so the client is told there are no reauthentication requirements. The POST is still answered
        # by the probe, never forwarded, so the server enforces nothing here and no setting changes.
        page.route(REAUTH_URL, lambda route, request: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"requirements": {}})))
        if os.environ.get("SCE_DEBUG"):
            page.on("request", lambda req: print("request %s %s" % (req.method, req.url), flush=True)
                    if "/api/" in req.url else None)

        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_function("typeof ko !== 'undefined' && document.querySelector('#navbar_show_settings') !== null",
                               timeout=20000)
        open_settings()

        # 5a. fresh install: the fixture carries no condition anywhere
        base = state["base"] or {}
        plugin_base = (base.get("plugins") or {}).get("systemcommandeditor") or {}
        with_when = [a.get("action") for a in (base.get("system") or {}).get("actions") or []
                     if isinstance(a, dict) and a.get("when")]
        with_when += ["core." + k for k, v in (plugin_base.get("core") or {}).items()
                      if isinstance(v, dict) and v.get("when")]
        with_when += ["device." + d.get("id", "") for d in plugin_base.get("devices") or [] if d.get("when")]
        check("fresh fixture carries no condition", not with_when, json.dumps(with_when))

        # Phase A: a switch device with two custom commands, a live consent preview, one condition
        add_switch_device("Laser")
        check("switch device added to the device list",
              page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="Laser").count() == 1)

        edit_command("Laser on", device="Laser", role="on")
        modal_done("#sce-editor")
        edit_command("Laser off", device="Laser", role="off")
        modal_done("#sce-editor")
        check("badges show the device and role on both commands",
              command_row("Laser on").locator(".sce-member", has_text="Laser · On").count() == 1 and
              command_row("Laser off").locator(".sce-member", has_text="Laser · Off").count() == 1)

        # a second On on the same switch is blocked, and Done re-enables once it is Off
        edit_command("Laser off", role="on")
        page.wait_for_timeout(200)
        block_text = page.locator("#sce-editor .help-block:visible", has_text="already has that role").count()
        done_disabled = page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("a second On on the same switch is blocked", block_text == 1 and done_disabled,
              "message=%s done_disabled=%s" % (block_text, done_disabled))
        page.select_option("#sce-editor .sce-role", value="off")
        page.wait_for_timeout(200)
        check("Done enables again once the role is Off",
              not page.locator("#sce-editor .modal-footer .btn-primary").is_disabled())
        modal_done("#sce-editor")

        # 4. live consent preview: the pre follows the command text
        edit_command("Laser off")
        page.check("#sce-editor label:has-text('Ask for confirmation') input")
        wait_visible("#sce-editor .sce-preview-command")
        pre = page.locator("#sce-editor .sce-preview-command").inner_text().strip()
        intro = page.locator("#sce-editor .sce-preview").inner_text()
        check("consent preview shows the command in a pre",
              pre == "echo off" and "The confirmation will show your message and this command:" in intro,
              "pre=%r" % pre)
        page.fill("#sce-editor-command", "echo one")
        page.wait_for_timeout(200)
        live = page.locator("#sce-editor .sce-preview-command").inner_text().strip()
        check("consent preview follows a changed command live", live == "echo one", "pre=%r" % live)
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")

        edit_command("Laser on")
        page.check("#sce-editor label:has-text('Only allow in some printer states') input")
        page.select_option("#sce-editor .sce-condition select >> nth=0", value="never")
        page.check("#sce-editor .sce-state-picks " + STATE_PICK.format("Printing"))
        page.select_option("#sce-editor .sce-condition select >> nth=1", value="disable")
        modal_done("#sce-editor")

        # 5b. the first save carries a condition on the one command this probe set, and none on core entries
        body = save_settings()
        if body is None:
            check("first save posts the settings", False, "no POST captured")
        else:
            plugin = (body.get("plugins") or {}).get("systemcommandeditor") or {}
            devices = plugin.get("devices")
            actions = first_actions(body)
            on_entry, off_entry = actions.get("laser_on_cmd", {}), actions.get("laser_off_cmd", {})
            check("POST carries the switch device in plugins.systemcommandeditor.devices",
                  isinstance(devices, list) and len(devices) == 1 and devices[0].get("id") == "laser" and
                  devices[0].get("type") == "switch", json.dumps(devices))
            check("POST gives both commands group and role",
                  on_entry.get("group") == "laser" and on_entry.get("role") == "on" and
                  off_entry.get("group") == "laser" and off_entry.get("role") == "off",
                  json.dumps({"on": on_entry, "off": off_entry}))
            check("POST gives the condition to the one command only",
                  on_entry.get("when") == {"mode": "never", "states": ["printing"], "effect": "disable"} and
                  "when" not in off_entry and not [k for k, a in actions.items() if a.get("when")
                                                   and k != "laser_on_cmd"],
                  json.dumps({"on": on_entry.get("when"), "off": off_entry.get("when")}))
            core = plugin.get("core") or {}
            check("first save carries no condition on any core entry",
                  not [k for k, v in core.items() if isinstance(v, dict) and v.get("when")], json.dumps(core))

        # Phase B: a device holding built-in commands; core editor for reboot and shutdown; a link and a readout
        open_settings()
        add_switch_device("Power")
        core_ok = core_edit("reboot", "Power", "restart", condition=True)
        core_ok = core_edit("shutdown", "Power", "off") and core_ok
        check("core editor opened for reboot and shutdown", core_ok)

        # 2. kind conflict: a device holding built-in commands cannot take a custom command
        edit_command("Laser off", device="Power")
        conflict = page.locator("#sce-editor .help-block:visible", has_text="custom command cannot use it").count()
        conflict_disabled = page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("a built-in device blocks a custom command with a message",
              conflict == 1 and conflict_disabled, "message=%s done_disabled=%s" % (conflict, conflict_disabled))
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")

        # 3. a link with a URL and a condition, and a readout with no condition
        page.get_by_role("button", name="Add device").click()
        wait_visible("#sce-device-editor")
        page.select_option("#sce-device-type", value="link")
        page.fill("#sce-device-name", "Camera")
        page.fill("#sce-device-url", "http://example.test/cam")
        page.check("#sce-device-editor label:has-text('Only allow in some printer states') input")
        page.check("#sce-device-editor .sce-state-picks " + STATE_PICK.format("Printing"))
        modal_done("#sce-device-editor")

        page.get_by_role("button", name="Add device").click()
        wait_visible("#sce-device-editor")
        page.select_option("#sce-device-type", value="readout")
        page.fill("#sce-device-name", "Disk")
        page.fill("#sce-device-status", "df -h")
        modal_done("#sce-device-editor")

        body = save_settings()
        if body is None:
            check("second save posts the settings", False, "no POST captured")
        else:
            plugin = (body.get("plugins") or {}).get("systemcommandeditor") or {}
            core = plugin.get("core") or {}
            devices = {d.get("id"): d for d in plugin.get("devices") or []}
            reboot, shutdown = core.get("reboot") or {}, core.get("shutdown") or {}
            check("core reboot carries group, role and the condition",
                  reboot.get("group") == "power" and reboot.get("role") == "restart" and
                  reboot.get("when") == {"mode": "never", "states": ["printing"], "effect": "disable"},
                  json.dumps(reboot))
            check("core shutdown carries group and role and no condition",
                  shutdown.get("group") == "power" and shutdown.get("role") == "off" and not shutdown.get("when"),
                  json.dumps(shutdown))
            camera, disk = devices.get("camera", {}), devices.get("disk", {})
            check("link device carries url and condition",
                  camera.get("url") == "http://example.test/cam" and camera.get("when") ==
                  {"mode": "only", "states": ["printing"], "effect": "disable"}, json.dumps(camera))
            check("readout device added without a condition carries no when",
                  disk.get("type") == "readout" and "when" not in disk, json.dumps(disk))

        # Phase C: delete the switch device; its group and role leave both commands
        open_settings()
        page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="Laser").locator(
            'button[title="Delete"]').click()
        proceed = page.locator("a:visible, button:visible").filter(has_text="Proceed").first
        proceed.wait_for(state="visible", timeout=8000)
        proceed.click()
        page.wait_for_timeout(300)
        body = save_settings()
        if body is None:
            check("save after delete posts the settings", False, "no POST captured")
        else:
            actions = first_actions(body)
            # a custom entry must not carry the keys at all: clean() leaves empty group, role and value out
            gone = all(not {"group", "role", "value"} & set(actions.get(k, {}))
                       for k in ("laser_on_cmd", "laser_off_cmd"))
            devices = ((body.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or []
            check("delete removes group and role from both commands and the device",
                  gone and "laser" not in [d.get("id") for d in devices],
                  json.dumps({"devices": [d.get("id") for d in devices], "actions": actions}))

        browser.close()
    print("elapsed: %.1f s" % (time.time() - started), flush=True)


def main():
    run_probe()
    failed = results.count(False)
    print("RESULT: %d passed, %d failed, base %s" % (len(results) - failed, failed, BASE), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
