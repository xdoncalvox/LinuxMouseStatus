"""Battery-powered mice and keyboards reported by UPower.

The kernel exposes batteries for many wireless peripherals: Logitech devices
on a Unifying, Bolt or Lightspeed receiver (through the hid-logitech-hidpp
driver) and Bluetooth mice and keyboards. UPower collects them, and we read
its cached values over D-Bus, so this never wakes a device up.

The numeric codes below are UPower's UpDeviceKind, UpDeviceState and
UpDeviceLevel enums.

Settings (Step 3b) go through the Solaar CLI (see solaar.py), matched to
this device by the serial number UPower also reports. Only Logitech
receiver devices Solaar manages will ever have any; Bluetooth devices and
anything Solaar doesn't recognise just get an empty settings_schema(), the
same as before Step 3b existed.
"""

from __future__ import annotations

import re
import time

from gi.repository import Gio, GLib

from . import solaar
from .base import (
    BatteryReadError,
    BatteryStatus,
    CoarseLevel,
    Connection,
    Device,
    DeviceKind,
    SettingField,
    SettingsValues,
    SettingsWriteError,
)

_BUS_NAME = "org.freedesktop.UPower"
_MANAGER_PATH = "/org/freedesktop/UPower"
_DEVICE_INTERFACE = "org.freedesktop.UPower.Device"
_TIMEOUT_MS = 2000

_KINDS = {5: DeviceKind.MOUSE, 6: DeviceKind.KEYBOARD}
_STATE_UNKNOWN = 0
_STATE_CHARGING = 1
# BatteryLevel is NONE (1) for devices that report a percentage instead.
_COARSE_LEVELS = {
    3: CoarseLevel.LOW,
    4: CoarseLevel.CRITICAL,
    6: CoarseLevel.NORMAL,
    7: CoarseLevel.HIGH,
    8: CoarseLevel.FULL,
}

# Kernel HID batteries of Bluetooth devices are named after the MAC address.
_BLUETOOTH_NATIVE_PATH = re.compile(r"^hid-([0-9a-f]{2}:){5}[0-9a-f]{2}", re.IGNORECASE)


def _call(bus, path, interface, method, args, reply_type):
    reply = bus.call_sync(
        _BUS_NAME,
        path,
        interface,
        method,
        args,
        GLib.VariantType(reply_type),
        Gio.DBusCallFlags.NONE,
        _TIMEOUT_MS,
        None,
    )
    return reply.unpack()


def _properties(bus, path) -> dict:
    (props,) = _call(
        bus,
        path,
        "org.freedesktop.DBus.Properties",
        "GetAll",
        GLib.Variant("(s)", (_DEVICE_INTERFACE,)),
        "(a{sv})",
    )
    return props


def discover() -> list[UPowerDevice]:
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    (paths,) = _call(bus, _MANAGER_PATH, _BUS_NAME, "EnumerateDevices", None, "(ao)")
    found = []
    for path in paths:
        props = _properties(bus, path)
        kind = _KINDS.get(props.get("Type"))
        # PowerSupply is true for batteries that power the computer itself.
        if kind is None or props.get("PowerSupply") or not props.get("IsPresent", True):
            continue
        found.append(UPowerDevice(bus, path, props, kind))
    return found


def _connection(native_path: str) -> Connection:
    # Bluetooth devices appear as /org/bluez/... (BlueZ battery service) or as
    # a kernel HID battery named after the MAC address. Any other peripheral
    # UPower lists came through a USB receiver.
    if native_path.startswith("/org/bluez") or _BLUETOOTH_NATIVE_PATH.match(native_path):
        return Connection.BLUETOOTH
    return Connection.WIRELESS


def _display_name(props: dict, kind: DeviceKind) -> str:
    vendor = props.get("Vendor", "").strip()
    model = props.get("Model", "").strip()
    if not model:
        return f"Wireless {kind.value}"
    if vendor and not model.lower().startswith(vendor.lower()):
        return f"{vendor} {model}"
    return model


class UPowerDevice(Device):
    # Reading UPower never touches the device, so it can be checked often.
    poll_interval_seconds = 30
    retry_interval_seconds = 30

    def __init__(self, bus: Gio.DBusConnection, path: str, props: dict, kind: DeviceKind):
        native_path = props.get("NativePath") or path
        super().__init__(
            id=f"upower:{native_path}",
            name=_display_name(props, kind),
            kind=kind,
            connection=_connection(native_path),
            has_battery=True,
        )
        self._bus = bus
        self._path = path
        # Used to find this device again through the Solaar CLI (see
        # solaar.py). None for devices UPower doesn't report a serial for.
        self._serial = (props.get("Serial") or "").strip() or None

    @property
    def hardware_key(self) -> object:
        return self._path

    def read_battery(self) -> BatteryStatus:
        try:
            props = _properties(self._bus, self._path)
        except GLib.Error as exc:
            raise BatteryReadError(
                f"UPower: {exc.message}",
                "Couldn't get this device's battery from UPower.",
            ) from exc

        coarse_level = _COARSE_LEVELS.get(props.get("BatteryLevel"))
        state = props.get("State", _STATE_UNKNOWN)
        percentage = None if coarse_level else round(props.get("Percentage", 0.0))
        if coarse_level is None and state == _STATE_UNKNOWN and percentage == 0:
            raise BatteryReadError(
                "UPower has no reading for this device yet",
                "Waiting for the first battery reading from this device.",
            )
        return BatteryStatus(
            percentage=percentage,
            coarse_level=coarse_level,
            is_charging=state == _STATE_CHARGING,
            read_at=time.time(),
        )

    # -- Settings (Step 3b, Logitech only, through Solaar) ----------------

    def settings_schema(self) -> list[SettingField]:
        return solaar.settings_schema(self._serial)

    def get_settings(self) -> SettingsValues:
        return solaar.get_settings(self._serial)

    def apply_settings(self, values: dict) -> SettingsValues:
        return solaar.apply_settings(self._serial, values)

    def reset_settings(self) -> SettingsValues:
        # Solaar's CLI has no reset-to-factory-defaults action (only
        # show/config/pair/unpair/profiles) - see solaar.py.
        raise SettingsWriteError(
            "solaar has no reset-to-defaults command",
            "Solaar doesn't support resetting a device to its factory defaults.",
        )
