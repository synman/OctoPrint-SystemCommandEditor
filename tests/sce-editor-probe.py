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
PLUGIN_URL = "**/api/plugin/systemcommandeditor"
# built-in commands the core editor needs; a rig whose server.commands does not set them lists none
CORE_FIXTURE = [
    {"action": "shutdown", "name": "Shutdown system", "confirm": "Shutdown the system?"},
    {"action": "reboot", "name": "Reboot system", "confirm": "Reboot the system?"},
]
STATE_PICK = "label:has-text('{}') input"
FIXTURE_DEVICES = [
    {"id": "disk", "type": "readout", "name": "Disk", "icon": "fa-solid fa-hard-drive", "status": "df -h"},
]
# answer to the presets call: one available preset, one unavailable, and one whose id is already a device
PRESETS = [
    {"id": "cpu", "type": "readout", "name": "CPU temperature", "icon": "fa-solid fa-microchip",
     "status": "vcgencmd measure_temp", "section": "Status", "sample": True, "available": True, "value": "42°C"},
    {"id": "throttle", "type": "readout", "name": "Throttling", "icon": "fa-solid fa-bolt",
     "status": "vcgencmd get_throttled", "section": "Status", "sample": True, "available": False, "value": ""},
    {"id": "disk", "type": "readout", "name": "Disk free", "icon": "fa-solid fa-hard-drive",
     "status": "df -h", "section": "Status", "sample": True, "available": True, "value": "18 GB"},
]

# maintenance answer: backup is a sample and available, webcam is available with a sudo note, clock is unavailable
MAINTENANCE = [
    {"action": "backup_now", "name": "Back up OctoPrint now", "icon": "fa-solid fa-box-archive",
     "command": "octoprint --basedir /home/pi/.octoprint plugins backup:backup", "confirm": "", "async": True,
     "sample": True, "available": True, "note": ""},
    {"action": "webcam_restart", "name": "Restart the webcam", "icon": "fa-solid fa-video",
     "command": "sudo systemctl restart camera-streamer", "confirm": "", "async": False,
     "sample": False, "available": True, "note": "Needs passwordless sudo"},
    {"action": "sync_clock", "name": "Sync the clock", "icon": "fa-solid fa-clock-rotate-left",
     "command": "sudo chronyc makestep", "confirm": "", "async": False,
     "sample": False, "available": False, "note": ""},
]

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
        real.setdefault("plugins", {}).setdefault("systemcommandeditor", {})["devices"] = copy.deepcopy(FIXTURE_DEVICES)
        state["base"] = real
        state["current"] = copy.deepcopy(real)
    route.fulfill(status=200, content_type="application/json", body=json.dumps(state["current"]))


def handle_presets(route, request):
    body = request.post_data_json or {} if request.method == "POST" else {}
    if body.get("command") != "presets":
        route.continue_()
        return
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"presets": PRESETS, "maintenance": MAINTENANCE}))


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
    """The edit button of a built-in command: on its control row when grouped, on its own row when not."""
    button = page.locator("#settings_plugin_systemcommandeditor .sce-core-list button[data-core-action='%s']" % action)
    return button.first if button.count() else None


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
    page.get_by_role("button", name="Add control").click()
    wait_visible("#sce-device-editor")
    page.fill("#sce-device-name", name)
    modal_done("#sce-device-editor")


def custom_button(name):
    """The edit button of a custom command on its control row, when the command is grouped; else None."""
    action = page.evaluate(
        """(name) => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const e = vm.entries().find(x => x.action !== 'divider' && x.name === name); return e ? e.action : null; }""",
        name)
    if action is None:
        return None
    button = page.locator("#settings_plugin_systemcommandeditor button[data-action='%s']" % action)
    return button.first if button.count() else None


def edit_command(name, device=None, role=None):
    button = custom_button(name)
    if button is None:
        button = command_row(name).locator('button[title="Edit"]')
    button.click()
    wait_visible("#sce-editor")
    if device is not None:
        page.select_option("#sce-editor-device", label=device)
    if role is not None:
        page.select_option("#sce-editor .sce-role", value=role)


def core_actions():
    return page.evaluate(
        """() => [...document.querySelectorAll('#settings_plugin_systemcommandeditor .sce-core-list [data-core-action]')]
            .map(el => el.getAttribute('data-core-action'))""")


def core_edit(action, device, role, condition=False):
    button = core_row(action)
    if button is None:
        print("core actions on this rig: %s" % core_actions(), flush=True)
        return False
    button.click()
    wait_visible("#sce-editor")
    page.select_option("#sce-editor-device", label=device)
    page.select_option("#sce-editor .sce-role", value=role)
    if condition:
        page.check("#sce-editor label:has-text('Limit to printer states') input")
        page.select_option("#sce-editor .sce-condition select >> nth=0", value="never")
        page.check("#sce-editor .sce-state-picks " + STATE_PICK.format("Printing"))
    modal_done("#sce-editor")
    return True


def first_actions(body):
    return {a.get("action"): a for a in ((body.get("system") or {}).get("actions") or []) if isinstance(a, dict)}


def icon_picker_checks():
    """Icon suggestions in the command editor. Opens the 'Laser on' fixture command and cancels it at the end."""
    edit_command("Laser on")
    # the editor moves focus to its first field 300 ms after it opens; wait for that so it cannot steal the typing
    page.wait_for_function("() => document.activeElement && document.activeElement.id === 'sce-editor-name'",
                           timeout=5000)
    icon = page.locator("#sce-editor-icon")
    over = page.evaluate("""() => { const b = document.querySelector('#sce-editor .modal-body');
        return b.scrollWidth - b.clientWidth; }""")
    check("the command editor has no horizontal scroll", over <= 0, "overflow=%spx" % over)
    classes = page.locator("#sce-editor .sce-icon-suggest-item .sce-icon-suggest-cls")

    icon.click()
    icon.fill("")
    icon.type("trash", delay=30)
    wait_visible("#sce-editor .sce-icon-suggest-item")
    texts = classes.all_inner_texts()
    check("typing trash lists solid and regular trash-can",
          "fa-solid fa-trash-can" in texts and "fa-regular fa-trash-can" in texts, "items=%r" % texts)

    second = classes.nth(1).inner_text().strip()
    icon.press("ArrowDown")
    icon.press("Enter")
    check("ArrowDown then Enter sets the input to the second suggestion",
          icon.input_value() == second, "input=%r want=%r" % (icon.input_value(), second))
    check("picking hides the suggestion list", not visible("#sce-editor .sce-icon-suggest"))
    check("Enter does not close the modal", visible("#sce-editor"))

    icon.fill("")
    icon.type("fa-brands git", delay=30)
    wait_visible("#sce-editor .sce-icon-suggest-item")
    texts = classes.all_inner_texts()
    check("fa-brands git lists only brands classes",
          len(texts) > 0 and all(t.startswith("fa-brands ") for t in texts), "items=%d" % len(texts))

    icon.press("Escape")
    check("Escape hides the suggestion list", not visible("#sce-editor .sce-icon-suggest"))
    check("Escape leaves the typed text unchanged", icon.input_value() == "fa-brands git",
          "input=%r" % icon.input_value())
    check("Escape keeps the modal open", visible("#sce-editor"))

    # per-icon color: picking a color tints the preview and sets icon_color; the clear button empties it
    page.evaluate("""() => { const i = document.querySelector('#sce-editor .sce-icon-color input[type=color]');
        i.value = '#00ff00'; i.dispatchEvent(new Event('change', {bubbles: true})); }""")
    picked = page.evaluate("""() => ({
        color: ko.dataFor(document.querySelector('#sce-editor-icon')).iconColor(),
        preview: getComputedStyle(document.querySelector('#sce-editor .sce-icon-input .add-on i')).color})""")
    check("picking an icon color sets it and tints the preview",
          picked["color"] == "#00ff00" and picked["preview"] == "rgb(0, 255, 0)", json.dumps(picked))
    page.locator("#sce-editor .sce-icon-invert").click()
    inverted = page.evaluate("""() => ({on: ko.dataFor(document.querySelector('#sce-editor-icon')).iconInvert(),
        badge: document.querySelector('#sce-editor .sce-icon-input .add-on i').classList.contains('sce-icon-inverted')})""")
    check("the invert button turns the icon into an inverted badge", inverted["on"] and inverted["badge"],
          json.dumps(inverted))
    page.locator("#sce-editor .sce-icon-invert").click()
    page.locator("#sce-editor .sce-icon-color-clear").click()
    cleared = page.evaluate("() => ko.dataFor(document.querySelector('#sce-editor-icon')).iconColor()")
    check("the no-color button clears the icon color", cleared == "", "color=%r" % cleared)

    page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
    wait_hidden("#sce-editor")

    # a new device's identifier follows its name: lowercase, with spaces and other characters turned into _
    page.get_by_role("button", name="Add control").click()
    wait_visible("#sce-device-editor")
    page.wait_for_timeout(400)
    over = page.evaluate("""() => { const b = document.querySelector('#sce-device-editor .modal-body');
        return b.scrollWidth - b.clientWidth; }""")
    check("the device editor has no horizontal scroll", over <= 0, "overflow=%spx" % over)
    got = {}
    for text in ("Chamber Light #2", "Wi-Fi  (5 GHz)", "Café plug"):
        page.fill("#sce-device-name", text)
        got[text] = page.input_value("#sce-device-identifier")
    check("a new device's identifier is built from its name",
          got == {"Chamber Light #2": "chamber_light_2", "Wi-Fi  (5 GHz)": "wi_fi_5_ghz", "Café plug": "cafe_plug"},
          json.dumps(got))
    page.locator("#sce-device-editor .modal-footer .btn:has-text('Cancel')").click()
    wait_hidden("#sce-device-editor")


BUSY_PICKS = """() => [...document.querySelectorAll('#settings_plugin_systemcommandeditor .sce-busy-icon .sce-icon-picks button i')]
    .map(i => i.className.replace(' fa-fw', '').trim())"""


def busy_icon_checks():
    allowed = page.evaluate("() => window.SceModel.BUSY_ICONS.slice()")
    shown = page.evaluate(BUSY_PICKS)
    check("the busy-icon picker shows exactly the allowed icons", sorted(shown) == sorted(allowed),
          "shown=%s" % shown)
    page.click('#settings_plugin_systemcommandeditor .sce-busy-icon button[title="gear"]')
    preview = page.locator("#settings_plugin_systemcommandeditor .sce-busy-preview i").get_attribute("class") or ""
    check("the busy-icon preview spins the picked icon", "fa-gear" in preview and "fa-spin" in preview,
          "preview=%r" % preview)
    saved = save_settings()
    value = (((saved or {}).get("plugins") or {}).get("systemcommandeditor") or {}).get("busy_icon")
    check("picking an icon and saving stores it as busy_icon", value == "fa-solid fa-gear", "busy_icon=%r" % value)
    open_settings()  # the save closed the dialog; the phases after this work in it


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
        page.route(PLUGIN_URL, handle_presets)
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

        icon_picker_checks()
        busy_icon_checks()

        # Phase A: a switch device with two custom commands, a live consent preview, one condition
        add_switch_device("Laser")
        check("switch device added to the device list",
              page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="Laser").count() == 1)

        edit_command("Laser on", device="Laser", role="on")
        modal_done("#sce-editor")
        edit_command("Laser off", device="Laser", role="off")
        modal_done("#sce-editor")
        laser_control = page.locator("#settings_plugin_systemcommandeditor .sce-control", has_text="Laser")
        check("the Laser control draws one row with a button per command, and the commands have no rows of their own",
              laser_control.count() == 1 and
              laser_control.locator("button[data-action='laser_on_cmd'][title='Laser on']").count() == 1 and
              laser_control.locator("button[data-action='laser_off_cmd'][title='Laser off']").count() == 1 and
              command_row("Laser on").count() == 0 and command_row("Laser off").count() == 0)

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
        page.check("#sce-editor label:has-text('Limit to printer states') input")
        page.select_option("#sce-editor .sce-condition select >> nth=0", value="never")
        page.check("#sce-editor .sce-state-picks " + STATE_PICK.format("Printing"))
        page.select_option("#sce-editor .sce-condition select >> nth=1", value="disable")
        summary = page.locator("#sce-editor .sce-condition-summary").text_content().strip()
        check("condition summary reads the picked condition in words",
              summary == "Grayed out while the printer is Printing.", "summary=%r" % summary)
        modal_done("#sce-editor")

        # moving the Laser control's row moves both its commands together, above the Webcam divider
        laser_control.locator('button[title="Move up"]').click()
        page.wait_for_timeout(200)

        # 5b. the first save carries a condition on the one command this probe set, and none on core entries
        body = save_settings()
        if body is None:
            check("first save posts the settings", False, "no POST captured")
        else:
            plugin = (body.get("plugins") or {}).get("systemcommandeditor") or {}
            devices = plugin.get("devices")
            actions = first_actions(body)
            on_entry, off_entry = actions.get("laser_on_cmd", {}), actions.get("laser_off_cmd", {})
            laser = [d for d in devices or [] if d.get("id") == "laser"]
            check("POST carries the switch device in plugins.systemcommandeditor.devices",
                  isinstance(devices, list) and len(devices) == 2 and len(laser) == 1 and
                  laser[0].get("type") == "switch", json.dumps(devices))
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
            order = [a.get("action") for a in (body.get("system") or {}).get("actions") or []]
            check("moving the Laser control moves both its commands together, above the Webcam divider",
                  order[:3] == ["laser_on_cmd", "laser_off_cmd", "divider"], json.dumps(order))

        # Phase B: a device holding built-in commands; core editor for reboot and shutdown; a link and a readout
        open_settings()
        add_switch_device("Power")
        core_ok = core_edit("reboot", "Power", "restart", condition=True)
        core_ok = core_edit("shutdown", "Power", "off") and core_ok
        check("core editor opened for reboot and shutdown", core_ok)
        power = page.locator("#settings_plugin_systemcommandeditor .sce-core-list .sce-control", has_text="Power")
        check("Built-in commands draws one Power row with one button per member and no member rows",
              power.count() == 1 and power.locator("button[data-core-action='reboot']").count() == 1 and
              power.locator("button[data-core-action='shutdown']").count() == 1 and
              page.locator("#settings_plugin_systemcommandeditor .sce-core-list .sce-command",
                           has_text="Reboot system").count() == 0 and
              page.locator("#settings_plugin_systemcommandeditor .sce-core-list .sce-command",
                           has_text="Shutdown system").count() == 0)

        # 2. kind conflict: a device holding built-in commands cannot take a custom command
        edit_command("Laser off", device="Power")
        conflict = page.locator("#sce-editor .help-block:visible", has_text="custom command cannot use it").count()
        conflict_disabled = page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("a built-in device blocks a custom command with a message",
              conflict == 1 and conflict_disabled, "message=%s done_disabled=%s" % (conflict, conflict_disabled))
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")

        # 3. a link with a URL and a condition, and a readout with no condition
        page.get_by_role("button", name="Add control").click()
        wait_visible("#sce-device-editor")
        page.select_option("#sce-device-type", value="link")
        page.fill("#sce-device-name", "Camera")
        page.fill("#sce-device-url", "http://example.test/cam")
        page.check("#sce-device-editor label:has-text('Limit to printer states') input")
        page.check("#sce-device-editor .sce-state-picks " + STATE_PICK.format("Printing"))
        summary = page.locator("#sce-device-editor .sce-condition-summary").text_content().strip()
        check("link condition summary reads the picked condition in words",
              summary == "Grayed out unless the printer is Printing.", "summary=%r" % summary)
        modal_done("#sce-device-editor")

        page.get_by_role("button", name="Add control").click()
        wait_visible("#sce-device-editor")
        page.select_option("#sce-device-type", value="readout")
        page.fill("#sce-device-name", "Load")
        page.fill("#sce-device-status", "uptime")
        modal_done("#sce-device-editor")

        body = save_settings()
        if body is None:
            check("second save posts the settings", False, "no POST captured")
        else:
            plugin = (body.get("plugins") or {}).get("systemcommandeditor") or {}
            # OctoPrint posts only what changed, so a key the rig already holds is absent from the POST; judge the
            # settings as they stand after the save (the rig's values merged with the POST)
            core = ((state["current"].get("plugins") or {}).get("systemcommandeditor") or {}).get("core") or {}
            devices = {d.get("id"): d for d in plugin.get("devices") or []}
            reboot, shutdown = core.get("reboot") or {}, core.get("shutdown") or {}
            check("core reboot carries group, role and the condition",
                  reboot.get("group") == "power" and reboot.get("role") == "restart" and
                  reboot.get("when") == {"mode": "never", "states": ["printing"], "effect": "disable"},
                  json.dumps(reboot))
            check("core shutdown carries group and role and no condition",
                  shutdown.get("group") == "power" and shutdown.get("role") == "off" and not shutdown.get("when"),
                  json.dumps(shutdown))
            camera, load = devices.get("camera", {}), devices.get("load", {})
            check("link device carries url and condition",
                  camera.get("url") == "http://example.test/cam" and camera.get("when") ==
                  {"mode": "only", "states": ["printing"], "effect": "disable"}, json.dumps(camera))
            check("readout device added without a condition carries no when",
                  load.get("type") == "readout" and "when" not in load, json.dumps(load))

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

        # Phase D: add from presets; cpu is ticked, throttle is unavailable, disk is already a device
        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets .sce-preset-row")
        rows = page.locator("#sce-presets [data-group=readouts] .sce-preset-row")
        check("presets modal lists three readout rows", rows.count() == 3, "rows=%d" % rows.count())
        cpu = rows.filter(has_text="CPU temperature")
        throttle = rows.filter(has_text="Throttling")
        disk_row = rows.filter(has_text="Disk free")
        check("an undetected readout stays pickable and shows Not detected on this system",
              throttle.locator("input").is_enabled() and not throttle.locator("input").is_checked() and
              throttle.filter(has_text="Not detected on this system").count() == 1)
        check("disk is enabled and ticked because it is already a device",
              disk_row.locator("input").is_enabled() and disk_row.locator("input").is_checked())
        check("cpu readout starts unticked", cpu.locator("input").is_enabled() and not cpu.locator("input").is_checked())
        cpu.locator("input").check()
        mrows = page.locator("#sce-presets [data-group=maintenance] .sce-preset-row")
        check("presets modal lists three maintenance rows", mrows.count() == 3, "rows=%d" % mrows.count())
        backup = mrows.filter(has_text="Back up OctoPrint now")
        webcam = mrows.filter(has_text="Restart the webcam")
        clock = mrows.filter(has_text="Sync the clock")
        check("backup is enabled and unticked until picked",
              backup.locator("input").is_enabled() and not backup.locator("input").is_checked())
        backup.locator("input").check()
        check("webcam shows the sudo note, stays enabled and unticked",
              webcam.filter(has_text="Needs passwordless sudo").count() == 1 and webcam.locator("input").is_enabled()
              and not webcam.locator("input").is_checked())
        check("clock stays enabled and shows its note or Not detected on this system",
              clock.locator("input").is_enabled() and
              clock.filter(has_text="Not detected on this system").count() + clock.filter(has_text="Needs passwordless sudo").count() >= 1)
        disk_row.locator("input").uncheck()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        body = save_settings()
        if body is None:
            check("save after presets posts the settings", False, "no POST captured")
        else:
            devices = ((body.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or []
            cpu_entry = [d for d in devices if d.get("id") == "cpu"]
            check("POST carries cpu from presets with exactly five keys and no section",
                  len(cpu_entry) == 1 and set(cpu_entry[0]) == {"id", "type", "name", "icon", "status"}
                  and cpu_entry[0].get("type") == "readout", json.dumps(cpu_entry))
            saved_actions = list((body.get("system") or {}).get("actions") or [])
            status_at = [i for i, a in enumerate(saved_actions) if a == {"action": "divider", "name": "Status"}]
            cpu_place = [i for i, a in enumerate(saved_actions) if a.get("device") == "cpu"]
            check("POST places cpu under the Status divider, as its own entry with no command",
                  len(status_at) == 1 and len(cpu_place) == 1 and cpu_place[0] > status_at[0]
                  and saved_actions[cpu_place[0]] == {"action": "cpu", "name": "CPU temperature", "device": "cpu"},
                  json.dumps(saved_actions))
            check("unticking an added readout and applying removes it from the POST",
                  len([d for d in devices if d.get("id") == "disk"]) == 0, json.dumps([d.get("id") for d in devices]))
            saved = list((body.get("system") or {}).get("actions") or [])
            check("POST system.actions ends with the Maintenance divider and the ticked backup command",
                  len(saved) >= 2 and saved[-2] == {"action": "divider", "name": "Maintenance"}
                  and saved[-1].get("action") == "backup_now" and saved[-1].get("async") is True
                  and not [a for a in saved if a.get("action") == "webcam_restart"],
                  json.dumps(saved[-2:]))

        # Phase E: an action control; its command takes the Run role, and the role list is just Run
        open_settings()   # the settings pane closes on each successful save
        page.get_by_role("button", name="Add control").click()
        wait_visible("#sce-device-editor")
        page.select_option("#sce-device-type", value="action")
        page.fill("#sce-device-name", "Upkeep")
        modal_done("#sce-device-editor")
        action_type = page.evaluate("""() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const d = ko.toJS(vm.devices()).find(x => x.id === 'upkeep');
            return d ? d.type : null;
        }""")
        check("an action control is added with type action", action_type == "action", "type=%r" % action_type)

        edit_command("Back up OctoPrint now", device="Upkeep")
        roles = page.evaluate("() => [...document.querySelectorAll('#sce-editor .sce-role option')].map(o => o.value)")
        check("an action's role list is just Run", roles == ["", "run"], "roles=%s" % roles)
        page.select_option("#sce-editor .sce-role", value="run")
        page.wait_for_timeout(200)
        check("Done enables once the command has the Run role",
              not page.locator("#sce-editor .modal-footer .btn-primary").is_disabled())
        modal_done("#sce-editor")
        upkeep = page.locator("#settings_plugin_systemcommandeditor .sce-control", has_text="Upkeep")
        check("the Upkeep control's row holds the command's run button, and the command has no row of its own",
              upkeep.locator("button[data-action='backup_now'][title='Back up OctoPrint now']").count() == 1 and
              command_row("Back up OctoPrint now").count() == 0,
              "control buttons=%s" % upkeep.locator("button[data-action]").count())

        browser.close()
    print("elapsed: %.1f s" % (time.time() - started), flush=True)


def main():
    run_probe()
    failed = results.count(False)
    print("RESULT: %d passed, %d failed, base %s" % (len(results) - failed, failed, BASE), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
