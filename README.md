# Linux Wireless Manager

A small Linux app that shows the battery of your wireless mice and
keyboards, warns you before they run out, and lets you change some of their
settings (DPI, polling rate, sleep timer, button mapping). A mouse icon in
the tray shows the mouse's battery level, a keyboard icon appears while a
wireless keyboard is connected, and double-clicking either opens a window
listing every detected device with Status, Settings and Alerts tabs.

This app satisfies the need of an application for Linux systems since some of the official companion applications (like SteelSeries GG) are not yet compatible/supported. This was done completely vibe coding, and serves a super simple purpouse.  

It doesn't implement device protocols itself:

- **SteelSeries mice** are read through
  [`rivalcfg`](https://github.com/flozz/rivalcfg), an actively maintained
  open-source library that knows how to talk to SteelSeries mice on Linux.
- **Other wireless mice and keyboards** come from
  [UPower](https://upower.freedesktop.org/), which collects the batteries
  the Linux kernel reports: Logitech devices on a Unifying, Bolt or
  Lightspeed receiver, and Bluetooth mice and keyboards. Their settings, on
  Logitech devices, come from [Solaar](https://pwr-solaar.github.io/Solaar/)
  (optionally installed by `install.sh`) - unlike SteelSeries, DPI and
  report rate can be read back from the device, not just written.

## Supported devices

| Devices | Battery | Settings | Tested on hardware |
|---|---|---|---|
| SteelSeries Rival 650 Wireless | Yes | DPI presets, polling rate, sleep timer, button mapping | Yes |
| SteelSeries Rival 3 Wireless (and Gen 2), Aerox 3 / 5 / 9 Wireless, Prime Wireless, Prime Mini Wireless, including special editions | Yes | Same, model-dependent (e.g. a DPI step list instead of two presets) | No |
| Other SteelSeries mice `rivalcfg` supports | No battery; listed in the window only | Same as above, where the model has any | No |
| Logitech mice and keyboards on a receiver, and Bluetooth mice and keyboards (through UPower) | Yes | Logitech only, through Solaar: DPI, pointer speed, report rate | No |

SteelSeries settings can't be read back from the mouse - it's a write-only
protocol - so the Settings tab shows either the values this app last saved,
or the model's own defaults if nothing has been saved yet; it says which.
Logitech settings, read live through Solaar, don't have that problem.

See [ROADMAP.md](ROADMAP.md) for what's still only been checked against
simulated hardware, not the real thing.

## Requirements

- Ubuntu (or another udev-based Linux distro) with a desktop tray/indicator
  area. GNOME users need the [AppIndicator and KStatusNotifierItem Support
  extension](https://extensions.gnome.org/extension/615/appindicator-support/)
  for the icons to show up, since stock GNOME Shell hides tray icons (Ubuntu
  ships it enabled).
- For SteelSeries mice: the USB wireless receiver plugged in (or the mouse
  connected by cable).
- For Logitech device *settings* specifically (battery works without it):
  [Solaar](https://pwr-solaar.github.io/Solaar/), which `install.sh` offers
  to install (`apt install solaar`) and which needs the device already
  paired in Solaar itself.

## Install

**Option 1: `.deb` package.** Download the latest one from
[Releases](https://github.com/xdoncalvox/LinuxMouseStatus/releases), then:

```bash
sudo apt install ./linux-wireless-manager_*_all.deb
```

(`apt install ./<file>` rather than `dpkg -i` so apt pulls in the system
packages it Depends on automatically; `sudo dpkg -i ./<file>.deb && sudo apt
install -f` works too.) It installs to `/usr`, adds an application menu
entry, and sets up a private Python virtualenv the first time it configures
(needs network access then, since `rivalcfg` isn't packaged for Debian or
Ubuntu - see `debian/control`). It does **not** enable autostart on login;
copy `/usr/share/applications/linux-wireless-manager.desktop` to
`~/.config/autostart/` yourself if you want that.

**Option 2: from this repo**, if you'd rather not install anything
system-wide:

```bash
./install.sh
```

This installs the required apt packages, sets up a Python virtualenv
*inside this repo's own `venv/` folder*, installs a udev rule so the app can
read SteelSeries mice without root, and adds an application menu entry
(with an option to start on login). If you had the app installed under its
old name, "SteelSeries Battery Monitor", its menu and autostart entries are
replaced.

Either way, **after installing, unplug and replug the USB receiver once**
(or reboot) so the new udev permission rule takes effect.

## Run

From the `.deb`:

```bash
linux-wireless-manager
```

From this repo (`./install.sh`):

```bash
./run.sh
```

Either way, you can also launch "Linux Wireless Manager" from your
application menu.

Pass `--show-window` to also open the window on startup, or `--verbose` to
log what's happening to the terminal:

```bash
./run.sh --show-window --verbose
```

## How it works

**Detection and battery**
- Every 10 seconds the app checks which devices are connected. This only
  lists USB devices and asks UPower; it never talks to a device, so it
  doesn't wake anything up.
- SteelSeries mice are read once a minute, because each read wakes the
  mouse's radio and polling more often drains its battery for little
  benefit. If a read fails (mouse asleep, out of range, momentarily busy),
  it retries after 10 seconds. UPower devices are checked every 30 seconds,
  which costs nothing since UPower already has the value.

**Tray icons and the window**
- The tray's mouse icon shows the lowest battery among your wireless mice.
  The keyboard icon only appears while a battery-powered keyboard is
  connected. Double-click either to open the window; their menus also have
  "Show Details", "Refresh Now" and "Quit".
- The window lists every detected device, including wired mice with no
  battery, and remembers which one you last selected. Each device has
  three tabs: **Status** (current level, connection, last update time),
  **Settings**, and **Alerts**.

**Alerts**
- You get a desktop notification when a battery first drops past 20% and
  again past 10%, and when a charging device reaches full. A warning only
  repeats after the device charges or climbs back above the level, so a
  battery hovering around 20% won't nag you. Each device's levels can be
  changed, or notifications turned off, in the window's Alerts tab.
  Clicking "Show Details" on a notification opens the window on that
  device.

**Settings**
- SteelSeries mice: DPI presets (or, on models with a multi-step DPI list,
  an editable add/remove list of them), polling rate, sleep timer, and
  button remapping - each button gets a dropdown of the mouse's other
  buttons, media keys, and keyboard keys. Since the mouse can't report
  these back, the tab shows either the values this app last saved to it,
  or the model's defaults if nothing has been saved yet, and says which.
- Logitech mice and keyboards (through [Solaar](https://pwr-solaar.github.io/Solaar/),
  see Requirements below): DPI, pointer speed, and report rate, read live
  from the device rather than remembered.
- Changes only take effect when you click **Apply**; a mouse's primary
  click button can't be remapped away to something else, so you can't
  accidentally lock yourself out of clicking. **Reset to Defaults** is
  available for SteelSeries mice (Logitech settings, through Solaar, have
  no such command).

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

**The Settings tab says "This device has no configurable settings"**
For a SteelSeries mouse, that model has no writable settings in `rivalcfg`
(rare - most wireless models have at least DPI and polling rate). For a
Logitech device, either [Solaar](https://pwr-solaar.github.io/Solaar/)
isn't installed, the device isn't paired in Solaar yet, or it's a
Bluetooth device Solaar doesn't manage (its battery still works either
way). Check with:

```bash
solaar show
```

**Applying a setting fails with a Solaar error**
Make sure the device shows up in `solaar show` and is online (not asleep
or out of range), and that Solaar's own app isn't mid-way through changing
the same device. `solaar config <device>` prints the raw values this app
reads.

**Installing the `.deb` fails, or `linux-wireless-manager` isn't found afterward**
The package's `postinst` step needs network access to fetch `rivalcfg` from
PyPI into a private virtualenv (`/usr/lib/linux-wireless-manager/venv`) -
there's no Debian/Ubuntu package for it, so this can't be a plain `Depends`.
If that step failed (check `sudo apt install -f` or `sudo dpkg --configure
-a`'s output), fix your network connection and retry it directly:

```bash
sudo /usr/lib/linux-wireless-manager/venv/bin/pip install \
    --no-build-isolation /usr/lib/linux-wireless-manager/src
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
build-deb.sh                         builds the .deb in dist/ (see debian/) - doesn't install it
debian/                              .deb packaging metadata (control, postinst, postrm, copyright, changelog)
pyproject.toml                       Python package metadata; what build-deb.sh/pip install actually installs
requirements.txt                     Python deps (rivalcfg) - what install.sh's venv uses directly
linux-wireless-manager.desktop.in    template for the app-menu/autostart entry
.github/workflows/release.yml        builds and publishes the .deb to GitHub Releases on a version tag push
linux_wireless_manager/
    main.py          CLI entry point
    app.py           wires the monitor, tray icons and window together
    monitor.py       background polling and settings reads/writes for every detected device
    devices/
        __init__.py      combines every backend's discover()
        base.py          the Device interface, battery snapshots and the settings schema
        steelseries.py   SteelSeries mice through rivalcfg (battery and settings)
        upower.py        mice and keyboards reported by UPower (battery; Logitech settings via solaar.py)
        solaar.py        Logitech mouse/keyboard settings through the Solaar CLI
    tray.py          the mouse and keyboard tray icons
    icons.py         generates the tray/window icons
    window.py        the main window (Status / Settings / Alerts tabs)
    alerts.py        low-battery and fully-charged notifications
    preferences.py   app preferences (~/.config/linux-wireless-manager/)
    config.py        app ID, name, detection interval
```

## License

[WTFPL](LICENSE) (same as `rivalcfg`, which it depends on and takes protocol
details from).
