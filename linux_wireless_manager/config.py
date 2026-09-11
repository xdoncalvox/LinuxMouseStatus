"""App-wide constants. Per-device polling intervals live on each device
backend (see devices/base.py).
"""

APP_ID = "linux-wireless-manager"
APP_NAME = "Linux Wireless Manager"

# How often to check for newly connected or removed devices, in seconds.
# Detection only lists USB devices and asks UPower; it never talks to a
# device, so it's cheap and doesn't wake anything up.
DISCOVERY_INTERVAL_SECONDS = 10

# Stop logging a device's read failures after this many in a row, since
# "mouse asleep" is a common, expected state.
MAX_CONSECUTIVE_FAILURES_BEFORE_QUIET = 3
