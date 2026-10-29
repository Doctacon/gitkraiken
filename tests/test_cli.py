from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from rhizome.cli import main

COMMIT = "1234567890abcdef1234567890abcdef12345678"
TAG_OBJECT = "abcdef1234567890abcdef1234567890abcdef12"


class Response:
    def __init__(self, payload: dict[str, object]):
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self._data


@pytest.mark.parametrize(
    "arguments",
    [
        ["--check-updates", "/repo"],
        ["--check-updates", "--demo"],
        ["--check-updates", "--clone", "https://example.test/repo", "/repo"],
        ["--check-updates", "--tmux"],
    ],
)
def test_update_check_rejects_process_and_repository_modes(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_other_cli_modes_make_no_update_request(capsys):
    with (
        patch("rhizome.distribution.urlopen") as request,
        pytest.raises(SystemExit) as error,
    ):
        main(["--version"])
    assert error.value.code == 0
    assert "rhizome 0.1.0" in capsys.readouterr().out
    request.assert_not_called()


def test_explicit_update_check_resolves_annotated_tag_to_commit(capsys):
    release = {
        "tag_name": "v1.2.3",
        "name": "Rhizome 1.2.3",
        "html_url": "https://github.com/Doctacon/rhizome/releases/tag/v1.2.3",
        "published_at": "2026-10-28T08:53:00Z",
        "target_commitish": "main",
    }
    tag_ref = {"object": {"type": "tag", "sha": TAG_OBJECT}}
    annotated_tag = {"object": {"type": "commit", "sha": COMMIT}}

    with patch(
        "rhizome.distribution.urlopen",
        side_effect=[Response(release), Response(tag_ref), Response(annotated_tag)],
    ) as request:
        main(["--check-updates"])

    output = capsys.readouterr().out
    assert "Rhizome 1.2.3" in output
    assert "v1.2.3" in output
    assert "2026-10-28T08:53:00Z" in output
    assert release["html_url"] in output
    assert COMMIT in output
    assert "raw.githubusercontent.com/Doctacon/rhizome/" + COMMIT in output
    assert "curl -q --fail --location --proto '=https' --tlsv1.2" in output
    assert "--revision " + COMMIT in output
    assert "main" not in output
    assert request.call_count == 3


def test_missing_public_release_is_an_honest_success(capsys):
    missing = HTTPError(
        "https://api.github.com/repos/Doctacon/rhizome/releases/latest",
        404,
        "Not Found",
        {},
        io.BytesIO(),
    )
    with patch("rhizome.distribution.urlopen", side_effect=missing):
        main(["--check-updates"])

    output = capsys.readouterr().out
    assert "No published Rhizome release was found" in output
    assert "ready" not in output.lower()
    assert "--revision" not in output


def test_update_check_network_failure_is_clear(capsys):
    with (
        patch("rhizome.distribution.urlopen", side_effect=OSError("network offline")),
        pytest.raises(SystemExit) as error,
    ):
        main(["--check-updates"])

    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert "update check failed" in stderr
    assert "network offline" in stderr
