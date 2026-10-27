import base64
import subprocess
import threading
from types import SimpleNamespace

import pytest
from textual.app import App

from rhizome.clipboard import CopyResult, copy_text

TEXT = "https://example.com/café?ref=feature/$literal&view=1\nSecond line"


@pytest.fixture
def clipboard_runtime(monkeypatch):
    """Simulate clipboard tools without using a real display or clipboard."""
    for name in (
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
        "WSL_DISTRO_NAME",
        "WSL_INTEROP",
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "TMUX",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: f"/tools/{name}")
    ui_thread = threading.get_ident()
    calls = []

    def run(argv, **kwargs):
        assert threading.get_ident() != ui_thread, "Clipboard tools must not block the UI"
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    ("platform", "environment", "command", "encoding"),
    [
        ("darwin", {}, ["pbcopy"], "utf-8"),
        ("darwin", {"TMUX": "/tmp/tmux/local,1,0"}, ["pbcopy"], "utf-8"),
        ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, ["wl-copy"], "utf-8"),
        (
            "linux",
            {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
            ["wl-copy"],
            "utf-8",
        ),
        ("linux", {"DISPLAY": ":0"}, ["xclip", "-selection", "clipboard"], "utf-8"),
        ("linux", {"WSL_DISTRO_NAME": "Ubuntu"}, ["clip.exe"], "utf-16le"),
        (
            "linux",
            {"WSL_INTEROP": "/run/WSL/1_interop", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
            ["clip.exe"],
            "utf-16le",
        ),
        ("win32", {}, ["clip.exe"], "utf-16le"),
    ],
)
async def test_native_clipboard_uses_stdin_and_correct_selection_and_encoding(
    clipboard_runtime, monkeypatch, platform, environment, command, encoding
):
    monkeypatch.setattr("sys.platform", platform)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    app = App()

    assert await copy_text(app, TEXT) is CopyResult.SYSTEM

    assert app.clipboard == TEXT  # Textual's own paste buffer stays in sync.
    assert clipboard_runtime == [
        (
            [f"/tools/{command[0]}", *command[1:]],
            {
                "input": TEXT.encode(encoding),
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "timeout": 2,
                "check": False,
            },
        )
    ]


async def test_xsel_is_used_when_xclip_is_missing(clipboard_runtime, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda name: "/tools/xsel" if name == "xsel" else None)

    assert await copy_text(App(), TEXT) is CopyResult.SYSTEM
    assert clipboard_runtime[0][0] == ["/tools/xsel", "--clipboard", "--input"]


async def test_failed_wayland_helper_tries_x11_helpers(clipboard_runtime, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    attempted = []

    def run(argv, **kwargs):
        attempted.append(argv[0])
        return subprocess.CompletedProcess(argv, 0 if argv[0] == "/tools/xsel" else 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert await copy_text(App(), TEXT) is CopyResult.SYSTEM
    assert attempted == ["/tools/wl-copy", "/tools/xclip", "/tools/xsel"]


@pytest.mark.parametrize(
    "failure",
    [
        1,
        FileNotFoundError("Helper disappeared"),
        PermissionError("Access denied"),
        subprocess.TimeoutExpired(["pbcopy"], 2),
        subprocess.CalledProcessError(1, ["pbcopy"]),
    ],
)
async def test_native_failure_preserves_terminal_fallback(clipboard_runtime, monkeypatch, failure):
    monkeypatch.setattr("sys.platform", "darwin")

    def run(argv, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return subprocess.CompletedProcess(argv, failure)

    monkeypatch.setattr(subprocess, "run", run)
    app = App()
    terminal_writes = []
    app._driver = SimpleNamespace(write=terminal_writes.append)

    assert await copy_text(app, TEXT) is CopyResult.TERMINAL
    assert app.clipboard == TEXT
    encoded = base64.b64encode(TEXT.encode("utf-8")).decode("ascii")
    assert terminal_writes == [f"\x1b]52;c;{encoded}\a"]
    assert "paste to verify" in CopyResult.TERMINAL.value
    assert "copied" not in CopyResult.TERMINAL.value.lower()


async def test_missing_native_helper_is_not_reported_as_success(clipboard_runtime, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("shutil.which", lambda name: None)
    app = App()
    assert await copy_text(app, TEXT) is CopyResult.TERMINAL
    assert app.clipboard == TEXT
    assert not clipboard_runtime


@pytest.mark.parametrize("ssh_variable", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
@pytest.mark.parametrize("platform", ["darwin", "linux"])
async def test_ssh_does_not_copy_to_remote_host_clipboard(
    clipboard_runtime, monkeypatch, ssh_variable, platform
):
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setenv(ssh_variable, "remote-session")
    monkeypatch.setenv("DISPLAY", "localhost:10.0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert await copy_text(App(), TEXT) is CopyResult.TERMINAL
    assert not clipboard_runtime


@pytest.mark.parametrize("platform", ["linux", "unknown"])
async def test_no_display_uses_terminal_clipboard(clipboard_runtime, monkeypatch, platform):
    monkeypatch.setattr("sys.platform", platform)
    assert await copy_text(App(), TEXT) is CopyResult.TERMINAL
    assert not clipboard_runtime
