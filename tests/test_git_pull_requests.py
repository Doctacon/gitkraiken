from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import rhizome.git as git_module
from rhizome.git import GitError, Repository
from rhizome.models import Action
from rhizome.pull_requests import PullRequestError


def _oid(git_run, root: Path, ref: str = "HEAD") -> str:
    return git_run(root, "rev-parse", ref).stdout.strip()


def _guard(git_run, root: Path, target: str) -> Action:
    return Action(
        "create-pr",
        target=target,
        expected_branch=git_run(root, "branch", "--show-current").stdout.strip(),
        expected_head=_oid(git_run, root),
        expected_ref=target,
        expected_oid=_oid(git_run, root, target),
    )


def _published_feature_repository(
    git_repo: Path, bare_remote: Path, git_run
) -> tuple[Repository, Action]:
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "-u", "origin", "main")
    git_run(git_repo, "switch", "-c", "feature")
    (git_repo / "feature.txt").write_text("feature\n")
    git_run(git_repo, "add", "feature.txt")
    git_run(git_repo, "commit", "-m", "feature")
    git_run(git_repo, "push", "-u", "origin", "feature")
    action = _guard(git_run, git_repo, "refs/remotes/origin/main")
    git_run(git_repo, "remote", "set-url", "origin", "git@github.com:acme/widgets.git")
    return Repository(git_repo), action


def test_plans_use_canonical_upstreams_and_do_not_require_different_oids(
    git_repo: Path, bare_remote: Path, git_run
) -> None:
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "remote", "add", "origin", str(bare_remote))
    git_run(git_repo, "push", "-u", "origin", "main")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "push", "-u", "origin", "feature")
    action = _guard(git_run, git_repo, "refs/remotes/origin/main")
    git_run(git_repo, "remote", "set-url", "origin", "https://github.com/acme/widgets.git")

    plans = Repository(git_repo).pull_request_plans(action)

    assert len(plans) == 1
    assert plans[0].remote == "origin"
    assert plans[0].source_branch == "feature"
    assert plans[0].target_branch == "main"
    assert plans[0].repository.web_url == "https://github.com/acme/widgets"


def test_unowned_local_branches_offer_each_supported_safe_remote(git_repo: Path, git_run) -> None:
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "branch", "test")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "remote", "add", "one", "git@github.com:acme/widgets.git")
    git_run(
        git_repo,
        "remote",
        "add",
        "two",
        "https://dev.azure.com/org/project/_git/widgets",
    )
    action = _guard(git_run, git_repo, "refs/heads/test")

    plans = Repository(git_repo).pull_request_plans(action)

    assert [plan.remote for plan in plans] == ["one", "two"]
    assert {plan.repository.provider for plan in plans} == {"GitHub", "Azure DevOps"}


def test_local_source_upstream_is_not_mistaken_for_hosted_source_name(
    git_repo: Path, git_run
) -> None:
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "branch", "test")
    git_run(git_repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    git_run(git_repo, "config", "branch.test.remote", "origin")
    git_run(git_repo, "config", "branch.test.merge", "refs/heads/test")
    git_run(git_repo, "update-ref", "refs/remotes/origin/test", "refs/heads/test")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "config", "branch.feature.remote", ".")
    git_run(git_repo, "config", "branch.feature.merge", "refs/heads/test")
    action = _guard(git_run, git_repo, "refs/heads/test")

    plan = Repository(git_repo).pull_request_plans(action)[0]

    assert plan.remote == "origin"
    assert plan.source_branch == "feature"
    assert plan.target_branch == "test"


def test_same_repository_push_url_may_use_different_transport_casing(
    git_repo: Path, git_run
) -> None:
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "branch", "test")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "remote", "add", "origin", "https://github.com/Acme/Widgets.git")
    git_run(git_repo, "remote", "set-url", "--push", "origin", "git@github.com:acme/widgets.git")
    action = _guard(git_run, git_repo, "refs/heads/test")

    plan = Repository(git_repo).pull_request_plans(action)[0]

    assert plan.repository.web_url == "https://github.com/Acme/Widgets"


def test_plan_respects_custom_fetch_mapping(git_repo: Path, git_run) -> None:
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    head = _oid(git_run, git_repo)
    git_run(git_repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
    git_run(
        git_repo,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/heads/release/*:refs/remotes/origin/stable/*",
    )
    git_run(git_repo, "update-ref", "refs/remotes/origin/stable/test", head)
    git_run(git_repo, "branch", "feature")
    git_run(git_repo, "config", "branch.feature.remote", "origin")
    git_run(git_repo, "config", "branch.feature.merge", "refs/heads/release/feature")
    git_run(git_repo, "update-ref", "refs/remotes/origin/stable/feature", head)
    git_run(git_repo, "switch", "feature")
    action = _guard(git_run, git_repo, "refs/remotes/origin/stable/test")

    plan = Repository(git_repo).pull_request_plans(action)[0]

    assert plan.source_branch == "release/feature"
    assert plan.target_branch == "release/test"


def test_plan_rejects_different_push_repository(git_repo: Path, git_run) -> None:
    (git_repo / "base.txt").write_text("base\n")
    git_run(git_repo, "add", "base.txt")
    git_run(git_repo, "commit", "-m", "base")
    git_run(git_repo, "branch", "test")
    git_run(git_repo, "switch", "-c", "feature")
    git_run(git_repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    git_run(git_repo, "remote", "set-url", "--push", "origin", "git@github.com:someone/fork.git")
    action = _guard(git_run, git_repo, "refs/heads/test")

    with pytest.raises(GitError, match="different or unsupported"):
        Repository(git_repo).pull_request_plans(action)


def test_submit_verifies_both_live_refs_and_exact_source_oid_before_provider(
    monkeypatch, git_repo: Path, bare_remote: Path, git_run
) -> None:
    repo, action = _published_feature_repository(git_repo, bare_remote, git_run)
    plan = repo.pull_request_plans(action)[0]
    original_git = repo._git
    network_calls = []

    def intercepted_git(args, **kwargs):
        if args[:2] == ["ls-remote", "--heads"]:
            network_calls.append((args, kwargs))
            output = (
                f"{plan.guard.expected_head}\trefs/heads/{plan.source_branch}\n"
                f"{plan.guard.expected_oid}\trefs/heads/{plan.target_branch}\n"
            ).encode()
            return subprocess.CompletedProcess(["git", *args], 0, output, b"")
        return original_git(args, **kwargs)

    provider_calls = []
    monkeypatch.setattr(repo, "_git", intercepted_git)
    monkeypatch.setattr(
        git_module,
        "_create_hosted_pull_request",
        lambda *args: provider_calls.append(args) or "https://github.com/acme/widgets/pull/9",
    )

    result = repo.create_pull_request(plan, " title ", "line one\nline two")

    assert result.endswith("/pull/9")
    assert len(network_calls) == 1
    assert network_calls[0][0] == [
        "ls-remote",
        "--heads",
        "--",
        "origin",
        "refs/heads/feature",
        "refs/heads/main",
    ]
    assert len(provider_calls) == 1


@pytest.mark.parametrize("mode", ["missing-source", "missing-target", "outdated-source"])
def test_submit_stops_before_provider_when_remote_branches_are_not_ready(
    monkeypatch, git_repo: Path, bare_remote: Path, git_run, mode: str
) -> None:
    repo, action = _published_feature_repository(git_repo, bare_remote, git_run)
    plan = repo.pull_request_plans(action)[0]
    original_git = repo._git

    def intercepted_git(args, **kwargs):
        if args[:2] != ["ls-remote", "--heads"]:
            return original_git(args, **kwargs)
        source_oid = "0" * 40 if mode == "outdated-source" else plan.guard.expected_head
        records = []
        if mode != "missing-source":
            records.append(f"{source_oid}\trefs/heads/{plan.source_branch}\n")
        if mode != "missing-target":
            records.append(f"{plan.guard.expected_oid}\trefs/heads/{plan.target_branch}\n")
        return subprocess.CompletedProcess(["git", *args], 0, "".join(records).encode(), b"")

    monkeypatch.setattr(repo, "_git", intercepted_git)
    monkeypatch.setattr(
        git_module,
        "_create_hosted_pull_request",
        lambda *args: pytest.fail("provider must not be called"),
    )

    with pytest.raises(GitError, match="Push|Publish|published"):
        repo.create_pull_request(plan, "title", "description")


def test_submit_rejects_head_and_remote_url_drift_before_network(
    monkeypatch, git_repo: Path, bare_remote: Path, git_run
) -> None:
    repo, action = _published_feature_repository(git_repo, bare_remote, git_run)
    plan = repo.pull_request_plans(action)[0]

    (git_repo / "later.txt").write_text("later\n")
    git_run(git_repo, "add", "later.txt")
    git_run(git_repo, "commit", "-m", "later")
    with pytest.raises(GitError, match="Branch changed"):
        repo.create_pull_request(plan, "title", "description")

    git_run(git_repo, "reset", "--hard", action.expected_head)
    git_run(git_repo, "remote", "set-url", "origin", "git@github.com:acme/other.git")
    with pytest.raises(GitError, match="mapping changed"):
        repo.create_pull_request(plan, "title", "description")


@pytest.mark.parametrize("change", ["target-oid", "source-mapping", "target-mapping"])
def test_context_drift_rejects_creation_before_any_network(
    monkeypatch, git_repo: Path, bare_remote: Path, git_run, change: str
) -> None:
    repo, action = _published_feature_repository(git_repo, bare_remote, git_run)
    plan = repo.pull_request_plans(action)[0]
    if change == "target-oid":
        git_run(git_repo, "update-ref", action.target, action.expected_head)
    elif change == "source-mapping":
        git_run(git_repo, "update-ref", "refs/remotes/origin/renamed", action.expected_head)
        git_run(git_repo, "config", "branch.feature.merge", "refs/heads/renamed")
    else:
        git_run(git_repo, "config", "--unset-all", "remote.origin.fetch")
        git_run(
            git_repo,
            "config",
            "--add",
            "remote.origin.fetch",
            "+refs/heads/feature:refs/remotes/origin/feature",
        )
        git_run(
            git_repo,
            "config",
            "--add",
            "remote.origin.fetch",
            "+refs/heads/release/main:refs/remotes/origin/main",
        )
    original_git = repo._git

    def no_network(args, **kwargs):
        assert args[0] != "ls-remote", "Stale context must fail before network I/O"
        return original_git(args, **kwargs)

    monkeypatch.setattr(repo, "_git", no_network)
    monkeypatch.setattr(
        git_module, "_create_hosted_pull_request", lambda *args: pytest.fail("Unexpected create")
    )
    with pytest.raises(GitError, match="changed"):
        repo.create_pull_request(plan, "title", "body")


def test_remote_identity_is_rechecked_after_network_verification(
    monkeypatch, git_repo: Path, bare_remote: Path, git_run
) -> None:
    repo, action = _published_feature_repository(git_repo, bare_remote, git_run)
    plan = repo.pull_request_plans(action)[0]
    original_git = repo._git

    def change_remote_during_network(args, **kwargs):
        if args[0] == "ls-remote":
            git_run(git_repo, "remote", "set-url", "origin", "https://github.com/other/repo.git")
            output = (
                f"{action.expected_head}\trefs/heads/feature\n"
                f"{action.expected_oid}\trefs/heads/main\n"
            ).encode()
            return subprocess.CompletedProcess(args, 0, output, b"")
        return original_git(args, **kwargs)

    monkeypatch.setattr(repo, "_git", change_remote_during_network)
    monkeypatch.setattr(
        git_module, "_create_hosted_pull_request", lambda *args: pytest.fail("Unexpected create")
    )
    with pytest.raises(GitError, match="mapping changed"):
        repo.create_pull_request(plan, "title", "body")


def test_planning_rejects_multiple_push_destinations(git_repo, bare_remote, git_run):
    repo, action = _published_feature_repository(git_repo, bare_remote, git_run)
    for url in ("https://github.com/acme/widgets.git", "https://github.com/other/widgets.git"):
        git_run(git_repo, "config", "--add", "remote.origin.pushurl", url)
    with pytest.raises(GitError, match="multiple push destinations"):
        repo.pull_request_plans(action)


def test_blank_title_stops_before_git_network(
    monkeypatch, git_repo: Path, bare_remote: Path, git_run
) -> None:
    repo, action = _published_feature_repository(git_repo, bare_remote, git_run)
    plan = repo.pull_request_plans(action)[0]
    original_git = repo._git

    def no_network(args, **kwargs):
        if args and args[0] == "ls-remote":
            pytest.fail("network must not run")
        return original_git(args, **kwargs)

    monkeypatch.setattr(repo, "_git", no_network)
    with pytest.raises(PullRequestError, match="title"):
        repo.create_pull_request(plan, "  ", "description")
