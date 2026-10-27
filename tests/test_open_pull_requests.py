from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import rhizome.git as git_module
import rhizome.pull_requests as pull_request_module
from rhizome.git import Repository
from rhizome.pull_requests import (
    HostedRepository,
    PullRequest,
    PullRequestError,
    list_open_pull_requests,
)


def _github_item(number: int) -> dict[str, object]:
    return {
        "number": number,
        "title": f"Request {number}",
        "user": {"login": "octocat"},
        "draft": number % 2 == 0,
        "head": {"ref": f"feature/{number}"},
        "base": {"ref": "main"},
        "html_url": "https://attacker.invalid/not-trusted",
    }


def test_github_listing_paginates_and_uses_canonical_urls(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    deleted_author = _github_item(2) | {"user": None}
    pages = [[_github_item(1), deleted_author], [_github_item(3)]]

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(pages.pop(0)).encode(), b"")

    monkeypatch.setattr(pull_request_module, "_LIST_PAGE_SIZE", 2)
    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = HostedRepository(
        "GitHub", "https://attacker.invalid/secret", "widgets", owner="Acme Org"
    )

    requests = list_open_pull_requests(tmp_path, repository)

    assert [request.number for request in requests] == [1, 2, 3]
    assert requests[1].draft
    assert requests[0].author == "octocat"
    assert requests[1].author == ""
    assert requests[0].source_branch == "feature/1"
    assert requests[0].target_branch == "main"
    assert requests[0].url == "https://github.com/Acme%20Org/widgets/pull/1"
    assert [call[0][-1] for call in calls] == [
        "repos/Acme%20Org/widgets/pulls?state=open&per_page=2&page=1",
        "repos/Acme%20Org/widgets/pulls?state=open&per_page=2&page=2",
    ]
    assert all(call[1]["cwd"] == tmp_path for call in calls)
    assert all(call[1]["stdin"] is subprocess.DEVNULL for call in calls)
    assert all(call[1]["env"]["GH_PROMPT_DISABLED"] == "true" for call in calls)
    assert all(call[1]["timeout"] == 60.0 and call[1]["check"] is False for call in calls)


def test_azure_listing_uses_active_rest_pages_and_validates_branches(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    pages = [
        [
            {
                "pullRequestId": 7,
                "title": "Ship it",
                "createdBy": {"uniqueName": "ada@example.invalid"},
                "isDraft": True,
                "sourceRefName": "refs/heads/feature",
                "targetRefName": "refs/heads/main",
                "url": "https://attacker.invalid/raw",
            }
        ],
        [],
    ]

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(pages.pop(0)).encode(), b"")

    monkeypatch.setattr(pull_request_module, "_LIST_PAGE_SIZE", 1)
    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = HostedRepository(
        "Azure DevOps",
        "https://attacker.invalid/secret",
        "repo",
        organization="my org",
        project="a project",
    )

    requests = list_open_pull_requests(tmp_path, repository)

    assert requests == (
        PullRequest(
            7,
            "Ship it",
            "https://dev.azure.com/my%20org/a%20project/_git/repo/pullrequest/7",
            "feature",
            "main",
            "ada@example.invalid",
            True,
        ),
    )
    assert len(calls) == 2
    assert calls[0][:4] == ["az", "repos", "pr", "list"]
    assert "--status=active" in calls[0]
    assert "--top=1" in calls[0]
    assert "--skip=0" in calls[0]
    assert "--skip=1" in calls[1]
    assert "--detect=false" in calls[0]
    assert "--only-show-errors" in calls[0]


@pytest.mark.parametrize(
    "stdout",
    [
        b"not json",
        b"{}",
        b'[{"number":true}]',
        json.dumps([{"number": 1, "title": "x", "head": {}, "base": {"ref": "main"}}]).encode(),
        json.dumps([_github_item(1) | {"draft": "false"}]).encode(),
    ],
)
def test_listing_rejects_malformed_provider_responses(
    monkeypatch, tmp_path: Path, stdout: bytes
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout, b"secret"),
    )
    repository = HostedRepository("GitHub", "ignored", "repo", owner="acme")

    with pytest.raises(PullRequestError, match="malformed") as caught:
        list_open_pull_requests(tmp_path, repository)

    assert "secret" not in str(caught.value)


def test_listing_errors_are_generic_and_pagination_has_a_safety_bound(
    monkeypatch, tmp_path: Path
) -> None:
    repository = HostedRepository("GitHub", "ignored", "repo", owner="acme")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, b"token=secret", b"credential secret"
        ),
    )
    with pytest.raises(PullRequestError, match="gh auth login") as caught:
        list_open_pull_requests(tmp_path, repository)
    assert "secret" not in str(caught.value)

    calls = 0

    def full_page(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(argv, 0, json.dumps([_github_item(calls)]).encode(), b"")

    monkeypatch.setattr(subprocess, "run", full_page)
    monkeypatch.setattr(pull_request_module, "_LIST_PAGE_SIZE", 1)
    monkeypatch.setattr(pull_request_module, "_MAX_LIST_PAGES", 2)
    with pytest.raises(PullRequestError, match="safety limit"):
        list_open_pull_requests(tmp_path, repository)
    assert calls == 2


def test_repository_listing_deduplicates_fetch_remotes_and_isolates_errors(
    monkeypatch, git_repo: Path, git_run
) -> None:
    git_run(git_repo, "remote", "add", "z-duplicate", "git@github.com:ACME/Widgets.git")
    git_run(git_repo, "remote", "add", "a-github", "https://github.com/acme/widgets.git")
    git_run(
        git_repo,
        "remote",
        "add",
        "b-azure",
        "https://dev.azure.com/org/project/_git/repo",
    )
    git_run(git_repo, "remote", "add", "unsupported", "https://gitlab.com/acme/other.git")
    calls: list[HostedRepository] = []

    def fake_list(root: Path, repository: HostedRepository):
        calls.append(repository)
        if repository.provider == "GitHub":
            raise PullRequestError("GitHub CLI (gh) could not list pull requests.")
        return (PullRequest(4, "Azure", f"{repository.web_url}/pullrequest/4", "x", "main"),)

    monkeypatch.setattr(git_module, "_list_open_pull_requests", fake_list)

    listings = Repository(git_repo).open_pull_requests()

    assert [listing.remote for listing in listings] == ["a-github", "b-azure"]
    assert [repository.provider for repository in calls] == ["GitHub", "Azure DevOps"]
    assert listings[0].requests == ()
    assert "could not list" in listings[0].error
    assert listings[1].error == ""
    assert listings[1].requests[0].number == 4
