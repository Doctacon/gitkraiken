from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gitkraiken.pull_requests import (
    HostedRepository,
    PullRequestError,
    create_pull_request,
    hosted_repository,
)


@pytest.mark.parametrize(
    ("remote", "provider", "web_url"),
    [
        ("https://github.com/acme/widgets.git", "GitHub", "https://github.com/acme/widgets"),
        ("https://github.com/acme/widgets.git/", "GitHub", "https://github.com/acme/widgets"),
        (
            "https://dev.azure.com/org/project/_git/repo/",
            "Azure DevOps",
            "https://dev.azure.com/org/project/_git/repo",
        ),
        ("git@github.com:acme/widgets.git", "GitHub", "https://github.com/acme/widgets"),
        ("ssh://git@github.com/acme/widgets", "GitHub", "https://github.com/acme/widgets"),
        (
            "https://token:secret@github.com/a%20b/r%23x.git?token=bad#secret",
            "GitHub",
            "https://github.com/a%20b/r%23x",
        ),
        (
            "https://dev.azure.com/my%20org/a%20project/_git/a%23repo",
            "Azure DevOps",
            "https://dev.azure.com/my%20org/a%20project/_git/a%23repo",
        ),
        (
            "git@ssh.dev.azure.com:v3/my%20org/a%20project/a%23repo",
            "Azure DevOps",
            "https://dev.azure.com/my%20org/a%20project/_git/a%23repo",
        ),
        (
            "ssh://git@ssh.dev.azure.com/v3/org/project/repo",
            "Azure DevOps",
            "https://dev.azure.com/org/project/_git/repo",
        ),
        (
            "https://org.visualstudio.com/DefaultCollection/project/_git/repo",
            "Azure DevOps",
            "https://dev.azure.com/org/project/_git/repo",
        ),
    ],
)
def test_hosted_repository_normalizes_supported_remote_shapes(
    remote: str, provider: str, web_url: str
) -> None:
    parsed = hosted_repository(remote)

    assert parsed is not None
    assert parsed.provider == provider
    assert parsed.web_url == web_url
    assert "secret" not in repr(parsed)
    assert "token" not in parsed.web_url


@pytest.mark.parametrize(
    "remote",
    [
        "http://github.com/acme/widgets",
        "https://github.example/acme/widgets",
        "https://github.com:8443/acme/widgets",
        "https://github.com/acme/widgets/extra",
        "https://github.com/acme%2Fother/widgets",
        "https://dev.azure.com/org/project/repo",
        "https://dev.azure.com/org/project/_git/repo/extra",
        "https://ssh.dev.azure.com/v2/org/project/repo",
        "https://server.example/tfs/project/_git/repo",
        "not a URL",
    ],
)
def test_hosted_repository_rejects_unsupported_or_unsafe_urls(remote: str) -> None:
    assert hosted_repository(remote) is None


def test_github_creation_uses_one_literal_cli_request(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b'{"number":17}', b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = HostedRepository("GitHub", "https://github.com/acme/repo", "repo", owner="acme")
    title = "  --literal title  "
    description = "first line\n--not-an-option\nsecret-looking=value"

    url = create_pull_request(tmp_path, repository, "feature/x", "test", title, description)

    assert url == "https://github.com/acme/repo/pull/17"
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == [
        "gh",
        "api",
        "--hostname",
        "github.com",
        "--method",
        "POST",
        "repos/acme/repo/pulls",
        "--input",
        "-",
    ]
    assert json.loads(options["input"]) == {
        "head": "feature/x",
        "base": "test",
        "title": "--literal title",
        "body": description,
    }
    assert options["timeout"] == 60.0
    assert options["check"] is False
    assert options["env"]["GH_PROMPT_DISABLED"] == "true"


@pytest.mark.parametrize("description", ["", "line one\n--debug\nline three"])
def test_azure_creation_uses_equals_arguments_and_preserves_description(
    monkeypatch, tmp_path: Path, description: str
) -> None:
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b'{"pullRequestId":23}', b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = HostedRepository(
        "Azure DevOps",
        "https://dev.azure.com/org/project/_git/repo",
        "repo",
        organization="org",
        project="project",
    )
    url = create_pull_request(tmp_path, repository, "-source", "-target", "--title", description)

    assert url == "https://dev.azure.com/org/project/_git/repo/pullrequest/23"
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[:4] == ["az", "repos", "pr", "create"]
    assert "--organization=https://dev.azure.com/org" in argv
    assert "--project=project" in argv
    assert "--repository=repo" in argv
    assert "--detect=false" in argv
    assert "--output=json" in argv
    assert "--source-branch=-source" in argv
    assert "--target-branch=-target" in argv
    assert "--title=--title" in argv
    assert f"--description={description}" in argv
    assert "--open" not in argv
    assert options["stdin"] is subprocess.DEVNULL
    assert options["env"]["AZURE_EXTENSION_USE_DYNAMIC_INSTALL"] == "no"


@pytest.mark.parametrize(
    ("behavior", "message"),
    [
        (FileNotFoundError(), "required"),
        (subprocess.TimeoutExpired(["gh"], 60), "may have succeeded"),
        (subprocess.CompletedProcess(["gh"], 1, b"token=secret", b"credential"), "gh auth login"),
        (subprocess.CompletedProcess(["gh"], 0, b"not-json", b""), "may have succeeded"),
        (subprocess.CompletedProcess(["gh"], 0, b'{"number":0}', b""), "may have succeeded"),
        (subprocess.CompletedProcess(["gh"], 0, b'{"number":true}', b""), "may have succeeded"),
        (subprocess.CompletedProcess(["gh"], 0, b"[]", b""), "may have succeeded"),
    ],
)
def test_creation_failures_are_safe_and_never_retry(
    monkeypatch, tmp_path: Path, behavior: object, message: str
) -> None:
    calls = 0

    def fake_run(argv, **kwargs):
        nonlocal calls
        calls += 1
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior

    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = HostedRepository("GitHub", "https://github.com/acme/repo", "repo", owner="acme")

    with pytest.raises(PullRequestError, match=message) as caught:
        create_pull_request(tmp_path, repository, "feature", "main", "title", "body")

    assert calls == 1
    assert "secret" not in str(caught.value)


def test_azure_failure_includes_exact_setup_without_raw_output(monkeypatch, tmp_path: Path) -> None:
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, b"pat=secret", b"credential secret")

    monkeypatch.setattr(subprocess, "run", fake_run)
    repository = HostedRepository(
        "Azure DevOps",
        "https://dev.azure.com/org/project/_git/repo",
        "repo",
        organization="org",
        project="project",
    )

    with pytest.raises(PullRequestError) as caught:
        create_pull_request(tmp_path, repository, "feature", "main", "title", "")

    message = str(caught.value)
    assert "az extension add --name azure-devops" in message
    assert "az login" in message
    assert "az devops login" in message
    assert "check the repository pull requests before retrying" in message.lower()
    assert "secret" not in message


def test_blank_title_stops_before_cli(monkeypatch, tmp_path: Path) -> None:
    def unexpected(*args, **kwargs):  # pragma: no cover - assertion helper
        raise AssertionError("CLI must not run")

    monkeypatch.setattr(subprocess, "run", unexpected)
    repository = HostedRepository("GitHub", "https://github.com/acme/repo", "repo", owner="acme")

    with pytest.raises(PullRequestError, match="title"):
        create_pull_request(tmp_path, repository, "feature", "main", " \n", "body")
