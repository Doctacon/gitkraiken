import re

import pytest

from rhizome.git import GitError, Repository
from rhizome.investigation import InvestigationService


def _blame_oracle(repo, git_run, revision, path):
    output = git_run(
        repo, "--literal-pathspecs", "blame", "--line-porcelain", revision, "--", path
    ).stdout
    headers = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) in (3, 4) and re.fullmatch(r"[0-9a-f]{40,64}", fields[0]):
            headers.append((fields[0], int(fields[1]), int(fields[2])))
    return headers


def test_file_history_pages_across_rename_with_literal_paths(git_repo, git_run):
    old, new = "old[one]?.txt", ":(literal)new.txt"
    (git_repo / old).write_text("line one\nline two\n")
    git_run(git_repo, "--literal-pathspecs", "add", "--", old)
    git_run(git_repo, "commit", "-m", "original file")
    original = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "mv", "--", old, new)
    git_run(git_repo, "commit", "-m", "rename file")
    renamed = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    first = service.file_history(scope, renamed, new, limit=1)

    assert not first.complete and first.next_cursor
    assert [
        (entry.commit.oid, entry.path, entry.previous_path, entry.status) for entry in first.entries
    ] == [(renamed, new, old, "R")]
    second = service.file_history(scope, renamed, new, cursor=first.next_cursor, limit=1)
    assert second.complete and second.next_cursor is None
    assert [(entry.commit.oid, entry.path, entry.status) for entry in second.entries] == [
        (original, old, "A")
    ]
    oracle = git_run(
        git_repo, "--literal-pathspecs", "log", "--follow", "--format=%H", "--", new
    ).stdout.splitlines()
    assert [first.entries[0].commit.oid, second.entries[0].commit.oid] == oracle
    assert "rename-heuristic" in second.limitations
    assert second.state.current


def test_blame_pages_preserve_lines_rename_provenance_and_boundaries(git_repo, git_run):
    old, new = "original\tname.txt", ":(glob)*renamed.txt"
    (git_repo / old).write_text("one\ntwo\nthree")
    git_run(git_repo, "--literal-pathspecs", "add", "--", old)
    git_run(git_repo, "commit", "-m", "original")
    original = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "mv", "--", old, new)
    (git_repo / new).write_text("one\nchanged\nthree")
    git_run(git_repo, "--literal-pathspecs", "add", "--", new)
    git_run(git_repo, "commit", "-m", "rename and edit")
    revision = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    first = service.blame(scope, revision, new, limit=2)

    assert not first.complete and first.next_cursor
    assert [(line.final_line, line.text) for line in first.lines] == [(1, "one"), (2, "changed")]
    assert first.lines[0].commit_oid == original
    assert first.lines[0].original_path == old
    assert first.lines[0].original_line == 1
    assert first.lines[0].boundary
    assert first.lines[1].commit_oid == revision
    assert not first.lines[1].boundary
    second = service.blame(scope, revision, new, cursor=first.next_cursor, limit=2)
    assert second.complete and second.next_cursor is None
    assert [(line.final_line, line.text) for line in second.lines] == [(3, "three")]
    assert second.lines[0].original_path == old
    assert "rename-heuristic" in second.limitations
    oracle = _blame_oracle(git_repo, git_run, revision, new)
    actual = [
        (line.commit_oid, line.original_line, line.final_line)
        for line in (*first.lines, *second.lines)
    ]
    assert actual == oracle


def test_file_history_no_follow_stops_at_rename(git_repo, git_run):
    old, new = "before.txt", "after.txt"
    (git_repo / old).write_text("one\n")
    git_run(git_repo, "add", old)
    git_run(git_repo, "commit", "-m", "original")
    git_run(git_repo, "mv", old, new)
    git_run(git_repo, "commit", "-m", "rename")
    renamed = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    page = service.file_history(scope, renamed, new, follow_renames=False)
    oracle = git_run(
        git_repo, "--literal-pathspecs", "log", "--format=%H", "--", new
    ).stdout.splitlines()

    assert [entry.commit.oid for entry in page.entries] == oracle == [renamed]
    assert page.follow_renames is False
    assert page.limitations == ()


def test_file_history_and_blame_preserve_unicode_newline_quoted_path(git_repo, git_run):
    path = '雪 "quoted"\nline.txt'
    (git_repo / path).write_text("alpha\nbeta\n")
    git_run(git_repo, "--literal-pathspecs", "add", "--", path)
    git_run(git_repo, "commit", "-m", "literal path")
    first = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    (git_repo / path).write_text("alpha\nchanged\n")
    git_run(git_repo, "--literal-pathspecs", "add", "--", path)
    git_run(git_repo, "commit", "-m", "literal update")
    revision = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    history = service.file_history(scope, revision, path)
    blame = service.blame(scope, revision, path)

    assert [entry.commit.oid for entry in history.entries] == [revision, first]
    assert all(entry.path == path for entry in history.entries)
    assert [(line.final_line, line.text) for line in blame.lines] == [
        (1, "alpha"),
        (2, "changed"),
    ]
    assert all(line.original_path == path for line in blame.lines)


def test_file_and_blame_cursors_bind_query_scope_and_mode(git_repo, git_run):
    path = "bound.txt"
    (git_repo / path).write_text("one\ntwo\n")
    git_run(git_repo, "add", path)
    git_run(git_repo, "commit", "-m", "first")
    first = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    (git_repo / path).write_text("one\nchanged\n")
    git_run(git_repo, "add", path)
    git_run(git_repo, "commit", "-m", "second")
    revision = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    file_cursor = service.file_history(scope, revision, path, limit=1).next_cursor
    blame_cursor = service.blame(scope, revision, path, limit=1).next_cursor
    assert file_cursor and blame_cursor

    for kwargs in (
        {"start_oid": first, "path": path, "follow_renames": True},
        {"start_oid": revision, "path": "other.txt", "follow_renames": True},
        {"start_oid": revision, "path": path, "follow_renames": False},
    ):
        with pytest.raises(ValueError, match="cursor"):
            service.file_history(scope, cursor=file_cursor, **kwargs)
    other_scope = service.capture((first,))
    with pytest.raises(ValueError, match="cursor"):
        service.file_history(other_scope, revision, path, cursor=file_cursor)
    with pytest.raises(ValueError, match="cursor"):
        service.blame(scope, revision, path, cursor=file_cursor)
    with pytest.raises(ValueError, match="cursor"):
        service.file_history(scope, revision, path, cursor=blame_cursor)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1001, 1.5])
def test_file_history_and_blame_reject_invalid_limits(git_repo, limit):
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(ValueError, match="limit"):
        service.file_history(scope, "f" * 40, "file.txt", limit=limit)
    with pytest.raises(ValueError, match="limit"):
        service.blame(scope, "f" * 40, "file.txt", limit=limit)


def test_file_history_rejects_non_boolean_follow_mode(git_repo):
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(ValueError, match="Follow"):
        service.file_history(
            scope,
            "f" * 40,
            "file.txt",
            follow_renames=1,  # type: ignore[arg-type]
        )


def test_file_history_and_blame_reject_missing_file_and_revision(git_repo, git_run):
    (git_repo / "present.txt").write_text("present\n")
    git_run(git_repo, "add", "present.txt")
    git_run(git_repo, "commit", "-m", "present")
    revision = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    for operation in (service.file_history, service.blame):
        with pytest.raises(GitError, match="(?i)commit|scope|revision"):
            operation(scope, "f" * 40, "present.txt")
    with pytest.raises(GitError, match="(?i)file|path"):
        service.blame(scope, revision, "missing.txt")
    with pytest.raises(GitError, match="(?i)file|path"):
        service.file_history(scope, revision, "missing.txt")


def test_blame_empty_file_is_valid(git_repo, git_run):
    (git_repo / "empty.txt").write_bytes(b"")
    git_run(git_repo, "add", "empty.txt")
    git_run(git_repo, "commit", "-m", "empty")
    revision = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    page = service.blame(scope, revision, "empty.txt", limit=1)

    assert page.lines == ()
    assert page.complete
    assert page.next_cursor is None


@pytest.mark.parametrize("kind", ["binary", "oversized"])
def test_blame_explicitly_rejects_unsupported_content(git_repo, git_run, kind):
    if kind == "binary":
        name, content, message = "binary.dat", b"before\x00after", "binary"
    else:
        name = "oversized.txt"
        content = b"x" * (2 * 1024 * 1024 + 1)
        message = "oversized|2 MiB"
    (git_repo / name).write_bytes(content)
    git_run(git_repo, "add", name)
    git_run(git_repo, "commit", "-m", name)
    revision = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(GitError, match=f"(?i){message}"):
        service.blame(scope, revision, name)
