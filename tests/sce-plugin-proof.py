"""Live proof of plugin-backed controls against a real OctoPrint: plugin presets, plugin reads, runs, the backend
condition, and an MQTT read.

Usage: sce-plugin-proof.py <base url> <basedir> <grbl port>, the port the proof connects for the Grbl read and
disconnects after (GRBL_SIM on the rigs). The server needs PSU Control and Better Grbl Support installed and
enabled, the MQTT plugin enabled on a broker this machine can publish to with mosquitto_pub (the broker port is
the MQTT plugin's own setting), and these test records in its config:
devices p8_psu (switch, read psucontrol get_psu_state, ^true$/^false$), p8_grbl (action, read bettergrblsupport
state field state), p8_mqtt (readout, read {"mqtt": "sce/p8/state"}); system.actions p8_psu_on and p8_psu_off
(run psucontrol turn_psu_on/off, group p8_psu) and p8_never_printing (run turn_psu_off, when only printing,
disable), p8_gcode (run {"gcode": ["M117 sce proof"]}); a device p8_state (readout, read {"printer": "state"}); and
plugins.classicwebcam.snapshot set to http://127.0.0.1/sce-proof-snapshot.jpg.
"""
import json
import os
import subprocess
import time

import requests
import yaml

import sys

BASE = sys.argv[1].rstrip("/")
cfg = yaml.safe_load(open(os.path.join(sys.argv[2], "config.yaml")))
mqtt_port = str(((cfg.get("plugins") or {}).get("mqtt") or {}).get("broker", {}).get("port", 1883))
H = {"X-Api-Key": cfg["api"]["key"], "Content-Type": "application/json"}
URL = BASE + "/api/plugin/systemcommandeditor"


def cmd(command, **data):
    r = requests.post(URL, headers=H, data=json.dumps(dict(command=command, **data)), timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:200]


results = []

# the web server answers before the plugin API is ready; wait for the API itself
for _ in range(30):
    try:
        if requests.get(BASE + "/api/version", headers=H, timeout=5).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(1)


def check(name, ok, detail=""):
    results.append(ok)
    print("%s: %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " -- " + str(detail)[:400]), flush=True)


code, body = cmd("presets")
groups = {g["plugin"]: g for g in (body.get("plugins") or [])} if code == 200 else {}
check("presets API offers PSU Control and Better Grbl Support as plugin presets",
      "psucontrol" in groups and "bettergrblsupport" in groups, (code, list(groups)))
psu = (groups.get("psucontrol") or {}).get("presets", [{}])[0]
check("the PSU preset is a switch with a plugin read and On/Off runs",
      psu.get("control", {}).get("type") == "switch" and [c.get("role") for c in psu.get("commands", [])] == ["on", "off"],
      psu)

# the Grbl state is only known while the controller is connected
requests.post(BASE + "/api/connection", headers=H, data=json.dumps({"command": "connect", "port": sys.argv[3], "baudrate": 115200}))
for _ in range(20):
    time.sleep(1)
    if requests.get(BASE + "/api/connection", headers=H).json()["current"]["state"] == "Operational":
        break
time.sleep(2)

ids = ["p8_psu", "p8_grbl", "p8_mqtt", "p8_state"]
code, body = cmd("status", ids=ids)
res = body.get("results", {}) if code == 200 else {}
check("a printer-state readout reads Operational while connected", (res.get("p8_state") or {}).get("output") == "Operational",
      res.get("p8_state"))
code, body = cmd("run", action="p8_gcode")
check("a G-code command sends while connected", code == 200 and body.get("ok") is True, (code, body))
code, body = cmd("presets")
snap = [p for g in (body.get("plugins") or []) for p in g.get("presets", []) if p.get("preset") == "octoprint:snapshot"]
check("the snapshot preset is a link command to the configured snapshot URL",
      len(snap) == 1 and snap[0]["control"] is None
      and snap[0]["commands"] == [{"name": "Webcam snapshot", "url": "http://127.0.0.1/sce-proof-snapshot.jpg",
                                   "icon": "fa-solid fa-camera"}], snap)
check("PSU switch reads off through PSU Control's helper", (res.get("p8_psu") or {}).get("state") == "off", res.get("p8_psu"))
check("Grbl action reads its state field through Better Grbl Support's helper",
      (res.get("p8_grbl") or {}).get("exit") == 0 and (res.get("p8_grbl") or {}).get("output") not in (None, ""), res.get("p8_grbl"))
check("MQTT readout is unknown before any message", (res.get("p8_mqtt") or {}).get("output") == "", res.get("p8_mqtt"))

requests.post(BASE + "/api/connection", headers=H, data=json.dumps({"command": "disconnect"}))

time.sleep(2)
code, body = cmd("run", action="p8_gcode")
check("a G-code command is refused while the printer is not connected",
      code == 200 and body.get("ok") is False and body.get("error") == "The printer is not connected", (code, body))

code, body = cmd("run", action="p8_psu_on")
check("run p8_psu_on answers ok", code == 200 and body.get("ok") is True, (code, body))
time.sleep(1)
code, body = cmd("status", ids=["p8_psu"])
check("PSU switch reads on after the run", (body.get("results", {}).get("p8_psu") or {}).get("state") == "on", body)
code, body = cmd("run", action="p8_psu_off")
time.sleep(1)
code, body = cmd("status", ids=["p8_psu"])
check("PSU switch reads off after the off run", (body.get("results", {}).get("p8_psu") or {}).get("state") == "off", body)

code, body = cmd("run", action="p8_never_printing")
check("a run whose condition disables it while not printing answers 409 naming the state",
      code == 409 and body.get("ok") is False and "printer is" in (body.get("error") or ""), (code, body))
code, body = cmd("run", action="nope_missing")
check("a run for a missing action answers 404", code == 404, code)

subprocess.run(["mosquitto_pub", "-h", "127.0.0.1", "-p", mqtt_port, "-t", "sce/p8/state", "-r", "-m", "42 C"],
               check=True)
# the first read subscribes; a retained message then arrives on its own time
for _ in range(10):
    time.sleep(0.5)
    code, body = cmd("status", ids=["p8_mqtt"])
    if (body.get("results", {}).get("p8_mqtt") or {}).get("output"):
        break
check("MQTT readout shows the last payload on its topic", (body.get("results", {}).get("p8_mqtt") or {}).get("output") == "42 C", body)

print("RESULT: %d passed, %d failed" % (results.count(True), results.count(False)))
