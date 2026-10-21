"""System clipboard support, with Textual's terminal clipboard as a fallback."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App


class CopyResult(Enum):
    SYSTEM = "Copied to system clipboard."
    TERMINAL = (
        "System clipboard unavailable. Sent an OSC 52 copy request to the terminal; "
        "paste to verify. If nothing pastes, enable OSC 52 or copy manually."
    )


def _clipboard_commands() -> list[tuple[list[str], str]]:
    # Over SSH the desired clipboard is on the client, not this machine (even
    # when the remote host has a display or clipboard helper available).
    if any(os.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return []
    if sys.platform == "darwin":
        return [(["pbcopy"], "utf-8")]
    if sys.platform == "win32":
        return [(["clip.exe"], "utf-16le")]
    commands = []
    if sys.platform.startswith("linux"):
        if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
            commands.append((["clip.exe"], "utf-16le"))
        if os.environ.get("WAYLAND_DISPLAY"):
            commands.append((["wl-copy"], "utf-8"))
        if os.environ.get("DISPLAY"):
            commands.extend(
                [
                    (["xclip", "-selection", "clipboard"], "utf-8"),
                    (["xsel", "--clipboard", "--input"], "utf-8"),
                ]
            )
    return commands


def _copy_native(text: str) -> bool:
    for command, encoding in _clipboard_commands():
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            result = subprocess.run(
                [executable, *command[1:]],
                input=text.encode(encoding),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False


async def copy_text(app: App, text: str) -> CopyResult:
    """Copy without blocking the UI; only native success can be confirmed.

    Textual's method also maintains the in-app paste buffer used by Input and
    TextArea, so keep it in sync even when a native helper is available. Its OSC
    52 request is best-effort: unsupported terminals may silently ignore it.
    """
    app.copy_to_clipboard(text)
    if await asyncio.to_thread(_copy_native, text):
        return CopyResult.SYSTEM
    return CopyResult.TERMINAL
