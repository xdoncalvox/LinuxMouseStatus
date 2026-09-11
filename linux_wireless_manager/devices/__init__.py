"""Device backends. Each backend module has a discover() that returns the
devices it can see right now; discover_devices() combines them.
"""

from __future__ import annotations

import logging

from .. import config
from . import steelseries, upower
from .base import (
    BatteryReadError,
    BatteryStatus,
    CoarseLevel,
    Connection,
    Device,
    DeviceKind,
    DeviceState,
)

__all__ = [
    "BatteryReadError",
    "BatteryStatus",
    "CoarseLevel",
    "Connection",
    "Device",
    "DeviceKind",
    "DeviceState",
    "discover_devices",
]

logger = logging.getLogger(config.APP_ID)

_BACKENDS = (steelseries, upower)
_failing_backends: set[str] = set()


def discover_devices() -> list[Device]:
    """Called from the polling thread every few seconds. Never talks to a
    device: backends only list USB devices and ask UPower."""
    found: list[Device] = []
    for backend in _BACKENDS:
        name = backend.__name__.rsplit(".", 1)[-1]
        try:
            found.extend(backend.discover())
        except Exception as exc:  # noqa: BLE001 - one broken backend mustn't hide the others
            # Log once per outage rather than on every detection run.
            if name not in _failing_backends:
                logger.warning("Device detection via %s failed: %s", name, exc)
                _failing_backends.add(name)
        else:
            _failing_backends.discard(name)
    return found
