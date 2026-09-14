# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Linux Wireless Manager is a GTK3 + AppIndicator tray app for Ubuntu that shows the battery of wireless mice and keyboards. It was called "SteelSeries Battery Monitor" (package `steelseries_battery_monitor`) before 2.0.0. There are two device backends:

- SteelSeries mice, through [`rivalcfg`](https://github.com/flozz/rivalcfg)
- anything UPower reports as a battery-powered mouse or keyboard (Logitech receivers, Bluetooth)

No device protocol is implemented here. Planned work (device settings, Logitech settings through Solaar) is tracked in `ROADMAP.md`.

## Commands

```bash
./install.sh                          # apt deps, venv (--system-site-packages), rivalcfg, udev rule, .desktop entries
./run.sh --show-window --verbose      # run with the window open and debug logging
venv/bin/python3 -m linux_wireless_manager   # equivalent to run.sh
venv/bin/rivalcfg --list-devices      # is the SteelSeries mouse visible to rivalcfg?
venv/bin/rivalcfg --battery-level     # raw SteelSeries battery read, bypassing this app
upower --dump                         # what the UPower backend will see
RIVALCFG_DRY=1 RIVALCFG_PROFILE=1038:1838 ./run.sh --show-window   # pretend an Aerox 3 Wireless is plugged in (its reads fail)
```

There is no test suite, linter config, or build step. To verify a change, run the app against real hardware. To exercise `DeviceMonitor` without hardware, patch `monitor.discover_devices` to return fake `Device` subclasses and call `_run_cycle()` directly, or run a `GLib.MainLoop`.

## Dependencies

- **GTK / PyGObject / AppIndicator come from apt, not pip.** The venv is created with `--system-site-packages` so it can see them. `requirements.txt` only lists `rivalcfg` (which pulls in `hidapi`, imported as `hid`).
- The UPower backend uses Gio's D-Bus API directly, so it needs no extra package, only the `upower` daemon.
- Notifications use libnotify (`gir1.2-notify-0.7`). If the bindings are missing the app still runs; it just doesn't notify, and the Alerts tab says so.
- `tray.py` tries `AyatanaAppIndicator3` first and falls back to legacy `AppIndicator3`. `main.py` imports GTK, then `devices` (rivalcfg/hidapi), then `app` lazily inside `main()`, so a missing dependency produces a specific install hint instead of a traceback. Keep new GI imports behind that same boundary.

## Architecture

Data flows from the device backends (`devices/steelseries.py`, `devices/upower.py`) to `monitor.DeviceMonitor`, which runs them in a worker thread. The monitor sends a list of `DeviceState` snapshots to `app.WirelessManagerApp._on_devices_updated`, which passes them to both tray icons (`tray.KindIndicator`) and the window (`window.MainWindow`).

**Device interface (`devices/base.py`).**
- `Device` objects only ever live in the polling thread. The UI only sees frozen `DeviceState` snapshots, so GTK code never touches a device and device code never touches GTK.
- `BatteryStatus` carries either a `percentage` or a `coarse_level` (UPower devices that only report Low/Normal/…). Use `estimated_percentage` and `describe()` rather than reading `percentage` directly.
- Expected failures raise `BatteryReadError(detail, user_message)`: `detail` goes to the log, and `user_message` is shown in the window.
- `hardware_key` changing for the same `id` makes the monitor replace the object and re-read it immediately. This is how a SteelSeries mouse switching from receiver to cable is handled.
- The settings methods (`settings_schema`, `get_settings`, `apply_settings`) are stubs until ROADMAP Step 2.

**Adding a backend.** Add a module in `devices/` with a `discover()` function and list it in `devices/__init__._BACKENDS`. `discover()` runs every `DISCOVERY_INTERVAL_SECONDS` (10s), so it must be cheap and must never talk to a device. `discover_devices()` catches and logs each backend's exceptions separately, so one broken backend can't hide the others.

**Polling (`monitor.py`).** The module docstring lists the scheduling rules; read them before changing the file. In short:
- At most one pending GLib timeout exists, and its ID is only touched on the main thread.
- Every cycle ends in `_apply()`, which schedules the next tick before touching the UI.
- Each tick re-runs detection but only reads devices whose own `poll_interval_seconds` / `retry_interval_seconds` has elapsed. A manual refresh (`refresh_now`) reads everything.
- Reads are sequential in the worker thread, and `_poll_lock` is taken non-blocking.
- With `--verbose`, every read logs `Reading battery: <name>`, so you can check the cadence.

**SteelSeries backend (`devices/steelseries.py`).**
- Detection lists USB devices with `hid.enumerate(0x1038)` and looks each product ID up in `rivalcfg.devices.get_profile`. It never opens a device.
- A wireless mouse has one product ID per connection. rivalcfg names end in `(wired mode)` or `(2.4 GHz [wireless] mode)`, so IDs are grouped by the name with that suffix stripped, giving one `SteelSeriesMouse` per physical mouse. Reads try the receiver ID before the cable ID.
- Each read opens and **always closes** the HID handle, because holding it open blocks the rivalcfg CLI and other tools.
- Reads wake the mouse's radio, hence the 60s interval (10s retry).
- rivalcfg-supported mice without a `battery_level` profile appear with `has_battery=False`.
- `RIVALCFG_PROFILE` is honoured during detection so that rivalcfg's dry-run mode can simulate other models.

**UPower backend (`devices/upower.py`).**
- It makes synchronous Gio D-Bus calls from the worker thread and reads UPower's cached properties, so it never wakes a device.
- Numeric codes are UPower's `UpDeviceKind` (5 = mouse, 6 = keyboard), `UpDeviceState` and `UpDeviceLevel` enums.
- It skips `PowerSupply=true` batteries (the laptop's own) and `IsPresent=false` devices.
- It hasn't been tested with a real Logitech or Bluetooth device yet. The `IsPresent` filter and Bluetooth detection (`/org/bluez/...` or `hid-<MAC>-battery` native paths) are unverified assumptions.

**Tray (`tray.py`).**
- There is one `KindIndicator` per `DeviceKind`. The mouse icon is always visible, so the app stays reachable. The keyboard icon only shows while a battery-powered keyboard is connected.
- Each icon shows the lowest battery among its readable devices, and its menu lists all of them.
- Devices with `has_battery=False` are never passed to the tray.

**Icons (`icons.py`).**
- `install()` generates the SVGs (mouse/keyboard × 6 level steps × charging, plus "unknown") into `~/.cache/linux-wireless-manager/icons/` at startup.
- The tray loads them through `Indicator.new_with_path`, and the window through `Gtk.IconTheme.append_search_path`.
- Draw with **fills only**. GTK recolours symbolic icons by overriding `fill` (plain shapes become the panel's foreground colour, `class="error"` becomes red, `class="success"` green), so strokes wouldn't follow the theme.
- If the icons can't be written, the themed `input-mouse-symbolic` / `input-keyboard-symbolic` icons are used.

**Window (`window.py`).**
- A single `MainWindow` is created at startup and hidden, never destroyed, on close.
- The left side is a `Gtk.ListBox` of devices. The right side is a `Gtk.Stack` with Status / Settings / Alerts tabs. Settings is still a placeholder (ROADMAP Step 2).
- The Alerts tab is only re-filled when the shown device changes (`_alerts_device_id`), so a poll can't overwrite what the user is editing, and `_loading_alerts` stops the fill-in from counting as an edit.
- The list is only rebuilt when the snapshot list changes.
- The user's selected device is remembered in preferences (`selected_device`). While that device is absent, the window shows the first device without overwriting the remembered choice.

**Alerts (`alerts.py`).**
- `AlertManager.update()` gets every snapshot from `app._on_devices_updated`, on the main thread.
- Each threshold fires once and only re-arms when the device charges or climbs `_REARM_MARGIN` (5 points) above it. When several thresholds are crossed at once, only the lowest one notifies and all of them are marked as fired.
- Coarse-level devices need no special case: `estimated_percentage` maps Low to 20 and Critical to 5, which trips the default 20/10 thresholds.
- "Fully charged" needs a previous reading (`seen`), so a device that is already full at startup stays quiet.
- Settings live in preferences under `alerts` as `{device_id: {enabled, thresholds, notify_charged}}`, and bad values fall back to defaults instead of raising.
- Notifications hold a reference in `_shown` per device, because a garbage-collected notification loses its "Show Details" action, and showing a new one closes that device's previous notification.
- The `desktop-entry` hint is what makes the shell attribute notifications to this app.

**Preferences (`preferences.py`).** JSON at `$XDG_CONFIG_HOME/linux-wireless-manager/config.json`, saved atomically, main thread only. An unreadable file falls back to defaults.

## What rivalcfg can do

Look up per-model details in `venv/lib/python3.12/site-packages/rivalcfg/devices/`; `devices.PROFILES` is keyed by `(vendor_id, product_id)`.

- **Battery:** 22 USB IDs across 12 wireless models (Rival 650, Rival 3 Wireless and Gen 2, Aerox 3/5/9, Prime, Prime Mini, plus special editions). It's the only thing readable over HID. The Rival 650 has no `firmware_version` command.
- **Settings are write-only.** Use `mouse.set_<name>(value)`, then `mouse.save()` to store them on the mouse and in rivalcfg's cache.
  - Setting types vary by model: the Rival 650 has `sensitivity1`/`sensitivity2` (type `range`); the Aerox/Prime/Rival 3 models have a list of DPI steps (`multidpi_range_choice[_xy]`); plus `polling_rate` (`choice`), `sleep_timer` (`range`) and `buttons_mapping` (`buttons`).
  - Wireless-mode profiles reuse the wired profile's settings, with patched commands and a different USB endpoint.
- **The cache file** is `$XDG_CONFIG_HOME/rivalcfg/1038_<product_id>.device.json`. It is only written by `save()`, is keyed per product ID (so wired and wireless have separate files), and `MouseSettings.get()` raises `KeyError` when a setting is missing from an existing file.
- **Debugging:** `RIVALCFG_DRY=1` swaps in a fake HID device and doesn't write the cache file (battery reads then return no data). `RIVALCFG_PROFILE=1038:<pid>` pretends that model is plugged in.
