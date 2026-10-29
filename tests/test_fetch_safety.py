from __future__ import annotations

import os
from pathlib import Path

import pytest

from rhizome.git import GitError, Repository
from rhizome.models import Action
from rhizome.operations import OperationCoordinator


def _oid(git_run, repository: Path, ref: str = "HEAD") -> str:
    return git_run(repository, "rev-parse", ref).stdout.strip()


def _commit(git_run, repository: Path, name: str, content: str) -> str:
    (repository / name).write_text(content)
    git_run(repository, "add", name)
    git_run(repository, "commit", "-m", content.strip())
    return _oid(git_run, repository)


def _remote_with_branch(tmp_path: Path, git_run, *, branch: str = "main") -> tuple[Path, str]:
    remote = tmp_path / "fetch-remote.git"
    seed = tmp_path / "fetch-seed"
    remote.mkdir()
    seed.mkdir()
    git_run(remote, "init", "--bare", "-b", branch)
    git_run(seed, "init", "-b", branch)
    oid = _commit(git_run, seed, "remote.txt", "remote\n")
    git_run(seed, "push", str(remote), f"HEAD:refs/heads/{branch}")
    return remote, oid


def _add_remote(git_run, repository: Path, remote: Path, name: str = "origin") -> None:
    git_run(repository, "remote", "add", name, str(remote))


def _set_fetch(git_run, repository: Path, *refspecs: str, remote: str = "origin") -> None:
    git_run(repository, "config", "--unset-all", f"remote.{remote}.fetch", check=False)
    for refspec in refspecs:
        git_run(repository, "config", "--add", f"remote.{remote}.fetch", refspec)


@pytest.mark.parametrize(
    "destination",
    ("refs/heads/victim", "refs/tags/victim", "refs/notes/private"),
)
def test_fetch_rejects_user_namespace_destinations_before_transport(
    git_repo: Path, tmp_path: Path, git_run, destination: str
) -> None:
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    git_run(git_repo, "branch", "victim")
    git_run(git_repo, "tag", "victim")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    _set_fetch(git_run, git_repo, f"+refs/heads/main:{destination}")
    before = git_run(
        git_repo, "for-each-ref", "--format=%(refname) %(objectname)"
    ).stdout

    with pytest.raises(GitError, match="remote-tracking"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo) == local
    assert git_run(
        git_repo, "for-each-ref", "--format=%(refname) %(objectname)"
    ).stdout == before


@pytest.mark.parametrize("alias", ("refs/heads/alias", "refs/tags/alias", "refs/notes/alias"))
def test_fetch_preserves_existing_user_refs_aliased_into_its_cache(
    git_repo, tmp_path, git_run, alias
):
    git_run(git_repo, "config", "core.logAllRefUpdates", "false")
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    git_run(git_repo, "switch", "-c", "private-source")
    git_run(git_repo, "commit", "--allow-empty", "-m", "Unpublished private history")
    private = _oid(git_run, git_repo)
    cache = "refs/remotes/origin/main"
    git_run(git_repo, "update-ref", cache, private)
    git_run(git_repo, "symbolic-ref", alias, cache)
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "branch", "-D", "private-source")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    assert not (git_repo / ".git" / "logs").exists()
    failure = None
    try:
        Repository(git_repo).execute(Action("fetch", remote="origin"))
    except GitError as error:
        failure = error

    unreachable = git_run(git_repo, "fsck", "--unreachable", "--no-reflogs").stdout
    assert f"unreachable commit {private}" not in unreachable
    assert _oid(git_run, git_repo, alias) == private
    assert _oid(git_run, git_repo, cache) == private
    assert _oid(git_run, git_repo) == local
    assert git_run(git_repo, "symbolic-ref", alias).stdout.strip() == cache
    assert failure is not None and "user-owned symbolic ref" in str(failure)
    assert not (git_repo / ".git" / "logs").exists()


@pytest.mark.parametrize("alias", ("refs/heads/alias", "refs/tags/alias"))
def test_fetch_can_resolve_a_dangling_inbound_alias_without_losing_a_tip(
    git_repo, tmp_path, git_run, alias
):
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    remote, incoming = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    cache = "refs/remotes/origin/main"
    git_run(git_repo, "symbolic-ref", alias, cache)
    assert git_run(git_repo, "rev-parse", "--verify", alias, check=False).returncode != 0
    before = (git_repo / ".git" / alias).read_bytes()

    Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, alias) == incoming
    assert _oid(git_run, git_repo) == local
    assert (git_repo / ".git" / alias).read_bytes() == before


@pytest.mark.parametrize("target_exists", (False, True))
def test_native_tag_auto_follow_preserves_existing_symbolic_targets(
    git_repo, tmp_path, git_run, target_exists
):
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    twin = tmp_path / "native-twin"
    git_run(tmp_path, "clone", str(git_repo), str(twin))
    remote, incoming = _remote_with_branch(tmp_path, git_run)
    seed = tmp_path / "fetch-seed"
    git_run(seed, "tag", "release")
    git_run(seed, "push", str(remote), "refs/tags/release")
    _add_remote(git_run, git_repo, remote)
    git_run(twin, "remote", "set-url", "origin", str(remote))
    alias, target = "refs/tags/release", "refs/heads/tag-target"
    for repo in (git_repo, twin):
        if target_exists:
            git_run(repo, "update-ref", target, local)
        git_run(repo, "symbolic-ref", alias, target)

    git_run(twin, "fetch", "origin")
    Repository(git_repo).execute(Action("fetch", remote="origin"))

    expected = local if target_exists else incoming
    for repo in (git_repo, twin):
        assert _oid(git_run, repo, target) == expected
        assert _oid(git_run, repo, alias) == expected
        assert _oid(git_run, repo) == local
        assert _oid(git_run, repo, "refs/remotes/origin/main") == incoming
        assert git_run(repo, "symbolic-ref", alias).stdout.strip() == target


def test_fetch_detects_aliases_of_an_indirect_cache_destination(
    git_repo, tmp_path, git_run
):
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    destination, terminal = "refs/remotes/origin/indirect", "refs/remotes/cache/main"
    git_run(git_repo, "update-ref", terminal, local)
    git_run(git_repo, "symbolic-ref", destination, terminal)
    git_run(git_repo, "symbolic-ref", "refs/heads/alias", terminal)
    _set_fetch(git_run, git_repo, f"+refs/heads/main:{destination}")

    with pytest.raises(GitError, match="user-owned symbolic ref"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))
    assert _oid(git_run, git_repo, "refs/heads/alias") == local
    assert _oid(git_run, git_repo, terminal) == local


@pytest.mark.parametrize("requested", ("", "origin"))
def test_reviewed_fetch_updates_only_its_safe_caches(git_repo, tmp_path, git_run, requested):
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    git_run(git_repo, "tag", "private")
    remote, incoming = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()
    coordinator = OperationCoordinator(Repository(git_repo))
    plan = coordinator.plan(Action("fetch", remote=requested))
    assert plan.available and not plan.requires_confirmation

    coordinator.submit(plan)

    assert _oid(git_run, git_repo, "refs/remotes/origin/main") == incoming
    assert _oid(git_run, git_repo) == local
    assert _oid(git_run, git_repo, "refs/tags/private") == local
    assert index.read_bytes() == before_index
    assert (git_repo / "local.txt").read_text() == "local\n"
    assert coordinator.activity()[-1].state == "succeeded"


@pytest.mark.parametrize("change", ("destination", "inbound-alias"))
def test_reviewed_fetch_revalidates_active_scope_before_transport(
    git_repo, tmp_path, git_run, change
):
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    cache = "refs/remotes/origin/main"
    git_run(git_repo, "update-ref", cache, local)
    coordinator = OperationCoordinator(Repository(git_repo))
    plan = coordinator.plan(Action("fetch", remote="origin"))
    assert plan.available
    if change == "destination":
        _set_fetch(git_run, git_repo, "+refs/heads/main:refs/heads/private")
    else:
        git_run(git_repo, "symbolic-ref", "refs/heads/private", cache)

    with pytest.raises(GitError, match="outside.*cache|user-owned symbolic ref"):
        coordinator.submit(plan)

    assert _oid(git_run, git_repo) == local
    assert _oid(git_run, git_repo, cache) == local
    assert not (git_repo / ".git" / "FETCH_HEAD").exists()


def test_fetch_refuses_an_incomplete_symbolic_ref_inventory(
    git_repo, tmp_path, git_run, monkeypatch
):
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    git_run(
        git_repo, "symbolic-ref", "refs/remotes/origin/main", "refs/heads/not-yet-local"
    )
    tracking = git_repo / ".git" / "refs" / "remotes"
    scandir = os.scandir

    def unreadable_inventory(path="."):
        if not isinstance(path, int) and Path(path).resolve() == tracking.resolve():
            raise PermissionError("Injected unreadable ref inventory")
        return scandir(path)

    monkeypatch.setattr(os, "scandir", unreadable_inventory)
    with pytest.raises(GitError, match="inspect"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))
    assert _oid(git_run, git_repo) == local
    assert git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/heads/not-yet-local", check=False
    ).returncode != 0


def test_fetch_rejects_checked_out_branch_destination_and_preserves_head(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    local_oid = _commit(git_run, git_repo, "local.txt", "local\n")
    remote, remote_oid = _remote_with_branch(tmp_path, git_run)
    assert remote_oid != local_oid
    _add_remote(git_run, git_repo, remote)
    _set_fetch(git_run, git_repo, "+refs/heads/main:refs/heads/main")

    with pytest.raises(GitError, match="remote-tracking"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "HEAD") == local_oid
    assert _oid(git_run, git_repo, "refs/heads/main") == local_oid


@pytest.mark.parametrize("key", ("fetch.pruneTags", "remote.origin.pruneTags"))
def test_fetch_rejects_effective_global_and_remote_tag_pruning(
    git_repo: Path, tmp_path: Path, git_run, key: str
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    git_run(git_repo, "tag", "private-local")
    git_run(git_repo, "config", "fetch.prune", "true")
    git_run(git_repo, "config", key, "true")

    repository = Repository(git_repo)
    with pytest.raises(GitError, match="tag pruning"):
        OperationCoordinator(repository).plan(Action("fetch", remote="origin"))
    with pytest.raises(GitError, match="tag pruning"):
        repository.execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "refs/tags/private-local") == _oid(git_run, git_repo)


def test_fetch_reads_included_inherited_refspec(
    git_repo: Path, tmp_path: Path, git_run, monkeypatch
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    inherited = tmp_path / "inherited-fetch.conf"
    inherited.write_text(
        "[remote \"origin\"]\n"
        "\tfetch = +refs/heads/main:refs/heads/inherited-victim\n"
    )
    home = tmp_path / "fetch-home"
    home.mkdir()
    (home / ".gitconfig").write_text(f"[include]\n\tpath = {inherited}\n")
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(GitError, match="remote-tracking"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))

    missing = git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/heads/inherited-victim", check=False
    )
    assert missing.returncode != 0


def test_fetch_reads_included_inherited_tag_pruning(
    git_repo: Path, tmp_path: Path, git_run, monkeypatch
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    inherited = tmp_path / "inherited-prune.conf"
    inherited.write_text("[fetch]\n\tpruneTags = true\n")
    home = tmp_path / "fetch-prune-home"
    home.mkdir()
    (home / ".gitconfig").write_text(f"[include]\n\tpath = {inherited}\n")
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(GitError, match="tag pruning"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))


@pytest.mark.parametrize("kind", ("recursive", "dangling", "wildcard-dangling"))
def test_fetch_rejects_symbolic_destinations_resolving_outside_remote_cache(
    git_repo: Path, tmp_path: Path, git_run, kind: str
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    if kind == "recursive":
        _set_fetch(git_run, git_repo, "+refs/heads/main:refs/remotes/origin/topic")
        git_run(
            git_repo,
            "symbolic-ref",
            "refs/remotes/origin/topic",
            "refs/remotes/origin/indirect",
        )
        git_run(
            git_repo,
            "symbolic-ref",
            "refs/remotes/origin/indirect",
            "refs/heads/main",
        )
    elif kind == "dangling":
        _set_fetch(git_run, git_repo, "+refs/heads/main:refs/remotes/origin/topic")
        git_run(
            git_repo,
            "symbolic-ref",
            "refs/remotes/origin/topic",
            "refs/heads/missing",
        )
    else:
        _set_fetch(git_run, git_repo, "+refs/heads/*:refs/remotes/origin/*")
        git_run(
            git_repo,
            "symbolic-ref",
            "refs/remotes/origin/topic",
            "refs/tags/missing",
        )

    with pytest.raises(GitError, match="symbolic.*outside"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "refs/heads/main")


def test_fetch_updates_and_prunes_only_remote_cache(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, remote_oid = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    git_run(git_repo, "update-ref", "refs/remotes/origin/stale", "HEAD")
    git_run(git_repo, "tag", "private-local")
    git_run(git_repo, "config", "fetch.prune", "true")
    git_run(git_repo, "config", "fetch.pruneTags", "true")
    git_run(git_repo, "config", "remote.origin.pruneTags", "false")

    Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "refs/remotes/origin/main") == remote_oid
    stale = git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/stale", check=False
    )
    assert stale.returncode != 0
    assert _oid(git_run, git_repo, "refs/tags/private-local") == _oid(git_run, git_repo)


def test_fetch_preserves_negative_refspec_semantics(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, main_oid = _remote_with_branch(tmp_path, git_run)
    seed = tmp_path / "fetch-seed"
    secret_oid = _commit(git_run, seed, "secret.txt", "secret\n")
    git_run(seed, "push", str(remote), "HEAD:refs/heads/secret")
    _add_remote(git_run, git_repo, remote)
    _set_fetch(
        git_run,
        git_repo,
        "+refs/heads/*:refs/remotes/origin/*",
        "^refs/heads/secret",
    )

    Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "refs/remotes/origin/main") == main_oid
    secret = git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/secret", check=False
    )
    assert secret.returncode != 0
    assert secret_oid != main_oid


def test_fetch_preserves_source_only_refspec_semantics(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, remote_oid = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    _set_fetch(git_run, git_repo, "refs/heads/main")

    Repository(git_repo).execute(Action("fetch", remote="origin"))

    fetch_head = git_run(git_repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    assert fetch_head == remote_oid
    tracking = git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/main", check=False
    )
    assert tracking.returncode != 0


def test_fetch_default_tag_autofollow_preserves_existing_tag(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    local_oid = _commit(git_run, git_repo, "local.txt", "local\n")
    git_run(git_repo, "tag", "shared", local_oid)
    remote, remote_oid = _remote_with_branch(tmp_path, git_run)
    seed = tmp_path / "fetch-seed"
    git_run(seed, "tag", "shared", remote_oid)
    git_run(seed, "tag", "new-followed", remote_oid)
    git_run(seed, "push", str(remote), "refs/tags/shared", "refs/tags/new-followed")
    _add_remote(git_run, git_repo, remote)

    Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "refs/tags/shared") == local_oid
    assert _oid(git_run, git_repo, "refs/tags/new-followed") == remote_oid
    assert _oid(git_run, git_repo, "refs/remotes/origin/main") == remote_oid


@pytest.mark.parametrize("skip_key", ("skipDefaultUpdate", "skipFetchAll"))
def test_fetch_all_preserves_remote_skip_behavior(
    git_repo: Path, tmp_path: Path, git_run, skip_key: str
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, remote_oid = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote, "kept")
    git_run(git_repo, "config", "remote.skipped.url", str(tmp_path / "missing.git"))
    git_run(
        git_repo,
        "config",
        "remote.skipped.fetch",
        "+refs/heads/main:refs/heads/must-not-be-validated",
    )
    git_run(git_repo, "config", f"remote.skipped.{skip_key}", "true")

    Repository(git_repo).execute(Action("fetch"))

    assert _oid(git_run, git_repo, "refs/remotes/kept/main") == remote_oid
    missing = git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/heads/must-not-be-validated", check=False
    )
    assert missing.returncode != 0


def test_fetch_without_configured_refspec_pins_remote_head_only(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, remote_oid = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    _set_fetch(git_run, git_repo)

    Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "FETCH_HEAD") == remote_oid
    tracking = git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/main", check=False
    )
    assert tracking.returncode != 0


def test_fetch_honors_explicit_no_tags_without_disabling_safe_cache_updates(
    git_repo, tmp_path, git_run
):
    local = _commit(git_run, git_repo, "local.txt", "local\n")
    remote, incoming = _remote_with_branch(tmp_path, git_run)
    seed = tmp_path / "fetch-seed"
    git_run(seed, "tag", "remote-only")
    git_run(seed, "push", str(remote), "refs/tags/remote-only")
    _add_remote(git_run, git_repo, remote)
    git_run(git_repo, "config", "remote.origin.tagOpt", "--no-tags")

    Repository(git_repo).execute(Action("fetch", remote="origin"))

    assert _oid(git_run, git_repo, "refs/remotes/origin/main") == incoming
    assert _oid(git_run, git_repo) == local
    assert git_run(
        git_repo, "show-ref", "--verify", "--quiet", "refs/tags/remote-only", check=False
    ).returncode != 0
    assert git_run(git_repo, "config", "remote.origin.tagOpt").stdout.strip() == "--no-tags"


def test_fetch_rejects_nondefault_tag_mode(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    git_run(git_repo, "config", "remote.origin.tagOpt", "--tags")

    with pytest.raises(GitError, match="default.*tag auto-follow"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))


@pytest.mark.parametrize(
    "refspec",
    (
        "+refs/heads/*:refs/remotes/origin/main",
        "^refs/heads/main:refs/remotes/origin/main",
        "refs/heads/*",
    ),
)
def test_fetch_rejects_malformed_or_unsupported_refspecs(
    git_repo: Path, tmp_path: Path, git_run, refspec: str
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    _set_fetch(git_run, git_repo, refspec)

    with pytest.raises(GitError, match="invalid refspec|malformed|unsupported"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))


def test_fetch_rejects_ambiguous_cache_mappings(
    git_repo: Path, tmp_path: Path, git_run
) -> None:
    _commit(git_run, git_repo, "local.txt", "local\n")
    remote, _ = _remote_with_branch(tmp_path, git_run)
    _add_remote(git_run, git_repo, remote)
    _set_fetch(
        git_run,
        git_repo,
        "+refs/heads/*:refs/remotes/origin/*",
        "+refs/tags/*:refs/remotes/origin/*",
    )

    with pytest.raises(GitError, match="ambiguous"):
        Repository(git_repo).execute(Action("fetch", remote="origin"))
