"""Pure renderers for the commit ancestry graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.cells import cell_len
from rich.text import Text

from .models import Commit, HistoryLine

GraphStyle = Literal["smooth", "compact"]

_TRANSLATION = str.maketrans({"*": "●", "|": "│", "/": "╱", "\\": "╲", "_": "─"})
_PITCH = 4
_INSET = 1
_PALETTE = (
    "#62d6c5",
    "#c5a6ef",
    "#f2c779",
    "#9ed8a5",
    "#ee939b",
    "#83b9ef",
    "#e5a8d8",
    "#76c7a0",
    "#f0a36f",
    "#aab8f0",
    "#d5b66f",
    "#8fcad0",
)


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
    def __init__(self) -> None:
        self.lanes: list[_Lane | None] = []
        self.color_index = 0

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
        elif len(lines) == 1:
            # Even disconnected roots need physical separation from the next
            # commit node; active unrelated lanes continue through that space.
            lines.append(_rails(self.lanes))

        return GraphRow(_to_text(lines), commit)


def render_history(
    history: tuple[HistoryLine, ...], style: GraphStyle = "smooth"
) -> tuple[GraphRow, ...]:
    """Render history without I/O or mutation of the supplied model values.

    Structured commits must be ordered as Git emits ``log --graph``: every
    child appears before each of its parents. Compact rendering retains Git's
    native connector rows. Smooth rendering derives one (possibly multiline)
    row per commit from immutable parent data.
    If no structured commit data is available, native rendering is the only
    truthful fallback.
    """
    if style == "compact":
        return _compact(history)
    if style != "smooth":
        raise ValueError(f"Unknown graph style: {style}")

    commits = tuple(line.commit for line in history if line.commit is not None)
    if not commits:
        return _compact(history)

    renderer = _SmoothRenderer()
    return tuple(
        renderer.render(commit, final=index == len(commits) - 1)
        for index, commit in enumerate(commits)
    )
