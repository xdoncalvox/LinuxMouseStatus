# SteelSeries Battery Monitor

A small Ubuntu tray app that shows the battery level of a **SteelSeries
Rival 650 Wireless** mouse: a tray icon that updates automatically, plus an
optional detail window.

It doesn't reimplement the SteelSeries USB/HID protocol itself — it uses
[`rivalcfg`](https://github.com/flozz/rivalcfg), an existing, actively
maintained open-source library that already knows how to talk to SteelSeries
mice on Linux.

## Requirements

- Ubuntu (or another udev-based Linux distro) with a desktop tray/indicator
  area — GNOME users need the [AppIndicator and KStatusNotifierItem Support
  extension](https://extensions.gnome.org/extension/615/appindicator-support/)
  for the icon to show up, since stock GNOME Shell hides tray icons.
- The mouse's USB wireless receiver plugged in (or the mouse connected via
  USB-C cable).

## Install

```bash
./install.sh
```

This installs the required apt packages, sets up a Python virtualenv,
installs a udev rule so the app can read the mouse without root, and adds
an application menu entry (with an option to start on login).

**After installing, unplug and replug the USB receiver once** (or reboot) so
the new udev permission rule takes effect.

## Run

```bash
./run.sh
```

or launch "SteelSeries Battery Monitor" from your application menu.

Pass `--show-window` to also open the detail window on startup, or
`--verbose` to log what's happening to the terminal:

```bash
./run.sh --show-window --verbose
```

## How it works

- The tray icon polls the mouse once a minute (it has to wake the mouse's
  radio to ask, so polling more often drains the battery faster for little
  benefit). If a read fails — mouse asleep, out of range, momentarily busy —
  it retries after 10 seconds instead of waiting the full minute.
- Left-clicking (or right-clicking, depending on your desktop) the tray icon
  opens a menu with the current percentage, a "Show Details" window, a
  manual "Refresh Now", and "Quit".
- The icon and label update to reflect charge level and whether the mouse is
  currently charging.

## Troubleshooting

**Tray icon shows a "?" / battery unavailable**
The mouse could not be reached. Check that:
- the mouse is turned on (its power switch) and not out of USB-dongle range,
- the USB receiver is plugged in,
- the udev rule was installed — re-run `./install.sh`, then unplug/replug
  the receiver,
- you're not running SteelSeries GG or another tool that's holding the
  device open at the same time.

Run `./run.sh --verbose` and check the terminal output for the specific
error.

**No tray icon shows up at all (stock GNOME)**
GNOME Shell doesn't show legacy tray icons without an extension. Install
["AppIndicator and KStatusNotifierItem
Support"](https://extensions.gnome.org/extension/615/appindicator-support/)
from the GNOME Extensions site, or use another desktop (KDE, Xfce, Ubuntu's
default "Ubuntu" session with Yaru, MATE, etc.) that supports it natively.

**`install.sh` can't find an AppIndicator package**
Ubuntu renamed the underlying library from `libappindicator` to
`libayatana-appindicator` a few releases ago, and the exact GObject
introspection package name (`gir1.2-ayatanaappindicator3-0.1` vs.
`gir1.2-appindicator3-0.1`) depends on your release. If `apt` can't find
either, search `apt search appindicator` for what's available on your
system and install it manually, then re-run `./install.sh`.

## Project layout

```
install.sh                                 setup script (apt deps, venv, udev, menu entry)
run.sh                                      launcher
requirements.txt                            Python deps (rivalcfg)
steelseries-battery-monitor.desktop.in      template for the app-menu/autostart entry
steelseries_battery_monitor/
    config.py       vendor/product IDs, polling interval
    battery.py       talks to rivalcfg to read battery %/charging state
    icons.py         picks a themed battery icon name for a given level
    window.py        the detail window
    app.py           the tray indicator + polling loop
    main.py           CLI entry point
```

## Adapting this to a different SteelSeries mouse

This app is wired specifically to the Rival 650 Wireless's USB IDs
(`0x1038:0x1726` wireless, `0x1038:0x172b` wired). If you have a different
SteelSeries mouse, check whether `rivalcfg` supports it and reports battery
for it (`rivalcfg --list-devices`, `rivalcfg --battery-level`), then update
`PRODUCT_IDS` in `steelseries_battery_monitor/config.py` to match.

## License

This project: WTFPL (same as `rivalcfg`, which it depends on and takes
protocol details from).
