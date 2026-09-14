"""Tray icons: one for mice, which is always shown so the app stays
reachable, and one for keyboards, shown only while a battery-powered keyboard
is connected.

Each icon shows the lowest battery among its devices, and its menu lists
them all. Wired devices without a battery never appear here, only in the
window.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3  # type: ignore[no-redef]

from gi.repository import Gtk  # noqa: E402

from . import config, icons  # noqa: E402
from .devices import DeviceKind, DeviceState  # noqa: E402

_NOTHING_FOUND = {
    DeviceKind.MOUSE: "No wireless mouse found",
    DeviceKind.KEYBOARD: "No wireless keyboard found",
}


def _menu_status(state: DeviceState) -> str:
    if state.error is not None:
        return "unavailable"
    if state.battery is None:
        return "checking..."
    charging = " (charging)" if state.battery.is_charging else ""
    return state.battery.describe() + charging


class KindIndicator:
    def __init__(
        self,
        kind: DeviceKind,
        *,
        icon_dir: str | None,
        always_visible: bool,
        on_show_details: Callable[[], None],
        on_refresh: Callable[[], None],
        on_quit: Callable[[], None],
    ):
        self._kind = kind
        self._custom_icons = icon_dir is not None
        self._always_visible = always_visible

        indicator_id = f"{config.APP_ID}-{kind.value}"
        initial_icon = icons.icon_name(kind, None, custom=self._custom_icons)
        category = AppIndicator3.IndicatorCategory.HARDWARE
        if icon_dir is not None:
            self._indicator = AppIndicator3.Indicator.new_with_path(
                indicator_id, initial_icon, category, icon_dir
            )
        else:
            self._indicator = AppIndicator3.Indicator.new(indicator_id, initial_icon, category)
        self._indicator.set_title(config.APP_NAME)

        self._menu = Gtk.Menu()
        self._device_items: list[Gtk.MenuItem] = []
        self._menu.append(Gtk.SeparatorMenuItem())
        show_details_item = None
        for label, callback in (("Show Details", on_show_details), ("Refresh Now", on_refresh)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, cb=callback: cb())
            self._menu.append(item)
            if label == "Show Details":
                show_details_item = item
        self._menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: on_quit())
        self._menu.append(quit_item)
        self._menu.show_all()
        self._indicator.set_menu(self._menu)
        # AppIndicator has no plain "activate"/double-click signal - every
        # click opens the menu. The "secondary activate" target is the
        # closest thing: Ubuntu's top bar triggers it on a double-click.
        self._indicator.set_secondary_activate_target(show_details_item)

        self.update([])

    def update(self, states: list[DeviceState]) -> None:
        """states: the battery-powered devices of this kind."""
        visible = bool(states) or self._always_visible
        self._indicator.set_status(
            AppIndicator3.IndicatorStatus.ACTIVE if visible else AppIndicator3.IndicatorStatus.PASSIVE
        )

        readable = [s for s in states if s.battery is not None and s.error is None]
        lowest = min(readable, key=lambda s: s.battery.estimated_percentage, default=None)
        battery = lowest.battery if lowest is not None else None

        description = f"{self._kind.value.capitalize()} battery " + (
            battery.describe() if battery else "unknown"
        )
        self._indicator.set_icon_full(
            icons.icon_name(self._kind, battery, custom=self._custom_icons), description
        )
        # The second argument is a sizing guide so the panel doesn't jiggle
        # as the label text changes length.
        self._indicator.set_label(battery.describe() if battery else "?", "100%")

        for item in self._device_items:
            self._menu.remove(item)
            item.destroy()
        texts = [f"{s.name}: {_menu_status(s)}" for s in states] or [_NOTHING_FOUND[self._kind]]
        self._device_items = []
        for position, text in enumerate(texts):
            item = Gtk.MenuItem(label=text)
            item.set_sensitive(False)
            item.show()
            self._menu.insert(item, position)
            self._device_items.append(item)
