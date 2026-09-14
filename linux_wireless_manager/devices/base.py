"""The interface every device backend implements, and the value types that
travel from the polling thread to the UI.

Device objects live only in the polling thread (see monitor.py). The UI only
ever sees DeviceState, an immutable snapshot, so no GTK code touches a device
and no device code touches GTK. Settings work the same way: the UI only ever
sees SettingField/SettingsValues, and reads or writes them through
DeviceMonitor.run_device_task() (see monitor.py), never by touching a Device
directly.
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


class SettingsWriteError(Exception):
    """A settings read or write failed in an expected way (device off,
    asleep, unplugged, or a bad value). Same detail/user_message split as
    BatteryReadError."""

    def __init__(
        self,
        detail: str,
        user_message: str = "Couldn't save the setting. The device may be turned off or asleep.",
    ):
        super().__init__(detail)
        self.user_message = user_message


# -- Settings schema ------------------------------------------------------
#
# A device describes its Settings tab as a list of these fields. Each field
# type carries everything the window needs to build one widget and to
# validate/pass a value back, without the window knowing anything about
# rivalcfg or any other backend's internals.


@dataclasses.dataclass(frozen=True)
class RangeSetting:
    """A single integer between minimum and maximum (sleep timer, or a DPI
    preset on models with a fixed number of presets)."""

    name: str
    label: str
    minimum: int
    maximum: int
    step: int
    default: int


@dataclasses.dataclass(frozen=True)
class ChoiceSetting:
    """One value picked from a fixed list (polling rate)."""

    name: str
    label: str
    options: list[tuple[str, object]]  # (display text, value), in display order
    default: object


@dataclasses.dataclass(frozen=True)
class DpiPresetsSetting:
    """A editable list of 1-max_presets DPI values, used by the models that
    let several DPI steps be cycled with a button. axes_linked is False only
    for the handful of models (e.g. Rival 3 Gen 2) that can set X and Y DPI
    separately; those get an X/Y pair per preset instead of one value."""

    name: str
    label: str
    minimum: int
    maximum: int
    step: int
    max_presets: int
    axes_linked: bool
    default: list[int] | list[tuple[int, int]]


@dataclasses.dataclass(frozen=True)
class ButtonAction:
    """One choice offered in a button's dropdown."""

    label: str
    value: str
    group: str  # "Action", "Mouse button", "Media key" or "Keyboard key"


@dataclasses.dataclass(frozen=True)
class ButtonsSetting:
    """Per-button remapping. `buttons` lists the physical button names in
    profile order; `actions` are the choices offered for all of them.
    `primary_button`, when set, must never be left mapped to "disabled" -
    that would leave the mouse unclickable."""

    name: str
    label: str
    buttons: list[str]
    actions: list[ButtonAction]
    primary_button: str | None
    default: dict[str, str]


SettingField = RangeSetting | ChoiceSetting | DpiPresetsSetting | ButtonsSetting


@dataclasses.dataclass(frozen=True)
class SettingsValues:
    """What get_settings()/apply_settings()/reset_settings() hand back: the
    current value for each of settings_schema()'s fields, keyed by name.

    from_cache is True when these reflect a previous write from this app
    (SteelSeries settings can't be read back from the mouse itself, so
    that's the only record); False means they're just the model's defaults
    because nothing has been saved yet.
    """

    values: dict[str, object]
    from_cache: bool


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

    # -- Settings ---------------------------------------------------------
    #
    # Only settings_schema() is safe to call cheaply and often: it must never
    # talk to the device, only describe the model. get_settings(),
    # apply_settings() and reset_settings() run in the polling thread, behind
    # DeviceMonitor.run_device_task(), and may block on hardware I/O.

    def settings_schema(self) -> list[SettingField]:
        """Fields for the Settings tab, or [] when this device has none."""
        return []

    def get_settings(self) -> SettingsValues:
        """The values to show for settings_schema()'s fields. Only called
        when settings_schema() is non-empty. Raises SettingsWriteError for
        expected failures."""
        raise NotImplementedError(f"{self.name} has no configurable settings")

    def apply_settings(self, values: dict) -> SettingsValues:
        """Write `values` (one entry per settings_schema() field name) to the
        device and return the values now in effect. Raises SettingsWriteError
        for expected failures, including a rejected value."""
        raise NotImplementedError(f"{self.name} has no configurable settings")

    def reset_settings(self) -> SettingsValues:
        """Restore the model's factory defaults and return the values now in
        effect. Raises SettingsWriteError for expected failures."""
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
