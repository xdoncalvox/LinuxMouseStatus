"""Maps a battery reading to a standard freedesktop/GNOME battery icon name.

These icon names (e.g. "battery-good-symbolic") are shipped as part of the
Adwaita and Yaru icon themes that come with stock Ubuntu, so the app does not
need to bundle any icon files of its own.
"""

UNKNOWN_ICON = "battery-missing-symbolic"


def icon_name_for(level: int, is_charging: bool) -> str:
    if level is None:
        return UNKNOWN_ICON

    if level >= 90:
        bucket = "full"
    elif level >= 60:
        bucket = "good"
    elif level >= 30:
        bucket = "low"
    elif level >= 15:
        bucket = "caution"
    else:
        bucket = "empty"

    if is_charging:
        # There's no "empty-charging" icon in most themes; "caution-charging"
        # is the lowest charging variant that reliably exists.
        if bucket == "empty":
            bucket = "caution"
        return f"battery-{bucket}-charging-symbolic"

    return f"battery-{bucket}-symbolic"
