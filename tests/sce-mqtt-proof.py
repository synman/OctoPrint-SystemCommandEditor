"""Live proof of the MQTT readout and switch against a real OctoPrint: a retained MQTT value reads back through a
readout, a retained state reads back through a switch, and the switch's On and Off runs publish to the set topic.
The proof restores the original devices and actions and clears its retained topics afterwards.

Usage: sce-mqtt-proof.py <base url> <basedir>. The MQTT plugin must be enabled and connected to an unauthenticated
broker this machine reaches with mosquitto_pub and mosquitto_sub. The host is the MQTT plugin's
plugins.mqtt.broker.url (default 127.0.0.1) and the port is its plugins.mqtt.broker.port (default 1883). The proof
refuses to run (exit 2) when plugins.mqtt.broker.username is set.
"""
import copy
import json
import os
import subprocess
import sys
import time

import requests
import yaml

if len(sys.argv) != 3:
    sys.exit("usage: sce-mqtt-proof.py <base url> <basedir>")

BASE = sys.argv[1].rstrip("/")
cfg = yaml.safe_load(open(os.path.join(sys.argv[2], "config.yaml")))
broker = ((cfg.get("plugins") or {}).get("mqtt") or {}).get("broker") or {}
if broker.get("username"):
    print("refusing to run: plugins.mqtt.broker.username is set; the proof needs an unauthenticated broker",
          file=sys.stderr)
    sys.exit(2)
MQTT_HOST = str(broker.get("url") or "127.0.0.1")
MQTT_PORT = str(broker.get("port", 1883))
H = {"X-Api-Key": cfg["api"]["key"], "Content-Type": "application/json"}
URL = BASE + "/api/plugin/systemcommandeditor"
SETTINGS = BASE + "/api/settings"

READOUT = "sce_mqtt_proof_readout"
SWITCH = "sce_mqtt_proof_switch"
DEVICE_IDS = [READOUT, SWITCH]
HUMIDITY = "sce/proof/humidity"
STATE = "sce/proof/state"
SET = "sce/proof/set"


def cmd(command, **data):
    r = requests.post(URL, headers=H, data=json.dumps(dict(command=command, **data)), timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:200]


def get_settings():
    r = requests.get(SETTINGS, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def save_settings(devices, actions):
    body = {"plugins": {"systemcommandeditor": {"devices": devices}}, "system": {"actions": actions}}
    r = requests.post(SETTINGS, headers=H, data=json.dumps(body), timeout=30)
    return r.status_code


def mqtt_pub(topic, payload=None, retained=True):
    args = ["mosquitto_pub", "-h", MQTT_HOST, "-p", MQTT_PORT, "-t", topic]
    if retained:
        args.append("-r")
    if payload is None:
        args.append("-n")
    else:
        args += ["-m", payload]
    subprocess.run(args, check=True)


def poll_status(expect, timeout=10):
    """Poll the live status until every (id, field, value) in expect matches or the timeout passes."""
    deadline = time.time() + timeout
    body = {}
    while time.time() < deadline:
        code, body = cmd("status", ids=DEVICE_IDS, live=True)
        res = body.get("results", {}) if code == 200 else {}
        if all((res.get(i) or {}).get(field) == value for i, field, value in expect):
            return res
        time.sleep(0.5)
    return body.get("results", {}) if isinstance(body, dict) else {}


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s: %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " -- " + str(detail)[:400]), flush=True)


# the web server answers before the plugin API is ready; wait for the API itself
for _ in range(30):
    try:
        if requests.get(BASE + "/api/version", headers=H, timeout=5).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(1)

original = get_settings()
orig_devices = copy.deepcopy(((original.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or [])
orig_actions = copy.deepcopy(((original.get("system") or {}).get("actions")) or [])

proof_devices = [
    {"id": READOUT, "type": "readout", "name": "SCE MQTT proof humidity", "read": {"mqtt": HUMIDITY},
     "format": "{value:.1f}%"},
    {"id": SWITCH, "type": "switch", "name": "SCE MQTT proof switch", "read": {"mqtt": STATE},
     "on_pattern": "^ON$", "off_pattern": "^OFF$"},
]
proof_actions = [
    {"action": "sce_mqtt_proof_on", "name": "SCE MQTT proof on", "group": SWITCH, "role": "on",
     "run": {"plugin": "mqtt", "helper": "mqtt_publish",
             "args": {"topic": SET, "payload": "ON", "retained": False}}},
    {"action": "sce_mqtt_proof_off", "name": "SCE MQTT proof off", "group": SWITCH, "role": "off",
     "run": {"plugin": "mqtt", "helper": "mqtt_publish",
             "args": {"topic": SET, "payload": "OFF", "retained": False}}},
]

try:
    code = save_settings(orig_devices + proof_devices, orig_actions + proof_actions)
    check("proof readout, switch and On/Off actions saved", code == 200, code)

    # readout and switch read retained values from the broker
    mqtt_pub(HUMIDITY, "38")
    mqtt_pub(STATE, "ON")
    # the format applies to the state the row shows; output stays the raw payload
    res = poll_status([(READOUT, "state", "38.0%"), (SWITCH, "state", "on")])
    check("MQTT readout shows the retained humidity formatted as 38.0%",
          (res.get(READOUT) or {}).get("state") == "38.0%" and (res.get(READOUT) or {}).get("output") == "38",
          res.get(READOUT))
    check("MQTT switch reads on from the retained state", (res.get(SWITCH) or {}).get("state") == "on",
          res.get(SWITCH))

    mqtt_pub(STATE, "OFF")
    res = poll_status([(SWITCH, "state", "off")])
    check("MQTT switch reads off after the retained state changes to OFF",
          (res.get(SWITCH) or {}).get("state") == "off", res.get(SWITCH))

    # the On and Off runs publish to the set topic
    for action, payload in (("sce_mqtt_proof_on", "ON"), ("sce_mqtt_proof_off", "OFF")):
        sub = subprocess.Popen(["mosquitto_sub", "-h", MQTT_HOST, "-p", MQTT_PORT, "-t", SET, "-C", "1", "-W", "10",
                                "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1)  # let the subscriber connect before the run publishes
        code, body = cmd("run", action=action)
        try:
            out, _err = sub.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            sub.kill()
            out, _err = sub.communicate()
        expected = "%s %s" % (SET, payload)
        check("run %s publishes %s to %s" % (action, payload, SET),
              code == 200 and body.get("ok") is True and expected in out.splitlines(), (code, body, out))

    # the set topic must not keep a retained message
    retained = subprocess.run(["mosquitto_sub", "-h", MQTT_HOST, "-p", MQTT_PORT, "-t", SET, "--retained-only",
                               "-W", "3"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check("no retained message is left on %s" % SET, retained.stdout.strip() == "", retained.stdout[:200])
finally:
    restored = save_settings(orig_devices, orig_actions)
    for topic in (HUMIDITY, STATE):
        mqtt_pub(topic, None, retained=True)
    after = get_settings()
    now_devices = ((after.get("plugins") or {}).get("systemcommandeditor") or {}).get("devices") or []
    now_actions = ((after.get("system") or {}).get("actions")) or []
    check("original devices and actions restored", restored == 200 and now_devices == orig_devices
          and now_actions == orig_actions, (restored, len(now_devices), len(orig_devices), len(now_actions),
                                            len(orig_actions)))

print("RESULT: %d passed, %d failed" % (results.count(True), results.count(False)))
sys.exit(0 if results and all(results) else 1)
