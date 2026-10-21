from dataclasses import replace
from pathlib import Path

import pytest

from gitkraiken.context_actions import ContextTarget, context_actions, remote_link
from gitkraiken.models import Ref, Remote, Snapshot


def snapshot():
    return Snapshot(
        root=Path("/repo"),
        branch="feature/payments",
        head="a" * 40,
        upstream="origin/payments",
        refs=(
            Ref(
                "feature/payments",
                "refs/heads/feature/payments",
                "a" * 40,
                "local",
                True,
                "origin/payments",
            ),
            Ref("test", "refs/heads/test", "b" * 40, "local"),
            Ref("origin/test", "refs/remotes/origin/test", "b" * 40, "remote", remote="origin"),
        ),
        remotes=(
            Remote(
                "origin", "git@github.com:org/repo.git", "GitHub", "https://github.com/org/repo"
            ),
        ),
    )


def ids(state, target):
    return {action[0] for action in context_actions(state, target) if action is not None}


def test_branch_menu_operations_apply_to_clicked_ref_not_current_branch():
    state = snapshot()
    other = ContextTarget(state.refs[1].oid, state.refs[1])
    available = ids(state, other)
    assert {
        "checkout",
        "create-branch",
        "create-worktree",
        "tag",
        "merge",
        "rebase",
        "reset",
        "revert",
        "rename-branch",
        "delete-branch",
        "copy-sha",
        "copy-branch",
        "set-upstream",
        "apply-patch",
    } <= available
    assert not {"pull", "push", "edit-message", "delete-remote-branch"} & available
    labels = dict(action for action in context_actions(state, other) if action)
    assert labels["rebase"] == "Rebase feature/payments onto test"
    assert labels["create-pr"] == "Start a PR against test from feature/payments…"
    assert other.revision == "refs/heads/test"
    assert other.label == "test"


def test_current_and_remote_contexts_have_distinct_safe_actions():
    state = snapshot()
    current = ids(state, ContextTarget(state.head, state.refs[0]))
    assert {"pull", "push", "reset", "edit-message", "rename-branch"} <= current
    assert not {"checkout", "delete-branch", "merge", "rebase", "create-pr"} & current
    remote = ids(state, ContextTarget(state.refs[2].oid, state.refs[2]))
    assert {"delete-remote-branch", "copy-branch-link", "set-upstream"} <= remote
    assert not {"rename-branch", "delete-branch", "pull", "push"} & remote
    orphan = replace(state.refs[2], remote="")
    assert "delete-remote-branch" not in ids(state, ContextTarget(orphan.oid, orphan))


def test_detached_and_operation_menus_keep_copy_without_invalid_mutations():
    state = snapshot()
    target = ContextTarget(state.refs[1].oid, state.refs[1])
    detached = ids(replace(state, branch=""), target)
    assert {"checkout", "create-branch", "create-worktree", "copy-sha"} <= detached
    assert not {"rebase", "reset", "edit-message", "merge", "revert", "create-pr"} & detached
    active = ids(replace(state, operation="rebase"), target)
    assert active == {"copy-sha", "copy-branch", "copy-commit-link"}


def test_local_only_remotes_do_not_advertise_browser_links():
    state = replace(snapshot(), remotes=(Remote("origin", "/tmp/local.git"),))
    available = ids(state, ContextTarget(state.refs[2].oid, state.refs[2]))
    assert "copy-branch-link" not in available and "copy-commit-link" not in available
    assert "create-pr" not in available


def test_commit_menu_never_renames_a_previously_selected_branch():
    state = snapshot()
    target = ContextTarget(state.head)
    assert target.revision == state.head
    available = ids(state, target)
    assert {"checkout", "create-branch", "reset", "edit-message", "copy-sha"} <= available
    assert (
        not {
            "rename-branch",
            "delete-branch",
            "copy-branch",
            "set-upstream",
            "pull",
            "push",
            "create-pr",
        }
        & available
    )


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:org/repo.git",
        "https://dev.azure.com/org/project/_git/repo",
        "git@ssh.dev.azure.com:v3/org/project/repo",
    ],
)
def test_pr_menu_supports_local_and_remote_targets_even_at_same_commit(url):
    state = replace(snapshot(), remotes=(Remote("origin", url),))
    for ref in state.refs[1:]:
        ref = replace(ref, oid=state.head)
        assert "create-pr" in ids(state, ContextTarget(ref.oid, ref))
    source_remote = replace(state.refs[2], name="origin/payments")
    assert "create-pr" not in ids(state, ContextTarget(source_remote.oid, source_remote))


def test_pr_menu_does_not_offer_unsupported_remote_when_another_is_supported():
    state = snapshot()
    state = replace(state, remotes=(*state.remotes, Remote("other", "https://gitlab.com/o/r")))
    ref = replace(state.refs[2], remote="other")
    assert "create-pr" not in ids(state, ContextTarget(ref.oid, ref))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/org/repo.git", "https://github.com/org/repo/tree/feature%2Fone%23two"),
        (
            "https://gitlab.com/group/team/repo",
            "https://gitlab.com/group/team/repo/-/tree/feature%2Fone%23two",
        ),
        (
            "https://dev.azure.com/org/project/_git/repo",
            "https://dev.azure.com/org/project/_git/repo?version=GBfeature%2Fone%23two",
        ),
        (
            "https://ssh.dev.azure.com/v3/org/project/repo",
            "https://dev.azure.com/org/project/_git/repo?version=GBfeature%2Fone%23two",
        ),
        (
            "git@ssh.dev.azure.com:v3/org/project/repo",
            "https://dev.azure.com/org/project/_git/repo?version=GBfeature%2Fone%23two",
        ),
    ],
)
def test_browser_branch_links_encode_remote_source_names(url, expected):
    assert remote_link(Remote("origin", url, web_url=url), "branch", "feature/one#two") == expected


def test_browser_links_strip_credentials_and_unsupported_hosts_have_no_link():
    remote = Remote(
        "origin", "", web_url="https://user:secret@github.com/org/repo.git?token=secret#secret"
    )
    assert remote_link(remote, "commit", "abc123") == "https://github.com/org/repo/commit/abc123"
    assert (
        remote_link(Remote("origin", "ssh://git@github.com:22/org/repo.git"), "commit", "abc123")
        == "https://github.com/org/repo/commit/abc123"
    )
    assert (
        remote_link(
            Remote("origin", "ssh://git@ssh.dev.azure.com/v3/org/project/repo"), "commit", "abc123"
        )
        == "https://dev.azure.com/org/project/_git/repo/commit/abc123"
    )
    for url in (
        "/tmp/bare.git",
        "https://unknown.example/org/repo",
        "file:///tmp/repo",
        "https://github.com:bad/repo",
        "https://dev.azure.com/org/project",
    ):
        assert not remote_link(Remote("origin", url, web_url=url), "branch", "test")
