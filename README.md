# Linux Wireless Manager

A small Ubuntu tray app that shows the battery of your wireless mice and
keyboards. A mouse icon in the tray shows the mouse's battery level, a
keyboard icon appears while a wireless keyboard is connected, and a window
lists every detected device.

It doesn't implement device protocols itself:

- **SteelSeries mice** are read through
  [`rivalcfg`](https://github.com/flozz/rivalcfg), an actively maintained
  open-source library that knows how to talk to SteelSeries mice on Linux.
- **Other wireless mice and keyboards** come from
  [UPower](https://upower.freedesktop.org/), which collects the batteries
  the Linux kernel reports: Logitech devices on a Unifying, Bolt or
  Lightspeed receiver, and Bluetooth mice and keyboards.

## Supported devices

| Devices | Battery | Tested on hardware |
|---|---|---|
| SteelSeries Rival 650 Wireless | Yes | Yes |
| SteelSeries Rival 3 Wireless (and Gen 2), Aerox 3 / 5 / 9 Wireless, Prime Wireless, Prime Mini Wireless, including special editions | Yes | No |
| Other SteelSeries mice `rivalcfg` supports | No battery; listed in the window only | No |
| Logitech mice and keyboards on a receiver, and Bluetooth mice and keyboards (through UPower) | Yes | No |

See [ROADMAP.md](ROADMAP.md) for what's planned next: device settings (DPI,
polling rate, sleep timer, button mapping) and Logitech settings through
Solaar.

## Requirements

- Ubuntu (or another udev-based Linux distro) with a desktop tray/indicator
  area. GNOME users need the [AppIndicator and KStatusNotifierItem Support
  extension](https://extensions.gnome.org/extension/615/appindicator-support/)
  for the icons to show up, since stock GNOME Shell hides tray icons (Ubuntu
  ships it enabled).
- For SteelSeries mice: the USB wireless receiver plugged in (or the mouse
  connected by cable).

## Install

```bash
./install.sh
```

This installs the required apt packages, sets up a Python virtualenv,
installs a udev rule so the app can read SteelSeries mice without root, and
adds an application menu entry (with an option to start on login). If you
had the app installed under its old name, "SteelSeries Battery Monitor",
its menu and autostart entries are replaced.

**After installing, unplug and replug the USB receiver once** (or reboot) so
the new udev permission rule takes effect.

## Run

```bash
./run.sh
```

or launch "Linux Wireless Manager" from your application menu.

Pass `--show-window` to also open the window on startup, or `--verbose` to
log what's happening to the terminal:

```bash
./run.sh --show-window --verbose
```

## How it works

- Every 10 seconds the app checks which devices are connected. This only
  lists USB devices and asks UPower; it never talks to a device, so it
  doesn't wake anything up.
- SteelSeries mice are read once a minute, because each read wakes the
  mouse's radio and polling more often drains its battery for little
  benefit. If a read fails (mouse asleep, out of range, momentarily busy),
  it retries after 10 seconds. UPower devices are checked every 30 seconds,
  which costs nothing since UPower already has the value.
- The tray's mouse icon shows the lowest battery among your wireless mice.
  The keyboard icon only appears while a battery-powered keyboard is
  connected. Their menus list each device's level, and have "Show Details",
  "Refresh Now" and "Quit".
- The window lists every detected device, including wired mice with no
  battery, and remembers which one you last selected.
- You get a desktop notification when a battery first drops past 20% and
  again past 10%, and when a charging device reaches full. A warning only
  repeats after the device charges or climbs back above the level, so a
  battery hovering around 20% won't nag you. Each device's levels can be
  changed, or notifications turned off, in the window's Alerts tab.

## Troubleshooting

**The mouse icon shows "?" / the mouse is "unavailable"**
The mouse could not be reached. Check that:
- the mouse is turned on (its power switch) and within range of the USB
  receiver,
- the USB receiver is plugged in,
- the udev rule was installed: re-run `./install.sh`, then unplug and replug
  the receiver,
- you're not running another tool that's holding the device open at the
  same time.

The window shows a more specific message, and `./run.sh --verbose` prints
the underlying error.

**The window says "No supported devices found"**
No SteelSeries mouse that `rivalcfg` knows is plugged in, and UPower
reports no battery-powered mouse or keyboard. Check with:

```bash
venv/bin/rivalcfg --list-devices
```

```bash
upower --dump
```

**No tray icon shows up at all (stock GNOME)**
GNOME Shell doesn't show tray icons without an extension. Install
["AppIndicator and KStatusNotifierItem
Support"](https://extensions.gnome.org/extension/615/appindicator-support/)
from the GNOME Extensions site, or use another desktop (KDE, Xfce, MATE,
Ubuntu's default session) that supports it natively.

**`install.sh` can't find an AppIndicator package**
Ubuntu renamed the underlying library from `libappindicator` to
`libayatana-appindicator` a few releases ago, and the exact GObject
introspection package name (`gir1.2-ayatanaappindicator3-0.1` vs.
`gir1.2-appindicator3-0.1`) depends on your release. If `apt` can't find
either, search `apt search appindicator` for what's available on your
system and install it manually, then re-run `./install.sh`.

## Project layout

```
install.sh                           setup script (apt deps, venv, udev, menu entry)
run.sh                               launcher
requirements.txt                     Python deps (rivalcfg)
linux-wireless-manager.desktop.in    template for the app-menu/autostart entry
linux_wireless_manager/
    main.py          CLI entry point
    app.py           wires the monitor, tray icons and window together
    monitor.py       background polling of every detected device
    devices/
        base.py          the Device interface and the snapshots sent to the UI
        steelseries.py   SteelSeries mice through rivalcfg
        upower.py        mice and keyboards reported by UPower
    tray.py          the mouse and keyboard tray icons
    icons.py         generates the tray/window icons
    window.py        the main window
    alerts.py        low-battery and fully-charged notifications
    preferences.py   app preferences (~/.config/linux-wireless-manager/)
    config.py        app ID, name, detection interval
```

## License

This project: WTFPL (same as `rivalcfg`, which it depends on and takes
protocol details from).
