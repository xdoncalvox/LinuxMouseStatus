"""App preferences, stored as JSON in
$XDG_CONFIG_HOME/linux-wireless-manager/config.json.

Main thread only. A missing or unreadable file falls back to defaults
instead of stopping the app.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from . import config

logger = logging.getLogger(config.APP_ID)


def default_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, config.APP_ID, "config.json")


class Preferences:
    def __init__(self, path: str | None = None):
        self._path = path or default_path()
        self._values: dict = {}
        try:
            with open(self._path, encoding="utf-8") as file:
                loaded = json.load(file)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("Couldn't read %s, using defaults: %s", self._path, exc)
            return
        if isinstance(loaded, dict):
            self._values = loaded
        else:
            logger.warning("Ignoring %s: expected a JSON object", self._path)

    def get(self, key: str, default=None):
        return self._values.get(key, default)

    def set(self, key: str, value) -> None:
        if key in self._values and self._values[key] == value:
            return
        self._values[key] = value
        self._save()

    def _save(self) -> None:
        directory = os.path.dirname(self._path)
        try:
            os.makedirs(directory, exist_ok=True)
            # Write a temp file and rename it, so a crash can't leave a
            # half-written config behind.
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(self._values, file, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            logger.warning("Couldn't save preferences to %s: %s", self._path, exc)
