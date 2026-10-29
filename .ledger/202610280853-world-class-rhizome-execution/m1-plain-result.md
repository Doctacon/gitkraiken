Implemented bounded M1 plain-glyph fallback.

### Changed
- `src/rhizome/graph.py`
  - Added `render_history(..., *, plain_glyphs=False)`.
  - Generated smooth graphs use one-cell ASCII topology.
  - Native compact graph input remains literal.
- `src/rhizome/ref_badges.py`
  - Added `build_ref_badges(snapshot, selected_ref="", *, plain_glyphs=False)`.
  - Plain markers such as `[L]`, `[WT]`, `[GH]`, `[T]`, and `[S]`.
  - Descriptions/tooltips also use plain generated punctuation.
- `src/rhizome/sidebar.py`
  - Added `ReferenceSidebar.set_plain_glyphs(enabled)`.
  - ASCII tree guides, toggles, separators, ellipses, and status punctuation.
- `src/rhizome/commit_files.py`
  - Added `CommitFileTree.set_plain_glyphs(enabled)`.
  - Per-instance ASCII tree guides and toggles.
- `tests/test_plain_workspace.py`
  - Covers graph output, badges, metadata preservation, widget tree chrome, and literal Unicode repository names using a disposable real repository.

### Validation
- Ruff: passed.
- Focused graph/ref/sidebar/commit-file suite: **85 passed**.
- `tests/test_working_directory_graph.py`: **13 failed** because current `RhizomeApp.on_mount` references missing `_save_navigation_context`; unrelated to owned files.

### Root integration required
1. Pass `plain_glyphs=` to both renderer functions.
2. Include the preference in graph/ref-badge cache keys and rebuild when changed.
3. Call both widget setters after settings load and whenever the preference changes.
4. Make app-owned WIP nodes and graph legends plain-mode aware.
5. Complete RhizomeApp Pilot acceptance after wiring; current tests exercise the public renderer and widgets via Pilot, not root preference integration.

`build_ref_badges` retains positional `selected_ref` compatibility; only `plain_glyphs` is keyword-only.