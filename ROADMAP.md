# Roadmap

Turns the SteelSeries Rival 650 battery monitor into **Linux Wireless Manager**, a battery and settings manager for wireless mice and keyboards on Linux. There are three tracks: **low-battery alerts**, **mouse settings**, and **more devices** (SteelSeries and Logitech). Step 0 is groundwork that all three depend on.

**Status:** Prerequisites, Step 0 and Step 1 are done (Step 1 on 2026-09-14). Next is Step 2.

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
  - `Device` provides `id`, `name`, `kind`, `connection`, `has_battery`, `hardware_key`, per-backend poll and retry intervals, `read_battery()`, and settings stubs for Step 2.
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
- [x] **Window rewrite:** a device list on the left and **Status / Settings / Alerts** tabs. Alerts was filled in by Step 1; Settings waits for Step 2.
- Moved to Step 3b: checking which Logitech devices Solaar can see (only needed for settings).

## Step 1 — Low-battery alerts (done)

- [x] Desktop notifications through libnotify, added to `install.sh` as `gir1.2-notify-0.7`. If the bindings are missing, the app runs without notifications and the Alerts tab says so.
- [x] Default alert levels are 20% and 10%, set per device in the Alerts tab, which also has an off switch and a "fully charged" switch. Coarse levels need no special case: "Low" counts as 20% and "Critical" as 5%.
- [x] Each alert fires once and re-arms only after the device charges or climbs 5 points above the threshold. Crossing both levels at once notifies once, about the lower one.
- [x] "Fully charged" notification, which stays quiet for a device that is already full when the app starts.
- [x] Clicking "Show Details" on a notification opens the window on that device. GNOME Shell reports the `actions` capability, and the notification is kept referenced so the button keeps working.
- Settings are stored per device in `~/.config/linux-wireless-manager/config.json` under `alerts`.

## Step 2 — Mouse settings (SteelSeries via rivalcfg)

- [ ] **Build the Settings tab from the model's `rivalcfg` description**, choosing a widget by `value_type`:
  - `range`: a number field (sleep timer; Rival 650 DPI presets 1 and 2)
  - `multidpi_range_choice(_xy)`: an editable list of DPI steps
  - `choice`: a dropdown (polling rate: 125, 250, 500 or 1,000 Hz)
  - `buttons`: one dropdown per button
- [ ] **Button remapping**, with options grouped as:
  - special actions: `disabled`, `dpi`, `ScrollUp`, `ScrollDown`
  - mouse buttons
  - media keys (`layout_multimedia`)
  - keyboard keys (`layout_qwerty`)

  Also: produce the `buttons(...; layout=qwerty)` string `rivalcfg` expects, and refuse to leave `button1` unassigned.
- [ ] **Applying a change** goes through the monitor's worker thread and lock, like battery reads: open the mouse, `set_<name>()`, `save()`, close.
- [ ] **Show saved values labelled "last set by this app".** Fall back to the model's defaults on `KeyError` or when there's no file. Choose between the wired-mode and wireless-mode files.
- [ ] **Reset to defaults** button, using `mouse.reset_settings()` followed by `save()`.

## Step 3a — The other SteelSeries mice

- [x] Detection and battery for all battery-capable `rivalcfg` models. This came with Step 0, but only the Rival 650 has been tested on hardware.
- [ ] Settings for them, once Step 2 is done. Wireless-mode models reuse the wired model's settings, with a patched command and a different USB endpoint. Make sure the right one is opened.

## Step 3b — Logitech mice and keyboards

- [x] **Battery through UPower**, showing both percentages and coarse levels. This also drives the keyboard tray icon. Built in Step 0 and tested only with fake UPower data.
- [ ] Verify it with a real wireless Logitech device, in particular the `IsPresent` filter and how a powered-off device shows up.
- [ ] **Settings through the Solaar CLI:**
  - Read the list of devices and settings with `solaar show`, then read and change values with `solaar config <device> <setting> [value]`.
  - Unlike SteelSeries, Logitech settings can be read back from the device.
  - Start with DPI and polling rate.
- [ ] Check whether the Solaar CLI works while Solaar's own tray app is running, and whether the two conflict over the device.
- [ ] Add `solaar` to `install.sh`, as optional.

## Testing

| Hardware / mode | Covers |
|---|---|
| **Rival 650 Wireless** (available) | SteelSeries detection and battery (verified in Step 0: 78% through the receiver), alerts, all Step 2 settings, the mouse tray icon |
| **Logitech G502 HERO, wired** (available later) | Logitech detection and settings through Solaar, and a wired device with no battery (no tray icon). **Not** Logitech battery or alerts, since it has no battery. Check its USB ID with `lsusb` (expected `046d:c08b`). If Solaar doesn't support it wired, consider `ratbagd` (apt `ratbagd` 0.17). |
| `RIVALCFG_DRY=1` | Fake HID device and no writes to `rivalcfg`'s settings file, so settings can be changed without touching the mouse. It returns no battery reading. |
| `RIVALCFG_DRY=1 RIVALCFG_PROFILE=1038:<pid>` | Shows another model in the window, and its Settings tab once Step 2 exists, e.g. an Aerox 3 (`1838`), without owning one. Detection honours this variable. |
| Fake `Device` subclasses or fake UPower properties | The monitor's scheduling and the UPower parsing, as done for Step 0. Covers Logitech wireless battery and the keyboard tray icon until a wireless Logitech device is available. |

## Out of scope for now

- Headsets (SteelSeries Arctis, Logitech G), which would need HeadsetControl built from source
- RGB lighting, and button remapping on Logitech devices
- SteelSeries keyboards (the Apex Pro here is wired, and `rivalcfg` only supports mice)
