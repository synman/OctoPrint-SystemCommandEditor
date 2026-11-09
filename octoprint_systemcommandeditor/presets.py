import shlex
from concurrent.futures import ThreadPoolExecutor

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


def sample_devices(detected):
    return [
        {key: item[key] for key in DEVICE_KEYS}
        for item in detected
        if item["sample"] and item["available"]
    ]


SUDO_CHECK = "sudo -n true"


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
            "name": "Delete timelapses older than 30 days",
            "icon": "fa-solid fa-film",
            "command": "find {} -maxdepth 1 -type f -mtime +30 -delete".format(timelapse),
            "confirm": "This deletes timelapse videos older than 30 days.",
            "async": False,
            "sample": False,
            "needs_sudo": False,
            "detect": "test -d {}".format(timelapse),
        },
        {
            "action": "purge_logs",
            "name": "Delete logs older than 14 days",
            "icon": "fa-solid fa-file-lines",
            "command": "find {} -maxdepth 1 -type f -name '*.log.*' -mtime +14 -delete".format(logs),
            "confirm": "This deletes rotated OctoPrint logs older than 14 days.",
            "async": False,
            "sample": False,
            "needs_sudo": False,
            "detect": "test -d {}".format(logs),
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
