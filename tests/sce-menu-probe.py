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
     "section": "Machine"},
    {"id": "disk", "type": "readout", "name": "Disk", "icon": "fa-solid fa-hard-drive", "status": "disk-state",
     "section": "Machine"},
    {"id": "cam", "type": "link", "name": "Camera", "icon": "fa-solid fa-video", "url": "http://example.test/cam",
     "section": "Machine"},    {"id": "nost", "type": "switch", "name": "No-status switch", "icon": "fa-solid fa-plug",
     "on_pattern": "", "off_pattern": "", "section": "Machine"},
    {"id": "ghost", "type": "switch", "name": "Ghost switch", "icon": "fa-solid fa-ghost", "status": "ghost-state",
     "section": "Machine"},
]

# devices the menu is expected to draw: "ghost" has no member in core's command list, so it draws no row
DRAWN = [d for d in DEVICES if d["id"] != "ghost"]

ENTRIES = {
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
    "sync_action": {"name": "Sync action", "command": "sync-cmd", "group": "", "role": "", "value": "",
                    "icon": "fa-solid fa-gear", "async": False, "confirm": ""},
    "sync_fail": {"name": "Failing sync action", "command": "fail-cmd", "group": "", "role": "", "value": "",
                  "icon": "fa-solid fa-gear", "async": False, "confirm": ""},
    "sync_cancel": {"name": "Cancel test action", "command": "cancel-cmd", "group": "", "role": "", "value": "",
                    "icon": "fa-solid fa-gear", "async": False, "confirm": "Run the cancel test action?"},
    "async_action": {"name": "Async action", "command": "async-cmd", "group": "", "role": "", "value": "",
                     "icon": "fa-solid fa-bolt-lightning", "async": True,
                     "confirm": "Run the async action now?"},
}

CORE = {
    "restart": {"icon": "fa-solid fa-arrows-rotate", "heading": "OctoPrint", "group": "", "role": "", "value": ""},
}

CORE_COMMANDS = {"restart": "sudo service octoprint restart"}

STATUS = {
    "laser": {"state": "off", "output": "", "exit": 0, "error": None},
    "res": {"state": "1080", "output": "", "exit": 0, "error": None},
    "disk": {"state": "42 GB free", "output": "", "exit": 0, "error": None},
}

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
    {"action": "sync_action", "name": "Sync action", "command": "sync-cmd"},
    {"action": "sync_fail", "name": "Failing sync action", "command": "fail-cmd"},
    {"action": "sync_cancel", "name": "Cancel test action", "command": "cancel-cmd",
     "confirm": "Run the cancel test action?"},
    {"action": "async_action", "name": "Async action", "command": "async-cmd",
     "confirm": "Run the async action now?"},
]

CORE_LIST = [{"action": "restart", "name": "Restart OctoPrint", "confirm": "Restart OctoPrint?"}]

results = []
calls = []        # custom command actions the page executed
status_log = []   # the ids named by each status POST
released = []     # states of the held status answers, in the order they were delivered
status_plan = []  # ("hold", seconds, overrides) entries, consumed by the next status POST
pending = []      # (due, route, http_status, body, tag) answers fulfilled by pump()
page = None


def check(name, ok, detail=""):
    results.append(bool(ok))
    suffix = "" if ok or not detail else " -- " + detail
    print(("PASS: " if ok else "FAIL: ") + name + suffix, flush=True)


def status_body(ids, overrides):
    out = {}
    for i in ids:
        row = dict(STATUS.get(i, {"state": None, "output": "", "exit": 0, "error": None}))
        if i in overrides:
            row["state"] = overrides[i]
        out[i] = row
    return json.dumps({"results": out})


def handle_plugin(route, request):
    if request.method == "POST":
        ids = (request.post_data_json or {}).get("ids", [])
        status_log.append(sorted(ids))
        if status_plan:
            _, hold, overrides = status_plan.pop(0)
            pending.append((time.time() + hold, route, 200, status_body(ids, overrides), "held"))
            return
        route.fulfill(status=200, content_type="application/json", body=status_body(ids, {}))
        return
    route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "devices": DEVICES, "core": CORE, "entries": ENTRIES, "core_commands": CORE_COMMANDS}))


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
            "() => document.querySelectorAll('li.sce-device').length === 4 && "
            "document.querySelector('li.sce-device[data-device-id=\"laser\"] .sce-dot.sce-off') !== null"), 10)
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
        check("sync button spins while pending", spun)
        pump(int((SYNC_DELAY + 1.2) * 1000))
        still = page.evaluate(SPIN_IN, "laser") > 0
        check("sync button stops after", spun and not still, "spinning=%s" % still)

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
        expected = sorted(d["id"] for d in DEVICES if d.get("status"))
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

        browser.close()
    print("elapsed: %.1f s" % (time.time() - started), flush=True)


def main():
    run_probe()
    failed = results.count(False)
    print("RESULT: %d passed, %d failed, base %s" % (len(results) - failed, failed, BASE), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
