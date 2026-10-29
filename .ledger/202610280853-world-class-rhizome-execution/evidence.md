# Execution evidence

## Current safety override

**Ordinary commit submission is temporarily gated in both coordinator and UI.**
Writer exclusion, raw-index/config review, read-only inspection, native identity,
live-descendant retention and artifact ownership now have focused fixes/proofs.
Dedicated process groups require absence before publication; live/unprovable
writers and ambiguous spawn outcomes retain artifacts without killing/retrying.
Interactive signing/TTY compatibility, composition, remaining publication faults
and coordinator/UI success proof remain blockers. See `commit-root-followup.md`.
Earlier stage→commit flows are not current candidate acceptance; their success
contracts remain intact.

Fetch was separately protected and restored after scoped current/minimum-Git and
coordinator/Pilot/automatic-refresh proof; see `fetch-root-evidence.md`. Installer
recovery remains preview-only: root reproduced a second installer succeeding while
a surviving child was active (**1 failed in 3.38 s**). No production fix yet; V05
remains blocked. No changed full suite or milestone acceptance is claimed.

## Latest root integration results

- `task-20`: unchanged 44-case patch/transaction/representation slice passed on actual Git 2.36 after correcting fixture template configuration.
- `task-21`: 46 cases passed on both current and actual Git 2.36, before raw-index fingerprint changes.
- `task-22`: current Git **126 passed in 98.37 s**; Git 2.36 **123 passed, 3 failed in 46.27 s**. Fixed unsupported `symbolic-ref --no-recurse` and normalized equivalent UTC date output without weakening tests. The third failure was a missing HTTPS helper in the disposable build, not a redaction behavior failure. `task-24` built local HTTP/file helpers and corrected the compiled exec path without installation; subsequent minimum-Git engine/fetch slice: **73 passed in 26.00 s**.
- Raw-index/config plan binding and inspection-without-index-rewrites slice: **14 passed in 11.19 s**, Ruff green. Git 2.36 diff needs `diff.autoRefreshIndex=false` as well as optional-lock suppression.
- Added dangling-lock detection and fixed the second diagnostic scanner pathology (`+`, **15.05 s** red). Process slice **4 passed in 5.50 s** and legacy credential/redaction/environment slice **3 passed in 2.15 s**, Ruff green. These are structural guards, not p95 performance acceptance.
- First commit fault matrix (spawn failure, direct signal, duplicate late header, native nonzero after ref movement) plus process tests: **8 passed in 10.27 s**, Ruff green, after adding honest observed HEAD/symbolic and publication phases. This predates the descendant/ownership follow-up in `commit-root-followup.md`.
- Fetch public safety matrix after alias-loss fixes and coordinator restoration: current Git **37 passed in 23.42 s**, actual Git 2.36 **37 passed in 10.94 s**, Ruff green. `task-25` manual fetch/automatic refresh/sidebar/activity Pilot batch: **29 passed in 97.37 s**, Ruff green.
- `task-23`: independent installer/CLI/persistence slice **61 passed in 40.97 s**, shell syntax and Ruff green. Boundary doubles only; no live install/V05 acceptance.

- `task-26`: current Git **147 passed in 117.38 s**; Git 2.36 **146 passed, 1 failed in 61.76 s**. The inherited-output fixture incorrectly required Git 2.36 to exit despite its hook-output EOF wait. Corrected that premise while preserving all safety oracles; redirected cases still require direct Git exit with a live child.
- `task-27`: final combined engine/patch/native/fetch/read-only/plan slice **151 passed in 120.02 s** on current Git, **151 passed in 56.47 s** on actual Git 2.36. Includes directory/context ownership and linked+split proofs, but not gated coordinator/UI commit successes or the red installer guard. See `commit-root-followup.md`.

## Baseline

- Baseline revision: `70005ce6dd23510d6f16af73ff5247fba32b81dc`.
- Host: macOS ARM64, Git 2.50.1 (Apple Git-155), tmux 3.5a, Python 3.13.0.
- `uv run --locked pytest` (existing suite, started before implementation): **456 passed in 564.39 s**. Background task `task-1`, exit 0.
- This is existing-code baseline evidence, not proof of any new milestone or other platform.

## First integration slices

- Sidebar double-click regression first failed because it checked out `test` instead of retaining `main`. After removing the mutation gesture and updating its hint, the test passed. A test-render accessor was corrected from `render().plain` to the public `renderable.plain`; this was a harness fix, not product evidence.
- Graph double-click regression first failed because it checked out a branch. Activation now inspects the selected commit without mutation.
- `uv run --locked pytest tests/test_app.py::test_graph_double_click_inspects_without_checkout tests/test_app.py::test_sidebar_double_click_only_selects_without_checkout -q`: **3 passed in 8.31 s**.
- Operation coordinator scaffold remains unwired while its contracts are completed. Three real-Git red→green cases established stale worktree rejection, fail-closed discard without a checkpoint, and moved checkout-target rejection. Focused run: **3 passed in 0.88 s**; subsequent ownership transferred to the operations worker for completion.
- Added an autouse `RHIZOME_STATE_HOME` temporary-directory fixture before persistence is integrated, to prevent tests from touching the real user's drafts/settings/recovery.

## Parallel implementation/prototype artifacts

- `patches.md`: initial canonical patch implementation and focused test evidence. The final index-validation/update race remains explicitly unproven; no UI adoption until reviewed/fixed or safely gated.
- `persistence.md`: private durable store/diagnostics implementation and focused evidence; application integration still required.
- `recovery.md`: bounded journal and recover-aside evidence; unsupported/non-HEAD ref cases remain gated pending exact coverage.
- `prototype-precision/`: three native Textual variants with headless captures. A is the recommended direction, not accepted human/platform evidence. Its own report records limitations in narrow stale-action/mouse exercise.
- `index-transaction-review.md`: read-only safety review requested for the final index update transaction; pending until its report is written.

## Integrated precision and safety slices

- Migrated remaining double-click browser expectations: `uv run --locked pytest tests/test_app.py -k double_click -q` — **8 passed in 23.59 s**. Explicit checkout still has separate coverage.
- Canonical patch publication now holds the real worktree-specific Git index lock across candidate construction, final validation and atomic replacement. A real competing `git add` initially succeeded in the unsafe window; after the change it is refused on `index.lock`, and its later retry preserves the selected result.
- Added real-Git checks for the competing writer winning first, linked-worktree isolation, split-index sidecar durability, sparse-index/skip-worktree preservation, pre-publication failure cleanup, and unsupported/replayed selections. Focused patch run after transaction changes: **27 passed in 15.77 s**.
- A reconstructed document with altered unseen base bytes initially changed the index. Comparing the entire immutable canonical document now rejects it: focused red→green **1 passed in 0.54 s**.
- Post-publication fsync failure initially appeared as failed activity despite the changed index. Added `GitOutcomeUncertain` and proved the coordinator reports **uncertain**, retains actual index effects, and refuses replay: focused red→green **1 passed in 1.40 s**.
- Removed the new `git check-attr --source` dependency (absent from the official Git v2.36.0 documentation). Target attributes use a disposable alternate index plus `check-attr --cached` instead. A real-Git test with the newer flag refused at the subprocess boundary went red→green; **3 focused checkout/attribute tests passed in 3.97 s**. This is not an actual minimum-version runtime certification.
- Initial store/recovery/patch/sidebar integration batch: **75 passed in 30.89 s**. This predates the latest UI changes and is not a full-candidate acceptance result.
- Commit-draft restart regression went red (empty after reopen) → green (**1 passed in 2.75 s**). A nonexistent fixture was fixed first; that setup error was not counted as behavioral red evidence.
- All workspace `Repository.execute` shortcuts, including automatic fetch, now go through `OperationCoordinator.plan/submit`. No recovery journal is injected into the workspace yet, so discard and other unproven destructive workflows remain unavailable. A real-Git UI test first discarded a modified file, then passed with the content retained and an unavailable explanation.
- The production `PrecisionDiff` is integrated through the coordinator's original-document/selection plan. Keyboard `v` → Down → `s` first left the index unchanged, then staged only the selected replacement. No rendered text is used as patch input.
- External-change review retains the captured text/cursor/scroll, marks it stale, refuses Stage, and requires explicit refresh. A mouse hunk action preserves a second hunk. **3 precision UI tests passed in 10.67 s** before the narrow-flow addition.
- At 80×24, Files/Commit/Refs summons now use one primary task panel with Back. A real mouse/keyboard inspect → range-stage → type → commit flow went red (missing Files control) → green (**1 passed in 6.97 s**), with only the selected replacement committed and the other worktree edit intact.
- Full integration discovery (`task-4`, `integration-test-run.txt`) finished with **17 failed, 550 passed in 657.93 s**. Thirteen failures encoded now-gated mutation behavior; three required focus/precision-widget/one-primary-panel expectation migration; one exposed a real narrow Inspector toggle regression. This was not a passing acceptance run.
- The safety-test migration retained real Git HEAD/ref/index/status/content oracles, explicit target pickers, real fetch/push success, and externally created conflict state. Focused migration: **15 passed in 65.27 s** (`m1-test-migration.md`); no skips or exceptions enabled unsafe actions.
- Inspector toggling at narrow sizes now preserves the active Details tab instead of always summoning Files. Updated diff/viewport and responsive tests: **11 passed in 31.54 s** (`task-5`).
- A real Git fetch blocked at a fake external SSH transport originally prevented opening a local precision diff. Splitting background transport from the short local-read queue fixed it. Mutations still serialize in the coordinator; no running Git process is cancelled. The real-Git delayed-status regression proves a concurrent mutation cannot be replaced by the earlier read, even when the final state equals the prior visible snapshot. An explicit mutation epoch complements busy/generation checks.
- Hidden-terminal automatic refresh now pauses until focus resumes. Existing cadence assertions were migrated from 30 to the approved 60 seconds. Workspace-state, refresh, and background-inspection batch: **21 passed in 64.17 s** (`task-8`), including the real Git hidden/focus test and the independent PR-draft target-isolation test. Legacy refresh tests still contain their preexisting internal mocks; the new scheduling proofs use actual Git with only external transport/clock/process boundaries controlled.
- PR title/body restart retention went red (empty title after reopening) → green (**1 passed in 4.61 s**). Draft identity includes worktree incarnation plus hosted provider/repository/source/target intent, not a moving commit OID. Cancellation retains text; completion cleanup failure must not masquerade as failed remote creation. No real PR was created in this validation.
- Activity is wired to a visible control and Ctrl+L, with coordinator outcomes and distinct background-read status. The 80×24 stage→activity→Close test exposed and fixed a Textual private-name collision and an overflowing Close control; **1 passed in 3.43 s**, Ruff passed. Closing the view does not cancel or retry Git. Broader running/queued and terminal validation remain to be done.
- Distribution initially reported **52 focused tests passed**; the independent safety review found material gaps. Follow-up added explicit runtime/build artifacts, managed CPython 3.13.13, configuration/auth boundaries, physical ownership checks and interruption recovery. Latest worker evidence: **27 focused tests passed in 14.98 s**, Ruff/shell syntax passed (`distribution-followup.md`). Boundary doubles are not a live install; a follow-up read-only safety review is pending. No installation or release was performed.

## Later refresh, persistence, and distribution verification

- Second full discovery (`task-9`, `integration-test-run-2.txt`): **1 failed, 586 passed in 691.80 s**. The unchanged stale hosted-result regression was a real defect. It and a new real-Git remote ABA regression passed after returned-identity validation; see `refresh-race.md`.
- The broader follow-up first found impossible legacy hosted fixtures (**2 failed, 25 passed in 77.75 s**, `task-10`). Corrected identities/local transport routing preserved cache assertions and strengthened non-vacuous initial-state checks. `task-11`: **27 passed**. With non-blocking explicit hosted reads and the corrected local-usability expectation, `task-13`: **36 passed in 103.95 s**. This predates the commit gate.
- Offline PR drafting remains available but attempted publication now refuses before branch verification/provider transport. Real-Git/public-UI red **1 failed in 3.11 s** → green **1 passed in 2.79 s**, Ruff green. No PR was published.
- Expanded state/lifecycle slice (`task-14`): **9 passed in 36.46 s**, Ruff green, including hook-failure draft retention followed by successful completion, linked-worktree switching, and recreated-repository isolation. This also predates the commit gate; its successful commit path is not current acceptance evidence.
- Filesystem-boundary draft-write failure and actual corrupt document preservation: **2 passed in 5.61 s**, Ruff green. The editor reports failure and does not replace the last saved/corrupt data.
- Added temporary HOME/XDG Git configuration isolation and cleared inherited Git selectors for every test. Real user templates/hooks/config are no longer part of fixture behavior; tests intentionally exercising global config can still set their own HOME.
- Distribution hardening worker reports **48 passed in 21.00 s**, Ruff, shell syntax and whitespace checks (`distribution-hardening.md`). Root rerun is `task-15`; still no live installation, artifact-transport validation, publication, or target-platform acceptance.
- Ordinary commit competing-index-writer test: coordinator red **1 failed in 0.71 s**, then gated. Public Repository reproduction red **1 failed in 0.70 s** → first private transaction green **1 passed in 0.89 s**. The next same-parent ref-substitution case is **1 failed in 1.51 s**. Protection is incomplete; coordinator/UI remain gated.

## Native transaction and active-fetch follow-up

- Native-created OID binding fixed the sibling-ref substitution; hook stdout routing was corrected from pinned Git source, not guessed. See `commit-native-sources.md`. Coordinator/UI commit remain gated.
- Six real-Git representation cases cover unborn/detached/SHA-256, linked-worktree isolation, native-twin sparse preservation and split sidecar storage. The initial split test passed without necessarily creating a new base; root strengthened it with a synchronous staging/forced-rebuild hook. This exposed the actual expiry defect: **1 failed in 1.87 s**. A selected-patch expiry test initially passed without forced rebuild, then correctly failed with `splitIndex.maxPercentChange=0`: **1 failed in 0.89 s**, real reader exit 128/missing shared index.
- Candidate operations now defer shared-index expiration with per-call `splitIndex.sharedIndexExpire=never`, leaving saved config and representation intact. Both regressions: **2 passed in 2.92 s**, Ruff green. Combined current-host patches/commit transactions/representations (`task-18`): **44 passed in 35.24 s**.
- Actual upstream Git 2.36.0 was built only in a disposable `/tmp` tree; no install. `task-17` had 6 passes/4 fixture failures and `task-18` minimum-version half had 39 passes/5 fixture failures because an incorrectly cased Make variable left the compiled template path nonexistent. These are NOT behavioral reds or minimum-version acceptance. `task-19` corrected `template_dir` and proved a disposable init has its hooks directory; `task-20` reruns the unchanged 44 tests.
- Root installer hardening rerun (`task-15`): **48 passed in 21.74 s**, shell syntax/Ruff green. Final static review still identifies material recovery/ownership/environment gaps; new hardening is in progress, no live-install acceptance.
- Public-API disposable audit reproduced active fetch refspec branch rewrite and `fetch.prune`/`fetch.pruneTags` local-tag deletion. Fetch now fails closed in coordinator and visible UI pending backend proof; earlier automatic-fetch passes are historical, not current availability. See `network-ref-safety-review.md`. Push scope review found existing explicit no-force/no-mirror/no-follow-tags protections and real-Git coverage.

## Current limits

The candidate is in progress and no changed full suite has passed. Earlier obsolete destructive/browsing expectations were migrated, but the new commit failures are real and must be fixed, not reclassified. No milestone is complete or releasable. No human pilot, other OS/architecture/minimum-version matrix, live private-provider validation, publication, release, or project commit has been performed.
