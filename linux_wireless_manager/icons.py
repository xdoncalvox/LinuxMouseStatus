"""Tray and window icons: a small mouse or keyboard outline with a battery
level, so they aren't mistaken for the laptop's own battery icon.

install() writes the SVGs into the user's cache directory at startup. The
tray loads them through AppIndicator's icon theme path and the window through
Gtk.IconTheme.append_search_path(). If writing fails, the plain themed
input-mouse-symbolic / input-keyboard-symbolic icons are used instead.

Shapes use fills only, no strokes, because GTK recolours symbolic icons by
overriding fill colours: plain shapes take the panel's foreground colour, and
the "error" and "success" classes take the theme's red and green.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

from . import config
from .devices import BatteryStatus, DeviceKind

logger = logging.getLogger(config.APP_ID)

_PREFIX = "lwm"
_LEVEL_STEPS = (0, 20, 40, 60, 80, 100)
_THEME_ICONS = {
    DeviceKind.MOUSE: "input-mouse-symbolic",
    DeviceKind.KEYBOARD: "input-keyboard-symbolic",
}
# Literal colours, for panels that don't recolour symbolic icons.
_COLORS = {None: "#bebebe", "error": "#e01b24", "success": "#33d17a"}


def icon_name(kind: DeviceKind, battery: BatteryStatus | None, *, custom: bool = True) -> str:
    """The icon for a device kind and battery reading (None means unknown).
    With custom=False, returns the plain themed icon."""
    if not custom:
        return _THEME_ICONS[kind]
    if battery is None:
        return f"{_PREFIX}-{kind.value}-unknown-symbolic"
    return _level_icon_name(kind, _nearest_step(battery.estimated_percentage), battery.is_charging)


def _nearest_step(percentage: int) -> int:
    return min(100, max(0, (percentage + 10) // 20 * 20))


def _level_icon_name(kind: DeviceKind, step: int, charging: bool) -> str:
    suffix = "-charging" if charging else ""
    return f"{_PREFIX}-{kind.value}-{step}{suffix}-symbolic"


def install() -> str | None:
    """Write every icon variant and return the directory, or None on failure."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    directory = os.path.join(base, config.APP_ID, "icons")
    try:
        os.makedirs(directory, exist_ok=True)
        for name, svg in _all_icons():
            path = os.path.join(directory, f"{name}.svg")
            try:
                with open(path, encoding="utf-8") as file:
                    if file.read() == svg:
                        continue
            except FileNotFoundError:
                pass
            with open(path, "w", encoding="utf-8") as file:
                file.write(svg)
    except OSError as exc:
        logger.warning("Couldn't write icons to %s, using theme icons instead: %s", directory, exc)
        return None
    return directory


def _all_icons() -> Iterator[tuple[str, str]]:
    drawers = {DeviceKind.MOUSE: _mouse_svg, DeviceKind.KEYBOARD: _keyboard_svg}
    for kind, draw in drawers.items():
        yield f"{_PREFIX}-{kind.value}-unknown-symbolic", draw(None, False)
        for step in _LEVEL_STEPS:
            for charging in (False, True):
                yield _level_icon_name(kind, step, charging), draw(step, charging)


# -- Drawing, on a 16x16 grid --------------------------------------------

_MOUSE_BOLT = "M14.5,1L11.5,5.5H13.25L12.5,9.5L15.5,4.5H13.75Z"
_KEYBOARD_BOLT = "M14.75,11L12.25,13.75H13.75L13,16L15.75,13H14.25Z"


def _rounded_rect(x: float, y: float, w: float, h: float, r: float) -> str:
    """SVG path data for a rounded rectangle."""
    return (
        f"M{x + r:g},{y:g}h{w - 2 * r:g}a{r:g},{r:g} 0 0 1 {r:g},{r:g}"
        f"v{h - 2 * r:g}a{r:g},{r:g} 0 0 1 {-r:g},{r:g}"
        f"h{-(w - 2 * r):g}a{r:g},{r:g} 0 0 1 {-r:g},{-r:g}"
        f"v{-(h - 2 * r):g}a{r:g},{r:g} 0 0 1 {r:g},{-r:g}z"
    )


def _rect(x: float, y: float, w: float, h: float) -> str:
    return f"M{x:g},{y:g}h{w:g}v{h:g}h{-w:g}z"


def _path(d: str, css_class: str | None = None, extra: str = "") -> str:
    class_attr = f' class="{css_class}"' if css_class else ""
    return f'<path d="{d}" fill="{_COLORS[css_class]}"{class_attr}{extra}/>'


def _ring(outer: tuple, inner: tuple, css_class: str | None = None, extra: str = "") -> str:
    """A filled outline: the outer rounded rectangle minus the inner one."""
    return _path(
        _rounded_rect(*outer) + _rounded_rect(*inner),
        css_class,
        ' fill-rule="evenodd"' + extra,
    )


def _svg(parts: list[str]) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        + "".join(parts)
        + "</svg>\n"
    )


def _mouse_svg(step: int | None, charging: bool) -> str:
    dim = ' opacity="0.5"' if step is None else ""
    parts = [
        # Body outline, then the split between the two buttons.
        _ring((1, 0.5, 10, 15, 5), (2.25, 1.75, 7.5, 12.5, 3.75), "error" if step == 0 else None, dim),
        _path(_rect(5.5, 1.75, 1, 3.75), extra=dim),
        _path(_rect(2.25, 5.5, 7.5, 1), extra=dim),
    ]
    if step:
        # The level fills the palm from the bottom, clipped to the body's
        # rounded shape.
        height = 5.75 * step / 100
        parts.append(
            f'<clipPath id="palm"><path d="{_rounded_rect(3.25, 2.75, 5.5, 10.5, 2.75)}"/></clipPath>'
        )
        parts.append(
            _path(
                _rect(3.25, 13.25 - height, 5.5, height),
                "error" if step <= 20 else None,
                ' clip-path="url(#palm)"',
            )
        )
    if charging:
        parts.append(_path(_MOUSE_BOLT, "success"))
    return _svg(parts)


def _keyboard_svg(step: int | None, charging: bool) -> str:
    dim = ' opacity="0.5"' if step is None else ""
    keys = "".join(
        _rect(x, y, 1.5, 1.25) for y in (3.75, 5.75) for x in (2.75, 5, 7.25, 9.5, 11.75)
    )
    parts = [
        _ring((0.5, 1.5, 15, 9, 1.5), (1.75, 2.75, 12.5, 6.5, 0.5), None, dim),
        _path(keys + _rect(4.5, 7.5, 7, 1), extra=dim),  # keys and space bar
    ]
    # Battery bar under the keyboard, shortened to make room for the bolt.
    bar_width = 10.5 if charging else 15
    parts.append(
        _ring((0.5, 12, bar_width, 3.5, 1), (1.5, 13, bar_width - 2, 1.5, 0.25), "error" if step == 0 else None, dim)
    )
    if step:
        parts.append(
            _path(_rect(1.5, 13, (bar_width - 2) * step / 100, 1.5), "error" if step <= 20 else None)
        )
    if charging:
        parts.append(_path(_KEYBOARD_BOLT, "success"))
    return _svg(parts)
