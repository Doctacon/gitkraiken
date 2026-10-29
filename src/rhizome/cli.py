"""Command line entrypoint. Interactive work stays in Textual, not in Git prompts."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import __version__


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="rhizome",
        description="A GitKraken-inspired Git workspace for your terminal and tmux.",
        epilog="Private remotes use your existing Git credential helper or SSH agent.",
    )
    result.add_argument("path", nargs="?", default=".", help="repository or a directory inside it")
    result.add_argument("--version", action="version", version=f"rhizome {__version__}")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument(
        "--tmux", action="store_true", help="open/reuse a tmux window (session outside tmux)"
    )
    mode.add_argument("--popup", action="store_true", help="open/reuse a persistent tmux popup")
    mode.add_argument(
        "--tmux-config",
        action="store_true",
        help="print a prefix+g binding for this installation (does not edit tmux.conf)",
    )
    mode.add_argument(
        "--check-updates",
        action="store_true",
        help="query GitHub for the latest public release (does not install or restart)",
    )
    result.add_argument(
        "--demo", action="store_true", help="explore a disposable real Git repository"
    )
    result.add_argument(
        "--clone", metavar="URL", help="clone URL into PATH, then open the workspace"
    )
    return result


def main(argv: list[str] | None = None) -> None:
    arg_parser = parser()
    args = arg_parser.parse_args(argv)
    if args.tmux_config and (args.demo or args.clone or args.path != "."):
        arg_parser.error("--tmux-config cannot be combined with a repository, --clone, or --demo")
    if args.check_updates and (args.demo or args.clone or args.path != "."):
        arg_parser.error(
            "--check-updates is a standalone network check; do not combine it with "
            "a repository, --clone, or --demo"
        )
    if args.demo and (args.clone or args.tmux or args.popup):
        arg_parser.error(
            "--demo runs in this terminal; do not combine it with --clone/--tmux/--popup"
        )
    if args.clone and args.path == ".":
        arg_parser.error("specify a new destination: rhizome --clone URL ./repository")
    if args.check_updates:
        from .distribution import UpdateCheckError, print_update_check

        try:
            print_update_check(__version__)
        except UpdateCheckError as error:
            print(f"rhizome: update check failed: {error}", file=sys.stderr)
            raise SystemExit(1) from None
        return

    from .git import GitError, Repository
    from .tmux import TmuxError, config, launch

    try:
        if args.tmux_config:
            print(config(), end="")
            return
        if args.demo:
            from .app import RhizomeApp
            from .demo import create_demo

            with tempfile.TemporaryDirectory(prefix="rhizome-demo-") as directory:
                root = create_demo(Path(directory))
                app = RhizomeApp(root)
                app.title = "Rhizome · DEMO (disposable local repository)"
                app.run()
            return
        if args.clone:
            print("Cloning with your configured Git credentials…", file=sys.stderr)
            root = Repository.clone(args.clone, str(Path(args.path).expanduser()))
        else:
            root = Repository(Path(args.path).expanduser()).root
        if args.tmux or args.popup:
            launch(root, popup=args.popup)
        else:
            from .app import RhizomeApp

            RhizomeApp(root).run()
    except (GitError, TmuxError, OSError, ValueError) as error:
        print(f"rhizome: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
