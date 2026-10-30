# M2 execution

## Authorization and discipline

2026-10-29: the operator authorized M2 and renewed the rule that every individual
piece must take **15 minutes or less**. They freshly confirmed public test seams:
`InvestigationService` / `Repository` with disposable real Git, `WorkspaceStateStore`,
`render_history`, `RhizomeApp` through Textual Pilot, and public CLI smoke.
No private parser/worker tests, live provider writes, commits/pushes, merge, tag, or
release publication are authorized by this work.

M1 is accepted. M2 is **in progress**, not complete. The approved design and
`m2-investigation-plan.md` govern behavior. All timing is wall-clock time; check at
12 minutes and split an unfinished piece before 15 minutes. Test suites and scale
work are separate bounded pieces, not hidden in implementation time.

## Ordered bounded pieces

Each row is a work family, not one giant timed task. Execute its listed pieces
separately, splitting again if any piece will exceed 15 minutes.

| Family | Independently bounded pieces (target 5–12 min each) |
| --- | --- |
| Captured scopes | immutable values + exact capture; relevant-ref staleness; replacement/missing-object refusal |
| History | topology pages + cursor validation; merge/octopus/disconnected oracles; streamed author-time ordering; >5k reachability |
| Search | bounded text batches/progress; path/date criteria; captured refs and jump cursors; stale/cancellation tests |
| Comparison | unique-base branch review + endpoints; ambiguous/missing/shallow refusal; bounded patch/file limits |
| File investigation | rename-aware file history; literal paths + paging; bounded porcelain blame; binary/shallow/refusal coverage |
| App cutover | one service/scope + history-free polling; bounded older/newer windows; ordering independent of density |
| Focus/search UI | explicit focus + return; progressive search criteria/progress; jump/widen/cancel; stale-state preservation |
| Investigation UI | comparison intent/endpoints/base; file history navigation; blame and original-context return |
| Personalization | validated curated settings; editable file + overrides/reset; remappable command validation; in-app key editor |
| Presentation | optional split inspection + narrow fallback; palette tokens; light/dark/high-contrast states; bounded appearance detection |
| Validation | focused regression partitions; 80×24/wide Pilot journeys; contrast/plain glyphs; V04/V08 100k workload partitions; V11 evidence |
| Closure | implementation/usage docs; exact-candidate evidence; explicit unrun platform/human checks; acceptance status |

## Work log

- Opening–22:44 UTC (first recorded checkpoint): located the existing plan, checked clean `main` at `d32d12c`,
  read M2/V08/V11 contracts and repository adapters, confirmed public test seams,
  and decomposed the milestone. No production behavior changed in this piece.
- 22:44–22:48 UTC: captured-scope models/Repository adapters and service wrapper.
  Sixteen real-Git capture/status cases passed (5.72 s). Initial red was missing
  module/adapter, not independent behavioral evidence; later slices use executable
  stubs to establish behavioral red. Scope ownership, exact refs/OIDs, detached and
  empty roots, relevant-ref staleness and immutable captured names are covered.
- 22:48–22:51 UTC: bounded topology/history service, signed cursors, streaming
  author-time adapter (author-time behavioral evidence is the next separate piece).
  History behavioral red was the missing public method; 13 targeted and 29 total
  investigation cases passed (full run 13.61 s). No fixed total browsing ceiling
  exists in the new service; the App still uses the legacy path pending cutover.
- 22:50–22:51 UTC: independent history-free snapshot polling option. Public test
  failed on unsupported `include_history`, then passed; nine legacy history/snapshot
  cases passed in 8.91 s. Existing snapshot default remains compatible.
- 22:51–22:54 UTC: bounded full-message/author/email/SHA/captured-ref search adapter
  and service. One public body-search/progress/jump test failed on an explicit stub,
  then passed in 1.43 s. Path/date filtering and broader search evidence followed.
- 22:51–22:56 UTC (independent test piece): merge/octopus/disconnected topology,
  skewed author times and ties, 5,001-commit fast-import pagination, missing objects
  and repository replacement. All 37 history/scope tests passed (26.78 s).
- 22:54–22:56 UTC: literal rename paths and inclusive timezone-aware author-date
  criteria. Behavioral test first exposed ignored filters; both search tests then
  passed. Follow-on search coverage (22:56–23:02) found invalid paths bypassing
  validation in empty scopes; fixed before the empty return. All 22 search cases
  pass (15.78 s). Examined counts mean commits evaluated against search criteria,
  not lightweight OID lookahead or Git's traversal of a chronological/paged prefix.
- 22:56–22:58 UTC: intent-aware comparisons, explicit unique-base versus endpoint
  results. Divergent-tree test failed on the stub then passed (1.19 s). Independent
  23:02–23:05 refusal evidence covers criss-cross/disconnected/shallow/missing or
  outside-scope endpoints, frozen refs and explicit patch truncation: eight cases
  including shallow-boundary changes passed (7.70 s).
- 22:59–23:02 UTC: paged file history. The real rename test exposed Git's `--skip`
  bypassing rename discovery under `--follow`; changed to stream/discard the prefix
  while retaining only the requested page. No durable or whole-history index.
- 23:02–23:04 UTC: bounded porcelain blame, original path/line provenance, boundary
  markers, explicit binary/2 MiB/encoding refusal. Stub red then two file cases
  passed (2.22 s). Independent file/shallow coverage (23:05–23:10) exposed missing
  file history incorrectly returning empty success; fixed while allowing deleted
  paths with available history. Added literal Unicode/newline paths and cursor tests.
- 23:10–23:12 UTC: backend regression partition, **147 passed in 111.27 s**;
  `m2-backend-tests.txt` includes new service tests and existing Git/diff/read-only
  index tests. No UI or final scale claim follows from this partition.
- 23:08–23:10 UTC: App initial topology cutover and history-free polling. Observable
  Pilot order test failed under legacy chronology, then passed in 2.56 s.
- 23:10–23:13 UTC: bounded Older/Newer page replacement and separate order control;
  removed active `self.limit` browsing and legacy snapshot scans. Three Pilot cases
  passed (11.13 s), including 601 commits, 80×24 order/style independence, passive
  stale indication, and explicit-refresh recapture. Polling retains captured labels.
- 23:14–23:17 UTC: explicit Focus/Exit Focus, non-filtering ref selection, original
  scope/page/selection/search/scroll return context. Focus Pilot red then passed in
  4.50 s. Broader progressive search/return tests remain to be added.
- Independent presentation pieces: comparison dialog (23:06–23:11) and file/blame
  dialog (23:11–23:18) implemented with bounded reads and 80×24 local harness checks.
  Root App integration and full Pilot proof are still pending; harness checks are
  not final acceptance evidence.
- 2026-10-29 16:49–16:52 MST: palette foundation and CSS tokenization. Added
  registered Textual light/dark/high-contrast themes, non-probing `COLORFGBG`
  hint resolution, and Rich color translation; converted `app.tcss` literals to
  built-in Textual variables. `py_compile` and a local contrast harness passed
  (dark minimum 5.73:1; high-contrast 7.72:1). Existing light-only Pilot tests
  were not expected to pass before root registers the new themes: they resolved
  `$background` through `textual-light`, yielding `#e0e0e0` rather than the
  existing pale Rhizome surface. Full app-theme proof remains pending root wiring.
- 2026-10-30 00:13 UTC checkpoint: the root keymap/settings integration slice
  exceeded the 15-minute cap. This is a process deviation, not a completed bounded
  slice; further pieces use explicit short timers and narrower ownership.
  Catalog IDs and safe overrides are now wired into App bindings; a real App Pilot
  first failed because the remap did nothing, then passed (including ordinary
  composer typing). Combined check: **2 passed, 1 failed**; the remaining legacy
  theme test expects `textual-light` rather than the new `rhizome-light` identity.
  Theme adapters, dialog tokenization, split integration and broader settings
  validation remain separate unfinished pieces.
- Additional backend/UI evidence: detached linked-worktree heads now participate
  in unified captured roots and staleness; focused worktree tests **3 passed in
  17.03 s** (2026-10-29 23:42:38 UTC). Passive stale observation is latched until
  refresh. Full backend rerun still pending after this change.
- Catalog/storage pieces completed by the builder by 23:50:36 UTC: **36 persistence
  tests passed**. A behavioral red caught mixed-priority alias IDs accepting a
  printable priority remap; binding IDs are now unique, with separately editable
  nonpriority letter aliases. Escape remains fixed Back/Clear navigation.
- Split inspection widget completed independently 00:05:39–00:07:48 UTC; standalone
  harness and Ruff passed. No App integration or staging-safety acceptance follows
  from that harness.
- 00:13–00:19 UTC: theme-to-workspace adapters and public contrast proof. Both
  dark variants first failed on actual history spans (2.88:1 / 3.45:1); App now
  themes history, captured badges, sidebar/file trees, headers and commit editor.
  Theme/graph/keymap partition: **31 passed in 15.00 s**. Light name assertion was
  migrated to `rhizome-light`; its surface/contrast oracles were retained.
  Dark dialog red tests now independently expose remaining white modal surfaces;
  delegated as a separate bounded piece, not treated as completed theme coverage.
- 00:19–00:27 UTC: split inspection App integration checkpoint. Stored preference
  opens a read-only split view over captured source; original canonical patch is
  retained for staging. Cosmetic rows align changes, wrap within fixed columns,
  reset file numbering, and narrow fallback retains literal source order. Public
  Pilot now proves paired content and unchanged index/worktree after inspection
  keys, but **return-to-Unified still fails**; button/layout diagnosis is a new
  piece rather than extending this one. The initial red was missing widget
  integration, not independent behavioral evidence.
- 00:19:03–00:21:33 UTC independent dialog/precision-theme piece: four owned UI
  modules converted to theme variables, precision palette setter added without
  changing canonical patch or selection. **6 requested tests + 7 supplementary
  tests passed**. Root has now wired the precision setter; rerun still pending.
- 00:27–00:34 UTC: split-controls diagnosis/fix. A targeted probe confirmed the
  canonical document was retained; the button extended outside its parent and
  Pilot's click returned false. Constrained footer text, made nonstageable sources
  say `Unified view`, removed temporary probes, and retained a source-order narrow
  view. Public Pilot: **2 passed in 7.33 s**, at 200×50 and 80×24, including unchanged
  worktree/index and return to canonical Unified. Committed-source label/fallback
  and stale split inspection still need additional coverage.
- 00:34–00:41 UTC: settings acceptance and discoverability. **19 tests passed in
  58.75 s** across global/workspace precedence, order-toggle persistence, reload,
  reset, invalid-file retention, appearance hints/overrides, and legacy controls.
  Migrated the obsolete arbitrary-keymap fixture to a catalog mapping; protection
  and exclusion preservation assertions remain. Help now uses effective catalog
  bindings and removes the obsolete 5,000 ceiling; focused Pilot **1 passed in
  5.50 s**. Broader terminal appearance remains unverified.
- 00:34:21–00:36:08 UTC independent benchmark-harness piece: new
  `scripts/benchmark_m2.py` reuses isolated M1 fixtures and fresh child workers.
  Smoke verified 1,200 commits, complete no-hit search and bounded App history.
  This is harness evidence only; reference-scale performance remains unrun at this checkpoint.
- Bounded full regression partitions: backend **597 passed in 354.78 s**
  (00:43:30–00:49:26 UTC); core App **84 passed in 476.27 s**
  (00:47:54–00:55:51); other workspace tests **191 passed, 2 failed in 539.06 s**
  (00:48:59–00:57:59). Together these selected all 874 collected cases.
  Follow-up 01:03–01:05: migrated immediate-search navigation and obsolete
  `app.limit=1` assumptions. The latter now exercises a real >300-commit page
  boundary; preservation assertions remain. **22 affected tests passed**.
- 01:03:02–01:04:16 UTC independent memory-lifetime piece: service registry uses
  weak scope ownership plus immutable tamper comparison copies. Public GC regression
  failed first; **39 investigation tests passed**, Ruff and service Pyright clean.
  Discarded refresh/focus scopes no longer retain all captured refs indefinitely.
- 01:08:14–01:09:55 UTC: isolated reference measurement completed, saved in
  `m2-reference-scale.json` (100,000 commits / 20,000 files / 1,000 refs / 200 changes).
  Full no-hit search examined all 100,000 in 100 bounded batches, **81.48 s** total.
  App retained 300 commits (301 rows including WIP), peak RSS **81,854,464 bytes**.
  Single headless startup **1.906 s**, explicit warm refresh **1.573 s**. The earlier
  200 ms comparison was erroneous: V04 specifies **≤1 s warm usable workspace**,
  **≤2 s cold**, **p95 feedback ≤100 ms**, and **diff ≤500 ms**. The warm sample still
  exceeds 1 s; it is an explicit recapture, not a separately measured passive poll.
  No p95, real-terminal, cold-cache, or milestone-pass claim.
- Broad Pyright reports **56 errors**; isolated HEAD app/git baseline reports **45**.
  New typing issues (scope trails/ref kind/optional snapshot narrowing, streaming
  stdout, dialog page narrowing and palette color type) remain to resolve; inherited
  App/Git errors must not be presented as a new clean whole-project typecheck.
- Documentation piece: `docs/investigation.md` now describes captured scopes, paging,
  complete-scope criteria/progress, review intent, file limitations, split safety,
  scoped settings/keymaps and hint-only appearance. README links it and explicitly
  keeps M2 development separate from accepted M1/release status.
- 01:24:48–01:25:58 UTC adapter/dialog/palette typing piece: **23 focused tests passed**;
  targeted new errors removed. Root App narrowing/trail/ref-kind fixes were made
  separately. The 01:50:43 recheck reports **44 inherited errors** (28 Git, 16 App),
  versus 45 in archived HEAD. Diagnostic-signature comparison finds **no new
  signatures**. This is not a clean whole-project typecheck.
- Process deviation: root App typing edits around 01:26 were left awaiting focused
  validation until 01:43 and type verification until 01:50, exceeding the 15-minute
  slice cap. Interleaving work did not make that compliant. Checkpointed here;
  future slices must include their own validation before starting another root task.
- Diagnostic profiling: `m2-refresh-profile.json` records a disposable 5k-commit,
  100-ref fixture. A history page spent 0.360/0.460 s in three full scope checks;
  one no-hit batch spent 0.235/0.375 s in two checks. Status timings include nested
  Git timing and must not be added twice. Consultant hypotheses were followed by
  this actual profile. Removed only the duplicate Service history precheck;
  Repository pre-read validation and Service post-read validation remain. No
  broader unchecked-reader or subprocess-wait redesign was performed.
- 01:34:53–01:43:17 UTC theme surface piece: actual dark/HC Activity failed white,
  then working-change contrast failed. Replaced remaining owned Activity/PR/divider
  CSS colors, selected dark PR editor theme, and used active WIP status colors.
  Final **13 tests passed in 45.15 s** (theme, precision, Focus); independent owned
  Activity/PR/splitter checks **15 passed**. Ruff across source/tests/new benchmark
  passed. This does not establish general accessibility or terminal compatibility.
  Validation ended after the 8-minute checkpoint target but before the 15-minute cap.
- 02:07:38–02:12:40 UTC split edge piece: real-App newline provenance test failed
  because an old-only missing-newline marker appeared in both columns. Fixed its
  side attribution without changing canonical patch data. **5 split tests passed**,
  covering wide/narrow inspection, wrapping, committed-source static Unified fallback,
  explicit canonical staging after return, and stale-review refusal with unchanged
  index/worktree. Both stale warning phrasings are accepted case-insensitively;
  index/content safety assertions remain exact. Ruff and split Pyright passed.
- 02:17:14–02:26:25 UTC full-regression piece: all **880 collected tests passed**
  across disjoint file partitions: **598 backend**, **84 core App**, **198 workspace**.
  Each subprocess had an explicit 660-second supervisor deadline; none timed out.
  `m2-final-regression-manifest.json` records selection, logs and elapsed times.
  Two formatting-only fixes followed; Python AST equality was checked before/after.
  By 02:28:42 Ruff check/format (101 files), `git diff --check`, public CLI `--help`,
  and 880-case collection verification passed. The Pyright inherited-error caveat
  remains. This test coverage is not real-terminal, accessibility or V08 acceptance.
- 02:34:00–02:36:38 UTC additional public-search piece: **2 tests passed** for Stop
  after at least one no-hit batch (no late overwrite; a new query works) and
  Focus/Exit Focus restoring query results, selected OID, 300-row window and scroll.
  This is additional coverage, not a rerun of all 882 now-present tests.
- 02:34–02:39 UTC benchmark-harness piece: added bounded 1–50 repeated explicit App
  refreshes, raw per-sample CPU/wall/RSS, and nearest-rank p95 only with ≥20 samples.
  Smoke with two samples and invalid 0/51 CLI counts passed; Ruff passed. Schema 2
  explicitly separates one-sample startup/backend from repeated same-App refreshes
  and excludes Git child CPU/RSS from resource claims.
- 02:41:13–02:43:19 UTC quiet, separately supervised reference run completed:
  `m2-reference-repeated.json`. Same verified 100k/20k/1k/200 envelope, linear DAG
  and 1,000 refs sharing one tip. Complete no-hit search: **100,000 examined / 100
  batches / 81.18 s**. First topology/author-time pages: **0.736 / 0.817 s**, 300 rows.
  Headless startup: **1.792 s** (one sample, not cold-cache evidence). Twenty explicit
  warm recaptures: **min 1.315, median 1.403, p95 1.566, max 1.633 s**. Every sample
  exceeds the **≤1 s warm-workspace target**; this is a miss, not an acceptance.
  App stayed at 300 commits / 301 rows. Python-worker peak RSS **74,235,904 bytes**;
  sampled current RSS first/last **72,400,896 / 66,502,656 bytes**. Child Git resources
  are excluded; no universal leak-free, whole-process-tree or approved-budget claim.

## Current checkpoint

M2 remains **in progress and unaccepted**. Investigation, settings/theme and split
features are implemented with the evidence above; there have been no M2 commits,
pushes, releases, live installation changes or provider writes.

Next independently bounded pieces:

1. Preserve the ≤1 s warm contract and no-index posture while evaluating less
   redundant Git scope work. The small duplicate-precheck removal is measured;
   more aggressive internal exact-root reads with final status/error translation
   were only proposed, not implemented or validated. Do not bypass safety checks
   or adopt an eager/durable index without a justified decision.
2. Complete V08's mixed/merge/skewed topology, deep-page/jump, author-time search,
   repeated-resource and responsiveness matrix. The linear 100k run proves bounded
   rows and search completion only for its recorded workload.
3. Complete remaining criteria/widening/return-context and live-theme review-state
   coverage, then V11 terminal/SSH/tmux, appearance, screen-reader/human evidence.
   Existing headless/contrast checks do not replace these.
4. Reconcile the exact final candidate and seek milestone acceptance only after
   the remaining gates or explicit operator decisions. Do not reuse M1's exception
   as an M2 waiver. Current inherited Pyright debt is 44 errors, with no newly
   introduced diagnostic signatures in the selected-module comparison.
