"""Battery reading logic for the SteelSeries Rival 650 Wireless.

This is a thin wrapper around the `rivalcfg` library, which already knows the
HID protocol for SteelSeries mice (see https://github.com/flozz/rivalcfg).
We just:

  1. try each known USB product ID for the Rival 650 (wireless dongle, then
     wired) until one opens successfully,
  2. ask for `mouse.battery`, and
  3. translate whatever goes wrong into a single `BatteryReadError` so the
     UI code doesn't need to know about HID/USB internals.
"""

from __future__ import annotations

import dataclasses
import time

from . import config

try:
    from rivalcfg.mouse import get_mouse
    from rivalcfg.usbhid import DeviceNotFound
except ImportError as exc:  # pragma: no cover - surfaced at startup instead
    raise ImportError(
        "The 'rivalcfg' package is required but not installed. "
        "Run install.sh, or `pip install rivalcfg` inside your virtualenv."
    ) from exc


class BatteryReadError(Exception):
    """Raised when the mouse's battery status could not be read.

    This covers the ordinary, expected cases: the mouse is off/asleep, out
    of range of the dongle, not plugged in, or udev permissions are missing.
    It is not necessarily a bug -- the UI should treat it as "unknown right
    now" rather than crash.
    """


@dataclasses.dataclass
class BatteryStatus:
    level: int  # 0-100
    is_charging: bool
    connection: str  # "wireless_dongle" or "wired"
    read_at: float  # time.time() of the successful read


def _try_read(product_id: int) -> dict:
    """Open the mouse at a specific product ID and return its raw
    ``{"is_charging": ..., "level": ...}`` battery dict.

    Raises BatteryReadError on any failure (device absent, no permission,
    HID I/O error, etc). The underlying HID handle is always closed again,
    since holding it open would block the mouse's own configuration tools.
    """
    try:
        mouse = get_mouse(product_id=product_id, vendor_id=config.VENDOR_ID)
    except DeviceNotFound as exc:
        raise BatteryReadError(f"device not found: {exc}") from exc
    except (OSError, IOError) as exc:
        raise BatteryReadError(
            f"could not open device (permissions? udev rules missing?): {exc}"
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


def read_battery_status() -> BatteryStatus:
    """Read the Rival 650 Wireless battery status.

    Tries the wireless-dongle product ID first (the common case for this
    model), then falls back to the wired product ID in case the mouse is
    plugged in directly. Raises BatteryReadError if neither works.
    """
    errors = []
    for connection, product_id in config.PRODUCT_IDS.items():
        try:
            raw = _try_read(product_id)
        except BatteryReadError as exc:
            errors.append(f"{connection} (0x{product_id:04x}): {exc}")
            continue

        level = raw.get("level")
        is_charging = raw.get("is_charging")
        if level is None:
            # Device responded but reported "no data" -- typically means the
            # mouse itself is powered off even though the dongle is present.
            errors.append(
                f"{connection} (0x{product_id:04x}): mouse did not report a "
                "battery level (it may be turned off or asleep)"
            )
            continue

        return BatteryStatus(
            level=int(level),
            is_charging=bool(is_charging),
            connection=connection,
            read_at=time.time(),
        )

    raise BatteryReadError("; ".join(errors) or "no SteelSeries mouse found")
