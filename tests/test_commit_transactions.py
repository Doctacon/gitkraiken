"""Native commit behavior through Repository; UI stays gated until integration proof."""

from __future__ import annotations

import os
import shlex
import subprocess

import pytest

from rhizome.git import GitError, GitOutcomeUncertain, Repository
from rhizome.models import Action


@pytest.fixture
def staged_change(git_repo, git_run):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", "example.txt")
    git_run(git_repo, "commit", "-m", "base")
    path.write_text("reviewed\n")
    git_run(git_repo, "add", "example.txt")
    return path


def test_empty_unborn_commit_refuses_without_leaving_transaction_files(git_repo):
    with pytest.raises(GitError, match="no staged changes"):
        Repository(git_repo).execute(Action("commit", message="Nothing staged"))
    assert not (git_repo / ".git" / "index.lock").exists()
    assert not list((git_repo / ".git").glob("rhizome-commit-*"))


def test_native_commit_keeps_competing_index_writer_out(
    git_repo, git_run, monkeypatch, staged_change
):
    path = staged_change
    run = subprocess.run
    popen = subprocess.Popen
    competing = []

    def external_writer(command, *args, **kwargs):
        if "commit" in command and not competing:
            path.write_text("changed after review\n")
            competing.append(
                run(
                    ["git", "-C", str(git_repo), "add", "example.txt"],
                    capture_output=True,
                )
            )
        return popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", external_writer)
    Repository(git_repo).execute(Action("commit", message="Commit the reviewed index"))
    assert len(competing) == 1
    assert competing[0].returncode != 0
    assert b"index.lock" in competing[0].stderr
    assert git_run(git_repo, "show", "HEAD:example.txt").stdout == "reviewed\n"
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert path.read_text() == "changed after review\n"
    assert not (git_repo / ".git" / "index.lock").exists()


def test_index_writer_winning_before_lock_is_revalidated_without_losing_its_content(
    git_repo, git_run, monkeypatch, staged_change
):
    head = git_run(git_repo, "rev-parse", "HEAD").stdout
    open_file = os.open
    wrote = False

    def writer_first(path, flags, *args, **kwargs):
        nonlocal wrote
        if os.fsdecode(path).endswith("index.lock") and flags & os.O_EXCL and not wrote:
            wrote = True
            staged_change.write_text("Competing writer won first\n")
            git_run(git_repo, "add", "example.txt")
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", writer_first)
    with pytest.raises(GitError, match="changed"):
        Repository(git_repo).execute(Action("commit", message="Old reviewed state"))
    assert wrote
    assert git_run(git_repo, "rev-parse", "HEAD").stdout == head
    assert git_run(git_repo, "show", ":example.txt").stdout == "Competing writer won first\n"
    assert staged_change.read_text() == "Competing writer won first\n"
    assert not (git_repo / ".git" / "index.lock").exists()


def test_intent_to_add_flag_change_before_lock_invalidates_commit_review(
    git_repo, git_run, monkeypatch, staged_change
):
    empty = git_repo / "empty.txt"
    empty.write_bytes(b"")
    git_run(git_repo, "add", empty.name)
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout
    before_entries = git_run(git_repo, "ls-files", "--stage", "-z").stdout
    open_file = os.open
    changed = False

    def change_meaning_before_lock(path, flags, *args, **kwargs):
        nonlocal changed
        if os.fsdecode(path).endswith("index.lock") and flags & os.O_EXCL and not changed:
            changed = True
            git_run(git_repo, "reset", "--", empty.name)
            git_run(git_repo, "add", "--intent-to-add", empty.name)
            assert git_run(git_repo, "ls-files", "--stage", "-z").stdout == before_entries
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", change_meaning_before_lock)
    with pytest.raises(GitError, match="changed"):
        Repository(git_repo).execute(Action("commit", message="Include the reviewed empty file"))
    assert changed
    assert git_run(git_repo, "rev-parse", "HEAD").stdout == before_head
    assert " A empty.txt\n" in git_run(git_repo, "status", "--porcelain=v1").stdout
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert empty.read_bytes() == b""
    assert not (git_repo / ".git" / "index.lock").exists()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_configured_signer_is_invoked_and_rejection_preserves_index(
    git_repo, git_run, staged_change, exit_code
):
    head = git_run(git_repo, "rev-parse", "HEAD").stdout
    signer = git_repo.parent / "fixture-signer"
    marker = git_repo.parent / "signer-arguments"
    signer.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(marker))}\n"
        "cat >/dev/null\n"
        + (
            "printf 'Fixture signing rejection\\n' >&2\nexit 1\n"
            if exit_code
            else "printf '%s\\n' '-----BEGIN PGP SIGNATURE-----' 'Zml4dHVyZQ==' "
            "'-----END PGP SIGNATURE-----'\n"
            "printf '[GNUPG:] SIG_CREATED D 1 8 00 1234567890 " + "A" * 40 + "\\n' >&2\n"
        )
    )
    signer.chmod(0o755)
    git_run(git_repo, "config", "commit.gpgsign", "true")
    git_run(git_repo, "config", "user.signingkey", "fixture-key")
    git_run(git_repo, "config", "gpg.program", str(signer))
    if exit_code:
        with pytest.raises(GitError, match="sign") as raised:
            Repository(git_repo).execute(Action("commit", message="Signed intent"))
        assert not isinstance(raised.value, GitOutcomeUncertain)
        assert git_run(git_repo, "rev-parse", "HEAD").stdout == head
    else:
        Repository(git_repo).execute(Action("commit", message="Signed intent"))
        raw = git_run(git_repo, "cat-file", "commit", "HEAD").stdout
        assert "gpgsig -----BEGIN PGP SIGNATURE-----" in raw
    assert "fixture-key" in marker.read_text()
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert not (git_repo / ".git" / "index.lock").exists()
    # This is an external signer protocol double, not a cryptographic signature proof.


@pytest.mark.parametrize("exit_code", [0, 1])
def test_synchronous_hook_index_edits_survive_success_and_rejection(
    git_repo, git_run, staged_change, exit_code
):
    head = git_run(git_repo, "rev-parse", "HEAD").stdout
    marker = git_repo.parent / "hook-index-path"
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$GIT_INDEX_FILE\" > {shlex.quote(str(marker))}\n"
        "printf 'hook staged content\\n' > hook.txt\ngit add hook.txt\n"
        "printf 'Fixture hook output without a newline: '\n"
        f"exit {exit_code}\n"
    )
    hook.chmod(0o755)
    if exit_code:
        with pytest.raises(GitError, match="Fixture hook output") as raised:
            Repository(git_repo).execute(Action("commit", message="Reviewed commit"))
        assert not isinstance(raised.value, GitOutcomeUncertain)
        assert git_run(git_repo, "rev-parse", "HEAD").stdout == head
    else:
        Repository(git_repo).execute(Action("commit", message="Reviewed commit"))
        assert git_run(git_repo, "show", "HEAD:hook.txt").stdout == "hook staged content\n"
    assert "rhizome-commit-" in marker.read_text()
    assert git_run(git_repo, "show", ":hook.txt").stdout == "hook staged content\n"
    assert staged_change.read_text() == "reviewed\n"
    assert not (git_repo / ".git" / "index.lock").exists()
    assert not list((git_repo / ".git").glob("rhizome-commit-*"))


def test_native_result_is_not_confused_by_hook_text_subject_or_path(
    git_repo, git_run, staged_change
):
    old = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "branch", "-m", "topic]tail")
    git_run(git_repo, "config", "core.abbrev", "4")
    git_run(git_repo, "config", "color.ui", "always")
    git_run(git_repo, "config", "color.diff", "always")
    name = f"[main {old}] path.txt"
    (git_repo / name).write_text("literal name\n")
    git_run(git_repo, "--literal-pathspecs", "add", "--", name)
    for phase in ("pre-commit", "post-commit"):
        hook = git_repo / ".git" / "hooks" / phase
        hook.write_text(f"#!/bin/sh\nprintf '[main {old}] fake\\n[main {old}] no newline'\n")
        hook.chmod(0o755)
    message = f"Keep [main {old}] in the subject"
    result = Repository(git_repo).execute(Action("commit", message=message))
    created = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert created != old
    assert created in result
    assert git_run(git_repo, "show", f"HEAD:{name}").stdout == "literal name\n"
    assert git_run(git_repo, "log", "-1", "--format=%s").stdout.strip() == message
    assert not (git_repo / ".git" / "index.lock").exists()


def test_same_parent_ref_substitution_is_not_reported_as_commit_success(
    git_repo, git_run, staged_change
):
    head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    sibling = git_run(
        git_repo,
        "commit-tree",
        "HEAD^{tree}",
        "-p",
        head,
        "-m",
        "Concurrent sibling",
    ).stdout.strip()
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\ngit update-ref refs/heads/main {sibling}\n")
    hook.chmod(0o755)
    with pytest.raises(GitOutcomeUncertain):
        Repository(git_repo).execute(Action("commit", message="Reviewed commit"))
    assert git_run(git_repo, "rev-parse", "HEAD").stdout.strip() == sibling
    assert git_run(git_repo, "show", ":example.txt").stdout == "reviewed\n"
    assert staged_change.read_text() == "reviewed\n"
