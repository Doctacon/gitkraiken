"""Explicit public release checks for the Rhizome command line."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_API_ROOT = "https://api.github.com/repos/Doctacon/rhizome"
_SOURCE_ROOT = "https://github.com/Doctacon/rhizome"
_MAX_RESPONSE_BYTES = 1_000_000
_SHA = re.compile(r"[0-9a-f]{40}")


class UpdateCheckError(Exception):
    """A public release response could not be retrieved or safely interpreted."""


@dataclass(frozen=True)
class Release:
    name: str
    tag: str
    published_at: str
    url: str
    commit: str


def _line(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UpdateCheckError(f"GitHub release metadata has no valid {field}.")
    cleaned = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise UpdateCheckError(f"GitHub release metadata has an unsafe {field}.")
    return cleaned


def _json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "rhizome-update-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed HTTPS API URLs
            encoded = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError:
        raise
    except (OSError, URLError) as error:
        raise UpdateCheckError(str(error)) from error
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise UpdateCheckError("GitHub release metadata response is unexpectedly large.")
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateCheckError("GitHub returned malformed release metadata.") from error
    if not isinstance(value, dict):
        raise UpdateCheckError("GitHub returned malformed release metadata.")
    return value


def _object(payload: dict[str, Any]) -> tuple[str, str]:
    value = payload.get("object")
    if not isinstance(value, dict):
        raise UpdateCheckError("GitHub tag metadata has no object.")
    kind = value.get("type")
    sha = value.get("sha")
    if kind not in {"commit", "tag"} or not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise UpdateCheckError("GitHub tag metadata has no immutable commit target.")
    return kind, sha


def _tag_commit(tag: str) -> str:
    try:
        payload = _json(f"{_API_ROOT}/git/ref/tags/{quote(tag, safe='')}")
        kind, sha = _object(payload)
        for _ in range(8):
            if kind == "commit":
                return sha
            kind, sha = _object(_json(f"{_API_ROOT}/git/tags/{sha}"))
    except HTTPError as error:
        raise UpdateCheckError(f"GitHub returned HTTP {error.code} while resolving the tag.") from error
    raise UpdateCheckError("GitHub tag metadata is nested too deeply to resolve safely.")


def latest_release() -> Release | None:
    """Fetch the latest published release and resolve its tag to an immutable commit."""

    try:
        payload = _json(f"{_API_ROOT}/releases/latest")
    except HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateCheckError(f"GitHub returned HTTP {error.code}.") from error
    tag = _line(payload.get("tag_name"), "tag")
    name_value = payload.get("name")
    name = _line(name_value, "name") if name_value else tag
    published_at = _line(payload.get("published_at"), "publication date")
    url = _line(payload.get("html_url"), "release URL")
    if not url.startswith(f"{_SOURCE_ROOT}/releases/"):
        raise UpdateCheckError("GitHub release metadata has an unexpected release URL.")
    return Release(name, tag, published_at, url, _tag_commit(tag))


def print_update_check(current_version: str) -> None:
    """Print release information without installing or changing the running process."""

    release = latest_release()
    if release is None:
        print("No published Rhizome release was found on GitHub.")
        print(f"Installed version: {current_version}")
        return
    script_url = (
        "https://raw.githubusercontent.com/Doctacon/rhizome/"
        f"{release.commit}/scripts/install.sh"
    )
    print(f"Release: {release.name} ({release.tag})")
    print(f"Published: {release.published_at}")
    print(f"Details: {release.url}")
    print(f"Immutable commit: {release.commit}")
    print(f"Installed version: {current_version}")
    print("Pinned install command (inspect the downloaded script before running it):")
    print(
        "curl -q --fail --location --proto '=https' --tlsv1.2 "
        f"--output rhizome-install.sh {script_url}"
    )
    print(f"sh rhizome-install.sh --revision {release.commit}")
    print("This check did not install, restart, or modify the running application.")
