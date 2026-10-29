# Rhizome

A local-first Git workspace for your terminal. Browse without changing your
checkout, review mixed edits, stage exact hunks or line ranges, and commit with a
mouse or keyboard. **Runs standalone; tmux is optional.** Git uses your existing
authentication.

![Rhizome's branches, commit graph, and changed files](docs/assets/workspace.png)

## Development preview

This checkout is implementing the [approved roadmap](docs/design/world-class-rhizome.md).
It is **not a published or release-certified upgrade**. Precision-core integration
is in progress; later investigation, integration, and hosted-review milestones are
not yet complete. **Ordinary commit submission is temporarily unavailable** while
a newly reproduced reviewed-index race is being fixed. Inspection, precise staging
and durable composition remain available; earlier commit-flow tests are not proof
of the current gated candidate. The screenshot above predates the precision workspace.

Run an inspected development checkout with Python 3.13+, Git 2.36+, and
[uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked
uv run --locked rhizome /path/to/repository
# Or explore a disposable real Git repository with a local-only remote:
uv run --locked rhizome --demo
```

The target is macOS, Linux, and WSL2, including SSH/tmux, at **80×24 or larger**.
Wide terminals show the three-panel workspace; narrow terminals summon one primary
task panel. Platform, minimum-version, real-terminal, and human usability validation
must not be inferred from the local headless tests.

## Precision workflow

1. **Files / F2** opens working changes. Activate a file to inspect its captured diff.
2. Press **v**, extend a contiguous range with arrows, then **s** to stage or **u**
   to unstage. Mouse/Shift-click ranges and hunk controls are also available.
3. Search, expand context, or show whitespace without changing the canonical patch.
4. **Commit / F3** opens the durable composer. Submission is currently disabled
   pending reviewed-index protection. **Back / Esc** restores the prior view.

Browsing and double-clicking never check out a branch. Use an explicit **Checkout**
action. If a reviewed file changes externally, the captured review remains visible
and becomes stale: refresh and select again before applying it.

**Ctrl+P** opens actions, **Ctrl+L / Activity** shows queued/running/completed Git
work, **?** shows help, and **Ctrl+Q** quits. Closing Activity does not cancel Git.

## Safety and network behavior

Unproven destructive/integration workflows are intentionally unavailable—not routed
around their guards. This currently includes discard, resets, rewriting, stash
mutations, pull, deletion, external-patch application, and Continue/Abort. Use an
explicit external Git/editor workflow for those tasks and refresh afterward;
Rhizome does not promise recovery for external actions.

Fetch validates configured destinations before updating remote-tracking caches;
unsafe mappings, tag pruning, and resolving user-ref aliases into affected caches
are refused. Native non-force tag auto-follow and explicit `--no-tags` are honored.
Optional automatic fetch and hosted metadata refresh use a configurable
**60-second** interval and pause for inactive terminals or user work. Local polling
remains separate from network requests. Offline mode disables network actions;
non-force push remains deliberate. No force-push or telemetry is added.

## Installation and integration

The inspectable bootstrap accepts **only a full Git commit**, provisions a private
pinned uv/Python environment, and provides explicit update, rollback, and uninstall
commands. It never edits shell, Git, or tmux configuration. **Live installation
validation is still pending, and this uncommitted preview has no published installer
revision.** See [installation and ownership boundaries](docs/installation.md).

Optional tmux integration: `rhizome --tmux /path/to/repository` with tmux 3.2+.
The [tmux guide](docs/usage.md#tmux) explains the opt-in persistent pane hotkey.

[Usage](docs/usage.md) · [Pull requests](docs/pull-requests.md) ·
[Development and checks](docs/usage.md#development-and-checks) ·
[Validation plan](docs/design/validation-plan.md)

An independent terminal adaptation, not an official GitKraken product.
