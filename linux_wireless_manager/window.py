"""The main window: detected devices on the left, and Status / Settings /
Alerts tabs for the selected device on the right.

A single instance is created at startup and hidden, not destroyed, on close.
It knows nothing about polling: it receives DeviceState snapshots through
update_devices() and calls on_refresh_requested for the Refresh button.

The Settings tab knows nothing about rivalcfg or any other backend either -
it only ever sees a device's SettingField schema and SettingsValues, fetched
and applied through the on_load_settings / on_apply_settings /
on_reset_settings callbacks (device_id in, callback(result, error) out). The
window never holds a Device or opens one; see app.py and monitor.py.
"""

from __future__ import annotations

import time
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import alerts, config, icons  # noqa: E402
from .devices import (  # noqa: E402
    ButtonsSetting,
    ChoiceSetting,
    DeviceState,
    DpiPresetsSetting,
    RangeSetting,
    SettingField,
    SettingsValues,
)
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


# -- Settings field widgets --------------------------------------------
#
# One builder per SettingField subtype. Each returns (widget, getter): the
# widget to show, and a zero-argument callable that reads back the value
# currently in the widget, in the shape apply_settings() expects.


def _ignore_unfocused_scroll(widget: Gtk.Widget, _event) -> bool:
    """GTK spin buttons and combo boxes change their value on a mouse-wheel
    scroll even when the user is only scrolling the page past them, not
    interacting with that particular widget. Since the whole form lives in
    a Gtk.ScrolledWindow, that's a real trap here - it's how a single stray
    tick over a button's dropdown while scrolling to reach the DPI field
    once silently remapped it to a media key. Swallow the scroll unless the
    widget was deliberately focused (clicked or tabbed to) first."""
    return not widget.has_focus()


def _no_scroll(widget: Gtk.Widget) -> Gtk.Widget:
    widget.connect("scroll-event", _ignore_unfocused_scroll)
    return widget


def _build_range_field(field: RangeSetting, value, on_changed: Callable) -> tuple[Gtk.Widget, Callable]:
    step = field.step or 1
    spin = _no_scroll(Gtk.SpinButton.new_with_range(field.minimum, field.maximum, step))
    spin.set_numeric(True)
    spin.set_value(value if value is not None else field.default)
    spin.connect("value-changed", on_changed)
    return spin, lambda: int(spin.get_value())


def _build_choice_field(field: ChoiceSetting, value, on_changed: Callable) -> tuple[Gtk.Widget, Callable]:
    combo = _no_scroll(Gtk.ComboBoxText())
    ordered_values = []
    active_index = 0
    for index, (label, option_value) in enumerate(field.options):
        combo.append_text(label)
        ordered_values.append(option_value)
        if option_value == value:
            active_index = index
    combo.set_active(active_index)
    combo.connect("changed", on_changed)
    return combo, lambda: ordered_values[combo.get_active()]


def _build_dpi_presets_field(
    field: DpiPresetsSetting, value, on_changed: Callable
) -> tuple[Gtk.Widget, Callable]:
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    presets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    outer.pack_start(presets_box, False, False, 0)
    rows: list[tuple[Gtk.Widget, Gtk.SpinButton, Gtk.SpinButton | None]] = []

    def _update_remove_sensitivity() -> None:
        # Never allow removing the last remaining preset.
        can_remove = len(rows) > 1
        for row, *_spins in rows:
            row.get_children()[-1].set_sensitive(can_remove)

    def _add_preset_row(x_value: int, y_value: int | None) -> None:
        step = field.step or 1
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        spin_x = _no_scroll(Gtk.SpinButton.new_with_range(field.minimum, field.maximum, step))
        spin_x.set_numeric(True)
        spin_x.set_value(x_value)
        spin_x.connect("value-changed", on_changed)
        row.pack_start(spin_x, False, False, 0)

        spin_y = None
        if not field.axes_linked:
            row.pack_start(Gtk.Label(label="×"), False, False, 0)
            spin_y = _no_scroll(Gtk.SpinButton.new_with_range(field.minimum, field.maximum, step))
            spin_y.set_numeric(True)
            spin_y.set_value(y_value if y_value is not None else x_value)
            spin_y.connect("value-changed", on_changed)
            row.pack_start(spin_y, False, False, 0)

        remove_button = Gtk.Button.new_from_icon_name("list-remove-symbolic", Gtk.IconSize.BUTTON)
        remove_button.set_tooltip_text("Remove this preset")

        def _on_remove(_button, row=row) -> None:
            presets_box.remove(row)
            rows[:] = [entry for entry in rows if entry[0] is not row]
            add_button.set_sensitive(len(rows) < field.max_presets)
            _update_remove_sensitivity()
            on_changed()

        remove_button.connect("clicked", _on_remove)
        row.pack_start(remove_button, False, False, 0)

        presets_box.pack_start(row, False, False, 0)
        row.show_all()
        rows.append((row, spin_x, spin_y))
        add_button.set_sensitive(len(rows) < field.max_presets)
        _update_remove_sensitivity()

    add_button = Gtk.Button(label="Add preset")

    def _on_add(_button) -> None:
        _add_preset_row(field.minimum, field.minimum)
        on_changed()

    add_button.connect("clicked", _on_add)

    initial = value if value else field.default
    if field.axes_linked:
        for preset in initial:
            _add_preset_row(preset, None)
    else:
        for x_value, y_value in initial:
            _add_preset_row(x_value, y_value)

    add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    add_row.pack_start(add_button, False, False, 0)
    outer.pack_start(add_row, False, False, 0)

    def getter():
        if field.axes_linked:
            return [int(spin_x.get_value()) for _row, spin_x, _spin_y in rows]
        return [(int(spin_x.get_value()), int(spin_y.get_value())) for _row, spin_x, spin_y in rows]

    return outer, getter


def _build_buttons_field(field: ButtonsSetting, value, on_changed: Callable) -> tuple[Gtk.Widget, Callable]:
    grid = Gtk.Grid(row_spacing=6, column_spacing=12)
    current = value if value else field.default
    getters: dict[str, Callable] = {}

    for row_index, button_name in enumerate(field.buttons):
        grid.attach(Gtk.Label(label=button_name, xalign=0), 0, row_index, 1, 1)
        combo = _no_scroll(Gtk.ComboBoxText())
        ordered_values = []
        active_index = 0
        button_key = button_name.lower()
        current_value = current.get(button_key, field.default.get(button_key))
        for index, action in enumerate(field.actions):
            label = action.label if action.group == "Action" else f"{action.group}: {action.label}"
            combo.append_text(label)
            ordered_values.append(action.value)
            if action.value == current_value:
                active_index = index
        combo.set_active(active_index)
        combo.connect("changed", on_changed)
        grid.attach(combo, 1, row_index, 1, 1)
        getters[button_key] = (lambda combo=combo, ordered_values=ordered_values: ordered_values[combo.get_active()])

    def getter():
        return {name: get() for name, get in getters.items()}

    return grid, getter


def _build_field_group(field: SettingField, current_values: dict, on_changed: Callable) -> tuple[Gtk.Widget, Callable]:
    value = current_values.get(field.name, field.default)
    if isinstance(field, RangeSetting):
        widget, getter = _build_range_field(field, value, on_changed)
    elif isinstance(field, ChoiceSetting):
        widget, getter = _build_choice_field(field, value, on_changed)
    elif isinstance(field, DpiPresetsSetting):
        widget, getter = _build_dpi_presets_field(field, value, on_changed)
    elif isinstance(field, ButtonsSetting):
        widget, getter = _build_buttons_field(field, value, on_changed)
    else:  # pragma: no cover - defensive; every current field type is handled above
        widget, getter = _dim_label("Unsupported setting"), (lambda: None)
    widget.set_halign(Gtk.Align.START)

    heading = Gtk.Label(xalign=0)
    heading.set_markup(f"<b>{GLib.markup_escape_text(field.label)}</b>")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.pack_start(heading, False, False, 0)
    box.pack_start(widget, False, False, 0)
    return box, getter


class MainWindow(Gtk.Window):
    def __init__(
        self,
        *,
        on_refresh_requested: Callable[[], None],
        preferences: Preferences,
        custom_icons: bool,
        alert_manager: alerts.AlertManager,
        on_load_settings: Callable[[str, Callable], None],
        on_apply_settings: Callable[[str, dict, Callable], None],
        on_reset_settings: Callable[[str, Callable], None],
    ):
        super().__init__(title=config.APP_NAME)
        self._on_refresh_requested = on_refresh_requested
        self._preferences = preferences
        self._custom_icons = custom_icons
        self._alerts = alert_manager
        self._on_load_settings = on_load_settings
        self._on_apply_settings = on_apply_settings
        self._on_reset_settings = on_reset_settings
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
        # The device the Settings tab is showing (or loading), the schema it
        # loaded for that device, and the field getters currently on screen.
        # Re-filled only when the shown device changes, same reasoning as
        # Alerts: a poll must never overwrite what the user is editing.
        self._settings_device_id: str | None = None
        self._settings_schema: list[SettingField] = []
        self._settings_getters: dict[str, Callable] = {}

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
        self._tabs.add_titled(self._build_settings_tab(), "settings", "Settings")
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

    def _build_settings_tab(self) -> Gtk.Widget:
        self._settings_loading = _dim_label("Loading settings...")
        self._settings_loading.set_valign(Gtk.Align.CENTER)
        self._settings_loading.set_halign(Gtk.Align.CENTER)

        self._settings_message = _placeholder("")

        self._settings_form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._settings_form_box.set_border_width(24)
        form_scroller = Gtk.ScrolledWindow()
        form_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroller.add(self._settings_form_box)

        self._settings_note = _dim_label()
        self._settings_note.set_line_wrap(True)
        self._settings_note.set_xalign(0)
        self._settings_note.set_margin_start(24)
        self._settings_note.set_margin_end(24)
        self._settings_note.set_margin_top(12)

        self._settings_status = _dim_label()
        self._settings_status.set_line_wrap(True)
        self._settings_status.set_xalign(0)
        self._settings_status.set_margin_start(24)

        self._settings_reset_button = Gtk.Button(label="Reset to Defaults")
        self._settings_reset_button.connect("clicked", self._on_reset_settings_clicked)
        self._settings_apply_button = Gtk.Button(label="Apply")
        self._settings_apply_button.set_sensitive(False)
        self._settings_apply_button.connect("clicked", self._on_apply_settings_clicked)
        form_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        form_buttons.set_halign(Gtk.Align.END)
        form_buttons.set_border_width(12)
        form_buttons.pack_start(self._settings_reset_button, False, False, 0)
        form_buttons.pack_start(self._settings_apply_button, False, False, 0)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_row.pack_start(self._settings_status, True, True, 0)
        status_row.pack_start(form_buttons, False, False, 0)

        form_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        form_panel.pack_start(self._settings_note, False, False, 0)
        form_panel.pack_start(form_scroller, True, True, 0)
        form_panel.pack_start(Gtk.Separator(), False, False, 0)
        form_panel.pack_start(status_row, False, False, 0)

        self._settings_page = Gtk.Stack()
        self._settings_page.add_named(self._settings_loading, "loading")
        self._settings_page.add_named(self._settings_message, "message")
        self._settings_page.add_named(form_panel, "form")
        return self._settings_page

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

    # -- Settings tab -----------------------------------------------------

    def _populate_settings(self, state: DeviceState) -> None:
        """(Re)load the Settings tab, but only when the shown device changes,
        so a poll can't overwrite an edit in progress or a request already
        in flight."""
        if state.id == self._settings_device_id:
            return
        self._settings_device_id = state.id
        self._settings_schema = []
        self._clear_settings_form()
        self._settings_status.set_text("")
        self._settings_apply_button.set_sensitive(False)
        self._settings_page.set_visible_child_name("loading")

        device_id = state.id
        self._on_load_settings(
            device_id, lambda result, error: self._on_settings_loaded(device_id, result, error)
        )

    def _on_settings_loaded(self, device_id: str, result, error) -> None:
        if device_id != self._settings_device_id:
            return  # the user has since switched devices; this reply is stale
        if error is not None:
            self._show_settings_message("Couldn't load settings for this device.")
            return
        schema, settings_values = result
        if not schema:
            self._show_settings_message("This device has no configurable settings.")
            return
        self._build_settings_form(schema, settings_values)

    def _clear_settings_form(self) -> None:
        for child in self._settings_form_box.get_children():
            self._settings_form_box.remove(child)
        self._settings_getters = {}

    def _build_settings_form(self, schema: list[SettingField], settings_values: SettingsValues | None) -> None:
        self._settings_schema = schema
        self._clear_settings_form()
        current_values = dict(settings_values.values) if settings_values is not None else {}
        for field in schema:
            group, getter = _build_field_group(field, current_values, self._on_settings_field_changed)
            self._settings_form_box.pack_start(group, False, False, 0)
            self._settings_getters[field.name] = getter
        self._settings_form_box.show_all()
        self._update_settings_cache_note(settings_values)
        self._settings_apply_button.set_sensitive(False)
        self._settings_page.set_visible_child_name("form")

    def _update_settings_cache_note(self, settings_values: SettingsValues | None) -> None:
        if settings_values is None:
            self._settings_note.set_text("")
        elif settings_values.from_cache:
            self._settings_note.set_text("Showing the values this app last saved to the device.")
        else:
            self._settings_note.set_text(
                "Showing the model's defaults - nothing has been saved from this app yet."
            )

    def _show_settings_message(self, text: str) -> None:
        self._settings_message.set_text(text)
        self._settings_page.set_visible_child_name("message")

    def _on_settings_field_changed(self, *_args) -> None:
        self._settings_apply_button.set_sensitive(True)

    def _on_apply_settings_clicked(self, *_args) -> None:
        device_id = self._shown_id
        if device_id is None:
            return
        values = {name: getter() for name, getter in self._settings_getters.items()}
        self._settings_apply_button.set_sensitive(False)
        self._settings_reset_button.set_sensitive(False)
        self._settings_status.set_text("Applying...")
        self._on_apply_settings(
            device_id, values, lambda result, error: self._on_settings_applied(device_id, result, error)
        )

    def _on_settings_applied(self, device_id: str, result: SettingsValues | None, error: Exception | None) -> None:
        if device_id != self._settings_device_id:
            return  # the form for this device isn't on screen any more
        self._settings_reset_button.set_sensitive(True)
        if error is not None:
            self._settings_apply_button.set_sensitive(True)
            self._settings_status.set_text(getattr(error, "user_message", None) or f"Couldn't save: {error}")
            return
        self._settings_status.set_text("Saved.")
        if result is not None:
            # Rebuild so the form reflects exactly what got saved (values may
            # have been snapped to the nearest step the hardware supports).
            self._build_settings_form(self._settings_schema, result)

    def _on_reset_settings_clicked(self, *_args) -> None:
        device_id = self._shown_id
        if device_id is None:
            return
        self._settings_apply_button.set_sensitive(False)
        self._settings_reset_button.set_sensitive(False)
        self._settings_status.set_text("Resetting...")
        self._on_reset_settings(
            device_id, lambda result, error: self._on_settings_reset(device_id, result, error)
        )

    def _on_settings_reset(self, device_id: str, result: SettingsValues | None, error: Exception | None) -> None:
        if device_id != self._settings_device_id:
            return
        self._settings_reset_button.set_sensitive(True)
        if error is not None:
            self._settings_apply_button.set_sensitive(False)
            self._settings_status.set_text(getattr(error, "user_message", None) or f"Couldn't reset: {error}")
            return
        self._settings_status.set_text("Reset to the model's defaults.")
        if result is not None:
            self._build_settings_form(self._settings_schema, result)

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
        self._populate_settings(state)

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
