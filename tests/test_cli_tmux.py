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
    launch_pane,
    restore_pane,
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
            "gitkraiken.tmux._run",
            side_effect=[result(f"@2\tother\n@4\t{key}\n"), result(), result()],
        ) as run,
    ):
        launch(tmp_path, socket="/tmp/socket")
    assert [call.args for call in run.call_args_list[1:]] == [
        ("select-window", "-t", "@4"),
        ("send-keys", "-t", "@4", "-H", "1b", "5b", "49"),
    ]
    assert run.call_args_list[2].kwargs["check"] is False
    assert all(call.kwargs["socket"] == "/tmp/socket" for call in run.call_args_list)


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
    assert not any(call.args[0] == "send-keys" for call in run.call_args_list)


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
            side_effect=[result(), result(), result(), result("/tmp/alternate tmux/socket\n")],
        ) as run,
        patch("gitkraiken.tmux.subprocess.run", return_value=result()) as foreground,
    ):
        launch(tmp_path, popup=True)
    session = f"gitkraiken-{workspace_id(tmp_path)}"
    assert [call.args for call in run.call_args_list] == [
        ("has-session", "-t", f"={session}"),
        ("set-option", "-t", session, "@gitkraiken_popup", "1"),
        ("send-keys", "-t", f"={session}:", "-H", "1b", "5b", "49"),
        ("display-message", "-p", "#{socket_path}"),
    ]
    assert run.call_args_list[2].kwargs["check"] is False
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
    assert shlex.split(command[-1])[-1] == session


def test_fresh_popup_does_not_signal_the_new_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX", "/fake/socket,1,0")
    with (
        patch("gitkraiken.tmux.shutil.which", return_value="/bin/tmux"),
        patch(
            "gitkraiken.tmux._run",
            side_effect=[result(returncode=1), result(), result(), result("/tmp/socket\n")],
        ) as run,
        patch("gitkraiken.tmux.subprocess.run", return_value=result()),
    ):
        launch(tmp_path, popup=True)
    assert not any(call.args[0] == "send-keys" for call in run.call_args_list)


def test_popup_focus_notification_failure_does_not_report_open_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX", "/fake/socket,1,0")

    def fake_run(*args, **kwargs):
        if args[0] == "send-keys":
            raise TmuxError("notification failed")
        if args[0] == "display-message":
            return result("/tmp/socket\n")
        return result()

    with (
        patch("gitkraiken.tmux.shutil.which", return_value="/bin/tmux"),
        patch("gitkraiken.tmux._run", side_effect=fake_run),
        patch("gitkraiken.tmux.subprocess.run", return_value=result()) as foreground,
    ):
        launch(tmp_path, popup=True)
    foreground.assert_called_once()


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
    session = f"gitkraiken-{workspace_id(tmp_path)}"
    assert ("send-keys", "-t", f"={session}:", "-H", "1b", "5b", "49") in [
        call.args for call in run.call_args_list
    ]
    command = foreground.call_args.args[0]
    assert command[:6] == ["tmux", "-S", "/tmp/server", "display-popup", "-c", "/dev/pts/42"]


def test_hotkey_uses_exact_pane_directory_and_client(tmp_path):
    root = tmp_path / "repo's $(not-a-command)"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    with (
        patch("gitkraiken.tmux._run", side_effect=[result("\n"), result(str(root) + "\n")]) as run,
        patch("gitkraiken.tmux.launch_pane") as show,
    ):
        hotkey(socket="/tmp/socket", client="/dev/pts/42", pane="%7")
    assert run.call_args_list[1].args == (
        "display-message",
        "-p",
        "-t",
        "%7",
        "#{pane_current_path}",
    )
    show.assert_called_once_with(root.resolve(), pane="%7", socket="/tmp/socket")


def test_hotkey_inside_popup_detaches_without_opening_another():
    with (
        patch("gitkraiken.tmux._run", side_effect=[result("1\n"), result()]) as run,
        patch("gitkraiken.tmux.launch") as show,
    ):
        hotkey(socket="/tmp/socket", client="/dev/pts/42", pane="%7")
    show.assert_not_called()
    assert run.call_args_list[-1].args == ("detach-client", "-t", "/dev/pts/42")


def test_hotkey_inside_pane_restores_origin_without_touching_processes():
    with (
        patch(
            "gitkraiken.tmux._run", side_effect=[result("\t%7\n"), result(), result(), result()]
        ) as run,
        patch("gitkraiken.tmux.launch_pane") as show,
    ):
        hotkey(socket="/tmp/socket", client="/dev/pts/42", pane="%9")
    show.assert_not_called()
    assert [call.args for call in run.call_args_list[1:]] == [
        ("wait-for", "-L", "gitkraiken-pane-%7"),
        ("swap-pane", "-d", "-Z", "-s", "%7", "-t", "%9"),
        ("wait-for", "-U", "gitkraiken-pane-%7"),
    ]
    assert all(call.kwargs["socket"] == "/tmp/socket" for call in run.call_args_list)


def test_pane_launch_reuses_parked_app(tmp_path):
    with patch(
        "gitkraiken.tmux._run",
        side_effect=[result(), result("%9\n"), result(), result(), result()],
    ) as run:
        launch_pane(tmp_path, pane="%7", socket="/tmp/socket")
    commands = [call.args for call in run.call_args_list]
    assert commands[1] == (
        "display-message",
        "-p",
        "-t",
        f"=gitkraiken-pane-7-{workspace_id(tmp_path)}:",
        "#{@gitkraiken_app}",
    )
    assert commands[2:4] == [
        ("swap-pane", "-d", "-Z", "-s", "%9", "-t", "%7"),
        ("send-keys", "-t", "%9", "-H", "1b", "5b", "49"),
    ]
    assert run.call_args_list[3].kwargs == {"socket": "/tmp/socket", "check": False}
    assert commands[4] == ("wait-for", "-U", "gitkraiken-pane-%7")
    assert not any(
        command[0] in {"new-session", "respawn-pane", "display-popup"} for command in commands
    )


def test_pane_focus_notification_failure_still_unlocks(tmp_path):
    def fake_run(*args, **kwargs):
        if args[0] == "display-message":
            return result("%9\n")
        if args[0] == "send-keys":
            raise TmuxError("notification failed")
        return result()

    with patch("gitkraiken.tmux._run", side_effect=fake_run) as run:
        launch_pane(tmp_path, pane="%7", socket="/tmp/socket")
    assert run.call_args_list[-1].args == ("wait-for", "-U", "gitkraiken-pane-%7")


def test_new_pane_is_guarded_before_start_and_swap(tmp_path):
    def fake_run(*args, **kwargs):
        if args[0] == "new-session":
            return result("$2\t%9\n")
        if args[-1] == "#{pane_width}\t#{pane_height}":
            return result("80\t24\n")
        return result()

    with patch("gitkraiken.tmux._run", side_effect=fake_run) as run:
        launch_pane(tmp_path, pane="%7", socket="/tmp/socket")
    commands = [call.args for call in run.call_args_list]
    assert ("set-option", "-p", "-t", "%9", "@gitkraiken_origin", "%7") in commands
    assert ("set-option", "-p", "-t", "%9", "@gitkraiken_parking", "$2") in commands
    guard = commands.index(("set-option", "-p", "-t", "%9", "remain-on-exit", "on"))
    hook = next(i for i, command in enumerate(commands) if command[0] == "set-hook")
    spawn = next(i for i, command in enumerate(commands) if command[0] == "respawn-pane")
    swap = next(i for i, command in enumerate(commands) if command[0] == "swap-pane")
    assert guard < hook < spawn < swap
    assert "--restore" in commands[hook][-1]
    assert commands[spawn][-1] == app_command(tmp_path)
    assert commands[swap] == ("swap-pane", "-d", "-Z", "-s", "%9", "-t", "%7")
    assert all(call.kwargs["socket"] == "/tmp/socket" for call in run.call_args_list)
    assert all("-g" not in command for command in commands)
    assert not any(command[0] == "send-keys" for command in commands)


@pytest.mark.parametrize("failure", ["respawn-pane", "swap-pane"])
def test_pane_setup_failure_never_kills_the_origin(tmp_path, failure):
    def fake_run(*args, **kwargs):
        if args[0] == "new-session":
            return result("$2\t%9\n")
        if args[-1] == "#{pane_width}\t#{pane_height}":
            return result("80\t24\n")
        if args[0] == failure:
            raise TmuxError("failed")
        return result()

    with patch("gitkraiken.tmux._run", side_effect=fake_run) as run:
        with pytest.raises(TmuxError, match="failed"):
            launch_pane(tmp_path, pane="%7", socket="/tmp/socket")
    commands = [call.args for call in run.call_args_list]
    kills = [command for command in commands if command[0].startswith("kill-")]
    # If a swap timed out, it may already have completed. Leave both processes
    # intact instead of removing the app slot with the user's origin still parked.
    assert kills == ([("kill-pane", "-t", "%9")] if failure == "respawn-pane" else [])
    assert commands[-1] == ("wait-for", "-U", "gitkraiken-pane-%7")


@pytest.mark.parametrize(
    ("app_session", "origin_session", "swap"),
    [("$0", "$2", True), ("$2", "$0", False), ("$0", "", False)],
)
def test_exit_restores_only_a_parked_origin(app_session, origin_session, swap):
    def fake_run(*args, **kwargs):
        if args[0] != "display-message":
            return result()
        target, field = args[3:]
        values = {
            ("%9", "#{@gitkraiken_origin}"): "%7",
            ("%9", "#{@gitkraiken_parking}"): "$2",
            ("%9", "#{session_id}"): app_session,
            ("%9", "#{pane_dead}"): "1",
            ("%7", "#{session_id}"): origin_session,
        }
        return result(values[target, field] + "\n")

    with patch("gitkraiken.tmux._run", side_effect=fake_run) as run:
        restore_pane(pane="%9", socket="/tmp/socket")
    commands = [call.args for call in run.call_args_list]
    swaps = [command for command in commands if command[0] == "swap-pane"]
    assert swaps == ([("swap-pane", "-d", "-Z", "-s", "%7", "-t", "%9")] if swap else [])
    assert [command for command in commands if command[0].startswith("kill-")] == [
        ("kill-pane", "-t", "%9"),
    ]
    assert not any(command[0] == "send-keys" for command in commands)
    assert commands[-1] == ("wait-for", "-U", "gitkraiken-pane-%7")


def test_delayed_exit_hook_does_not_kill_a_respawned_live_pane():
    with patch(
        "gitkraiken.tmux._run",
        side_effect=[result("%7"), result(), result("$2"), result("$0"), result("0"), result()],
    ) as run:
        restore_pane(pane="%9", socket="/tmp/socket")
    commands = [call.args for call in run.call_args_list]
    assert not any(command[0] in {"kill-pane", "swap-pane"} for command in commands)
    assert commands[-1] == ("wait-for", "-U", "gitkraiken-pane-%7")


def test_duplicate_exit_cleanup_is_noop():
    with patch("gitkraiken.tmux._run", return_value=result()) as run:
        restore_pane(pane="%9", socket="/tmp/socket")
    assert run.call_count == 1


def test_exit_hook_entrypoint_does_not_require_a_client(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["hotkey", "--socket", "/tmp/socket", "--pane", "%9", "--restore"],
    )
    with patch("gitkraiken.tmux.restore_pane") as restore:
        hotkey_main()
    restore.assert_called_once_with(pane="%9", socket="/tmp/socket")


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
