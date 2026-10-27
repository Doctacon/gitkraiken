"""A disposable, real repository for exploring the UI without touching your work."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path


def create_demo(destination: Path) -> Path:
    """Create a small branched history and local-only origin in an EMPTY directory."""
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("The demo destination must be empty.")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination / "tidal"
    root.mkdir()
    counter = 0
    people = [
        ("Alex Morgan", "alex@example.test"),
        ("Sam Rivera", "sam@example.test"),
        ("Jamie Chen", "jamie@example.test"),
    ]

    def git(*args: str, env: dict[str, str] | None = None) -> str:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"Demo setup failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def commit(message: str, filename: str, content: str, author: int = 0) -> None:
        nonlocal counter
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git("add", "--", filename)
        name, email = people[author]
        date = (datetime.now(UTC) - timedelta(days=3) + timedelta(hours=counter * 3)).isoformat()
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME=name,
            GIT_AUTHOR_EMAIL=email,
            GIT_COMMITTER_NAME=name,
            GIT_COMMITTER_EMAIL=email,
            GIT_AUTHOR_DATE=date,
            GIT_COMMITTER_DATE=date,
        )
        git("commit", "-m", message, env=env)
        counter += 1

    git("init", "-b", "main")
    git("config", "user.name", "Demo Developer")
    git("config", "user.email", "demo@example.test")
    git("config", "commit.gpgsign", "false")
    git("config", "tag.gpgsign", "false")
    git("config", "core.hooksPath", "/dev/null")
    commit("Initial commit", "README.md", "# Tidal\n\nA calm place for your code.\n")
    commit(
        "Scaffold the application shell",
        "src/app.py",
        'TITLE = "Tidal"\n\ndef main():\n    print(TITLE)\n',
    )
    commit(
        "Add the ocean color palette",
        "src/theme.py",
        'BACKGROUND = "#191d28"\nACCENT = "#62d6c5"\n',
        1,
    )
    git("tag", "v0.1.0")
    git("switch", "-c", "feature/azure-auth")
    commit(
        "Use the system credential helper for Azure",
        "src/auth.py",
        'PROVIDERS = ["github", "azure"]\n',
        2,
    )
    commit(
        "Document SSH and credential-manager setup",
        "docs/auth.md",
        "# Private repositories\n\nCredentials stay in your system keychain.\n",
        2,
    )
    git("switch", "main")
    commit(
        "Build the repository navigation tree",
        "src/branches.py",
        'SECTIONS = ["Local", "Remotes", "Tags", "Stashes"]\n',
        0,
    )
    git("switch", "-c", "feature/commit-graph")
    commit(
        "Draw colored commit graph lanes",
        "src/graph.py",
        'LANES = ["teal", "mauve", "blue", "yellow"]\n',
        1,
    )
    commit(
        "Keep merge connectors aligned while scrolling",
        "tests/test_graph.py",
        "def test_graph():\n    assert True\n",
        1,
    )
    git("switch", "main")
    git("merge", "--no-ff", "feature/azure-auth", "-m", "Merge branch 'feature/azure-auth'")
    git("merge", "--no-ff", "feature/commit-graph", "-m", "Merge branch 'feature/commit-graph'")
    git("tag", "v0.2.0")
    git("switch", "-c", "fix/empty-repository")
    commit(
        "Handle a repository with no commits yet",
        "src/empty.py",
        'EMPTY_MESSAGE = "Your story starts here."\n',
        2,
    )
    git("switch", "main")
    commit(
        "Add keyboard navigation and command menu",
        "src/keys.py",
        'KEYS = {"ctrl+p": "commands", "ctrl+r": "refresh"}\n',
    )
    git("switch", "-c", "feature/workspace")
    commit(
        "Add the working directory panel",
        "src/workspace.py",
        'PANELS = ["Unstaged", "Staged", "Commit"]\n',
        1,
    )
    commit("Show a diff before staging a file", "src/diff.py", "CONTEXT_LINES = 3\n", 0)
    # Local-only upstream: demo Fetch/Pull/Push cannot contact an external host.
    upstream = destination / "origin.git"
    git("init", "--bare", str(upstream))
    git("remote", "add", "origin", str(upstream))
    git("push", "--all", "origin")
    git("push", "--tags", "origin")
    git("branch", "--set-upstream-to=origin/feature/workspace")
    (root / "src/theme.py").write_text('BACKGROUND = "#171b24"\nACCENT = "#62d6c5"\n')
    git("stash", "push", "-m", "Experiment with a quieter background")
    (root / "src/workspace.py").write_text(
        'PANELS = ["Unstaged", "Staged", "Commit"]\nSHOW_DIFF = True\n'
    )
    git("add", "src/workspace.py")
    (root / "README.md").write_text(
        "# Tidal\n\nA calm place for your code.\n\n## Getting started\n\nOpen a repository and make yourself at home.\n"
    )
    (root / "notes.md").write_text(
        "# Next up\n\n- Review the staging experience\n- Ship something good\n"
    )
    return root
