from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Tree

from rhizome.commit_files import CommitFileTree
from rhizome.git import Repository
from rhizome.graph import render_history
from rhizome.models import Commit, HistoryLine, Ref, Remote, Snapshot, Stash, Worktree
from rhizome.ref_badges import build_ref_badges
from rhizome.sidebar import ReferenceSidebar


def _commit(oid: str, *parents: str, subject: str = "literal 树 subject") -> Commit:
    return Commit(oid, tuple(parents), "Zoë", "zoë@example.test", "2025-01-01", subject)


def test_plain_history_uses_single_cell_ascii_and_keeps_native_graph_literal() -> None:
    commits = (
        _commit("merge", "left", "right"),
        _commit("left", "base"),
        _commit("right", "base"),
        _commit("base"),
    )
    source = tuple(HistoryLine("", commit) for commit in commits)

    normal = render_history(source)
    plain = render_history(source, plain_glyphs=True)

    assert "├───╮" in normal[0].graph.plain
    assert "+---\\" in plain[0].graph.plain
    assert "+---/" in plain[2].graph.plain
    assert {row.commit for row in plain} == set(commits)
    assert plain[0].commit.subject == "literal 树 subject"
    assert not set("●│┊├┤─╴╶╮╭╰╯↑") & set("\n".join(row.graph.plain for row in plain))

    native = (HistoryLine("\x1b[31m* |\\/_ literal 树\x1b[0m"),)
    native_plain = render_history(native, "compact", plain_glyphs=True)[0].graph
    assert native_plain.plain == "* |\\/_ literal 树"
    assert native_plain.spans  # ANSI styling is retained while the text stays literal.


def test_plain_history_names_upward_parents_without_unicode_status_marks() -> None:
    parent = _commit("parent")
    child = _commit("child", "parent")

    rows = render_history((HistoryLine("", parent), HistoryLine("", child)), plain_glyphs=True)

    assert "^ parent parent" in rows[1].graph.plain
    assert "↑" not in rows[1].graph.plain


def test_plain_ref_badges_change_generated_markers_but_not_repository_text() -> None:
    branch = "feature/naïve-树"
    path = Path("/linked/工作树")
    snapshot = Snapshot(
        Path("/repo/根"),
        branch,
        head="tip",
        refs=(
            Ref(branch, f"refs/heads/{branch}", "tip", "local", True, f"origin/{branch}"),
            Ref(
                f"origin/{branch}",
                f"refs/remotes/origin/{branch}",
                "tip",
                "remote",
                remote="origin",
            ),
            Ref("v雪", "refs/tags/v雪", "tip", "tag"),
        ),
        remotes=(Remote("origin", "https://github.com/example/树.git", provider="GitHub"),),
        worktrees=(Worktree(path, "tip", branch, locked=True, lock_reason="占用"),),
        stashes=(Stash("stash@{0}", "tip", "保留 · literal → text"),),
    )

    normal = build_ref_badges(snapshot)["tip"]
    plain = build_ref_badges(snapshot, f"refs/heads/{branch}", plain_glyphs=True)["tip"]

    assert normal.text.plain.startswith("💻 🌳 🐙 ◇ ▣ ")
    assert plain.text.plain.startswith("[L] [WT] [GH] [T] [S] ")
    assert "💻" not in plain.description.plain
    assert "🌳" not in plain.description.plain
    assert f"refs/heads/{branch} -> origin/{branch}" in plain.description.plain
    assert f"[WT] Worktree: {path} | {branch} (locked: 占用)" in plain.description.plain
    assert "[S] stash@{0}: 保留 · literal → text" in plain.description.plain
    assert branch in plain.text.plain
    assert "v雪" in plain.text.plain
    assert any("underline" in str(span.style) for span in plain.text.spans)
    assert any(
        hasattr(span.style, "meta") and span.style.meta.get("context_ref") == f"refs/heads/{branch}"
        for span in plain.text.spans
    )


class _PlainWidgetApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ReferenceSidebar(id="reference-sidebar")
        yield CommitFileTree(id="commit-file-tree")


@pytest.mark.asyncio
async def test_plain_widget_trees_keep_real_unicode_repo_names_and_ascii_tree_chrome(
    git_repo: Path, git_run
) -> None:
    filename = "src/naïve-树.txt"
    (git_repo / "src").mkdir()
    (git_repo / filename).write_text("first\n")
    git_run(git_repo, "add", filename)
    git_run(git_repo, "commit", "-m", "literal 树 commit")
    git_run(git_repo, "switch", "-c", "feature/naïve-树")
    (git_repo / filename).write_text("second\n")
    git_run(git_repo, "commit", "-am", "second literal 树")
    repository = Repository(git_repo)
    snapshot = repository.snapshot()
    files = repository.detail(snapshot.head).files

    async with _PlainWidgetApp().run_test(size=(80, 30)) as pilot:
        sidebar = pilot.app.query_one(ReferenceSidebar)
        file_tree = pilot.app.query_one(CommitFileTree)
        sidebar.set_plain_glyphs(True)
        file_tree.set_plain_glyphs(True)
        sidebar.update_snapshot(snapshot, None)
        file_tree.set_files(files, identity=snapshot.head)
        await pilot.pause()

        assert sidebar.query_one("#ref-filter").placeholder == "Filter refs..."
        branch_key = "local:refs/heads/feature/naïve-树"
        assert sidebar.get_selection(branch_key).target == "refs/heads/feature/naïve-树"
        assert sidebar._tree_nodes[branch_key].label.plain == "naïve-树"
        file = next(change for change in files if change.path == filename)
        assert file_tree._path_nodes[file_tree._key_for(file)].label.plain == "M naïve-树.txt"

        for tree in (sidebar.query_one(Tree), file_tree):
            assert tree.ICON_NODE == "> "
            assert tree.ICON_NODE_EXPANDED == "v "
            assert tree.LINES["default"] == ("  ", "| ", "\\-", "+-")
            rendered = "\n".join(tree.render_line(y).text for y in range(tree.size.height))
            assert "▶" not in rendered and "▼" not in rendered
            assert not set("│└─├") & set(rendered)

        sidebar.set_plain_glyphs(False)
        file_tree.set_plain_glyphs(False)
        await pilot.pause()
        assert sidebar._tree.ICON_NODE == "▶ "
        assert file_tree.ICON_NODE_EXPANDED == "▼ "
