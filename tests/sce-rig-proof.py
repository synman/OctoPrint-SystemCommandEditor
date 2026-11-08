#!/usr/bin/env python3
"""Rig proof for System Command Editor 2.1: real backend, real commands, no faked routes.

Seeds a test command set through the settings API, drives the real System menu, then restores the rig's
own settings from a snapshot taken first. Commands only touch files in a temp directory; the built-in
reboot and shutdown entries are grouped but never run.

Usage: python tests/sce-rig-proof.py <base url> <rig dir>   (rig dir holds the apikey file)
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE, RIG = sys.argv[1], os.path.expanduser(sys.argv[2])
KEY = open(os.path.join(RIG, "apikey")).read().strip()
D = tempfile.mkdtemp(prefix="sce21p5-")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESULTS = []


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-Api-Key": KEY, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with OPENER.open(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def check(label, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS: " if ok else "FAIL: ") + label + ("" if ok else " -- " + str(detail)), flush=True)


def snapshot():
    s = api("GET", "/api/settings")
    return {"system": {"actions": s["system"]["actions"]},
            "plugins": {"systemcommandeditor": {k: s["plugins"]["systemcommandeditor"][k] for k in ("core", "devices")}}}


ORIGINAL = snapshot()
core = json.loads(json.dumps(ORIGINAL["plugins"]["systemcommandeditor"]["core"]))
core["reboot"].update(group="host", role="restart")
core["shutdown"].update(group="host", role="off")
SEED = {
    "system": {"actions": [
        {"action": "divider", "name": "Test"},
        {"action": "lamp_on", "name": "Lamp on", "command": f"touch {D}/lamp", "group": "lamp", "role": "on"},
        {"action": "lamp_off", "name": "Lamp off", "command": f"rm -f {D}/lamp", "confirm": "Turn the lamp off?",
         "group": "lamp", "role": "off"},
        {"action": "lamp_cycle", "name": "Power cycle lamp", "command": f"rm -f {D}/lamp; sleep 2; touch {D}/lamp",
         "group": "lamp", "role": "restart"},
        {"action": "mode_a", "name": "Mode A", "command": f"echo a > {D}/mode", "group": "mode", "role": "choice", "value": "a"},
        {"action": "mode_b", "name": "Mode B", "command": f"echo b > {D}/mode", "group": "mode", "role": "choice", "value": "b"},
        {"action": "bg_job", "name": "Background job", "command": "sleep 1", "async": True},
    ]},
    "plugins": {"systemcommandeditor": {"core": core, "devices": [
        {"id": "host", "type": "switch", "name": "This machine"},
        {"id": "lamp", "type": "switch", "name": "Lamp", "icon": "fa-solid fa-lightbulb", "status": f"test -f {D}/lamp"},
        {"id": "mode", "type": "selector", "name": "Mode", "status": f"cat {D}/mode"},
        {"id": "disk", "type": "readout", "name": "Free space", "status": "df -h / | tail -1 | awk '{print $4}'", "section": "Test"},
        {"id": "docs", "type": "link", "name": "OctoPrint docs", "url": "https://docs.octoprint.org", "section": "Test"},
    ]}},
}
open(f"{D}/mode", "w").write("a\n")

t0 = time.time()
try:
    api("POST", "/api/settings", SEED)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.OctoPrint && OctoPrint.coreui && OctoPrint.coreui.startedUp", timeout=30000)
        menu = "#navbar_systemmenu > ul.dropdown-menu"

        def open_menu():
            if not page.is_visible(menu):
                page.click("#navbar_systemmenu > a")
            page.wait_for_selector(menu, state="visible")

        def row(dev):
            return page.locator(f"{menu} li.sce-device[data-device-id='{dev}']")

        def wait_state(dev, word, timeout=8000):
            page.wait_for_function(
                "([m,d,w]) => { const r = document.querySelector(m + \" li.sce-device[data-device-id='\" + d + \"'] .sce-state\");"
                " return r && r.textContent.trim() === w; }", arg=[menu, dev, word], timeout=timeout)

        open_menu()
        wait_state("lamp", "Off")
        check("lamp row shows Off from the real status command", True)
        check("one row per device, members hidden",
              row("lamp").count() == 1 and page.locator(f"{menu} li.sce-hidden").count() >= 5,
              page.locator(f"{menu} li.sce-hidden").count())
        core_listed = {c["action"] for c in api("GET", "/api/system/commands").get("core", [])}
        want_host = 1 if core_listed & {"reboot", "shutdown"} else 0
        check("host row is drawn only when core lists reboot or shutdown", row("host").count() == want_host,
              f"listed={sorted(core_listed)} rows={row('host').count()}")
        titles = row("lamp").locator("button").evaluate_all("bs => bs.map(b => b.title)")
        check("lamp buttons are restart and on while off, titled by command name",
              titles == ["Power cycle lamp", "Lamp on"], titles)

        row("lamp").locator("a.sce-device-row").click()
        page.wait_for_timeout(1500)
        check("a click on the row itself runs nothing", not os.path.exists(f"{D}/lamp"))

        open_menu()
        row("lamp").locator("button[title='Lamp on']").click()
        wait_state("lamp", "On")
        check("the on button ran Lamp on and the status re-check shows On", os.path.exists(f"{D}/lamp"))

        open_menu()
        row("lamp").locator("button[title='Lamp off']").click()
        page.wait_for_selector("div.modal.in", timeout=5000)
        body = page.inner_html("div.modal.in .modal-body")
        check("consent dialog shows the message and the exact command",
              "Turn the lamp off?" in body and f"<pre>rm -f {D}/lamp</pre>" in body, body[:200])
        page.locator("div.modal.in .modal-footer a.btn-danger, div.modal.in .modal-footer a.btn-primary").last.click()
        open_menu()
        wait_state("lamp", "Off")
        check("confirmed off ran and the row shows Off", not os.path.exists(f"{D}/lamp"))

        open_menu()
        started = time.time()
        row("lamp").locator("button[title='Power cycle lamp']").click()
        page.wait_for_function(
            "m => !!document.querySelector(m + \" li.sce-device[data-device-id='lamp'] button[title='Power cycle lamp'] i.fa-spin\")",
            arg=menu, timeout=3000)
        check("sync restart spins while running", True)
        page.wait_for_function(
            "m => !!document.querySelector(m + \" li.sce-device[data-device-id='lamp'] button[title='Power cycle lamp'] i.fa-check\")",
            arg=menu, timeout=8000)
        took = time.time() - started
        check("sync restart ends with a check on its own button after the 2 s command", os.path.exists(f"{D}/lamp") and took >= 1.8,
              f"{took:.1f} s")

        open_menu()
        wait_state("mode", "Mode A")
        row("mode").hover()
        row("mode").locator("a.sce-choice", has_text="Mode B").click()
        open_menu()
        wait_state("mode", "Mode B")
        check("selector ran Mode B and the status shows it", open(f"{D}/mode").read().strip() == "b")

        disk = row("disk").locator(".sce-state").inner_text().strip()
        check("readout shows the real free space", bool(disk) and disk != "Unknown", disk)
        href = row("docs").locator("a").get_attribute("href")
        check("link opens its URL", href == "https://docs.octoprint.org", href)

        bg = page.locator(f"{menu} li", has_text="Background job").locator("a").first
        bg.click()
        spun = page.locator(f"{menu} li", has_text="Background job").locator("i.fa-spin").count()
        page.wait_for_selector(".ui-pnotify:has-text('asynchronously')", timeout=5000)
        check("async job never spins and core's toast reports it", spun == 0, spun)
        browser.close()
finally:
    api("POST", "/api/settings", ORIGINAL)
    check("rig settings restored exactly", snapshot() == ORIGINAL)

print(f"elapsed: {time.time() - t0:.1f} s")
print(f"RESULT: {sum(RESULTS)} passed, {len(RESULTS) - sum(RESULTS)} failed, base {BASE}")
sys.exit(0 if all(RESULTS) else 1)
