"""The main window: detected devices on the left, and Status / Settings /
Alerts tabs for the selected device on the right.

A single instance is created at startup and hidden, not destroyed, on close.
It knows nothing about polling: it receives DeviceState snapshots through
update_devices() and calls on_refresh_requested for the Refresh button.
"""

from __future__ import annotations

import time
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import alerts, config, icons  # noqa: E402
from .devices import DeviceState  # noqa: E402
from .preferences import Preferences  # noqa: E402

_SELECTED_DEVICE_KEY = "selected_device"


def _clock(timestamp: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def _row_detail(state: DeviceState) -> str:
    if not state.has_battery:
        return f"No battery · {state.connection.value}"
    if state.error is not None:
        return "Unavailable"
    if state.battery is None:
        return "Checking..."
    charging = " · charging" if state.battery.is_charging else ""
    return state.battery.describe() + charging


def _status_texts(state: DeviceState) -> tuple[str, bool, str, str]:
    """(level text, whether to show it large, status line, timing line)."""
    connected = f"connected via {state.connection.value}"
    if not state.has_battery:
        return "No battery", False, connected.capitalize(), ""
    if state.error is not None:
        if state.battery is not None:
            timing = (
                f"Last reading {state.battery.describe()} at {_clock(state.battery.read_at)}"
                f" · last attempt {_clock(state.last_attempt_at)}"
            )
        else:
            timing = f"Last attempt {_clock(state.last_attempt_at)}"
        return "--", True, state.error, timing
    if state.battery is None:
        return "--", True, "Checking...", ""
    charging = "Charging" if state.battery.is_charging else "On battery"
    return (
        state.battery.describe(),
        True,
        f"{charging} — {connected}",
        f"Last updated {_clock(state.battery.read_at)}",
    )


def _dim_label(text: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.get_style_context().add_class("dim-label")
    return label


def _placeholder(text: str) -> Gtk.Widget:
    label = _dim_label(text)
    label.set_line_wrap(True)
    label.set_margin_start(24)
    label.set_margin_end(24)
    return label


class _DeviceRow(Gtk.ListBoxRow):
    def __init__(self, state: DeviceState, custom_icons: bool):
        super().__init__()
        self.device_id = state.id

        battery = state.battery if state.error is None else None
        icon = icons.icon_name(state.kind, battery, custom=custom_icons and state.has_battery)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)

        name = Gtk.Label(label=state.name, xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        detail = _dim_label(_row_detail(state))
        detail.set_xalign(0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.pack_start(name, False, False, 0)
        text.pack_start(detail, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_border_width(8)
        box.pack_start(image, False, False, 0)
        box.pack_start(text, True, True, 0)
        self.add(box)


class MainWindow(Gtk.Window):
    def __init__(
        self,
        *,
        on_refresh_requested: Callable[[], None],
        preferences: Preferences,
        custom_icons: bool,
        alert_manager: alerts.AlertManager,
    ):
        super().__init__(title=config.APP_NAME)
        self._on_refresh_requested = on_refresh_requested
        self._preferences = preferences
        self._custom_icons = custom_icons
        self._alerts = alert_manager
        self._states: dict[str, DeviceState] = {}
        self._listed_states: list[DeviceState] | None = None
        # The device the user picked (remembered across restarts), and the one
        # actually shown, which falls back to the first device while the
        # picked one isn't connected.
        self._chosen_id: str | None = preferences.get(_SELECTED_DEVICE_KEY)
        self._shown_id: str | None = None
        self._rebuilding_list = False
        # The device whose settings are in the Alerts tab, and a guard so
        # filling that tab in doesn't count as the user editing it.
        self._alerts_device_id: str | None = None
        self._loading_alerts = False

        self.set_default_size(640, 360)
        # Hide instead of destroying on close, so the tray icons can reuse
        # the same window instead of rebuilding it every time.
        self.connect("delete-event", self._on_delete_event)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self._list.connect("row-selected", self._on_row_selected)
        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroller.set_size_request(270, -1)
        list_scroller.add(self._list)

        # Right side: the device's tabs, or a message when nothing is connected.
        self._content = Gtk.Stack()
        self._content.add_named(self._build_device_page(), "device")
        self._content.add_named(self._build_empty_page(), "empty")

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.pack_start(list_scroller, False, False, 0)
        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        body.pack_start(self._content, True, True, 0)

        refresh_button = Gtk.Button(label="Refresh Now")
        refresh_button.connect("clicked", self._on_refresh_clicked)
        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda *_: self.hide())
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        buttons.set_border_width(12)
        buttons.pack_start(refresh_button, False, False, 0)
        buttons.pack_start(close_button, False, False, 0)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(body, True, True, 0)
        root.pack_start(Gtk.Separator(), False, False, 0)
        root.pack_start(buttons, False, False, 0)
        self.add(root)
        root.show_all()
        self._show_current()

    # -- Construction ---------------------------------------------------

    def _build_device_page(self) -> Gtk.Widget:
        self._tabs = Gtk.Stack()
        self._tabs.add_titled(self._build_status_tab(), "status", "Status")
        self._tabs.add_titled(
            _placeholder("Device settings aren't available yet."), "settings", "Settings"
        )
        self._tabs.add_titled(self._build_alerts_tab(), "alerts", "Alerts")
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self._tabs)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(12)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.pack_start(switcher, False, False, 0)
        page.pack_start(self._tabs, True, True, 0)
        return page

    def _build_status_tab(self) -> Gtk.Widget:
        self._name_label = Gtk.Label()
        self._name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._level_label = Gtk.Label()
        self._status_label = Gtk.Label()
        self._status_label.set_line_wrap(True)
        self._status_label.set_justify(Gtk.Justification.CENTER)
        self._status_label.set_max_width_chars(40)
        self._updated_label = _dim_label()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(24)
        box.set_valign(Gtk.Align.CENTER)
        for label in (self._name_label, self._level_label, self._status_label, self._updated_label):
            box.pack_start(label, False, False, 0)
        return box

    def _build_alerts_tab(self) -> Gtk.Widget:
        self._alerts_enabled = Gtk.Switch(halign=Gtk.Align.START)
        self._alerts_enabled.connect("notify::active", self._on_alert_changed)
        self._alerts_charged = Gtk.Switch(halign=Gtk.Align.START)
        self._alerts_charged.connect("notify::active", self._on_alert_changed)
        self._alert_spins = []
        for _ in range(2):
            spin = Gtk.SpinButton.new_with_range(alerts.MIN_THRESHOLD, alerts.MAX_THRESHOLD, 5)
            spin.set_numeric(True)
            spin.connect("value-changed", self._on_alert_changed)
            self._alert_spins.append(spin)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        fields = (
            ("Warn me when the battery is low", self._alerts_enabled),
            ("First warning at (%)", self._alert_spins[0]),
            ("Second warning at (%)", self._alert_spins[1]),
            ("Tell me when it's fully charged", self._alerts_charged),
        )
        for row, (text, widget) in enumerate(fields):
            grid.attach(Gtk.Label(label=text, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        self._alerts_note = _dim_label()
        self._alerts_note.set_line_wrap(True)
        self._alerts_note.set_max_width_chars(44)
        self._alerts_note.set_xalign(0)

        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        form.set_border_width(24)
        form.set_valign(Gtk.Align.CENTER)
        form.pack_start(grid, False, False, 0)
        form.pack_start(self._alerts_note, False, False, 0)

        self._alerts_message = _placeholder("")
        self._alerts_page = Gtk.Stack()
        self._alerts_page.add_named(form, "form")
        self._alerts_page.add_named(self._alerts_message, "message")
        return self._alerts_page

    def _populate_alerts(self, state: DeviceState) -> None:
        """Fill the Alerts tab, but only when the shown device changes, so a
        poll can't overwrite what the user is editing."""
        if state.id == self._alerts_device_id:
            self._update_alerts_note(state)
            return
        self._alerts_device_id = state.id

        if not state.has_battery:
            self._alerts_message.set_text(
                "This device has no battery, so there's nothing to alert on."
            )
            self._alerts_page.set_visible_child_name("message")
            return
        if not self._alerts.available:
            self._alerts_message.set_text(
                "Desktop notifications aren't available. Install gir1.2-notify-0.7 "
                "and restart the app."
            )
            self._alerts_page.set_visible_child_name("message")
            return

        settings = self._alerts.settings_for(state.id)
        values = list(settings.thresholds)[:2]
        while len(values) < 2:
            values.append(alerts.DEFAULT_THRESHOLDS[len(values)])
        self._loading_alerts = True
        try:
            self._alerts_enabled.set_active(settings.enabled)
            self._alerts_charged.set_active(settings.notify_charged)
            for spin, value in zip(self._alert_spins, values):
                spin.set_value(value)
                spin.set_sensitive(settings.enabled)
        finally:
            self._loading_alerts = False
        self._update_alerts_note(state)
        self._alerts_page.set_visible_child_name("form")

    def _update_alerts_note(self, state: DeviceState) -> None:
        coarse = state.battery is not None and state.battery.percentage is None
        self._alerts_note.set_text(
            'This device reports a rough level instead of a percentage: '
            '"Low" counts as 20% and "Critical" as 5%.'
            if coarse
            else ""
        )

    def _build_empty_page(self) -> Gtk.Widget:
        title = Gtk.Label()
        title.set_markup("<b>No supported devices found</b>")
        hint = _dim_label(
            "Make sure the device is turned on and its USB receiver is plugged in, "
            "then click Refresh Now."
        )
        hint.set_line_wrap(True)
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_max_width_chars(40)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(24)
        box.set_valign(Gtk.Align.CENTER)
        box.pack_start(title, False, False, 0)
        box.pack_start(hint, False, False, 0)
        return box

    # -- Updates --------------------------------------------------------

    def update_devices(self, states: list[DeviceState]) -> None:
        self._states = {state.id: state for state in states}
        # Most detection ticks change nothing; don't rebuild the list then.
        if states != self._listed_states:
            self._listed_states = states
            self._rebuild_list(states)
        self._show_current()

    def _rebuild_list(self, states: list[DeviceState]) -> None:
        self._rebuilding_list = True
        try:
            for row in self._list.get_children():
                self._list.remove(row)
            rows = [_DeviceRow(state, self._custom_icons) for state in states]
            for row in rows:
                self._list.add(row)
            self._list.show_all()
            shown = next((row for row in rows if row.device_id == self._chosen_id), None)
            if shown is None and rows:
                shown = rows[0]
            if shown is not None:
                self._list.select_row(shown)
            self._shown_id = shown.device_id if shown is not None else None
        finally:
            self._rebuilding_list = False

    def _show_current(self) -> None:
        state = self._states.get(self._shown_id)
        if state is None:
            self._content.set_visible_child_name("empty")
            return
        self._content.set_visible_child_name("device")

        level, large, status, timing = _status_texts(state)
        size = 48000 if large else 24000
        self._name_label.set_markup(f"<b>{GLib.markup_escape_text(state.name)}</b>")
        self._level_label.set_markup(f"<span size='{size}'>{GLib.markup_escape_text(level)}</span>")
        self._status_label.set_text(status)
        self._updated_label.set_text(timing)
        self._populate_alerts(state)

    def show_device(self, device_id: str) -> None:
        """Select one device, e.g. when its notification is clicked."""
        self._chosen_id = self._shown_id = device_id
        for row in self._list.get_children():
            if row.device_id == device_id:
                self._list.select_row(row)
                break
        self._show_current()

    # -- Signal handlers ------------------------------------------------

    def _on_row_selected(self, _list: Gtk.ListBox, row: _DeviceRow | None) -> None:
        if self._rebuilding_list or row is None:
            return
        self._chosen_id = self._shown_id = row.device_id
        self._preferences.set(_SELECTED_DEVICE_KEY, row.device_id)
        self._show_current()

    def _on_alert_changed(self, *_args) -> None:
        if self._loading_alerts or self._shown_id is None:
            return
        state = self._states.get(self._shown_id)
        if state is None or not state.has_battery:
            return
        enabled = self._alerts_enabled.get_active()
        for spin in self._alert_spins:
            spin.set_sensitive(enabled)
        self._alerts.set_settings(
            state.id,
            alerts.AlertSettings(
                enabled=enabled,
                thresholds=alerts.clean_thresholds(
                    int(spin.get_value()) for spin in self._alert_spins
                ),
                notify_charged=self._alerts_charged.get_active(),
            ),
        )

    def _on_refresh_clicked(self, *_args) -> None:
        if self._content.get_visible_child_name() == "device":
            self._status_label.set_text("Refreshing...")
        self._on_refresh_requested()

    def _on_delete_event(self, *_args) -> bool:
        self.hide()
        return True  # don't destroy the window, just hide it
