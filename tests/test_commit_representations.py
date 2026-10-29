"""Real-Git evidence for native commit index representation preservation."""

from __future__ import annotations

import shlex
import threading
import time
from pathlib import Path

import pytest

from rhizome.git import Repository
from rhizome.models import Action


def _git_path(repository: Path, git_run, name: str) -> Path:
    path = Path(git_run(repository, "rev-parse", "--git-path", name).stdout.removesuffix("\n"))
    return path if path.is_absolute() else repository / path


def _prepare_sparse_index(repository: Path, git_run) -> None:
    repository.mkdir()
    git_run(repository, "init", "-b", "main")
    git_run(repository, "config", "user.name", "Test User")
    git_run(repository, "config", "user.email", "test@example.invalid")
    git_run(repository, "config", "commit.gpgsign", "false")
    (repository / "visible").mkdir()
    (repository / "outside").mkdir()
    (repository / "visible" / "file.txt").write_text("visible base\n")
    (repository / "outside" / "a.txt").write_text("outside a\n")
    (repository / "outside" / "b.txt").write_text("outside b\n")
    git_run(repository, "add", ".")
    git_run(repository, "commit", "-m", "base")
    git_run(repository, "sparse-checkout", "init", "--cone", "--sparse-index")
    git_run(repository, "sparse-checkout", "set", "visible")
    (repository / "visible" / "file.txt").write_text("visible reviewed\n")
    git_run(repository, "add", "visible/file.txt")


def test_unborn_initial_commit_uses_staged_source_and_has_no_raw_parents(git_repo, git_run):
    source = git_repo / "initial.txt"
    source.write_text("staged initial content\n")
    git_run(git_repo, "add", source.name)
    staged_oid = git_run(git_repo, "rev-parse", ":initial.txt").stdout.strip()
    staged_tree = git_run(git_repo, "write-tree").stdout.strip()
    source.write_text("unstaged working content\n")

    result = Repository(git_repo).execute(Action("commit", message="initial transaction"))

    created = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    raw = git_run(git_repo, "cat-file", "commit", created).stdout
    headers = raw.split("\n\n", 1)[0].splitlines()
    assert len(created) == 40
    assert created in result
    assert headers[0] == f"tree {staged_tree}"
    assert not [line for line in headers if line.startswith("parent ")]
    assert git_run(git_repo, "rev-parse", ":initial.txt").stdout.strip() == staged_oid
    assert git_run(git_repo, "show", "HEAD:initial.txt").stdout == "staged initial content\n"
    assert git_run(git_repo, "show", ":initial.txt").stdout == "staged initial content\n"
    assert source.read_text() == "unstaged working content\n"


def test_detached_head_commit_keeps_raw_parent_and_staged_source(git_repo, git_run):
    source = git_repo / "detached.txt"
    source.write_text("base\n")
    git_run(git_repo, "add", source.name)
    git_run(git_repo, "commit", "-m", "base")
    parent = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "checkout", "--detach", parent)
    source.write_text("reviewed detached content\n")
    git_run(git_repo, "add", source.name)
    staged_oid = git_run(git_repo, "rev-parse", ":detached.txt").stdout.strip()
    staged_tree = git_run(git_repo, "write-tree").stdout.strip()
    source.write_text("unstaged detached content\n")

    result = Repository(git_repo).execute(Action("commit", message="detached transaction"))

    created = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    raw = git_run(git_repo, "cat-file", "commit", created).stdout
    headers = raw.split("\n\n", 1)[0].splitlines()
    assert len(created) == 40
    assert created != parent
    assert created in result
    assert headers[0] == f"tree {staged_tree}"
    assert [line for line in headers if line.startswith("parent ")] == [f"parent {parent}"]
    assert git_run(git_repo, "symbolic-ref", "--quiet", "HEAD", check=False).returncode == 1
    assert git_run(git_repo, "rev-parse", "main").stdout.strip() == parent
    assert git_run(git_repo, "rev-parse", ":detached.txt").stdout.strip() == staged_oid
    assert git_run(git_repo, "show", "HEAD:detached.txt").stdout == "reviewed detached content\n"
    assert git_run(git_repo, "show", ":detached.txt").stdout == "reviewed detached content\n"
    assert source.read_text() == "unstaged detached content\n"


def test_sha256_commit_reports_and_verifies_full_native_oid(tmp_path, git_run):
    repository = tmp_path / "sha256-worktree"
    repository.mkdir()
    git_run(repository, "init", "--object-format=sha256", "-b", "main")
    git_run(repository, "config", "user.name", "Test User")
    git_run(repository, "config", "user.email", "test@example.invalid")
    git_run(repository, "config", "commit.gpgsign", "false")
    assert git_run(repository, "rev-parse", "--show-object-format").stdout.strip() == "sha256"
    source = repository / "sha256.txt"
    source.write_text("base\n")
    git_run(repository, "add", source.name)
    git_run(repository, "commit", "-m", "base")
    parent = git_run(repository, "rev-parse", "HEAD").stdout.strip()
    source.write_text("reviewed sha256 content\n")
    git_run(repository, "add", source.name)

    result = Repository(repository).execute(Action("commit", message="sha256 transaction"))

    created = git_run(repository, "rev-parse", "HEAD").stdout.strip()
    raw = git_run(repository, "cat-file", "commit", created).stdout
    headers = raw.split("\n\n", 1)[0].splitlines()
    assert len(parent) == 64
    assert len(created) == 64
    assert created != parent
    assert created in result
    assert [line for line in headers if line.startswith("parent ")] == [f"parent {parent}"]
    assert git_run(repository, "show", "HEAD:sha256.txt").stdout == "reviewed sha256 content\n"
    assert git_run(repository, "show", ":sha256.txt").stdout == "reviewed sha256 content\n"


def test_linked_worktree_commit_does_not_publish_over_primary_index(git_repo, git_run):
    source = git_repo / "shared.txt"
    source.write_text("base\n")
    git_run(git_repo, "add", source.name)
    git_run(git_repo, "commit", "-m", "base")
    parent = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    linked = git_repo.parent / "linked-worktree"
    git_run(git_repo, "worktree", "add", "-b", "linked", str(linked), parent)

    source.write_text("primary staged content\n")
    git_run(git_repo, "add", source.name)
    linked_source = linked / source.name
    linked_source.write_text("linked staged content\n")
    git_run(linked, "add", linked_source.name)
    primary_index = _git_path(git_repo, git_run, "index")
    linked_index = _git_path(linked, git_run, "index")
    assert primary_index != linked_index
    primary_bytes = primary_index.read_bytes()
    primary_entries = git_run(git_repo, "ls-files", "--stage", "-z").stdout

    result = Repository(linked).execute(Action("commit", message="linked transaction"))

    linked_created = git_run(linked, "rev-parse", "HEAD").stdout.strip()
    raw = git_run(linked, "cat-file", "commit", linked_created).stdout
    headers = raw.split("\n\n", 1)[0].splitlines()
    assert linked_created in result
    assert [line for line in headers if line.startswith("parent ")] == [f"parent {parent}"]
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == parent
    assert git_run(git_repo, "rev-parse", "linked").stdout.strip() == linked_created
    assert primary_index.read_bytes() == primary_bytes
    assert git_run(git_repo, "ls-files", "--stage", "-z").stdout == primary_entries
    assert git_run(git_repo, "show", ":shared.txt").stdout == "primary staged content\n"
    assert git_run(linked, "show", "HEAD:shared.txt").stdout == "linked staged content\n"
    assert git_run(linked, "diff", "--cached", "--quiet", "--exit-code").returncode == 0


def test_sparse_index_commit_matches_native_collapsed_and_skip_worktree_state(tmp_path, git_run):
    native = tmp_path / "native-sparse"
    public = tmp_path / "public-sparse"
    _prepare_sparse_index(native, git_run)
    _prepare_sparse_index(public, git_run)

    expected_before = git_run(native, "ls-files", "--sparse", "--stage", "-z").stdout
    tags_before = git_run(native, "ls-files", "--sparse", "-t", "-z").stdout
    assert expected_before == git_run(public, "ls-files", "--sparse", "--stage", "-z").stdout
    assert tags_before == git_run(public, "ls-files", "--sparse", "-t", "-z").stdout
    assert "040000 " in expected_before and "\toutside/\0" in expected_before
    assert tags_before.split("\0")[:2] == ["S outside/", "H visible/file.txt"]
    assert not (public / "outside").exists()

    git_run(native, "commit", "-m", "native sparse transaction")
    result = Repository(public).execute(Action("commit", message="public sparse transaction"))

    native_stage = git_run(native, "ls-files", "--sparse", "--stage", "-z").stdout
    public_stage = git_run(public, "ls-files", "--sparse", "--stage", "-z").stdout
    native_tags = git_run(native, "ls-files", "--sparse", "-t", "-z").stdout
    public_tags = git_run(public, "ls-files", "--sparse", "-t", "-z").stdout
    public_index = _git_path(public, git_run, "index").read_bytes()
    native_index = _git_path(native, git_run, "index").read_bytes()
    assert git_run(public, "rev-parse", "HEAD").stdout.strip() in result
    assert public_stage == native_stage
    assert public_tags == native_tags
    assert "040000 " in public_stage and "\toutside/\0" in public_stage
    assert public_tags.split("\0")[:2] == ["S outside/", "H visible/file.txt"]
    assert int.from_bytes(public_index[8:12], "big") == 2
    assert int.from_bytes(native_index[8:12], "big") == 2
    assert (
        git_run(public, "rev-parse", "HEAD^{tree}").stdout
        == git_run(native, "rev-parse", "HEAD^{tree}").stdout
    )
    assert git_run(public, "show", "HEAD:outside/a.txt").stdout == "outside a\n"
    assert git_run(public, "show", "HEAD:outside/b.txt").stdout == "outside b\n"
    assert git_run(public, "show", "HEAD:visible/file.txt").stdout == "visible reviewed\n"
    assert not (public / "outside").exists()


@pytest.mark.parametrize("linked_worktree", (False, True))
def test_split_index_expiration_does_not_remove_storage_used_by_locked_real_index(
    git_repo, git_run, linked_worktree
):
    for number in range(32):
        (git_repo / f"tracked-{number:02}.txt").write_text(f"base {number}\n")
    git_run(git_repo, "add", ".")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "config", "core.splitIndex", "true")
    git_run(git_repo, "update-index", "--split-index")
    primary = git_repo
    primary_head = git_run(primary, "rev-parse", "HEAD").stdout
    if linked_worktree:
        linked = primary.parent / "linked-split-worktree"
        git_run(primary, "worktree", "add", "-b", "linked-split", str(linked))
        (primary / "tracked-00.txt").write_text("primary staged content\n")
        git_run(primary, "add", "tracked-00.txt")
        git_repo = linked
        git_run(git_repo, "update-index", "--split-index")
    primary_index = _git_path(primary, git_run, "index")
    primary_bytes = primary_index.read_bytes()
    primary_entries = git_run(primary, "ls-files", "--stage", "-z").stdout

    changed = git_repo / "tracked-00.txt"
    changed.write_text("reviewed\n")
    git_run(git_repo, "add", changed.name)
    index = _git_path(git_repo, git_run, "index")
    shared_output = git_run(git_repo, "rev-parse", "--shared-index-path").stdout.removesuffix("\n")
    assert shared_output
    shared_index = Path(shared_output)
    if not shared_index.is_absolute():
        shared_index = git_repo / shared_index
    assert shared_index.is_file()
    index_before = index.read_bytes()
    entries_before = git_run(git_repo, "ls-files", "--stage", "-z").stdout

    # Rebuild a different shared base from a synchronous hook. Merely committing
    # the existing split representation may never invoke native sidecar cleanup.
    pre_commit = _git_path(git_repo, git_run, "hooks") / "pre-commit"
    pre_commit.write_text(
        "#!/bin/sh\n"
        "printf 'hook content\\n' > hook-added.txt\n"
        "git add hook-added.txt && git update-index --split-index\n"
    )
    pre_commit.chmod(0o755)
    # Exercise aggressive cleanup only after the real split index is ready.
    git_run(git_repo, "config", "splitIndex.sharedIndexExpire", "now")
    entered = git_repo.parent / "post-commit-entered"
    release = git_repo.parent / "release-post-commit"
    hook = _git_path(git_repo, git_run, "hooks") / "post-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f": > {shlex.quote(str(entered))}\n"
        f"while test ! -e {shlex.quote(str(release))}; do sleep 0.02; done\n"
    )
    hook.chmod(0o755)

    outcome: list[BaseException | str] = []

    def commit() -> None:
        try:
            outcome.append(
                Repository(git_repo).execute(Action("commit", message="split index transaction"))
            )
        except BaseException as error:
            outcome.append(error)

    worker = threading.Thread(target=commit, name="native-commit", daemon=False)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not entered.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert entered.exists(), "native Git did not reach the blocking post-commit hook"
        assert worker.is_alive(), "native commit did not remain paused in its post-commit hook"

        # HEAD has advanced, but publication has not: readers still see the byte-identical
        # real index and must be able to resolve the shared storage it references.
        assert index.read_bytes() == index_before
        assert shared_index.is_file()
        assert git_run(git_repo, "ls-files", "--stage", "-z").stdout == entries_before
        if linked_worktree:
            assert primary_index.read_bytes() == primary_bytes
            assert git_run(primary, "ls-files", "--stage", "-z").stdout == primary_entries
            assert git_run(primary, "rev-parse", "HEAD").stdout == primary_head
    finally:
        release.touch()
        worker.join()

    assert len(outcome) == 1
    if isinstance(outcome[0], BaseException):
        raise outcome[0]
    assert git_run(git_repo, "show", "HEAD:tracked-00.txt").stdout == "reviewed\n"
    assert git_run(git_repo, "diff", "--cached", "--quiet", "--exit-code").returncode == 0
    published_shared = git_run(git_repo, "rev-parse", "--shared-index-path").stdout.removesuffix(
        "\n"
    )
    assert published_shared
    published_shared_path = Path(published_shared)
    if not published_shared_path.is_absolute():
        published_shared_path = git_repo / published_shared_path
    assert published_shared_path.is_file()
    assert published_shared_path != shared_index
    assert git_run(git_repo, "show", "HEAD:hook-added.txt").stdout == "hook content\n"
    assert not list(index.parent.glob("rhizome-commit-*"))
    if linked_worktree:
        assert primary_index.read_bytes() == primary_bytes
        assert git_run(primary, "ls-files", "--stage", "-z").stdout == primary_entries
        assert git_run(primary, "rev-parse", "HEAD").stdout == primary_head
        assert git_run(primary, "show", ":tracked-00.txt").stdout == "primary staged content\n"
        assert (primary / "tracked-00.txt").read_text() == "primary staged content\n"
