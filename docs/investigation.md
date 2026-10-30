# Investigation and personalization (M2 development)

These features are implemented in the development checkout. This is not an M2
release-acceptance or publication record. Real-terminal/platform checks and the
remaining performance/visual validation are tracked in the execution ledger.
Default shortcuts below can be changed in **Workspace → Edit key bindings**;
Help displays the effective bindings.

## Captured history

The default view is unified, topology-first history: captured commit-bearing refs
and available worktree HEADs, including detached HEADs. A scope contains exact
object IDs and captured decorations, not moving branch names.

- **Older / Newer** replace bounded 300-commit windows. There is no 5,000-commit
  total ceiling. Omitted parents and page boundaries are not invented joins.
- **F6 / History order** switches topology and descending author-time order.
  Author-time ties are deterministic within the captured traversal. This is
  independent of **g / Smooth–Compact**, which changes graph presentation.
- Selecting a branch does not filter history or check it out. **F5 / Focus** is an
  explicit scope change. **Shift+F5 / Exit focus** restores the prior browsing
  context; Focus is not checkout.
- Passive polling reports **CHANGED** without replacing captured graph roots or
  labels. Once observed, the indication stays latched even if refs move back.
  **Refresh** explicitly captures a new view. Missing objects, replacement of the
  repository, and changed shallow boundaries have explicit limitations/refusals.

Investigation reads do not apply Git replacement-object rewriting. There is no
persistent eager history index. Bounded output limits still apply; a bounded page
is not a promise to display arbitrarily large commit messages or patches.

## Complete-scope search

The search field scans the captured scope, not just rendered rows. Results do not
filter the graph. **Next / Enter in search** chooses a hit; an unloaded hit opens a
bounded history window at that commit.

Text is a case-insensitive literal substring across full commit messages, author
names/emails, SHA text, and captured ref names. Those text fields are alternatives.
**Filters / Shift+F** adds AND criteria:

- A literal repository-relative path, without glob/pathspec expansion. Path search
  asks whether that commit changed the path; it is not rename-following file history.
- Inclusive author-date bounds, not committer dates. Supply ISO 8601 timestamps
  with a timezone, for example `2026-10-01T00:00:00+00:00`.

Progress counts commits evaluated, not matches. A batch with no hits may still be
incomplete. Lightweight Git traversal/lookahead is not an evaluated commit count.
Empty batches continue automatically; a result-bearing batch offers **Search more**.
Only one result batch is retained. **F10 / Stop search** stops further batches and
invalidates obsolete results; it does not cancel a Git mutation. **Widen to all
refs / Ctrl+F5** explicitly captures a wider scope while retaining the criteria.
Changing the query, scope, order, repository, or view invalidates incompatible
continuations rather than silently mixing results.

## Comparison and file investigation

**F7 / Compare history**, or the selected-ref review action, captures explicit
endpoint object IDs. The dialog distinguishes:

- **Branch review:** unique merge base of A and B → B (three-dot review intent).
- **Endpoints:** exact tree at A → exact tree at B, independent of ancestry.

The actual base and endpoints are labeled. Disconnected, ambiguous/criss-cross or
shallow branch-review cases refuse rather than substituting a different comparison.
Choose endpoint comparison explicitly when that is the intended question.

**F8 / File history** and **F9 / Blame** use a fixed revision and literal path.
They show provenance, bounded pages, completeness and limitations. File history
uses Git rename following; it is heuristic lineage, not a claim of complete copy
or merge-lineage reconstruction. Blame shows original path/line attribution and
boundary information. Binary, invalid UTF-8, oversized (over 2 MiB), missing, or
otherwise unsupported blame inputs are refused explicitly. Empty text files are
valid. Closing these views does not change the checkout, graph or search.

## Unified and split diffs

**Workspace → Diff layout** chooses unified or split file inspection. Split mode
is read-only and aligns changes cosmetically. It never supplies a patch for staging.
Long lines wrap; narrow panes show literal source-order unified inspection instead
of squeezed columns. The underlying captured source is unchanged.

**Unified for exact staging** returns to the original canonical patch when exact
selection is supported. Committed or unsupported sources instead offer **Unified
view**, without promising staging. External edits mark captured reviews stale;
returning to Unified does not bypass stale-review checks.

## Preferences and key bindings

**F4 / Workspace** edits global defaults or workspace overrides outside the
checkout. A workspace override replaces that preference, including its keymap;
it is not mutation replay. Global offline remains a master stop. Resetting global
preferences resets curated settings/keymaps but preserves protection/exclusion
records; this does not add M3 branch-protection behavior.

The dialog shows the user-owned `settings.json` path and offers **Reload settings**.
The default location is `$XDG_STATE_HOME/rhizome/settings.json`, falling back to
`~/.local/state/rhizome/settings.json`; `RHIZOME_STATE_HOME` overrides the Rhizome
state directory. The file has a `schema_version: 1` envelope and a `data` object.
Edit existing values rather than replacing unrelated privacy settings. For example,
these are fields inside `data`:

```json
{
  "theme": "dark",
  "graph_style": "compact",
  "graph_order": "topo",
  "diff_layout": "split",
  "keymap": {"toggle_graph_style": "ctrl+alt+g"}
}
```

Keymaps use catalog command IDs, not executable action strings. The editor displays
default keys; blank restores a default. Validation rejects unknown IDs, malformed
chords, collisions, reserved editor/navigation keys and unsafe priority text keys.
Escape remains fixed Back/Clear navigation. Nonpriority letter shortcuts have
separate IDs from their priority control-key aliases. Invalid files are not silently
overwritten; repair them and reload. The last applied configuration remains in use
when an explicit reload fails.

## Appearance and accessibility limits

Select **Light**, **Dark**, **High contrast**, or **Auto**. A saved explicit choice
wins. Auto uses only the nonblocking `COLORFGBG` environment hint: canonical
background index 0 suggests dark; 7/15 suggest light. Missing, malformed or
inconclusive hints use light. Environment values can be stale and terminal palettes
customized. There is no OSC appearance query or input-stream consumption.

Keyboard controls, focus treatment, textual status/selection cues and generated
plain glyphs do not require color alone. Repository text is not transliterated.
Automated contrast/headless checks are not proof of SSH/tmux/terminal behavior,
general accessibility, or screen-reader compatibility; those remain separate
validation claims.
