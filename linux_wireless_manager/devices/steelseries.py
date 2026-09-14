"""SteelSeries mice, through rivalcfg (https://github.com/flozz/rivalcfg).

rivalcfg knows the HID protocol and has one profile per USB product ID. A
wireless SteelSeries mouse has a different product ID for each connection
(2.4 GHz receiver or USB cable), so discover() groups the plugged-in IDs by
model name and returns one SteelSeriesMouse per physical mouse.

Every read opens the HID device and closes it again, because holding it open
would block the rivalcfg CLI and other tools. Settings writes do the same
(see apply_settings() and reset_settings()).

Settings are write-only: there's no HID command to read DPI, polling rate or
button mapping back from the mouse. The only record is rivalcfg's own cache
file (one per product ID, so wired and wireless modes don't share one), which
this module also reads from and writes to directly, alongside the mouse
itself. See get_settings().
"""

from __future__ import annotations

import json
import os
import re
import time

from .base import (
    BatteryReadError,
    BatteryStatus,
    ButtonAction,
    ButtonsSetting,
    ChoiceSetting,
    Connection,
    Device,
    DeviceKind,
    DpiPresetsSetting,
    RangeSetting,
    SettingField,
    SettingsValues,
    SettingsWriteError,
)

try:
    import hid
    from rivalcfg import devices as rivalcfg_devices
    from rivalcfg import mouse_settings
    from rivalcfg.handlers.buttons import layout_multimedia, layout_qwerty
    from rivalcfg.helpers import parse_param_string
    from rivalcfg.mouse import get_mouse
    from rivalcfg.usbhid import DeviceNotFound
except ImportError as exc:  # pragma: no cover - surfaced at startup instead
    raise ImportError(
        "rivalcfg (and its hidapi dependency) isn't installed. Run install.sh, "
        "or `pip install -r requirements.txt` inside the virtualenv."
    ) from exc

VENDOR_ID = 0x1038

# rivalcfg model names end in the connection, e.g.
# "SteelSeries Rival 650 Wireless (2.4 GHz wireless mode)" or "... (wired mode)".
_MODE_SUFFIX = re.compile(r"^(?P<model>.+?)\s*\((?P<mode>[^()]*\bmode)\)$")

_ASLEEP_MESSAGE = (
    "The receiver is plugged in, but the mouse didn't respond. "
    "It may be turned off, asleep or out of range."
)
_OPEN_FAILED_MESSAGE = (
    "Couldn't open the mouse. If this keeps happening, re-run install.sh to "
    "install the udev permission rule, then unplug and replug the receiver."
)

# First version of Settings covers DPI, polling rate, sleep timer and button
# mapping (see ROADMAP.md). Everything else a profile might expose - RGB
# lighting, dim timers, firmware-only toggles - is left out on purpose.
_SUPPORTED_SETTINGS = frozenset(
    {"sensitivity", "sensitivity1", "sensitivity2", "polling_rate", "sleep_timer", "buttons_mapping"}
)


def _split_profile_name(profile_name: str) -> tuple[str, Connection]:
    match = _MODE_SUFFIX.match(profile_name)
    if match is None:
        return profile_name, Connection.WIRED
    connection = Connection.WIRED if "wired" in match["mode"] else Connection.WIRELESS
    return match["model"], connection


def _plugged_product_ids() -> set[int]:
    product_ids = {info["product_id"] for info in hid.enumerate(VENDOR_ID)}
    # rivalcfg's debug variable pretends a model is plugged in. Together with
    # RIVALCFG_DRY=1 it lets you try another model without owning it.
    debug_profile = os.environ.get("RIVALCFG_PROFILE")
    if debug_profile:
        vendor_id, _, product_id = debug_profile.partition(":")
        if int(vendor_id, 16) == VENDOR_ID:
            product_ids.add(int(product_id, 16))
    return product_ids


def discover() -> list[SteelSeriesMouse]:
    """List plugged-in mice that rivalcfg supports. Only reads the USB device
    list; never opens a device, so it doesn't wake a mouse up."""
    models: dict[str, list[tuple[int, Connection]]] = {}
    has_battery: dict[str, bool] = {}
    for product_id in _plugged_product_ids():
        try:
            profile = rivalcfg_devices.get_profile(VENDOR_ID, product_id)
        except rivalcfg_devices.UnsupportedDevice:
            continue  # keyboards, headsets, and mice rivalcfg doesn't know
        model, connection = _split_profile_name(profile["name"])
        models.setdefault(model, []).append((product_id, connection))
        has_battery[model] = has_battery.get(model, False) or "battery_level" in profile
    return [SteelSeriesMouse(model, ids, has_battery[model]) for model, ids in models.items()]


def _read_raw_battery(product_id: int) -> dict:
    """Open the mouse at one product ID and return rivalcfg's raw
    ``{"is_charging": ..., "level": ...}`` dict. Always closes the HID handle
    again."""
    try:
        mouse = get_mouse(product_id=product_id, vendor_id=VENDOR_ID)
    except DeviceNotFound as exc:
        raise BatteryReadError(f"device not found: {exc}") from exc
    except (OSError, IOError) as exc:
        raise BatteryReadError(
            f"could not open device (permissions? udev rules missing?): {exc}",
            _OPEN_FAILED_MESSAGE,
        ) from exc

    try:
        return mouse.battery
    except Exception as exc:  # noqa: BLE001 - HID errors surface as plain Exception
        raise BatteryReadError(f"failed to read battery report: {exc}") from exc
    finally:
        close = getattr(mouse, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


def _open_mouse_for_settings(product_id: int):
    """Like _read_raw_battery's open step, but raising SettingsWriteError
    instead of BatteryReadError so the Settings tab gets its own wording."""
    try:
        return get_mouse(product_id=product_id, vendor_id=VENDOR_ID)
    except DeviceNotFound as exc:
        raise SettingsWriteError(f"device not found: {exc}", _ASLEEP_MESSAGE) from exc
    except (OSError, IOError) as exc:
        raise SettingsWriteError(
            f"could not open device (permissions? udev rules missing?): {exc}",
            _OPEN_FAILED_MESSAGE,
        ) from exc


def _parse_dpi_list(raw: str) -> list[int]:
    return [int(part) for part in raw.split(",") if part.strip()]


def _parse_dpi_pairs(raw: str) -> list[tuple[int, int]]:
    pairs = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            x, y = part.split(":", 1)
        else:
            x = y = part
        pairs.append((int(x), int(y)))
    return pairs


def _button_actions(info: dict) -> list[ButtonAction]:
    """Every value offered in a button's dropdown: the model's special
    actions, its other physical buttons, media keys, then keyboard keys."""
    actions: list[ButtonAction] = []
    if info.get("button_disable") is not None:
        actions.append(ButtonAction("Disabled", "disabled", "Action"))
    if info.get("button_dpi_switch") is not None:
        actions.append(ButtonAction("Cycle DPI presets", "dpi", "Action"))
    if info.get("button_scroll_up") is not None:
        actions.append(ButtonAction("Scroll wheel up", "scrollup", "Action"))
    if info.get("button_scroll_down") is not None:
        actions.append(ButtonAction("Scroll wheel down", "scrolldown", "Action"))
    for button_name in info["buttons"]:
        actions.append(ButtonAction(button_name, button_name.lower(), "Mouse button"))
    for key_label in layout_multimedia.layout:
        actions.append(ButtonAction(key_label, key_label.lower(), "Media key"))
    if info.get("button_keyboard") is not None:
        for key_label in layout_qwerty.layout:
            actions.append(ButtonAction(key_label, key_label.lower(), "Keyboard key"))
    return actions


def _buttons_default(info: dict) -> dict[str, str]:
    """The default mapping, as {lowercase button name: lowercase action}.
    Parsed from the profile's DSL default string, with each profile's own
    per-button default filling in anything the string doesn't mention."""
    mapping: dict[str, str] = {}
    try:
        parsed = parse_param_string(info["default"])
        mapping = {
            key.lower(): value.lower()
            for key, value in parsed.get("buttons", {}).items()
            if key.lower() != "layout"
        }
    except ValueError:
        pass
    for button_name, button_info in info["buttons"].items():
        mapping.setdefault(button_name.lower(), str(button_info.get("default", "disabled")).lower())
    return mapping


def _read_cache_file(product_id: int) -> dict | None:
    """The raw {setting_name: value} dict rivalcfg last saved for this
    product ID, or None when there's no cache file yet. Deliberately doesn't
    go through rivalcfg's own MouseSettings.get(): that class fills in gaps
    with the profile's *raw* defaults (a DSL string for buttons, a
    comma-separated string for DPI presets), which don't match the typed
    values settings_schema() promises. _normalize_saved_value() handles a
    genuinely-saved value instead; an absent key falls back to the field's
    own (already-typed) default in get_settings()."""
    path = mouse_settings.get_settings_path(VENDOR_ID, product_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return None
    profile_settings = data.get("default")
    return profile_settings if isinstance(profile_settings, dict) else None


def _normalize_saved_value(field: SettingField, raw_value: object) -> object:
    """Converts a value read back from the cache file into the shape
    settings_schema() promises for this field (see _read_cache_file)."""
    if isinstance(field, ButtonsSetting):
        if isinstance(raw_value, dict) and isinstance(raw_value.get("buttons"), dict):
            mapping = {
                key.lower(): str(mapped).lower()
                for key, mapped in raw_value["buttons"].items()
                if key.lower() != "layout"
            }
            for button_name in field.buttons:
                mapping.setdefault(button_name.lower(), field.default.get(button_name.lower(), "disabled"))
            return mapping
        return dict(field.default)
    if isinstance(field, DpiPresetsSetting) and isinstance(raw_value, list):
        if field.axes_linked:
            return [int(item) for item in raw_value]
        return [tuple(pair) for pair in raw_value]
    return raw_value


def _build_field(name: str, info: dict) -> SettingField | None:
    value_type = info.get("value_type")
    label = info.get("label", name)

    if value_type in ("range", "range_choice"):
        minimum, maximum, step = info["input_range"]
        return RangeSetting(name, label, minimum, maximum, step, info["default"])

    if value_type == "choice":
        keys = sorted(info["choices"].keys(), key=lambda v: v if isinstance(v, int) else -1)
        unit = " Hz" if name == "polling_rate" else ""
        options = [(f"{key}{unit}", key) for key in keys]
        return ChoiceSetting(name, label, options, info["default"])

    if value_type in ("multidpi_range", "multidpi_range_choice"):
        minimum, maximum, step = info["input_range"]
        return DpiPresetsSetting(
            name, label, minimum, maximum, step, info["max_preset_count"], True, _parse_dpi_list(info["default"])
        )

    if value_type == "multidpi_range_choice_xy":
        minimum, maximum, step = info["input_range"]
        return DpiPresetsSetting(
            name,
            label,
            minimum,
            maximum,
            step,
            info["max_preset_count"],
            False,
            _parse_dpi_pairs(info["default"]),
        )

    if value_type == "buttons":
        buttons = list(info["buttons"].keys())
        primary = next((button for button in buttons if button.lower() == "button1"), None)
        return ButtonsSetting(name, label, buttons, _button_actions(info), primary, _buttons_default(info))

    return None  # a value_type we don't build a widget for (skipped defensively)


class SteelSeriesMouse(Device):
    # Each read wakes the mouse's radio, so don't read more often than this.
    poll_interval_seconds = 60
    # A failed read (mouse asleep, out of range, busy) retries sooner.
    retry_interval_seconds = 10

    def __init__(self, model: str, product_ids: list[tuple[int, Connection]], has_battery: bool):
        # Try the receiver before the cable, as the original app did.
        self.product_ids = sorted(
            product_ids, key=lambda item: (item[1] is not Connection.WIRELESS, item[0])
        )
        # "SteelSeries Rival 650 Wireless" -> id "steelseries:rival-650-wireless"
        short_name = re.sub(r"^steelseries\s+", "", model, flags=re.IGNORECASE)
        slug = re.sub(r"[^a-z0-9]+", "-", short_name.lower()).strip("-")
        super().__init__(
            id=f"steelseries:{slug}",
            name=model,
            kind=DeviceKind.MOUSE,
            connection=self.product_ids[0][1],
            has_battery=has_battery,
        )

    @property
    def hardware_key(self) -> object:
        return tuple(self.product_ids)

    def read_battery(self) -> BatteryStatus:
        errors = []
        user_message = _ASLEEP_MESSAGE
        for product_id, connection in self.product_ids:
            try:
                raw = _read_raw_battery(product_id)
            except BatteryReadError as exc:
                errors.append(f"0x{product_id:04x}: {exc}")
                if exc.user_message == _OPEN_FAILED_MESSAGE:
                    user_message = _OPEN_FAILED_MESSAGE
                continue

            level = raw.get("level")
            if level is None:
                # The receiver answered without data: usually it's plugged
                # in but the mouse itself is off or asleep.
                errors.append(f"0x{product_id:04x}: mouse did not report a battery level")
                continue

            self.connection = connection
            return BatteryStatus(
                percentage=int(level),
                coarse_level=None,
                is_charging=bool(raw.get("is_charging")),
                read_at=time.time(),
            )

        raise BatteryReadError("; ".join(errors), user_message)

    # -- Settings ---------------------------------------------------------

    def _active_product_id(self) -> int:
        """The product ID for the connection currently in use, so settings
        are read from and written to the right rivalcfg cache file."""
        for product_id, connection in self.product_ids:
            if connection is self.connection:
                return product_id
        return self.product_ids[0][0]

    def _profile(self) -> dict:
        return rivalcfg_devices.get_profile(VENDOR_ID, self._active_product_id())

    def settings_schema(self) -> list[SettingField]:
        profile = self._profile()
        fields = []
        for name, info in profile["settings"].items():
            if name not in _SUPPORTED_SETTINGS:
                continue
            field = _build_field(name, info)
            if field is not None:
                fields.append(field)
        return fields

    def get_settings(self) -> SettingsValues:
        raw = _read_cache_file(self._active_product_id())
        values = {}
        # from_cache stays True only if every field came from a real save;
        # one missing field (a partial or stale cache file) is enough to
        # call the whole panel "not yet saved from this app".
        from_cache = raw is not None
        for field in self.settings_schema():
            if raw is not None and field.name in raw:
                values[field.name] = _normalize_saved_value(field, raw[field.name])
            else:
                values[field.name] = field.default
                from_cache = False
        return SettingsValues(values=values, from_cache=from_cache)

    def apply_settings(self, values: dict) -> SettingsValues:
        schema = {field.name: field for field in self.settings_schema()}
        for name, field in schema.items():
            if not isinstance(field, ButtonsSetting) or field.primary_button is None:
                continue
            mapping = values.get(name)
            if not mapping:
                continue
            if mapping.get(field.primary_button.lower()) == "disabled":
                raise SettingsWriteError(
                    f"refusing to leave {field.primary_button} disabled",
                    f"{field.primary_button} can't be disabled - you would lose the ability to click.",
                )

        mouse = _open_mouse_for_settings(self._active_product_id())
        try:
            for name, value in values.items():
                field = schema.get(name)
                if field is None:
                    continue
                setter = getattr(mouse, f"set_{name}")
                try:
                    if isinstance(field, ButtonsSetting):
                        setter({"buttons": dict(value)})
                    else:
                        setter(value)
                except SettingsWriteError:
                    raise
                except Exception as exc:  # noqa: BLE001 - bad value or HID error
                    raise SettingsWriteError(
                        f"failed to set {name} to {value!r}: {exc}",
                        f"Couldn't set {field.label}: {exc}",
                    ) from exc
            mouse.save()
        finally:
            mouse.close()
        return self.get_settings()

    def reset_settings(self) -> SettingsValues:
        mouse = _open_mouse_for_settings(self._active_product_id())
        try:
            try:
                mouse.reset_settings()
                mouse.save()
            except Exception as exc:  # noqa: BLE001 - HID error mid-reset
                raise SettingsWriteError(f"failed to reset settings: {exc}") from exc
        finally:
            mouse.close()
        return self.get_settings()
