# Pinned installation

[Usage](usage.md) · [README](../README.md)

## M1 validation status

M1 is [accepted for a full release](validation/m1-release-acceptance.md). Actual
pinned downloads and install/update/rollback/uninstall pass in disposable homes
on macOS ARM64, Linux ARM64 and Linux AMD64 under emulation, including paths with
spaces and preservation of unrelated state/configuration. Candidate CI also passes.
This does not certify every platform, proxy, interruption or terminal combination;
the acceptance record lists explicit validation deferrals. Acceptance itself does
not create a tag or GitHub release.

The distribution contract is: an inspected full Git commit, a private managed
runtime, explicit updates, rollback and uninstall, and no automatic changes to
shell profiles, PATH, Git settings, or tmux settings. Native Windows is not a
target; run inside WSL2 instead.

## Inspect, then install a published revision

Once a reviewed revision containing the bootstrap and both constraint manifests
exists, set `REV` to its **full lowercase 40-character commit ID**. Moving branches,
short SHAs and `latest` are not accepted. Do not paste a placeholder as a revision.

```sh
REV=FULL_REVIEWED_COMMIT_ID
curl -q --fail --location --proto '=https' --tlsv1.2 \
  "https://raw.githubusercontent.com/Doctacon/rhizome/$REV/scripts/install.sh" \
  --output rhizome-install.sh
less rhizome-install.sh
sh rhizome-install.sh --revision "$REV"
```

Inspect the constraint manifests from that same commit as part of reviewing it:

- `scripts/install-runtime-constraints.txt`: production dependencies from `uv.lock`.
- `scripts/install-build-constraints.txt`: the isolated Python build dependency closure.

The current bootstrap selects a platform-specific, SHA-256-checked **uv 0.11.7**
archive and requests managed **CPython 3.13.13**. It pins the application commit and
runtime/build wheel URLs and hashes. Git 2.36+ remains an external prerequisite;
tmux 3.2+ is optional. Python and uv need not be preinstalled for this path. Use a
maintained Git version rather than treating the minimum as a security recommendation.

Inherited `UV_*` configuration is refused, rather than silently accepting an
alternate index, mirror, authentication provider or insecure-host setting. Review
and remove such overrides for the installer subprocess. Standard proxy/CA settings
are retained. Installation's private authentication/configuration environment is
separate from normal Rhizome operation, which uses your Git and provider CLI setup.

## Locations and ownership

The default application installation is:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/rhizome/
  bin/rhizome                 stable launcher
  store/<commit>/             revision-specific runtime, tool, and cache payload
  revisions/<commit>/         completed revision records
  current, previous           launcher selection records
  transactions/               interrupted-operation records
```

`RHIZOME_INSTALL_ROOT` overrides the application root. Use an absolute normalized
path dedicated to Rhizome—not an existing shared tools directory. The script prints
planned locations and a direct launcher command; it does not add anything to PATH
or use elevated installation. The bootstrap's uv is private implementation storage,
not a public wrapper for modifying a selected revision.

Private application state is **separate from the installation**:

1. `RHIZOME_STATE_HOME`, when explicitly set;
2. `$XDG_STATE_HOME/rhizome`, when set;
3. otherwise `~/Library/Application Support/Rhizome` on macOS, or
   `~/.local/state/rhizome` on Linux/WSL.

Drafts can contain source and PR text. Keep that location private, outside the
checkout and outside the installation root. The installer checks physical
state/root separation. Runtime state storage also rejects physical locations inside
a marked installation, including symlink aliases and later override changes. Do not
place unrelated data inside an application-owned installation directory.

## Explicit updates and rollback

`rhizome --check-updates` explicitly queries public GitHub release metadata. It
prints information; it does not install, select a version, or restart an app.
No release means no suggested release installation. Normal workspace startup does
not perform an application update check.

For an update, inspect the new commit's bootstrap and constraints, then rerun it
with `--revision NEW_FULL_COMMIT`. Quit/reopen running workspaces deliberately;
changing the launcher does not replace an already-running Python process.

```sh
sh rhizome-install.sh --rollback
```

Rollback selects the prior completed revision. It does not undo Git changes,
restore repository content, or change application state. Interrupted installation
and rollback handling have isolated subprocess-test coverage; successful real
lifecycles do not establish every live failure/interruption scenario. Install,
rollback, and uninstall share one lock beside the root in its canonical parent,
including first-root publication. An
interruption that leaves unverifiable lock ownership fails closed for manual
inspection; never blindly delete a lock while an installer might still be running.
If a guarded command dies with a signal-style exit status (greater than 128), its
children may survive. The installer retains its activity marker, lock and staging
files, and refuses subsequent install/rollback/uninstall attempts. This fence does
not automatically expire when a child exits: manual inspection is required.
Intentional high exit codes receive the same conservative treatment. Ordinary
command failures retain the existing cleanup and retry behavior.

Generated tmux bindings contain an absolute Python path. A managed revision changes
that path: review/regenerate your opt-in binding after an update or rollback.
The installer never edits it for you.

## Uninstall

```sh
sh rhizome-install.sh --uninstall
```

Use the same `RHIZOME_INSTALL_ROOT` override, if any. The script checks application
ownership and directory shape before removing the installation. The separate state
location is retained; uninstall is not a draft/recovery purge. Remove an optional
shell alias or tmux binding yourself after reviewing it. Do not delete retained
state unless you deliberately intend to remove its drafts and recovery content.

## Development checkout

An inspected checkout can run without installing a GitHub release:

```sh
uv sync --locked
uv run --locked rhizome /path/to/repository
uv run --locked rhizome --demo
```

This uses the checkout's lockfile and environment, not the managed bootstrap's
release/rollback mechanism. Development dependencies and source edits are not a
certified published application artifact.
