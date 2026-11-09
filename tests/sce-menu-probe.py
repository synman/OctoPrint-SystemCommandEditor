#!/usr/bin/env python3
"""Browser probe for the System Command Editor System menu.

Run: ~/.virtualenvs/main/bin/python tests/sce-menu-probe.py   (env SCE_BASE, default http://127.0.0.1:5097)

Every API the menu uses is answered by page.route with a fixture; no server setting is changed.
Prints one PASS or FAIL line per check and exits non-zero on any FAIL.
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SCE_BASE", "http://127.0.0.1:5097")
TOGGLE = "#navbar_systemmenu > a"
PLUGIN_URL = "**/api/plugin/systemcommandeditor"
COMMANDS_URL = "**/api/system/commands"
RUN_URL = "**/api/system/commands/custom/*"
SYNC_DELAY = 1.5    # seconds a sync command stays pending before its answer
STATUS_HOLD = 2.0   # seconds the older status answer is held back

DEVICES = [
    {"id": "laser", "type": "switch", "name": "Laser", "icon": "fa-solid fa-bolt", "status": "laser-state",
     "on_pattern": "ON", "off_pattern": "OFF", "section": "Machine"},
    {"id": "res", "type": "selector", "name": "Resolution", "icon": "fa-solid fa-ruler", "status": "res-state",
     "icon_color": "#0000ff", "icon_invert": True,
     "section": "Machine"},
    {"id": "disk", "type": "readout", "name": "Disk", "icon": "fa-solid fa-hard-drive", "icon_color": "#ff0000",
     "status": "disk-state"},
    {"id": "cam", "type": "link", "name": "Camera", "icon": "fa-solid fa-video", "url": "http://example.test/cam",
     "section": "Machine"},
    {"id": "nost", "type": "switch", "name": "No-status switch", "icon": "fa-solid fa-plug",
     "on_pattern": "", "off_pattern": "", "section": "Machine"},
    {"id": "ghost", "type": "switch", "name": "Ghost switch", "icon": "fa-solid fa-ghost", "status": "ghost-state",
     "section": "Machine"},
    {"id": "upt", "type": "readout", "name": "Uptime", "icon": "fa-solid fa-clock", "status": "upt-state"},
    {"id": "bkup", "type": "action", "name": "Upkeep", "icon": "fa-solid fa-box-archive", "status": "bkup-state"},
    {"id": "plug", "type": "switch", "name": "Plug", "icon": "fa-solid fa-plug",
     "read": {"plugin": "psucontrol", "helper": "get_psu_state"}, "on_pattern": "^true$", "off_pattern": "^false$",
     "section": "Machine"},
    {"id": "tplink", "type": "switch", "name": "Smart plug", "icon": "fa-solid fa-power-off",
     "read": {"plugin": "tplinksmartplug", "helper": "get_state", "live": False},
     "on_pattern": "^on$", "off_pattern": "^off$", "section": "Machine"},
    {"id": "grbl", "type": "action", "name": "Grbl", "icon": "fa-solid fa-microchip", "status": "grbl-state"},
]

# devices the menu is expected to draw: "ghost" has no member in core's command list, so it draws no row
DRAWN = [d for d in DEVICES if d["id"] != "ghost"]

ENTRIES = {
    "disk": {"name": "Disk", "device": "disk"},
    "upt": {"name": "Uptime", "device": "upt"},
    "bkup_run": {"name": "Back up now", "command": "bkup-cmd", "group": "bkup", "role": "run",
                 "icon": "fa-solid fa-box-archive", "async": False, "confirm": "", "value": ""},
    "laser_on": {"name": "Laser on", "command": "laser-on-cmd", "group": "laser", "role": "on",
                 "icon": "fa-solid fa-lightbulb", "async": False, "confirm": "", "value": "", "when": {
                     "mode": "never", "states": ["printing"], "effect": "disable"}},
    "laser_off": {"name": "Laser off", "command": "laser-off-cmd", "group": "laser", "role": "off",
                  "icon": "fa-solid fa-circle", "async": False, "confirm": "", "value": ""},
    "laser_restart": {"name": "Restart laser", "command": "laser-restart-cmd", "group": "laser", "role": "restart",
                      "icon": "fa-solid fa-arrows-rotate", "async": False, "confirm": "", "value": ""},
    "res_1080": {"name": "1080p", "command": "res-1080-cmd", "group": "res", "role": "choice", "value": "1080",
                 "icon": "", "async": False, "confirm": ""},
    "res_720": {"name": "720p", "command": "res-720-cmd", "group": "res", "role": "choice", "value": "720",
                "icon": "", "async": False, "confirm": ""},
    "nost_on": {"name": "No-status on", "command": "nost-on-cmd", "group": "nost", "role": "on",
                "icon": "fa-solid fa-lightbulb", "async": False, "confirm": "", "value": ""},
    "nost_off": {"name": "No-status off", "command": "nost-off-cmd", "group": "nost", "role": "off",
                 "icon": "fa-solid fa-circle", "async": False, "confirm": "", "value": ""},
    "ghost_on": {"name": "Ghost on", "command": "ghost-on-cmd", "group": "ghost", "role": "on",
                 "icon": "fa-solid fa-lightbulb", "async": False, "confirm": "", "value": ""},
    "ghost_off": {"name": "Ghost off", "command": "ghost-off-cmd", "group": "ghost", "role": "off",
                  "icon": "fa-solid fa-circle", "async": False, "confirm": "", "value": ""},
    "plug_on": {"name": "Plug on", "run": {"plugin": "psucontrol", "helper": "turn_psu_on"}, "group": "plug",
                "role": "on", "icon": "fa-solid fa-lightbulb", "async": False, "confirm": "Turn the plug on?", "value": ""},
    "plug_off": {"name": "Plug off", "run": {"plugin": "psucontrol", "helper": "turn_psu_off"}, "group": "plug",
                 "role": "off", "icon": "fa-solid fa-circle", "async": False, "confirm": "", "value": ""},
    "plug_fail": {"name": "Failing plug command", "run": {"plugin": "psucontrol", "helper": "turn_psu_off"},
                  "group": "", "role": "", "icon": "fa-solid fa-gear", "async": False, "confirm": "", "value": ""},
    "plug_blocked": {"name": "Blocked plug command", "run": {"plugin": "psucontrol", "helper": "turn_psu_on"},
                     "group": "", "role": "", "icon": "fa-solid fa-gear", "async": False, "confirm": "", "value": ""},
    "tplink_on": {"name": "Smart plug on", "command": "tplink-on-cmd", "group": "tplink", "role": "on",
                  "icon": "fa-solid fa-lightbulb", "async": False, "confirm": "", "value": ""},
    "tplink_off": {"name": "Smart plug off", "command": "tplink-off-cmd", "group": "tplink", "role": "off",
                   "icon": "fa-solid fa-circle", "async": False, "confirm": "", "value": ""},
    "sync_action": {"name": "Sync action", "command": "sync-cmd", "group": "", "role": "", "value": "",
                    "icon": "fa-solid fa-gear", "async": False, "confirm": ""},
    "sync_fail": {"name": "Failing sync action", "command": "fail-cmd", "group": "", "role": "", "value": "",
                  "icon": "fa-solid fa-gear", "async": False, "confirm": ""},
    "sync_cancel": {"name": "Cancel test action", "command": "cancel-cmd", "group": "", "role": "", "value": "",
                    "icon": "fa-solid fa-gear", "async": False, "confirm": "Run the cancel test action?"},
    "async_action": {"name": "Async action", "command": "async-cmd", "group": "", "role": "", "value": "",
                     "icon": "fa-solid fa-bolt-lightning", "async": True,
                     "confirm": "Run the async action now?"},
    # control-state members of Grbl: Unlock only while Alarm, Home only while Idle or Alarm, Sleep never while Sleep
    "grbl_unlock": {"name": "Unlock", "command": "unlock-cmd", "group": "grbl", "role": "run",
                    "icon": "fa-solid fa-lock-open", "async": False, "confirm": "", "value": "",
                    "when_control": {"mode": "only", "states": ["Alarm"], "effect": "disable"}},
    "grbl_home": {"name": "Home", "command": "home-cmd", "group": "grbl", "role": "run",
                  "icon": "fa-solid fa-house", "async": False, "confirm": "", "value": "",
                  "when_control": {"mode": "only", "states": ["Idle", "Alarm"], "effect": "disable"}},
    "grbl_sleep": {"name": "Sleep", "command": "sleep-cmd", "group": "grbl", "role": "run",
                   "icon": "fa-solid fa-moon", "async": False, "confirm": "", "value": "",
                   "when_control": {"mode": "never", "states": ["Sleep"], "effect": "disable"}},
}

CORE = {
    "restart": {"icon": "fa-solid fa-arrows-rotate", "heading": "OctoPrint", "group": "", "role": "", "value": ""},
}

CORE_COMMANDS = {"restart": "sudo service octoprint restart"}

STATUS = {
    "laser": {"state": "off", "output": "", "exit": 0, "error": None},
    "res": {"state": "1080", "output": "", "exit": 0, "error": None},
    "disk": {"state": "42 GB free", "output": "", "exit": 0, "error": None},
    "upt": {"state": "3 days", "output": "", "exit": 0, "error": None},
    "bkup": {"state": "Last backup 2 h ago", "output": "", "exit": 0, "error": None},
    "plug": {"state": "off", "output": "", "exit": None, "error": None},
    "tplink": {"state": "on", "output": "", "exit": None, "error": None},
    "grbl": {"state": "Idle", "output": "Idle", "exit": 0, "error": None},
}

# controls whose read says live: false: a live check answers them skipped
NOT_LIVE = {d["id"] for d in DEVICES if d.get("read", {}).get("live") is False}

CUSTOM_LIST = [
    {"action": "divider", "name": "Machine"},
    {"action": "laser_on", "name": "Laser on", "command": "laser-on-cmd"},
    {"action": "laser_off", "name": "Laser off", "command": "laser-off-cmd"},
    {"action": "laser_restart", "name": "Restart laser", "command": "laser-restart-cmd"},
    {"action": "res_1080", "name": "1080p", "command": "res-1080-cmd"},
    {"action": "res_720", "name": "720p", "command": "res-720-cmd"},
    {"action": "nost_on", "name": "No-status on", "command": "nost-on-cmd"},
    {"action": "nost_off", "name": "No-status off", "command": "nost-off-cmd"},
    {"action": "divider", "name": "Tools"},
    {"action": "bkup_run", "name": "Back up now", "command": "bkup-cmd"},
    {"action": "sync_action", "name": "Sync action", "command": "sync-cmd"},
    {"action": "sync_fail", "name": "Failing sync action", "command": "fail-cmd"},
    {"action": "sync_cancel", "name": "Cancel test action", "command": "cancel-cmd",
     "confirm": "Run the cancel test action?"},
    {"action": "async_action", "name": "Async action", "command": "async-cmd",
     "confirm": "Run the async action now?"},
    {"action": "plug_on", "name": "Plug on", "confirm": "Turn the plug on?"},
    {"action": "plug_off", "name": "Plug off"},
    {"action": "plug_fail", "name": "Failing plug command"},
    {"action": "plug_blocked", "name": "Blocked plug command"},
    {"action": "tplink_on", "name": "Smart plug on", "command": "tplink-on-cmd"},
    {"action": "tplink_off", "name": "Smart plug off", "command": "tplink-off-cmd"},
    {"action": "grbl_unlock", "name": "Unlock", "command": "unlock-cmd"},
    {"action": "grbl_home", "name": "Home", "command": "home-cmd"},
    {"action": "grbl_sleep", "name": "Sleep", "command": "sleep-cmd"},
    # readouts are drawn only at their placement entries: disk under Machine, upt under Status
    {"action": "disk", "name": "Disk", "device": "disk"},
    {"action": "divider", "name": "Status"},
    {"action": "upt", "name": "Uptime", "device": "upt"},
]

CORE_LIST = [{"action": "restart", "name": "Restart OctoPrint", "confirm": "Restart OctoPrint?"}]

results = []
calls = []        # custom command actions the page executed through OctoPrint's own execute endpoint
plugin_runs = []  # actions the page sent to this plugin's run command
status_log = []   # the ids named by each status POST
status_live = []  # the live flag of each status POST, in order
released = []     # states of the held status answers, in the order they were delivered
status_plan = []  # ("hold", seconds, overrides) entries, consumed by the next status POST
pending = []      # (due, route, http_status, body, tag) answers fulfilled by pump()
page = None


def check(name, ok, detail=""):
    results.append(bool(ok))
    suffix = "" if ok or not detail else " -- " + detail
    print(("PASS: " if ok else "FAIL: ") + name + suffix, flush=True)


def status_body(ids, overrides, live=False):
    out = {}
    for i in ids:
        if live and i in NOT_LIVE:
            out[i] = {"state": None, "output": "", "exit": None, "error": None, "skipped": True}
            continue
        row = dict(STATUS.get(i, {"state": None, "output": "", "exit": 0, "error": None}))
        if i in overrides:
            row["state"] = overrides[i]
        out[i] = row
    return json.dumps({"results": out})


options = {"success_toast": False, "live_updates": False, "live_interval": 1}

SUCCESS_TOASTS = """() => [...document.querySelectorAll('.ui-pnotify')]
    .filter(n => /executed successfully/.test(n.innerText)).length"""


def handle_plugin_run(route, action):
    plugin_runs.append(action)
    if action == "plug_blocked":
        route.fulfill(status=409, content_type="application/json", body=json.dumps(
            {"ok": False, "error": "Not available while the printer is ready"}))
    elif action == "plug_fail":
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"ok": False, "output": "", "error": "plug unreachable"}))
    else:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"ok": True, "output": "", "error": None}))


def handle_plugin(route, request):
    if request.method == "POST":
        body = request.post_data_json or {}
        if body.get("command") == "run":
            handle_plugin_run(route, body.get("action"))
            return
        ids = body.get("ids", [])
        live = body.get("live") is True
        status_log.append(sorted(ids))
        status_live.append(live)
        if status_plan:
            _, hold, overrides = status_plan.pop(0)
            pending.append((time.time() + hold, route, 200, status_body(ids, overrides), "held"))
            return
        route.fulfill(status=200, content_type="application/json", body=status_body(ids, {}, live))
        return
    route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "devices": DEVICES, "core": CORE, "entries": ENTRIES, "core_commands": CORE_COMMANDS,
        "success_toast": options["success_toast"], "live_updates": options["live_updates"],
        "live_interval": options["live_interval"]}))


def handle_commands(route, request):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"core": CORE_LIST, "custom": CUSTOM_LIST}))


def handle_run(route, request):
    action = request.url.rstrip("/").split("/")[-1]
    calls.append(action)
    if action == "async_action":
        route.fulfill(status=204, body="")
        return
    if action == "sync_fail":
        pending.append((time.time() + SYNC_DELAY, route, 500, json.dumps({"error": "boom"}), None))
        return
    pending.append((time.time() + SYNC_DELAY, route, 204, "", None))


def pump(ms):
    """Let Playwright dispatch events and deliver held answers when their delay has passed."""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        now = time.time()
        for item in list(pending):
            due, route, http_status, body, tag = item
            if now < due:
                continue
            pending.remove(item)
            if http_status == 204:
                route.fulfill(status=204, body="")
            else:
                route.fulfill(status=http_status, content_type="application/json", body=body)
            if tag == "held":
                released.append(json.loads(body)["results"]["laser"]["state"])
        page.wait_for_timeout(50)


def wait_until(fn, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        pump(100)
    return bool(fn())


FIND_LI = """(action) => {
    const li = [...document.querySelectorAll('#navbar_systemmenu ul.dropdown-menu > li')]
        .find(l => { const d = ko.dataFor(l); return d && d.action === action; });
    if (!li) return null;
    return {cls: li.className, hidden: getComputedStyle(li).display === 'none'};
}"""

DEVICE_ROWS = """(id) => document.querySelectorAll('#navbar_systemmenu li.sce-device[data-device-id="' + id + '"]').length"""

BUTTONS = """(id) => [...document.querySelectorAll('#navbar_systemmenu li.sce-device[data-device-id="' + id + '"] button.sce-btn')]
    .map(b => ({title: b.title, cls: b.className, icon: (b.querySelector('i') || {}).className || ''}))"""

SPIN_IN = """(id) => document.querySelectorAll('#navbar_systemmenu li.sce-device[data-device-id="' + id + '"] i.fa-spin').length"""

# the class list of the icon on one button of a device row, "" when the button is not drawn
BUTTON_ICON_CLASS = """([id, title]) => {
    const i = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="' + id + '"] button[title="' + title + '"] i');
    return i ? i.className : "";
}"""

STANDALONE_ICON = """(action) => {
    const li = [...document.querySelectorAll('#navbar_systemmenu ul.dropdown-menu > li')]
        .find(l => { const d = ko.dataFor(l); return d && d.action === action; });
    const i = li && li.querySelector('a > i.sce-menu-icon');
    return i ? i.className : null;
}"""

CLICK_STANDALONE = """(action) => {
    const li = [...document.querySelectorAll('#navbar_systemmenu ul.dropdown-menu > li')]
        .find(l => { const d = ko.dataFor(l); return d && d.action === action; });
    const a = li && li.querySelector('a');
    if (!a) return false;
    a.click();
    return true;
}"""

MENU_OPEN = """() => {
    const ul = document.querySelector('#navbar_systemmenu > ul.dropdown-menu');
    return !!ul && getComputedStyle(ul).display !== 'none';
}"""

DIALOG_VISIBLE = """() => document.querySelectorAll('.modal pre').length > 0"""

# wantCancel: click the dialog's cancel button; otherwise the last button that is not cancel or close
CLICK_DIALOG = r"""(wantCancel) => {
    const modal = [...document.querySelectorAll('.modal')].find(m => getComputedStyle(m).display !== 'none');
    if (!modal) return null;
    const buttons = [...modal.querySelectorAll('button, a.btn')];
    const target = wantCancel
        ? buttons.find(b => /cancel/i.test(b.innerText || ''))
        : buttons.filter(b => !/cancel|close|^\s*[×x]\s*$/i.test(b.innerText || '')).pop();
    if (!target) return null;
    const text = target.innerText.trim();
    target.click();
    return text;
}"""

DIALOG_PRE = """() => {
    const modal = [...document.querySelectorAll('.modal')]
        .find(m => getComputedStyle(m).display !== 'none' && m.querySelector('pre'));
    return modal ? modal.querySelector('pre').textContent : null;
}"""

PRINT_STATE = """() => ({
    isPrinting: window.__sceProbePrinter ? window.__sceProbePrinter.isPrinting() : null,
    laserButtons: [...document.querySelectorAll('li.sce-device[data-device-id="laser"] button')]
        .map(b => b.title + ' | ' + b.className)
})"""

# printerStateViewModel is not on any global. Several view models share _processStateData and an isPrinting
# observable, so the scan also requires stateString (only PrinterStateViewModel has it)
FIND_PRINTER = """() => {
    for (const el of document.querySelectorAll('*')) {
        let d = null;
        try { d = ko.dataFor(el); } catch (e) { d = null; }
        if (d && typeof d._processStateData === 'function' && ko.isObservable(d.isPrinting) && 'stateString' in d) {
            window.__sceProbePrinter = d;
            return true;
        }
    }
    return false;
}"""

PROCESS_PRINTING = """() => {
    window.__sceProbePrinter._processStateData({text: "Printing", flags: {operational: true, printing: true,
        paused: false, error: false, ready: false, closedOrError: false, cancelling: false, pausing: false,
        sdReady: false}});
    return true;
}"""

STATUS_HEADING = """() => {
    const heads = [...document.querySelectorAll('#navbar_systemmenu li.dropdown-header.sce-added')]
        .filter(h => h.innerText.trim() === 'Status');
    const row = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="upt"]');
    return {heads: heads.length, rowFollows: heads.length === 1 && row !== null && heads[0].nextElementSibling === row};
}"""

# the Grbl control's member buttons in order (Unlock, Home, Sleep): title and whether the button is grayed out
GRBL_BUTTONS = """() => [...document.querySelectorAll('#navbar_systemmenu li.sce-device[data-device-id="grbl"] button.sce-btn')]
    .map(b => ({title: b.getAttribute('title'), disabled: b.classList.contains('disabled')}))"""

PLUG_ROW = """() => {
    const li = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="plug"]');
    if (!li) return null;
    return {state: ((li.querySelector('.sce-state') || {}).innerText || '').trim(),
            titles: [...li.querySelectorAll('button.sce-btn')].map(b => b.title)};
}"""

# how many toasts on screen carry the given text
TOAST_COUNT = """(text) => [...document.querySelectorAll('.ui-pnotify')].filter(n => n.innerText.includes(text)).length"""

LASER_OFF = """() => {
    const dot = document.querySelector('li.sce-device[data-device-id="laser"] .sce-dot.sce-off');
    return dot !== null;
}"""


def open_menu():
    if not page.evaluate(MENU_OPEN):
        page.click(TOGGLE)
        wait_until(lambda: page.evaluate(MENU_OPEN), 3)


def count_added():
    return page.evaluate("() => document.querySelectorAll('#navbar_systemmenu .sce-added').length")


def device_rows():
    return {d["id"]: page.evaluate(DEVICE_ROWS, d["id"]) for d in DRAWN}


def run_probe():
    global page
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context().new_page()
        page.route(PLUGIN_URL, handle_plugin)
        page.route(COMMANDS_URL, handle_commands)
        page.route(RUN_URL, handle_run)

        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_function("typeof ko !== 'undefined' && document.querySelector('#navbar_systemmenu') !== null",
                               timeout=20000)
        wait_until(lambda: page.evaluate(
            "() => document.querySelectorAll('li.sce-device').length === %d && "
            "document.querySelector('li.sce-device[data-device-id=\"laser\"] .sce-dot.sce-off') !== null" % len(DRAWN)), 10)
        open_menu()
        pump(300)

        # A. one row per device
        counts = device_rows()
        check("one row per device", all(v == 1 for v in counts.values()), json.dumps(counts))

        # B. member items hidden
        members = ["laser_on", "laser_off", "laser_restart", "res_1080", "res_720"]
        hidden = {a: page.evaluate(FIND_LI, a) for a in members}
        check("member items hidden", all(v and "sce-hidden" in v["cls"] and v["hidden"] for v in hidden.values()),
              json.dumps(hidden))

        # C. buttons and titles per switchView (laser state off: restart and on)
        buttons = page.evaluate(BUTTONS, "laser")
        titles = sorted(b["title"] for b in buttons)
        icon_ok = any("fa-rotate-right" in b["icon"] for b in buttons if b["title"] == "Restart laser") and \
            any("fa-play" in b["icon"] for b in buttons if b["title"] == "Laser on")
        dot_ok = page.locator('li.sce-device[data-device-id="laser"] .sce-dot.sce-off').count() == 1
        check("buttons and titles per switchView", titles == ["Laser on", "Restart laser"] and icon_ok and dot_ok,
              json.dumps({"titles": titles, "dot_off": dot_ok}))

        # D. sync button spins while pending and stops after
        page.locator('li.sce-device[data-device-id="laser"] button[title="Restart laser"]').click()
        pump(300)
        spun = page.evaluate(SPIN_IN, "laser") > 0
        busy = page.evaluate(BUTTON_ICON_CLASS, ["laser", "Restart laser"])
        # the busy icon (the default fa-spinner) replaces the button's own icon, and that one is not spinning
        check("sync button shows the busy icon spinning while pending",
              spun and "fa-spinner" in busy and "fa-spin" in busy and "fa-rotate-right" not in busy,
              "busy=%r" % busy)
        pump(int((SYNC_DELAY + 1.2) * 1000))
        still = page.evaluate(SPIN_IN, "laser") > 0
        check("sync button stops after", spun and not still, "spinning=%s" % still)
        flash = page.evaluate(BUTTON_ICON_CLASS, ["laser", "Restart laser"])
        check("sync button then shows the check or cross flash", "fa-check" in flash or "fa-xmark" in flash,
              "icon=%r" % flash)
        toasts = page.evaluate(SUCCESS_TOASTS)
        check("a finished sync run shows no success toast by default", toasts == 0, "toasts=%s" % toasts)

        # E. async never spins; consent dialog body carries the command in <pre>
        open_menu()
        page.evaluate(CLICK_STANDALONE, "async_action")
        wait_until(lambda: page.evaluate(DIALOG_VISIBLE), 6)
        pre = page.evaluate(DIALOG_PRE)
        check("consent dialog body contains the command inside pre", pre == "async-cmd", "pre=%r" % pre)
        confirmed = page.evaluate(CLICK_DIALOG, False)
        check("consent dialog confirm button found", confirmed is not None, "clicked=%r" % confirmed)
        spin_samples = []
        for _ in range(4):
            pump(120)
            spin_samples.append(page.evaluate(STANDALONE_ICON, "async_action") or "")
        check("async action never spins", "async_action" in calls and not any("fa-spin" in s for s in spin_samples),
              "icons=%s calls=%s" % (spin_samples, calls))

        # F. selector submenu shows the check on the current choice
        check_mark = page.evaluate("""() => {
            const row = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="res"]');
            if (!row) return null;
            const items = [...row.querySelectorAll('ul.dropdown-menu li')];
            const current = items.find(li => li.querySelector('i.fa-check'));
            return {label: row.querySelector('.sce-state').innerText.trim(),
                    current: current ? current.innerText.trim() : null};
        }""")
        check("selector submenu shows the check on the current choice",
              check_mark is not None and check_mark["current"] == "1080p" and check_mark["label"] == "1080p",
              json.dumps(check_mark))

        # G. readout text
        readout = page.evaluate("""() => {
            const li = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="disk"]');
            return li ? li.innerText.trim() : null;
        }""")
        check("readout shows name and state text",
              readout is not None and "Disk" in readout and "42 GB free" in readout, json.dumps(readout))

        # G2. an action row: its status text, then one button per run member, drawn with the member's icon
        open_menu()  # measured open: a closed menu has no layout, so every rect reads zero
        action_row = page.evaluate("""() => {
            const li = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="bkup"]');
            if (!li) return null;
            const state = li.querySelector('.sce-state');
            const buttons = [...li.querySelectorAll('button.sce-btn')].map(b => ({
                title: b.getAttribute('title'), icon: b.querySelector('i').className}));
            const name = li.querySelector('.sce-device-name > span:first-child');
            const rs = state && state.getBoundingClientRect(), rn = name && name.getBoundingClientRect();
            const under = !!(rs && rn) && rn.height > 0 && rs.height > 0 && rs.top >= rn.bottom - 1;
            return {name: name ? name.innerText.trim() : null,
                    state: state ? state.innerText.trim() : null, under: under, buttons: buttons};
        }""")
        check("action row's status sits under its name", action_row is not None and action_row["under"],
              json.dumps(action_row))
        check("action row shows its status text and one button with the member icon",
              action_row is not None and action_row["state"] == "Last backup 2 h ago"
              and len(action_row["buttons"]) == 1 and action_row["buttons"][0]["title"] == "Back up now"
              and "fa-box-archive" in action_row["buttons"][0]["icon"], json.dumps(action_row))

        # G3. the action button runs its member through the same custom command path as a switch verb
        open_menu()
        page.locator('li.sce-device[data-device-id="bkup"] button[title="Back up now"]').click()
        pump(600)
        if page.evaluate(MENU_OPEN):
            page.click(TOGGLE)     # close the menu again, as the other phases do
            pump(300)
        check("the action button runs its member through the custom command endpoint",
              "bkup_run" in calls, "calls=%s" % calls)

        # H. link href and target
        link = page.evaluate("""() => {
            const a = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="cam"] a');
            return a ? {href: a.getAttribute('href'), target: a.getAttribute('target'),
                         rel: a.getAttribute('rel')} : null;
        }""")
        check("link href and target", link is not None and link["href"] == "http://example.test/cam"
              and link["target"] == "_blank", json.dumps(link))

        # H2. a switch with no status command shows no dot and no word and both buttons; a switch with no member
        #     in core's command list draws no row
        nost_row = page.evaluate("""() => {
            const li = document.querySelector('#navbar_systemmenu li.sce-device[data-device-id="nost"]');
            if (!li) return null;
            return {dots: li.querySelectorAll('.sce-dot').length,
                    state: ((li.querySelector('.sce-state') || {}).textContent || '').trim(),
                    buttons: [...li.querySelectorAll('button.sce-btn')].map(b => b.title)};
        }""")
        check("switch without status shows no dot, no word, and both on and off buttons",
              nost_row is not None and nost_row["dots"] == 0 and nost_row["state"] == ""
              and sorted(nost_row["buttons"]) == ["No-status off", "No-status on"], json.dumps(nost_row))
        ghost_rows = page.locator('li.sce-device[data-device-id="ghost"]').count()
        check("switch whose members are absent from the command list draws no row", ghost_rows == 0,
              "rows=%s" % ghost_rows)

        # I. opening the System menu sends one status POST naming every device that has a status
        status_log.clear()
        page.click(TOGGLE)
        pump(400)
        expected = sorted(d["id"] for d in DEVICES if d.get("status") or d.get("read"))
        check("menu open sends a status POST for every device with a status",
              bool(status_log) and status_log[-1] == expected,
              "expected=%s sent=%s" % (expected, status_log))

        # J. an answer to an older check does not overwrite a newer one
        open_menu()
        released.clear()
        status_plan.append(("hold", STATUS_HOLD, {"laser": "on"}))
        page.click(TOGGLE)     # check 1: its answer is held back and says On
        pump(200)
        page.click(TOGGLE)     # check 2: answered at once and says Off
        wait_until(lambda: page.evaluate(LASER_OFF), 4)
        pump(STATUS_HOLD * 1000 + 600)   # the held On answer arrives after the newer Off
        laser_text = page.locator('li.sce-device[data-device-id="laser"] .sce-state').inner_text().strip()
        check("older status answer does not overwrite the newer one",
              released == ["on"] and page.evaluate(LASER_OFF) and laser_text == "Off",
              "released=%s text=%r" % (released, laser_text))

        # J2. a re-check keeps the last known state on screen while it runs, so the menu does not reflow
        if page.evaluate(MENU_OPEN):
            page.click(TOGGLE)     # close it; that click's own check is answered at once
            pump(600)
        released.clear()
        status_plan.append(("hold", STATUS_HOLD, {"laser": "off"}))
        page.click(TOGGLE)         # one open, one held check
        pump(300)
        during = page.locator('li.sce-device[data-device-id="laser"] .sce-state').inner_text().strip()
        state_spins = page.evaluate("""() => document.querySelectorAll(
            'li.sce-device[data-device-id="laser"] .sce-state i.fa-spin').length""")
        pump(STATUS_HOLD * 1000 + 600)
        check("a re-check keeps the last state shown, with no spinner",
              during == "Off" and state_spins == 0 and released == ["off"],
              "during=%r spins=%s released=%s" % (during, state_spins, released))

        # K. cancelling the consent dialog leaves the plain icon with no check or cross
        open_menu()
        page.evaluate(CLICK_STANDALONE, "sync_cancel")
        dialog = wait_until(lambda: page.evaluate(DIALOG_VISIBLE), 6)
        cancelled = page.evaluate(CLICK_DIALOG, True) if dialog else None
        pump(1200)
        icon = page.evaluate(STANDALONE_ICON, "sync_cancel") or ""
        check("cancelled consent leaves the plain icon",
              cancelled is not None and "fa-gear" in icon and "fa-check" not in icon and "fa-xmark" not in icon
              and "sync_cancel" not in calls,
              "cancel=%r icon=%r calls=%s" % (cancelled, icon, calls))

        # L. a sync standalone action whose execute POST answers 500 ends with the cross icon
        open_menu()
        page.evaluate(CLICK_STANDALONE, "sync_fail")
        crossed = wait_until(lambda: "fa-xmark" in (page.evaluate(STANDALONE_ICON, "sync_fail") or ""), 8)
        icon = page.evaluate(STANDALONE_ICON, "sync_fail") or ""
        check("failed sync standalone ends with the cross icon", crossed and "sync_fail" in calls,
              "icon=%r calls=%s" % (icon, calls))

        # M. printing disables the conditioned button and its title says why
        found = page.evaluate(FIND_PRINTER)
        if found:
            page.evaluate(PROCESS_PRINTING)
        disabled = page.locator(
            'li.sce-device[data-device-id="laser"] button[title="Not available while printing"]')
        wait_until(lambda: disabled.count() == 1, 3)
        disabled_ok = disabled.count() == 1 and "disabled" in (disabled.first.get_attribute("class") or "")
        check("printing disables the conditioned button and its title says why", found and disabled_ok,
              "printer VM found=%s buttons=%s" % (found, json.dumps(page.evaluate(PRINT_STATE))))

        # N. re-render leaves no duplicate nodes
        before = count_added()
        page.click(TOGGLE)
        pump(300)
        page.click(TOGGLE)
        pump(600)
        after = count_added()
        rows = device_rows()
        check("re-render leaves no duplicate sce-added nodes",
              before == after and all(v == 1 for v in rows.values()),
              "before=%s after=%s rows=%s" % (before, after, json.dumps(rows)))

        # P. the Status divider gets one heading, and the upt readout row follows it, also after a re-render
        open_menu()
        heading = page.evaluate(STATUS_HEADING)
        check("one Status heading exists and the upt readout row follows it after a re-render",
              heading["heads"] == 1 and heading["rowFollows"], json.dumps(heading))

        # T. an icon color set on a device colors its icon; one without a color keeps the menu's own
        colors = page.evaluate("""() => ({
            disk: getComputedStyle(document.querySelector('li.sce-device[data-device-id="disk"] i.sce-menu-icon')).color,
            diskInline: document.querySelector('li.sce-device[data-device-id="disk"] i.sce-menu-icon').style.color,
            laserInline: document.querySelector('li.sce-device[data-device-id="laser"] i.sce-menu-icon').style.color})""")
        check("a device's icon color colors its icon, and others keep the menu's color",
              colors["disk"] == "rgb(255, 0, 0)" and colors["laserInline"] == "", json.dumps(colors))
        inv = page.evaluate("""() => { const i = document.querySelector('li.sce-device[data-device-id="res"] i.sce-menu-icon');
            const s = getComputedStyle(i); return {cls: i.classList.contains('sce-icon-inverted'), bg: s.backgroundColor, fg: s.color}; }""")
        check("an inverted icon is a light glyph on a badge of its color",
              inv["cls"] and inv["bg"] == "rgb(0, 0, 255)" and inv["fg"] == "rgb(255, 255, 255)", json.dumps(inv))

        # R. a plugin-backed switch: its state comes from its read, and its buttons run through the plugin's run command
        open_menu()
        plug_row = page.evaluate(PLUG_ROW)
        # the Off button is hidden while the state is off, as on any switch; the On button is the one shown
        check("the Plug row shows Off and its On button",
              plug_row is not None and plug_row["state"] == "Off" and "Plug on" in plug_row["titles"],
              json.dumps(plug_row))

        page.locator('li.sce-device[data-device-id="plug"] button[title="Plug on"]').click()
        dialog = wait_until(lambda: page.evaluate(DIALOG_VISIBLE), 6)
        plug_pre = page.evaluate(DIALOG_PRE) if dialog else None
        check("the plug's consent dialog shows what will run", plug_pre == "psucontrol: turn_psu_on",
              "pre=%r" % plug_pre)
        page.evaluate(CLICK_DIALOG, False)
        ran = wait_until(lambda: "plug_on" in plugin_runs, 6)
        pump(300)
        plug_flash = page.evaluate(BUTTON_ICON_CLASS, ["plug", "Plug on"])
        check("confirming posts the run to the plugin, never to OctoPrint's execute, and shows the check",
              ran and "plug_on" not in calls and "fa-check" in plug_flash,
              "plugin_runs=%s calls=%s icon=%r" % (plugin_runs, calls, plug_flash))

        page.evaluate(CLICK_STANDALONE, "plug_fail")
        crossed = wait_until(lambda: "fa-xmark" in (page.evaluate(STANDALONE_ICON, "plug_fail") or ""), 6)
        pump(300)
        fail_toasts = page.evaluate(TOAST_COUNT, "plug unreachable")
        check("a plugin run answering ok false ends in the cross and an error toast with its error",
              crossed and fail_toasts >= 1, "toasts=%s runs=%s" % (fail_toasts, plugin_runs))

        page.evaluate(CLICK_STANDALONE, "plug_blocked")
        blocked = wait_until(lambda: "fa-xmark" in (page.evaluate(STANDALONE_ICON, "plug_blocked") or ""), 6)
        pump(300)
        blocked_toasts = page.evaluate(TOAST_COUNT, "Not available while the printer is ready")
        check("a plugin run answering 409 ends in the cross and an error toast with its error",
              blocked and blocked_toasts >= 1 and "plug_blocked" in plugin_runs and "plug_blocked" not in calls,
              "toasts=%s runs=%s" % (blocked_toasts, plugin_runs))

        # P. live updates are off by default: an open menu sends no further status checks
        open_menu()
        pump(800)
        status_log.clear()
        pump(2500)
        check("an open menu sends no further status checks by default", status_log == [], "sent=%s" % status_log)

        # O. with the success toast turned on, a finished sync run shows core's success toast again
        options["success_toast"] = True
        page.reload(wait_until="domcontentloaded")
        wait_until(lambda: page.evaluate(
            "() => document.querySelector('li.sce-device[data-device-id=\"laser\"] .sce-dot.sce-off') !== null"), 15)
        open_menu()
        page.locator('li.sce-device[data-device-id="laser"] button[title="Restart laser"]').click()
        pump(int((SYNC_DELAY + 1.0) * 1000))
        toasts = page.evaluate(SUCCESS_TOASTS)
        check("with the option on, a finished sync run shows the success toast", toasts == 1, "toasts=%s" % toasts)

        # Q. with live updates on, an open menu re-checks every interval, and a closed one stops
        options["live_updates"] = True
        page.reload(wait_until="domcontentloaded")
        wait_until(lambda: page.evaluate(
            "() => document.querySelector('li.sce-device[data-device-id=\"laser\"] .sce-dot.sce-off') !== null"), 15)
        open_menu()
        pump(800)
        status_log.clear()
        status_live.clear()
        pump(3500)
        open_checks = len(status_log)
        # captured while the menu is still open: only the interval checks so far, before the closing click
        interval_live = list(status_live)
        live_tplink = page.locator('li.sce-device[data-device-id="tplink"] .sce-state').inner_text().strip()
        page.click(TOGGLE)
        pump(1500)
        closed = not page.evaluate(MENU_OPEN)
        status_log.clear()
        pump(2500)
        check("with live updates on, an open menu re-checks each second and stops when closed",
              open_checks >= 2 and closed and status_log == [],
              "open_checks=%s closed=%s after_close=%s" % (open_checks, closed, status_log))

        # Q2. the interval checks carry live true, and a skipped answer leaves the plugin control on its last state
        check("live interval checks send live true, and the tplink row keeps its last state when skipped",
              len(interval_live) >= 2 and all(interval_live) and live_tplink == "On",
              "interval_live=%s tplink=%r" % (interval_live, live_tplink))

        # S. a member command's when_control grays its button from the control's status word: Unlock needs Alarm,
        #    Home needs Idle or Alarm, Sleep is blocked only by Sleep. An errored or unknown word blocks nothing.
        def grbl_phase(output, error):
            STATUS["grbl"]["output"] = output
            STATUS["grbl"]["error"] = error
            if page.evaluate(MENU_OPEN):
                page.click(TOGGLE)
                pump(300)
            open_menu()     # opening the menu checks the status again

        def grbl_matches(expected):
            rows = page.evaluate(GRBL_BUTTONS) or []
            if len(rows) != 3:
                return False
            return all(r["disabled"] == d and r["title"] == t for r, (d, t) in zip(rows, expected))

        idle = [(True, "Not available while Grbl is Idle"), (False, "Home"), (False, "Sleep")]
        alarm = [(False, "Unlock"), (False, "Home"), (False, "Sleep")]
        errored = [(False, "Unlock"), (False, "Home"), (False, "Sleep")]
        not_connected = [(True, "Not available while Grbl is N/A"), (True, "Not available while Grbl is N/A"),
                         (False, "Sleep")]

        grbl_phase("Idle", None)
        idle_ok = wait_until(lambda: grbl_matches(idle), 6)
        check("Unlock is grayed with its reason while Idle, and Home and Sleep stay enabled", idle_ok,
              json.dumps(page.evaluate(GRBL_BUTTONS)))
        page.locator('li.sce-device[data-device-id="grbl"] button.sce-btn').nth(0).click(force=True)
        pump(400)
        check("a grayed-out command's click runs nothing", "grbl_unlock" not in calls, "calls=%s" % calls)
        page.locator('li.sce-device[data-device-id="grbl"] button.sce-btn').nth(1).click()
        pump(int((SYNC_DELAY + 1.2) * 1000))
        check("an enabled member still runs while its neighbour is grayed out", "grbl_home" in calls,
              "calls=%s" % calls)

        grbl_phase("Alarm", None)
        check("Alarm enables Unlock", wait_until(lambda: grbl_matches(alarm), 6),
              json.dumps(page.evaluate(GRBL_BUTTONS)))

        grbl_phase("Idle", "boom")
        check("a status error leaves every member enabled", wait_until(lambda: grbl_matches(errored), 6),
              json.dumps(page.evaluate(GRBL_BUTTONS)))
        STATUS["grbl"]["error"] = None

        grbl_phase("N/A", None)
        check("a reported N/A word grays out Unlock and Home, and Sleep stays enabled",
              wait_until(lambda: grbl_matches(not_connected), 6), json.dumps(page.evaluate(GRBL_BUTTONS)))
        if page.evaluate(MENU_OPEN):
            page.click(TOGGLE)
            pump(300)

        browser.close()
    print("elapsed: %.1f s" % (time.time() - started), flush=True)


def main():
    run_probe()
    failed = results.count(False)
    print("RESULT: %d passed, %d failed, base %s" % (len(results) - failed, failed, BASE), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
