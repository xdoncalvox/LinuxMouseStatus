# Roadmap

Turns the SteelSeries Rival 650 battery monitor into **Linux Wireless Manager**, a battery and settings manager for wireless mice and keyboards on Linux. There are three tracks: **low-battery alerts**, **mouse settings**, and **more devices** (SteelSeries and Logitech). Step 0 is groundwork that all three depend on.

**Status:** All planned steps (Prerequisites, 0, 1, 2, 3a, 3b) are done (Step 2 and Step 3 on 2026-09-14). What's left is hardware verification this environment can't do itself - see each step's notes and the Testing table - plus whatever the "Out of scope for now" list below turns into next.

## Decisions

| Topic | Decision |
|---|---|
| App name | **Linux Wireless Manager** (app ID `linux-wireless-manager`, package `linux_wireless_manager`). |
| Tray | **One icon per device type:** a small mouse icon for the connected wireless mouse, plus a keyboard icon only when a wireless keyboard is connected. The mouse shape avoids confusion with the laptop battery icon. |
| SteelSeries | **Mice only**, through `rivalcfg`. Headsets (e.g. Arctis Nova Pro Wireless) are out of scope. |
| Logitech | **Mice and keyboards.** Battery comes from UPower, which also covers Bluetooth mice and keyboards; settings come from Solaar (Step 3b). |
| Settings, first version | Sleep timer, DPI, polling rate, button remapping. |

## Known constraints

These come from reading `rivalcfg` 4.17 and checking this machine (Ubuntu 24.04, UPower 1.90.3).

- **rivalcfg reports battery for 22 USB IDs across 12 wireless SteelSeries models:** Rival 650, Rival 3 Wireless and Rival 3 Wireless Gen 2, Aerox 3, 5 and 9 Wireless, Prime Wireless, and Prime Mini Wireless, plus special editions. Most models have one ID for the wired connection and one for the wireless one.
- **SteelSeries settings are write-only.** There is no way to read DPI, polling rate and so on back from the mouse. The only record is `rivalcfg`'s own file, `~/.config/rivalcfg/1038_<product_id>.device.json`, which has these problems:
  - It is only written when `mouse.save()` is called. That call also stores the setting in the mouse's own memory; without it, a change is lost when the mouse powers off.
  - If the file exists but is missing a setting, `MouseSettings.get()` raises `KeyError` instead of returning the default.
  - Wired and wireless modes use different product IDs, so each has its own file.
  - It goes stale if settings are changed elsewhere, e.g. in SteelSeries GG on Windows.
- **Settings are described differently per model.** The Rival 650 uses two fixed presets (`sensitivity1` and `sensitivity2`, type `range`). Aerox, Prime and Rival 3 use a list of DPI steps (`multidpi_range_choice`, or `multidpi_range_choice_xy` on the Rival 3 Gen 2). The settings UI therefore has to be built from each model's `rivalcfg` description by `value_type`.
- **The Rival 650 has no firmware-version command, and `rivalcfg` exposes no lighting settings for it.**
- **Logitech battery doesn't need Solaar.** The kernel's `hid-logitech-hidpp` driver reports battery-powered Logitech devices, and UPower picks them up. Some devices only report a coarse level (Critical/Low/Normal/High/Full), not a percentage.
- **Solaar 1.1.11 is in Ubuntu's repos** (`apt install solaar`). Its Python modules are internal and aren't on the virtualenv's path, so the plan is to call its CLI (`solaar show`, `solaar config`).
- **The themes only have plain device icons.** Adwaita and Yaru include `input-mouse-symbolic` and `input-keyboard-symbolic`, but nothing that combines a device shape with a battery level.

## Prerequisites

- [x] Duplicate-polling fix merged (`2e7f963`).
- [x] `.gitignore` added, and `venv/` and `__pycache__/` removed from git tracking (`ebce1a7`).

## Step 0 — Groundwork (done)

- [x] **Renamed the app** to Linux Wireless Manager: the package, app ID, `.desktop.in`, `run.sh`, `install.sh` and README. `install.sh` removes the old `steelseries-battery-monitor.desktop` menu and autostart entries, and keeps autostart on if it was on before.
- [x] **Device interface** in `devices/base.py`:
  - `Device` provides `id`, `name`, `kind`, `connection`, `has_battery`, `hardware_key`, per-backend poll and retry intervals, `read_battery()`, and settings hooks (`settings_schema()`, `get_settings()`, `apply_settings()`, `reset_settings()`; implemented by Step 2).
  - `BatteryStatus` holds a percentage *or* a coarse level. `DeviceState` is the frozen snapshot the UI gets.
- [x] **Detection** replaces the hardcoded product IDs:
  - SteelSeries: `hid.enumerate(0x1038)` matched against `rivalcfg`'s profiles and grouped into one device per physical mouse. It never opens a device.
  - UPower: mice and keyboards over D-Bus, excluding the laptop battery.
  - The window now tells "no supported devices found" apart from "receiver plugged in, but the mouse didn't respond".
- [x] **Polling loop** (`monitor.py`) covering all devices:
  - Detection runs every 10 s.
  - SteelSeries devices are read every 60 s, retrying after 10 s; UPower devices every 30 s.
  - There is still at most one pending timer, and reads happen one at a time.
- [x] **App preferences file** at `~/.config/linux-wireless-manager/config.json`. It currently remembers the selected device.
- [x] **Tray icons:**
  - A mouse icon that is always shown, and a keyboard icon only while a battery-powered keyboard is connected.
  - Each shows the lowest battery among its devices, and its menu lists them all.
  - The SVGs (mouse or keyboard outline, level in 20% steps, charging bolt, unknown) are generated into `~/.cache/linux-wireless-manager/icons/`, with the theme icons as fallback.
  - Checked in the GNOME top bar.
- [x] **Window rewrite:** a device list on the left and **Status / Settings / Alerts** tabs. Alerts was filled in by Step 1, Settings by Step 2.
- Moved to Step 3b: checking which Logitech devices Solaar can see (only needed for settings).

## Step 1 — Low-battery alerts (done)

- [x] Desktop notifications through libnotify, added to `install.sh` as `gir1.2-notify-0.7`. If the bindings are missing, the app runs without notifications and the Alerts tab says so.
- [x] Default alert levels are 20% and 10%, set per device in the Alerts tab, which also has an off switch and a "fully charged" switch. Coarse levels need no special case: "Low" counts as 20% and "Critical" as 5%.
- [x] Each alert fires once and re-arms only after the device charges or climbs 5 points above the threshold. Crossing both levels at once notifies once, about the lower one.
- [x] "Fully charged" notification, which stays quiet for a device that is already full when the app starts.
- [x] Clicking "Show Details" on a notification opens the window on that device. GNOME Shell reports the `actions` capability, and the notification is kept referenced so the button keeps working.
- Settings are stored per device in `~/.config/linux-wireless-manager/config.json` under `alerts`.

## Step 2 — Mouse settings (SteelSeries via rivalcfg) (done)

- [x] **Settings schema in `devices/base.py`:** `RangeSetting`, `ChoiceSetting`, `DpiPresetsSetting` and `ButtonsSetting` describe a field without either side knowing about rivalcfg or GTK. `devices/steelseries.py` builds these from the model's profile, filtered to an allowlist of setting names (`sensitivity`/`sensitivity1`/`sensitivity2`, `polling_rate`, `sleep_timer`, `buttons_mapping`) so lighting and other out-of-scope settings never surface, whatever their `value_type`. This ended up covering every DPI-ish `value_type` rivalcfg uses (`range`, `range_choice`, `multidpi_range`, `multidpi_range_choice`, `multidpi_range_choice_xy`), not just the Rival 650's and Aerox's, so Step 3a's settings needed no extra work.
- [x] **The Settings tab (`window.py`)** builds one widget group per field: a `Gtk.SpinButton` for `RangeSetting`, a `Gtk.ComboBoxText` for `ChoiceSetting`, an editable add/remove list of spin buttons (pairs of them for X/Y-independent models) for `DpiPresetsSetting`, and one dropdown per button for `ButtonsSetting`.
- [x] **Button remapping**, with each dropdown offering, in order: the model's special actions (`disabled`, `dpi`, `ScrollUp`/`ScrollDown` - only the ones the profile actually has), its other mouse buttons, `layout_multimedia`'s keys, then `layout_qwerty`'s (only when the profile supports keyboard remapping). `apply_settings()` passes a plain `{"buttons": {...}}` dict straight to rivalcfg's handler (it accepts that as an alternative to the `buttons(...)` DSL string), and refuses to save if the button named `Button1` would end up `disabled`.
- [x] **Applying a change** goes through `DeviceMonitor.run_device_task()` (new in `monitor.py`): it takes `_poll_lock` *blocking*, so it can't overlap a battery read but is never silently dropped either, runs `open → set_<name>() per field → save() → close` in the worker thread, and hands the window a fresh `SettingsValues` (or the error) back on the main thread. `window.py` never imports rivalcfg or touches a `Device`.
- [x] **Saved values, labelled by where they came from:** `get_settings()` reads rivalcfg's own cache file directly (bypassing `MouseSettings.get()`, whose defaults-on-miss are raw profile strings, not the typed values `settings_schema()` promises) and reports `from_cache=True` only when every field was actually found in it; the Settings tab shows a note either way. A stale/partial file (or `RIVALCFG_DRY=1`, which never writes one) falls back per-field to the schema's own default.
- [x] **Reset to defaults** button, using `mouse.reset_settings()` (every setting in the profile, not just the ones in our schema - matches how rivalcfg itself defines a full reset) followed by `save()`.
- [x] Tested with `RIVALCFG_DRY=1` against the Rival 650 (range/choice/buttons) and the Aerox 3 Wireless (multidpi_range_choice/choice/buttons), plus simulated cache files exercising the from-cache/partial-cache/reset paths, a `DeviceMonitor.run_device_task()` round trip with a fake `Device`, and an offscreen render of the built form.
- [x] **Exercised against the real Rival 650's Settings tab:** DPI, polling rate and sleep timer applied correctly. Button remapping did not, at first - a GTK footgun (spin buttons/combo boxes change value on a mouse-wheel scroll even while unfocused, and the form scrolls) silently remapped `Button1` to a media key when the user scrolled past it to reach the DPI field. Fixed (`window._no_scroll`, see CLAUDE.md's Window section) and the mouse's mapping corrected by hand afterward. **Still only `RIVALCFG_DRY`-tested:** Reset to Defaults, the "can't disable Button1" refusal, and re-confirming button remapping works correctly now that `_no_scroll` exists.

## Step 3a — The other SteelSeries mice (done)

- [x] Detection and battery for all battery-capable `rivalcfg` models. This came with Step 0, but only the Rival 650 has been tested on hardware.
- [x] Settings for them: Step 2's schema builder and widgets are generic across every rivalcfg mouse profile, wireless-mode models included (each has its own `product_id`, so its own settings cache file - `SteelSeriesMouse._active_product_id()` picks the one matching the current connection). Verified on the real Rival 650 (see Step 2's own line above); every other model only with `RIVALCFG_DRY=1 RIVALCFG_PROFILE=1038:1838` (Aerox 3 Wireless) - nobody owns a second SteelSeries mouse to check against real hardware.

## Step 3b — Logitech mice and keyboards (done)

- [x] **Battery through UPower**, showing both percentages and coarse levels. This also drives the keyboard tray icon. Built in Step 0 and still only tested with fake UPower data - no wireless Logitech device has been available in any session so far.
- [x] **Settings through the Solaar CLI** (`devices/solaar.py`): `solaar config <device>` lists a paired device's settings with their current values, parsed into this app's usual `SettingField`/`SettingsValues` shapes; `solaar config <device> <setting> <value>` applies a change. Covers `dpi`, `pointer_speed`, `report_rate` and `report_rate_extended` (`dpi_extended`, the X/Y/LOD-capable DPI feature some mice have, needs a value per axis and is left for later). `UPowerDevice` (`devices/upower.py`) matches itself to a Solaar device using the same serial number UPower reports.
  - **This was written from Solaar 1.1.20's own source** (`lib/solaar/cli/config.py`, `lib/logitech_receiver/settings_templates.py`, downloaded via `pip download --no-deps --no-binary :all: solaar` - no build step needed just to read it), not from running the tool, and tested against a faithful line-for-line reproduction of its `_print_setting()` output - **not against the real `solaar` binary or a real Logitech device**, neither of which is available in this environment (no passwordless sudo to `apt install solaar`; no wireless Logitech mouse owned). Every call fails soft (empty settings, or a `SettingsWriteError` with a plain message) if the binary is missing, the device isn't paired, or the output doesn't parse - but the untested parts are: whether UPower's `Serial` property actually matches what Solaar calls a device's serial (the whole matching scheme depends on it), and whether solaar 1.1.20's real output byte-for-byte matches what its source implies.
  - No reset-to-defaults: Solaar's CLI doesn't have one (only `show`/`config`/`pair`/`unpair`/`profiles`), so `reset_settings()` always raises a clear `SettingsWriteError` instead of pretending to support it.
- [ ] Check whether the Solaar CLI works while Solaar's own tray app is running, and whether the two conflict over the device. Needs the real tool and a real device - couldn't be checked.
- [x] Added `solaar` to `install.sh`, as an optional interactive install (skipped automatically, with a warning, if it's not in the apt repos).

## Testing

| Hardware / mode | Covers |
|---|---|
| **Rival 650 Wireless** (available) | SteelSeries detection and battery (verified in Step 0: 78% through the receiver), alerts, its Settings tab (verified in Step 2, including a real bug found and fixed - see CLAUDE.md), the mouse tray icon |
| **Logitech G502 HERO, wired** (expected later, not yet available) | Logitech detection and a wired device with no battery (no tray icon). **Not** Logitech battery, alerts, or Solaar settings, since it has no battery and Solaar's DPI/report-rate settings target wireless-capable HID++ features this wired-only model may not have anyway. Check its USB ID with `lsusb` (expected `046d:c08b`). |
| `RIVALCFG_DRY=1` | Fake HID device and no writes to `rivalcfg`'s settings file, so settings can be changed without touching the mouse. It returns no battery reading. |
| `RIVALCFG_DRY=1 RIVALCFG_PROFILE=1038:<pid>` | Shows another model in the window, including its Settings tab, e.g. an Aerox 3 (`1838`), without owning one or writing to rivalcfg's cache file. Detection honours this variable. |
| Fake `Device` subclasses or fake UPower properties | The monitor's scheduling and the UPower parsing, as done for Step 0. Covers Logitech wireless battery and the keyboard tray icon until a wireless Logitech device is available. |
| A hand-built reproduction of Solaar's `_print_setting()` output (see Step 3b) | `devices/solaar.py`'s output parsing and settings round-trip, without the `solaar` binary or a Logitech device. Does **not** cover the real CLI's actual output or real UPower/Solaar serial-number matching. |

## Out of scope for now

- Headsets (SteelSeries Arctis, Logitech G), which would need HeadsetControl built from source
- RGB lighting, and button remapping on Logitech devices
- SteelSeries keyboards (the Apex Pro here is wired, and `rivalcfg` only supports mice)
- `dpi_extended`, the independent X/Y (and lift-off distance) DPI setting some newer Logitech mice expose instead of plain `dpi` - it needs a value per axis, not one, which `devices/base.py`'s `DpiPresetsSetting` doesn't model for a *live-read* setting the way SteelSeries's preset list does (see `devices/solaar.py`)
