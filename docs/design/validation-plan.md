# Rhizome: validation plan and bounded hand-offs

> **Status: approved briefs, not executed validation.**
>
> This document accompanies the [approved future design](world-class-rhizome.md).
> The operator authorized writing these two documents only. No prototype, benchmark,
> test run, live-provider operation, installation experiment, or participant study
> has been performed under that authorization. Every brief below is **not run**.
>
> Inspected source baseline: `70005ce6dd23510d6f16af73ff5247fba32b81dc`.
> Existing tests and source are evidence of implementation intent, not evidence that
> the new design or release gates already pass.

## Execution and approval rules

1. Obtain a later instruction before executing any brief. Establish its execution
   owner and time/resource budget then; none is implicitly authorized here.
2. Use disposable repositories, temporary working trees, synthetic content, and
   local bare remotes for mutation/fault tests. Do not experiment on the operator's
   checkout, installed tools, active tmux sessions, or private repositories.
3. A throwaway prototype answers its stated question. It is not permission to
   implement the entire feature, adopt a new framework, or continue an unbounded
   redesign if the experiment fails.
4. Network/authenticated provider validation needs separately authorized accounts,
   repositories, actions, and data handling. CLI-owned credentials stay CLI-owned.
   No live PR creation/deletion/push follows merely from permission to inspect APIs.
5. Record raw evidence and failures. Do not substitute mocks for proof of Git
   content fidelity, synthetic model users for the human pilot, or headless tests
   for every real terminal/platform check.
6. Return to the operator for behavior, privacy, environment, scope, or acceptance
   target changes. Internal interfaces and algorithms may vary within the contracts.
   Do not quietly downgrade line staging, waive recovery, raise minimum versions,
   shrink the reference workload, or move a first-class platform to best-effort.
7. Stop a brief when its bounded question is answered or its stop condition is hit.
   A blocked result with a reproducible reason is useful evidence, not permission
   to expand the project or claim success.
8. Prototype success establishes feasibility only. Actual release candidates must
   satisfy the relevant regression, compatibility, performance, and user gates.

## Validation queue

First-release-critical validation comes first. Later-feature investigations happen
just before their milestones, rather than requiring a whole-roadmap research phase.
A user pilot requires a usable candidate; “critical first” does not mean observing
nonexistent functionality before implementation.

| Brief | Bounded question | Timing/dependencies | Status |
| --- | --- | --- | --- |
| [V01](#v01-patch-and-index-correctness) | Can hunk/line stage and unstage preserve exactly the intended index/worktree state? | Early M1 feasibility, then actual candidate regression | Not run |
| [V02](#v02-safety-checkpoints-and-recovery) | Which actions have proven protection, retention, and recover-aside behavior? | Early M1 action inventory; candidate gates; expand before M3 | Not run |
| [V03](#v03-precision-workflow-interaction-prototype) | Is range selection/core flow understandable with keyboard and mouse at 80×24? | Before locking M1 interaction details; uses V01 behavior | Not run |
| [V04](#v04-performance-and-resource-baseline) | Can the reference workload meet the selected latency goals without hidden stalls? | Baseline before optimization; remeasure each candidate | Not run |
| [V05](#v05-installation-bootstrap-and-updates) | Is pinned managed-runtime install/update/uninstall explicit, safe, and reproducible? | Before M1 distribution gate; coordinates with V06 | Not run |
| [V06](#v06-platform-terminal-and-git-interoperability) | Which named supported combinations actually work? | Availability planning early; actual M1 candidate matrix | Not run |
| [V07](#v07-five-user-usability-pilot) | Can independent terminal-native developers complete the core without unsafe surprises? | Usable M1 candidate after major V01–V06 blockers | Not run |
| [V08](#v08-graph-search-and-investigation) | Are graph/search/comparison/file-history scope and semantics faithful at scale? | Just before and during M2; uses V04 | Not run |
| [V09](#v09-history-conflict-and-worktree-workflows) | Are eligible cleanup, conflicts, and worktree operations safe and understandable? | Just before M3; requires V02 recovery foundation | Not run |
| [V10](#v10-hosted-review-snapshot-fidelity) | Can both providers supply faithful isolated same-repository review? | Just before M4; requires M2 comparison/V08 | Not run |
| [V11](#v11-themes-appearance-detection-and-visual-accessibility) | Do adaptive themes/fallbacks remain readable without delaying interaction? | Just before M2 personalization; preserve M1 baseline | Not run |
| [V12](#v12-screen-reader-feasibility) | What screen-reader workflows can actually be supported, and at what cost? | Later presentation investigation; not an M1 screen-reader gate | Not run |

## Evidence format

Each executed brief should return a compact report containing:

- **Identity:** brief ID, question, relevant design contract, baseline/candidate
  revisions, date, executor, authorized scope and budget.
- **Environment:** OS/architecture, terminal/dimensions, tmux/Git/Python/Textual
  versions, filesystem/storage, relevant configuration, and network conditions.
- **Inputs:** reproducible fixture construction, synthetic/public/private provenance,
  exact revision/content identities where relevant, and injected failures.
- **Method:** commands/procedure, comparison oracle, cache/sampling protocol where
  relevant, and how raw observations were collected.
- **Results:** pass/fail/blocked by case, raw timings or before/after state evidence,
  redacted screenshots/recordings where appropriate, and limitations.
- **Disposition:** what the result proves, what it does not prove, reproducible
  blockers, proposed internal choices, and any operator decision required.

Keep source-bearing checkpoints, patches, drafts, and recordings distinct from
content-free diagnostic exports. Do not commit private source or credentials into
these design documents or fixture/evidence repositories. User-facing exports need
preview/redaction and deliberate sharing.

## V01: Patch and index correctness

**Question.** Can the proposed canonical patch/selection interface implement hunk
and contiguous-line stage/unstage without staging unintended content, losing prior
index state, or silently applying stale selections?

**Contracts.** C2, C3, C4; M1 includes both hunks and lines. Relevant baseline areas:
[`git.py`](../../src/rhizome/git.py), [`models.py`](../../src/rhizome/models.py),
[`app.py`](../../src/rhizome/app.py), [Git tests](../../tests/test_git.py), and
[diff workspace tests](../../tests/test_diff_workspace.py).

**Fixtures.** Build a finite disposable-repository matrix covering:

- Added/deleted/modified lines; partial replacements; contiguous ranges within and
  across relevant hunk boundaries; stage and inverse unstage.
- Already staged content plus unrelated unstaged edits in the same file; staged
  new files; unborn repositories; files becoming empty or deleted.
- Newline-at-EOF differences, CRLF/LF, whitespace-only changes, Unicode and literal
  paths including spaces/newlines/leading option-like characters.
- Renames, mode-only changes, binary data, and representations for which line
  selection must be unavailable with a truthful fallback.
- View transformations: word highlighting, context expansion, whitespace controls,
  and later split rendering must not change canonical staging meaning.
- External edits/index/ref changes after display and before application; index locks,
  failed patch validation, cancelled selection, repeated actions, and subprocess errors.

**Method.** Compare exact before/after index blobs and working-file bytes against
an independently constructed expected result using real Git. Preserve unrelated
staged/unstaged data as explicit assertions. Inject stale-state changes at controlled
seams and verify rejection rather than guessed remapping. Use a small headless
experiment first; connect the same semantics to V03 and actual candidate tests.

**Deliverable.** Case matrix, canonical selection/patch interface proposal, supported
and unsupported representations, exact-state regression evidence, and remaining
race/encoding/filter limitations. Keep algorithms behind the interface rather than
requiring UI callers to reconstruct Git invariants.

**Pass.** Every claimed supported case applies exactly the intended index change,
leaves unrelated state intact, and reports its result honestly. Stale/unsupported
cases refuse or explicitly hand off without unintended mutation. The actual M1
candidate passes the same obligations, not just the prototype.

**Stop/return.** A representation cannot safely map selected lines, filesystem/Git
configuration invalidates assumptions, or partial staging requires a product-level
trade-off. Do not silently switch M1 to hunk-only or make reading filters alter what
gets staged; return evidence and options.

## V02: Safety, checkpoints, and recovery

**Question.** For each enabled action, can Rhizome prove its required protection,
persist recovery content before mutation, enforce retention honestly, and recover
aside without destroying newer work?

**Contracts.** C4, C6, C8; M1 transition rule. Relevant baseline:
[`Action`](../../src/rhizome/models.py), [`Repository.execute`](../../src/rhizome/git.py),
[context actions](../../src/rhizome/context_actions.py), and
[context-action Git tests](../../tests/test_git_context_actions.py).

**Fixtures.** Include mixed index/worktree edits, deletes/renames/modes, branches and
stashes becoming otherwise unreachable, multiple linked worktrees, detached/unborn
states, known-published/protected targets, and obstructing untracked content.
Exercise missing/corrupt recovery data, permission errors, disk exhaustion, an
exhausted content budget, active/pinned checkpoints, process interruption, restart,
and relevant Git object housekeeping in disposable repositories only.

**Method.**

1. Inventory every existing/new mutation and entry point: exact target, affected
   state, risk tier, required preview/checkpoint, safe refusal, recovery claim,
   uncertain-outcome behavior, and whether it can remain enabled in M1.
2. Fault-inject before/during/after checkpoint persistence and before/during/after
   the relevant Git operation. Verify that a failed required checkpoint prevents
   the protected mutation and that restart never replays it automatically.
3. Verify recorded refs/content remain recoverable for the promised retention;
   retaining an OID string while permitting its objects to disappear is not a pass.
4. Exercise 30-day/1-GiB policy, active/pinned protection, explicit purge/exclusion,
   and failure-safe cleanup/admission. Define accounting for content and retained
   objects; expose usage and exhausted capacity rather than hiding unbounded storage.
5. Add newer work, then recover history into a separate branch or file content into
   a reviewable copy. Prove the current checkout is preserved. Validate any later
   apply step separately against changed destination state.
6. Simulate stale remote refs and uncertain request completion. Verify exact remote
   deletion/lease warnings and no automatic mutation retries or false undo promises.

**Deliverable.** Action-capability matrix, checkpoint fidelity/accounting report,
fault/restart/retention tests, recover-aside demonstration, and a precise list of
risky actions to keep unavailable until their proof exists. Expand the matrix for
M3 rather than claiming blanket protection from a few successful examples.

**Pass.** Every enabled protected action has usable recovery for its stated coverage;
required backup failure stops it. No unsupported untracked content is destroyed,
no active/pinned recovery is silently evicted, no newer work is overwritten by the
default recovery path, and no remote/external action is labeled universally undoable.

**Stop/return.** Protection needs broader untracked-data collection, a different
privacy/retention policy, or a weaker safety claim. Gating an unproven risky action
is permitted; waiving its safeguards is not. Return any material policy trade-off.

## V03: Precision workflow interaction prototype

**Question.** Can users understand and operate the proposed contiguous selection,
explicit stage/unstage, stable review, composition, and navigation model with either
input style at the required terminal size?

**Contracts.** C1–C3, C8; M1. A prototype is throwaway validation, not implementation
authorization. Follow V01 semantics even if much of the surrounding UI is minimal.

**Fixtures.** Use a clearly disposable real repository with staged and unstaged edits,
multiple hunks, long paths/lines, a changed-on-disk selection, an unsent draft, a failed
hook scenario, and both available and deliberately gated risky actions. Exercise
80×24 and a wide layout such as 170×48, with a mid-sized layout where useful.

**Method.** Walk the same finite task set with keyboard only and mouse only:
inspect → select range → stage → correct/unstage → inspect staged result → compose →
recover draft after failure/reopen → commit. Add explicit checkout, contextual help,
Back/panel switching, changed-state notices, and compact activity visibility. Verify
letters do not steal text entry, focus remains visible, and resizing preserves useful
context rather than silently selecting different content.

**Deliverable.** Screen/interaction examples, proposed precise key/gesture assignments
within the approved conventions, focus/state transition notes, and a short issue list.
Distinguish demonstrated feasibility from untested accessibility claims.

**Pass.** Both input styles can finish essential tasks, 80×24 has a clear primary task
and return path, selections have unambiguous extent, and users can tell what will be
staged and whether the view is stale. No browsing gesture checks out a branch and
no unresolved interaction requires violating the safety contract.

**Stop/return.** The model requires changing the spatial model, contiguous-selection
policy, supported terminal size, or essential input parity. Return the observed
failure and alternatives; do not quietly introduce a modal editor or redefine M1.

## V04: Performance and resource baseline

**Question.** Can the chosen progressive/live design meet the reference-profile
latency targets while expensive Git/network work remains visible and local
interaction stays usable?

**Contracts.** C1, C5, C9 and the performance section of the design. The baseline
currently scans OIDs/timestamps to select chronological history and shares an
operation lock with remote refresh; neither fact establishes actual timings.

**Fixtures.** Use reproducible small fixtures, the agreed reference envelope, and
explicit stress cases beyond it. The reference envelope is **100k commits, 20k
tracked files, 1k refs, 200 changed files, ordinary diffs up to 200 KiB**. Include
merge/skewed-date history, many refs, clean/dirty states, repeated navigation,
large/binary/truncated views, a stalled remote, and slow hooks/signers. Keep stress
cases bounded by the separately authorized execution budget.

**Method.**

- Record reference SSD laptop hardware, OS, filesystem, tool versions, workload
  topology/content, terminal dimensions, and repeatable generation procedure.
- Define the timing points, sample counts/statistics, and cache protocol before
  measurement. Distinguish process/app/filesystem cache states and report them;
  do not silently call a warm run cold or select only favorable samples.
- Measure visible app feedback, first usable local workspace, ordinary diff
  presentation, queue wait, Git/subprocess time, and render time separately.
- Targets are p95 app feedback **≤100 ms**, usable local workspace **≤2 s cold /
  ≤1 s warm**, and ordinary diff presentation **≤500 ms**. Report distributions
  and outliers. State how the non-p95 thresholds are evaluated before claiming
  a pass; any interpretation that weakens the agreed target needs approval.
- Stall remote work deliberately. A quick “queued” notification is not a substitute
  for local-view responsiveness. Separate unavoidable hook/signer/network duration
  from app responsiveness; preserve honest progress and safe cancellation limits.
- Measure CPU, memory, repeated-operation growth, and background resource use.
  Return proposed numerical resource budgets: none are approved yet.
- Remeasure the actual M1 candidate. Repeat relevant profiles for M2 search/graph
  changes and later capabilities rather than extrapolating from a tiny demo.

**Deliverable.** Reproducible workload/protocol, raw measurements, bottleneck profile,
comparison of justified internal approaches, target assessment, and proposed resource
budgets. Include failures and deviations, not just a single favorable screenshot.

**Pass.** Evidence establishes the selected latency contract on the declared profile,
no stalled remote freezes local interaction, incomplete work is labeled, and candidate
behavior matches the measurement protocol. Resource claims remain provisional until
the proposed budgets are approved and tested.

**Approved M1 exception (2026-10-29).** The operator accepted the measured 1.509 s
startup maximum on the recorded five-run local reference profile. This startup
result is no longer an M1 blocker; original thresholds/raw results stay intact.
See the [acceptance record](../validation/m1-performance.md#operator-acceptance--2026-10-29).
Other V04 measurements and release gates remain unchanged.

**Stop/return.** A target cannot be met within the chosen behavior/support envelope,
cache/fixture assumptions materially change its meaning, or the needed approach
requires a product/privacy/scope change. Do not weaken thresholds, hide work, shrink
the reference dataset, or add an eager index by default without returning the trade-off.

## V05: Installation, bootstrap, and updates

**Question.** Can a version-pinned GitHub release be installed with managed Python,
updated explicitly, inspected, rolled back, and removed without silently modifying
unrelated tools or configuration?

**Contracts.** C10; Git and authentication remain user prerequisites. PyPI publishing
and self-contained executable distribution are not selected. Baseline entry points
are [`cli.py`](../../src/rhizome/cli.py), [`pyproject.toml`](../../pyproject.toml), and
[`tmux.py`](../../src/rhizome/tmux.py).

**Fixtures.** Isolated homes/environments with and without uv/compatible Python,
existing unrelated uv tools/runtimes, missing Git or too-old supported tools, an
existing Rhizome installation, paths with spaces, restricted permissions, interrupted
and corrupted downloads, offline/failed resolution, and requested rollback/uninstall.
Cover the agreed architectures through V06 rather than the executor's host alone.

**Method.** Prototype the smallest inspectable bootstrap and the direct-uv path.
Record its plan and exact persistent changes. Verify release/source pinning,
download validation/provenance assumptions, failure diagnostics, and PATH guidance.
Check that it does not silently edit shell profiles, Git/tmux configuration, request
elevation, or remove unrelated existing runtimes/tools. Exercise explicit check →
release information → pinned install, with no background update traffic, automatic
installation, or restart. Preserve a usable prior installation or report a failed
state honestly when interrupted; test the documented recovery procedure.

**Deliverable.** Platform-specific install/update/rollback/uninstall transcript,
persistence inventory, trust/provenance explanation, version verification, and
failure matrix. Document the source of the first downloaded executable and what
validation actually establishes; a checksum is not automatically a complete trust
model. Do not publish a release or installer as part of this experiment.

**Pass.** Clean-machine and direct-uv paths meet the selected managed-runtime behavior,
install the intended pinned version, disclose their effects, and preserve unrelated
configuration/tools. Updates are explicit and failures have reproducible recovery.

**Stop/return.** The design requires a system package-manager channel, silent setup,
elevated privileges, new distribution targets, or changed minimum versions. Return
those decisions rather than adding channels or moving back to unpinned installs.

## V06: Platform, terminal, and Git interoperability

**Question.** Which exact combinations substantiate the first-class platform promise,
and what limitations must be disclosed without silently narrowing that promise?

**Contracts.** C1, C8, C10; named practical matrix, not exhaustive cross-product.
Current [CI](../../.github/workflows/ci.yml) is Ubuntu-only. Existing
[tmux](../../scripts/tmux_smoke.py), [popup](../../scripts/popup_smoke.py), and
[pane](../../scripts/pane_smoke.py) smoke scripts are starting points, not results.

**Matrix obligations.** Fill actual OS/terminal/tool versions when execution is
scheduled; no untested versions are implicitly certified by this table.

| Required coverage | Evidence type |
| --- | --- |
| macOS and Linux core application | Automated correctness/UI checks on the candidate |
| macOS ARM64 and x86_64 | Install/runtime coverage, using appropriate actual runners/environments |
| Git 2.36+ and tmux 3.2+ floors plus current versions | Boundary-version coverage across jobs; not every possible cross-product |
| A named real macOS terminal and real Linux terminal | Human-visible input/rendering/resize/clipboard smoke |
| WSL2 with Windows Terminal | Actual WSL runtime, Git/path/clipboard/terminal behavior; not native-Windows application support |
| SSH and tmux | Remote interaction, pane containment/resize, resume/pause, cleanup/process preservation, clipboard fallbacks |
| 80×24 and a wide layout | Complete core paths, focus, labels, stable context, no special font |

**Fixtures/method.** Run authorized candidate checks and named smoke journeys using
disposable repositories and isolated tmux servers. Check real input/focus behavior,
small/wide resize, helper availability/failure, OSC 52 fallback honesty, and no false
“copied” success when a terminal request cannot be confirmed. Use literal paths,
missing optional provider CLIs, and offline operation. Verify local work remains
available without hosted authentication.

Exercise recognition/safe hand-off for submodules, LFS, shallow/sparse state, hooks,
and signing. Preserve Git's configuration/trust semantics; do not claim sandboxing
or general native management. Auth/signing tests requiring credentials or devices
need separate authorization and must not collect secrets in evidence.

**Deliverable.** Completed versioned matrix, reproducible smoke procedures, exact
exceptions and failure reports, supported-versus-unverified labeling, and necessary
release documentation updates proposed for the implemented milestone.

**Pass.** Required families/architectures and minimum-version obligations have credible
recorded evidence, essential workflows work at required sizes/input styles, and
unsupported optional capabilities produce honest hand-offs. No destructive tmux
cleanup or unrelated process/configuration damage occurs.

**Stop/return.** A promised environment is unavailable for verification or materially
fails, a minimum version is infeasible, or interoperability would need native feature
expansion. Record a blocked cell and obtain a decision; do not quietly relabel it
best-effort or treat Linux headless success as proof of WSL/macOS/SSH behavior.

## V07: Five-user usability pilot

**Question.** Do independent terminal-native developers understand and complete the
precision workflow without coaching or unsafe surprises?

**Contracts.** C1–C4 and the release usability gate. Use a usable M1 candidate after
major correctness and interaction blockers have been resolved. V03 is a prototype
check, not a replacement for this pilot.

**Participants/environment.** Five people who know Git basics and work in terminals.
Cover keyboard-only and mouse interaction, 80×24, and tmux/SSH cases across the pilot.
Use synthetic/disposable repositories; obtain consent for any recording and avoid
capturing private code or credentials. Model-generated personas are not substitutes
for observed participants.

**Task set.**

1. Enter a repository and discover the relevant commands/help without a mandatory tour.
2. Inspect mixed staged/unstaged edits and state what would currently be committed.
3. Stage an intended hunk and a selected line range; correct a selection by unstaging
   the relevant part without losing unrelated changes.
4. Compose a commit with the available template/guidance, preserve a draft through
   an appropriate failure/reopen case, and commit the intended staged result.
5. Separately exercise explicit checkout and understand dirty-state/context blockers.
6. Inspect available recovery and recover-aside behavior, or correctly understand a
   deliberately gated risky action and its external hand-off.

**Method.** Observe rather than coach the core tasks; in-app help is part of the
product. Record completion, mistaken assumptions, unexpected mutations, focus/input
problems, and points where the displayed state disagrees with the user's mental
model. Record time as diagnostic evidence, not an invented numeric time gate.

**Deliverable.** Anonymized task-by-participant findings, completion counts, input/
environment coverage, issue severity, and fix/retest evidence. Explain the limits of
a small qualitative pilot; do not present it as a population-wide usability estimate.

**Pass.** At least **4/5 independently complete the core inspect → stage/correct →
compose/commit flow**. No observed safety misunderstanding or core blocker remains
unresolved. Required keyboard/mouse/narrow/remote coverage is present; relevant fixes
are retested. Minor cosmetic findings can be explicitly deferred under the defect gate.

**Stop/return.** The model repeatedly produces safety misunderstandings or cannot
support an agreed input/environment. Return evidence and a bounded redesign proposal;
do not count assisted completion as independent or waive a blocker to hit a date.

## V08: Graph, search, and investigation

**Question.** Are ordering, focus, pagination, search, comparison, and file investigation
faithful to Git while remaining understandable and bounded at the selected scale?

**Contracts.** C1, C5, C9; M2. Build on [`graph.py`](../../src/rhizome/graph.py),
[graph tests](../../tests/test_graph.py), sidebar/ref identities, and V04.

**Fixtures.** Merge/octopus/disconnected histories, skewed author/committer times,
unloaded parents, many/wide refs, branches sharing tips, deep history beyond 5,000,
shallow/missing history, renamed files, literal paths, and changing refs during
search/comparison. Include comparisons with different endpoint/common-base results
and cases where a unique useful base cannot be established.

**Method.** Trace visible graph connections against actual parents, not only input
metadata. Check topology-first and chronological modes separately from Smooth/Compact
spacing. Verify single-click selection never filters, explicit focus/exit is labeled,
and pagination/jump-to-result stays bounded without hiding reachable history. Compare
complete-scope search and file-history/blame output with appropriate Git oracles,
including progress/cancellation and stale-result handling. Verify branch-review versus
endpoint comparison labels and refusal/explicit hand-off for unavailable semantics.

**Deliverable.** Geometry/scope correctness matrix, representative screens, query/
comparison semantics documentation, scale measurements, and limitations. Propose
internal changes without silently adding an eager index or changing search scope.

**Pass.** Visible ancestry is truthful, active/searchable/loaded scope is unmistakable,
results are not silently capped to rendered rows, selected identities survive
presentation changes correctly, and comparisons do not substitute semantics silently.
The candidate meets relevant V04 behavior and preserves the user's return context.

**Stop/return.** Meeting scale would require a new indexing/storage policy, changed
chronological semantics, or narrower history promises. Return the trade-off rather
than misdrawing ancestry or presenting partial results as complete.

## V09: History, conflict, and worktree workflows

**Question.** Can the approved integration workflows execute with clear plans, exact
identity, explicit resolution/staging, and recovery under realistic failures?

**Contracts.** C4, C6; M3. Depends on proven V02 capabilities, not a generic claim that
Git's reflog makes every operation recoverable.

**Fixtures.** Eligible linear ranges for reword/reorder/squash/fixup/drop; ranges
containing merges; protected branches; commits reachable from known remote refs;
stale/offline publication state; diverged branches; dirty working state; conflicts
from merge/rebase/cherry-pick/revert; binary/rename/manual-resolution cases; and
worktrees with clean/dirty/untracked/locked/prunable/moved states. Include signing/
hook failures and restart during an operation using synthetic commands, not secrets.

**Method.** Compare the previewed plan with resulting commits, content, and branch
movement. Reject unsupported plans before mutation. Verify offline uncertainty
acknowledgment does not bypass known-publication/protection checks and that no force
push is available. Test fast-forward-only Pull and distinct integration choices
without automatic stash or publication.

For conflicts, verify actual commit/branch side labels under rebase as well as merge,
inspect results before explicit Stage, and require separate Continue. Do not equate
closing an editor or removing marker-like text with a verified/staged resolution.
Complex unsupported cases must retain a clear external hand-off and safe resume.

For worktrees, verify exact registered destinations, Git-managed move/remove/prune,
clean/unlocked refusal rules, retained branches, preserved context, and no unrelated
filesystem deletion or process killing. Honor Git locks and known in-use conditions
without claiming omniscient detection of every external editor/process.

**Deliverable.** Plan/result/failure transcripts, eligibility and protection matrix,
recovery demonstrations, conflict-side examples, worktree identity tests, and clear
external hand-off coverage. Re-run V02 fault cases for newly enabled actions.

**Pass.** Only eligible plans execute, actual changes match the reviewed intent,
required recovery works, conflicts remain deliberate, and destructive worktree actions
obey the clean-only contract. No hidden strategy change, merge flattening, force-push,
or unsupported data loss occurs.

**Stop/return.** Supporting a case requires published/merge-history editing, broader
backup scope, force removal, embedded general code editing, or a weaker recovery
claim. Keep the case unavailable and return a scoped decision request.

## V10: Hosted-review snapshot fidelity

**Question.** Can GitHub.com and Azure DevOps Services each supply a faithful,
revision-identified, same-repository review without disturbing the current checkout?

**Contracts.** C7; M4, after M2 comparison. Current
[`PullRequest`](../../src/rhizome/pull_requests.py) stores branch names rather than
source/target OIDs and source repository identity; the new workflow must establish
those facts explicitly. The [existing PR guide](../pull-requests.md) links primary
provider documentation and describes current authentication/creation limits.

**Fixtures/sources.** Obtain current official API/CLI documentation and versioned,
redacted representative responses for both providers. Include source/target movement,
draft PRs, missing/deleted branches, multiple remotes, custom mappings, forks,
authentication/permission failures, pagination/truncation, missing check status,
and unavailable Git objects. Use mocks/local remotes for destructive/failure cases;
live authenticated reads require separately approved accounts/repositories.

**Method.**

- Establish trustworthy provider/repository/PR identity and captured source/target
  OIDs; distinguish forks even when branch names match local names.
- Demonstrate a stable isolated comparison and explicit fetch of missing objects
  without moving HEAD, changing the index/worktree, or automatically creating a
  worktree. Record any local object/private-ref writes as part of that explicit fetch.
- Verify moving PRs produce a changed-state notice rather than switching revisions
  under active review. Missing/unfetchable captured revisions produce a truthful
  browser hand-off, not a comparison of whichever branch tip is now available.
- Map review/check states faithfully. Unknown, unsupported, malformed, and incomplete
  states must not be shown as successful/complete. Preserve CLI-owned authentication
  and safe literal rendering/URL identity handling.
- Preserve existing same-repository creation guards and no automatic retry after an
  uncertain creation outcome. Comments, approval, merge, and fork creation remain out.

**Deliverable.** Source-linked provider capability/permission matrix, captured-identity
contract, exact-revision comparison evidence for each provider, fetch/isolation tests,
and documented browser hand-offs. Do not claim parity where provider capabilities differ.

**Pass.** Both selected providers' claimed capabilities have evidence; reviewed commits
are explicit and stable, current work remains untouched, forks are not misidentified,
and failures/limits are truthful. No unintended hosted writes or credential collection.

**Stop/return.** A provider cannot support a faithful workflow with the approved CLI/
authentication model, or the solution requires fork support, another provider, broader
credentials, automatic checkout, or different review semantics. Return the choice.

## V11: Themes, appearance detection, and visual accessibility

**Question.** Can best-effort appearance matching and selectable themes stay readable,
respect overrides, and fail harmlessly across the named terminal matrix?

**Contracts.** C1, C8; M2 personalization, with baseline keyboard/visual accessibility
already required in M1. Existing [`test_theme.py`](../../tests/test_theme.py) checks
4.5:1 contrast for several actual light-theme text/selection/diff surfaces; preserve
that existing baseline rather than treating a palette screenshot as sufficient proof.

**Fixtures.** Light/dark terminal settings; no/unsupported appearance response; SSH/
tmux pass-through differences; explicit saved override; normal/selected/hover/focused
states; diff additions/deletions and range selection; high-contrast/plain-glyph use;
resize and reconnect. Record actual terminal versions and capabilities.

**Method.** Read version-appropriate Textual/terminal documentation before choosing a
detection mechanism. Probe in an isolated session with a bounded timeout, verifying
it cannot consume ordinary input incorrectly or stall startup. Exercise selectable
light/dark/high-contrast themes and override persistence. Measure actual rendered
foreground/background combinations and inspect non-color cues, focus, labels, and
plain-glyph fallbacks with both input styles.

**Deliverable.** Detection/support/fallback matrix, contrast and state screenshots,
input/latency evidence, and an explicit list of what can be claimed. This is visual/
keyboard evidence, not proof of general accessibility or screen-reader compatibility.

**Pass.** Reliable detection is used only where supported; failure uses the approved
current-light fallback without blocking work; overrides win and survive restart.
No essential meaning depends on color or special glyphs. Existing contrast guarantees
are retained and new theme states have equivalent measured/readable treatment.

**Stop/return.** Reliable matching requires platform/support changes, a different
fallback, or a materially different input/rendering architecture. Keep manual themes
and the approved fallback truthful; return any change to the selected behavior.

## V12: Screen-reader feasibility

**Question.** Which core workflows are actually usable with named screen-reader/
terminal combinations, and would meaningful support require a new product decision?

**Contracts.** C8. This investigation is selected; broad screen-reader support is not
already promised and is not an M1 screen-reader release gate.

**Fixtures/method.** Choose a small, explicit set of relevant combinations from the
supported platform families using current primary documentation and actual access
to those tools. Observe focus, labels, navigation, diff content, selection, errors,
operation status, and composition in the core workflow. Where feasible, involve
people who use the relevant assistive technology rather than relying on an author's
visual interpretation or framework feature claims.

Distinguish terminal output availability from usable interactive workflow access.
Evaluate whether existing task-focused/linear representations help before proposing
any major new mode. No alternate GUI, framework rewrite, or large accessibility
implementation is authorized by this brief.

**Deliverable.** Reproducible capability/limitation matrix, sources and observed task
evidence, small feasible improvements within the current contract, and explicit
options for anything requiring broader investment or a changed support promise.

**Pass for the brief.** The feasibility question is answered honestly with named
combinations and limitations. A negative result is a valid investigation result;
it is not a screen-reader support pass. Make no broader claim than the evidence.

**Stop/return.** Necessary changes exceed the approved interaction/support scope or
cannot be assessed with available tools/users. Record the blocker and ask for a new
decision rather than declaring support or quietly abandoning the selected investigation.

## Release evidence reconciliation

Before a later release decision, collect a traceable evidence index rather than
marking a milestone complete because its code exists.

| Gate | Required reconciliation |
| --- | --- |
| Product scope | Every shipped feature matches its milestone/contract; deferred and gated features are clearly identified |
| Integrity/safety | V01/V02 and applicable later cases pass for every enabled action and entry point |
| Core usability | Candidate task/input/80×24 behavior is supported by V03 regression and V07 observations |
| Performance | V04 reproduces the approved targets on the recorded profile; resource budgets are not invented or claimed prematurely |
| Distribution/platforms | V05/V06 substantiate the actual pinned artifact and named supported matrix |
| Later capabilities | Relevant V08–V12 results and actual candidate tests support each additional claim |
| Documentation | Usage/help/release notes describe implemented behavior and limitations, not merely this approved future design |
| Defects | No known safety/core-workflow blockers; deferred cosmetic findings are explicit |
| Privacy | Evidence collection/exports retain consent, redaction, and source/credential handling requirements |
| Approval changes | Any changed scope, behavior, privacy, support promise, or target has an explicit operator decision |

No row is currently marked passed. The immediate completed undertaking is writing
the approved specification and these validation briefs; execution and implementation
remain separate work requiring further authorization.
