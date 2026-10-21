"""Hosted-repository parsing and headless pull-request creation clients."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote_to_bytes, urlsplit

from .models import Action

_CLI_TIMEOUT = 60.0
_BAD_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class HostedRepository:
    """A supported hosted Git repository with a canonical credential-free URL."""

    provider: str
    web_url: str
    repository: str
    owner: str = ""
    organization: str = ""
    project: str = ""


@dataclass(frozen=True)
class PullRequestPlan:
    """A locally-derived, guarded pull-request destination."""

    remote: str
    repository: HostedRepository
    source_branch: str
    target_branch: str
    guard: Action


class PullRequestError(RuntimeError):
    """A safe, human-readable pull-request failure."""


def _component(value: str) -> str | None:
    """Strictly decode one URL path component."""
    if not value or _BAD_PERCENT_RE.search(value):
        return None
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", "strict")
    except UnicodeDecodeError:
        return None
    if (
        not decoded
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or _CONTROL_RE.search(decoded)
    ):
        return None
    return decoded


def _encoded(value: str) -> str:
    return quote(value, safe="")


def _split_remote_url(url: str) -> tuple[str, str, str, int | None] | None:
    """Return scheme, exact host, path, and port for URL and SCP URL forms."""
    if (
        not isinstance(url, str)
        or not url
        or _CONTROL_RE.search(url)
        or any(character.isspace() for character in url)
    ):
        return None

    # SCP-like Git URLs have no scheme and use the first colon after the host
    # as the path separator. Query strings and fragments are not repository
    # identity and are deliberately discarded just like URL-form equivalents.
    scp = re.fullmatch(r"(?:(?P<user>[^@\s/:]+)@)?(?P<host>[^@\s/:?#]+):(?P<path>[^\s]+)", url)
    if scp and "://" not in url:
        path = re.split(r"[?#]", scp.group("path"), maxsplit=1)[0]
        return "scp", scp.group("host").lower(), path, None

    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        return None
    if not parts.scheme or not parts.netloc or parts.hostname is None:
        return None
    if parts.path.startswith("//"):
        return None
    return parts.scheme.lower(), parts.hostname.lower(), parts.path, port


def _decoded_path(path: str) -> list[str] | None:
    # A single trailing slash is a common, equivalent clone-URL spelling.
    path = path.removesuffix("/")
    if not path.startswith("/"):
        # SCP paths conventionally have no leading slash.
        raw = path.split("/")
    else:
        raw = path[1:].split("/")
    if not raw or any(not item for item in raw):
        return None
    decoded = [_component(item) for item in raw]
    if any(item is None for item in decoded):
        return None
    return [item for item in decoded if item is not None]


def hosted_repository(url: str) -> HostedRepository | None:
    """Parse a supported GitHub.com or Azure DevOps cloud remote URL.

    Parsing is local and strict. The returned web URL never includes userinfo,
    query parameters, fragments, or a noncanonical transport spelling.
    """
    split = _split_remote_url(url)
    if split is None:
        return None
    scheme, host, path, port = split

    if host == "github.com":
        if scheme not in {"https", "ssh", "scp"}:
            return None
        if port is not None and port != (443 if scheme == "https" else 22):
            return None
        pieces = _decoded_path(path)
        if pieces is None or len(pieces) != 2:
            return None
        owner, repository = pieces
        if repository.endswith(".git"):
            repository = repository[:-4]
        if not owner or not repository:
            return None
        web_url = f"https://github.com/{_encoded(owner)}/{_encoded(repository)}"
        return HostedRepository(
            provider="GitHub",
            web_url=web_url,
            owner=owner,
            repository=repository,
        )

    if host == "ssh.dev.azure.com":
        if scheme not in {"https", "ssh", "scp"}:
            return None
        if port is not None and port != (443 if scheme == "https" else 22):
            return None
        pieces = _decoded_path(path)
        if pieces is None or len(pieces) != 4 or pieces[0].lower() != "v3":
            return None
        _, organization, project, repository = pieces
    elif host == "dev.azure.com":
        if scheme != "https" or (port is not None and port != 443):
            return None
        pieces = _decoded_path(path)
        if pieces is None or len(pieces) != 4 or pieces[2].lower() != "_git":
            return None
        organization, project, _, repository = pieces
    elif host.endswith(".visualstudio.com"):
        if scheme != "https" or (port is not None and port != 443):
            return None
        organization_raw = host[: -len(".visualstudio.com")]
        if not organization_raw or "." in organization_raw:
            return None
        organization = _component(organization_raw)
        pieces = _decoded_path(path)
        if organization is None or pieces is None:
            return None
        if len(pieces) == 4 and pieces[0].lower() == "defaultcollection":
            _, project, marker, repository = pieces
        elif len(pieces) == 3:
            project, marker, repository = pieces
        else:
            return None
        if marker.lower() != "_git":
            return None
    else:
        return None

    if not organization or not project or not repository:
        return None
    web_url = (
        f"https://dev.azure.com/{_encoded(organization)}/{_encoded(project)}"
        f"/_git/{_encoded(repository)}"
    )
    return HostedRepository(
        provider="Azure DevOps",
        web_url=web_url,
        organization=organization,
        project=project,
        repository=repository,
    )


def _cli_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in ("GH_DEBUG", "AZURE_CORE_DEBUG"):
        env.pop(name, None)
    env.update(
        {
            "GH_PROMPT_DISABLED": "true",
            "GH_PAGER": "cat",
            "PAGER": "cat",
            "AZURE_CORE_DISABLE_CONFIRM_PROMPT": "true",
            "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
            "AZURE_CORE_NO_COLOR": "true",
        }
    )
    return env


def _positive_id(stdout: bytes, field: str) -> int:
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PullRequestError(
            "The service response could not be verified; creation may have succeeded. "
            "Check the repository pull requests before retrying."
        ) from error
    value = response.get(field) if isinstance(response, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PullRequestError(
            "The service response could not be verified; creation may have succeeded. "
            "Check the repository pull requests before retrying."
        )
    return value


def create_pull_request(
    root: Path,
    repository: HostedRepository,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
) -> str:
    """Create one pull request with the provider CLI and return its web URL."""
    clean_title = title.strip()
    if not clean_title:
        raise PullRequestError("A pull request title is required.")
    if not source_branch or not target_branch:
        raise PullRequestError("Source and target branch names are required.")
    if any("\x00" in value for value in (source_branch, target_branch, title, description)):
        raise PullRequestError("Pull request fields must not contain NUL bytes.")

    input_bytes: bytes | None = None
    if repository.provider == "GitHub":
        if not repository.owner or not repository.repository:
            raise PullRequestError("The GitHub repository identity is incomplete.")
        endpoint = f"repos/{_encoded(repository.owner)}/{_encoded(repository.repository)}/pulls"
        argv = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "POST",
            endpoint,
            "--input",
            "-",
        ]
        input_bytes = json.dumps(
            {
                "head": source_branch,
                "base": target_branch,
                "title": clean_title,
                "body": description,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        response_field = "number"
        tool = "GitHub CLI (gh)"
        setup = "Install gh and run 'gh auth login' for github.com."
    elif repository.provider == "Azure DevOps":
        if not repository.organization or not repository.project or not repository.repository:
            raise PullRequestError("The Azure DevOps repository identity is incomplete.")
        organization_url = f"https://dev.azure.com/{_encoded(repository.organization)}"
        argv = [
            "az",
            "repos",
            "pr",
            "create",
            f"--organization={organization_url}",
            f"--project={repository.project}",
            f"--repository={repository.repository}",
            f"--source-branch={source_branch}",
            f"--target-branch={target_branch}",
            f"--title={clean_title}",
            f"--description={description}",
            "--detect=false",
            "--output=json",
            "--only-show-errors",
        ]
        response_field = "pullRequestId"
        tool = "Azure CLI with the azure-devops extension"
        setup = (
            "Install az, run 'az extension add --name azure-devops', then run "
            "'az login' or 'az devops login'."
        )
    else:
        raise PullRequestError(f"Unsupported pull request provider: {repository.provider}")

    try:
        run_options: dict[str, object]
        if input_bytes is None:
            run_options = {"stdin": subprocess.DEVNULL}
        else:
            run_options = {"input": input_bytes}
        result = subprocess.run(
            argv,
            cwd=root,
            env=_cli_environment(),
            capture_output=True,
            timeout=_CLI_TIMEOUT,
            check=False,
            **run_options,
        )
    except FileNotFoundError as error:
        raise PullRequestError(f"{tool} is required. {setup}") from error
    except subprocess.TimeoutExpired as error:
        raise PullRequestError(
            "Pull request creation timed out and may have succeeded. "
            "Check the repository pull requests before retrying."
        ) from error
    except OSError as error:
        raise PullRequestError(
            f"Could not run {tool}. Check its installation and try again."
        ) from error

    if result.returncode != 0:
        raise PullRequestError(
            f"{tool} could not confirm pull request creation. {setup} Check repository access "
            "and branch policies. Because the request may have reached the service, check the "
            "repository pull requests before retrying."
        )

    request_id = _positive_id(result.stdout, response_field)
    if repository.provider == "GitHub":
        return f"{repository.web_url}/pull/{request_id}"
    return f"{repository.web_url}/pullrequest/{request_id}"
