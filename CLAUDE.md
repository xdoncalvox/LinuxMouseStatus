# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Linux Wireless Manager is a GTK3 + AppIndicator tray app for Ubuntu that shows the battery of wireless mice and keyboards, and lets you change SteelSeries and Logitech mouse settings (DPI, polling rate, sleep timer, button mapping). It was called "SteelSeries Battery Monitor" (package `steelseries_battery_monitor`) before 2.0.0. There are two device-*discovery* backends:

- SteelSeries mice, through [`rivalcfg`](https://github.com/flozz/rivalcfg) - battery and settings both
- anything UPower reports as a battery-powered mouse or keyboard (Logitech receivers, Bluetooth) - battery only; Logitech settings are layered on separately, through [Solaar](https://pwr-solaar.github.io/Solaar/)'s CLI (`devices/solaar.py`), matched to a UPower device by serial number

No device protocol is implemented here. ROADMAP.md tracks what's built vs. what's only been checked against simulated hardware (dry-run modes, fake D-Bus properties, a reproduction of Solaar's own output format) rather than the real thing - read its per-step notes and the Testing table before assuming something works on hardware just because the code exists.

## Commands

```bash
./install.sh                          # apt deps, venv (--system-site-packages), rivalcfg, udev rule, .desktop entries
./run.sh --show-window --verbose      # run with the window open and debug logging
venv/bin/python3 -m linux_wireless_manager   # equivalent to run.sh
venv/bin/rivalcfg --list-devices      # is the SteelSeries mouse visible to rivalcfg?
venv/bin/rivalcfg --battery-level     # raw SteelSeries battery read, bypassing this app
upower --dump                         # what the UPower backend will see
RIVALCFG_DRY=1 RIVALCFG_PROFILE=1038:1838 ./run.sh --show-window   # pretend an Aerox 3 Wireless is plugged in (its reads fail)
solaar show                           # is a Logitech device paired and does UPower's Serial match Solaar's?
solaar config <serial>                # raw settings listing devices/solaar.py parses - useful when it disagrees with the app
```

There is no test suite, linter config, or build step. To verify a change, run the app against real hardware - and see "Never write to hardware by accident" below before writing any script that might touch a device. To exercise `DeviceMonitor` without hardware:
- **Polling:** patch `monitor.discover_devices` to return fake `Device` subclasses and call `_run_cycle()` directly, or run a `GLib.MainLoop`.
- **`run_device_task()`:** seed `m._devices`/`m._states`/`m._next_read_at` directly with a fake `Device` (skip `_update_registry`), call it, and run a real `GLib.MainLoop` until `on_done` fires - it's delivered via `GLib.idle_add`, so a plain call-and-check without pumping the loop won't see it.
- **The Settings tab, without a device backend:** build `MainWindow` with stub `on_load_settings`/`on_apply_settings`/`on_reset_settings` callbacks that call back synchronously with hand-built `SettingField`/`SettingsValues` objects - the window's own code is synchronous, only the real `app.py` wiring is async. This does *not* exercise real mouse/keyboard-focus interaction, though - it builds widgets and reads getters straight back, so it wouldn't have caught the scroll-wheel bug above; for that class of bug, a real Apply's actual effect (e.g. the saved cache file) is the more trustworthy signal.
- **A screenshot without popping a window on the user's screen:** build the widget, `win.remove(child); Gtk.OffscreenWindow().add(child)`, pump `Gtk.main_iteration()` a few dozen times, `off.get_pixbuf().savev(path, "png", [], [])`.
- **Reading a dependency's real behaviour without installing it:** `pip download --no-deps --no-binary :all: -d <dir> <package>` fetches the sdist tarball without building/installing - extract it and read the source directly. Used for `devices/solaar.py` below when `solaar` itself couldn't be installed (needs system dbus dev headers this venv doesn't have).

## Never write to hardware by accident

A one-off test script that calls `rivalcfg.mouse.get_mouse()` (or anything that reaches `apply_settings()`/`reset_settings()`) without `RIVALCFG_DRY=1` is exactly as real as a write from the running app - if the product ID it asks for happens to be plugged in, it opens the *real* device and can call `.save()` on it. This has already happened twice in this project's history, from two different scripts. `RIVALCFG_DRY=1` prevents it categorically: `usbhid.open_device()` always constructs a `FakeDevice` under it, even when a real device with the same product ID is found by `hid.enumerate()`. There is no equivalent guard for `devices/solaar.py` (it always calls the real `solaar` binary) - there's simply no dry-run flag to reach for there, so double-check a value before passing it to `apply_settings`/`solaar.apply_settings` at all.

## Dependencies

- **GTK / PyGObject / AppIndicator come from apt, not pip.** The venv is created with `--system-site-packages` so it can see them. `requirements.txt` only lists `rivalcfg` (which pulls in `hidapi`, imported as `hid`).
- The UPower backend uses Gio's D-Bus API directly, so it needs no extra package, only the `upower` daemon.
- Notifications use libnotify (`gir1.2-notify-0.7`). If the bindings are missing the app still runs; it just doesn't notify, and the Alerts tab says so.
- `tray.py` tries `AyatanaAppIndicator3` first and falls back to legacy `AppIndicator3`. `main.py` imports GTK, then `devices` (rivalcfg/hidapi), then `app` lazily inside `main()`, so a missing dependency produces a specific install hint instead of a traceback. Keep new GI imports behind that same boundary.
- `main.py` calls `GLib.set_prgname(config.APP_ID)` / `set_application_name(config.APP_NAME)` right after importing GTK, before any window exists. Running as `python3 -m linux_wireless_manager` otherwise leaves the window's WM_CLASS as `__main__.py` (GLib/GTK default it from `argv[0]`'s basename), which is what a taskbar or dock shows on hover instead of the app's name. The `.desktop` file's `StartupWMClass` matches the same app ID.

## Architecture

Data flows from the device backends (`devices/steelseries.py`, `devices/upower.py`) to `monitor.DeviceMonitor`, which runs them in a worker thread. The monitor sends a list of `DeviceState` snapshots to `app.WirelessManagerApp._on_devices_updated`, which passes them to both tray icons (`tray.KindIndicator`) and the window (`window.MainWindow`).

**Device interface (`devices/base.py`).**
- `Device` objects only ever live in the polling thread. The UI only sees frozen `DeviceState` snapshots, so GTK code never touches a device and device code never touches GTK.
- `BatteryStatus` carries either a `percentage` or a `coarse_level` (UPower devices that only report Low/Normal/…). Use `estimated_percentage` and `describe()` rather than reading `percentage` directly.
- Expected failures raise `BatteryReadError(detail, user_message)`: `detail` goes to the log, and `user_message` is shown in the window.
- `hardware_key` changing for the same `id` makes the monitor replace the object and re-read it immediately. This is how a SteelSeries mouse switching from receiver to cable is handled.
- The settings methods (`settings_schema`, `get_settings`, `apply_settings`, `reset_settings`) are implemented by `devices/steelseries.py` (ROADMAP Step 2) and `devices/upower.py` (ROADMAP Step 3b, Logitech devices only, delegating to `devices/solaar.py`); the base-class versions in `devices/base.py` are the "no settings" stub every other device (Bluetooth, non-Logitech UPower devices) still uses.

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
- **Settings** (`settings_schema`/`get_settings`/`apply_settings`/`reset_settings`) are built from `self._active_product_id()`'s profile - the ID for the connection currently in use, so wired and wireless modes read/write their own separate rivalcfg cache files, as CLAUDE.md's "What rivalcfg can do" section describes.
  - `settings_schema()` only ever exposes setting names in `_SUPPORTED_SETTINGS` (`sensitivity`/`sensitivity1`/`sensitivity2`, `polling_rate`, `sleep_timer`, `buttons_mapping`), by name rather than `value_type`, since some profiles reuse a supported `value_type` (e.g. `choice`) for an out-of-scope setting (`default_lighting`). This one allowlist ends up covering every rivalcfg model's DPI representation (`range`, `range_choice`, `multidpi_range`, `multidpi_range_choice`, `multidpi_range_choice_xy`).
  - `get_settings()` reads the JSON cache file itself (`_read_cache_file`/`_normalize_saved_value`) instead of going through `MouseSettings.get()`: that method's own fallback for a missing key is the profile's *raw* default (a DSL string for buttons, a comma-separated string for multi-DPI), not the typed value (`dict`, `list[int]`/`list[tuple]`) `settings_schema()`'s field promises. A value missing from the file falls back per-field to the field's own (already-typed) `default`.
  - `apply_settings()` passes a button mapping as `{"buttons": {button_lower: action_lower}}` directly - rivalcfg's `buttons` handler accepts that dict shape as an alternative to the `buttons(...)` DSL string - and refuses to disable whichever button is named `Button1` in the profile (`ButtonsSetting.primary_button`), since that would leave the mouse unclickable.
  - `reset_settings()` calls `mouse.reset_settings()` as-is, which resets *every* setting in the profile (lighting included), not just the ones our schema exposes - matching what rivalcfg itself means by a full reset.

**UPower backend (`devices/upower.py`).**
- It makes synchronous Gio D-Bus calls from the worker thread and reads UPower's cached properties, so it never wakes a device.
- Numeric codes are UPower's `UpDeviceKind` (5 = mouse, 6 = keyboard), `UpDeviceState` and `UpDeviceLevel` enums.
- It skips `PowerSupply=true` batteries (the laptop's own) and `IsPresent=false` devices.
- It hasn't been tested with a real Logitech or Bluetooth device yet. The `IsPresent` filter and Bluetooth detection (`/org/bluez/...` or `hid-<MAC>-battery` native paths) are unverified assumptions.
- **Settings** (Step 3b, Logitech only) just delegate to `solaar.py`, passing `self._serial` (from UPower's `Serial` property, captured in `__init__`). A device UPower reports no serial for - most Bluetooth devices, or anything Solaar doesn't manage - gets an empty `settings_schema()`, same as before Step 3b existed. `reset_settings()` always raises `SettingsWriteError`: Solaar's CLI has no reset-to-factory action.

**Logitech settings (`devices/solaar.py`).** Shells out to the `solaar` CLI rather than reimplementing HID++ - unlike SteelSeries, these settings *can* be read back from the device, so there's no cache-file concept to worry about.
- Covers `dpi`, `pointer_speed`, `report_rate`, `report_rate_extended` - Solaar's own machine names for Sensitivity (DPI), Sensitivity (Pointer Speed), and Report Rate (two HID++ features, older vs. extended). `dpi_extended` (independent X/Y/LOD DPI) is deliberately left out - it needs a value per axis/key, which doesn't fit this app's simple-value settings widgets yet.
- `solaar config <serial>` (no setting name) lists every setting the device actually has, each as a `#`-commented label/description/choices block followed by `<name> = <value>` (or `= ? (failed to read from device)`); `_parse_listing()` parses that. `solaar config <serial> <setting> <value>` applies a change.
- **Written from Solaar 1.1.20's own source, not from running it or against real hardware** - there's no `solaar` binary or Logitech wireless device in this environment (`pip download --no-deps --no-binary :all: solaar` gets the source without needing to build/install it, e.g. into a scratch venv, and is a good way to check this against a newer Solaar release later). Tested against a faithful reproduction of `lib/solaar/cli/config.py`'s exact print statements, not the real CLI. Treat any assumption here as unverified until checked against the real tool and a real device: the output format, the exact setting names, and - especially - whether UPower's `Serial` property actually matches what Solaar calls a device's serial (the device-matching scheme depends entirely on that).
- Every function fails soft: a missing `solaar` binary, an unmatched serial, a nonzero exit code, or unparseable output all become an empty `settings_schema()` or a `SettingsWriteError` with a plain-language `user_message`, never a crash.

**Tray (`tray.py`).**
- There is one `KindIndicator` per `DeviceKind`. The mouse icon is always visible, so the app stays reachable. The keyboard icon only shows while a battery-powered keyboard is connected.
- Each icon shows the lowest battery among its readable devices, and its menu lists all of them.
- Devices with `has_battery=False` are never passed to the tray.
- AppIndicator has no plain click/double-click signal - every click opens the menu set via `set_menu()`. `set_secondary_activate_target(show_details_item)` is what makes Ubuntu's top bar open the window on a double-click instead.

**Icons (`icons.py`).**
- `install()` generates the SVGs (mouse/keyboard × 6 level steps × charging, plus "unknown") into `~/.cache/linux-wireless-manager/icons/` at startup.
- The tray loads them through `Indicator.new_with_path`, and the window through `Gtk.IconTheme.append_search_path`.
- Draw with **fills only**. GTK recolours symbolic icons by overriding `fill` (plain shapes become the panel's foreground colour, `class="error"` becomes red, `class="success"` green), so strokes wouldn't follow the theme.
- If the icons can't be written, the themed `input-mouse-symbolic` / `input-keyboard-symbolic` icons are used.

**Window (`window.py`).**
- A single `MainWindow` is created at startup and hidden, never destroyed, on close.
- The left side is a `Gtk.ListBox` of devices. The right side is a `Gtk.Stack` with Status / Settings / Alerts tabs.
- The Settings tab (ROADMAP Step 2) is built from a device's `SettingField` list (`devices/base.py`: `RangeSetting`, `ChoiceSetting`, `DpiPresetsSetting`, `ButtonsSetting`) - one widget group per field, an Apply and a Reset to Defaults button. Like Alerts, it's only re-filled when the shown device changes (`_settings_device_id`), but the fill itself is async: `_populate_settings()` calls `on_load_settings(device_id, callback)` and shows a loading state until the callback lands. Every callback closes over the `device_id` it was issued for and checks it against `self._settings_device_id` before touching the UI, since the user may have switched devices while it was in flight.
- **Every settings widget goes through `_no_scroll()`.** GTK spin buttons and combo boxes change their value on a mouse-wheel scroll even while unfocused - and since the whole form sits in a `Gtk.ScrolledWindow`, scrolling *past* a widget to reach another field used to silently change it too. This is not hypothetical: on the very first real-hardware use of the Settings tab, scrolling past the `Button1` dropdown to reach the DPI field silently remapped it to a media key (confirmed by reading rivalcfg's cache file afterward - every other field was correct, only that one dropdown was wrong). `_no_scroll()` connects `"scroll-event"` and swallows it (`_ignore_unfocused_scroll`) unless the widget already has focus. Apply it to any new spin button or combo box added here.
- `window.py` never imports a device backend or touches a `Device`. The three callbacks it's given (`on_load_settings` / `on_apply_settings` / `on_reset_settings`, wired in `app.py`) all go through `DeviceMonitor.run_device_task(device_id, task, on_done, refresh_battery=...)`, which runs `task(device)` in the worker thread under `_poll_lock` (so it can't overlap a battery read) and calls `on_done(result, error)` on the main thread. Pass `refresh_battery=True` for a task that opens the device (a write), so the freshly-current battery reading comes back with it; leave it `False` for a settings read, which only touches rivalcfg's cache file and shouldn't wake the mouse's radio.
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

## What Solaar can do

Unverified against the real tool - see `devices/solaar.py`'s docstring and the Logitech settings bullet above. Ground truth is Solaar 1.1.20's source, downloaded (without installing) with:

```bash
pip download --no-deps --no-binary :all: -d /tmp/solaar_src solaar
tar xzf /tmp/solaar_src/solaar-*.tar.gz -C /tmp/solaar_src
```

- `lib/solaar/cli/config.py` is the whole `solaar config` implementation: listing (`run()`'s `if not args.setting` branch, `_print_setting()`) and setting a value (`set()`, dispatched by `settings.Kind`).
- `lib/logitech_receiver/settings_templates.py`'s `SETTINGS` list is every setting class Solaar knows; `AdjustableDpi`/`ExtendedAdjustableDpi`/`PointerSpeed`/`ReportRate`/`ExtendedReportRate` are the ones this app uses, each with its machine `name`, `label`, and `settings.Kind` (`CHOICE` for a fixed list, `RANGE` for a bounded integer - `_print_setting()` only prints a "possible values" line for `CHOICE`/`TOGGLE`, so a `RANGE` setting's bounds have to come from its class's `min_value`/`max_value` in this file instead, hardcoded into `devices/solaar.py`).
- `lib/solaar/cli/__init__.py`'s `_find_device()` matches the CLI's `device` argument by exact lowercased serial, exact codename, exact kind, or substring of the name - passing a serial number (what `devices/solaar.py` does) is the only exact, unambiguous match.
- No reset-to-factory-defaults action exists in the CLI (only `show`/`config`/`pair`/`unpair`/`profiles`/`probe`).
