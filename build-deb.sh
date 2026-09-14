#!/usr/bin/env bash
# Builds an installable .deb from this repo, without installing it anywhere.
#
# The package is Architecture: all - it ships this app's pure-Python source
# plus packaging metadata, not compiled code. What actually needs compiling
# (hidapi's native extension, pulled in by rivalcfg) is left to pip at
# `postinst` time on the machine that installs the package, the same way
# install.sh already does it, since rivalcfg isn't packaged for Debian or
# Ubuntu. See debian/control and debian/postinst.
#
# Usage: ./build-deb.sh [output-directory]   (default: dist/)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${1:-$SCRIPT_DIR/dist}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

VERSION="$(cd "$SCRIPT_DIR" && python3 -c 'import re; print(re.search(r"__version__ = \"([^\"]+)\"", open("linux_wireless_manager/__init__.py").read())[1])')"
ARCH="all"
PKG_NAME="linux-wireless-manager"
DEB_FILE="$OUT_DIR/${PKG_NAME}_${VERSION}-1_${ARCH}.deb"

BUILD_ROOT="$(mktemp -d)"
trap 'rm -rf "$BUILD_ROOT"' EXIT

echo "==> Building ${PKG_NAME} ${VERSION} (${ARCH}) ..."

# -- DEBIAN/ (control files) -------------------------------------------
mkdir -p "$BUILD_ROOT/DEBIAN"
sed "s/__VERSION__/$VERSION/" "$SCRIPT_DIR/debian/control.in" > "$BUILD_ROOT/DEBIAN/control"
install -m 0755 "$SCRIPT_DIR/debian/postinst" "$BUILD_ROOT/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/debian/postrm" "$BUILD_ROOT/DEBIAN/postrm"

# -- App source, installed into a venv by postinst at configure time ----
SRC_DIR="$BUILD_ROOT/usr/lib/linux-wireless-manager/src"
mkdir -p "$SRC_DIR"
cp "$SCRIPT_DIR/pyproject.toml" "$SCRIPT_DIR/README.md" "$SRC_DIR/"
cp -r "$SCRIPT_DIR/linux_wireless_manager" "$SRC_DIR/linux_wireless_manager"
find "$SRC_DIR/linux_wireless_manager" -name "__pycache__" -exec rm -rf {} +

# -- Launcher --------------------------------------------------------------
mkdir -p "$BUILD_ROOT/usr/bin"
install -m 0755 "$SCRIPT_DIR/debian/linux-wireless-manager.wrapper" "$BUILD_ROOT/usr/bin/linux-wireless-manager"

# -- Desktop entry (same template install.sh uses) --------------------------
mkdir -p "$BUILD_ROOT/usr/share/applications"
sed "s|__EXEC__|/usr/bin/linux-wireless-manager|; s|__ICON__|input-mouse|" \
    "$SCRIPT_DIR/linux-wireless-manager.desktop.in" > "$BUILD_ROOT/usr/share/applications/linux-wireless-manager.desktop"

# -- Doc / copyright / changelog --------------------------------------------
DOC_DIR="$BUILD_ROOT/usr/share/doc/linux-wireless-manager"
mkdir -p "$DOC_DIR"
install -m 0644 "$SCRIPT_DIR/debian/copyright" "$DOC_DIR/copyright"
sed "s/__VERSION__/$VERSION/; s/__DATE__/$(date -R)/" "$SCRIPT_DIR/debian/changelog.in" | gzip -9 -n > "$DOC_DIR/changelog.Debian.gz"

# -- Permissions and ownership -----------------------------------------
find "$BUILD_ROOT" -type d -exec chmod 0755 {} +
find "$BUILD_ROOT" -type f -exec chmod 0644 {} +
chmod 0755 "$BUILD_ROOT/DEBIAN/postinst" "$BUILD_ROOT/DEBIAN/postrm" "$BUILD_ROOT/usr/bin/linux-wireless-manager"

echo "==> Packaging into $DEB_FILE ..."
dpkg-deb --build --root-owner-group "$BUILD_ROOT" "$DEB_FILE"

if command -v lintian >/dev/null 2>&1; then
    echo "==> lintian (informational only) ..."
    lintian "$DEB_FILE" || true
fi

echo "==> Built: $DEB_FILE"
