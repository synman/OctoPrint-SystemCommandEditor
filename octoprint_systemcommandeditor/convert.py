# coding=utf-8
"""Conversion of system commands saved by older versions of this plugin.

Before 2.0 the plugin kept a copy of OctoPrint's ``system.actions`` in its own settings and, on every startup,
copied that copy back over ``system.actions``. 2.0 edits ``system.actions`` directly and keeps no copy, so on
upgrade the two lists are merged into ``system.actions`` once and the copy is dropped.

Kept free of OctoPrint imports so it can be tested on its own.
"""

DIVIDER = "divider"

# keys OctoPrint itself reads from a system action, plus the ones this plugin adds
KNOWN_KEYS = ("action", "name", "command", "async", "confirm", "fresh_credentials", "ignore", "icon")


def _is_divider(entry):
    return entry.get("action") == DIVIDER


def _clean(entry):
    """Copy an entry, dropping empty optional values. Unknown keys are kept so nothing is lost."""
    result = {}
    for key, value in entry.items():
        if key == "action":
            result[key] = value
        elif key in ("confirm", "icon", "name") and not value:
            continue
        elif key in ("async", "fresh_credentials", "ignore") and not value:
            continue
        else:
            result[key] = value
    return result


def _valid(entry):
    return isinstance(entry, dict) and isinstance(entry.get("action"), str) and entry["action"] != ""


def convert(system_actions, legacy_actions):
    """Merge OctoPrint's system actions with the plugin's legacy copy.

    Returns ``(actions, report)``. ``actions`` keeps the order of ``system_actions``, then appends commands only
    the legacy copy has. A command whose ``action`` was already seen is dropped, so the duplicates the old editor
    created collapse to their first entry. Adjacent dividers collapse, and dividers at either end are dropped.
    """
    report = {"duplicates": [], "restored": [], "invalid": 0, "dividers_dropped": 0}

    merged = []
    seen = set()

    def add(entry, restoring):
        if not _valid(entry):
            report["invalid"] += 1
            return
        if _is_divider(entry):
            merged.append(_clean(entry))
            return
        action = entry["action"]
        if action in seen:
            if not restoring:
                report["duplicates"].append(action)
            return
        seen.add(action)
        if restoring:
            report["restored"].append(action)
        merged.append(_clean(entry))

    for entry in system_actions or []:
        add(entry, False)

    restoring = [entry for entry in (legacy_actions or []) if _valid(entry) and not _is_divider(entry)]
    if restoring and any(entry["action"] not in seen for entry in restoring):
        if merged and not _is_divider(merged[-1]):
            merged.append({"action": DIVIDER})
        for entry in restoring:
            add(entry, True)

    actions = []
    for entry in merged:
        if _is_divider(entry):
            if actions and _is_divider(actions[-1]):
                # two dividers in a row: keep one, preferring the one that names a section
                if entry.get("name") and not actions[-1].get("name"):
                    actions[-1] = entry
                report["dividers_dropped"] += 1
                continue
            if not actions and not entry.get("name"):
                # a plain divider at the top separates nothing; a named one starts the first section
                report["dividers_dropped"] += 1
                continue
        actions.append(entry)
    while actions and _is_divider(actions[-1]):
        actions.pop()
        report["dividers_dropped"] += 1

    return actions, report


LINK_FIELDS = ("name", "url", "icon", "icon_color", "icon_invert")


def link_devices(devices, actions, key_for):
    """Settings v5: every device of type ``link`` becomes a standalone link command at the end of ``actions``.

    ``key_for(name, fallback, taken)`` makes each command's action key (the picker's key rule, passed in so this
    module needs no import of presets). Returns ``(devices, actions, changed)``. The other devices and entries are
    kept as they are, in order; a config with no link device is returned untouched.
    """
    if not any(isinstance(d, dict) and d.get("type") == "link" for d in devices or []):
        return devices, actions, False
    actions = [dict(entry) for entry in actions or []]
    taken = {entry.get("action") for entry in actions if _valid(entry) and not _is_divider(entry)}
    kept = []
    for device in devices:
        if not (isinstance(device, dict) and device.get("type") == "link"):
            kept.append(device)
            continue
        key = key_for(device.get("name") or "", "link", taken)
        taken.add(key)
        entry = {"action": key}
        entry.update({field: device[field] for field in LINK_FIELDS if field in device})
        if device.get("when"):
            entry["when"] = device["when"]
        if device.get("preset"):
            entry["preset"] = device["preset"]
        actions.append(entry)
    return kept, actions, True


def live_plug_reads(devices):
    """Settings v6: the TP-Link and Tasmota plug reads go back into live updates.

    Those presets used to save ``live: false`` on their read, which kept the System menu's live timer from polling
    them. Every device whose read is from ``tplinksmartplug`` or ``tasmota`` and carries ``live: false`` gets a copy
    of its read without the ``live`` key. Returns ``(devices, changed)``. Every other device is kept as it is, in
    order; a config with no such read is returned untouched.
    """
    if not any(_is_plug_read(device) for device in devices or []):
        return devices, False
    fixed = []
    for device in devices:
        if _is_plug_read(device):
            read = {key: value for key, value in device["read"].items() if key != "live"}
            device = dict(device, read=read)
        fixed.append(device)
    return fixed, True


def role_icons(actions):
    """Settings v7: a switch's On and Off commands with the old default icons get the toggle icons.

    The On command had the play icon and the Off command the power icon; both now read as the switch they drive.
    Only those two pairs change; any other icon, and any other role, is kept as it is. Returns ``(actions, changed)``.
    The inputs are not modified, and a config with no such entry is returned untouched.
    """
    fixed = []
    changed = False
    for entry in actions or []:
        if isinstance(entry, dict):
            role, icon = entry.get("role"), entry.get("icon")
            if role == "on" and icon == "fa-solid fa-play":
                entry = dict(entry, icon="fa-solid fa-toggle-on")
                changed = True
            elif role == "off" and icon == "fa-solid fa-power-off":
                entry = dict(entry, icon="fa-solid fa-toggle-off")
                changed = True
        fixed.append(entry)
    return (fixed, True) if changed else (actions, False)


def _is_plug_read(device):
    if not isinstance(device, dict):
        return False
    read = device.get("read")
    return isinstance(read, dict) and read.get("plugin") in ("tplinksmartplug", "tasmota") and read.get("live") is False


def command_count(actions):
    return sum(1 for entry in actions or [] if _valid(entry) and not _is_divider(entry) and "command" in entry)


def free_key(base, used):
    """Return ``base`` if unused, else ``base_2``, ``base_3``... ``used`` holds every action key in use."""
    if base not in used:
        return base
    n = 2
    while "{}_{}".format(base, n) in used:
        n += 1
    return "{}_{}".format(base, n)


def _is_placement(entry):
    # a placement names its readout; an entry with a run and no command is a plugin control's command, not one
    return _valid(entry) and not _is_divider(entry) and "command" not in entry and "device" in entry


def place_readouts(devices, actions):
    """Give every readout device without a placement one, placed after its section's divider's last entry.

    A readout's placement is an action entry ``{"action", "name", "device"}`` with no command. Returns
    ``(devices, actions, changed)``. Readouts keep their order. A section with no divider gets one at the end;
    a readout with no section is appended at the end. ``section`` is dropped from every readout device.
    Nothing is touched when there are no readouts.
    """
    readouts = [d for d in devices or [] if isinstance(d, dict) and d.get("type") == "readout"]
    if not readouts:
        return devices, actions, False

    placed = {entry.get("device") for entry in actions or [] if _is_placement(entry)}
    actions = [dict(entry) for entry in actions or []]
    for device in readouts:
        if device.get("id") in placed:
            continue
        used = {entry.get("action") for entry in actions} | {DIVIDER}
        placement = {"action": free_key(device["id"], used), "name": device.get("name", ""), "device": device["id"]}
        section = device.get("section")
        if not section:
            actions.append(placement)
            continue
        divider = next((i for i, e in enumerate(actions) if _is_divider(e) and e.get("name") == section), None)
        if divider is None:
            actions.append({"action": DIVIDER, "name": section})
            actions.append(placement)
            continue
        end = divider
        while end + 1 < len(actions) and not _is_divider(actions[end + 1]):
            end += 1
        actions.insert(end + 1, placement)

    new_devices = []
    for device in devices:
        if isinstance(device, dict) and device.get("type") == "readout" and "section" in device:
            device = {key: value for key, value in device.items() if key != "section"}
        new_devices.append(device)
    return new_devices, actions, True
