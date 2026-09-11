"""The interface every device backend implements, and the value types that
travel from the polling thread to the UI.

Device objects live only in the polling thread (see monitor.py). The UI only
ever sees DeviceState, an immutable snapshot, so no GTK code touches a device
and no device code touches GTK.
"""

from __future__ import annotations

import abc
import dataclasses
import enum


class DeviceKind(enum.Enum):
    MOUSE = "mouse"
    KEYBOARD = "keyboard"


class Connection(enum.Enum):
    WIRELESS = "wireless receiver"
    WIRED = "USB cable"
    BLUETOOTH = "Bluetooth"


class CoarseLevel(enum.Enum):
    """Battery level for devices that report a rough level, not a percentage."""

    CRITICAL = "Critical"
    LOW = "Low"
    NORMAL = "Normal"
    HIGH = "High"
    FULL = "Full"


# Stand-in percentages so coarse-only devices can still pick an icon and be
# compared with devices that report a percentage.
_COARSE_ESTIMATES = {
    CoarseLevel.CRITICAL: 5,
    CoarseLevel.LOW: 20,
    CoarseLevel.NORMAL: 55,
    CoarseLevel.HIGH: 80,
    CoarseLevel.FULL: 100,
}


@dataclasses.dataclass(frozen=True)
class BatteryStatus:
    percentage: int | None  # 0-100, or None when only coarse_level is known
    coarse_level: CoarseLevel | None
    is_charging: bool
    read_at: float  # time.time() of the successful read

    def __post_init__(self):
        if self.percentage is None and self.coarse_level is None:
            raise ValueError("BatteryStatus needs a percentage or a coarse level")

    @property
    def estimated_percentage(self) -> int:
        if self.percentage is not None:
            return self.percentage
        return _COARSE_ESTIMATES[self.coarse_level]

    def describe(self) -> str:
        """Short text such as "42%" or "Low"."""
        if self.percentage is not None:
            return f"{self.percentage}%"
        return self.coarse_level.value


class BatteryReadError(Exception):
    """The battery couldn't be read right now: the device is off, asleep, out
    of range, or couldn't be opened. This is expected, not a bug; the UI shows
    it as "unknown right now".

    str(exc) is the technical detail for the log; user_message is what the
    window shows.
    """

    def __init__(
        self,
        detail: str,
        user_message: str = "The device didn't respond. It may be turned off or asleep.",
    ):
        super().__init__(detail)
        self.user_message = user_message


class Device(abc.ABC):
    """One physical device, created by a backend's discover()."""

    # Seconds until the next battery read after a successful / failed read.
    poll_interval_seconds: int = 60
    retry_interval_seconds: int = 10

    def __init__(
        self,
        *,
        id: str,  # noqa: A002 - stable across discovery runs, e.g. "steelseries:rival-650-wireless"
        name: str,
        kind: DeviceKind,
        connection: Connection,
        has_battery: bool,
    ):
        self.id = id
        self.name = name
        self.kind = kind
        # May change after a read, e.g. when a SteelSeries mouse answers over
        # its cable instead of its receiver.
        self.connection = connection
        self.has_battery = has_battery

    @property
    def hardware_key(self) -> object:
        """Changes when the same device shows up on different hardware (for
        example a cable was plugged in), which tells the monitor to replace
        the object and read it again straight away."""
        return self.id

    @abc.abstractmethod
    def read_battery(self) -> BatteryStatus:
        """Blocking read, called from the polling thread and only when
        has_battery is True. Raises BatteryReadError for expected failures."""

    # -- Settings: no backend implements these yet (see ROADMAP.md) ------

    def settings_schema(self) -> list:
        return []

    def get_settings(self) -> dict:
        raise NotImplementedError(f"{self.name} has no configurable settings")

    def apply_settings(self, values: dict) -> None:
        raise NotImplementedError(f"{self.name} has no configurable settings")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}>"


@dataclasses.dataclass(frozen=True)
class DeviceState:
    """Immutable snapshot of one device, handed to the UI."""

    id: str
    name: str
    kind: DeviceKind
    connection: Connection
    has_battery: bool
    battery: BatteryStatus | None = None  # last successful reading
    error: str | None = None  # user-facing message if the latest read failed
    last_attempt_at: float | None = None

    @classmethod
    def for_device(cls, device: Device) -> DeviceState:
        return cls(
            id=device.id,
            name=device.name,
            kind=device.kind,
            connection=device.connection,
            has_battery=device.has_battery,
        )
