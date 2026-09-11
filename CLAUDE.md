# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A GTK3 + AppIndicator tray app for Ubuntu that shows the battery level of a SteelSeries Rival 650 Wireless mouse. All USB/HID protocol work is delegated to the [`rivalcfg`](https://github.com/flozz/rivalcfg) library; this repo only contains the polling loop and UI.

## Commands

```bash
./install.sh                          # apt deps, venv (--system-site-packages), rivalcfg, udev rule, .desktop entry
./run.sh --show-window --verbose      # run with detail window open and debug logging
venv/bin/python3 -m steelseries_battery_monitor   # equivalent to run.sh
venv/bin/rivalcfg --list-devices      # check that the mouse is visible to rivalcfg
venv/bin/rivalcfg --battery-level     # raw battery read, bypassing this app
```

There is no test suite, linter config, or build step. Verifying a change means running the app against real hardware (or stubbing `read_battery_status`).

## Dependencies

- **GTK / PyGObject / AppIndicator come from apt, not pip.** The venv is created with `--system-site-packages` so it can see them. `requirements.txt` only lists `rivalcfg` (which pulls in `hidapi`).
- `app.py` tries `AyatanaAppIndicator3` first and falls back to legacy `AppIndicator3`. `main.py` imports GTK and `app` lazily inside `main()` so that missing system bindings produce a friendly apt-install hint instead of a traceback. Keep new GI imports behind that same boundary.

## Architecture

Data flows `config.py` → `battery.py` → `app.py` → `window.py` / indicator.

**Threading model (`app.py`).** HID reads block, so `poll_now()` runs `read_battery_status()` in a daemon thread. The results are sent back to the GTK main thread with `GLib.idle_add(self._apply_status, ...)` or `GLib.idle_add(self._apply_error, ...)`. Any GTK or indicator call must happen on the main thread. `_poll_lock` is acquired non-blocking, so a poll requested while another is in flight is dropped silently.

**Self-rescheduling poll chain.** There is no repeating timer. Each `_apply_status` schedules the next poll after `POLL_INTERVAL_SECONDS` (60s) and each `_apply_error` after `QUICK_RETRY_SECONDS` (10s). Both go through `_schedule_poll()`, which wraps a one-shot `GLib.timeout_add_seconds` and stores the source ID in `self._next_poll_source`. At most one poll is ever pending:
- `_schedule_poll()` removes any pending source before adding the new one.
- `poll_now()` removes the pending source once it holds `_poll_lock`, because the new read's result will schedule the next poll. A manual "Refresh Now" therefore restarts the 60s countdown rather than starting a second chain.
- `_on_poll_timeout()` clears the ID when the timer fires.

If `poll_now()` finds a read already in flight, it returns without touching the timer, because that read will reschedule when its result is applied. This relies on every read ending in `_apply_status` or `_apply_error`, so the worker also routes unexpected exceptions to `_apply_error`, and both methods schedule before touching any UI. All source-ID bookkeeping runs on the main thread. A new poll trigger should call `poll_now()` and never add its own timer. The low polling rate is intentional, because each read wakes the mouse's radio and drains its battery. With `--verbose`, every read logs `Reading battery`, so you can check the cadence.

**Device access (`battery.py`).** It tries each entry in `config.PRODUCT_IDS` in order (wireless dongle `0x1726`, then wired `0x172B`). It opens the device with `rivalcfg.mouse.get_mouse`, reads `mouse.battery`, and **always closes the HID handle**, because holding it open blocks SteelSeries GG and the `rivalcfg` CLI. Every expected failure (device absent, udev permissions, HID I/O, `level is None` meaning the dongle is present but the mouse is off or asleep) is turned into `BatteryReadError`. The UI treats that as "unknown right now", not as a crash.

**Passing data to the UI.** The worker unpacks `BatteryStatus` into primitive arguments for `_apply_status(level, is_charging, connection)`, which then calls `BatteryWindow.update_status(level, is_charging, connection)`. `BatteryStatus.read_at` is currently unused, and the window stamps its own `time.strftime`. Adding a new field means touching all three places: the worker, `_apply_status`, and `update_status`.

**Detail window (`window.py`).** A single `BatteryWindow` instance is created at startup and reused. Closing it hides it (`delete-event` returns `True`) instead of destroying it. It knows nothing about polling; it gets a refresh callback and exposes `update_status` and `update_error`.

**Icons (`icons.py`).** Maps a level to freedesktop `battery-*-symbolic` icon names from the system theme; no icon files are bundled. There is no `empty-charging` icon in most themes, so charging at <15% uses `caution-charging`.

## What rivalcfg can do for this mouse

This matters when extending the app, and is defined in `venv/lib/python3.12/site-packages/rivalcfg/devices/rival650.py`:

- **Readable over HID:** only battery (`level` 0–100, `is_charging`). The Rival 650 profile has no `firmware_version` command, so `mouse.firmware_version` returns `"0"`.
- **Writable but not readable:** `sensitivity1` / `sensitivity2` (DPI 100–12000, step 100), `polling_rate` (125/250/500/1000 Hz), `buttons_mapping`, `sleep_timer` (1–20 min). Use `mouse.set_<name>(value)`, then `mouse.save()` to persist to on-board memory.
- The only record of current settings is rivalcfg's local cache at `$XDG_CONFIG_HOME/rivalcfg/1038_<product_id>.device.json`, written by rivalcfg itself. It is keyed **per product ID**, so wired and wireless modes have separate files.
- rivalcfg exposes no RGB settings for the Rival 650.

## Targeting a different mouse

Change `PRODUCT_IDS` in `config.py` (and the user-facing strings that say "Rival 650"), after confirming with `rivalcfg --list-devices` / `--battery-level` that rivalcfg reports battery for that model.
