"""Pure renderers for the commit ancestry graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.cells import cell_len
from rich.text import Text

from .models import Commit, HistoryLine
from .theme import GRAPH_PALETTE

GraphStyle = Literal["smooth", "compact"]

_TRANSLATION = str.maketrans({"*": "●", "|": "│", "/": "╱", "\\": "╲", "_": "─"})
_PITCH = 4
_INSET = 1
_PALETTE = GRAPH_PALETTE


@dataclass(frozen=True)
class GraphRow:
    """Rendered graph content associated with one history item."""

    graph: Text
    commit: Commit | None

    @property
    def height(self) -> int:
        """Number of physical terminal lines occupied by this row."""
        return self.graph.plain.count("\n") + 1

    @property
    def width(self) -> int:
        """Maximum terminal-cell width of a physical line."""
        return max((cell_len(line) for line in self.graph.plain.split("\n")), default=0)


@dataclass(frozen=True)
class _Lane:
    target: str
    color: str


_Cell = tuple[str, str | None]
_Canvas = dict[int, _Cell]


def _lane_x(slot: int) -> int:
    return _INSET + slot * _PITCH


def _put(canvas: _Canvas, x: int, glyph: str, color: str | None = None) -> None:
    canvas[x] = (glyph, color)


def _rails(lanes: list[_Lane | None], glyph: str = "│") -> _Canvas:
    canvas: _Canvas = {}
    for slot, lane in enumerate(lanes):
        if lane is not None:
            _put(canvas, _lane_x(slot), glyph, lane.color)
    return canvas


def _horizontal_route(
    canvas: _Canvas,
    lanes: list[_Lane | None],
    start: int,
    end: int,
    *,
    start_glyph: str,
    end_glyph: str,
    route_color: str,
    start_color: str | None = None,
    end_color: str | None = None,
    endpoint_slots: frozenset[int] = frozenset(),
) -> None:
    """Add a horizontal route, leaving unrelated vertical rails unbroken."""
    left, right = sorted((start, end))
    crossings = {
        _lane_x(slot)
        for slot, lane in enumerate(lanes)
        if lane is not None and slot not in endpoint_slots and left < _lane_x(slot) < right
    }
    for x in range(left + 1, right):
        if x in crossings:
            continue
        if x + 1 in crossings:
            _put(canvas, x, "╴", route_color)
        elif x - 1 in crossings:
            _put(canvas, x, "╶", route_color)
        else:
            _put(canvas, x, "─", route_color)
    _put(canvas, start, start_glyph, start_color or route_color)
    _put(canvas, end, end_glyph, end_color or route_color)


def _to_text(lines: list[_Canvas]) -> Text:
    text = Text(no_wrap=True)
    for line_number, canvas in enumerate(lines):
        if line_number:
            text.append("\n")
        if not canvas:
            continue
        final_x = max(canvas)
        for x in range(final_x + 1):
            glyph, color = canvas.get(x, (" ", None))
            text.append(glyph, style=color or "")
    return text


def _compact(history: tuple[HistoryLine, ...]) -> tuple[GraphRow, ...]:
    return tuple(
        GraphRow(
            Text.from_ansi(line.graph.translate(_TRANSLATION), no_wrap=True, end=""),
            line.commit,
        )
        for line in history
    )


class _SmoothRenderer:
    def __init__(self, *, spaced: bool = True) -> None:
        self.lanes: list[_Lane | None] = []
        self.color_index = 0
        self.spaced = spaced
        self.seen: set[str] = set()

    def _new_lane(self, target: str) -> _Lane:
        active_colors = {lane.color for lane in self.lanes if lane is not None}
        # Recycle retired colors before sharing a still-live lane's color. A
        # long-lived main lane must not become indistinguishable from every
        # twelfth short-lived topic just because the palette wrapped around.
        for offset in range(len(_PALETTE)):
            color = _PALETTE[(self.color_index + offset) % len(_PALETTE)]
            if color not in active_colors:
                self.color_index += offset + 1
                return _Lane(target, color)
        color = _PALETTE[self.color_index % len(_PALETTE)]
        self.color_index += 1
        return _Lane(target, color)

    def _current_slot(self, oid: str) -> int:
        for slot, lane in enumerate(self.lanes):
            if lane is not None and lane.target == oid:
                return slot
        for slot, lane in enumerate(self.lanes):
            if lane is None:
                self.lanes[slot] = self._new_lane(oid)
                return slot
        self.lanes.append(self._new_lane(oid))
        return len(self.lanes) - 1

    def _target_slot(self, oid: str) -> int | None:
        for slot, lane in enumerate(self.lanes):
            if lane is not None and lane.target == oid:
                return slot
        return None

    def _allocate_near(self, source: int, target: str) -> int:
        free = [slot for slot, lane in enumerate(self.lanes) if lane is None]
        if free:
            slot = min(free, key=lambda candidate: (abs(candidate - source), candidate))
            self.lanes[slot] = self._new_lane(target)
            return slot
        self.lanes.append(self._new_lane(target))
        return len(self.lanes) - 1

    def _fork_new(self, source: int, target: int) -> _Canvas:
        source_lane = self.lanes[source]
        target_lane = self.lanes[target]
        assert source_lane is not None and target_lane is not None
        canvas = _rails(self.lanes)
        if source < target:
            source_glyph, target_glyph = "├", "╮"
        else:
            source_glyph, target_glyph = "┤", "╭"
        _horizontal_route(
            canvas,
            self.lanes,
            _lane_x(source),
            _lane_x(target),
            start_glyph=source_glyph,
            end_glyph=target_glyph,
            route_color=target_lane.color,
            start_color=source_lane.color,
            end_color=target_lane.color,
            endpoint_slots=frozenset((source, target)),
        )
        return canvas

    def _fork_existing(self, source: int, target: int) -> tuple[_Canvas, _Canvas]:
        source_lane = self.lanes[source]
        target_lane = self.lanes[target]
        assert source_lane is not None and target_lane is not None
        source_x = _lane_x(source)
        target_x = _lane_x(target)
        bend_x = target_x - 1 if source < target else target_x + 1

        first = _rails(self.lanes)
        _horizontal_route(
            first,
            self.lanes,
            source_x,
            bend_x,
            start_glyph="├" if source < target else "┤",
            end_glyph="╮" if source < target else "╭",
            route_color=target_lane.color,
            start_color=source_lane.color,
            endpoint_slots=frozenset((source,)),
        )

        second = _rails(self.lanes)
        _horizontal_route(
            second,
            self.lanes,
            bend_x,
            target_x,
            start_glyph="╰" if source < target else "╯",
            end_glyph="┤" if source < target else "├",
            route_color=target_lane.color,
            end_color=target_lane.color,
            endpoint_slots=frozenset((target,)),
        )
        return first, second

    def _coalesce(self, first: int, second: int, parent: str) -> _Canvas:
        retained, retired = sorted((first, second))
        retained_lane = self.lanes[retained]
        retired_lane = self.lanes[retired]
        assert retained_lane is not None and retired_lane is not None
        canvas = _rails(self.lanes)
        _horizontal_route(
            canvas,
            self.lanes,
            _lane_x(retired),
            _lane_x(retained),
            start_glyph="╯",
            end_glyph="├",
            route_color=retired_lane.color,
            end_color=retained_lane.color,
            endpoint_slots=frozenset((retained, retired)),
        )
        self.lanes[retained] = _Lane(parent, retained_lane.color)
        self.lanes[retired] = None
        return canvas

    def render(self, commit: Commit, *, final: bool) -> GraphRow:
        current = self._current_slot(commit.oid)
        current_lane = self.lanes[current]
        assert current_lane is not None

        node = _rails(self.lanes)
        _put(node, _lane_x(current), "●", current_lane.color)
        lines = [node]

        # Repeated parent IDs are invalid but can appear in synthetic or damaged
        # history. Treat them as one edge so they cannot create phantom lanes.
        parents = tuple(dict.fromkeys(commit.parents))
        # Author timestamps can put a parent above its child (for example after
        # a rebase). Do not invent a downward lane to an already-rendered node:
        # name that parent explicitly, keeping the chronological rows untouched.
        above = tuple(parent for parent in parents if parent in self.seen)
        parents = tuple(parent for parent in parents if parent not in self.seen)
        self.seen.add(commit.oid)
        if above:
            label = "↑ parent " + ", ".join(parent[:8] for parent in above)
            start = max(node) + 2
            for offset, glyph in enumerate(label):
                _put(node, start + offset, glyph, current_lane.color)
        first_parent = parents[0] if parents else None

        # The current lane remains an exclusive stream while every secondary
        # edge is split off. This is what makes later coalescing topology-safe.
        for parent in parents[1:]:
            target = self._target_slot(parent)
            if target is None:
                target = self._allocate_near(current, parent)
                lines.append(self._fork_new(current, target))
            else:
                lines.extend(self._fork_existing(current, target))

        if first_parent is None:
            self.lanes[current] = None
        else:
            target = self._target_slot(first_parent)
            if target is None or target == current:
                self.lanes[current] = _Lane(first_parent, current_lane.color)
            else:
                lines.append(self._coalesce(current, target, first_parent))

        if final and any(lane is not None for lane in self.lanes):
            lines.append(_rails(self.lanes, glyph="┊"))
        elif len(lines) == 1 and (self.spaced or (first_parent is None and not final)):
            # Even Compact needs separation after a root: reusing its slot on
            # the very next line would falsely connect two unrelated nodes.
            lines.append(_rails(self.lanes))

        return GraphRow(_to_text(lines), commit)


def render_history(
    history: tuple[HistoryLine, ...], style: GraphStyle = "smooth"
) -> tuple[GraphRow, ...]:
    """Render history without I/O or mutation of the supplied model values.

    Keep the supplied chronological order. Parents below their children use
    connected lanes; parents already above them use explicit upward references.
    Smooth groups connector lines with their commit and adds breathing room.
    Compact omits optional spacers and gives each required line its own row.
    Legacy native graph input can still be displayed directly in Compact mode,
    and is the fallback when no structured commits are available.
    """
    if style not in {"smooth", "compact"}:
        raise ValueError(f"Unknown graph style: {style}")
    if style == "compact" and any(line.graph for line in history):
        return _compact(history)

    commits = tuple(line.commit for line in history if line.commit is not None)
    if not commits:
        return _compact(history)

    renderer = _SmoothRenderer(spaced=style == "smooth")
    rows = tuple(
        renderer.render(commit, final=index == len(commits) - 1)
        for index, commit in enumerate(commits)
    )
    if style == "smooth":
        return rows
    compact_rows: list[GraphRow] = []
    for row in rows:
        for index, line in enumerate(row.graph.split("\n", allow_blank=True)):
            line.no_wrap = True  # Rich's split retains spans, but not this setting.
            compact_rows.append(GraphRow(line, row.commit if index == 0 else None))
    return tuple(compact_rows)
