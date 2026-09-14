"""Wires the pieces together: DeviceMonitor produces DeviceState snapshots,
which go to the tray icons and the window. Everything here runs on the GTK
main thread.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import icons  # noqa: E402
from .alerts import AlertManager  # noqa: E402
from .devices import DeviceKind, DeviceState  # noqa: E402
from .monitor import DeviceMonitor  # noqa: E402
from .preferences import Preferences  # noqa: E402
from .tray import KindIndicator  # noqa: E402
from .window import MainWindow  # noqa: E402


class WirelessManagerApp:
    def __init__(self, show_window_on_start: bool = False):
        icon_dir = icons.install()
        if icon_dir is not None:
            # Lets the window use the same icons as the tray.
            Gtk.IconTheme.get_default().append_search_path(icon_dir)

        preferences = Preferences()
        self.monitor = DeviceMonitor(on_update=self._on_devices_updated)
        self.alerts = AlertManager(
            preferences, icon_dir=icon_dir, on_show_device=self.show_device
        )
        self.window = MainWindow(
            on_refresh_requested=self.monitor.refresh_now,
            preferences=preferences,
            custom_icons=icon_dir is not None,
            alert_manager=self.alerts,
            on_load_settings=self._load_settings,
            on_apply_settings=self._apply_settings,
            on_reset_settings=self._reset_settings,
        )
        # The mouse icon is always shown so the app stays reachable; the
        # keyboard icon only while a battery-powered keyboard is connected.
        self.indicators = {
            kind: KindIndicator(
                kind,
                icon_dir=icon_dir,
                always_visible=kind is DeviceKind.MOUSE,
                on_show_details=self.show_window,
                on_refresh=self.monitor.refresh_now,
                on_quit=Gtk.main_quit,
            )
            for kind in DeviceKind
        }

        if show_window_on_start:
            self.show_window()

        self.monitor.start()

    def show_window(self) -> None:
        self.window.present()

    def show_device(self, device_id: str) -> None:
        """Open the window on one device, e.g. from a notification."""
        self.window.show_device(device_id)
        self.show_window()

    def _on_devices_updated(self, states: list[DeviceState]) -> None:
        for kind, indicator in self.indicators.items():
            indicator.update([s for s in states if s.kind is kind and s.has_battery])
        self.window.update_devices(states)
        self.alerts.update(states)

    # -- Settings tab plumbing -------------------------------------------
    #
    # window.py only ever sees device_id strings and these callbacks; the
    # Device objects (and settings I/O) stay in the monitor's worker thread.

    def _load_settings(self, device_id: str, callback) -> None:
        def task(device):
            schema = device.settings_schema()
            # get_settings() is only implemented (and only safe to call) when
            # a backend actually declared fields; most devices have none.
            return schema, (device.get_settings() if schema else None)

        self.monitor.run_device_task(device_id, task, callback)

    def _apply_settings(self, device_id: str, values: dict, callback) -> None:
        self.monitor.run_device_task(
            device_id, lambda device: device.apply_settings(values), callback, refresh_battery=True
        )

    def _reset_settings(self, device_id: str, callback) -> None:
        self.monitor.run_device_task(
            device_id, lambda device: device.reset_settings(), callback, refresh_battery=True
        )
