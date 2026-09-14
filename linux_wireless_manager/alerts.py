"""Low-battery and fully-charged notifications.

update() is called with every DeviceState snapshot, on the main thread. Each
threshold fires once when the level first drops past it, and only re-arms
after the device charges or climbs back above the threshold, so a reading
hovering around 20% doesn't notify over and over.

Devices that only report a coarse level are handled by the same numbers
through BatteryStatus.estimated_percentage: "Low" counts as 20% and
"Critical" as 5%, so they trigger the default 20% and 10% thresholds.

Notifications are optional: if the libnotify bindings are missing, the app
still runs and simply doesn't notify.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Callable, Iterable

from gi.repository import GLib

from . import config, icons
from .devices import DeviceState

logger = logging.getLogger(config.APP_ID)

_ALERTS_KEY = "alerts"
# How far a device must climb back above a threshold before it can fire again.
_REARM_MARGIN = 5
DEFAULT_THRESHOLDS = (20, 10)
MIN_THRESHOLD = 1
MAX_THRESHOLD = 99


def _load_notify():
    try:
        import gi

        gi.require_version("Notify", "0.7")
        from gi.repository import Notify
    except (ImportError, ValueError) as exc:
        logger.warning(
            "Desktop notifications unavailable (install gir1.2-notify-0.7): %s", exc
        )
        return None
    if not Notify.is_initted() and not Notify.init(config.APP_NAME):
        logger.warning("Couldn't initialise desktop notifications")
        return None
    return Notify


def clean_thresholds(values: Iterable[int]) -> tuple[int, ...]:
    """Clamp thresholds to 1-99 and sort them highest first."""
    cleaned = tuple(
        sorted((min(MAX_THRESHOLD, max(MIN_THRESHOLD, int(v))) for v in values), reverse=True)
    )
    return cleaned or DEFAULT_THRESHOLDS


@dataclasses.dataclass(frozen=True)
class AlertSettings:
    enabled: bool = True
    thresholds: tuple[int, ...] = DEFAULT_THRESHOLDS
    notify_charged: bool = True

    @classmethod
    def from_dict(cls, raw) -> AlertSettings:
        """Read one device's settings from the preferences file, ignoring
        anything invalid."""
        if not isinstance(raw, dict):
            return cls()
        thresholds = raw.get("thresholds")
        if not isinstance(thresholds, list) or not all(
            isinstance(value, int) for value in thresholds
        ):
            thresholds = list(DEFAULT_THRESHOLDS)
        return cls(
            enabled=bool(raw.get("enabled", True)),
            thresholds=clean_thresholds(thresholds),
            notify_charged=bool(raw.get("notify_charged", True)),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "thresholds": list(self.thresholds),
            "notify_charged": self.notify_charged,
        }


@dataclasses.dataclass
class _AlertState:
    """What has already been notified for one device."""

    fired: set[int] = dataclasses.field(default_factory=set)
    charged_notified: bool = False
    was_charging: bool = False
    seen: bool = False


class AlertManager:
    def __init__(
        self,
        preferences,
        *,
        icon_dir: str | None = None,
        on_show_device: Callable[[str], None] | None = None,
    ):
        self._preferences = preferences
        self._icon_dir = icon_dir
        self._on_show_device = on_show_device
        self._notify = _load_notify()
        self._server_caps: set[str] = set()
        if self._notify is not None:
            try:
                self._server_caps = set(self._notify.get_server_caps() or [])
            except Exception as exc:  # noqa: BLE001 - the daemon may be absent
                logger.warning("Couldn't ask the notification server what it supports: %s", exc)

        stored = preferences.get(_ALERTS_KEY)
        self._settings: dict[str, AlertSettings] = (
            {device_id: AlertSettings.from_dict(raw) for device_id, raw in stored.items()}
            if isinstance(stored, dict)
            else {}
        )
        self._device_states: dict[str, _AlertState] = {}
        # Keep references: a garbage-collected notification loses its action.
        self._shown: dict[str, object] = {}

    @property
    def available(self) -> bool:
        return self._notify is not None

    # -- Settings -------------------------------------------------------

    def settings_for(self, device_id: str) -> AlertSettings:
        return self._settings.get(device_id, AlertSettings())

    def set_settings(self, device_id: str, settings: AlertSettings) -> None:
        self._settings[device_id] = settings
        self._preferences.set(
            _ALERTS_KEY, {key: value.to_dict() for key, value in self._settings.items()}
        )
        # Start over, so new thresholds can fire even if the old ones did.
        self._device_states.pop(device_id, None)

    # -- Reacting to readings -------------------------------------------

    def update(self, states: list[DeviceState]) -> None:
        live = {state.id for state in states}
        for device_id in list(self._device_states):
            if device_id not in live:
                del self._device_states[device_id]
                self._shown.pop(device_id, None)
        for state in states:
            if state.has_battery and state.battery is not None and state.error is None:
                self._check(state)

    def _check(self, state: DeviceState) -> None:
        settings = self.settings_for(state.id)
        tracked = self._device_states.setdefault(state.id, _AlertState())
        level = state.battery.estimated_percentage
        charging = state.battery.is_charging

        # Re-arm thresholds the device climbed back above, and all of them
        # while it is charging.
        for threshold in list(tracked.fired):
            if charging or level >= threshold + _REARM_MARGIN:
                tracked.fired.discard(threshold)

        if settings.enabled and not charging:
            crossed = {threshold for threshold in settings.thresholds if level <= threshold}
            if crossed and not crossed <= tracked.fired:
                self._notify_low(state, min(crossed), settings)
                tracked.fired |= crossed

        started_charging = charging and not tracked.was_charging
        if started_charging or level < 100:
            tracked.charged_notified = False
        if (
            settings.notify_charged
            and tracked.seen  # don't announce a full battery at startup
            and level >= 100
            and (charging or tracked.was_charging)
            and not tracked.charged_notified
        ):
            self._notify_charged(state)
            tracked.charged_notified = True

        tracked.was_charging = charging
        tracked.seen = True

    # -- Showing notifications ------------------------------------------

    def _notify_low(self, state: DeviceState, threshold: int, settings: AlertSettings) -> None:
        worst = threshold <= min(settings.thresholds)
        level = "critically low" if worst else "low"
        self._show(
            state,
            f"{state.name} battery {level}",
            f"{state.battery.describe()} remaining.",
            urgent=worst,
        )

    def _notify_charged(self, state: DeviceState) -> None:
        self._show(state, f"{state.name} is fully charged", "You can unplug it now.", urgent=False)

    def _show(self, state: DeviceState, summary: str, body: str, *, urgent: bool) -> None:
        if self._notify is None:
            return
        icon = icons.icon_name(state.kind, state.battery, custom=self._icon_dir is not None)
        if self._icon_dir is not None:
            icon = os.path.join(self._icon_dir, f"{icon}.svg")
        try:
            previous = self._shown.pop(state.id, None)
            if previous is not None:
                previous.close()  # replace this device's earlier notification
            notification = self._notify.Notification.new(summary, body, icon)
            notification.set_urgency(
                self._notify.Urgency.CRITICAL if urgent else self._notify.Urgency.NORMAL
            )
            # Lets the shell attribute the notification to this app.
            notification.set_hint("desktop-entry", GLib.Variant("s", config.APP_ID))
            if self._on_show_device is not None and "actions" in self._server_caps:
                notification.add_action(
                    "show-details",
                    "Show Details",
                    lambda *_args, device_id=state.id: self._on_show_device(device_id),
                )
            notification.show()
            self._shown[state.id] = notification
        except Exception as exc:  # noqa: BLE001 - a failed notification mustn't stop polling
            logger.warning("Couldn't show notification for %s: %s", state.name, exc)
        else:
            logger.info("Notified: %s - %s", summary, body)
