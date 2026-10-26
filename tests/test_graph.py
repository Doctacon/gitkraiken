from __future__ import annotations

import random

import pytest
from rich.color import Color
from rich.console import Console
from rich.text import Text

from gitkraiken.graph import GraphRow, render_history
from gitkraiken.models import Commit, HistoryLine
from gitkraiken.theme import GRAPH_PALETTE


def commit(oid: str, *parents: str) -> Commit:
    return Commit(oid, tuple(parents), "A", "a@example.test", "2025-01-01", oid)


def history(*commits: Commit) -> tuple[HistoryLine, ...]:
    return tuple(HistoryLine("native", item) for item in commits)


def physical(rows: tuple[GraphRow, ...]) -> list[str]:
    return [line for row in rows for line in row.graph.plain.split("\n")]


_PORTS = {
    "●": frozenset("TB"),
    "│": frozenset("TB"),
    "┊": frozenset("TB"),
    "├": frozenset("TBR"),
    "┤": frozenset("TBL"),
    "─": frozenset("LR"),
    "╴": frozenset("L"),
    "╶": frozenset("R"),
    "╮": frozenset("LB"),
    "╭": frozenset("RB"),
    "╰": frozenset("TR"),
    "╯": frozenset("TL"),
}


def traced_parents(rows: tuple[GraphRow, ...]) -> dict[str, set[str]]:
    """Trace the displayed directed graph rather than consulting parent metadata."""
    lines = physical(rows)
    grid = {(y, x): glyph for y, line in enumerate(lines) for x, glyph in enumerate(line)}
    nodes: dict[tuple[int, int], str] = {}
    row_start = 0
    for row in rows:
        node_x = row.graph.plain.split("\n", 1)[0].index("●")
        assert row.commit is not None
        nodes[(row_start, node_x)] = row.commit.oid
        row_start += row.height

    result: dict[str, set[str]] = {}
    for source_position, source_oid in nodes.items():
        found: set[str] = set()
        pending = [source_position]
        visited: set[tuple[int, int]] = set()
        while pending:
            y, x = pending.pop()
            if (y, x) in visited:
                continue
            visited.add((y, x))
            if (y, x) != source_position and (target := nodes.get((y, x))) is not None:
                found.add(target)
                continue
            glyph = grid.get((y, x), " ")
            ports = _PORTS.get(glyph, frozenset())
            for direction, dx, reciprocal in (("L", -1, "R"), ("R", 1, "L")):
                if direction in ports and reciprocal in _PORTS.get(
                    grid.get((y, x + dx), " "), frozenset()
                ):
                    pending.append((y, x + dx))
            if "B" in ports and "T" in _PORTS.get(grid.get((y + 1, x), " "), frozenset()):
                pending.append((y + 1, x))

            # ╴│╶ is deliberately a horizontal edge passing behind an
            # independent vertical edge; the center cell is not a junction.
            if glyph == "╴" and grid.get((y, x + 1)) in {"│", "├", "┤"}:
                if grid.get((y, x + 2)) == "╶":
                    pending.append((y, x + 2))
            elif glyph == "╶" and grid.get((y, x - 1)) in {"│", "├", "┤"}:
                if grid.get((y, x - 2)) == "╴":
                    pending.append((y, x - 2))
        result[source_oid] = found
    return result


def assert_topology(rows: tuple[GraphRow, ...], commits: tuple[Commit, ...]) -> None:
    visible = {item.oid for item in commits}
    expected = {item.oid: set(item.parents) & visible for item in commits}
    assert traced_parents(rows) == expected


def assert_prefixes_stitch(commits: tuple[Commit, ...]) -> None:
    """A truncated tail must reconnect to the same lanes when more rows load."""
    full_rows = render_history(history(*commits))
    for cut in range(1, len(commits)):
        prefix_rows = render_history(history(*commits[:cut]))
        assert "┊" in prefix_rows[-1].graph.plain or not commits[cut - 1].parents
        assert_topology(prefix_rows + full_rows[cut:], commits)


def color_of(row: GraphRow, glyph: str, occurrence: int = 1) -> object:
    console = Console()
    offset = -1
    for _ in range(occurrence):
        offset = row.graph.plain.index(glyph, offset + 1)
    return row.graph.get_style_at_offset(console, offset).color


def test_empty_history_is_empty_for_both_styles() -> None:
    assert render_history(()) == ()
    assert render_history((), "compact") == ()


def test_compact_preserves_every_native_line_ansi_and_glyph_translation() -> None:
    item = commit("a")
    source = (
        HistoryLine("\x1b[31m* |\\/_\x1b[0m", item),
        HistoryLine("\x1b[34m|/\x1b[0m"),
    )

    rows = render_history(source, "compact")

    assert [row.graph.plain for row in rows] == ["● │╲╱─", "│╱"]
    assert [row.commit for row in rows] == [item, None]
    assert all(row.graph.no_wrap for row in rows)
    console = Console()
    assert rows[0].graph.get_style_at_offset(console, 0).color.name == "color(1)"
    assert rows[1].graph.get_style_at_offset(console, 0).color.name == "color(4)"


def test_smooth_linear_history_and_root_have_spacer_lines() -> None:
    commits = (commit("a", "b"), commit("b", "c"), commit("c"))
    rows = render_history(history(*commits))

    assert [row.graph.plain for row in rows] == [" ●\n │", " ●\n │", " ●\n"]
    assert [row.height for row in rows] == [2, 2, 2]
    assert all(row.graph.no_wrap for row in rows)
    assert_topology(rows, commits)


def test_divergence_and_rejoin_preserve_exact_ancestry() -> None:
    commits = (
        commit("merge", "left", "right"),
        commit("left", "base"),
        commit("right", "base"),
        commit("base"),
    )
    rows = render_history(history(*commits))

    assert "├───╮" in rows[0].graph.plain
    assert "├───╯" in rows[2].graph.plain
    assert_topology(rows, commits)


def test_new_fork_uses_target_color_except_at_source_junction() -> None:
    commits = (commit("merge", "first", "second"), commit("first"), commit("second"))
    row = render_history(history(*commits))[0]

    source_color = color_of(row, "●")
    target_color = color_of(row, "╮")
    assert color_of(row, "├") == source_color
    assert color_of(row, "─") == target_color
    assert target_color != source_color


def test_octopus_merge_splits_all_secondaries_before_first_parent_join() -> None:
    commits = (
        commit("octopus", "one", "two", "three", "four"),
        commit("one", "root"),
        commit("two", "root"),
        commit("three", "root"),
        commit("four", "root"),
        commit("root"),
    )
    rows = render_history(history(*commits))

    assert rows[0].height == 4  # node plus three distinct fanouts
    assert_topology(rows, commits)


def test_existing_secondary_uses_delayed_join_and_does_not_pollute_existing_child() -> None:
    commits = (
        commit("existing-child", "shared"),
        commit("merge", "own-parent", "shared"),
        commit("own-parent"),
        commit("shared"),
    )
    rows = render_history(history(*commits))

    # The existing target is left of the source: fork to a temporary bend,
    # then join on the following row rather than drawing a T-to-T shortcut.
    assert "╭──┤" in rows[1].graph.plain
    assert "├╯" in rows[1].graph.plain
    assert rows[1].height >= 3

    source_color = color_of(rows[1], "●")
    target_color = color_of(rows[0], "●")
    assert color_of(rows[1], "┤") == source_color
    for glyph in ("╭", "─", "╯", "├"):
        assert color_of(rows[1], glyph) == target_color

    assert_topology(rows, commits)
    assert traced_parents(rows)["existing-child"] == {"shared"}


def test_crossing_is_an_underpass_not_a_junction() -> None:
    commits = (
        commit("tip-a", "far"),
        commit("tip-b", "near"),
        commit("merge", "own", "far"),
        commit("near"),
        commit("own"),
        commit("far"),
    )
    rows = render_history(history(*commits))

    rendered = "\n".join(physical(rows))
    assert "╴│╶" in rendered
    assert not ({"┼", "┬", "┴"} & set(rendered))
    assert_topology(rows, commits)


def test_criss_cross_history_keeps_both_shared_ancestries_distinct() -> None:
    commits = (
        commit("merge", "left", "right"),
        commit("left", "a", "b"),
        commit("right", "b", "a"),
        commit("a", "root"),
        commit("b", "root"),
        commit("root"),
    )
    rows = render_history(history(*commits))

    assert_topology(rows, commits)
    assert traced_parents(rows)["left"] == {"a", "b"}
    assert traced_parents(rows)["right"] == {"a", "b"}
    assert_prefixes_stitch(commits)


def test_multiple_tips_roots_and_retired_slot_reuse() -> None:
    commits = (
        commit("a"),
        commit("b", "d"),
        commit("c"),
        commit("d"),
    )
    rows = render_history(history(*commits))

    # Every disconnected tip takes the first free physical slot; retired slots
    # do not force still-live lanes to move.
    node_columns = [row.graph.plain.split("\n", 1)[0].index("●") for row in rows]
    assert node_columns == [1, 1, 5, 1]
    assert_topology(rows, commits)


def test_lane_colors_are_stable_and_nodes_match_their_rails() -> None:
    commits = (commit("m", "a", "b"), commit("a"), commit("b"))
    rows = render_history(history(*commits))

    console = Console()
    first_color = rows[0].graph.get_style_at_offset(console, 1).color
    continued_color = rows[1].graph.get_style_at_offset(console, 1).color
    second_lane_color = rows[2].graph.get_style_at_offset(console, 5).color
    assert first_color == Color.parse(GRAPH_PALETTE[0])
    assert continued_color == first_color
    assert second_lane_color != first_color


def test_recycled_branch_colors_do_not_match_the_still_active_main_lane() -> None:
    commits = []
    for index in range(30):
        parent = f"main-{index + 1}"
        commits.extend(
            (commit(f"main-{index}", parent, f"topic-{index}"), commit(f"topic-{index}", parent))
        )
    commits.append(commit("main-30"))
    rows = render_history(history(*commits))
    console = Console()
    main_color = rows[0].graph.get_style_at_offset(console, 1).color
    for row in rows:
        node_x = row.graph.plain.index("●")
        color = row.graph.get_style_at_offset(console, node_x).color
        if row.commit.oid.startswith("main-"):
            assert color == main_color
        else:
            assert color != main_color
    assert_topology(rows, tuple(commits))


def test_truncated_parents_continue_below_last_loaded_commit() -> None:
    commits = (commit("a", "missing-one", "missing-two"),)
    rows = render_history(history(*commits))

    assert rows[0].graph.plain.split("\n")[-1].count("┊") == 2
    assert traced_parents(rows) == {"a": set()}


def test_native_fallback_without_structured_commits() -> None:
    source = (HistoryLine("* "), HistoryLine("|\\"))
    rows = render_history(source)
    assert [row.graph.plain for row in rows] == ["● ", "│╲"]
    assert all(row.commit is None for row in rows)


def test_graph_row_width_and_height_measure_physical_lines() -> None:
    row = GraphRow(Text("界\nabc", no_wrap=True), None)
    assert row.height == 2
    assert row.width == 3


def test_repeated_parent_ids_are_deduplicated() -> None:
    commits = (commit("a", "b", "b", "b"), commit("b"))
    rows = render_history(history(*commits))
    assert rows[0].height == 2
    assert_topology(rows, commits)


@pytest.mark.parametrize("style", ["smooth", "compact"])
def test_chronological_graph_marks_parents_above_without_dangling_lanes(style) -> None:
    commits = (
        commit("newer-parent"),
        commit("child", "newer-parent", "older-parent"),
        commit("older-parent"),
    )
    source = tuple(HistoryLine("", item) for item in commits)
    rows = render_history(source, style)
    commit_rows = tuple(row for row in rows if row.commit is not None)

    assert tuple(row.commit for row in commit_rows) == commits
    assert "↑ parent newer-pa" in commit_rows[1].graph.plain
    assert "┊" not in "\n".join(physical(rows))
    # Metadata stays intact even though an upward edge is a named backreference.
    assert commit_rows[1].commit.parents == ("newer-parent", "older-parent")
    assert traced_parents(group_commit_rows(rows)) == {
        "newer-parent": set(),
        "child": {"older-parent"},
        "older-parent": set(),
    }


@pytest.mark.parametrize("style", ["smooth", "compact"])
def test_chronological_graph_deduplicates_multiple_upward_parents(style) -> None:
    commits = (
        commit("first"),
        commit("second"),
        commit("child", "first", "second", "first"),
    )
    rows = render_history(tuple(HistoryLine("", item) for item in commits), style)
    child = next(row for row in rows if row.commit and row.commit.oid == "child")
    assert "↑ parent first, second" in child.graph.plain
    assert "┊" not in child.graph.plain


def test_generated_compact_graph_keeps_required_connectors_but_omits_spacers() -> None:
    commits = (
        commit("merge", "left", "right"),
        commit("left", "base"),
        commit("right", "base"),
        commit("base"),
    )
    source = tuple(HistoryLine("", item) for item in commits)
    rows = render_history(source, "compact")
    assert all(row.height == 1 for row in rows)
    assert all(row.graph.no_wrap for row in rows)
    assert tuple(row.commit for row in rows if row.commit) == commits
    assert any(row.commit is None for row in rows)
    assert len(rows) < sum(row.height for row in render_history(source, "smooth"))
    assert_topology(group_commit_rows(rows), commits)


def group_commit_rows(rows: tuple[GraphRow, ...]) -> tuple[GraphRow, ...]:
    """Group compact connector rows with their source for the topology tracer."""
    grouped: list[GraphRow] = []
    for row in rows:
        if row.commit is not None:
            grouped.append(GraphRow(row.graph.copy(), row.commit))
        else:
            grouped[-1].graph.append("\n").append(row.graph)
    return tuple(grouped)


def test_invalid_style_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown graph style"):
        render_history((), "wide")  # type: ignore[arg-type]


@pytest.mark.parametrize("style", ["smooth", "compact"])
def test_shuffled_chronological_graphs_keep_forward_edges_and_name_upward_parents(style) -> None:
    randomizer = random.Random(19_734)
    for _ in range(40):
        commits = [
            commit(f"c{index}", *randomizer.sample([f"c{j}" for j in range(index)], min(index, 3)))
            for index in range(12)
        ]
        randomizer.shuffle(commits)
        positions = {item.oid: index for index, item in enumerate(commits)}
        rows = render_history(tuple(HistoryLine("", item) for item in commits), style)
        grouped = group_commit_rows(rows)
        assert [row.commit for row in grouped] == commits
        assert traced_parents(grouped) == {
            item.oid: {p for p in item.parents if positions[p] > positions[item.oid]}
            for item in commits
        }
        assert "┊" not in "\n".join(physical(rows))
        for row in grouped:
            above = [p for p in row.commit.parents if positions[p] < positions[row.commit.oid]]
            if above:
                assert "↑ parent " + ", ".join(above) in row.graph.plain
            else:
                assert "↑ parent " not in row.graph.plain


def test_deterministic_random_valid_dags_have_exact_rendered_topology() -> None:
    randomizer = random.Random(947_031)
    for _ in range(60):
        size = randomizer.randint(2, 18)
        commits: list[Commit] = []
        for index in range(size):
            candidates = list(range(index + 1, size))
            count = randomizer.randint(0, min(4, len(candidates)))
            parents = randomizer.sample(candidates, count)
            commits.append(commit(f"c{index}", *(f"c{parent}" for parent in parents)))
        source = history(*commits)
        first = render_history(source)
        second = render_history(source)
        assert [row.graph.plain for row in first] == [row.graph.plain for row in second]
        assert_topology(first, tuple(commits))
        assert_prefixes_stitch(tuple(commits))
        compact = render_history(tuple(HistoryLine("", item) for item in commits), "compact")
        assert_topology(group_commit_rows(compact), tuple(commits))
