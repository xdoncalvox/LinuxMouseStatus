"""SteelSeries mice, through rivalcfg (https://github.com/flozz/rivalcfg).

rivalcfg knows the HID protocol and has one profile per USB product ID. A
wireless SteelSeries mouse has a different product ID for each connection
(2.4 GHz receiver or USB cable), so discover() groups the plugged-in IDs by
model name and returns one SteelSeriesMouse per physical mouse.

Every read opens the HID device and closes it again, because holding it open
would block the rivalcfg CLI and other tools.
"""

from __future__ import annotations

import os
import re
import time

from .base import BatteryReadError, BatteryStatus, Connection, Device, DeviceKind

try:
    import hid
    from rivalcfg import devices as rivalcfg_devices
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
