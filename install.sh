#!/usr/bin/env bash
# Installs the SteelSeries Rival 650 Wireless battery monitor on Ubuntu.
#
# What this does:
#   1. Installs the system packages needed for the GTK tray icon and for
#      talking to the mouse over USB/HID.
#   2. Creates a Python virtualenv (with access to the system PyGObject
#      packages, since those aren't reliably installable via pip) and
#      installs `rivalcfg` into it.
#   3. Installs a udev rule (via rivalcfg) so the mouse can be read without
#      running as root.
#   4. Registers the app in your application menu and, optionally, to start
#      automatically when you log in.
#
# Safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo "==> Installing system packages (requires sudo)..."
sudo apt update
sudo apt install -y \
    python3 python3-venv python3-dev \
    python3-gi gir1.2-gtk-3.0 \
    build-essential libusb-1.0-0-dev libudev-dev libhidapi-hidraw0

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

echo "==> Installing rivalcfg into the virtualenv..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install "rivalcfg>=4.9.1"

echo "==> Installing udev rule so the mouse can be read without root..."
sudo "$VENV_DIR/bin/rivalcfg" --update-udev

chmod +x "$SCRIPT_DIR/run.sh"

echo "==> Registering application menu entry..."
mkdir -p "$HOME/.local/share/applications"
DESKTOP_FILE="$HOME/.local/share/applications/steelseries-battery-monitor.desktop"
sed "s|__EXEC__|$SCRIPT_DIR/run.sh|; s|__ICON__|battery-good-symbolic|" \
    "$SCRIPT_DIR/steelseries-battery-monitor.desktop.in" > "$DESKTOP_FILE"

read -r -p "Start automatically when you log in? [Y/n] " AUTOSTART_ANSWER
AUTOSTART_ANSWER="${AUTOSTART_ANSWER:-Y}"
if [[ "$AUTOSTART_ANSWER" =~ ^[Yy] ]]; then
    mkdir -p "$HOME/.config/autostart"
    cp "$DESKTOP_FILE" "$HOME/.config/autostart/steelseries-battery-monitor.desktop"
    echo "    Autostart enabled (edit/remove ~/.config/autostart/steelseries-battery-monitor.desktop to change)."
fi

cat <<EOF

==> Done.

The mouse's USB dongle needs to be unplugged and replugged once (or you can
just reboot) for the new udev permission rule to take effect.

Run it now with:
    $SCRIPT_DIR/run.sh

Or find "SteelSeries Battery Monitor" in your application menu.
EOF
