#!/usr/bin/env bash
# Installs Linux Wireless Manager on Ubuntu.
#
# What this does:
#   1. Installs the system packages needed for the GTK tray icons, for
#      talking to devices over USB/HID, and UPower (which reports Logitech
#      and Bluetooth device batteries).
#   2. Creates a Python virtualenv (with access to the system PyGObject
#      packages, since those aren't reliably installable via pip) and
#      installs the Python dependencies into it.
#   3. Installs a udev rule (via rivalcfg) so SteelSeries mice can be read
#      without running as root.
#   4. Registers the app in your application menu and, optionally, to start
#      automatically when you log in. Entries left over from the app's old
#      name, "SteelSeries Battery Monitor", are replaced.
#
# Safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
APP_ID="linux-wireless-manager"
OLD_APP_ID="steelseries-battery-monitor"

echo "==> Installing system packages (requires sudo)..."
sudo apt update
sudo apt install -y \
    python3 python3-venv python3-dev \
    python3-gi gir1.2-gtk-3.0 gir1.2-notify-0.7 \
    build-essential libusb-1.0-0-dev libudev-dev libhidapi-hidraw0 \
    upower

# The AppIndicator GObject bindings package is named differently depending
# on the Ubuntu release. Try the modern (Ayatana) one first, then the
# legacy one; don't fail the whole install if one of the two is missing
# from the repos.
if apt-cache show gir1.2-ayatanaappindicator3-0.1 >/dev/null 2>&1; then
    sudo apt install -y gir1.2-ayatanaappindicator3-0.1
elif apt-cache show gir1.2-appindicator3-0.1 >/dev/null 2>&1; then
    sudo apt install -y gir1.2-appindicator3-0.1
else
    echo "WARNING: could not find an AppIndicator3 GI package to install." >&2
    echo "         The tray icon may not work; see README.md for alternatives." >&2
fi

echo "==> Creating virtualenv at $VENV_DIR ..."
# --system-site-packages so the venv can see the apt-installed PyGObject /
# GTK / AppIndicator bindings above, which are not pip-installable.
python3 -m venv --system-site-packages "$VENV_DIR"

echo "==> Installing Python dependencies into the virtualenv..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "==> Installing udev rule so SteelSeries mice can be read without root..."
sudo "$VENV_DIR/bin/rivalcfg" --update-udev

chmod +x "$SCRIPT_DIR/run.sh"

echo "==> Registering application menu entry..."
APPS_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"

# Replace entries from the old name, remembering whether it autostarted.
HAD_OLD_AUTOSTART=no
if [[ -f "$AUTOSTART_DIR/$OLD_APP_ID.desktop" ]]; then
    HAD_OLD_AUTOSTART=yes
fi
rm -f "$APPS_DIR/$OLD_APP_ID.desktop" "$AUTOSTART_DIR/$OLD_APP_ID.desktop"

mkdir -p "$APPS_DIR"
sed "s|__EXEC__|$SCRIPT_DIR/run.sh|; s|__ICON__|input-mouse|" \
    "$SCRIPT_DIR/$APP_ID.desktop.in" > "$DESKTOP_FILE"

if [[ "$HAD_OLD_AUTOSTART" == yes || -f "$AUTOSTART_DIR/$APP_ID.desktop" ]]; then
    AUTOSTART_ANSWER=Y
    echo "    Autostart was already enabled; keeping it."
else
    read -r -p "Start automatically when you log in? [Y/n] " AUTOSTART_ANSWER
    AUTOSTART_ANSWER="${AUTOSTART_ANSWER:-Y}"
fi
if [[ "$AUTOSTART_ANSWER" =~ ^[Yy] ]]; then
    mkdir -p "$AUTOSTART_DIR"
    cp "$DESKTOP_FILE" "$AUTOSTART_DIR/$APP_ID.desktop"
    echo "    Autostart enabled (edit/remove $AUTOSTART_DIR/$APP_ID.desktop to change)."
fi

cat <<EOF

==> Done.

The mouse's USB receiver needs to be unplugged and replugged once (or you can
just reboot) for the new udev permission rule to take effect.

Run it now with:
    $SCRIPT_DIR/run.sh

Or find "Linux Wireless Manager" in your application menu.
EOF
