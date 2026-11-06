# OctoPrint System Command Editor

Add, edit, reorder and group the commands in OctoPrint's System menu, the power icon in the navigation bar,
from **Settings → Plugins → System Command Editor**.

![The editor](extras/screenshots/editor.png)

![The System menu](extras/screenshots/menu.png)

![Editing a command](extras/screenshots/command.png)

## What it does

- Lists your commands the way the System menu shows them, with buttons on each row to move, edit, duplicate
  and delete it. Drag a row by its handle to move it.
- **Add command** opens an editor for the name, the shell command, an icon and these options:
  - ask for confirmation, with your own message
  - run in the background, for commands that stop or restart OctoPrint
  - require a recent login
  - ignore errors
- **Add section** adds a divider line, with an optional heading that the System menu shows above the
  commands that follow it.
- **Run now** in the editor runs a saved command so you can try it.
- OctoPrint's built-in entries (Shutdown, Reboot, Restart, Restart in safe mode) get icons and the headings
  "System" and "OctoPrint" out of the box. Their rows at the top of the editor let you change those icons and
  headings. What they run stays under **Settings → Server**, and they appear only once a command is set there.
- The System menu shows each command's icon and each section's heading. Only the plugin's own CSS and
  JavaScript are used; OctoPrint itself is not modified. Icons come from the settings, so users who may run
  system commands but not change settings see headings without icons.

Changes take effect when you click **Save** in the settings dialog. On OctoPrint 2.0 you are asked for your
password, because OctoPrint requires a recent login to change system commands.

## Examples

Any shell command the OctoPrint user can run works. Some common ones for a printer setup:

| Section | Command | What it runs |
|---|---|---|
| Printer | Turn printer on / off | `curl -s "http://printer-plug.local/cm?cmnd=Power%20On"` on a Tasmota smart plug (`Power%20Off` to turn it off) |
| Chamber | Chamber heater on / off | the same `curl` against the heater's smart plug |
| Chamber | Chamber light on / off | `pinctrl set 17 op dh` / `pinctrl set 17 op dl` for a relay on a Raspberry Pi GPIO pin |
| Camera | Restart camera stream | `sudo systemctl restart camera-streamer` |
| Laser module | Laser power on / off | the same `curl` against the laser's smart plug |
| Maintenance | Back up OctoPrint | `~/oprint/bin/octoprint plugins backup:backup`, run in the background |
| Maintenance | Delete old timelapses | `find ~/.octoprint/timelapse -name '*.mp4' -mtime +30 -delete` |

Turn on **Ask for confirmation** for anything you would not want to click by mistake, like turning the printer off
while the hotend is still hot. A command that uses `sudo` needs a sudoers rule that lets the OctoPrint user run it
without a password. Host names, GPIO pins and paths depend on your setup.

## Where your commands are kept

Commands are stored where OctoPrint keeps them, under `system.actions` in `config.yaml`. The plugin keeps no
copy of its own. If you uninstall it, your commands stay in the System menu. Icons and section headings are
saved with the commands as two extra keys, `icon` and a divider's `name`, which OctoPrint ignores. The icons
and headings of OctoPrint's built-in entries have nowhere to go in OctoPrint's own settings, so they are kept in
the plugin's settings under `plugins.systemcommandeditor.core`.

## Upgrading from 1.x

Versions before 2.0 kept a second copy of the commands in the plugin's own settings and copied it back over
`system.actions` on every start. That could wipe commands on a fresh install, and its editor could save
several copies of a new command.

On the first start after upgrading, 2.0 converts your commands in place, once:

1. Both lists are saved to `data/systemcommandeditor/pre-2.0-backup-<date>.yaml` in OctoPrint's base folder.
2. Commands from the plugin's copy that are missing from `system.actions` are added back at the end.
3. Duplicate commands, which share an identifier, are reduced to the first one.
4. Repeated dividers, a plain divider at the start, and any divider at the end are removed.
5. The plugin's copy is deleted.

The result is written to `octoprint.log`, for example:

```
Converted system commands: 4 commands, 2 duplicates removed ['restart'], 1 restored from the plugin's old copy ['wifi'], 0 invalid entries skipped
```

## Requirements

OctoPrint 1.9 or newer, including 2.0, on Python 3.7 or newer. Tested on 1.9.3, 1.10.3, 1.11.8 and 2.0.0rc5.

## Setup

Install it through the Plugin Manager, or with the Python that runs OctoPrint:

    pip install https://github.com/synman/OctoPrint-SystemCommandEditor/archive/master.zip

## Development

Unit tests cover the conversion and the upgrade step. Run them with a Python that has OctoPrint installed:

    pip install pytest
    python -m pytest tests

Files:

| Path | What it holds |
|---|---|
| `octoprint_systemcommandeditor/__init__.py` | the plugin and the upgrade step |
| `octoprint_systemcommandeditor/convert.py` | the conversion of 1.x commands, no OctoPrint imports |
| `octoprint_systemcommandeditor/templates/systemcommandeditor_settings.jinja2` | the settings pane and editor |
| `octoprint_systemcommandeditor/static/js/systemcommandeditor.js` | the settings pane and editor logic |
| `octoprint_systemcommandeditor/static/js/systemcommandeditor_menu.js` | icons and headings in the System menu |
| `octoprint_systemcommandeditor/static/css/systemcommandeditor.css` | styles for both |
| `tests/` | pytest suites |
