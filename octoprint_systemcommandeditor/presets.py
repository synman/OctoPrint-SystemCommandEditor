import os
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor

from .convert import free_key
from .status import check_devices, run_status

CATALOG = [
    {
        "id": "disk",
        "name": "Free disk",
        "icon": "fa-solid fa-hard-drive",
        "sample": True,
        "status": "df -h / | awk 'NR==2{print $4\" free\"}'",
    },
    {
        "id": "memory",
        "name": "Free memory",
        "icon": "fa-solid fa-memory",
        "sample": False,
        "status": "if command -v free >/dev/null; then free -h | awk '/Mem/{print $7\" free\"}'; else memory_pressure | awk '/free percentage/{print $5\" free\"}'; fi",
    },
    {
        "id": "load",
        "name": "Load",
        "icon": "fa-solid fa-gauge",
        "sample": False,
        "status": "uptime | sed 's/.*load averages*: //; s/,//g'",
    },
    {
        "id": "uptime",
        "name": "Uptime",
        "icon": "fa-solid fa-clock",
        "sample": False,
        "status": "uptime -p 2>/dev/null | sed \"s/^up //; s/ weeks*/w/; s/ days*/d/; s/ hours*/h/; s/ minutes*/m/; s/,//g\" | grep . || uptime | sed \"s/.*up *//; s/, *[0-9]* users*.*//; s/,//\"",
    },
    {
        "id": "cpu_temp",
        "name": "CPU temperature",
        "icon": "fa-solid fa-temperature-half",
        "sample": True,
        "status": "for z in /sys/class/thermal/thermal_zone*; do case \"$(cat $z/type 2>/dev/null)\" in x86_pkg_temp|cpu-thermal|cpu_thermal) awk '{printf \"%.0f°C\\n\", $1/1000}' $z/temp; exit 0;; esac; done; exit 1",
    },
    {
        "id": "throttle",
        "name": "Power and throttling",
        "icon": "fa-solid fa-bolt",
        "sample": False,
        "status": "vcgencmd get_throttled | sed 's/^throttled=0x0$/OK/; s/^throttled=/Throttled /'",
    },
    {
        "id": "ip",
        "name": "IP address",
        "icon": "fa-solid fa-network-wired",
        "sample": False,
        "status": "(hostname -I 2>/dev/null || ipconfig getifaddr en0 || ipconfig getifaddr en1) | awk '{print $1}'",
    },
    {
        "id": "wifi",
        "name": "Wi-Fi signal",
        "icon": "fa-solid fa-wifi",
        "sample": False,
        "status": "awk 'NR==3{printf \"%d dBm\\n\", $4}' /proc/net/wireless | grep dBm",
    },
]

DEVICE_KEYS = ("id", "type", "name", "icon", "status")


def device_of(preset):
    return {
        "id": preset["id"],
        "type": "readout",
        "name": preset["name"],
        "icon": preset["icon"],
        "status": preset["status"],
    }


def detect(runner=run_status, timeout=10):
    results = check_devices(
        [device_of(p) for p in CATALOG], [], [p["id"] for p in CATALOG], runner=runner, timeout=timeout
    )
    detected = []
    for preset in CATALOG:
        result = results[preset["id"]]
        state = result["state"]
        # a timeout or a failed start reports an error with no exit code, so exit 0 rules both out
        available = result["exit"] == 0 and bool(state)
        detected.append(
            {
                **device_of(preset),
                "sample": preset["sample"],
                "available": available,
                "value": state if available else "",
            }
        )
    return detected


# OctoPrint's own System menu entries, each joined to one of two switches by its role. First start adds both, and the
# presets picker adds and removes them like any other preset.
SYSTEM_SWITCHES = [
    {"id": "host", "icon": "fa-solid fa-server", "members": {"reboot": "restart", "shutdown": "off"}},
    {"id": "octoprint", "name": "OctoPrint", "icon": "fa-solid fa-print",
     "members": {"restart": "restart", "restart_safe": "safe_restart"}},
]


def system_switches(hostname):
    """The two switches as presets; the host switch is named after this machine."""
    host = hostname.split(".")[0] or "Host"
    return [
        {
            "id": switch["id"],
            "type": "switch",
            "name": switch.get("name", host),
            "icon": switch["icon"],
            "members": dict(switch["members"]),
            "available": True,
        }
        for switch in SYSTEM_SWITCHES
    ]


def sample_devices(detected):
    return [
        {**{key: item[key] for key in DEVICE_KEYS}, "preset": item["id"]}
        for item in detected
        if item["sample"] and item["available"]
    ]


SUDO_CHECK = "sudo -n true"

# Status scripts run with the interpreter that runs OctoPrint, so they need no GNU-only tools. Each takes one folder.
BACKUP_AGE_SCRIPT = """
import os, sys, time
folder = sys.argv[1]
mtimes = []
if os.path.isdir(folder):
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            mtimes.append(os.path.getmtime(path))
if not mtimes:
    print("Last backup none")
else:
    age = max(0, time.time() - max(mtimes))
    if age >= 86400:
        text = "%d d ago" % (age // 86400)
    elif age >= 3600:
        text = "%d h ago" % (age // 3600)
    else:
        text = "%d min ago" % (age // 60)
    print("Last backup " + text)
"""

# What a clean-up would delete right now: files at the top of the folder that match the pattern, aged as find's -mtime
# counts whole days, so the readout and the command always agree. Arguments: folder, days, pattern, noun.
OLD_FILES_SCRIPT = """
import fnmatch, os, sys, time
folder, days, pattern, noun = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
count = total = 0
if os.path.isdir(folder):
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if not os.path.isfile(path) or not fnmatch.fnmatch(name, pattern):
                continue
            if int((time.time() - os.path.getmtime(path)) // 86400) > days:
                count += 1
                total += os.path.getsize(path)
        except OSError:
            pass
if not count:
    print("No old %ss" % noun)
else:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(total)
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    amount = "%d B" % total if i == 0 else "%.1f %s" % (size, units[i])
    print("%d old %s%s, %s" % (count, noun, "" if count == 1 else "s", amount))
"""

LOG_DAYS = 7
TIMELAPSE_DAYS = 30


def _old_files_status(folder, days, pattern, noun):
    return "{} {} {} {}".format(_python_status(OLD_FILES_SCRIPT, folder), days, shlex.quote(pattern), noun)

def _python_status(script, folder):
    return "{} -c {} {}".format(shlex.quote(sys.executable), shlex.quote(script), shlex.quote(folder))


def maintenance(env):
    octoprint = shlex.quote(env["octoprint"])
    basedir = shlex.quote(env["basedir"])
    timelapse = shlex.quote(env["timelapse"])
    logs = shlex.quote(env["logs"])
    return [
        {
            "action": "backup_now",
            "name": "Back up OctoPrint now",
            "icon": "fa-solid fa-box-archive",
            "command": "{} --basedir {} plugins backup:backup".format(octoprint, basedir),
            "confirm": "",
            "async": True,
            "sample": True,
            "needs_sudo": False,
            "detect": "test -x {}".format(octoprint),
            "status": _python_status(BACKUP_AGE_SCRIPT, os.path.join(env["basedir"], "data", "backup")),
        },
        {
            "action": "webcam_restart",
            "name": "Restart the webcam",
            "icon": "fa-solid fa-video",
            "command": 'for u in camera-streamer webcamd mjpg-streamer; do if systemctl list-unit-files "$u.service" --no-legend | grep -q .; then exec sudo systemctl restart "$u"; fi; done; exit 1',
            "confirm": "",
            "async": False,
            "sample": True,
            "needs_sudo": True,
            "detect": "systemctl list-unit-files camera-streamer.service webcamd.service mjpg-streamer.service --no-legend | grep -q .",
        },
        {
            "action": "purge_timelapses",
            "name": "Delete timelapses older than {} days".format(TIMELAPSE_DAYS),
            "icon": "fa-solid fa-film",
            "command": "find {} -maxdepth 1 -type f -mtime +{} -delete".format(timelapse, TIMELAPSE_DAYS),
            "confirm": "This deletes timelapse videos older than {} days.".format(TIMELAPSE_DAYS),
            "async": False,
            "sample": False,
            "needs_sudo": False,
            "detect": "test -d {}".format(timelapse),
            "status": _old_files_status(env["timelapse"], TIMELAPSE_DAYS, "*", "timelapse"),
        },
        {
            "action": "purge_logs",
            "name": "Delete logs older than {} days".format(LOG_DAYS),
            "icon": "fa-solid fa-file-lines",
            "command": "find {} -maxdepth 1 -type f -name '*.log.*' -mtime +{} -delete".format(logs, LOG_DAYS),
            "confirm": "This deletes rotated OctoPrint logs older than {} days.".format(LOG_DAYS),
            "async": False,
            "sample": False,
            "needs_sudo": False,
            "detect": "test -d {}".format(logs),
            "status": _old_files_status(env["logs"], LOG_DAYS, "*.log.*", "log"),
        },
        {
            "action": "sync_clock",
            "name": "Sync the clock",
            "icon": "fa-solid fa-clock-rotate-left",
            "command": "sudo chronyc makestep || sudo systemctl restart systemd-timesyncd",
            "confirm": "",
            "async": False,
            "sample": False,
            "needs_sudo": True,
            "detect": "command -v chronyc || command -v timedatectl",
        },
        {
            "action": "wifi_powersave_off",
            "name": "Turn Wi-Fi power saving off",
            "icon": "fa-solid fa-wifi",
            "command": "sudo iw dev wlan0 set power_save off",
            "confirm": "",
            "async": False,
            "sample": False,
            "needs_sudo": True,
            "detect": "command -v iw && test -d /sys/class/net/wlan0",
        },
        {
            "action": "os_update",
            "name": "Update the system's packages",
            "icon": "fa-solid fa-download",
            "command": "sudo apt-get update && sudo apt-get -y upgrade",
            "confirm": "This updates every installed package and can take a long time.",
            "async": True,
            "sample": False,
            "needs_sudo": True,
            "detect": "command -v apt-get",
        },
    ]


def detect_maintenance(env, runner=run_status, timeout=10):
    presets = maintenance(env)
    needs_sudo = any(preset["needs_sudo"] for preset in presets)
    with ThreadPoolExecutor(max_workers=8) as pool:
        detects = [pool.submit(runner, preset["detect"], timeout) for preset in presets]
        sudo = pool.submit(runner, SUDO_CHECK, timeout) if needs_sudo else None
        results = [future.result() for future in detects]
        sudo_ok = sudo.result()[0] == 0 if sudo is not None else True
    listed = []
    for preset, (exit_code, _output, _error) in zip(presets, results):
        listed.append(
            {
                "action": preset["action"],
                "name": preset["name"],
                "icon": preset["icon"],
                "command": preset["command"],
                "confirm": preset["confirm"],
                "async": preset["async"],
                "sample": preset["sample"],
                "status": preset.get("status", ""),
                "available": exit_code == 0,
                "note": "Needs passwordless sudo" if preset["needs_sudo"] and not sudo_ok else "",
            }
        )
    return listed


def entry_of(preset):
    entry = {"action": preset["action"], "name": preset["name"], "command": preset["command"], "icon": preset["icon"]}
    if preset.get("confirm"):
        entry["confirm"] = preset["confirm"]
    if preset.get("async"):
        entry["async"] = True
    return entry


def action_of(preset, device_ids):
    """Return ``(device, entry)`` for a maintenance preset as an action control and its run command.

    The device id is the preset's action key when free among ``device_ids``, else ``_2``, ``_3``... The command
    joins it with ``group`` = that id and ``role`` ``run``. Only maintenance presets with a status carry one.
    Both carry ``preset`` = the preset's action, which marks them as made by that preset.
    """
    device_id = free_key(preset["action"], device_ids)
    device = {"id": device_id, "type": "action", "name": preset["name"], "icon": preset["icon"],
              "preset": preset["action"]}
    if preset.get("status"):
        device["status"] = preset["status"]
    entry = entry_of(preset)
    entry["group"] = device_id
    entry["role"] = "run"
    entry["preset"] = preset["action"]
    return device, entry


def maintenance_actions():
    """The action keys of the maintenance presets. They are constants, so no detection or environment is needed."""
    blank = {"octoprint": "", "basedir": "", "timelapse": "", "logs": ""}
    return [preset["action"] for preset in maintenance(blank)]


def mark_presets(devices, actions, maintenance_action_keys):
    """Add a ``preset`` marker to the records that an older version created without one.

    Returns ``(devices, actions, changed)``; the inputs are not modified. The rule, applied where no marker exists:
    a readout control whose id is a catalog id, a switch control named ``host`` or ``octoprint``, and a readout
    placement of such a readout get the id; a maintenance command gets its action, and the action control its
    ``group`` names gets the same. A marker that is already set is never overwritten.
    """
    readout_ids = {preset["id"] for preset in CATALOG}
    switch_ids = {switch["id"] for switch in SYSTEM_SWITCHES}
    maintenance_set = set(maintenance_action_keys)
    changed = False

    new_actions = [dict(entry) if isinstance(entry, dict) else entry for entry in actions or []]
    action_groups = {}
    for entry in new_actions:
        if isinstance(entry, dict) and entry.get("action") in maintenance_set and "command" in entry:
            if not entry.get("preset"):
                entry["preset"] = entry["action"]
                changed = True
            if entry.get("group"):
                action_groups.setdefault(entry["group"], entry["action"])

    new_devices = [dict(device) if isinstance(device, dict) else device for device in devices or []]
    for device in new_devices:
        if not isinstance(device, dict) or device.get("preset"):
            continue
        device_id, kind = device.get("id"), device.get("type")
        if kind == "readout" and device_id in readout_ids:
            marker = device_id
        elif kind == "switch" and device_id in switch_ids:
            marker = device_id
        elif kind == "action" and device_id in action_groups:
            marker = action_groups[device_id]
        else:
            continue
        device["preset"] = marker
        changed = True

    readout_presets = {
        device["id"]: device["preset"]
        for device in new_devices
        if isinstance(device, dict) and device.get("type") == "readout" and device.get("preset")
    }
    for entry in new_actions:
        if (
            isinstance(entry, dict)
            and entry.get("action") != "divider"
            and "command" not in entry
            and entry.get("device") in readout_presets
            and not entry.get("preset")
        ):
            entry["preset"] = readout_presets[entry["device"]]
            changed = True

    return new_devices, new_actions, changed


# Plugin presets: one group per installed, enabled plugin that lists devices. Each reader returns
# (device key, preset name, control, commands). The control has no id and the commands have no action or group: the
# editor assigns both. Only label, ip, idx, name and active are read from a plugin's settings; never a credential.
# Each source line cited is the one the reader relies on.

ICON = "fa-solid fa-plug"


def _switch(name, read, on_pattern, off_pattern, on_run, off_run):
    control = {"type": "switch", "name": name, "icon": ICON, "read": read, "on_pattern": on_pattern,
               "off_pattern": off_pattern}
    commands = [{"name": "On", "run": on_run, "role": "on"}, {"name": "Off", "run": off_run, "role": "off"}]
    return control, commands


def _psucontrol(get):
    # psu.py:878-884 __plugin_helpers__ get_psu_state, turn_psu_on, turn_psu_off; get_psu_state returns the bool
    # isPSUOn (psu.py:581-582). One PSU, so no inventory.
    control, commands = _switch(
        "Printer power",
        {"plugin": "psucontrol", "helper": "get_psu_state"},
        "^true$",
        "^false$",
        {"plugin": "psucontrol", "helper": "turn_psu_on"},
        {"plugin": "psucontrol", "helper": "turn_psu_off"},
    )
    return [("psu", "PSU Control", control, commands)]


def _tplink(get):
    # tplink.py:1395-1399 __plugin_helpers__ turn_on, turn_off, check_status (plugip); check_status returns
    # dict(currentState=...) (tplink.py:589-593). Inventory arrSmartplugs with ip and label (tplink.py:1190).
    devices = []
    for plug in get("tplinksmartplug", "arrSmartplugs") or []:
        ip = plug.get("ip")
        if not ip:
            continue
        label = plug.get("label") or ip
        args = {"plugip": ip}
        control, commands = _switch(
            label,
            {"plugin": "tplinksmartplug", "helper": "check_status", "args": args, "live": False},
            '"currentState": "on"',
            '"currentState": "off"',
            {"plugin": "tplinksmartplug", "helper": "turn_on", "args": args},
            {"plugin": "tplinksmartplug", "helper": "turn_off", "args": args},
        )
        devices.append((ip, label, control, commands))
    return devices


def _tasmota(get):
    # tasmota.py:467 turn_on(self, plugip, plugidx), :512 turn_off, :557 check_status(plugip, plugidx); the dict
    # check_status returns carries currentState (tasmota.py:626-639, returned at :644-645). No helpers, so the
    # "method" kind. Inventory arrSmartplugs with ip, idx, label (tasmota.py:756).
    devices = []
    for plug in get("tasmota", "arrSmartplugs") or []:
        ip = plug.get("ip")
        if not ip:
            continue
        label = plug.get("label") or ip
        args = {"plugip": ip, "plugidx": plug.get("idx")}
        control, commands = _switch(
            label,
            {"plugin": "tasmota", "method": "check_status", "args": args, "live": False},
            '"currentState": "on"',
            '"currentState": "off"',
            {"plugin": "tasmota", "method": "turn_on", "args": args},
            {"plugin": "tasmota", "method": "turn_off", "args": args},
        )
        devices.append(("{}/{}".format(ip, plug.get("idx")), label, control, commands))
    return devices


def _octorelay(get):
    # octorelay_const.py:41 settings "r1" (active, label_text), relays r1..r8; octorelay_const.py:329-330 UPDATE_COMMAND
    # "update", GET_STATUS_COMMAND "getStatus"; on_api_command answers {"status": bool} (octorelay.py:163-184).
    devices = []
    for index in range(1, 9):
        key = "r{}".format(index)
        relay = get("octorelay", key) or {}
        if not relay.get("active"):
            continue
        label = relay.get("label_text") or key.upper()
        control, commands = _switch(
            label,
            {"plugin": "octorelay", "api": "getStatus", "data": {"subject": key}},
            '"status": true',
            '"status": false',
            {"plugin": "octorelay", "api": "update", "data": {"subject": key, "target": True}},
            {"plugin": "octorelay", "api": "update", "data": {"subject": key, "target": False}},
        )
        devices.append((key, label, control, commands))
    return devices


def _indexed_relays(get, plugin_id, setting, state_api, on_api, off_api, on_pattern, off_pattern):
    # gpiocontrol.py:53 gpio_configurations and usbrelaycontrol.py:43 usbrelay_configurations: each entry has name;
    # the id is the list index (gpiocontrol.py:130). Commands answer through on_api_command (gpiocontrol.py:126-139,
    # usbrelaycontrol.py:132-144); the state is a JSON string, so the Response body parses to "on"/"off".
    devices = []
    for index, config in enumerate(get(plugin_id, setting) or []):
        name = config.get("name") or "{} {}".format(plugin_id, index)
        data = {"id": index}
        control, commands = _switch(
            name,
            {"plugin": plugin_id, "api": state_api, "data": data},
            on_pattern,
            off_pattern,
            {"plugin": plugin_id, "api": on_api, "data": data},
            {"plugin": plugin_id, "api": off_api, "data": data},
        )
        devices.append((str(index), name, control, commands))
    return devices


def _gpiocontrol(get):
    return _indexed_relays(
        get, "gpiocontrol", "gpio_configurations", "getGpioState", "turnGpioOn", "turnGpioOff", "^on$", "^off$"
    )


def _usbrelaycontrol(get):
    return _indexed_relays(
        get,
        "usbrelaycontrol",
        "usbrelay_configurations",
        "getUSBRelayState",
        "turnUSBRelayOn",
        "turnUSBRelayOff",
        "^on$",
        "^off$",
    )


def _bettergrblsupport(get):
    # __init__.py:1926-1934 __plugin_helpers__ sleep, unlock, reset, home, state; helper_state returns a dict (:1835),
    # so the state is read from its "state" key. One controller, so no inventory.
    grbl = "bettergrblsupport"
    control = {"type": "action", "name": "Grbl", "icon": ICON, "read": {"plugin": grbl, "helper": "state", "field": "state"}}
    commands = [
        {"name": "Home", "run": {"plugin": grbl, "helper": "home"}, "role": "run"},
        {"name": "Unlock", "run": {"plugin": grbl, "helper": "unlock"}, "role": "run"},
        {"name": "Sleep", "run": {"plugin": grbl, "helper": "sleep"}, "role": "run"},
        {
            "name": "Reset",
            "run": {"plugin": grbl, "helper": "reset"},
            "role": "run",
            "confirm": "This sends a reset to the Grbl controller.",
        },
    ]
    return [("grbl", "Better Grbl Support", control, commands)]


PLUGINS = (
    ("psucontrol", "PSU Control", _psucontrol),
    ("tplinksmartplug", "TP-Link Smartplug", _tplink),
    ("tasmota", "Tasmota", _tasmota),
    ("octorelay", "OctoRelay", _octorelay),
    ("gpiocontrol", "GPIO Control", _gpiocontrol),
    ("usbrelaycontrol", "USB Relay Control", _usbrelaycontrol),
    ("bettergrblsupport", "Better Grbl Support", _bettergrblsupport),
)


def harvest(plugin_manager, get_setting):
    """Plugin presets for every installed, enabled plugin that lists devices.

    ``get_setting(plugin_id, key)`` reads that plugin's own settings.
    """
    groups = []
    for plugin_id, label, devices_of in PLUGINS:
        info = plugin_manager.get_plugin_info(plugin_id)
        if info is None or not info.enabled:
            continue
        devices = devices_of(get_setting)
        if not devices:
            continue
        presets = [
            {"preset": "{}:{}".format(plugin_id, key), "name": name, "control": control, "commands": commands}
            for key, name, control, commands in devices
        ]
        groups.append({"plugin": plugin_id, "name": label, "presets": presets})
    return groups
