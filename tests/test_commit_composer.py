"""Acceptance coverage for native commit-message semantics and composer templates."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from test_app import settle
from textual.widgets import Static, TextArea

from rhizome.app import RhizomeApp
from rhizome.git import GitError, Repository
from rhizome.models import Action

_TEMPLATE_LIMIT = 1024 * 1024


def _run_bytes(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_bytes,
        capture_output=True,
        check=check,
        env=env,
    )


def _stage(root: Path, name: str = "reviewed.txt", content: bytes = b"reviewed\n") -> None:
    (root / name).write_bytes(content)
    _run_bytes(root, "add", "--", name)


def _init_repository(root: Path) -> None:
    root.mkdir()
    _run_bytes(root, "init", "-b", "main")
    for key, value in (
        ("user.name", "Test User"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        _run_bytes(root, "config", key, value)


def _raw_commit(root: Path) -> bytes:
    return _run_bytes(root, "cat-file", "commit", "HEAD").stdout


def _raw_message(root: Path) -> bytes:
    headers, separator, message = _raw_commit(root).partition(b"\n\n")
    assert separator and headers.startswith(b"tree ")
    return message


def _native_editor_commit(root: Path, message: bytes) -> None:
    env = os.environ.copy()
    env["GIT_EDITOR"] = "true"
    result = _run_bytes(
        root,
        "commit",
        "--edit",
        "--no-status",
        "--no-verbose",
        "-F",
        "-",
        input_bytes=message,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


def _configure(root: Path, *, cleanup: str | None = None, comment: str | None = None) -> None:
    if cleanup is not None:
        _run_bytes(root, "config", "commit.cleanup", cleanup)
    if comment is not None:
        _run_bytes(root, "config", "core.commentChar", comment)


def _product_and_native_messages(
    git_repo: Path,
    tmp_path: Path,
    message: bytes,
    *,
    cleanup: str | None = None,
    comment: str | None = None,
    presentation_config: bool = False,
) -> tuple[bytes, bytes]:
    _stage(git_repo)
    _configure(git_repo, cleanup=cleanup, comment=comment)
    if presentation_config:
        _run_bytes(git_repo, "config", "commit.status", "true")
        _run_bytes(git_repo, "config", "commit.verbose", "true")

    native = tmp_path / "native"
    _init_repository(native)
    _stage(native)
    _configure(native, cleanup=cleanup, comment=comment)
    if presentation_config:
        _run_bytes(native, "config", "commit.status", "true")
        _run_bytes(native, "config", "commit.verbose", "true")
    _native_editor_commit(native, message)

    Repository(git_repo).execute(Action("commit", message=message.decode("utf-8")))
    return _raw_message(git_repo), _raw_message(native)


@pytest.mark.parametrize(
    ("case", "cleanup", "comment", "message", "expected"),
    [
        (
            "default",
            None,
            None,
            b"  Subject  \n\nBody with spaces  \n# remove this\n\n",
            b"  Subject\n\nBody with spaces\n",
        ),
        (
            "strip",
            "strip",
            None,
            b"  Subject  \n\nBody with spaces  \n# remove this\n\n",
            b"  Subject\n\nBody with spaces\n",
        ),
        (
            "whitespace",
            "whitespace",
            None,
            b"  Subject  \n\nBody with spaces  \n# keep this\n\n",
            b"  Subject\n\nBody with spaces\n# keep this\n",
        ),
        (
            "verbatim",
            "verbatim",
            None,
            b"Subject with spaces  \n\nBody with spaces  \nlast-without-newline",
            b"Subject with spaces  \n\nBody with spaces  \nlast-without-newline",
        ),
        (
            "scissors",
            "scissors",
            None,
            b"Subject\n\nkeep\n# ------------------------ >8 ------------------------\ndrop\n",
            b"Subject\n\nkeep\n",
        ),
        (
            "nondefault-comment-character",
            "strip",
            ";",
            b"Subject\n; remove this\n# keep this\n",
            b"Subject\n# keep this\n",
        ),
    ],
    ids=("default", "strip", "whitespace", "verbatim", "scissors", "comment-character"),
)
def test_engine_cleanup_modes_match_native_editor_oracle(
    git_repo: Path,
    tmp_path: Path,
    case: str,
    cleanup: str | None,
    comment: str | None,
    message: bytes,
    expected: bytes,
) -> None:
    product, native = _product_and_native_messages(
        git_repo, tmp_path, message, cleanup=cleanup, comment=comment
    )
    assert product == native == expected, case


@pytest.mark.parametrize(
    "message",
    [
        b"Multiline subject  \n\nBody line with trailing spaces  \nno-final-newline",
        b"\nLeading blank line\n\nBody\n\n",
    ],
    ids=("trailing-whitespace-no-final-newline", "leading-and-trailing-blank-lines"),
)
def test_engine_verbatim_preserves_exact_raw_message_bytes(
    git_repo: Path, tmp_path: Path, message: bytes
) -> None:
    product, native = _product_and_native_messages(
        git_repo,
        tmp_path,
        message,
        cleanup="verbatim",
        presentation_config=True,
    )
    assert native == message
    assert product == message


def test_engine_prepare_message_source_rewrite_and_commit_message_rejection(
    git_repo: Path,
) -> None:
    _stage(git_repo, content=b"first\n")
    _configure(git_repo, cleanup="verbatim")
    hooks = git_repo / ".git" / "hooks"
    prepare = hooks / "prepare-commit-msg"
    prepare.write_text(
        "#!/bin/sh\n"
        "printf '%s' \"$2\" > prepare-source.txt\n"
        "printf 'Rewritten by prepare-commit-msg\\n\\nHook body  ' > \"$1\"\n"
    )
    prepare.chmod(0o755)

    Repository(git_repo).execute(Action("commit", message="Original composer text"))
    assert (git_repo / "prepare-source.txt").read_text() == "message"
    assert _raw_message(git_repo) == b"Rewritten by prepare-commit-msg\n\nHook body  "

    _stage(git_repo, "second.txt", b"second\n")
    before_head = _run_bytes(git_repo, "rev-parse", "HEAD").stdout
    before_tree = _run_bytes(git_repo, "write-tree").stdout
    reject = hooks / "commit-msg"
    reject.write_text("#!/bin/sh\nprintf 'commit-msg fixture rejected\\n' >&2\nexit 1\n")
    reject.chmod(0o755)

    with pytest.raises(GitError, match="commit-msg fixture rejected"):
        Repository(git_repo).execute(Action("commit", message="Must remain uncommitted"))

    assert _run_bytes(git_repo, "rev-parse", "HEAD").stdout == before_head
    assert _run_bytes(git_repo, "write-tree").stdout == before_tree
    assert _run_bytes(git_repo, "diff", "--cached", "--name-only").stdout == b"second.txt\n"


def test_engine_iso8859_1_header_and_message_bytes(git_repo: Path) -> None:
    _stage(git_repo)
    _configure(git_repo, cleanup="verbatim")
    _run_bytes(git_repo, "config", "i18n.commitEncoding", "ISO8859-1")
    message = "Café\n\nRésumé without final newline"

    Repository(git_repo).execute(Action("commit", message=message))

    raw = _raw_commit(git_repo)
    headers, _, body = raw.partition(b"\n\n")
    assert b"\nencoding ISO8859-1\n" in b"\n" + headers + b"\n"
    assert body == message.encode("iso8859-1")


def test_engine_unrepresentable_message_refuses_before_hooks_or_index_mutation(
    git_repo: Path,
) -> None:
    _stage(git_repo)
    _run_bytes(git_repo, "config", "i18n.commitEncoding", "ISO8859-1")
    marker = git_repo / "hook-ran"
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf ran > hook-ran\n")
    hook.chmod(0o755)
    index = git_repo / ".git" / "index"
    before_index = index.read_bytes()

    with pytest.raises(GitError, match="cannot be encoded as ISO8859-1"):
        Repository(git_repo).execute(Action("commit", message="Not representable: €"))

    assert not marker.exists()
    assert index.read_bytes() == before_index
    assert _run_bytes(git_repo, "rev-parse", "--verify", "HEAD", check=False).returncode != 0
    assert not (git_repo / ".git" / "index.lock").exists()
    assert not list((git_repo / ".git").glob("rhizome-commit-*"))


def _template_status(app: RhizomeApp) -> str:
    renderable = app.query_one("#draft-status", Static).renderable
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


def _configure_template(root: Path, value: str) -> None:
    _run_bytes(root, "config", "commit.template", value)


def _native_template_capture(root: Path, tmp_path: Path, expected: bytes) -> None:
    capture = tmp_path / "native-template-capture"
    editor = tmp_path / "capture-editor.sh"
    editor.write_text('#!/bin/sh\ncp "$1" "$CAPTURE"\nexit 1\n')
    editor.chmod(0o755)
    env = os.environ.copy()
    env.update({"GIT_EDITOR": str(editor), "CAPTURE": str(capture)})
    result = _run_bytes(root, "commit", check=False, env=env)
    assert result.returncode != 0
    assert capture.read_bytes().startswith(expected)


@pytest.mark.parametrize("path_kind", ("relative", "absolute", "home", "symlink"))
async def test_ui_configured_template_paths_match_native_git(
    git_repo: Path,
    tmp_path: Path,
    path_kind: str,
) -> None:
    content = f"{path_kind.title()} template\n\nEdit this body.\n".encode()
    if path_kind == "relative":
        template = git_repo / "templates" / "commit.txt"
        template.parent.mkdir()
        configured = "templates/commit.txt"
    elif path_kind == "absolute":
        template = tmp_path / "absolute-template.txt"
        configured = str(template)
    elif path_kind == "home":
        template = Path(os.environ["HOME"]) / "home-template.txt"
        configured = "~/home-template.txt"
    else:
        target = tmp_path / "template-target.txt"
        target.write_bytes(content)
        template = tmp_path / "template-link.txt"
        template.symlink_to(target)
        configured = str(template)
    if path_kind != "symlink":
        template.write_bytes(content)
    _configure_template(git_repo, configured)
    _stage(git_repo)
    _native_template_capture(git_repo, tmp_path, content)

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(130, 42)) as pilot:
        await settle(app, pilot)
        assert app.query_one("#commit-message", TextArea).text == content.decode()
        assert _template_status(app) == "Template loaded · edit before committing"


async def test_ui_nonempty_saved_draft_precedes_missing_template(git_repo: Path) -> None:
    draft = "Saved draft wins\n\nEven when the configured template disappears."
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(130, 42)) as pilot:
        await settle(app, pilot)
        await pilot.press("f3")
        app.query_one("#commit-message", TextArea).load_text(draft)
        await pilot.pause()

    _configure_template(git_repo, "missing-template.txt")
    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(130, 42)) as pilot:
        await settle(reopened, pilot)
        assert reopened.query_one("#commit-message", TextArea).text == draft
        assert _template_status(reopened) == "Draft restored locally"


async def test_ui_untouched_template_is_refused_and_not_persisted(
    git_repo: Path, tmp_path: Path
) -> None:
    template = tmp_path / "untouched-template.txt"
    seed = "Template seed\n\nReplace these instructions.\n"
    template.write_text(seed)
    _configure_template(git_repo, str(template))
    _stage(git_repo)

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(130, 42)) as pilot:
        await settle(app, pilot)
        assert app.query_one("#commit-message", TextArea).text == seed
        await pilot.press("f3")
        await pilot.click("#commit")
        await pilot.pause()
        assert not app.busy
        assert _run_bytes(git_repo, "rev-parse", "--verify", "HEAD", check=False).returncode != 0
        assert app.query_one("#commit-message", TextArea).text == seed

    template.unlink()
    reopened = RhizomeApp(git_repo)
    async with reopened.run_test(size=(130, 42)) as pilot:
        await settle(reopened, pilot)
        assert reopened.query_one("#commit-message", TextArea).text == ""
        assert "missing" in _template_status(reopened).lower()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("missing", "missing"),
        ("nul", "nul"),
        ("directory", "regular file"),
        ("fifo", "regular file"),
        ("oversized", "1 mib limit"),
        ("invalid-encoding", "not valid utf-8"),
        ("unsupported-codec", "unsupported"),
        ("mixed-newlines", "line endings"),
    ],
)
async def test_ui_template_failures_are_bounded_and_do_not_hang(
    git_repo: Path,
    tmp_path: Path,
    failure: str,
    expected: str,
) -> None:
    template = tmp_path / f"template-{failure}"
    if failure == "missing":
        pass
    elif failure == "nul":
        template.write_bytes(b"Subject\0body")
    elif failure == "directory":
        template.mkdir()
    elif failure == "fifo":
        os.mkfifo(template)
    elif failure == "oversized":
        with template.open("wb") as stream:
            stream.truncate(_TEMPLATE_LIMIT + 1)
    elif failure == "invalid-encoding":
        template.write_bytes(b"Subject\n\xff\n")
    elif failure == "unsupported-codec":
        template.write_text("Subject\n")
        _run_bytes(git_repo, "config", "i18n.commitEncoding", "fixture-no-such-codec")
    elif failure == "mixed-newlines":
        template.write_bytes(b"Subject\r\n\r\nBody\n")
    _configure_template(git_repo, str(template))

    started = time.monotonic()
    app = RhizomeApp(git_repo)
    async with app.run_test(size=(130, 42)) as pilot:
        await settle(app, pilot)
        elapsed = time.monotonic() - started
        status = _template_status(app)
        assert app.query_one("#commit-message", TextArea).text == ""
        assert expected in status.lower()
        assert len(status) < 300
        assert elapsed < 10


async def test_ui_successive_commits_reseed_the_configured_template(
    git_repo: Path, tmp_path: Path
) -> None:
    template = tmp_path / "success-template.txt"
    seed = "Template subject\n\nTemplate body\n"
    template.write_text(seed)
    _configure_template(git_repo, str(template))
    _stage(git_repo, content=b"first\n")

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(130, 42)) as pilot:
        await settle(app, pilot)
        editor = app.query_one("#commit-message", TextArea)
        assert editor.text == seed
        editor.load_text("First successful commit")
        await pilot.click("#commit")
        await settle(app, pilot)
        assert editor.text == seed
        assert _template_status(app) == "Template loaded · edit before committing"

        (git_repo / "reviewed.txt").write_text("second\n")
        _run_bytes(git_repo, "add", "--", "reviewed.txt")
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        editor.load_text("Second successful commit")
        await pilot.click("#commit")
        await settle(app, pilot)
        assert editor.text == seed
        assert _template_status(app) == "Template loaded · edit before committing"

    subjects = _run_bytes(git_repo, "log", "-2", "--format=%s").stdout.splitlines()
    assert subjects == [b"Second successful commit", b"First successful commit"]


async def test_ui_failed_commit_retains_edited_draft_and_template_seed(
    git_repo: Path, tmp_path: Path
) -> None:
    template = tmp_path / "failure-template.txt"
    seed = "Template seed\n\nEdit before committing.\n"
    template.write_text(seed)
    _configure_template(git_repo, str(template))
    _stage(git_repo)
    hook = git_repo / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\nprintf 'composer rejection fixture\\n' >&2\nexit 1\n")
    hook.chmod(0o755)
    edited = "Edited draft\n\nKeep this after failure."

    app = RhizomeApp(git_repo)
    async with app.run_test(size=(130, 42)) as pilot:
        await settle(app, pilot)
        editor = app.query_one("#commit-message", TextArea)
        assert editor.text == seed
        editor.load_text(edited)
        await pilot.click("#commit")
        await settle(app, pilot)
        assert editor.text == edited
        assert (
            "composer rejection fixture" in app.query_one("#status-line", Static).renderable.plain
        )
        assert _run_bytes(git_repo, "rev-parse", "--verify", "HEAD", check=False).returncode != 0

        hook.unlink()
        editor.load_text(seed)
        await pilot.click("#commit")
        await pilot.pause()
        assert not app.busy
        assert editor.text == seed
        assert _run_bytes(git_repo, "rev-parse", "--verify", "HEAD", check=False).returncode != 0
