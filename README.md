# GitKraiken

A GitKraken-inspired Git workspace for your **terminal and tmux**, built with Python
and Textual. Local-first, mouse-friendly, keyboard-driven. Works with private
GitHub and Azure DevOps repositories through your existing Git authentication.

![GitKraiken showing a real repository's branches, commit graph and diff](docs/assets/workspace.png)

## Run

For **macOS or Linux (including WSL2)**. Requires **Git 2.31+**, **Python 3.13+**, and
[uv](https://docs.astral.sh/uv/).
A true-color terminal at **120×40 or larger** is recommended; 170 columns gives
all three panels room. No special font is required.

```sh
git clone https://github.com/Doctacon/gitkraiken.git
cd gitkraiken
uv sync
uv run gitkraiken /path/to/repository

# Explore a disposable REAL Git repository with a local-only remote.
# Nothing is cloned from or pushed to an external service.
uv run gitkraiken --demo

# Clone, then open (destination must be new or empty)
uv run gitkraiken --clone git@github.com:ORG/REPO.git ~/work/repo
```

To make `gitkraiken` available from any directory:

```sh
uv tool install .
gitkraiken ~/work/repo
```

To update a checkout-based tool installation, quit any running GitKraiken workspace
with `Ctrl+Q`, then run `git pull --ff-only` and `uv tool install --force --reinstall .`
in this checkout. Reopen it normally (or with your existing tmux hotkey).

`uv run main.py [path]` and `uv run python -m gitkraiken [path]` also work. A path
inside a worktree is enough; GitKraiken discovers its root. Bare repositories are
intentionally rejected.

## The main screen

- **Left:** a searchable reference tree. Collapse Repository, Local, Remotes,
  individual remotes, and branch namespaces such as `feature/jlott` using
  their carets. Tags and Stashes also collapse. Search reveals matching paths;
  clearing it restores your folds. Full names remain available in tooltips.
  **Single-click selects action context without moving or filtering the graph.**
  **Double-click a local/remote branch label to request confirmed checkout.**
- **Center:** a colored commit graph with rounded bends, branch/tag labels,
  date/time, subjects and SHAs. Dates show `YYYY-MM-DD HH:MM` in the author's
  recorded timezone; hover for the full timestamp including seconds and offset.
  Working changes and connector-only rows have no date. Smooth mode draws Git's
  actual parent relationships; Compact mode retains Git's native lane layout.
  Search by message, author, ref or SHA and
  jump between matches **without removing graph connections**. Load history in
  batches of 300, up to 5,000 commits. History stays unified across branch
  selections and checkouts; sidebar search never changes graph topology.
- **Right:** selected commit's complete message, author/date/SHA, changed files,
  and a syntax-colored unified diff. Merge commits are compared to their first
  parent. The **Working directory** tab has separate staged/unstaged entries,
  file/all staging, safe discard, diff preview, and a multiline commit composer.
- **Toolbar / Actions:** fetch, fast-forward-only pull, push/publish branch,
  checkout, create/rename/safe-delete branch, merge, rebase, cherry-pick, revert,
  soft reset, create/delete local tags, stash/apply/pop/drop, and open another repo.
- **Conflicts:** a visible operation banner with Continue/Abort. Resolve files in
  your editor, stage resolutions, then Continue. Git's errors remain visible;
  failed operations refresh the screen too.

Local state refreshes every five seconds while no dialog or operation is active.
Network requests happen **only when you ask**. Large patches are clipped with a
visible notice; binary files have a summary, not a binary-patch dump.

### Branch pointers and navigation

The graph distinguishes **💻 local branch / HEAD**, **🐙 GitHub**, **☁ Azure DevOps**,
**🦊 GitLab**, and **↗ other remote** pointers, plus **◇ tags** and **▣ stashes**.
Both local and remote markers appear on a commit when their tips agree. A local
commit moves only its local pointer; Fetch updates last-fetched remote-tracking
pointers. These are not live server indicators. Hover a badge or inspect the
commit for full reference names. Icon appearance depends on your terminal font.

Creating a branch at an existing commit adds a pointer, not an invented graph
fork. New commits create divergence only when their actual ancestry diverges.
Use **Actions → Locate … in unified graph** to navigate to a selected tip.
If it is not loaded, the caption says so; **Load more** can reveal it explicitly.
Selecting a branch never automatically loads more history.

Double-click a commit row (including its graph node) to request checkout. A branch
at that commit is preferred over detached HEAD, with local branches preferred over
remote-tracking refs. If several branches qualify, choose one before confirming.
For a commit without a branch tip, confirmation explicitly says **detached HEAD**.
Single-click and Enter still only inspect; cancelling leaves the repository unchanged.

Remote checkout uses the exact selected remote ref. An existing same-named local
branch is reused only if its upstream is that exact ref on that remote; a branch tracking another
remote (or no upstream) is not silently substituted.

### Graph styles

Press **g** or click **Smooth [g] / Compact [g]** above the graph to switch:

- **Smooth** (default): rounded Unicode bends, separated lanes, consistent colors
  along each active lane, and more space between commits. Active lanes do not
  shuffle sideways just because a neighboring branch ends. Each commit and its
  outgoing connectors form one selectable row, so arrows / `j` / `k` move directly
  between commits rather than stopping on empty connector lines.
- **Compact**: the original, denser native Git graph with diagonal connectors.
  Useful when you want to see more commits at once.

In Smooth mode, `─╴│╶─` is a crossing: the horizontal line passes **behind** the
vertical lane without joining it; a reminder appears above graphs with crossings.
Rounded forks/joins represent real parent connections. Dotted tails (`┊`) continue
beyond the loaded history, not to an
invented root. Wide graphs scroll horizontally rather than clipping away lanes.

This uses ordinary terminal characters, not image protocols or a special font,
so it works in tmux and Windows Terminal/WSL. It is smoother routing, not a
pixel-rendered Bézier graph. The choice lasts for the running workspace, including
when its tmux popup is detached.

### Controls

| Key | Action |
| --- | --- |
| Click / arrows / `j`, `k` | Select history or files |
| `/` or `f` | Search history |
| Enter in search / **Next** | Next matching commit |
| `g` | Switch Smooth / Compact graph (not tmux prefix + g) |
| Sidebar click / Enter | Select a reference without moving the graph |
| Sidebar double-click | Confirm checkout of a local/remote branch |
| Graph row double-click | Confirm checkout of a branch at that commit, or detached HEAD |
| Sidebar caret / Space | Collapse or expand a section or namespace |
| `Esc` | Clear focused sidebar search; otherwise clear graph search/ref selection; cancel a dialog |
| `Ctrl+P` or `x` | Actions appropriate to the selected ref/commit/stash |
| `Ctrl+O` | Open a local repository |
| `Ctrl+R` or `r` | Refresh |
| `Ctrl+B` | Create a branch |
| `Ctrl+Left`, `Ctrl+Right` | Hide/show refs or inspector for more space |
| `?` | Help |
| `Ctrl+Q` | Quit |

Ordinary letter shortcuts do not steal input while you search or write a commit message.
Folds and sidebar search survive local refreshes; opening another repository resets them.
Consequential actions show a confirmation naming the repository and target.
Staging and committing are explicit button actions. Only staged changes are committed.

## tmux

tmux **3.2+** is optional. The design follows
[apple-pi's current Textual-in-tmux approach](docs/research.md), without requiring
Pi, a plugin, global key bindings, a web server, or changes to `~/.tmux.conf`.

```sh
# Inside tmux: open/reuse this repo's window in the current session.
# Outside tmux: attach a dedicated persistent session.
uv run gitkraiken --tmux ~/work/repo

# Inside tmux: persistent popup over your current work.
uv run gitkraiken --popup ~/work/repo
```

In a popup, use your normal tmux **prefix + d** to detach; reopening resumes the
same workspace. `Ctrl+Q` exits it. Custom tmux sockets are supported.

### Install the `prefix + g` hotkey

After installing with `uv tool install .`, run these **once on the machine where
you will use tmux**:

```sh
# Append a binding for this installation; no changes are made until you redirect it.
"$(uv tool dir --bin)/gitkraiken" --tmux-config >> ~/.tmux.conf

# Reload if tmux is already running; otherwise it loads the binding on next start.
tmux source-file ~/.tmux.conf
```

Use your configured prefix (normally **Ctrl+B**, release, then **g**):

- In a repository pane, **prefix + g** opens/resumes that repo in a popup.
- Inside the popup, **prefix + g** again closes it without losing its state.
- It uses the pane's current directory, including subdirectories of a repo.
- Outside a repository it shows an error on the invoking client's status line.

The generated binding contains the absolute Python path, so it works even when
an existing tmux server cannot find `gitkraiken` or `uv` on its `PATH`. It targets
the invoking client/pane/socket explicitly. It **does override an existing
prefix + g binding** when loaded, but does not change your prefix or other keys.
Remove the generated `bind-key` line and run `tmux unbind-key -T prefix g` to
remove it (then restore any previous binding).

For checkout-based development, `uv run gitkraiken --tmux-config` generates the
same binding using that checkout's `.venv`. Regenerate the binding if you move or
remove that environment. Do not copy a generated macOS binding to WSL: generate
it in WSL after installation.

### WSL2 quick setup (Ubuntu/Debian)

Run inside your **WSL Linux shell**, not PowerShell. With `uv` installed:

```sh
sudo apt update && sudo apt install -y git tmux
mkdir -p ~/work
git clone https://github.com/Doctacon/gitkraiken.git ~/work/gitkraiken
cd ~/work/gitkraiken
uv tool install --python 3.13 .         # uv supplies Python if necessary
"$(uv tool dir --bin)/gitkraiken" --tmux-config >> ~/.tmux.conf

tmux -V                                # must be 3.2 or newer
# If tmux is already running:
tmux source-file ~/.tmux.conf
# Otherwise start it:
tmux
```

Then `cd` into a repository and press **Ctrl+B, then g**. Prefer keeping checkouts
under WSL's Linux filesystem (for example `~/work`) rather than `/mnt/c` for Git
performance. Do not copy a macOS/Windows `.venv`; install fresh in WSL. Windows
Terminal's WSL profile works well. Authenticate with Git **inside WSL** first;
Windows credentials and SSH agents are not automatically the same as WSL's.

## Private GitHub and Azure DevOps

There is no GitKraiken login, token database, GitHub API token requirement, or
subscription. **If command-line Git can authenticate, this app uses the same
credential helper, SSH configuration and agent.** Use SSH keys or a system
credential manager—not a PAT embedded in a URL.

```sh
# GitHub, including private repositories
gitkraiken --clone git@github.com:ORG/REPO.git ~/work/github-repo

# Azure DevOps SSH
gitkraiken --clone git@ssh.dev.azure.com:v3/ORG/PROJECT/REPO ~/work/azure-repo

# Azure DevOps HTTPS, with your configured Git Credential Manager
gitkraiken --clone https://dev.azure.com/ORG/PROJECT/_git/REPO ~/work/azure-repo
```

Authenticate **once in a normal terminal** first. For GitHub, `gh auth login`
followed by `gh auth setup-git` configures HTTPS authentication if you use the
GitHub CLI. For Azure, use
[Git Credential Manager](https://github.com/git-ecosystem/git-credential-manager)
or [Azure's SSH setup](https://learn.microsoft.com/azure/devops/repos/git/use-ssh-keys).
For GitHub Enterprise or Azure Server, use your organization's regular clone URL;
provider labeling may be generic, but Git transport is not host-restricted.

If authentication fails:

1. Run `git ls-remote <remote-url>` in a normal terminal and complete login or SSH
   host-key acceptance there.
2. Unlock/load the key in your SSH agent as necessary.
3. Retry Fetch/Pull/Push in the app.

Git runs without terminal password prompts, askpass dialogs or interactive SSH
login; network commands time out after 60 seconds. Custom `GIT_SSH_COMMAND`/`GIT_SSH`
wrappers must accept OpenSSH's `-oBatchMode=yes`; shell fragments in custom commands
are normalized rather than executed as arbitrary shell source. Fetch/push URLs
are redacted in the interface. Git itself owns its credential storage/configuration.

## Safety and deliberate limits

- Git is invoked with argument arrays, never interpolated shell commands. File
  paths use literal pathspecs; repository-selection environment variables are
  isolated so operations apply to the repository you opened.
- No force push, hard reset, `git clean`, remote deletion, implicit network activity,
  or automatic stashing. Pull refuses divergence. Safe branch deletion refuses
  unmerged branches. Discard only affects explicitly selected tracked worktree
  changes and preserves the index; untracked/intent-to-add content is protected.
- Push sends only the current branch with an explicit non-force refspec, not Git's
  potentially broad configured push defaults. Its confirmation identifies the
  push destination. Multiple configured push URLs are refused.
- Confirmations cannot be carried over to a different repository. Git operations
  are serialized; changing selection/refresh cannot cancel a running mutation.
- This is a **terminal adaptation of the main repository screen**, not an official
  GitKraken product or a pixel-identical clone. No proprietary assets were copied.
  It intentionally omits hosted PR/issue browsing, OAuth/account management,
  arbitrary undo/redo, drag-and-drop rebase, interactive rebase editing, hunk-level
  staging, a graphical conflict editor, and submodule/LFS-specific management.
  Use Git or your editor for those workflows. Git hooks/signing configured by the
  repository still run during commits and can reject or time out an operation.

## Development and checks

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python scripts/tmux_smoke.py  # isolated server; leaves your tmux sessions alone
uv run python scripts/popup_smoke.py # real checkout, live popup/PID, detach/reopen
uv run python scripts/popup_smoke.py --hotkey # generated config + actual prefix+g presses
uv build
```

Tests use disposable repositories and local bare remotes; they never write to a
real private repository. Textual pilot tests exercise actual staging/committing,
selection, double-click checkout, fold/search persistence, unified graph geometry,
local/remote pointers, diffs, dialogs, branch creation, refresh and narrow layouts.
Checkout and notification regressions enable toast rendering explicitly; Textual
otherwise disables notifications in tests. Popup smoke checks exercise real Git
checkouts and verify that the application process and popup stay alive.
Authentication against your organization's private remotes must still be verified
with your credentials; none were requested or accessed during development.

For visual inspection:

```sh
uv run python scripts/capture_demo.py /tmp/workspace.svg --width 170 --height 48
# Add --compact to compare the original denser graph.
# Exercise nested local/remote folders and sidebar-only search:
uv run python scripts/capture_demo.py /tmp/refs.svg --many-refs --ref-filter jlott
# Add --collapse-remotes to preview a folded Remotes section instead.
# Optional: rsvg-convert -o /tmp/workspace.png /tmp/workspace.svg
```

Implementation seams and source research are in [`docs/implementation.md`](docs/implementation.md)
and [`docs/research.md`](docs/research.md).
