"""Logitech mouse/keyboard settings, through the Solaar CLI
(https://pwr-solaar.github.io/Solaar/).

Unlike SteelSeries, HID++ settings *can* be read back from the device, and
Solaar already knows how - so this module shells out to its `solaar`
command instead of re-implementing HID++. Solaar's Python package isn't on
this project's venv path (see CLAUDE.md), so the CLI is the whole
interface.

UPowerDevice (devices/upower.py) is the Device object for every Logitech or
Bluetooth peripheral; this module only supplies its settings_schema() /
get_settings() / apply_settings(), matched to a Solaar device by the same
serial number UPower reports. `available()` and every public function here
fail soft (empty schema, or a SettingsWriteError with a plain-language
message) rather than raising something unexpected, since a missing `solaar`
binary or an unmatched serial number are expected, not bugs.

**Ground truth, not guesswork**: `_parse_listing()`'s format and the setting
names below (`dpi`, `pointer_speed`, `report_rate`, `report_rate_extended`)
come from reading Solaar 1.1.20's own source
(`lib/solaar/cli/config.py:_print_setting`, `lib/logitech_receiver/
settings_templates.py`), not from running it - there's no `solaar` binary
or wireless Logitech device available in this environment. See ROADMAP.md's
Step 3b for what that means still needs checking against the real thing.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from .base import ChoiceSetting, RangeSetting, SettingField, SettingsValues, SettingsWriteError

_SOLAAR_BIN = "solaar"
_TIMEOUT_SECONDS = 8

# Settings this app exposes (ROADMAP's "start with DPI and polling rate"),
# by the machine name settings_templates.py gives each. dpi_extended (the
# X/Y/LOD-capable DPI feature on some mice) needs a value per axis, not one
# value, so it's left out for now - see ROADMAP.md.
_RANGE_SETTINGS = {
    # (label fallback, minimum, maximum) - settings_templates.py's
    # PointerSpeed.min_value/max_value. Solaar's listing doesn't print a
    # RANGE setting's bounds (only CHOICE/TOGGLE get a "possible values"
    # line), so these are hardcoded from source instead.
    "pointer_speed": ("Sensitivity (Pointer Speed)", 46, 511),
}
_CHOICE_SETTINGS = frozenset({"dpi", "report_rate", "report_rate_extended"})
_SUPPORTED_SETTINGS = frozenset(_RANGE_SETTINGS) | _CHOICE_SETTINGS

# Matches config.py's _print_setting(): a "# possible values: one of [ a, b,
# c ], or higher/..." line (CHOICE kind only), a value line "name = value"
# or "name = ? (failed to read from device)", and any other "#..." line is
# the label (first one after a blank line) or an ignored description.
_CHOICES_LINE = re.compile(r"^#\s*possible values:\s*one of\s*\[\s*(.*?)\s*\](?:,.*)?\s*$")
_COMMENT_LINE = re.compile(r"^#\s?(.*)$")
_VALUE_LINE = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(.*)$")


def available() -> bool:
    return shutil.which(_SOLAAR_BIN) is not None


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            [_SOLAAR_BIN, *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise SettingsWriteError(f"solaar not installed: {exc}", "Solaar isn't installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise SettingsWriteError(f"solaar {' '.join(args)} timed out: {exc}", "Solaar didn't respond in time.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SettingsWriteError(
            f"solaar {' '.join(args)} failed: {detail}",
            "Couldn't reach this device through Solaar. It may be off, out of range, or unpaired.",
        )
    return result.stdout


def _parse_listing(output: str) -> dict[str, dict]:
    """`solaar config <device>` prints one blank-line-separated block per
    setting: a "# <label>" line, then optional "# <description>" and
    "# possible values: ..." lines, then "<name> = <value>". Returns
    {name: {"label": str, "value": str, "choices": list[str] | None}}."""
    parsed: dict[str, dict] = {}
    label: str | None = None
    choices: list[str] | None = None
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            label = None
            choices = None
            continue
        choices_match = _CHOICES_LINE.match(line)
        if choices_match:
            choices = [choice.strip() for choice in choices_match.group(1).split(",") if choice.strip()]
            continue
        comment_match = _COMMENT_LINE.match(line)
        if comment_match:
            if label is None:
                label = comment_match.group(1).strip()
            continue  # a later "#" line is just the description - ignored
        value_match = _VALUE_LINE.match(line)
        if value_match:
            name, value = value_match.group(1), value_match.group(2).strip()
            parsed[name] = {"label": label or name, "value": value, "choices": choices}
            label = None
            choices = None
    return parsed


def _list_settings(serial: str) -> dict[str, dict]:
    if not serial or not available():
        return {}
    try:
        output = _run(["config", serial.lower()])
    except SettingsWriteError:
        return {}  # not paired, offline, or no settings - same as "nothing to show"
    return _parse_listing(output)


def settings_schema(serial: str) -> list[SettingField]:
    fields: list[SettingField] = []
    for name, info in _list_settings(serial).items():
        if name in _RANGE_SETTINGS:
            _fallback_label, minimum, maximum = _RANGE_SETTINGS[name]
            try:
                default = int(info["value"])
            except ValueError:
                default = minimum
            fields.append(RangeSetting(name, info["label"], minimum, maximum, 1, default))
        elif name in _CHOICE_SETTINGS and info["choices"]:
            options = [(choice, choice) for choice in info["choices"]]
            fields.append(ChoiceSetting(name, info["label"], options, info["value"]))
    return fields


def get_settings(serial: str) -> SettingsValues:
    listing = _list_settings(serial)
    values = {}
    all_read = bool(listing)
    for field in settings_schema(serial):
        info = listing.get(field.name)
        raw_value = info["value"] if info else None
        # A failed read prints as "? (failed to read from device)", not a
        # bare "?" - see cli/config.py's _print_setting().
        if raw_value is None or raw_value.startswith("?"):
            values[field.name] = field.default
            all_read = False
        elif isinstance(field, RangeSetting):
            try:
                values[field.name] = int(raw_value)
            except ValueError:
                values[field.name] = field.default
                all_read = False
        else:
            values[field.name] = raw_value
    # from_cache doubles here as "every value below is a confirmed live read
    # from the device" - Solaar has no separate cache concept the way
    # rivalcfg does, since HID++ settings actually can be read back.
    return SettingsValues(values=values, from_cache=all_read)


def apply_settings(serial: str, values: dict) -> SettingsValues:
    if not serial or not available():
        raise SettingsWriteError("solaar not available", "Solaar isn't installed.")
    for name, value in values.items():
        if name not in _SUPPORTED_SETTINGS:
            continue
        _run(["config", serial.lower(), name, str(value)])
    return get_settings(serial)
