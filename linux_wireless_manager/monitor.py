"""Polls every detected device from a background thread and hands immutable
DeviceState snapshots to the UI on the GTK main thread.

Scheduling rules (keep them when changing this file):

- There is at most one pending GLib timeout. Its ID is kept in
  _next_tick_source, which is only touched on the main thread.
  _schedule_tick() replaces any pending timeout, and _start_cycle() cancels it
  because the cycle it starts schedules the next tick when its result is
  applied. A manual refresh therefore restarts the countdown instead of
  starting a second chain.
- Every cycle ends in _apply(), even after an unexpected exception, and
  _apply() schedules the next tick before calling into the UI. Otherwise
  polling would stop.
- Each tick re-runs detection, which is cheap and never talks to a device,
  but only reads the battery of devices whose own interval has passed. An
  asleep mouse retries on its own schedule without making healthy devices
  poll faster.
- Battery reads happen one at a time in the worker thread. _poll_lock is
  taken non-blocking, so a request that arrives while a cycle is running is
  dropped; that cycle will schedule the next tick anyway.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import threading
import time
from typing import Callable

from gi.repository import GLib

from . import config
from .devices import (
    BatteryReadError,
    Device,
    DeviceKind,
    DeviceState,
    discover_devices,
)

logger = logging.getLogger(config.APP_ID)


class DeviceMonitor:
    def __init__(self, on_update: Callable[[list[DeviceState]], None]):
        self._on_update = on_update  # called on the main thread
        self._poll_lock = threading.Lock()
        self._next_tick_source: int | None = None
        # Worker-thread state, only touched while holding _poll_lock.
        self._devices: dict[str, Device] = {}
        self._states: dict[str, DeviceState] = {}
        self._next_read_at: dict[str, float] = {}
        self._failures: dict[str, int] = {}

    def start(self) -> None:
        GLib.idle_add(self._start_cycle, False)

    def refresh_now(self) -> None:
        """Re-detect devices and read every battery now. Main thread only."""
        self._start_cycle(True)

    # -- Main thread ----------------------------------------------------

    def _start_cycle(self, force_read: bool) -> bool:
        if not self._poll_lock.acquire(blocking=False):
            logger.debug("Poll already in flight; ignoring request")
            return False
        self._cancel_tick()
        threading.Thread(target=self._worker, args=(force_read,), daemon=True).start()
        return False  # also used as a one-shot GLib.idle_add callback

    def _apply(self, states: list[DeviceState], delay: float) -> bool:
        self._schedule_tick(delay)
        self._on_update(states)
        return False

    def _schedule_tick(self, delay: float) -> None:
        self._cancel_tick()
        self._next_tick_source = GLib.timeout_add_seconds(
            max(1, math.ceil(delay)), self._on_tick
        )

    def _cancel_tick(self) -> None:
        if self._next_tick_source is not None:
            GLib.source_remove(self._next_tick_source)
            self._next_tick_source = None

    def _on_tick(self) -> bool:
        # The source is finished once this returns False; forget its ID
        # first so _start_cycle() doesn't try to remove it.
        self._next_tick_source = None
        self._start_cycle(False)
        return False

    # -- Worker thread --------------------------------------------------

    def _worker(self, force_read: bool) -> None:
        try:
            try:
                states, delay = self._run_cycle(force_read)
            except Exception:  # noqa: BLE001 - must not stop polling
                logger.exception("Unexpected error while polling devices")
                states, delay = self._snapshot(), config.DISCOVERY_INTERVAL_SECONDS
        finally:
            self._poll_lock.release()
        GLib.idle_add(self._apply, states, delay)

    def _run_cycle(self, force_read: bool) -> tuple[list[DeviceState], float]:
        self._update_registry(discover_devices())

        for device_id, device in self._devices.items():
            if device.has_battery and (force_read or time.time() >= self._next_read_at[device_id]):
                self._read(device)

        now = time.time()
        next_reads = [
            self._next_read_at[device_id]
            for device_id, device in self._devices.items()
            if device.has_battery
        ]
        next_event = min(next_reads + [now + config.DISCOVERY_INTERVAL_SECONDS])
        return self._snapshot(), next_event - now

    def _update_registry(self, found: list[Device]) -> None:
        now = time.time()
        found_by_id = {device.id: device for device in found}

        for device_id in list(self._devices):
            if device_id not in found_by_id:
                logger.info("Disconnected: %s", self._devices[device_id].name)
                for table in (self._devices, self._states, self._next_read_at, self._failures):
                    table.pop(device_id, None)

        for device_id, device in found_by_id.items():
            old = self._devices.get(device_id)
            if old is not None and old.hardware_key == device.hardware_key:
                continue  # keep the existing object and its schedule
            if old is None:
                logger.info("Connected: %s", device.name)
                self._states[device_id] = DeviceState.for_device(device)
            else:
                # Same device on different hardware, e.g. a cable was plugged
                # in. Keep the last reading until the re-read below.
                self._states[device_id] = dataclasses.replace(
                    self._states[device_id], connection=device.connection
                )
            self._devices[device_id] = device
            self._next_read_at[device_id] = now

    def _read(self, device: Device) -> None:
        logger.debug("Reading battery: %s", device.name)
        now = time.time()
        state = self._states[device.id]
        try:
            battery = device.read_battery()
        except Exception as exc:  # noqa: BLE001 - one device mustn't stop the others
            failures = self._failures.get(device.id, 0) + 1
            self._failures[device.id] = failures
            # Failures repeat on every retry ("mouse asleep" is common), so
            # only log the first few in a row.
            quiet = failures > config.MAX_CONSECUTIVE_FAILURES_BEFORE_QUIET
            if isinstance(exc, BatteryReadError):
                message = exc.user_message
                if not quiet:
                    logger.info("Battery read failed for %s: %s", device.name, exc)
            else:
                message = "Something went wrong reading this device. Run with --verbose for details."
                if not quiet:
                    logger.exception("Unexpected error reading %s", device.name)
            self._states[device.id] = dataclasses.replace(
                state, connection=device.connection, error=message, last_attempt_at=now
            )
            self._next_read_at[device.id] = now + device.retry_interval_seconds
        else:
            self._failures[device.id] = 0
            self._states[device.id] = dataclasses.replace(
                state,
                connection=device.connection,
                battery=battery,
                error=None,
                last_attempt_at=now,
            )
            self._next_read_at[device.id] = now + device.poll_interval_seconds

    def _snapshot(self) -> list[DeviceState]:
        return sorted(
            self._states.values(),
            key=lambda state: (state.kind is not DeviceKind.MOUSE, state.name.lower()),
        )
