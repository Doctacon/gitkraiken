from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from rhizome.git import GitError, Repository
from rhizome.patches import PatchDocument, PatchService


def _commit_file(git_repo: Path, git_run, path: str, data: bytes) -> None:
    target = git_repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    git_run(git_repo, "add", "--", path)
    git_run(git_repo, "commit", "-m", "base")


def _index_blob(git_repo: Path, path: str) -> bytes | None:
    listing = subprocess.run(
        ["git", "--literal-pathspecs", "ls-files", "--stage", "-z", "--", path],
        cwd=git_repo,
        check=True,
        capture_output=True,
    ).stdout
    encoded = os.fsencode(path)
    for record in listing.split(b"\x00"):
        metadata, separator, name = record.partition(b"\t")
        if separator and name == encoded:
            oid = metadata.split()[1].decode("ascii")
            return subprocess.run(
                ["git", "cat-file", "blob", oid],
                cwd=git_repo,
                check=True,
                capture_output=True,
            ).stdout
    return None


def _change_range(document: PatchDocument, *needles: str) -> tuple[int, int]:
    matches = [
        line.id
        for line in document.lines
        if line.kind in {"add", "delete"} and any(needle in line.text for needle in needles)
    ]
    assert matches
    return min(matches), max(matches)


def _all_changes(document: PatchDocument) -> tuple[int, int]:
    changes = [line.id for line in document.lines if line.kind in {"add", "delete"}]
    assert changes
    return min(changes), max(changes)


def test_split_index_expiry_keeps_the_visible_real_index_readable(git_repo, git_run, monkeypatch):
    _commit_file(git_repo, git_run, "notes.txt", b"before\n")
    git_run(git_repo, "config", "core.splitIndex", "true")
    git_run(git_repo, "update-index", "--split-index")
    git_run(git_repo, "config", "splitIndex.sharedIndexExpire", "now")
    git_run(git_repo, "config", "splitIndex.maxPercentChange", "0")
    (git_repo / "notes.txt").write_bytes(b"reviewed\n")
    service = PatchService(Repository(git_repo))
    document = service.open("notes.txt")
    run = subprocess.run
    readers = []

    def read_before_publication(command, *args, **kwargs):
        result = run(command, *args, **kwargs)
        if "update-index" in command and kwargs.get("env", {}).get("GIT_INDEX_FILE"):
            readers.append(
                run(
                    ["git", "ls-files", "--stage", "-z"],
                    cwd=git_repo,
                    capture_output=True,
                )
            )
        return result

    monkeypatch.setattr(subprocess, "run", read_before_publication)
    failure = None
    try:
        service.apply(document, *_all_changes(document))
    except GitError as error:
        failure = error
    assert len(readers) == 1
    assert readers[0].returncode == 0, readers[0].stderr
    assert failure is None
    assert _index_blob(git_repo, "notes.txt") == b"reviewed\n"
    assert (git_repo / "notes.txt").read_bytes() == b"reviewed\n"
    assert git_run(git_repo, "config", "splitIndex.sharedIndexExpire").stdout.strip() == "now"


def test_stage_selected_replacement_preserves_other_worktree_edit(git_repo: Path, git_run) -> None:
    _commit_file(git_repo, git_run, "notes.txt", b"one\ntwo\nthree\nfour\n")
    path = git_repo / "notes.txt"
    path.write_bytes(b"one\nTWO\nthree\nFOUR\n")

    service = PatchService(Repository(git_repo))
    document = service.open("notes.txt", context=0)
    start, end = _change_range(document, "two", "TWO")

    assert service.apply(document, start, end) == "Staged selected lines in notes.txt."

    assert _index_blob(git_repo, "notes.txt") == b"one\nTWO\nthree\nfour\n"
    assert path.read_bytes() == b"one\nTWO\nthree\nFOUR\n"


def test_partial_line_semantics_can_select_only_addition_or_deletion(
    git_repo: Path, git_run
) -> None:
    _commit_file(git_repo, git_run, "partial.txt", b"before\nold\nafter\n")
    path = git_repo / "partial.txt"
    path.write_bytes(b"before\nnew\nafter\n")
    service = PatchService(Repository(git_repo))

    addition_document = service.open("partial.txt", context=0)
    addition = next(line for line in addition_document.lines if line.kind == "add")
    service.apply(addition_document, addition.id, addition.id)
    assert _index_blob(git_repo, "partial.txt") == b"before\nold\nnew\nafter\n"

    git_run(git_repo, "restore", "--staged", "partial.txt")
    deletion_document = service.open("partial.txt", context=0)
    deletion = next(line for line in deletion_document.lines if line.kind == "delete")
    service.apply(deletion_document, deletion.id, deletion.id)
    assert _index_blob(git_repo, "partial.txt") == b"before\nafter\n"
    assert path.read_bytes() == b"before\nnew\nafter\n"


def test_whole_hunk_selection_uses_displayed_line_ids(git_repo: Path, git_run) -> None:
    _commit_file(git_repo, git_run, "hunk.txt", b"a\nb\nc\n")
    (git_repo / "hunk.txt").write_bytes(b"a\nB\nc\n")
    service = PatchService(Repository(git_repo))
    document = service.open("hunk.txt")
    hunk_lines = [line.id for line in document.lines if line.hunk == 1]

    service.apply(document, min(hunk_lines), max(hunk_lines))

    assert _index_blob(git_repo, "hunk.txt") == b"a\nB\nc\n"


def test_stage_across_multiple_hunks_and_delete_file(git_repo: Path, git_run) -> None:
    original = b"".join(f"line {number}\n".encode() for number in range(30))
    _commit_file(git_repo, git_run, "many.txt", original)
    edited = original.replace(b"line 2\n", b"LINE TWO\n").replace(b"line 27\n", b"")
    (git_repo / "many.txt").write_bytes(edited)
    service = PatchService(Repository(git_repo))
    document = service.open("many.txt", context=1)
    assert {line.hunk for line in document.lines if line.kind == "add"} == {1}
    assert {line.hunk for line in document.lines if line.kind == "delete"} == {1, 2}

    service.apply(document, *_all_changes(document))
    assert _index_blob(git_repo, "many.txt") == edited

    (git_repo / "many.txt").unlink()
    deletion = service.open("many.txt")
    service.apply(deletion, *_all_changes(deletion))
    assert _index_blob(git_repo, "many.txt") is None


def test_stage_from_mixed_state_preserves_prior_index_and_remaining_worktree_edit(
    git_repo: Path, git_run
) -> None:
    _commit_file(git_repo, git_run, "compose.txt", b"a\nb\nc\nd\n")
    path = git_repo / "compose.txt"
    path.write_bytes(b"a\nb\nstaged\nc\nd\n")
    git_run(git_repo, "add", "compose.txt")
    path.write_bytes(b"a\nb\nstaged\nc\nD\n")
    service = PatchService(Repository(git_repo))
    document = service.open("compose.txt", context=0)

    service.apply(document, *_change_range(document, "d", "D"))

    assert _index_blob(git_repo, "compose.txt") == b"a\nb\nstaged\nc\nD\n"
    assert path.read_bytes() == b"a\nb\nstaged\nc\nD\n"


def test_inverse_unstage_preserves_unrelated_staged_and_unstaged_content(
    git_repo: Path, git_run
) -> None:
    _commit_file(git_repo, git_run, "mixed.txt", b"a\nb\nc\nd\n")
    path = git_repo / "mixed.txt"
    path.write_bytes(b"a\nB\ninserted\nc\nd\n")
    git_run(git_repo, "add", "mixed.txt")
    path.write_bytes(b"a\nB\ninserted\nc\nD\n")
    service = PatchService(Repository(git_repo))
    document = service.open("mixed.txt", staged=True, context=0)
    start, end = _change_range(document, "b", "B")

    service.apply(document, start, end)

    # Literal expected index bytes are the oracle: the adjacent staged addition
    # survives while only the selected replacement returns to HEAD.
    assert _index_blob(git_repo, "mixed.txt") == b"a\nb\ninserted\nc\nd\n"
    assert path.read_bytes() == b"a\nB\ninserted\nc\nD\n"


def test_untracked_and_unborn_new_files_support_partial_stage_and_unstage(
    git_repo: Path, git_run
) -> None:
    path = git_repo / "new file.txt"
    path.write_bytes(b"first\nsecond\nthird\n")
    service = PatchService(Repository(git_repo))
    unstaged = service.open("new file.txt", context=0)
    second = next(line for line in unstaged.lines if line.kind == "add" and "second" in line.text)

    service.apply(unstaged, second.id, second.id)
    assert _index_blob(git_repo, "new file.txt") == b"second\n"
    assert path.read_bytes() == b"first\nsecond\nthird\n"

    staged = service.open("new file.txt", staged=True, context=0)
    service.apply(staged, *_all_changes(staged))
    assert _index_blob(git_repo, "new file.txt") is None
    assert path.read_bytes() == b"first\nsecond\nthird\n"


def test_no_newline_and_crlf_bytes_are_preserved(git_repo: Path, git_run) -> None:
    _commit_file(git_repo, git_run, "eol.txt", b"one\r\ntwo\r\nlast")
    path = git_repo / "eol.txt"
    path.write_bytes(b"one\r\nTWO\r\nlast changed")
    service = PatchService(Repository(git_repo))
    document = service.open("eol.txt", context=0)
    assert any("No newline at end of file" in line.text for line in document.lines)
    start, end = _change_range(document, "two", "TWO", "last", "last changed")

    service.apply(document, start, end)

    assert _index_blob(git_repo, "eol.txt") == b"one\r\nTWO\r\nlast changed"
    assert path.read_bytes() == b"one\r\nTWO\r\nlast changed"


def test_literal_odd_path_is_never_shell_interpolated(git_repo: Path, git_run) -> None:
    path_name = "- odd\nünicode.txt"
    path = git_repo / path_name
    path.write_bytes(b"old\n")
    git_run(git_repo, "add", "--", path_name)
    git_run(git_repo, "commit", "-m", "odd path")
    path.write_bytes("new λ\n".encode())
    service = PatchService(Repository(git_repo))
    document = service.open(path_name, context=0)

    service.apply(document, *_all_changes(document))

    assert _index_blob(git_repo, path_name) == "new λ\n".encode()
    assert not (git_repo / "odd").exists()


@pytest.mark.parametrize("changed", ["worktree", "index", "head"])
def test_apply_rejects_stale_repository_state(git_repo: Path, git_run, changed: str) -> None:
    _commit_file(git_repo, git_run, "stale.txt", b"old\n")
    path = git_repo / "stale.txt"
    path.write_bytes(b"new\n")
    service = PatchService(Repository(git_repo))
    document = service.open("stale.txt", context=0)
    before = _index_blob(git_repo, "stale.txt")

    if changed == "worktree":
        path.write_bytes(b"newer\n")
    elif changed == "index":
        (git_repo / "other.txt").write_bytes(b"other\n")
        git_run(git_repo, "add", "other.txt")
    else:
        git_run(git_repo, "commit", "--allow-empty", "-m", "new head")

    with pytest.raises(GitError, match="changed"):
        service.apply(document, *_all_changes(document))
    assert _index_blob(git_repo, "stale.txt") == before


def test_index_lock_failure_leaves_index_and_worktree_unchanged(git_repo: Path, git_run) -> None:
    _commit_file(git_repo, git_run, "locked.txt", b"old\n")
    path = git_repo / "locked.txt"
    path.write_bytes(b"new\n")
    service = PatchService(Repository(git_repo))
    document = service.open("locked.txt")
    before = _index_blob(git_repo, "locked.txt")
    lock_path = Path(git_run(git_repo, "rev-parse", "--git-path", "index.lock").stdout.strip())
    if not lock_path.is_absolute():
        lock_path = git_repo / lock_path
    lock_path.write_bytes(b"")
    try:
        with pytest.raises(GitError, match="apply selected patch to index failed"):
            service.apply(document, *_all_changes(document))
    finally:
        lock_path.unlink()

    assert _index_blob(git_repo, "locked.txt") == before
    assert path.read_bytes() == b"new\n"


def test_reconstructed_document_cannot_change_unreviewed_base_content(git_repo, git_run):
    _commit_file(git_repo, git_run, "canonical.txt", b"a\nb\nc\n")
    (git_repo / "canonical.txt").write_bytes(b"a\nB\nc\n")
    service = PatchService(Repository(git_repo))
    document = service.open("canonical.txt", context=0)
    altered = replace(document, _old_data=b"a\nb\nnot reviewed\n")

    with pytest.raises(GitError, match="changed"):
        service.apply(altered, *_all_changes(document))

    assert _index_blob(git_repo, "canonical.txt") == b"a\nb\nc\n"
    assert (git_repo / "canonical.txt").read_bytes() == b"a\nB\nc\n"


def test_external_writer_winning_index_lock_is_preserved(git_repo, git_run, monkeypatch):
    _commit_file(git_repo, git_run, "race.txt", b"a\nb\nc\nd\n")
    (git_repo / "race.txt").write_bytes(b"a\nB\nc\nD\n")
    service = PatchService(Repository(git_repo))
    document = service.open("race.txt", context=0)
    opened = os.open
    attempts = []

    def external_writer_first(path, flags, *args, **kwargs):
        if os.fspath(path) == "index.lock" and flags & os.O_EXCL and not attempts:
            attempts.append(True)
            git_run(git_repo, "add", "race.txt")
        return opened(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", external_writer_first)
    with pytest.raises(GitError, match="changed"):
        service.apply(document, *_change_range(document, "b", "B"))

    assert attempts
    assert _index_blob(git_repo, "race.txt") == b"a\nB\nc\nD\n"
    assert not (git_repo / ".git" / "index.lock").exists()


def test_selected_patch_uses_linked_worktrees_own_index(git_repo, git_run):
    _commit_file(git_repo, git_run, "linked.txt", b"old\n")
    linked = git_repo.parent / "linked"
    git_run(git_repo, "worktree", "add", "-b", "topic", str(linked))
    before = (git_repo / ".git" / "index").read_bytes()
    (linked / "linked.txt").write_bytes(b"new\n")
    service = PatchService(Repository(linked))
    document = service.open("linked.txt")

    service.apply(document, *_all_changes(document))

    assert _index_blob(linked, "linked.txt") == b"new\n"
    assert _index_blob(git_repo, "linked.txt") == b"old\n"
    assert (git_repo / ".git" / "index").read_bytes() == before
    assert (git_repo / "linked.txt").read_bytes() == b"old\n"


def test_sparse_index_selection_preserves_outside_paths_and_skip_worktree(git_repo, git_run):
    _commit_file(git_repo, git_run, "inside/file.txt", b"a\nb\nc\nd\n")
    _commit_file(git_repo, git_run, "outside/keep.txt", b"outside stays exact\n")
    git_run(git_repo, "sparse-checkout", "init", "--cone", "--sparse-index")
    git_run(git_repo, "sparse-checkout", "set", "inside")
    (git_repo / "inside/file.txt").write_bytes(b"a\nB\nc\nD\n")
    service = PatchService(Repository(git_repo))
    document = service.open("inside/file.txt", context=0)

    service.apply(document, *_change_range(document, "b", "B"))

    assert _index_blob(git_repo, "inside/file.txt") == b"a\nB\nc\nd\n"
    assert _index_blob(git_repo, "outside/keep.txt") == b"outside stays exact\n"
    assert git_run(git_repo, "ls-files", "-t", "outside/keep.txt").stdout.startswith("S ")
    assert not (git_repo / "outside").exists()
    git_run(git_repo, "write-tree")
    git_run(git_repo, "status", "--porcelain")


def test_failure_before_index_publication_preserves_original_and_cleans_owned_lock(
    git_repo, git_run, monkeypatch
):
    _commit_file(git_repo, git_run, "failure.txt", b"old\n")
    (git_repo / "failure.txt").write_bytes(b"new\n")
    service = PatchService(Repository(git_repo))
    document = service.open("failure.txt")
    replace_file = os.replace

    def fail_publication(source, destination, *args, **kwargs):
        if source == "index.lock" and destination == "index":
            raise OSError("simulated publication failure")
        return replace_file(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_publication)
    with pytest.raises(GitError, match="publication failure"):
        service.apply(document, *_all_changes(document))

    assert _index_blob(git_repo, "failure.txt") == b"old\n"
    assert (git_repo / "failure.txt").read_bytes() == b"new\n"
    assert not (git_repo / ".git" / "index.lock").exists()
    assert not tuple((git_repo / ".git").glob("rhizome-index-*"))


def test_split_index_selection_preserves_shared_storage_and_unrelated_staging(
    git_repo: Path, git_run
) -> None:
    _commit_file(git_repo, git_run, "split.txt", b"a\nb\nc\nd\n")
    (git_repo / "other.txt").write_bytes(b"already staged\n")
    git_run(git_repo, "add", "other.txt")
    git_run(git_repo, "config", "core.splitIndex", "true")
    git_run(git_repo, "update-index", "--split-index")
    (git_repo / "split.txt").write_bytes(b"a\nB\nc\nD\n")
    service = PatchService(Repository(git_repo))
    document = service.open("split.txt", context=0)

    service.apply(document, *_change_range(document, "b", "B"))

    assert _index_blob(git_repo, "split.txt") == b"a\nB\nc\nd\n"
    assert _index_blob(git_repo, "other.txt") == b"already staged\n"
    shared = git_run(git_repo, "rev-parse", "--shared-index-path").stdout.removesuffix("\n")
    assert shared and (git_repo / shared).is_file()
    git_run(git_repo, "write-tree")
    git_run(git_repo, "status", "--porcelain")
    assert (git_repo / "split.txt").read_bytes() == b"a\nB\nc\nD\n"


def test_selected_patch_holds_real_index_lock_before_candidate_write(
    git_repo: Path, git_run, monkeypatch
) -> None:
    _commit_file(git_repo, git_run, "race.txt", b"a\nb\nc\nd\n")
    (git_repo / "race.txt").write_bytes(b"a\nB\nc\nD\n")
    service = PatchService(Repository(git_repo))
    document = service.open("race.txt", context=0)
    external_writers = []
    run = subprocess.run

    def competing_git(command, *args, **kwargs):
        # The external process boundary, not a mocked repository collaborator:
        # run a real Git writer at the last index-write opportunity.
        if "update-index" in command and "--index-info" in command:
            external_writers.append(git_run(git_repo, "add", "race.txt", check=False))
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", competing_git)
    service.apply(document, *_change_range(document, "b", "B"))

    assert external_writers and all(result.returncode != 0 for result in external_writers)
    assert all("index.lock" in result.stderr for result in external_writers)
    assert _index_blob(git_repo, "race.txt") == b"a\nB\nc\nd\n"
    assert (git_repo / "race.txt").read_bytes() == b"a\nB\nc\nD\n"
    # Once Rhizome publishes, a normal external retry remains interoperable.
    git_run(git_repo, "add", "race.txt")
    assert _index_blob(git_repo, "race.txt") == b"a\nB\nc\nD\n"


def test_invalid_or_repeated_selection_refuses_without_mutation(git_repo: Path, git_run) -> None:
    _commit_file(git_repo, git_run, "select.txt", b"old\n")
    (git_repo / "select.txt").write_bytes(b"new\n")
    service = PatchService(Repository(git_repo))
    document = service.open("select.txt")
    meta = next(line for line in document.lines if line.kind == "meta")

    with pytest.raises(GitError, match="changed line"):
        service.apply(document, meta.id, meta.id)
    with pytest.raises(GitError, match="contiguous"):
        service.apply(document, 4, 3)
    service.apply(document, *_all_changes(document))
    with pytest.raises(GitError, match="changed"):
        service.apply(document, *_all_changes(document))


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("binary", "Binary"),
        ("mode", "mode"),
        ("symlink", "Symlink"),
        ("rename", "Rename"),
        ("filter", "filter"),
        ("ignored", "Ignored"),
    ],
)
def test_unsupported_representations_are_truthful_and_nonselectable(
    git_repo: Path, git_run, case: str, reason: str
) -> None:
    service = PatchService(Repository(git_repo))
    if case == "binary":
        _commit_file(git_repo, git_run, "item", b"old\x00bytes")
        (git_repo / "item").write_bytes(b"new\x00bytes")
        path = "item"
    elif case == "mode":
        _commit_file(git_repo, git_run, "item", b"text\n")
        os.chmod(git_repo / "item", 0o755)
        path = "item"
    elif case == "symlink":
        (git_repo / "target").write_bytes(b"target\n")
        os.symlink("target", git_repo / "item")
        path = "item"
    elif case == "rename":
        _commit_file(git_repo, git_run, "old", b"text\n")
        git_run(git_repo, "mv", "old", "item")
        path = "item"
    elif case == "filter":
        _commit_file(git_repo, git_run, ".gitattributes", b"item filter=custom\n")
        (git_repo / "item").write_bytes(b"text\n")
        path = "item"
    else:
        _commit_file(git_repo, git_run, ".gitignore", b"item\n")
        (git_repo / "item").write_bytes(b"text\n")
        path = "item"

    document = service.open(path)

    assert not document.selectable
    assert reason.lower() in document.reason.lower()
    with pytest.raises(GitError, match=reason):
        service.apply(document, 1, 1)


def test_oversized_patch_is_nonselectable(git_repo: Path, git_run) -> None:
    original = (b"a" * 120 + b"\n") * 9_000
    _commit_file(git_repo, git_run, "large.txt", original)
    (git_repo / "large.txt").write_bytes((b"b" * 120 + b"\n") * 9_000)

    document = PatchService(Repository(git_repo)).open("large.txt", context=0)

    assert not document.selectable
    assert "2 MiB" in document.reason
