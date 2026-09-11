"""Tray indicator for the SteelSeries Rival 650 Wireless battery monitor.

Polls the mouse on a timer, keeps a background thread doing the (blocking)
USB/HID read so the tray icon never freezes, and updates both the indicator
icon/label and the optional detail window from the GTK main thread.
"""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3  # type: ignore[no-redef]

from gi.repository import GLib, Gtk  # noqa: E402

from . import config  # noqa: E402
from .battery import BatteryReadError, read_battery_status  # noqa: E402
from .icons import icon_name_for  # noqa: E402
from .window import BatteryWindow  # noqa: E402

logger = logging.getLogger(config.APP_ID)


class BatteryMonitorApp:
    def __init__(self, show_window_on_start: bool = False):
        self._consecutive_failures = 0
        self._poll_lock = threading.Lock()  # avoid overlapping USB reads
        # GLib source ID of the single pending scheduled poll, or None. Only
        # touched on the GTK main thread.
        self._next_poll_source: int | None = None

        self.indicator = AppIndicator3.Indicator.new(
            config.APP_ID,
            icon_name_for(None, False),
            AppIndicator3.IndicatorCategory.HARDWARE,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title(config.APP_NAME)

        self.window = BatteryWindow(on_refresh_requested=self.poll_now)

        self._battery_menu_item = Gtk.MenuItem(label="Battery: checking...")
        self._battery_menu_item.set_sensitive(False)

        self.indicator.set_menu(self._build_menu())

        if show_window_on_start:
            self.window.show_all()
            self.window.present()

        # Kick off an immediate read. Each subsequent poll is scheduled from
        # its own result (see _apply_status/_apply_error) so that failures
        # retry sooner than the normal interval instead of waiting a full
        # minute to find out the mouse woke back up.
        GLib.idle_add(self.poll_now)

    # -- UI construction -----------------------------------------------

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        menu.append(self._battery_menu_item)
        menu.append(Gtk.SeparatorMenuItem())

        show_item = Gtk.MenuItem(label="Show Details")
        show_item.connect("activate", self._on_show_details)
        menu.append(show_item)

        refresh_item = Gtk.MenuItem(label="Refresh Now")
        refresh_item.connect("activate", lambda *_: self.poll_now())
        menu.append(refresh_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_show_details(self, *_args) -> None:
        self.window.show_all()
        self.window.present()

    def _on_quit(self, *_args) -> None:
        Gtk.main_quit()

    # -- Polling ----------------------------------------------------------

    def poll_now(self) -> bool:
        """Trigger a battery read in a background thread. Must be called on
        the GTK main thread (menu clicks, window button, the scheduled-poll
        timeout). Returns False so it can be used directly as a GLib.idle_add
        one-shot callback.
        """
        if not self._poll_lock.acquire(blocking=False):
            # A read is already in flight; don't pile up threads if the
            # user mashes "Refresh Now". Leave any pending timeout alone:
            # the in-flight read schedules the next poll when its result is
            # applied, so the chain can't die here.
            logger.debug("Read already in flight; ignoring poll request")
            return False

        # This read supersedes whatever poll was scheduled, and its result
        # will schedule the next one, so there is only ever one chain.
        self._cancel_scheduled_poll()
        logger.debug("Reading battery")

        def worker():
            try:
                status = read_battery_status()
            except BatteryReadError as exc:
                GLib.idle_add(self._apply_error, str(exc))
            except Exception as exc:  # noqa: BLE001 - must not kill the poll chain
                # _apply_status/_apply_error are the only places the next
                # poll gets scheduled, so every read has to end in one.
                logger.exception("Unexpected error while reading battery")
                GLib.idle_add(self._apply_error, f"unexpected error: {exc}")
            else:
                GLib.idle_add(
                    self._apply_status,
                    status.level,
                    status.is_charging,
                    status.connection,
                )
            finally:
                self._poll_lock.release()

        threading.Thread(target=worker, daemon=True).start()
        return False

    def _schedule_poll(self, delay_seconds: int) -> None:
        """Schedule the next poll, replacing any pending one. Main thread only."""
        self._cancel_scheduled_poll()
        self._next_poll_source = GLib.timeout_add_seconds(
            delay_seconds, self._on_poll_timeout
        )

    def _cancel_scheduled_poll(self) -> None:
        if self._next_poll_source is not None:
            GLib.source_remove(self._next_poll_source)
            self._next_poll_source = None

    def _on_poll_timeout(self) -> bool:
        # This source is finished once we return False; forget its ID first
        # so poll_now() doesn't try to remove it.
        self._next_poll_source = None
        self.poll_now()
        return False

    # -- Applying results back on the GTK main thread ----------------------

    def _apply_status(self, level: int, is_charging: bool, connection: str) -> bool:
        # Schedule first so an exception in the UI updates below can't break
        # the poll chain.
        self._schedule_poll(config.POLL_INTERVAL_SECONDS)
        self._consecutive_failures = 0

        self.indicator.set_icon_full(icon_name_for(level, is_charging), config.APP_NAME)
        charging_suffix = " (charging)" if is_charging else ""
        # The second argument is a sizing "guide" string so the panel
        # doesn't jiggle as the label text changes length.
        self.indicator.set_label(f"{level}%{charging_suffix}", "100% (charging)")

        label = f"Battery: {level}%{charging_suffix}"
        self._battery_menu_item.set_label(label)

        self.window.update_status(level, is_charging, connection)
        return False

    def _apply_error(self, message: str) -> bool:
        self._schedule_poll(config.QUICK_RETRY_SECONDS)
        self._consecutive_failures += 1

        self.indicator.set_icon_full(icon_name_for(None, False), config.APP_NAME)
        self.indicator.set_label("?", "100% (charging)")
        self._battery_menu_item.set_label("Battery: unavailable")

        friendly = (
            "Mouse not found. Make sure it's powered on and the USB "
            "receiver is plugged in."
        )
        self.window.update_error(friendly)

        # Only log the noisy underlying reason a few times, not every poll,
        # since "mouse asleep" is an extremely common transient state.
        if self._consecutive_failures <= config.MAX_CONSECUTIVE_FAILURES_BEFORE_QUIET:
            logger.info("Battery read failed: %s", message)

        return False
