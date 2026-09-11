"""Standalone detail window: a bigger view of the same battery status shown
in the tray icon, with a manual refresh button.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import config  # noqa: E402


class BatteryWindow(Gtk.Window):
    def __init__(self, on_refresh_requested):
        """
        on_refresh_requested: callable() -> None, invoked when the user
        clicks "Refresh Now" in this window. The caller is responsible for
        eventually calling `update_status()` or `update_error()` again once
        a new reading is available.
        """
        super().__init__(title=config.APP_NAME)
        self._on_refresh_requested = on_refresh_requested

        self.set_default_size(320, 220)
        self.set_resizable(False)
        self.set_border_width(24)
        # Hide instead of destroying on close, so the tray icon can reuse
        # the same window instance instead of rebuilding it every time.
        self.connect("delete-event", self._on_delete_event)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(outer)

        self._level_label = Gtk.Label()
        self._level_label.set_markup("<span size='48000'>--%</span>")
        outer.pack_start(self._level_label, False, False, 0)

        self._status_label = Gtk.Label(label="No reading yet")
        self._status_label.set_line_wrap(True)
        outer.pack_start(self._status_label, False, False, 0)

        self._updated_label = Gtk.Label(label="")
        self._updated_label.get_style_context().add_class("dim-label")
        outer.pack_start(self._updated_label, False, False, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.CENTER)
        outer.pack_end(button_box, False, False, 0)

        refresh_button = Gtk.Button(label="Refresh Now")
        refresh_button.connect("clicked", self._on_refresh_clicked)
        button_box.pack_start(refresh_button, False, False, 0)

        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda *_: self.hide())
        button_box.pack_start(close_button, False, False, 0)

        outer.show_all()

    def _on_delete_event(self, *_args) -> bool:
        self.hide()
        return True  # don't destroy the window, just hide it

    def _on_refresh_clicked(self, *_args) -> None:
        self._status_label.set_text("Refreshing...")
        self._on_refresh_requested()

    def update_status(self, level: int, is_charging: bool, connection: str) -> None:
        self._level_label.set_markup(f"<span size='48000'>{level}%</span>")
        charging_text = "Charging" if is_charging else "On battery"
        conn_text = "wireless dongle" if connection == "wireless_dongle" else "USB cable"
        self._status_label.set_text(f"{charging_text} — connected via {conn_text}")
        self._updated_label.set_text(f"Last updated {time.strftime('%H:%M:%S')}")

    def update_error(self, message: str) -> None:
        self._status_label.set_text(message)
        self._updated_label.set_text(f"Last attempt {time.strftime('%H:%M:%S')}")
