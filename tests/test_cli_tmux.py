from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gitkraiken.cli import main, parser
from gitkraiken.tmux import (
    TmuxError,
    app_command,
    config,
    hotkey,
    hotkey_main,
    launch,
    workspace_id,
)


def result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_cli_defaults_and_modes():
    args = parser().parse_args([])
    assert args.path == "."
    assert not args.tmux
    args = parser().parse_args(["--popup", "/tmp/my project"])
    assert args.popup
    assert args.path == "/tmp/my project"


def test_version(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0
    assert "gitkraiken 0.1.0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--clone", "https://example.test/repo"],
        ["--demo", "--popup"],
        ["--tmux-config", "--demo"],
        ["--tmux-config", "/repo"],
        ["--tmux-config", "--clone", "https://example.test/repo"],
    ],
)
def test_cli_invalid_combinations(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_tmux_command_quotes_shell_metacharacters(tmp_path):
    root = tmp_path / "a repo's $(touch NEVER) ; folder"
    words = shlex.split(app_command(root))
    assert words == ["exec", sys.executable, "-m", "gitkraiken", str(root.resolve())]
    assert len(workspace_id(root)) == 16
    assert workspace_id(root) == workspace_id(root / ".")


def test_existing_window_is_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX", "/fake/socket,1,0")
    key = workspace_id(tmp_path)
    with (
        patch("gitkraiken.tmux.shutil.which", return_value="/bin/tmux"),
        patch(
            "gitkraiken.tmux._run", side_effect=[result(f"@2\tother\n@4\t{key}\n"), result()]
        ) as run,
    ):
        launch(tmp_path)
    assert run.call_args_list[1].args == ("select-window", "-t", "@4")
    assert run.call_count == 2


def test_new_window_sets_only_its_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX", "/fake/socket,1,0")
    with (
        patch("gitkraiken.tmux.shutil.which", return_value="/bin/tmux"),
        patch(
            "gitkraiken.tmux._run", side_effect=[result("@2\t\n"), result("@7\n"), result()]
        ) as run,
    ):
        launch(tmp_path)
    assert run.call_args_list[1].args[0] == "new-window"
    assert run.call_args_list[2].args == (
        "set-option",
        "-w",
        "-t",
        "@7",
        "@gitkraiken_id",
        workspace_id(tmp_path),
    )
    assert all("-g" not in call.args for call in run.call_args_list)


def test_popup_requires_tmux(monkeypatch, tmp_path):
    monkeypatch.delenv("TMUX", raising=False)
    with (
        patch("gitkraiken.tmux.shutil.which", return_value="/bin/tmux"),
        pytest.raises(TmuxError, match="existing tmux client"),
    ):
        launch(tmp_path, popup=True)


def test_missing_tmux_message(tmp_path):
    with (
        patch("gitkraiken.tmux.shutil.which", return_value=None),
        pytest.raises(TmuxError, match="not installed"),
    ):
        launch(tmp_path)


def test_popup_reuses_session_and_unsets_tmux(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX", "/fake/socket,1,0")
    with (
        patch("gitkraiken.tmux.shutil.which", return_value="/bin/tmux"),
        patch(
            "gitkraiken.tmux._run",
            side_effect=[result(), result(), result("/tmp/alternate tmux/socket\n")],
        ) as run,
        patch("gitkraiken.tmux.subprocess.run", return_value=result()) as foreground,
    ):
        launch(tmp_path, popup=True)
    assert run.call_count == 3  # existing session + popup marker + socket discovery
    command = foreground.call_args.args[0]
    assert command[:4] == ["tmux", "-S", "/tmp/alternate tmux/socket", "display-popup"]
    assert shlex.split(command[-1])[:6] == [
        "env",
        "-u",
        "TMUX",
        "tmux",
        "-S",
        "/tmp/alternate tmux/socket",
    ]
    # zsh expands an unquoted '=name' as a command path before running tmux.
    assert shlex.split(command[-1])[-1] == f"gitkraiken-{workspace_id(tmp_path)}"


def test_config_is_printed_without_repository_or_config_mutation(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with patch("gitkraiken.git.Repository", side_effect=AssertionError("should not open repo")):
        main(["--tmux-config"])
    output = capsys.readouterr().out
    assert "bind-key -T prefix g run-shell -b" in output
    assert sys.executable in output
    assert "gitkraiken.tmux" in output
    assert "#{q:pane_id}" in output
    assert "#{q:client_name}" in output
    assert "#{q:socket_path}" in output
    assert "pane_current_path" not in output  # no filesystem data in shell source
    assert not list(tmp_path.iterdir())


def test_config_quotes_tmux_parser_and_literal_hashes(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/a repo's/$HOME/#{client_name}/python")
    output = config()
    assert "\\$HOME" in output
    assert "##{client_name}/python" in output
    assert output.count("bind-key -T prefix") == 1
    monkeypatch.setattr(sys, "executable", "/bad\npath/python")
    with pytest.raises(TmuxError, match="control characters"):
        config()


def test_popup_can_target_explicit_client_without_inherited_tmux(tmp_path, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    with (
        patch("gitkraiken.tmux.shutil.which", return_value="/bin/tmux"),
        patch("gitkraiken.tmux._run", return_value=result()) as run,
        patch("gitkraiken.tmux.subprocess.run", return_value=result()) as foreground,
    ):
        launch(tmp_path, popup=True, socket="/tmp/server", client="/dev/pts/42")
    assert all(call.kwargs["socket"] == "/tmp/server" for call in run.call_args_list)
    command = foreground.call_args.args[0]
    assert command[:6] == ["tmux", "-S", "/tmp/server", "display-popup", "-c", "/dev/pts/42"]


def test_hotkey_uses_exact_pane_directory_and_client(tmp_path):
    root = tmp_path / "repo's $(not-a-command)"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    with (
        patch("gitkraiken.tmux._run", side_effect=[result("\n"), result(str(root) + "\n")]) as run,
        patch("gitkraiken.tmux.launch") as show,
    ):
        hotkey(socket="/tmp/socket", client="/dev/pts/42", pane="%7")
    assert run.call_args_list[1].args == (
        "display-message",
        "-p",
        "-t",
        "%7",
        "#{pane_current_path}",
    )
    show.assert_called_once_with(
        root.resolve(), popup=True, client="/dev/pts/42", socket="/tmp/socket"
    )


def test_hotkey_inside_popup_detaches_without_opening_another():
    with (
        patch("gitkraiken.tmux._run", side_effect=[result("1\n"), result()]) as run,
        patch("gitkraiken.tmux.launch") as show,
    ):
        hotkey(socket="/tmp/socket", client="/dev/pts/42", pane="%7")
    show.assert_not_called()
    assert run.call_args_list[-1].args == ("detach-client", "-t", "/dev/pts/42")


def test_hotkey_errors_are_shown_on_originating_client(monkeypatch):
    from gitkraiken.git import GitError

    monkeypatch.setattr(
        sys,
        "argv",
        ["hotkey", "--socket", "/tmp/server", "--client", "/dev/pts/42", "--pane", "%7"],
    )
    with (
        patch("gitkraiken.tmux.hotkey", side_effect=GitError("Not a repository: #{client_name}")),
        patch("gitkraiken.tmux._run", return_value=result()) as run,
    ):
        hotkey_main()
    assert run.call_args.args[:5] == ("display-message", "-c", "/dev/pts/42", "-d", "5000")
    assert "##{client_name}" in run.call_args.args[-1]
    assert run.call_args.kwargs["socket"] == "/tmp/server"


def test_demo_uses_only_local_upstream_and_has_real_history(tmp_path):
    from gitkraiken.demo import create_demo

    root = create_demo(tmp_path / "demo")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    assert git("branch", "--show-current") == "feature/workspace"
    assert "feature/azure-auth" in git("branch", "--list")
    assert int(git("rev-list", "--all", "--count")) >= 14
    assert Path(git("remote", "get-url", "origin")).is_dir()
    assert git("stash", "list")
    assert "README.md" in git("status", "--porcelain")
    assert "src/workspace.py" in git("diff", "--cached", "--name-only")
    with pytest.raises(ValueError, match="must be empty"):
        create_demo(tmp_path / "demo")
