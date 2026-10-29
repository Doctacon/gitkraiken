# Finish M1, stop before M2

## Current authorization

The operator requested all remaining M1 work with engineering recommendations,
then an explicit stop before M2. Continue deferring installer crash/retry. The
prior existing-checks-only choice applied to the completed composer increment;
this increment includes the identified acceptance gaps. Start: clean operator
commit `0a1ea66` (`feat: security pass`). No project commits, publication, private
provider writes, live user installations, or invented platform/human evidence.

## Bounded work and ownership

- Root: app integration; per-workspace/global core preferences; state reset,
  exclusion and context restoration; draft lifecycle/successive templates;
  diagnostics capture/preview/export/purge; current documentation and integration.
- Designer: familiar narrow/wide UI finish, focus/selection cues, core-controls
  and diagnostics dialogs, contextual help, isolated screenshots and UI evidence.
- Plain-glyph implementation: graph/ref badges/sidebar/file tree fallback with
  literal repository text preserved; root integrates the preference.
- Validation builder: full-size disposable benchmark, compatibility workflow and
  evidence, real-user pilot task sheet (not invented results).
- Composer acceptance builder: public real-Git/Pilot coverage for templates,
  cleanup, encoding, hooks, and successive composition.

No new recovery supervisor, M2 theme/keymap/history/split-diff implementation, or
re-enabling unproven destructive workflows. Existing supported Git actions stay
available. Excluded workspaces retain only an opaque exclusion marker; live draft
text remains usable for the session. Normal navigation/context restoration never
replays mutations or old patch selections.

## Time-bounded continuation

Operator requires every increment to take at most 15 minutes; split before that
limit and continue. No broad delegated jobs. Each entry records elapsed wall time
and focused checks, separately from final acceptance.

1. **15:30–15:34 UTC (4 minutes): diagnostics preview + live plain mode.**
   Export now shares the complete JSON renderer with the UI preview and uses its
   captured records; later records cannot sneak into export and existing files
   are not overwritten. Wired the plain preference into the open precision view.
   Public UI coverage proves exact bytes, no clobber, seed nonpersistence and
   unchanged selection/index/Unicode source. **13 passed in 15.06 s**, Ruff and
   whitespace checks passed. Global-reset error-state clearing also corrected.

2. **15:35–15:43 UTC (8 minutes): state lifecycle and controls.**
   Global M1 reset preserves protected branches, exclusion policies and unrelated
   settings. Exclusion removes saved state without erasing session text; inclusion
   saves only the new session. PR drafts honestly report session-only mode. Added
   public checks for reset, exclusion/reopen, another instance's draft conflict,
   unsaved-quit confirmation and navigation restoration without Git mutation.
   Two initial new-test failures were shortcut-fixture mistakes (Quit is not an
   app shortcut inside modals; Ctrl+Left inside the editor moves by word). Corrected
   those fixtures without changing keyboard contracts. Existing restart cases then
   exposed the new context timer querying teardown widgets; `is_running` now gates
   it before Textual closes children. Final **43 passed in 71.24 s**, Ruff passed.

3. **15:43–15:56 UTC (13 minutes): remove duplicate refresh work.**
   A two-sample full-envelope baseline reproduced 2.413 s startup / 1.872 s
   refresh. A red UI regression confirmed manual refresh immediately reread the
   same snapshot. Remote scheduling now uses the already-presented snapshot;
   periodic local polling and the post-fetch read/apply remain intact. The full
   refresh suite passed (**12 tests, 45.73 s**), followed by two targeted tests
   (**3.97 s**) including fetched remote ref and history-row visibility. Ruff and
   diff whitespace checks passed. Identical two-sample benchmark: startup max
   **1.538 s**, warm refresh **0.964 s**, diff proxy **0.326 s**. This is a diagnostic
   comparison, not final performance acceptance; startup still misses 1 s and
   unrelated background work may affect the benchmark's all-worker-idle waits.
   Artifacts: `m1-performance-{before,after}-refresh-fix.json`. Commands were
   bounded to 180 s; baseline took 24.4 s, combined tests/recheck took 64.7 s.

4. **15:56–16:02 UTC (6 minutes): startup profile and full-envelope recheck.**
   Disposable profile (11.2 s) attributes ~0.517 s to the complete author-date
   history index and 0.101 s to status in a 0.921 s snapshot. Avoided a speculative
   ordering change or new persistent cache. Five-process full-envelope benchmark
   finished in **30.5 s**: startup max **1.489 s** (still fails 1 s), warm refresh
   **0.954 s**, feedback p95 **20.5 ms**, diff max **0.323 s**, peak RSS **91.1 MiB**.
   Updated `docs/validation/m1-performance.md`; no threshold or acceptance waiver.
   Evidence: `m1-startup-profile.json`, `m1-performance-final-local.json`.

5. **16:02–16:10 UTC (8 minutes): broad discovery and first regressions.**
   Complete collection succeeded (all 52 test modules). Interrupted the initial
   full run after 179 s rather than waiting through known failures: **3 failed,
   37 passed**. Restoration incorrectly replaced explicitly configured history
   limits with 300; it now defaults to the current limit and honors nondefault
   saved limits on workspace switch. The empty-repository fixture put XDG state
   inside its own worktree; changed it to the isolated `git_repo` fixture, retaining
   the no-WIP assertion. All three regressions plus the independent empty-repo
   behavior test passed (**4 tests, 11.71 s**). Remaining full coverage will be
   partitioned into bounded batches; this interrupted run is not a full pass.
   Broad Ruff checks also expose ledger-prototype lint and 39 unformatted files;
   no formatter success is claimed. No installer logic was changed.

6. **16:11–16:18 UTC (7 minutes): application/core regression partitions.**
   `tests/test_app.py`: **84 passed in 370.87 s**. All 26 non-async,
   non-installer modules: **430 passed in 245.85 s**. Both commands had 480 s
   limits and ran against independent disposable fixtures; no source changes
   occurred during them. Logs: `m1-regression-app.txt`, `m1-regression-core.txt`.
   Remaining UI modules and installer discovery still need their own batches.

7. **16:18–16:32 UTC (14 minutes): narrow-screen polish and remaining UI batches.**
   Fixed clipped Open/F4 labels and plain Refresh auto-sizing (Textual 1.0 requires
   explicit layout invalidation after a label change). Added red/green viewport
   and mouse-route checks for both glyph modes. UI partition A: **96 passed,
   1 failed**; partition B: **97 passed**. The failure exposed Activity's Close
   button below its dialog content boundary. Reduced two rows of spacing;
   strengthened the actual-dialog-boundary assertion. Final affected batch:
   **15 passed in 34.12 s**; `ruff check src tests scripts` and whitespace passed.
   Regenerated all M1 screenshots in an isolated environment (8.3 s) and reviewed
   the corrected 80-column plain toolbar. Usage/implementation docs now describe
   controls, exclusions, diagnostics, successive templates and actual refresh cadence.

8. **16:33–16:38 UTC (5 minutes): boundary and completed regression coverage.**
   Current Git composer: **27 passed in 40.12 s**. Actual Git 2.36.0 composer plus
   refresh: **39 passed in 46.33 s**. Original partition A rerun in full:
   **97 passed in 112.63 s** (`m1-regression-ui-a-final.txt`), superseding its prior
   red result. All **745 collected cases** now have partitioned execution evidence:
   **744 passed, 1 deferred installer failure**. Installer batch itself: 36 passed,
   1 failed in 44.02 s; no skip/xfail or installer modification. This is aggregate
   coverage over bounded runs, not a single clean complete-suite execution or
   acceptance waiver. Composer boundary report updated with exact commands/results.

9. **16:38–16:49 UTC (11 minutes): real-terminal characterization.**
   Updated popup journey to require non-mutating double-click plus explicit
   checkout. Isolated current-tmux startup and the real **80×24 partial-stage →
   hook refusal/draft retention → commit** journey pass. Popup/pane checks exposed
   a C-locale transport issue before completing: tmux changes literal field tabs
   into underscores (`80_24` vs UTF-8 `80\t24`), breaking existing metadata parsing.
   A two-locale isolated tmux probe confirmed the cause. Full logs retained as
   `m1-terminal-*.txt`; failed popup/pane results are not counted as passing.
   Next bounded piece will fix the narrow metadata delimiter and rerun the journeys.

10. **16:49–17:01 UTC (12 minutes): tmux locale fix and terminal closure.**
   Replaced control separators only in owned/numeric tmux metadata with printable
   delimiters. Added a red/green public pane-launch C-locale variant; CLI batch
   **43 passed in 1.86 s**. Corrected the popup fixture's missing selection-settlement
   wait. Current UTF-8 terminal startup, popup, popup hotkey and pane journeys now
   pass; old failure logs remain. The real **80×24 commit journey also passed with
   actual Git 2.36.0**. Minimum-tmux full journeys remain pending CI (no rebuild).
   Re-enabled corrected CI journeys, declared UTF-8 locale/build dependencies, and
   parsed all five workflow jobs locally. No CI publication/execution. Collection
   is now **746 cases**; aggregate evidence is **745 passed / 1 deferred installer
   failure**, with the additional CLI locale variant accounted for.

11. **17:01–17:14 UTC (13 minutes): final static/build/evidence reconciliation.**
   Formatted 24 M1-owned Python files with verified identical ASTs; root lint and
   their format checks pass. Whole-product formatting still flags 21 untouched,
   byte-identical-to-HEAD files (baseline had 26). Excluded only ledger throwaway
   prototypes from product lint. Offline wheel/sdist builds pass; M1 resources are
   present. Post-format affected batch: **60 passed in 41.51 s**. Workflow YAML and
   whitespace checks pass. A real public pane launch under `LC_ALL=C` also passes.
   Final five-run reference benchmark took **30.5 s**: startup **1.509 s** (still
   misses 1 s), refresh **0.942 s**, feedback p95 **21.5 ms**, diff **0.319 s**, peak
   RSS **98.1 MiB**. No targets waived. `m1-candidate-manifest.json` fingerprints
   131 code/test/build/documentation inputs against unchanged operator HEAD.
   Docs and this ledger distinguish local results from unperformed acceptance.

## Local implementation/evidence checklist — not release acceptance

- [x] Integrated narrow/wide UI and plain-glyph mode; screenshots regenerated/reviewed.
- [x] Core controls, reset/clear/exclusion, durable navigation/layout.
- [x] Successive composition and failure-safe draft lifecycle.
- [x] Local diagnostics; exact captured preview exported without overwrite.
- [x] Composer/state/control/diagnostics public-interface cases.
- [x] Current/minimum Git affected tests; current-tmux and native-commit terminal journeys.
- [x] Full collection covered in bounded regression partitions; deferred installer failure visible.
- [x] Reference-scale benchmark and explicit performance miss; no threshold waiver.
- [x] Compatibility automation and local-versus-pending matrix.
- [x] Docs/help/screenshots and acceptance record reconciled.
- [ ] Reach the 1 s warm-startup target and finish the remaining V04 measurements.
- [ ] Clear whole-tree formatting debt: 21 untouched files, all byte-identical to HEAD.

Local root lint, M1-owned-file formatting and offline wheel/sdist build pass. Full
formatter acceptance is not claimed. All 24 formatted M1-owned Python files retained
identical ASTs. Broader product acceptance also depends on the external gates below.

## Nonlocal / explicitly blocked gates

- Installer crash/retry repair and live install lifecycle remain deferred.
- Hosted CI requires separately publishing/running the workflow.
- Actual other-OS/architecture/WSL/SSH terminal cells require those environments.
- Five-person usability pilot requires real participants; prepare but do not invent.
- These gates prevent claiming release acceptance even after local implementation.
