from dataclasses import replace
from pathlib import Path

import pytest

from rhizome.git import Repository
from rhizome.models import Commit, HistoryLine, Ref, Remote, Snapshot, Stash, Worktree
from rhizome.ref_badges import build_ref_badges


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
        worktrees=(Worktree(Path("/demo"), "tip", name, current=True, primary=True),),
    )
    badge = build_ref_badges(snapshot)["tip"]
    assert badge.text.plain.startswith("⑂ ↗ ")
    assert "▤" not in badge.text.plain
    assert "Worktree:" not in badge.description.plain
    clipped = badge.text.copy()
    clipped.truncate(12, overflow="ellipsis")
    assert "⑂" in clipped.plain and "↗" in clipped.plain
    assert clipped.plain.endswith("…")
    assert f"refs/heads/{name}" in badge.description.plain
    assert f"refs/remotes/origin/{name}" in badge.description.plain
    assert "last-fetched" in badge.description.plain


@pytest.mark.parametrize("checkout", ["main", "topic", "detached"])
def test_primary_checkout_does_not_add_worktree_marker(git_repo, git_run, checkout):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    if checkout == "topic":
        git_run(git_repo, "switch", "-c", "topic")
    elif checkout == "detached":
        git_run(git_repo, "switch", "--detach")
    snapshot = Repository(git_repo).snapshot()

    badge = build_ref_badges(snapshot)[snapshot.head]

    assert badge.text.plain.startswith("⑂ ")
    assert "▤" not in badge.text.plain
    assert "Worktree:" not in badge.description.plain


@pytest.mark.parametrize("detached", [False, True])
def test_linked_worktree_marker_is_independent_of_open_checkout(
    git_repo, git_run, tmp_path, detached
):
    git_run(git_repo, "commit", "--allow-empty", "-m", "initial")
    primary_head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "switch", "-c", "topic")
    linked = tmp_path / "linked"
    mode = ["--detach"] if detached else []
    # Even a linked checkout of main gets the marker; branch names do not decide it.
    git_run(git_repo, "worktree", "add", *mode, str(linked), "main")
    git_run(linked, "commit", "--allow-empty", "-m", "advance linked")
    linked_head = git_run(linked, "rev-parse", "HEAD").stdout.strip()

    for root in (git_repo, linked):
        badges = build_ref_badges(Repository(root).snapshot())

        assert "⑂" in badges[primary_head].text.plain
        assert "▤" not in badges[primary_head].text.plain
        assert "Worktree:" not in badges[primary_head].description.plain
        assert "▤" in badges[linked_head].text.plain
        assert str(linked) in badges[linked_head].description.plain
        assert ("(current)" in badges[linked_head].description.plain) == (root == linked)


def test_worktree_marker_shares_local_and_remote_tip_without_repeating_names():
    snapshot = Snapshot(
        Path("/demo"),
        "main",
        head="tip",
        refs=(
            Ref("main", "refs/heads/main", "tip", "local", True, "origin/main"),
            Ref("origin/main", "refs/remotes/origin/main", "tip", "remote", remote="origin"),
        ),
        remotes=(Remote("origin", "https://github.com/example/demo.git", provider="GitHub"),),
        worktrees=(
            Worktree(Path("/demo"), "tip", "main", current=True, primary=True),
            Worktree(
                Path("/linked [literal]"), "tip", detached=True, locked=True, lock_reason="in use"
            ),
            Worktree(Path("/linked-topic"), "tip", "topic"),
        ),
    )
    badges = build_ref_badges(snapshot)
    assert list(badges) == ["tip"]
    badge = badges["tip"]
    assert badge.text.plain == "⑂ ▤ ↗ main · origin/main"
    clipped = badge.text.copy()
    clipped.truncate(12, overflow="ellipsis")
    assert all(icon in clipped.plain for icon in ("⑂", "▤", "↗"))
    assert "Worktree: /demo" not in badge.description.plain
    assert "▤ Worktree: /linked-topic · topic" in badge.description.plain
    assert (
        "▤ Worktree: /linked [literal] · detached HEAD (locked: in use)" in badge.description.plain
    )
    assert "refs/heads/main" in badge.description.plain
    assert "refs/remotes/origin/main" in badge.description.plain


def test_detached_worktree_uses_its_registered_head_without_inventing_a_local_ref():
    worktree = Worktree(Path("/demo/detached"), "detached-tip", detached=True)
    snapshot = Snapshot(Path("/demo"), "main", head="main-tip", worktrees=(worktree,))
    badge = build_ref_badges(snapshot)["detached-tip"]
    assert badge.text.plain == "▤ detached"
    assert "detached HEAD" in badge.description.plain
    assert "/demo/detached" in badge.description.plain
    assert "▤" not in build_ref_badges(snapshot)["main-tip"].text.plain
    assert not any(
        hasattr(span.style, "meta") and "context_ref" in span.style.meta
        for span in badge.text.spans
    )


def test_detached_worktree_path_cannot_introduce_extra_graph_lines():
    path = Path("/demo/line\nbreak\t[tree]")
    snapshot = Snapshot(Path("/demo"), "main", worktrees=(Worktree(path, "tip", detached=True),))
    badge = build_ref_badges(snapshot)["tip"]
    assert badge.text.plain == r"▤ line\nbreak\t[tree]"
    assert str(path) in badge.description.plain


def test_worktree_markers_follow_head_moves_and_registration_removal():
    worktree = Worktree(Path("/demo/linked"), "old-tip", "topic")
    snapshot = Snapshot(
        Path("/demo"),
        "main",
        refs=(Ref("main", "refs/heads/main", "old-tip", "local"),),
        worktrees=(worktree,),
    )
    assert "▤" in build_ref_badges(snapshot)["old-tip"].text.plain
    moved = replace(
        snapshot, worktrees=(replace(worktree, head="new-tip", detached=True, branch=""),)
    )
    badges = build_ref_badges(moved)
    assert "▤" not in badges["old-tip"].text.plain
    assert badges["new-tip"].text.plain == "▤ linked"
    removed = build_ref_badges(replace(moved, worktrees=()))
    assert set(removed) == {"old-tip"}
    assert "▤" not in removed["old-tip"].text.plain


def test_unborn_and_bare_worktrees_do_not_add_badges_and_prunable_state_is_explicit():
    snapshot = Snapshot(
        Path("/demo"),
        "main",
        worktrees=(
            Worktree(Path("/unborn"), branch="main"),
            Worktree(Path("/bare"), "bare-head", bare=True),
            Worktree(Path("/missing"), "tip", "topic", prunable=True, prune_reason="missing path"),
        ),
    )
    badges = build_ref_badges(snapshot)
    assert set(badges) == {"tip"}
    assert badges["tip"].text.plain == "▤ missing"
    assert "prunable: missing path" in badges["tip"].description.plain


def test_worktree_selection_emphasizes_only_its_marker():
    worktree = Worktree(Path("/linked"), "tip", "topic")
    snapshot = Snapshot(Path("/demo"), "main", worktrees=(worktree,))
    normal = build_ref_badges(snapshot)["tip"]
    selected = build_ref_badges(snapshot, str(worktree.path))["tip"]
    assert normal.text.plain == selected.text.plain
    assert normal.description == selected.description
    assert normal.text != selected.text
    assert any("underline" in str(span.style) for span in selected.text.spans)


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
    assert "⑂" in badges["local-tip"].text.plain
    assert "↗" not in badges["local-tip"].text.plain
    assert "↗" in badges["remote-tip"].text.plain
    assert "⑂" not in badges["remote-tip"].text.plain
    updated = replace(snapshot, refs=(local, replace(remote, oid="local-tip")))
    badges = build_ref_badges(updated)
    assert set(badges) == {"local-tip"}
    assert badges["local-tip"].text.plain.startswith("⑂ ↗ ")


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
    assert badge.text.plain.startswith("⑂ ↗ ")
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
    assert badges["detached"].text.plain.startswith("⑂ ◇ HEAD")
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
