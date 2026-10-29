# Private workspace persistence and diagnostics

## Outcome

Implemented the M1 durable-state seam in `src/rhizome/persistence.py` and the private diagnostics helper in `src/rhizome/diagnostics.py`. No recovery behavior is included.

## Public API

- `WorkspaceStateStore(root: Path | None = None)`
- `load(workspace_key: str) -> dict`
- `save(workspace_key: str, data: dict) -> None`
- `clear(workspace_key: str) -> None`
- `root: Path` and `settings_path: Path`
- Diagnostics remain behind the store seam:
  - `record_diagnostic(event, metadata=None) -> None`
  - `diagnostics_preview() -> list[dict]`
  - `export_diagnostics(destination: Path) -> Path`
- `DEFAULT_SETTINGS` exports the approved remote refresh, offline, theme, graph, glyph, keymap, and protected-branch defaults.

`global` maps to the editable `settings.json`; other validated keys map to isolated `workspaces/<key>.json` documents. Missing global state returns a deep copy of defaults, while missing workspace state returns `{}`. Persisted documents contain `schema_version: 1` and JSON data only.

## Filesystem and privacy behavior

- Root selection order is `RHIZOME_STATE_HOME`, then `XDG_STATE_HOME/rhizome`, then macOS Application Support, otherwise `~/.local/state/rhizome`.
- App-owned directories/files are forced to `0700`/`0600`.
- Writes use a same-directory exclusive temporary file, file `fsync`, atomic `os.replace`, and directory `fsync`.
- Invalid keys, non-JSON values (including NaN, non-string object keys, and cycles), non-regular files, and symlinked path components are rejected.
- Reads validate both schema and payload. Corrupt state raises a source-free error; `save` refuses to overwrite it. `clear` is the explicit discard operation.
- The store never deserializes executable objects or interprets/replays saved operations.

Diagnostics are JSON Lines under the private state root, limited to seven days and 10 MiB. Recording uses an allowlist of ordinary metadata fields and constrained scalar values; path, URL, message, patch/source, credential, and unknown fields are omitted and counted as redacted. Preview/export physically compact expired or over-budget records. Export is explicit, produces a `0600` JSON file, and does not alter permissions on a pre-existing caller-selected directory. There is no network or telemetry path.

## TDD evidence

Red/green slices were exercised against real temporary filesystem directories:

1. Initial reopen test failed at collection because `rhizome.persistence` did not exist; minimal store implementation made it pass.
2. Diagnostic recording test failed because the store had no diagnostic method; the private helper plus store methods made it pass.
3. XDG-on-macOS and cyclic-JSON tests failed, then passed after root precedence and recursion-safe validation changes.
4. Physical diagnostic pruning and export-directory permission-preservation tests failed, then passed after atomic compaction and non-mutating export-parent handling.

Final focused evidence:

- `uv run pytest tests/test_persistence.py tests/test_diagnostics.py -q` — **18 passed**.
- `uv run ruff check src/rhizome/persistence.py src/rhizome/diagnostics.py tests/test_persistence.py tests/test_diagnostics.py` — **passed**.
- `uv run ruff format --check src/rhizome/persistence.py src/rhizome/diagnostics.py tests/test_persistence.py tests/test_diagnostics.py` — **4 files already formatted**.

Tests cover reopen, defaults and root selection, global validation/editing, workspace isolation, scoped/idempotent clear, non-JSON rejection, replace failure preserving prior bytes, corruption refusal and explicit discard, private permissions, symlink rejection, diagnostic redaction/export, age and byte retention, physical pruning, invalid diagnostic inputs, and corrupt diagnostic refusal.

## Limits and integration notes

- No caller integration was added because `app.py`, `git.py`, models, and dependencies were outside this ownership boundary.
- No recovery/checkpoint behavior is implemented here; the separate recovery owner must build on or alongside this store without treating restored state as an instruction to mutate Git.
- Cleanup is activity-triggered: diagnostic access (record, preview, or export) compacts expired records; an application that never accesses its state cannot run time-based cleanup.
- Workspace keys are intentionally filename-safe identifiers of 1–128 characters (`A-Z`, `a-z`, digits, `.`, `_`, `-`) and case variants of reserved `global` are rejected. Callers should derive a stable worktree identity rather than placing repository paths in the key.
- Diagnostics accept only the documented conservative metadata allowlist; richer content requires an explicit privacy review rather than widening this default channel.
