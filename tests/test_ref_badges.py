from dataclasses import replace
from pathlib import Path

from gitkraiken.models import Commit, HistoryLine, Ref, Remote, Snapshot, Stash
from gitkraiken.ref_badges import build_ref_badges


def test_aligned_local_and_github_tips_show_both_markers_before_long_names():
    name = "feature/jlott/a-very-long-branch-name"
    snapshot = Snapshot(
        Path("/demo"),
        name,
        head="tip",
        refs=(
            Ref(name, f"refs/heads/{name}", "tip", "local", True, f"origin/{name}"),
            Ref(f"origin/{name}", f"refs/remotes/origin/{name}", "tip", "remote", remote="origin"),
        ),
        remotes=(Remote("origin", "https://github.com/example/demo.git", provider="GitHub"),),
    )
    badge = build_ref_badges(snapshot)["tip"]
    assert badge.text.plain.startswith("💻 🐙 ")
    clipped = badge.text.copy()
    clipped.truncate(12, overflow="ellipsis")
    assert "💻" in clipped.plain and "🐙" in clipped.plain
    assert clipped.plain.endswith("…")
    assert f"refs/heads/{name}" in badge.description.plain
    assert f"refs/remotes/origin/{name}" in badge.description.plain
    assert "last-fetched" in badge.description.plain


def test_local_and_remote_move_independently_and_reunite_after_fetch():
    local = Ref("main", "refs/heads/main", "local-tip", "local", True, "origin/main")
    remote = Ref("origin/main", "refs/remotes/origin/main", "remote-tip", "remote", remote="origin")
    snapshot = Snapshot(
        Path("/demo"),
        "main",
        head="local-tip",
        refs=(local, remote),
        remotes=(Remote("origin", "https://github.com/example/demo.git", provider="GitHub"),),
    )
    badges = build_ref_badges(snapshot)
    assert "💻" in badges["local-tip"].text.plain
    assert "🐙" not in badges["local-tip"].text.plain
    assert "🐙" in badges["remote-tip"].text.plain
    assert "💻" not in badges["remote-tip"].text.plain
    updated = replace(snapshot, refs=(local, replace(remote, oid="local-tip")))
    badges = build_ref_badges(updated)
    assert set(badges) == {"local-tip"}
    assert badges["local-tip"].text.plain.startswith("💻 🐙 ")


def test_typed_refs_keep_colliding_short_names_and_exact_slash_remote_provider():
    snapshot = Snapshot(
        Path("/demo"),
        "origin/main",
        head="tip",
        refs=(
            Ref("origin/main", "refs/heads/origin/main", "tip", "local", True),
            Ref("origin/main", "refs/remotes/origin/main", "tip", "remote", remote="origin"),
            Ref(
                "team/origin/main",
                "refs/remotes/team/origin/main",
                "tip",
                "remote",
                remote="team/origin",
            ),
        ),
        remotes=(
            Remote("origin", "https://github.com/example/demo.git", provider="GitHub"),
            Remote("team", "https://github.com/example/other.git", provider="GitHub"),
            Remote("team/origin", "https://dev.azure.com/example/demo", provider="Azure DevOps"),
        ),
    )
    badge = build_ref_badges(snapshot)["tip"]
    assert badge.text.plain.startswith("💻 🐙 ☁ ")
    assert "Local: refs/heads/origin/main" in badge.description.plain
    assert "GitHub remote: refs/remotes/origin/main" in badge.description.plain
    assert "Azure DevOps remote: refs/remotes/team/origin/main" in badge.description.plain


def test_adding_a_new_branch_at_same_commit_adds_pointer_not_a_graph_node():
    original = Snapshot(
        Path("/demo"),
        "main",
        head="tip",
        refs=(Ref("main", "refs/heads/main", "tip", "local", True),),
    )
    updated = replace(
        original,
        branch="Connor",
        refs=(
            replace(original.refs[0], current=False),
            Ref("Connor", "refs/heads/Connor", "tip", "local", True),
        ),
    )
    badges = build_ref_badges(updated)
    assert list(badges) == ["tip"]
    assert "Connor" in badges["tip"].text.plain and "main" in badges["tip"].text.plain
    assert "Checked out: Connor" in badges["tip"].description.plain


def test_detached_head_tags_stashes_and_unassigned_remote_refs_are_honest():
    snapshot = Snapshot(
        Path("/demo"),
        "",
        head="detached",
        refs=(
            Ref("v1", "refs/tags/v1", "detached", "tag"),
            Ref("gone/main", "refs/remotes/gone/main", "remote", "remote"),
        ),
        stashes=(Stash("stash@{2}", "stash-tip", "[literal] work"),),
    )
    badges = build_ref_badges(snapshot)
    assert badges["detached"].text.plain.startswith("💻 ◇ HEAD")
    assert "Detached HEAD" in badges["detached"].description.plain
    assert badges["remote"].text.plain.startswith("↗ ")
    assert badges["stash-tip"].text.plain == "▣ stash@{2}"
    assert "[literal] work" in badges["stash-tip"].description.plain


def test_lossy_commit_ref_strings_never_invent_pointer_roles():
    snapshot = Snapshot(
        Path("/demo"),
        "",
        history=(
            HistoryLine(
                "*", Commit("tip", (), "A", "a@example.test", "", "test", ("main", "origin/main"))
            ),
        ),
    )
    assert build_ref_badges(snapshot) == {}


def test_selection_changes_emphasis_only_and_hover_text_is_bounded():
    snapshot = Snapshot(
        Path("/demo"),
        "main",
        head="tip",
        refs=tuple(Ref(f"topic-{i}", f"refs/heads/topic-{i}", "tip", "local") for i in range(30)),
    )
    normal = build_ref_badges(snapshot)["tip"]
    selected = build_ref_badges(snapshot, "refs/heads/topic-20")["tip"]
    assert selected.text.plain == normal.text.plain
    assert selected.description == normal.description
    assert selected.text != normal.text
    assert any("underline" in str(span.style) for span in selected.text.spans)
    tooltip = next(
        span.style.meta["ref_description"]
        for span in selected.text.spans
        if hasattr(span.style, "meta") and "ref_description" in span.style.meta
    )
    assert len(tooltip.splitlines()) == 21
    assert "Full refs" in tooltip
    assert "refs/heads/topic-29" in selected.description.plain
