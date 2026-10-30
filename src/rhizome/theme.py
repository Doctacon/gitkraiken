"""Rhizome palettes and safe, non-probing appearance selection.

``COLORFGBG`` is only a hint: it may be stale or describe a custom terminal
palette. It is deliberately never supplemented by a terminal control query.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rich.color import Color as RichColor
from rich.style import Style
from rich.text import Text
from textual.theme import Theme


@dataclass(frozen=True)
class Palette:
    text: str
    muted: str
    subtle_text: str
    teal: str
    background: str
    surface: str
    panel: str
    selection: str
    addition: str
    deletion: str
    warning: str
    graph: tuple[str, ...]
    providers: Mapping[str, str]
    fg_colors: Mapping[str, str]
    bg_colors: Mapping[str, str]


_LIGHT_GRAPH = (
    "#006d77",
    "#7049a8",
    "#9a5a00",
    "#28733a",
    "#b13a48",
    "#1266a8",
    "#9b3b79",
    "#087657",
    "#a34a14",
    "#4955a6",
    "#806000",
    "#08717c",
)
_LIGHT_PROVIDERS = {
    "GitHub": "#7049a8",
    "Azure DevOps": "#1266a8",
    "GitLab": "#9a5a00",
    "Git": "#1266a8",
}
_LIGHT_FG = {
    "#14232c": "#14232c",
    "#48616c": "#48616c",
    "#36505b": "#36505b",
    "#006d77": "#006d77",
    **{color: color for color in _LIGHT_GRAPH},
}
_LIGHT_BG = {
    "#f7fbfc": "#f7fbfc",
    "#f8fbfb": "#f8fbfb",
    "#ffffff": "#ffffff",
    "#edf4f5": "#edf4f5",
    "#f3f8f9": "#f3f8f9",
    "#d8eeee": "#d8eeee",
}

LIGHT = Palette(
    "#14232c",
    "#48616c",
    "#36505b",
    "#006d77",
    "#f7fbfc",
    "#ffffff",
    "#edf4f5",
    "#d8eeee",
    "#28733a",
    "#b13a48",
    "#9a5a00",
    _LIGHT_GRAPH,
    _LIGHT_PROVIDERS,
    _LIGHT_FG,
    _LIGHT_BG,
)

_DARK_GRAPH = (
    "#62d6d0",
    "#c4a6ff",
    "#ffbd66",
    "#75d18a",
    "#ff9ca7",
    "#78bcff",
    "#f5a6dc",
    "#68d9ae",
    "#ffae78",
    "#b8c3ff",
    "#ddd36f",
    "#65d2df",
)
_DARK_PROVIDERS = {
    "GitHub": "#c4a6ff",
    "Azure DevOps": "#78bcff",
    "GitLab": "#ffbd66",
    "Git": "#78bcff",
}
DARK = Palette(
    "#edf4f5",
    "#b3c5ca",
    "#d0dce0",
    "#62d6d0",
    "#151a1f",
    "#1d252b",
    "#202a30",
    "#263b43",
    "#75d18a",
    "#ff9ca7",
    "#ffbd66",
    _DARK_GRAPH,
    _DARK_PROVIDERS,
    {
        **dict(zip(_LIGHT_GRAPH, _DARK_GRAPH, strict=True)),
        "#14232c": "#edf4f5",
        "#18323a": "#edf4f5",
        "#10242b": "#edf4f5",
        "#48616c": "#b3c5ca",
        "#36505b": "#d0dce0",
        "#6b4300": "#ffbd66",
        "#8d2635": "#ff9ca7",
    },
    {**{key: "#1d252b" for key in _LIGHT_BG}, "#d8eeee": "#263b43"},
)

_HC_GRAPH = (
    "#7ffff7",
    "#e3c7ff",
    "#ffd37f",
    "#9dffa8",
    "#ffb3bd",
    "#9bcaff",
    "#ffb8e8",
    "#8affc5",
    "#ffc18e",
    "#ccd5ff",
    "#fff58a",
    "#8af2ff",
)
_HC_PROVIDERS = {
    "GitHub": "#e3c7ff",
    "Azure DevOps": "#9bcaff",
    "GitLab": "#ffd37f",
    "Git": "#9bcaff",
}
HIGH_CONTRAST = Palette(
    "#ffffff",
    "#e1e1e1",
    "#ffffff",
    "#7ffff7",
    "#000000",
    "#000000",
    "#101010",
    "#303030",
    "#9dffa8",
    "#ffb3bd",
    "#ffd37f",
    _HC_GRAPH,
    _HC_PROVIDERS,
    {
        **dict(zip(_LIGHT_GRAPH, _HC_GRAPH, strict=True)),
        "#14232c": "#ffffff",
        "#18323a": "#ffffff",
        "#10242b": "#ffffff",
        "#48616c": "#e1e1e1",
        "#36505b": "#ffffff",
        "#6b4300": "#ffd37f",
        "#8d2635": "#ffb3bd",
    },
    {**{key: "#000000" for key in _LIGHT_BG}, "#d8eeee": "#303030"},
)

PALETTES = {"rhizome-light": LIGHT, "rhizome-dark": DARK, "rhizome-high-contrast": HIGH_CONTRAST}


def _theme(name: str, palette: Palette, *, dark: bool) -> Theme:
    return Theme(
        name=name,
        primary=palette.teal,
        secondary=palette.muted,
        warning=palette.warning,
        error=palette.deletion,
        success=palette.addition,
        accent=palette.teal,
        foreground=palette.text,
        background=palette.background,
        surface=palette.surface,
        panel=palette.panel,
        dark=dark,
        variables={
            "text": palette.text,
            "text-muted": palette.muted,
            "foreground-muted": palette.muted,
            "primary-muted": palette.selection,
            "block-cursor-foreground": palette.text,
            "block-cursor-background": palette.selection,
        },
    )


TEXTUAL_THEMES = (
    _theme("rhizome-light", LIGHT, dark=False),
    _theme("rhizome-dark", DARK, dark=True),
    _theme("rhizome-high-contrast", HIGH_CONTRAST, dark=True),
)


def palette_for(theme_name: str) -> Palette:
    return PALETTES.get(theme_name, LIGHT)


def _mapped(color: RichColor | None, colors: Mapping[str, str]) -> str | None:
    if color is None:
        return None
    try:
        return colors.get(color.get_truecolor().hex.lower())
    except AttributeError:
        return None


def theme_text(text: Text, palette: Palette) -> Text:
    """Copy ``text``, translating known light Rich colors without losing metadata."""
    themed = text.copy()

    def translate(style: Style) -> Style:
        foreground = _mapped(style.color, palette.fg_colors)
        background = _mapped(style.bgcolor, palette.bg_colors)
        return style + Style(color=foreground, bgcolor=background)

    def as_style(value: str | Style) -> Style:
        return Style.parse(value) if isinstance(value, str) else value

    themed.style = translate(as_style(text.style)) if text.style else text.style
    themed.spans = [span._replace(style=translate(as_style(span.style))) for span in text.spans]
    return themed


def resolve_theme(preference: str, environ: Mapping[str, str]) -> tuple[str, str]:
    """Resolve a saved preference without reading the terminal or environment itself."""
    normalized = preference.strip().lower()
    explicit = {
        "light": "rhizome-light",
        "rhizome-light": "rhizome-light",
        "dark": "rhizome-dark",
        "rhizome-dark": "rhizome-dark",
        "high-contrast": "rhizome-high-contrast",
        "high_contrast": "rhizome-high-contrast",
        "rhizome-high-contrast": "rhizome-high-contrast",
    }
    if normalized in explicit:
        return explicit[normalized], "explicit preference"
    if normalized not in {"", "auto"}:
        return "rhizome-light", "unknown preference; light fallback"
    value = environ.get("COLORFGBG", "")
    fields = value.split(";")
    # urxvt documents fg;bg and fg;xpm;bg; only canonical background hints count.
    if len(fields) in (2, 3) and all(
        field == "default"
        or (field.isascii() and field.isdecimal() and len(field) <= 3 and int(field) <= 255)
        for field in fields
    ):
        background = fields[-1]
        if background == "0":
            return "rhizome-dark", "COLORFGBG background hint 0 (hint, not a probe)"
        if background in {"7", "15"}:
            return "rhizome-light", f"COLORFGBG background hint {background} (hint, not a probe)"
    return "rhizome-light", "COLORFGBG unavailable or inconclusive; light fallback"


# Compatibility aliases: legacy renderers remain light until their adapters wire palettes.
TEAL = LIGHT.teal
MUTED = LIGHT.muted
TEXT = LIGHT.text
SUBTLE_TEXT = LIGHT.subtle_text
GRAPH_PALETTE = LIGHT.graph
PROVIDER_COLORS = LIGHT.providers
