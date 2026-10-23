"""Typed local/remote reference badges for the unified commit graph."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from rich.style import Style
from rich.text import Text

from .models import Ref, Snapshot
from .theme import MUTED, PROVIDER_COLORS, TEAL

_LOCAL = TEAL
_MUTED = MUTED
_PROVIDERS = {
    "GitHub": ("🐙", PROVIDER_COLORS["GitHub"]),
    "Azure DevOps": ("☁", PROVIDER_COLORS["Azure DevOps"]),
    "GitLab": ("🦊", PROVIDER_COLORS["GitLab"]),
    "Git": ("↗", PROVIDER_COLORS["Git"]),
}


@dataclass(frozen=True)
class RefBadges:
    text: Text
    description: Text


def build_ref_badges(snapshot: Snapshot, selected_ref: str = "") -> dict[str, RefBadges]:
    """Group actual typed pointers by OID, independent of history selection.

    Role markers precede names so local and remote pointers remain visible even
    when a long branch label is ellipsized. Descriptions retain every full ref;
    Commit.refs strings are deliberately not used to guess roles or providers.
    Remote tips are local remote-tracking refs, not live server observations.
    """
    by_oid: dict[str, list[Ref]] = defaultdict(list)
    for ref in snapshot.refs:
        by_oid[ref.oid].append(ref)
    if snapshot.head:
        by_oid.setdefault(snapshot.head, [])
    stashes = defaultdict(list)
    for stash in snapshot.stashes:
        stashes[stash.oid].append(stash)
        by_oid.setdefault(stash.oid, [])
    remotes = {remote.name: remote for remote in snapshot.remotes}
    badges: dict[str, RefBadges] = {}
    kind_order = {"local": 0, "remote": 1, "tag": 2}
    for oid, refs in by_oid.items():
        refs.sort(key=lambda ref: (not ref.current, kind_order[ref.kind], ref.name, ref.full_name))
        text = Text(no_wrap=True, overflow="ellipsis")
        description = Text()
        is_head = oid == snapshot.head
        if is_head or any(ref.kind == "local" for ref in refs):
            text.append("💻 ", style=f"bold {_LOCAL}")
        providers = {
            remotes[ref.remote].provider if ref.remote in remotes else "Git"
            for ref in refs
            if ref.kind == "remote"
        }
        for provider, (icon, color) in _PROVIDERS.items():
            if provider in providers or (provider == "Git" and providers - _PROVIDERS.keys()):
                text.append(icon + " ", style=color)
        if any(ref.kind == "tag" for ref in refs):
            text.append("◇ ", style="#9a5a00")
        if stashes[oid]:
            text.append("▣ ", style="#7049a8")

        labels: list[tuple[str, str, str]] = []
        if is_head:
            description.append(
                f"Checked out: {snapshot.branch}\n" if snapshot.branch else "Detached HEAD\n"
            )
            if not any(ref.current for ref in refs):
                labels.append(("HEAD", f"bold {_LOCAL}", ""))
        for ref in refs:
            style = f"bold {_LOCAL}" if ref.current else "#36505b"
            if ref.full_name == selected_ref:
                style += " bold underline"
            labels.append((ref.name, style, ref.full_name))
            if ref.kind == "local":
                description.append(f"💻 Local: {ref.full_name}")
                if ref.upstream:
                    description.append(f" → {ref.upstream}")
                description.append("\n")
            elif ref.kind == "remote":
                remote = remotes.get(ref.remote)
                provider = remote.provider if remote else "Git"
                icon = _PROVIDERS.get(provider, _PROVIDERS["Git"])[0]
                description.append(f"{icon} {provider} remote: {ref.full_name}\n")
            else:
                description.append(f"◇ Tag: {ref.full_name}\n")
        for stash in stashes[oid]:
            style = "#7049a8 bold underline" if stash.ref == selected_ref else "#7049a8"
            labels.append((stash.ref, style, ""))
            description.append(f"▣ {stash.ref}: {stash.subject}\n")
        for index, (name, style, full_ref) in enumerate(labels):
            if index:
                text.append(" · ", style=_MUTED)
            label_style = Style.parse(style)
            if full_ref:
                label_style += Style(meta={"context_ref": full_ref})
            text.append(name, style=label_style)
        if providers:
            description.append(
                "Remote markers show last-fetched tracking refs; Fetch updates them."
            )
        tooltip_lines = description.plain.splitlines()
        tooltip = "\n".join(tooltip_lines[:20])
        if len(tooltip_lines) > 20:
            tooltip += "\n… Full refs are listed in commit details."
        text.stylize(Style(meta={"ref_description": tooltip}))
        badges[oid] = RefBadges(text, description)
    return badges
