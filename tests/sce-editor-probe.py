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
import re
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
    {"id": "disk", "type": "readout", "name": "Disk", "icon": "fa-solid fa-hard-drive", "status": "df -h", "preset": "disk"},
    # made by hand with a preset's id but no marker: the picker must not count it as that preset
    {"id": "throttle", "type": "readout", "name": "My throttle", "icon": "fa-solid fa-bolt", "status": "echo ok"},
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

# maintenance answer: backup is a sample and available, Wi-Fi power saving is available with a sudo note, clock is unavailable
MAINTENANCE = [
    {"action": "backup_now", "name": "Back up OctoPrint now", "icon": "fa-solid fa-box-archive",
     "command": "octoprint --basedir /home/pi/.octoprint plugins backup:backup", "confirm": "", "async": True,
     "sample": True, "available": True, "note": ""},
    {"action": "wifi_powersave_off", "name": "Turn Wi-Fi power saving off", "icon": "fa-solid fa-wifi",
     "command": "sudo iw dev wlan0 set power_save off", "confirm": "", "async": False,
     "sample": False, "available": True, "note": "Needs passwordless sudo"},
    {"action": "sync_clock", "name": "Sync the clock", "icon": "fa-solid fa-clock-rotate-left",
     "command": "sudo chronyc makestep", "confirm": "", "async": False,
     "sample": False, "available": False, "note": ""},
]

# switches answer: the host and OctoPrint switches, as the server builds them; neither is in the fixture yet
SWITCHES = [
    {"id": "host", "type": "switch", "name": "probe-host", "icon": "fa-solid fa-server",
     "members": {"reboot": "restart", "shutdown": "off"}, "available": True},
    {"id": "octoprint", "type": "switch", "name": "OctoPrint", "icon": "fa-solid fa-print",
     "members": {"restart": "restart", "restart_safe": "safe_restart"}, "available": True},
]

# plugin presets answer: a PSU Control switch reading get_psu_state with On and Off runs, and a Better Grbl Support
# action reading state with one field, with Home and a confirmed Reset
SNAPSHOT_URL = "http://probe-octo.local/webcam/?action=snapshot"
PLUGINS = [
    {"plugin": "psucontrol", "name": "PSU Control", "presets": [
        {"preset": "psucontrol:psu", "name": "PSU Control",
         "control": {"type": "switch", "name": "PSU Control", "icon": "fa-solid fa-bolt",
                     "read": {"plugin": "psucontrol", "api": "get_psu_state"}},
         "commands": [
             {"name": "On", "run": {"plugin": "psucontrol", "api": "turn_psu_on"}, "role": "on"},
             {"name": "Off", "run": {"plugin": "psucontrol", "api": "turn_psu_off"}, "role": "off"}]}]},
    {"plugin": "bettergrblsupport", "name": "Better Grbl Support", "presets": [
        {"preset": "bettergrblsupport:grbl", "name": "Grbl",
         "control": {"type": "action", "name": "Grbl", "icon": "fa-solid fa-ruler",
                     "read": {"plugin": "bettergrblsupport", "api": "state", "field": "state"}},
         "commands": [
             {"name": "Home", "run": {"plugin": "bettergrblsupport", "api": "home"}, "role": "run",
              "icon": "fa-solid fa-house",
              "when_control": {"mode": "only", "states": ["Idle", "Alarm"], "effect": "disable"}},
             {"name": "Reset", "run": {"plugin": "bettergrblsupport", "api": "reset"}, "role": "run",
              "confirm": "Reset the controller?",
              "when": {"mode": "never", "states": ["printing"], "effect": "disable"}}]}]},
    # a webcam services group: one preset not detected (available false), a switch with a status and shell commands
    {"plugin": "webcam", "name": "Webcam services", "presets": [
        {"preset": "webcam:ustreamer", "name": "Webcam (ustreamer)", "available": False, "note": "",
         "control": {"type": "switch", "name": "Webcam (ustreamer)", "icon": "fa-solid fa-video",
                     "status": "systemctl is-active ustreamer.service",
                     "on_pattern": "^active$", "off_pattern": "^(inactive|failed)$"},
         "commands": [
             {"name": "Start", "command": "sudo systemctl start ustreamer", "role": "on",
              "icon": "fa-solid fa-toggle-on"},
             {"name": "Stop", "command": "sudo systemctl stop ustreamer", "role": "off",
              "icon": "fa-solid fa-toggle-off"},
             {"name": "Restart", "command": "sudo systemctl restart ustreamer", "role": "restart",
              "icon": "fa-solid fa-rotate-right"}]}]},
    # the OctoPrint group, as octoprint_presets() builds it with a snapshot URL: a command-only webcam link, and the
    # G-code macros control with its one named macro
    {"plugin": "octoprint", "name": "OctoPrint", "presets": [
        {"preset": "octoprint:snapshot", "name": "Webcam snapshot", "control": None,
         "commands": [{"name": "Webcam snapshot", "url": SNAPSHOT_URL, "icon": "fa-solid fa-camera"}]},
        {"preset": "octoprint:gcode", "name": "G-code macros",
         "control": {"type": "action", "name": "G-code macros", "icon": "fa-solid fa-terminal"},
         "commands": [{"name": "Show a message", "run": {"gcode": ["M117 Hello from System Commands"]}, "role": "run",
                       "icon": "fa-solid fa-paper-plane", "confirm": "Send this G-code?"}]}]},
]

FIXTURE_ACTIONS = [
    {"action": "divider", "name": "Webcam"},
    {"action": "laser_on_cmd", "name": "Laser on", "command": "echo on", "icon": "fa-solid fa-lightbulb"},
    {"action": "laser_off_cmd", "name": "Laser off", "command": "echo off", "icon": "fa-solid fa-circle"},
    # a hand edit that doubled a key: the pane renames the later one when it loads
    {"action": "laser_off_cmd", "name": "Spare twin", "command": "echo off twin"},
]

results = []
posts = []
# a control with a plugin read, added to the saved settings only by Phase S, so earlier phases see FIXTURE_DEVICES alone
GRBL_DEVICE = {"id": "grbl", "type": "action", "name": "Grbl", "icon": "fa-solid fa-microchip",
               "read": {"plugin": "bettergrblsupport", "helper": "state"}}
# a second control with a read and no commands, for Phase C: a built-in command may join only a control with no
# custom commands, so Grbl, which has some by then, cannot take one
SPINDLE_DEVICE = {"id": "spindle", "type": "action", "name": "Spindle", "icon": "fa-solid fa-fan",
                  "read": {"plugin": "bettergrblsupport", "helper": "state"}}
# the states calls the page made, and the words the fixture answers a plugin read with
state_calls = []
GRBL_WORDS = ["Idle", "Run", "Hold", "Jog", "Alarm", "Door", "Check", "Home", "Sleep", "Reset", "N/A"]
# plugin Run now: the run calls the probe saw, OctoPrint's own command executes (must stay empty for a plugin
# command), and the answer the route gives
runs = []
executes = []
RUN_ANSWER = {"ok": True, "output": "done"}
state = {"base": None, "current": None}
page = None


# the MQTT plugin's group, offered only while MQTT_INSTALLED is on, so the plugin rows of the earlier phases keep their
# counts; the MQTT phase turns it on for one picker and off again
MQTT_INSTALLED = {"on": False}
MQTT_GROUP = {"plugin": "mqtt", "name": "MQTT", "presets": [
    {"preset": "mqtt:readout", "name": "MQTT readout",
     "control": {"type": "readout", "name": "MQTT readout", "icon": "fa-solid fa-tower-broadcast",
                 "read": {"mqtt": "home/sensor/state"}},
     "commands": []},
    {"preset": "mqtt:switch", "name": "MQTT switch",
     "control": {"type": "switch", "name": "MQTT switch", "icon": "fa-solid fa-tower-broadcast",
                 "read": {"mqtt": "home/switch/state"}, "on_pattern": "^ON$", "off_pattern": "^OFF$"},
     "commands": [
         {"name": "On", "role": "on", "icon": "fa-solid fa-toggle-on",
          "run": {"plugin": "mqtt", "helper": "mqtt_publish", "args": {"topic": "home/switch/set", "payload": "ON"}}},
         {"name": "Off", "role": "off", "icon": "fa-solid fa-toggle-off",
          "run": {"plugin": "mqtt", "helper": "mqtt_publish", "args": {"topic": "home/switch/set", "payload": "OFF"}}}]}]}


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
    if body.get("command") == "states":
        # a plugin read answers its helper's words; a shell status answers free, with no list
        state_calls.append(copy.deepcopy(body))
        if "read" in body:
            answer = {"source": "helper", "states": GRBL_WORDS}
        else:
            answer = {"source": "free", "states": []}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))
        return
    if body.get("command") == "run":
        # a plugin command's Run now: recorded, answered from RUN_ANSWER, never forwarded
        runs.append(copy.deepcopy(body))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(RUN_ANSWER))
        return
    if body.get("command") != "presets":
        route.continue_()
        return
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"presets": PRESETS, "maintenance": MAINTENANCE, "switches": SWITCHES,
                                   "plugins": PLUGINS + ([MQTT_GROUP] if MQTT_INSTALLED["on"] else [])}))


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


def handle_execute(route, request):
    # OctoPrint's own execute of a custom command: recorded and answered, never forwarded
    if request.method == "POST":
        executes.append(request.url)
        route.fulfill(status=204, body="")
        return
    route.continue_()


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
    check("no editor shows an identifier field",
          page.locator("#sce-device-editor label:has-text('Identifier'), #sce-editor label:has-text('Identifier')").count() == 0)
    got = {}
    for text in ("Chamber Light #2", "Wi-Fi  (5 GHz)", "Café plug", "Disk"):
        page.fill("#sce-device-name", text)
        got[text] = page.evaluate("() => ko.dataFor(document.querySelector('#sce-device-name')).key()")
    check("a new control's hidden key is built from its name, and a taken one gets _2",
          got == {"Chamber Light #2": "chamber_light_2", "Wi-Fi  (5 GHz)": "wi_fi_5_ghz", "Café plug": "cafe_plug",
                  "Disk": "disk_2"}, json.dumps(got))
    page.locator("#sce-device-editor .modal-footer .btn:has-text('Cancel')").click()
    wait_hidden("#sce-device-editor")

    # the hidden key follows a rename: an unchanged name keeps an existing key, even an old hand-typed one
    keys = page.evaluate("""() => {
        const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
        const out = {};
        vm.editor.show({action: 'Old.Key', name: 'Webcam on', command: 'echo on'});
        out.kept = vm.editor.key();
        vm.editor.name('Webcam power on');
        out.renamed = vm.editor.key();
        vm.deviceEditor.show({id: 'cam', type: 'switch', name: 'Camera'});
        out.controlKept = vm.deviceEditor.key();
        vm.deviceEditor.name('Front camera');
        out.controlRenamed = vm.deviceEditor.key();
        return out;
    }""")
    check("a rename makes a new hidden key and an unchanged name keeps the old one",
          keys == {"kept": "Old.Key", "renamed": "webcam_power_on", "controlKept": "cam", "controlRenamed": "front_camera"},
          json.dumps(keys))
    for modal in ("#sce-device-editor", "#sce-editor"):
        page.locator(modal + " .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden(modal)


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


LIST_VM = "(() => ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor')))()"


def set_list(entries, devices):
    """Replaces the custom list and controls in the open pane; the save path is not touched."""
    page.evaluate("""([entries, devices]) => {
        const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
        vm.devices(devices);
        vm.entries(entries);
    }""", [entries, devices])


def heading_row(name):
    """The custom list's heading row with this name; the core list's headings are not in the custom list."""
    return page.locator("#settings_plugin_systemcommandeditor .sce-row.sce-section").filter(
        has=page.locator(".sce-section-label", has_text=re.compile(r"^\s*%s\s*$" % re.escape(name)))).first


def control_row(member_action):
    """The custom list's control row that holds this member button."""
    return page.locator("#settings_plugin_systemcommandeditor .sce-row.sce-control").filter(
        has=page.locator("button[data-action='%s']" % member_action)).first


def list_names():
    """Each list entry as 'Name' for a command, '#Name' for a heading, in list order."""
    return page.evaluate("""() => {
        const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
        return vm.entries().map(e => e.action === 'divider' ? '#' + (e.name || '') : (e.name || e.action));
    }""")


def list_state():
    return page.evaluate("""() => {
        const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
        return {entries: ko.toJS(vm.entries()), devices: ko.toJS(vm.devices())};
    }""")


def list_ops_checks():
    """Section moves and drags, section and control duplicates, run duplicates, unlinking, the maintenance place, and
    the run text. Works on a fixture list set through the open pane; never saves over the rig."""
    open_settings()
    ctl_devices = [{"id": "ctl", "type": "action", "name": "Ctl", "icon": "fa-solid fa-play"}]
    set_list([
        {"action": "divider", "name": "S"},
        {"action": "s_cmd", "name": "Plain S", "command": "echo s"},
        {"action": "ctl_on", "name": "Ctl on", "group": "ctl", "role": "run", "command": "echo on"},
        {"action": "ctl_off", "name": "Ctl off", "group": "ctl", "role": "run", "command": "echo off"},
        {"action": "divider", "name": "T"},
        {"action": "t_cmd", "name": "Plain T", "command": "echo t"},
    ], ctl_devices)
    page.wait_for_timeout(200)

    # Up on the heading below S moves its whole block above S
    heading_row("T").locator("button[title='Move up']").click()
    names = list_names()
    check("Up on a heading moves its whole block above the heading before it",
          names == ["#T", "Plain T", "#S", "Plain S", "Ctl on", "Ctl off"], "names=%s" % names)

    # dragging the heading S onto the heading T moves S's block above T
    heading_row("S").drag_to(heading_row("T"))
    names = list_names()
    check("dragging a heading moves its block to the drop place",
          names == ["#S", "Plain S", "Ctl on", "Ctl off", "#T", "Plain T"], "names=%s" % names)

    # Duplicate on the heading S: "Copy of S" right after S with copies of its items, the copied control joined
    heading_row("S").locator("button[title='Duplicate']").click()
    names = list_names()
    check("Duplicate on a heading adds Copy of S right after S with copies of its items",
          names == ["#S", "Plain S", "Ctl on", "Ctl off", "#Copy of S", "Copy of Plain S", "Ctl on", "Ctl off",
                    "#T", "Plain T"], "names=%s" % names)
    state_now = list_state()
    copied = [d for d in state_now["devices"] if d["name"] == "Copy 2 of Ctl" or d["name"] == "Copy of Ctl"]
    joined = [e for e in state_now["entries"] if copied and e.get("group") == copied[-1]["id"]]
    check("the section copy's control is joined to its copied members", len(copied) == 1 and len(joined) == 2,
          "devices=%s" % [d["name"] for d in state_now["devices"]])

    # the control row's copy button adds a Copy of control with its custom members right after the original unit
    control_row("ctl_on").locator("button[title='Duplicate']").click()
    state_now = list_state()
    ctl_copies = [d for d in state_now["devices"] if d["name"].startswith("Copy") and d["name"].endswith("of Ctl")]
    names = list_names()
    check("the control row's copy button adds a Copy of control after the original unit",
          len(ctl_copies) == 2 and names[2:5] == ["Ctl on", "Ctl off", "Ctl on"], "names=%s" % names)

    # a run command's Duplicate keeps its control and lands right after it
    custom_button("Ctl on").click()
    wait_visible("#sce-editor")
    page.locator("#sce-editor .modal-footer .btn:has-text('Duplicate')").click()
    wait_hidden("#sce-editor")
    state_now = list_state()
    ctl_on = [e for e in state_now["entries"] if e.get("name") == "Ctl on"][0]
    names = list_names()
    after = [e for e in state_now["entries"] if e.get("name") == "Copy of Ctl on"]
    check("a run command's Duplicate keeps its control and lands right after it",
          len(after) == 1 and after[0].get("group") == ctl_on.get("group") and
          names.index("Copy of Ctl on") == names.index("Ctl on") + 1, "names=%s" % names)

    # renaming a preset-made command drops the marker from every record of that preset
    set_list([
        {"action": "pz_run", "name": "Preset run", "group": "pz", "role": "run", "command": "echo p", "preset": "pset"},
        {"action": "pz_two", "name": "Preset two", "group": "pz", "role": "run", "command": "echo q", "preset": "pset"},
    ], [{"id": "pz", "type": "action", "name": "Preset ctl", "icon": "fa-solid fa-play", "preset": "pset"}])
    page.wait_for_timeout(200)
    custom_button("Preset run").click()
    wait_visible("#sce-editor")
    page.fill("#sce-editor-name", "Renamed run")
    modal_done("#sce-editor")
    state_now = list_state()
    marked = [x for x in state_now["entries"] + state_now["devices"] if x.get("preset") == "pset"]
    check("renaming a preset-made command drops the marker from every record of its preset", marked == [],
          "marked=%s" % [x.get("name") for x in marked])

    # renaming the host switch keeps its marker
    set_list([], [{"id": "host", "type": "switch", "name": "Host", "icon": "fa-solid fa-server", "preset": "host"}])
    page.wait_for_timeout(200)
    page.evaluate("() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor')); vm.editDevice(vm.devices()[0]); }")
    wait_visible("#sce-device-editor")
    page.fill("#sce-device-name", "Host renamed")
    modal_done("#sce-device-editor")
    state_now = list_state()
    check("renaming the host switch keeps its preset marker",
          state_now["devices"][0].get("preset") == "host" and state_now["devices"][0]["name"] == "Host renamed",
          "devices=%s" % state_now["devices"])

    # detaching one preset command drops only its marker
    set_list([
        {"action": "pd_a", "name": "Detach a", "group": "pd", "role": "run", "command": "echo a", "preset": "pdset"},
        {"action": "pd_b", "name": "Keep b", "group": "pd", "role": "run", "command": "echo b", "preset": "pdset"},
    ], [{"id": "pd", "type": "action", "name": "Detach ctl", "icon": "fa-solid fa-play", "preset": "pdset"}])
    page.wait_for_timeout(200)
    custom_button("Detach a").click()
    wait_visible("#sce-editor")
    page.select_option("#sce-editor-device", label="None")
    modal_done("#sce-editor")
    state_now = list_state()
    kept = {x.get("name"): x.get("preset") for x in state_now["entries"] + state_now["devices"]}
    check("detaching one preset command drops only its marker",
          kept.get("Detach a") is None and kept.get("Keep b") == "pdset" and kept.get("Detach ctl") == "pdset",
          "markers=%s" % kept)

    # ticking a maintenance preset when Maintenance is not last places it at the end of that section
    set_list([
        {"action": "divider", "name": "Maintenance"},
        {"action": "maint_x", "name": "Maint X", "command": "echo x"},
        {"action": "divider", "name": "Other"},
        {"action": "other_cmd", "name": "Other cmd", "command": "echo o"},
    ], [])
    page.wait_for_timeout(200)
    page.evaluate("""() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
        vm.presets.open(); }""")
    page.wait_for_function("""() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
        return !vm.presets.loading() && vm.presets.maintenance().length > 0; }""", timeout=8000)
    page.evaluate("""() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
        vm.presets.maintenance().find(r => r.item.action === 'backup_now').checked(true);
        vm.presets.apply(); }""")
    page.wait_for_timeout(200)
    names = list_names()
    check("a ticked maintenance preset lands at the end of the Maintenance section",
          names == ["#Maintenance", "Maint X", "Back up OctoPrint now", "#Other", "Other cmd"], "names=%s" % names)

    # the confirmation preview and the row code column show the run text, G-code from its lines
    set_list([{"action": "gc", "name": "Send macro", "run": {"gcode": ["M117 hi", "G28"]}, "role": ""}], [])
    page.wait_for_timeout(200)
    code = page.locator("#settings_plugin_systemcommandeditor .sce-row", has_text="Send macro").locator(".sce-cmd").first.text_content() or ""
    check("the row code column shows the G-code run text", code.startswith("G-code: ") and "M117 hi" in code, "code=%r" % code)
    edit_command("Send macro")
    page.check("#sce-editor label.checkbox:has-text('Ask for confirmation') input")
    wait_visible("#sce-editor .sce-preview-command")
    preview = page.locator("#sce-editor .sce-preview-command").first.text_content() or ""
    check("the confirmation preview shows the G-code run text", preview.startswith("G-code: ") and "G28" in preview,
          "preview=%r" % preview)
    page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
    wait_hidden("#sce-editor")

    # renaming a preset-made control unlinks it: every control and entry of that preset loses its marker
    set_list([
        {"action": "pc_a", "name": "Preset a", "group": "pc", "role": "run", "command": "echo a", "preset": "pcset"},
        {"action": "pc_b", "name": "Preset b", "group": "pc", "role": "run", "command": "echo b", "preset": "pcset"},
    ], [{"id": "pc", "type": "action", "name": "Preset ctl", "icon": "fa-solid fa-play", "preset": "pcset"}])
    page.wait_for_timeout(200)
    page.evaluate("() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor')); vm.editDevice(vm.devices()[0]); }")
    wait_visible("#sce-device-editor")
    page.fill("#sce-device-name", "Preset ctl renamed")
    modal_done("#sce-device-editor")
    state_now = list_state()
    still = [x.get("name") for x in state_now["entries"] + state_now["devices"] if x.get("preset") == "pcset"]
    check("renaming a preset-made control drops the marker from every record of its preset", still == [],
          "still marked=%s" % still)

    # the confirmation preview shows the G-code lines as typed in the box now, not the saved run
    set_list([{"action": "gcx", "name": "Macro typed", "run": {"gcode": ["M117 saved"]}}], [])
    page.wait_for_timeout(200)
    edit_command("Macro typed")
    page.check("#sce-editor label.checkbox:has-text('Ask for confirmation') input")
    page.fill("#sce-editor-gcode", "M117 typed\nG4 P1")
    page.wait_for_timeout(200)
    typed_preview = page.locator("#sce-editor .sce-preview-command").first.text_content() or ""
    check("the confirmation preview shows the G-code lines typed in the box, not the saved run",
          "M117 typed" in typed_preview and "G4 P1" in typed_preview and "M117 saved" not in typed_preview,
          "preview=%r" % typed_preview)
    page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
    wait_hidden("#sce-editor")

    # a heading's Move buttons are block moves: the last heading cannot move down, the first cannot move up
    set_list([
        {"action": "divider", "name": "S"},
        {"action": "s_only", "name": "Only S", "command": "echo s"},
        {"action": "divider", "name": "T"},
        {"action": "t_only", "name": "Only T", "command": "echo t"},
    ], [])
    page.wait_for_timeout(200)
    last_down = heading_row("T").locator("button[title='Move down']").is_disabled()
    first_up = heading_row("S").locator("button[title='Move up']").is_disabled()
    first_down = heading_row("S").locator("button[title='Move down']").is_disabled()
    check("the last heading's Move down is disabled, and the first heading's Move up is disabled",
          last_down and first_up and not first_down,
          "last_down=%s first_up=%s first_down=%s" % (last_down, first_up, first_down))

    # duplicating a placed readout control: its new placement is keyed by the copy's id, as placementKey gives it
    set_list([{"action": "rdo", "name": "Readout one", "device": "rdo"}],
             [{"id": "rdo", "type": "readout", "name": "Readout one", "icon": "fa-solid fa-hard-drive", "status": "echo r"}])
    page.wait_for_timeout(200)
    page.evaluate("() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor')); vm.editDevice(vm.devices()[0]); }")
    wait_visible("#sce-device-editor")
    page.locator("#sce-device-editor .modal-footer button:has-text('Duplicate')").click()
    wait_hidden("#sce-device-editor")
    state_now = list_state()
    copy_dev = [d for d in state_now["devices"] if d.get("name") == "Copy of Readout one"]
    placements = [e for e in state_now["entries"] if copy_dev and e.get("device") == copy_dev[0]["id"]]
    check("a duplicated readout's placement is keyed by the copy's id",
          len(placements) == 1 and placements[0].get("action") == copy_dev[0]["id"],
          "device=%s placements=%s" % (copy_dev, placements))



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
        page.route("**/api/system/commands/**", handle_execute)
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

        keys = page.evaluate("""() => ko.toJS(ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor')).entries())
            .filter((e) => e.action !== 'divider').map((e) => [e.name, e.action])""")
        check("a doubled key in the saved list is renamed on load: the first keeps it, the later one takes _2",
              ["Laser off", "laser_off_cmd"] in keys and ["Spare twin", "laser_off_cmd_2"] in keys, json.dumps(keys))
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
              laser_control.locator("button[data-action='laser_on_cmd'][title='Edit Laser on']").count() == 1 and
              laser_control.locator("button[data-action='laser_off_cmd'][title='Edit Laser off']").count() == 1 and
              command_row("Laser on").count() == 0 and command_row("Laser off").count() == 0)
        check("with no icon of its own, a switch's On button draws the toggle-on icon and Off the toggle-off icon",
              laser_control.locator("button[data-action='laser_on_cmd'] i.fa-toggle-on").count() == 1 and
              laser_control.locator("button[data-action='laser_off_cmd'] i.fa-toggle-off").count() == 1)

        # the On command's own icon and color replace the toggle icon on its button, once saved
        edit_command("Laser on")
        page.fill("#sce-editor-icon", "fa-solid fa-bolt")
        page.locator("#sce-editor .sce-icon-color input[type=color]").fill("#ff0000")
        modal_done("#sce-editor")
        save_settings()
        open_settings()
        laser_on_button = page.locator("#settings_plugin_systemcommandeditor button[data-action='laser_on_cmd']").first
        on_drawn = laser_on_button.locator("i.fa-bolt").evaluate("el => getComputedStyle(el).color") \
            if laser_on_button.locator("i.fa-bolt").count() == 1 else None
        check("a switch's On button draws its command's own icon and color once saved",
              on_drawn == "rgb(255, 0, 0)" and laser_on_button.get_attribute("title") == "Edit Laser on",
              "color=%r" % on_drawn)

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
                  isinstance(devices, list) and len(devices) == 3 and len(laser) == 1 and
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
        check("a built-in restart button with no icon of its own draws the restart icon",
              power.locator("button[data-core-action='reboot'] i.fa-rotate-right").count() == 1)
        core_row("shutdown").click()
        wait_visible("#sce-editor")
        page.fill("#sce-editor-icon", "fa-solid fa-bolt")
        modal_done("#sce-editor")
        check("a built-in command on a control draws its core icon when set",
              power.locator("button[data-core-action='shutdown'] i.fa-bolt").count() == 1)

        # 2. kind conflict: a device holding built-in commands cannot take a custom command
        edit_command("Laser off", device="Power")
        conflict = page.locator("#sce-editor .help-block:visible", has_text="custom command cannot use it").count()
        conflict_disabled = page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("a built-in device blocks a custom command with a message",
              conflict == 1 and conflict_disabled, "message=%s done_disabled=%s" % (conflict, conflict_disabled))
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")

        # 3. the control editor no longer offers Link; a readout with no condition still goes in
        page.get_by_role("button", name="Add control").click()
        wait_visible("#sce-device-editor")
        check("the control editor no longer offers Link",
              page.locator("#sce-device-type option[value='link']").count() == 0)
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
            load = devices.get("load", {})
            check("readout device added without a condition carries no when",
                  load.get("type") == "readout" and "when" not in load, json.dumps(load))
            check("no device of type link is written",
                  not [d for d in plugin.get("devices") or [] if d.get("type") == "link"])

        # Phase C: delete the switch device; its two custom commands leave the list, the ungrouped twin stays
        open_settings()
        page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="Laser").locator(
            'button[title="Delete"]').click()
        proceed = page.locator("a:visible, button:visible").filter(has_text="Proceed").first
        proceed.wait_for(state="visible", timeout=8000)
        dialog_text = page.evaluate("() => document.body.innerText")
        check("the delete confirmation names the control and its 2 custom commands",
              'removes the control "Laser" and its 2 custom commands' in dialog_text, dialog_text[:200])
        proceed.click()
        page.wait_for_timeout(300)
        body = save_settings()
        if body is None:
            check("save after delete posts the settings", False, "no POST captured")
        else:
            actions = first_actions(body)
            twin = [a for a in actions.values() if a.get("name") == "Spare twin"]
            devices = ((body.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or []
            check("delete removes both custom commands of the control and keeps the ungrouped command",
                  "laser_on_cmd" not in actions and "laser_off_cmd" not in actions and len(twin) == 1
                  and "group" not in twin[0] and "laser" not in [d.get("id") for d in devices],
                  json.dumps({"devices": [d.get("id") for d in devices], "actions": actions}))

        # Phase C2: delete the Power control, which groups the built-in reboot and shutdown and has no custom commands;
        # the built-in commands are kept and detached, and the confirmation keeps today's wording
        open_settings()
        page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="Power").locator(
            'button[title="Delete"]').click()
        proceed = page.locator("a:visible, button:visible").filter(has_text="Proceed").first
        proceed.wait_for(state="visible", timeout=8000)
        power_text = page.evaluate("() => document.body.innerText")
        check("deleting a control with no custom commands keeps today's confirmation wording",
              'removes the control "Power" and its role on each command.' in power_text,
              power_text[:200])
        proceed.click()
        page.wait_for_timeout(300)
        save_settings()
        after = ((state["current"].get("plugins") or {}).get("systemcommandeditor") or {})
        after_core = after.get("core") or {}
        check("deleting the Power control keeps reboot and shutdown and clears their group and role",
              "reboot" in after_core and "shutdown" in after_core
              and not (after_core.get("reboot") or {}).get("group") and not (after_core.get("reboot") or {}).get("role")
              and not (after_core.get("shutdown") or {}).get("group") and not (after_core.get("shutdown") or {}).get("role")
              and "power" not in [d.get("id") for d in after.get("devices") or []],
              json.dumps({"devices": [d.get("id") for d in after.get("devices") or []], "core": after_core}))

        # Phase D: add from presets; cpu is ticked, throttle is unavailable, disk is already a device
        open_settings()
        # earlier phases put reboot and shutdown on a control; free them so the host switch must set both roles itself
        page.evaluate("""() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const settings = Object.assign({}, vm.coreSettings());
            ['reboot', 'shutdown'].forEach((a) => { settings[a] = Object.assign({}, settings[a], {group: '', role: ''}); });
            vm.coreSettings(settings);
            vm._coreDirty = true;
        }""")
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
        check("disk is enabled and ticked because a control carries its marker",
              disk_row.locator("input").is_enabled() and disk_row.locator("input").is_checked())
        check("cpu readout starts unticked", cpu.locator("input").is_enabled() and not cpu.locator("input").is_checked())
        cpu.locator("input").check()
        srows = page.locator("#sce-presets [data-group=switches] .sce-preset-row")
        check("presets modal lists the host and OctoPrint switches, unticked while absent",
              srows.count() == 2 and srows.nth(0).filter(has_text="probe-host").count() == 1
              and not srows.nth(0).locator("input").is_checked() and not srows.nth(1).locator("input").is_checked(),
              "rows=%d" % srows.count())
        srows.filter(has_text="probe-host").locator("input").check()
        mrows = page.locator("#sce-presets [data-group=maintenance] .sce-preset-row")
        check("presets modal lists three maintenance rows", mrows.count() == 3, "rows=%d" % mrows.count())
        backup = mrows.filter(has_text="Back up OctoPrint now")
        wifi = mrows.filter(has_text="Turn Wi-Fi power saving off")
        clock = mrows.filter(has_text="Sync the clock")
        check("backup is enabled and unticked until picked",
              backup.locator("input").is_enabled() and not backup.locator("input").is_checked())
        backup.locator("input").check()
        check("Wi-Fi power saving shows the sudo note, stays enabled and unticked",
              wifi.filter(has_text="Needs passwordless sudo").count() == 1 and wifi.locator("input").is_enabled()
              and not wifi.locator("input").is_checked())
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
            check("POST carries cpu from presets with its marker and no section",
                  len(cpu_entry) == 1 and set(cpu_entry[0]) == {"id", "type", "name", "icon", "status", "preset"}
                  and cpu_entry[0].get("preset") == "cpu"
                  and cpu_entry[0].get("type") == "readout", json.dumps(cpu_entry))
            saved_actions = list((body.get("system") or {}).get("actions") or [])
            status_at = [i for i, a in enumerate(saved_actions) if a == {"action": "divider", "name": "Status"}]
            cpu_place = [i for i, a in enumerate(saved_actions) if a.get("device") == "cpu"]
            check("POST places cpu under the Status divider, as its own entry with no command",
                  len(status_at) == 1 and len(cpu_place) == 1 and cpu_place[0] > status_at[0]
                  and saved_actions[cpu_place[0]] == {"action": "cpu", "name": "CPU temperature", "device": "cpu", "preset": "cpu"},
                  json.dumps(saved_actions))
            host = [d for d in devices if d.get("id") == "host"]
            # OctoPrint posts only changed keys, so the roles are judged from the settings as they stand after the save
            core = ((state["current"].get("plugins") or {}).get("systemcommandeditor") or {}).get("core") or {}
            check("ticking the host switch adds it first and joins reboot and shutdown to it by role",
                  devices[:1] == [{"id": "host", "type": "switch", "name": "probe-host", "icon": "fa-solid fa-server", "preset": "host"}]
                  and (core.get("reboot") or {}).get("group") == "host" and (core.get("reboot") or {}).get("role") == "restart"
                  and (core.get("shutdown") or {}).get("group") == "host" and (core.get("shutdown") or {}).get("role") == "off",
                  json.dumps({"host": host, "core": core}))
            check("a hand-made readout with a preset's id but no marker is left alone",
                  len([d for d in devices if d.get("id") == "throttle" and not d.get("preset")]) == 1,
                  json.dumps([d.get("id") for d in devices]))
            check("unticking an added readout and applying removes it from the POST",
                  len([d for d in devices if d.get("id") == "disk"]) == 0, json.dumps([d.get("id") for d in devices]))
            saved = list((body.get("system") or {}).get("actions") or [])
            check("POST system.actions ends with the Maintenance divider and the ticked backup command",
                  len(saved) >= 2 and saved[-2] == {"action": "divider", "name": "Maintenance"}
                  and saved[-1].get("action") == "backup_now" and saved[-1].get("async") is True
                  and not [a for a in saved if a.get("action") == "wifi_powersave_off"],
                  json.dumps(saved[-2:]))

        # Phase D2: the host switch is now present, so it starts ticked; unticking it removes it and frees its members
        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets [data-group=switches] .sce-preset-row")
        host_row = page.locator("#sce-presets [data-group=switches] .sce-preset-row").filter(has_text="probe-host")
        check("a present switch starts ticked", host_row.locator("input").is_checked())
        host_row.locator("input").uncheck()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        save_settings()
        after = ((state["current"].get("plugins") or {}).get("systemcommandeditor") or {})
        after_core = after.get("core") or {}
        check("unticking the host switch removes it and detaches reboot and shutdown",
              not [d for d in after.get("devices") or [] if d.get("id") == "host"]
              and not (after_core.get("reboot") or {}).get("group") and not (after_core.get("shutdown") or {}).get("group"),
              json.dumps({"devices": [d.get("id") for d in after.get("devices") or []], "core": after_core}))

        # Phase D3: plugin presets. PSU Control is a switch reading get_psu_state, with On and Off run commands
        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets .sce-plugin-row")
        plugin_rows = page.locator("#sce-presets .sce-plugin-row")
        psu_row = plugin_rows.filter(has_text="PSU Control")
        grbl_row = plugin_rows.filter(has_text="Grbl")
        check("the presets picker shows a Plugin devices section with all five plugin presets unticked",
              page.locator("#sce-presets h4", has_text="Plugin devices").count() == 1 and plugin_rows.count() == 5
              and not psu_row.locator("input").is_checked() and not grbl_row.locator("input").is_checked(),
              "rows=%d" % plugin_rows.count())
        psu_row.locator("input").check()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        body = save_settings()
        if body is None:
            check("save after plugin pick posts the settings", False, "no POST captured")
        else:
            plugin = (body.get("plugins") or {}).get("systemcommandeditor") or {}
            devices = plugin.get("devices") or []
            psu = [d for d in devices if d.get("id") == "psu_control"]
            saved = list((body.get("system") or {}).get("actions") or [])
            check("POST carries the PSU control with its marker, its read and a key from its name",
                  len(psu) == 1 and psu[0].get("preset") == "psucontrol:psu" and psu[0].get("type") == "switch"
                  and psu[0].get("read") == {"plugin": "psucontrol", "api": "get_psu_state"}, json.dumps(psu))
            div_at = [i for i, a in enumerate(saved) if a == {"action": "divider", "name": "Plugin devices"}]
            on_entry = next((a for a in saved if a.get("action") == "psu_control_on"), {})
            off_entry = next((a for a in saved if a.get("action") == "psu_control_off"), {})
            check("POST places On and Off under a Plugin devices divider, each with run, role, group and marker",
                  len(div_at) == 1 and on_entry.get("run") == {"plugin": "psucontrol", "api": "turn_psu_on"}
                  and on_entry.get("role") == "on" and on_entry.get("group") == "psu_control"
                  and on_entry.get("preset") == "psucontrol:psu" and div_at and
                  saved.index(on_entry) > div_at[0] and
                  off_entry.get("run") == {"plugin": "psucontrol", "api": "turn_psu_off"}
                  and off_entry.get("role") == "off" and off_entry.get("group") == "psu_control"
                  and off_entry.get("preset") == "psucontrol:psu",
                  json.dumps({"on": on_entry, "off": off_entry, "dividers": div_at}))
        open_settings()  # the save closed the settings pane
        edit_command("On")
        run_line = page.locator("#sce-editor .sce-run-line").inner_text().strip()
        no_command_box = page.locator("#sce-editor #sce-editor-command").count() == 0
        done_on = not page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("the PSU On command editor shows its plugin call with no Command textarea and Done enabled",
              run_line == "psucontrol: turn_psu_on" and no_command_box and done_on,
              "run=%r no_box=%s done=%s" % (run_line, no_command_box, done_on))
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")
        page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="PSU Control").locator(
            'button[title="Edit"]').click()
        wait_visible("#sce-device-editor")
        read_line = page.locator("#sce-device-editor .sce-read-line").inner_text().strip()
        no_status_box = page.locator("#sce-device-status").count() == 0
        check("the PSU control editor shows its read call with no Status command textarea",
              read_line == "psucontrol: get_psu_state" and no_status_box,
              "read=%r no_status_box=%s" % (read_line, no_status_box))
        page.locator("#sce-device-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-device-editor")

        # Run now on the saved PSU On command (saved and unchanged, so it can run): the plugin's run is posted and
        # OctoPrint's own execute is not; then an error answer shows its text literally in the toast
        open_settings()
        edit_command("On")
        run_now = page.locator("#sce-editor .modal-footer button:has-text('Run now')")
        page.wait_for_timeout(300)
        runnable = run_now.is_enabled()
        runs_before, executes_before = len(runs), len(executes)
        run_now.click()
        end = time.time() + 8
        while len(runs) == runs_before and time.time() < end:
            page.wait_for_timeout(100)
        page.wait_for_timeout(300)
        check("Run now is enabled on the saved PSU On command", runnable)
        check("Run now posts the plugin's run command with the action, never OctoPrint's own execute",
              len(runs) == runs_before + 1 and runs[-1] == {"command": "run", "action": "psu_control_on"}
              and len(executes) == executes_before,
              "runs=%s executes=%s" % (runs[runs_before:], executes[executes_before:]))

        RUN_ANSWER.clear()
        RUN_ANSWER.update({"ok": False, "error": "<b>bad</b>"})
        runs_before = len(runs)
        run_now.click()
        end = time.time() + 8
        while len(runs) == runs_before and time.time() < end:
            page.wait_for_timeout(100)
        # the earlier success toast may still be showing, so look for the error toast by its text
        end = time.time() + 8
        toast_text = ""
        while time.time() < end and "bad" not in toast_text:
            toast_text = " | ".join(page.locator(".ui-pnotify-text").all_inner_texts())
            page.wait_for_timeout(100)
        toast_bold = page.locator(".ui-pnotify .ui-pnotify-text b").count()
        check("a failed Run now shows its error as literal text, with no bold element in the toast",
              "<b>bad</b>" in toast_text and toast_bold == 0,
              "text=%r bold=%d" % (toast_text, toast_bold))
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")
        RUN_ANSWER.clear()
        RUN_ANSWER.update({"ok": True, "output": "done"})

        # saving the PSU control through its editor keeps its plugin read and adds no status command
        page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="PSU Control").locator(
            'button[title="Edit"]').click()
        wait_visible("#sce-device-editor")
        modal_done("#sce-device-editor")
        psu = page.evaluate("""() => ko.toJS(ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'))
            .devices()).find((d) => d.preset === 'psucontrol:psu')""")
        check("saving a plugin control through its editor keeps its read and adds no status",
              psu is not None and psu.get("read") == {"plugin": "psucontrol", "api": "get_psu_state"}
              and not psu.get("status"), json.dumps(psu))

        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets .sce-plugin-row")
        check("a present plugin preset starts ticked",
              plugin_rows.filter(has_text="PSU Control").locator("input").is_checked())
        plugin_rows.filter(has_text="PSU Control").locator("input").uncheck()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        body = save_settings() or {}
        plugin = (body.get("plugins") or {}).get("systemcommandeditor") or {}
        left_devices = [d.get("id") for d in plugin.get("devices") or []]
        left_actions = list((body.get("system") or {}).get("actions") or [])
        check("unticking the PSU preset removes its control, both commands and the empty Plugin devices divider",
              "psu_control" not in left_devices
              and not [a for a in left_actions if a.get("preset") == "psucontrol:psu" or a.get("group") == "psu_control"]
              and not [a for a in left_actions if a == {"action": "divider", "name": "Plugin devices"}],
              json.dumps({"devices": left_devices, "actions": left_actions[-3:]}))

        # Phase D4: the OctoPrint group. Webcam snapshot is a command-only link; G-code macros is an action control
        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets .sce-plugin-row")
        snap_row = page.locator("#sce-presets .sce-plugin-row", has_text="Webcam snapshot")
        send_row = page.locator("#sce-presets .sce-plugin-row", has_text="G-code macros")
        check("the picker's OctoPrint group offers Webcam snapshot, unticked",
              snap_row.count() == 1 and not snap_row.locator("input").is_checked())
        check("the G-code preset row reads G-code macros", send_row.count() == 1)
        snap_row.locator("input").check()
        send_row.locator("input").check()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        # in memory, before any save: a command-only preset's command carries no group and no role
        in_memory = page.evaluate("""() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const e = vm.entries().find(x => x.preset === 'octoprint:snapshot');
            return e ? {hasGroup: 'group' in e, hasRole: 'role' in e} : null;
        }""")
        check("the picked command-only preset's command carries no group and no role",
              in_memory == {"hasGroup": False, "hasRole": False}, json.dumps(in_memory))
        body = save_settings()
        if body is None:
            check("save after OctoPrint pick posts the settings", False, "no POST captured")
        else:
            devices = ((body.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or []
            system_actions = (body.get("system") or {}).get("actions") or []
            snap_device = [d for d in devices if d.get("preset") == "octoprint:snapshot"]
            snap = [a for a in system_actions if a.get("preset") == "octoprint:snapshot"]
            check("ticking Webcam snapshot and saving adds a standalone link with its marker and no control",
                  not snap_device and len(snap) == 1 and snap[0].get("url") == SNAPSHOT_URL and
                  "group" not in snap[0] and "role" not in snap[0] and "command" not in snap[0],
                  json.dumps({"devices": snap_device, "actions": snap}))

        # the Show a message macro opens with its G-code lines in a textarea; replacing them saves the new lines
        open_settings()
        edit_command("Show a message")
        gcode_before = page.locator("#sce-editor-gcode").input_value()
        check("a G-code macro opens with a G-code textarea holding its lines",
              gcode_before == "M117 Hello from System Commands", "gcode=%r" % gcode_before)
        page.fill("#sce-editor-gcode", "G28\nM117 done\n\n")
        modal_done("#sce-editor")
        body = save_settings()
        if body is None:
            check("save after G-code edit posts the settings", False, "no POST captured")
        else:
            saved_send = next((a for a in (body.get("system") or {}).get("actions") or []
                               if a.get("preset") == "octoprint:gcode"), {})
            check("replacing the lines and saving posts run gcode with the trimmed non-blank lines",
                  saved_send.get("run") == {"gcode": ["G28", "M117 done"]}, json.dumps(saved_send))

        # clearing every G-code line shows the error and disables Done
        open_settings()
        edit_command("Show a message")
        page.fill("#sce-editor-gcode", "  \n\n")
        page.wait_for_timeout(200)
        error_text = page.locator("#sce-editor").inner_text()
        done_disabled = page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("clearing every G-code line shows the error and disables Done",
              "Enter at least one G-code line." in error_text and done_disabled,
              "done_disabled=%s" % done_disabled)
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")

        # Phase D6: a new command of kind Link saves its url and no command; an empty URL shows the error and
        # disables Done; its row shows the link as its run text
        open_settings()
        page.get_by_role("button", name="Add command").click()
        wait_visible("#sce-editor")
        page.fill("#sce-editor-name", "Open printer page")
        page.select_option("#sce-editor-kind", value="link")
        page.fill("#sce-editor-url", "")
        page.wait_for_timeout(200)
        error_text = page.locator("#sce-editor").inner_text()
        done_disabled = page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("an empty URL shows the error and disables Done",
              "A URL is required for a link." in error_text and done_disabled, "done_disabled=%s" % done_disabled)
        page.fill("#sce-editor-url", "http://printer.local/page")
        # a link joins only an action control, as its run button
        offered = page.evaluate("""() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const ids = Array.from(document.querySelectorAll('#sce-editor-device option')).map(o => o.value).filter(v => v);
            return ids.map(id => (vm.devices().find(d => d.id === id) || {}).type);
        }""")
        check("a Link command's Control list offers only action controls",
              len(offered) > 0 and all(t == "action" for t in offered), "types=%s" % offered)
        page.select_option("#sce-editor-device", index=1)
        page.wait_for_timeout(200)
        roles = page.locator("#sce-editor .sce-role option").evaluate_all("os => os.map(o => o.value)")
        check("a Link command in a control offers only the run role", roles == ["", "run"], "roles=%s" % roles)
        page.select_option("#sce-editor-device", value="")
        modal_done("#sce-editor")
        code = command_row("Open printer page").locator("code.sce-cmd").text_content().strip()
        check("the Custom commands row shows the link as Link: <url>",
              code == "Link: http://printer.local/page", "code=%r" % code)
        body = save_settings()
        if body is None:
            check("save after the Link command posts the settings", False, "no POST captured")
        else:
            saved = next((a for a in (body.get("system") or {}).get("actions") or []
                          if a.get("name") == "Open printer page"), {})
            check("a new Link command saves its url and no command or run",
                  saved.get("url") == "http://printer.local/page" and "command" not in saved and "run" not in saved,
                  json.dumps(saved))

        # Phase D7: reopening the saved link shows kind Link with its URL; with a confirmation on, the preview shows
        # its link text; unticking it again leaves the saved link as it was
        open_settings()
        edit_command("Open printer page")
        kind_value = page.locator("#sce-editor-kind").input_value()
        url_value = page.locator("#sce-editor-url").input_value()
        check("reopening a saved link command shows kind Link with its URL",
              kind_value == "link" and url_value == "http://printer.local/page",
              "kind=%r url=%r" % (kind_value, url_value))
        page.check("#sce-editor label:has-text('Ask for confirmation before running') input")
        preview = page.locator("#sce-editor pre.sce-preview-command").inner_text().strip()
        check("with a confirmation on, the preview shows Link: <url> as the link is typed",
              preview == "Link: http://printer.local/page", "preview=%r" % preview)
        page.uncheck("#sce-editor label:has-text('Ask for confirmation before running') input")
        modal_done("#sce-editor")
        saved_link = page.evaluate("""() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const e = vm.entries().find(x => x.name === 'Open printer page');
            return e ? {url: e.url, hasCommand: 'command' in e, hasRun: 'run' in e} : null;
        }""")
        check("saving the link unchanged keeps its url and no command or run",
              saved_link == {"url": "http://printer.local/page", "hasCommand": False, "hasRun": False},
              json.dumps(saved_link))

        # Phase D5: an undetected webcam preset shows its label and stays tickable; PSU and Grbl (no available key)
        # show no label; ticking Grbl and the webcam preset posts Grbl's icon-bearing command and the webcam switch
        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets .sce-plugin-row")
        webcam_row = page.locator("#sce-presets .sce-plugin-row", has_text="Webcam (ustreamer)")
        psu_row = page.locator("#sce-presets .sce-plugin-row", has_text="PSU Control")
        grbl_row = page.locator("#sce-presets .sce-plugin-row", has_text="Grbl")
        webcam_text = webcam_row.inner_text()
        check("an undetected webcam preset shows Not detected on this system and stays tickable",
              "Not detected on this system" in webcam_text and webcam_row.locator("input").is_enabled(),
              "text=%r" % webcam_text)
        check("the PSU Control and Grbl rows show no label",
              "Not detected" not in psu_row.inner_text() and "Not detected" not in grbl_row.inner_text())
        grbl_row.locator("input").check()
        webcam_row.locator("input").check()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        body = save_settings()
        if body is None:
            check("save after Grbl and webcam picks posts the settings", False, "no POST captured")
        else:
            plugin = (body.get("plugins") or {}).get("systemcommandeditor") or {}
            devices = plugin.get("devices") or []
            saved = list((body.get("system") or {}).get("actions") or [])
            home = next((a for a in saved if a.get("action") == "grbl_home"), {})
            check("ticking Grbl and saving posts its Home run with the icon the fixture gives it",
                  home.get("run") == {"plugin": "bettergrblsupport", "api": "home"}
                  and home.get("icon") == "fa-solid fa-house", json.dumps(home))
            reset = next((a for a in saved if a.get("action") == "grbl_reset"), {})
            check("ticking Grbl carries each command's control and printer conditions into the saved entries",
                  home.get("when_control") == {"mode": "only", "states": ["Idle", "Alarm"], "effect": "disable"}
                  and reset.get("when") == {"mode": "never", "states": ["printing"], "effect": "disable"},
                  json.dumps([home, reset]))
            web = [d for d in devices if d.get("preset") == "webcam:ustreamer"]
            check("ticking the webcam preset posts a switch with its status and marker",
                  len(web) == 1 and web[0].get("type") == "switch"
                  and web[0].get("status") == "systemctl is-active ustreamer.service", json.dumps(web))
            web_cmds = [a for a in saved if a.get("preset") == "webcam:ustreamer"]
            check("the webcam preset's three entries carry command, role and icon, and no run",
                  len(web_cmds) == 3 and all("run" not in a and a.get("command") and a.get("role")
                                             and a.get("icon") for a in web_cmds)
                  and [a.get("role") for a in web_cmds] == ["on", "off", "restart"],
                  json.dumps(web_cmds))
        # put the picker back as it was: untick both and save
        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets .sce-plugin-row")
        page.locator("#sce-presets .sce-plugin-row", has_text="Grbl").locator("input").uncheck()
        page.locator("#sce-presets .sce-plugin-row", has_text="Webcam (ustreamer)").locator("input").uncheck()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        save_settings()

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

        # Phase F: Duplicate copies the saved command or control under the first free "Copy of" name
        state_js = """() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            return {entries: ko.toJS(vm.entries()), devices: ko.toJS(vm.devices())};
        }"""
        edit_command("Back up OctoPrint now")
        page.locator("#sce-editor .modal-footer button:has-text('Duplicate')").click()
        wait_hidden("#sce-editor")
        edit_command("Back up OctoPrint now")
        page.locator("#sce-editor .modal-footer button:has-text('Duplicate')").click()
        wait_hidden("#sce-editor")
        st = page.evaluate(state_js)
        names = [e.get("name") for e in st["entries"]]
        at = names.index("Back up OctoPrint now") if "Back up OctoPrint now" in names else -1
        copies = [e for e in st["entries"] if (e.get("name") or "").startswith("Copy")]
        # the command is a run member of Upkeep, so its copies keep the control and the run role, and drop the preset
        check("a run command's copy is named Copy of, then Copy 2 of, sits after the original, keeps its control and role, and drops the preset",
              at >= 0 and names[at + 1:at + 3] == ["Copy 2 of Back up OctoPrint now", "Copy of Back up OctoPrint now"]
              and sorted(e.get("action") for e in copies) == ["copy_2_of_back_up_octoprint_now", "copy_of_back_up_octoprint_now"]
              and all(e.get("group") == "upkeep" and e.get("role") == "run" and not e.get("preset") for e in copies),
              json.dumps({"after": names[at + 1:at + 3], "copies": copies}))
        # a plain command has no control: its copy drops group, role and value, the other half of the rule
        plain = page.evaluate("""() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const e = vm.entries().find(x => x.action !== 'divider' && !x.group && !x.device && x.command); return e ? e.name : null; }""")
        if plain is not None:
            edit_command(plain)
            page.locator("#sce-editor .modal-footer button:has-text('Duplicate')").click()
            wait_hidden("#sce-editor")
            st = page.evaluate(state_js)
            plain_copies = [e for e in st["entries"] if (e.get("name") or "").startswith("Copy") and (e.get("name") or "").endswith(" of " + plain)]
            check("a plain command's copy drops group, role and value",
                  len(plain_copies) == 1 and not any(plain_copies[0].get(k) for k in ("group", "role", "value", "preset")),
                  json.dumps(plain_copies))
        else:
            check("a plain command is listed for the copy check", False, "no plain command in the list")
        open_cpu = """() => {
            const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            vm.editDevice(vm.devices().find((d) => d.id === 'cpu'));
        }"""
        page.evaluate(open_cpu)
        wait_visible("#sce-device-editor")
        modal_done("#sce-device-editor")
        st = page.evaluate(state_js)
        check("editing a preset's control keeps its marker",
              [d.get("preset") for d in st["devices"] if d.get("id") == "cpu"] == ["cpu"], json.dumps(st["devices"]))
        page.evaluate(open_cpu)
        wait_visible("#sce-device-editor")
        page.locator("#sce-device-editor .modal-footer button:has-text('Duplicate')").click()
        wait_hidden("#sce-device-editor")
        st = page.evaluate(state_js)
        copy = [d for d in st["devices"] if d.get("name") == "Copy of CPU temperature"]
        acts = [e.get("device") for e in st["entries"]]
        disk_at = acts.index("cpu") if "cpu" in acts else -1
        check("a control copy is the control alone, named Copy of, keyed from its name, with no marker; a readout copy is placed after the original",
              len(copy) == 1 and copy[0].get("id") == "copy_of_cpu_temperature" and not copy[0].get("preset")
              and copy[0].get("type") == "readout" and disk_at >= 0 and acts[disk_at + 1] == "copy_of_cpu_temperature",
              json.dumps({"copy": copy, "placements": acts}))
        page.get_by_role("button", name="Add command").click()
        wait_visible("#sce-editor")
        check("a new command offers no Duplicate", page.locator("#sce-editor .modal-footer button:has-text('Duplicate')").count() == 0)
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")

        # Phase S: a command's control condition. A control with a plugin read lists its words as checkboxes, a control
        # with a shell status gets a text box, both save when_control, Done waits for a picked state, and a new control
        # clears the condition.
        plugin_saved = state["current"].setdefault("plugins", {}).setdefault("systemcommandeditor", {})
        plugin_saved.setdefault("devices", []).append(dict(GRBL_DEVICE, read=dict(GRBL_DEVICE["read"])))
        plugin_saved["devices"].append(dict(SPINDLE_DEVICE, read=dict(SPINDLE_DEVICE["read"])))
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("typeof ko !== 'undefined' && document.querySelector('#navbar_show_settings') !== null",
                               timeout=20000)
        open_settings()
        page.get_by_role("button", name="Add control").click()
        wait_visible("#sce-device-editor")
        page.select_option("#sce-device-type", label="Action")
        page.fill("#sce-device-name", "Heater")
        page.fill("#sce-device-status", "echo heat")
        modal_done("#sce-device-editor")

        page.get_by_role("button", name="Add command").click()
        wait_visible("#sce-editor")
        page.fill("#sce-editor-name", "Grbl unlock")
        page.fill("#sce-editor-command", "echo unlock")
        page.select_option("#sce-editor-device", label="Grbl")
        page.select_option("#sce-editor .sce-role", value="run")
        page.check("#sce-editor label:has-text('Limit to Grbl states') input")
        wait_visible("#sce-editor .sce-state-picks")
        boxes = page.locator("#sce-editor .sce-state-picks label").count()
        check("a control with a plugin read lists its words as one checkbox each",
              boxes == len(GRBL_WORDS) and page.locator("#sce-editor input[data-bind='textInput: control.typed']").count() == 0,
              "boxes=%s" % boxes)
        page.check("#sce-editor .sce-state-picks label:has-text('Alarm') input")
        modal_done("#sce-editor")
        body = save_settings() or {}
        unlock = [a for a in ((body.get("system") or {}).get("actions") or [])
                  if isinstance(a, dict) and a.get("name") == "Grbl unlock"]
        check("ticking Alarm with Only when saves when_control with only Alarm, disabled",
              len(unlock) == 1 and unlock[0].get("when_control") == {"mode": "only", "states": ["Alarm"], "effect": "disable"},
              json.dumps(unlock))
        check("the states call asked the plugin read for its words",
              any("read" in c and c["read"].get("plugin") == "bettergrblsupport" for c in state_calls),
              json.dumps(state_calls))

        # the save closed the settings dialog; reopen it, as the earlier phases do, before editing the saved command
        open_settings()
        edit_command("Grbl unlock")
        page.select_option("#sce-editor-device", label="Heater")
        page.wait_for_timeout(300)
        cleared = not page.is_checked("#sce-editor label:has-text('Limit to Heater states') input")
        check("changing the control clears the control condition", cleared)
        page.locator("#sce-editor .modal-footer .btn:has-text('Cancel')").click()
        wait_hidden("#sce-editor")

        page.get_by_role("button", name="Add command").click()
        wait_visible("#sce-editor")
        page.fill("#sce-editor-name", "Heat on")
        page.fill("#sce-editor-command", "echo on")
        page.select_option("#sce-editor-device", label="Heater")
        page.select_option("#sce-editor .sce-role", value="run")
        page.check("#sce-editor label:has-text('Limit to Heater states') input")
        wait_visible("#sce-editor input[data-bind='textInput: control.typed']")
        page.wait_for_timeout(200)
        empty_error = page.locator("#sce-editor .help-block:visible", has_text="Pick at least one state.").count() == 1
        empty_disabled = page.locator("#sce-editor .modal-footer .btn-primary").is_disabled()
        check("enabled with nothing picked shows the error and disables Done", empty_error and empty_disabled,
              "error=%s disabled=%s" % (empty_error, empty_disabled))
        page.fill("#sce-editor input[data-bind='textInput: control.typed']", "Idle, Run")
        page.wait_for_timeout(200)
        modal_done("#sce-editor")
        body = save_settings() or {}
        heat = [a for a in ((body.get("system") or {}).get("actions") or [])
                if isinstance(a, dict) and a.get("name") == "Heat on"]
        check("a shell status control takes free words: Idle, Run saves states Idle and Run",
              len(heat) == 1 and heat[0].get("when_control") == {"mode": "only", "states": ["Idle", "Run"], "effect": "disable"},
              json.dumps(heat))
        check("a shell status asks the states call for its status line",
              any("status" in c and c["status"] == "echo heat" for c in state_calls), json.dumps(state_calls))

        # Phase C: a built-in command never offers the control condition, even on a control that has a read; with no
        # block offered there is nothing to tick, so the offer is the whole check
        open_settings()
        button = core_row("reboot")
        check("the built-in reboot command is listed for the check", button is not None, "actions=%s" % core_actions())
        if button is not None:
            button.click()
            wait_visible("#sce-editor")
            page.select_option("#sce-editor-device", label="Spindle")
            page.select_option("#sce-editor .sce-role", value="run")
            page.wait_for_timeout(300)
            offered = page.locator("#sce-editor label:has-text('Limit to Spindle states')").count()
            check("a built-in command offers no control condition block", offered == 0, "offered=%s" % offered)
            modal_done("#sce-editor")

        # Phase E: the MQTT presets. The picker offers the readout and the switch while the mqtt plugin is installed; the
        # switch's state topic is editable on its control, and its On command edits its topic and payload
        MQTT_INSTALLED["on"] = True
        open_settings()
        page.get_by_role("button", name="Add from presets").click()
        wait_visible("#sce-presets .sce-plugin-row")
        mqtt_readout_row = page.locator("#sce-presets .sce-plugin-row", has_text="MQTT readout")
        mqtt_switch_row = page.locator("#sce-presets .sce-plugin-row", has_text="MQTT switch")
        check("the presets picker offers an MQTT group with MQTT readout and MQTT switch",
              mqtt_readout_row.count() == 1 and mqtt_switch_row.count() == 1,
              "readout=%d switch=%d" % (mqtt_readout_row.count(), mqtt_switch_row.count()))
        mqtt_switch_row.locator("input").check()
        page.locator("#sce-presets .modal-footer .btn-primary").click()
        wait_hidden("#sce-presets")
        MQTT_INSTALLED["on"] = False
        body = save_settings() or {}
        mqtt_device = next((d for d in ((body.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or []
                            if d.get("preset") == "mqtt:switch"), None)
        check("adding the MQTT switch saves it with its state topic", mqtt_device is not None
              and mqtt_device.get("read") == {"mqtt": "home/switch/state"}, json.dumps(mqtt_device))

        open_settings()
        page.locator("#settings_plugin_systemcommandeditor .sce-device-row", has_text="MQTT switch").locator(
            'button[title="Edit"]').click()
        wait_visible("#sce-device-editor")
        topic_before = page.locator("#sce-device-mqtt-state").input_value()
        topic_editable = page.locator("#sce-device-mqtt-state").is_editable()
        check("the MQTT switch control shows its state topic in an editable input",
              topic_before == "home/switch/state" and topic_editable, "topic=%r editable=%s" % (topic_before, topic_editable))
        read_line_hidden = not visible("#sce-device-editor .sce-read-line")
        check("the MQTT switch control hides the read-only Reads line",
              read_line_hidden, "read line visible=%s" % (not read_line_hidden))
        page.fill("#sce-device-mqtt-state", "home/plug/state")
        modal_done("#sce-device-editor")
        body = save_settings() or {}
        mqtt_device = next((d for d in ((body.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or []
                            if d.get("preset") == "mqtt:switch"), None)
        check("saving the MQTT control stores read mqtt with the new state topic",
              mqtt_device is not None and mqtt_device.get("read") == {"mqtt": "home/plug/state"}, json.dumps(mqtt_device))

        open_settings()
        on_action = page.evaluate("""() => { const vm = ko.dataFor(document.querySelector('#settings_plugin_systemcommandeditor'));
            const e = vm.entries().find(x => x.preset === 'mqtt:switch' && x.role === 'on'); return e ? e.action : null; }""")
        on_button = page.locator("#settings_plugin_systemcommandeditor button[data-action='%s']" % on_action).first
        on_button.click()
        wait_visible("#sce-editor")
        topic_on = page.locator("#sce-editor-mqtt-topic").input_value()
        payload_on = page.locator("#sce-editor-mqtt-payload").input_value()
        check("the MQTT On command shows its topic and payload",
              topic_on == "home/switch/set" and payload_on == "ON", "topic=%r payload=%r" % (topic_on, payload_on))
        run_line_hidden = not visible("#sce-editor .sce-run-line")
        check("the MQTT On command dialog hides the read-only Runs line",
              run_line_hidden, "run line visible=%s" % (not run_line_hidden))
        page.fill("#sce-editor-mqtt-payload", "1")
        modal_done("#sce-editor")
        body = save_settings() or {}
        on_entry = next((a for a in (body.get("system") or {}).get("actions") or []
                         if isinstance(a, dict) and a.get("preset") == "mqtt:switch" and a.get("role") == "on"), {})
        check("saving the MQTT On command stores the new payload and keeps its topic",
              (on_entry.get("run") or {}).get("args") == {"topic": "home/switch/set", "payload": "1"}, json.dumps(on_entry))

        list_ops_checks()

        browser.close()
    print("elapsed: %.1f s" % (time.time() - started), flush=True)


def main():
    run_probe()
    failed = results.count(False)
    print("RESULT: %d passed, %d failed, base %s" % (len(results) - failed, failed, BASE), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
