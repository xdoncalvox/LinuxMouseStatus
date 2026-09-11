"""Static configuration for the SteelSeries Rival 650 Wireless battery monitor.

The USB vendor/product IDs and the battery HID command below come from the
`rivalcfg` project (https://github.com/flozz/rivalcfg), which reverse-engineered
the SteelSeries mouse protocol. We reuse `rivalcfg` as a library rather than
re-implementing the HID handshake ourselves.
"""

# SteelSeries vendor ID (shared by all SteelSeries USB devices).
VENDOR_ID = 0x1038

# The Rival 650 Wireless shows up under a different product ID depending on
# how it is connected. We try each of these, in order, until one responds.
PRODUCT_IDS = {
    "wireless_dongle": 0x1726,  # mouse talking to the USB 2.4GHz dongle
    "wired": 0x172B,  # mouse plugged in directly via USB-C cable
}

# How often to poll the mouse for a fresh battery reading, in seconds.
# The mouse has to wake its radio to answer, so we don't poll too often.
POLL_INTERVAL_SECONDS = 60

# If a poll fails (mouse asleep / out of range / momentarily busy), retry
# sooner than the normal interval, but give up logging errors after this many
# consecutive failures so the tray icon doesn't spam the log.
QUICK_RETRY_SECONDS = 10
MAX_CONSECUTIVE_FAILURES_BEFORE_QUIET = 3

APP_ID = "steelseries-battery-monitor"
APP_NAME = "SteelSeries Battery Monitor"
