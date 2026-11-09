import copy
import os
import re
import shlex
import sys
import unicodedata
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

# Role icons: a switch's on, off and restart commands carry these in the saved entries
ON_ICON = "fa-solid fa-play"
OFF_ICON = "fa-solid fa-power-off"
RESTART_ICON = "fa-solid fa-rotate-right"


def _switch(name, read, on_pattern, off_pattern, on_run, off_run, icon):
    control = {"type": "switch", "name": name, "icon": icon, "read": read, "on_pattern": on_pattern,
               "off_pattern": off_pattern}
    commands = [
        {"name": "On", "run": on_run, "role": "on", "icon": ON_ICON},
        {"name": "Off", "run": off_run, "role": "off", "icon": OFF_ICON},
    ]
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
        "fa-solid fa-power-off",
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
            "fa-solid fa-plug",
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
            "fa-solid fa-plug",
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
            "fa-solid fa-toggle-on",
        )
        devices.append((key, label, control, commands))
    return devices


def _indexed_relays(get, plugin_id, setting, state_api, on_api, off_api, on_pattern, off_pattern, icon):
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
            icon,
        )
        devices.append((str(index), name, control, commands))
    return devices


def _gpiocontrol(get):
    return _indexed_relays(
        get, "gpiocontrol", "gpio_configurations", "getGpioState", "turnGpioOn", "turnGpioOff", "^on$", "^off$",
        "fa-solid fa-microchip",
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
        "fa-brands fa-usb",
    )


def _bettergrblsupport(get):
    # __init__.py:1926-1934 __plugin_helpers__ sleep, unlock, reset, home, state; helper_state returns a dict (:1835),
    # so the state is read from its "state" key. One controller, so no inventory.
    grbl = "bettergrblsupport"
    control = {"type": "action", "name": "Grbl", "icon": "fa-solid fa-crosshairs",
               "read": {"plugin": grbl, "helper": "state", "field": "state"}}
    commands = [
        {"name": "Home", "run": {"plugin": grbl, "helper": "home"}, "role": "run", "icon": "fa-solid fa-house",
         "when_control": {"mode": "only", "states": ["Idle", "Alarm"], "effect": "disable"}},
        {"name": "Unlock", "run": {"plugin": grbl, "helper": "unlock"}, "role": "run",
         "icon": "fa-solid fa-lock-open", "when_control": {"mode": "only", "states": ["Alarm"], "effect": "disable"}},
        {"name": "Sleep", "run": {"plugin": grbl, "helper": "sleep"}, "role": "run", "icon": "fa-solid fa-moon",
         "when_control": {"mode": "never", "states": ["Sleep"], "effect": "disable"}},
        {
            "name": "Reset",
            "run": {"plugin": grbl, "helper": "reset"},
            "role": "run",
            "icon": "fa-solid fa-rotate-left",
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


def octoprint_presets(snapshot_url=None):
    """OctoPrint's own printer actions and readouts, as one group in the shape ``harvest`` returns. Always present.

    With a non-empty ``snapshot_url`` the group also offers a webcam snapshot link, last."""
    connection = {
        "type": "switch", "name": "Printer connection", "icon": "fa-solid fa-link", "read": {"printer": "state"},
        "on_pattern": "^(Operational|Printing|Paused|Pausing|Resuming|Cancelling|Finishing)",
        "off_pattern": "^(Offline|Closed|Error)",
    }
    connection_commands = [
        {"name": "Connect", "run": {"printer": "connect"}, "role": "on", "icon": ON_ICON},
        {"name": "Disconnect", "run": {"printer": "disconnect"}, "role": "off", "icon": OFF_ICON},
    ]

    def action(name, icon, commands, read=None):
        control = {"type": "action", "name": name, "icon": icon}
        if read is not None:
            control["read"] = read
        return control, commands

    job = action(
        "Print job",
        "fa-solid fa-hourglass-half",
        [
            {"name": "Pause", "run": {"printer": "pause"}, "role": "run", "icon": "fa-solid fa-pause",
             "when": {"mode": "only", "states": ["printing"], "effect": "disable"}},
            {"name": "Resume", "run": {"printer": "resume"}, "role": "run", "icon": "fa-solid fa-play",
             "when": {"mode": "only", "states": ["paused"], "effect": "disable"}},
            {"name": "Cancel", "run": {"printer": "cancel"}, "role": "run", "icon": "fa-solid fa-stop",
             "confirm": "Cancel the print?", "when": {"mode": "only", "states": ["printing", "paused"],
                                                      "effect": "disable"}},
        ],
        read={"printer": "progress"},
    )
    printer = action(
        "Printer",
        "fa-solid fa-temperature-half",
        [
            {"name": "Home", "run": {"printer": "home"}, "role": "run", "icon": "fa-solid fa-house",
             "when": {"mode": "never", "states": ["printing"], "effect": "disable"}},
            {"name": "Cool down", "run": {"printer": "cool_down"}, "role": "run", "icon": "fa-solid fa-snowflake"},
            {
                "name": "Emergency stop",
                "run": {"printer": "emergency_stop"},
                "role": "run",
                "icon": "fa-solid fa-hand",
                "confirm": "This sends M112, which halts the printer at once.",
            },
        ],
        read={"printer": "temperatures"},
    )
    gcode = action(
        "G-code macros",
        "fa-solid fa-terminal",
        [{"name": "Show a message", "run": {"gcode": ["M117 Hello from System Commands"]}, "role": "run",
          "icon": "fa-solid fa-paper-plane", "confirm": "Send this G-code?"}],
    )
    event = action(
        "Custom event",
        "fa-solid fa-bullhorn",
        [{"name": "Fire", "run": {"event": "systemcommandeditor_custom", "payload": {}}, "role": "run",
          "icon": "fa-solid fa-bullhorn"}],
    )
    entries = [
        ("octoprint:connection", "Printer connection", (connection, connection_commands)),
        ("octoprint:job", "Print job", job),
        ("octoprint:printer", "Printer", printer),
        ("octoprint:gcode", "G-code macros", gcode),
        ("octoprint:event", "Custom event", event),
    ]
    presets = [
        {"preset": key, "name": name, "control": control, "commands": commands}
        for key, name, (control, commands) in entries
    ]
    # the picker shows a note beside the row: these printer calls do the wrong thing on Grbl, where G28 moves to a
    # stored position instead of homing, M112 is refused, and there are no heaters to cool
    next(p for p in presets if p["preset"] == "octoprint:printer")["note"] = (
        "Not for Grbl machines: use the Grbl control's Home and Reset")
    if isinstance(snapshot_url, str) and snapshot_url:
        presets.append({
            "preset": "octoprint:snapshot", "name": "Webcam snapshot", "control": None,
            "commands": [{"name": "Webcam snapshot", "url": snapshot_url, "icon": "fa-solid fa-camera"}],
        })
    return {"plugin": "octoprint", "name": "OctoPrint", "presets": presets}


# Webcam services: systemd units and Docker's s6 mjpg-streamer, each a switch. A unit's detect runs `systemctl cat`;
# a camera listed in the octoprint_deploy file is a unit named for it, treated the same way.
WEBCAM_SERVICES = (
    ("webcamd", "Webcam (webcamd)"),
    ("camera-streamer", "Webcams (camera-streamer)"),
    ("ffmpeg_hls", "HLS stream (ffmpeg_hls)"),
    ("mjpg-streamer", "Webcam (mjpg-streamer)"),
    ("mjpg_streamer", "Webcam (mjpg_streamer)"),
    ("ustreamer", "Webcam (ustreamer)"),
    ("crowsnest", "Webcams (crowsnest)"),
    ("motion", "Webcam (motion)"),
    ("go2rtc", "Streams (go2rtc)"),
    ("mediamtx", "Streams (MediaMTX)"),
    ("rtsp-simple-server", "Streams (rtsp-simple-server)"),
)
WEBCAM_CAMERAS_FILE = "/etc/octoprint_cameras"
# camera-streamer's own unit is a oneshot that stays active, so its state is read from the units it starts
CAMERA_STREAMER_STATUS = (
    "systemctl is-active camera-streamer-libcamera.service 'camera-streamer-usb@*.service' "
    "| grep -qx active && echo active || echo inactive"
)
DOCKER_WEBCAM = "/var/run/s6/services/mjpg-streamer"


def _deploy_cameras(path):
    """The camera names in the octoprint_deploy file: the word after ``camera:`` on each such line."""
    try:
        with open(path) as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    names = []
    for line in lines:
        if not line.startswith("camera:"):
            continue
        words = line[len("camera:"):].split()
        if words:
            names.append(words[0])
    return names


def _unit_preset(unit, name):
    quoted = shlex.quote(unit)
    status = (
        CAMERA_STREAMER_STATUS if unit == "camera-streamer"
        else "systemctl is-active {}.service".format(quoted)
    )
    return {
        "preset": "webcam:{}".format(unit),
        "name": name,
        "control": {"type": "switch", "name": name, "icon": "fa-solid fa-video", "status": status,
                    "on_pattern": "^active$", "off_pattern": "^(inactive|failed)$"},
        "commands": [
            {"name": "Start", "command": "sudo systemctl start {}".format(quoted), "role": "on", "icon": ON_ICON},
            {"name": "Stop", "command": "sudo systemctl stop {}".format(quoted), "role": "off", "icon": OFF_ICON},
            {"name": "Restart", "command": "sudo systemctl restart {}".format(quoted), "role": "restart",
             "icon": RESTART_ICON},
        ],
    }


def _docker_preset():
    return {
        "preset": "webcam:docker-mjpg-streamer",
        "name": "Webcam (OctoPrint Docker)",
        "control": {"type": "switch", "name": "Webcam (OctoPrint Docker)", "icon": "fa-solid fa-video",
                    "status": "s6-svstat {}".format(DOCKER_WEBCAM), "on_pattern": "^up", "off_pattern": "^down"},
        "commands": [
            {"name": "Start", "command": "s6-svc -u {}".format(DOCKER_WEBCAM), "role": "on", "icon": ON_ICON},
            {"name": "Stop", "command": "s6-svc -d {}".format(DOCKER_WEBCAM), "role": "off", "icon": OFF_ICON},
            {"name": "Restart", "command": "s6-svc -r {}".format(DOCKER_WEBCAM), "role": "restart",
             "icon": RESTART_ICON},
        ],
    }


def webcam_presets(runner=run_status, timeout=10, cameras_file=WEBCAM_CAMERAS_FILE):
    """The webcam services group: one switch per systemd unit, then each deploy camera, then Docker's streamer.

    Each preset carries ``available`` (its detect exited 0) and ``note`` (passwordless sudo is needed and missing).
    The detects and the one sudo check run in parallel.
    """
    units = list(WEBCAM_SERVICES)
    taken = {unit for unit, _name in units}
    for camera in _deploy_cameras(cameras_file):
        if camera not in taken:
            taken.add(camera)
            units.append((camera, "Webcam ({})".format(camera)))
    presets = [_unit_preset(unit, name) for unit, name in units]
    detects = ["systemctl cat {}.service >/dev/null 2>&1".format(shlex.quote(unit)) for unit, _name in units]
    detects.append("test -d {}".format(DOCKER_WEBCAM))
    presets.append(_docker_preset())
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(runner, command, timeout) for command in detects]
        sudo = pool.submit(runner, SUDO_CHECK, timeout) if units else None
        results = [future.result() for future in futures]
        sudo_ok = sudo.result()[0] == 0 if sudo is not None else True
    for index, preset in enumerate(presets):
        exit_code = results[index][0]
        preset["available"] = exit_code == 0
        needs_sudo = index < len(units)
        preset["note"] = "Needs passwordless sudo" if preset["available"] and needs_sudo and not sudo_ok else ""
    return {"plugin": "webcam", "name": "Webcam services", "presets": presets}


# The picker's own rules, in Python: add_plugin_preset does what its addPlugin does, and key_for gives the key keyFor gives.
DIVIDER = "divider"
PLUGIN_DIVIDER = "Plugin devices"
_COMBINING = re.compile("[\u0300-\u036f]")
_NON_KEY = re.compile("[^a-z0-9]+")


def key_for(name, fallback, taken=()):
    """The hidden key of a control or command: its name slugged, then _2, _3... while divider or taken."""
    text = _COMBINING.sub("", unicodedata.normalize("NFKD", (name or "").lower()))
    base = _NON_KEY.sub("_", text).strip("_") or fallback
    candidate, counter = base, 2
    while candidate == DIVIDER or candidate in taken:
        candidate = "{}_{}".format(base, counter)
        counter += 1
    return candidate


def _entry_keys(actions):
    return {entry.get("action") for entry in actions if entry.get("action") not in (None, DIVIDER)}


def _section_end(actions, name):
    """The index just past the last item of the section headed ``name``, or None when there is no such heading."""
    start = next((i for i, e in enumerate(actions) if e.get("action") == DIVIDER and e.get("name") == name), None)
    if start is None:
        return None
    end = start + 1
    while end < len(actions) and actions[end].get("action") != DIVIDER:
        end += 1
    return end


def _place_in_plugin_section(actions, entries):
    """Put ``entries`` after the last item of the Plugin devices section, the divider made at the end when missing."""
    end = _section_end(actions, PLUGIN_DIVIDER)
    if end is None:
        actions.append({"action": DIVIDER, "name": PLUGIN_DIVIDER})
        actions.extend(entries)
    else:
        actions[end:end] = entries


def _command_entry(preset, command, key, group):
    """One command's ``system.actions`` entry. A command with ``group`` is a control's member; without, standalone."""
    entry = {"action": key, "name": command["name"]}
    if group is not None:
        entry["group"] = group
        entry["role"] = command["role"]
    entry["preset"] = preset["preset"]
    # a link goes in as its url, a shell line as the command, anything else as a run call. Never more than one.
    if command.get("url"):
        entry["url"] = command["url"]
    elif command.get("command"):
        entry["command"] = command["command"]
    else:
        entry["run"] = command["run"]
    if command.get("icon"):
        entry["icon"] = command["icon"]
    if command.get("async"):
        entry["async"] = True
    if command.get("confirm"):
        entry["confirm"] = command["confirm"]
    if command.get("when"):
        entry["when"] = copy.deepcopy(command["when"])
    if command.get("when_control"):
        entry["when_control"] = copy.deepcopy(command["when_control"])
    return entry


def add_plugin_preset(preset, devices, actions):
    """Return ``(devices, actions)`` with one plugin preset added, as the picker's addPlugin adds it.

    A preset with a control adds the control at the end of ``devices``, and its commands at the end of ``actions``
    under one Plugin devices divider made when missing. A command-only preset (``control`` None) adds no device;
    each command is standalone and goes at the end of the Plugin devices section, the divider made when missing.
    The inputs are not modified.
    """
    preset = copy.deepcopy(preset)
    control = preset["control"]
    devices = list(devices)
    actions = list(actions)
    commands = preset.get("commands") or []

    if control is None:
        entries = []
        for command in commands:
            key = key_for(command["name"], "command", _entry_keys(actions) | {e["action"] for e in entries})
            entries.append(_command_entry(preset, command, key, None))
        _place_in_plugin_section(actions, entries)
        return devices, actions

    taken = {device.get("id") for device in devices} | _entry_keys(actions)
    device_id = key_for(control["name"], "control", taken)
    device = {"id": device_id, "type": control["type"], "name": control["name"], "icon": control["icon"],
              "preset": preset["preset"]}
    if control.get("read") is not None:
        device["read"] = control["read"]
    if control.get("status"):
        device["status"] = control["status"]
    if control.get("on_pattern"):
        device["on_pattern"] = control["on_pattern"]
    if control.get("off_pattern"):
        device["off_pattern"] = control["off_pattern"]
    devices.append(device)

    if not commands:
        return devices, actions
    entries = []
    for command in commands:
        label = "{} {}".format(preset["name"], command["name"])
        key = key_for(label, "command", _entry_keys(actions) | {e["action"] for e in entries})
        entries.append(_command_entry(preset, command, key, device_id))
    _place_in_plugin_section(actions, entries)
    return devices, actions


# The state words each plugin's control can report, for a control read of a plugin with no state_values helper.
STATE_LISTS = {
    "bettergrblsupport": ["Idle", "Run", "Hold", "Jog", "Alarm", "Door", "Check", "Home", "Sleep", "Reset", "N/A"],
}

# OctoPrint's printer state words, as getStateString returns them on 1.11.8 and 2.0.0rc5
PRINTER_STATE_WORDS = [
    "Offline", "Offline after error", "Opening serial connection", "Detecting serial connection", "Connecting",
    "Operational", "Starting", "Starting print from SD", "Starting to send file to SD", "Printing",
    "Printing from SD", "Sending file to SD", "Transferring file to SD", "Pausing", "Paused", "Resuming",
    "Cancelling", "Finishing", "Error",
]


def _helper_states(plugin_id, plugin_manager):
    # the plugin's state_values helper as a list of words, or None when it has none, raises or returns no list
    try:
        helper = (plugin_manager.get_helpers(plugin_id, "state_values") or {}).get("state_values")
        if not callable(helper):
            return None
        values = helper()
    except Exception:
        return None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None
    return list(values)


def state_list(spec, plugin_manager):
    """``{"source", "states"}`` for a control read: the printer's words, a plugin's words, or free text.

    A printer state read is "printer"; a plugin read is "helper" when the plugin's state_values helper answers, else
    "catalog" when STATE_LISTS lists the plugin, else "free". Anything else, a status line included, is "free".
    """
    if not isinstance(spec, dict):
        return {"source": "free", "states": []}
    if spec.get("printer") == "state":
        return {"source": "printer", "states": list(PRINTER_STATE_WORDS)}
    plugin_id = spec.get("plugin")
    if not isinstance(plugin_id, str) or not plugin_id:
        return {"source": "free", "states": []}
    states = _helper_states(plugin_id, plugin_manager)
    if states is not None:
        return {"source": "helper", "states": states}
    if plugin_id in STATE_LISTS:
        return {"source": "catalog", "states": list(STATE_LISTS[plugin_id])}
    return {"source": "free", "states": []}


def first_start_presets(groups):
    """The presets first start adds: the OctoPrint connection, then every switch preset of the other groups.

    A preset whose ``available`` is False is left out; a preset with no ``available`` key counts as available. A
    command-only preset (``control`` None) is never added on first start.
    """
    connection = [
        preset
        for group in groups if group.get("plugin") == "octoprint"
        for preset in group["presets"] if preset["preset"] == "octoprint:connection"
    ]
    switches = [
        preset
        for group in groups if group.get("plugin") != "octoprint"
        for preset in group["presets"] if preset["control"] and preset["control"]["type"] == "switch"
    ]
    return [preset for preset in connection + switches if preset.get("available") is not False]
