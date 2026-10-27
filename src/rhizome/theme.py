"""Shared light-surface Rich palette.

These are deliberately dark foreground colors: Rich spans are rendered over a
selected row as well as the normal pale workspace background.
"""

from __future__ import annotations

TEAL = "#006d77"
MUTED = "#48616c"
TEXT = "#14232c"
SUBTLE_TEXT = "#36505b"

GRAPH_PALETTE = (
    TEAL,
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

PROVIDER_COLORS = {
    "GitHub": "#7049a8",
    "Azure DevOps": "#1266a8",
    "GitLab": "#9a5a00",
    "Git": "#1266a8",
}
