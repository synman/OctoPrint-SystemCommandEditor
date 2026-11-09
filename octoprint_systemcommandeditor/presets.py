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
        "sample": True,
        "status": "if command -v free >/dev/null; then free -h | awk '/Mem/{print $7\" free\"}'; else memory_pressure | awk '/free percentage/{print $5\" free\"}'; fi",
    },
    {
        "id": "load",
        "name": "Load",
        "icon": "fa-solid fa-gauge",
        "sample": True,
        "status": "uptime | sed 's/.*load averages*: //; s/,//g'",
    },
    {
        "id": "uptime",
        "name": "Uptime",
        "icon": "fa-solid fa-clock",
        "sample": True,
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
        "sample": True,
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

DEVICE_KEYS = ("id", "type", "name", "icon", "status", "section")


def device_of(preset):
    return {
        "id": preset["id"],
        "type": "readout",
        "name": preset["name"],
        "icon": preset["icon"],
        "status": preset["status"],
        "section": "Status",
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
