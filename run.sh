#!/usr/bin/env bash
# Launches Linux Wireless Manager using the project's virtualenv.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec "$SCRIPT_DIR/venv/bin/python3" -m linux_wireless_manager "$@"
