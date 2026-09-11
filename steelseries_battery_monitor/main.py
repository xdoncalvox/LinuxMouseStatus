"""Entry point: `python3 -m steelseries_battery_monitor [--show-window]`."""

from __future__ import annotations

import argparse
import logging
import signal
import sys


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="steelseries-battery-monitor",
        description=(
            "Tray icon + detail window showing the battery level of a "
            "SteelSeries Rival 650 Wireless mouse."
        ),
    )
    parser.add_argument(
        "--show-window",
        action="store_true",
        help="Also open the detail window immediately on startup.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging to stderr.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError):
        print(
            "Missing GTK3 Python bindings (PyGObject).\n"
            "On Ubuntu, install them with:\n"
            "  sudo apt install python3-gi gir1.2-gtk-3.0\n"
            "then re-run install.sh.",
            file=sys.stderr,
        )
        return 1

    try:
        from .app import BatteryMonitorApp
    except (ImportError, ValueError) as exc:
        print(
            "Missing AppIndicator bindings (needed for the tray icon).\n"
            "On Ubuntu, install one of:\n"
            "  sudo apt install gir1.2-ayatanaappindicator3-0.1\n"
            "  sudo apt install gir1.2-appindicator3-0.1\n"
            f"({exc})",
            file=sys.stderr,
        )
        return 1

    app = BatteryMonitorApp(show_window_on_start=args.show_window)  # noqa: F841

    # Let Ctrl+C / SIGTERM stop the GTK main loop cleanly instead of hanging
    # or printing a traceback.
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)

    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
