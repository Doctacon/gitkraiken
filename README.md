# GitKraiken

A GitKraken-inspired Git workspace for your terminal. Browse branches and commits,
review diffs, stage files, and commit with a mouse or keyboard.
**Runs standalone; tmux is optional.** Uses your existing Git authentication.

![GitKraiken's branches, commit graph, and changed files](docs/assets/workspace.png)

## Install & run

macOS or Linux (including WSL2). Requires **Git 2.36+** and
[uv](https://docs.astral.sh/uv/); uv can install the required Python 3.13.

```sh
uv tool install --python 3.13 git+https://github.com/Doctacon/gitkraiken.git
gitkraiken /path/to/repository
```

Run `gitkraiken` inside a repository, or try `gitkraiken --demo` for a disposable
local demo. A true-color terminal at **120×40 or larger** is recommended.

## Use

- Select a commit to see its details and changed files on the right.
- Click a file to show its diff in the center; **Esc** or **Back to graph** returns.
- Use **Working directory** to stage files and commit. WIP appears in the graph only
  when you have changes.
- **Ctrl+P** opens actions, **/** searches, **?** shows help, **Ctrl+Q** quits.
- Optional tmux integration: `gitkraiken --tmux /path/to/repository`.
  See the [tmux guide](docs/usage.md#tmux) for the persistent pane hotkey.

Network operations are explicit. Pull is fast-forward-only; push never force-pushes
history. Selected Git actions run immediately, so take care with discard and reset.

[Full usage & setup](docs/usage.md) · [Pull requests](docs/pull-requests.md) ·
[Development & checks](docs/usage.md#development-and-checks)

An independent terminal adaptation, not an official GitKraken product.
