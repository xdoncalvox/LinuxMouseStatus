"""Wires the pieces together: DeviceMonitor produces DeviceState snapshots,
which go to the tray icons and the window. Everything here runs on the GTK
main thread.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import icons  # noqa: E402
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

        self.monitor = DeviceMonitor(on_update=self._on_devices_updated)
        self.window = MainWindow(
            on_refresh_requested=self.monitor.refresh_now,
            preferences=Preferences(),
            custom_icons=icon_dir is not None,
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

    def _on_devices_updated(self, states: list[DeviceState]) -> None:
        for kind, indicator in self.indicators.items():
            indicator.update([s for s in states if s.kind is kind and s.has_battery])
        self.window.update_devices(states)
