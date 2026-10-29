# Rhizome: world-class terminal Git workspace

> **Status: approved future design; not an implementation report.**
>
> The operator approved the decisions recorded in the eight-round design interview
> (Q1–Q76) and explicitly confirmed creation of this document and its
> [validation plan](validation-plan.md). That confirmation authorizes documentation
> only: no feature implementation, prototypes, benchmarks, live-provider tests,
> releases, or commits. Those activities require a later instruction.
>
> Source baseline inspected: `70005ce6dd23510d6f16af73ff5247fba32b81dc` on `main`.
> No runtime or performance results were produced during the interview. The
> numerical targets below are requirements to validate, not achieved measurements.

## 1. Purpose and authority

Make Rhizome exceptional at the daily **inspect → stage → commit → integrate**
loop for terminal-native developers who know Git basics. Preserve the advantages
of staying in a terminal without requiring either keyboard or mouse users to
leave essential workflows unfinished.

The product remains a **local-first terminal Git workspace**:

- Standalone operation is first-class; tmux is optional and well supported.
- Existing Git authentication, hooks, signing, and user configuration remain
  Git-owned. There is no mandatory account, hosted service, or AI dependency.
- The recognizable graph/sidebar/inspector workspace evolves rather than being
  replaced wholesale.
- The roadmap is staged and quality-gated, with a sharply bounded first release,
  not an arbitrary deadline or a commitment to ship every feature at once.

This document specifies the approved destination and delivery policy. The
[usage guide](../usage.md), [implementation contract](../implementation.md),
[pull-request guide](../pull-requests.md), source, and tests describe the current
implementation. Do not change their present-tense promises merely because this
future design has been approved. Update them when the corresponding behavior is
implemented and verified.

The [decision register](#10-decision-register) preserves every interview answer.
Internal algorithms, storage formats, and precise widget/key assignments may be
chosen within these contracts. Changes to behavior, privacy, supported environments,
scope, or acceptance targets require an operator decision. Evidence of infeasibility
is a reason to return with options, not silently weaken a requirement.

## 2. Current foundation and verified gaps

These are source-inspection findings at the baseline above, not results of running
Rhizome or its tests.

| Area | Current foundation | Gap relevant to this design |
| --- | --- | --- |
| Git engine | [`Repository`](../../src/rhizome/git.py), immutable shared values in [`models.py`](../../src/rhizome/models.py), allowlisted operations, literal argv/path handling, captured-context guards | No partial-staging engine or app-owned durable recovery journal |
| Workspace | [`app.py`](../../src/rhizome/app.py), graph/sidebar/inspector, file diffs, file/all-file staging, commit composer | Session-local preferences; no durable settings/draft store; double-click currently checks out immediately |
| History | Pure rendering in [`graph.py`](../../src/rhizome/graph.py), Smooth/Compact styles, unified author-time history | Initial 300-commit window and 5,000-commit ceiling; loaded-history search; no guided history-plan editor |
| Work scheduling | Git calls run off the UI loop; local refresh every 2 seconds; remote refresh every 30 seconds | Background network work shares the operation lock and can delay user work; no established performance baseline |
| Safety | Fast-forward-only pull, non-force push, guarded remote deletion, operation Continue/Abort, explicit reset modes | Some destructive actions execute immediately after selection; hard reset can affect obstructing untracked content; no general recovery UI |
| Hosted workflows | GitHub.com/Azure DevOps Services browsing and same-repository PR creation through authenticated `gh`/`az` | Current [`PullRequest`](../../src/rhizome/pull_requests.py) records have branch names but no captured source/target OIDs or fork identity for faithful local review |
| Compatibility/release | Python 3.13+, Textual 1.x, Git 2.36+, optional tmux 3.2+; real-Git/UI/tmux tests exist | [CI](../../.github/workflows/ci.yml) runs on Ubuntu; no verified cross-platform release matrix or publishing workflow |
| Appearance | Fixed light palette; [`test_theme.py`](../../tests/test_theme.py) checks contrast on several actual rendered surfaces | No adaptive/selectable theme experience or established screen-reader support |

The distribution decision is **versioned GitHub installs**, not PyPI publishing.
During the interview, [PyPI's `rhizome` project](https://pypi.org/project/rhizome/)
was found to be an unrelated package. No package-name acquisition or product/CLI
rename is part of this roadmap.

## 3. Design tree and dependencies

A decision becomes interview-ready when its prerequisites are settled. The current
product/delivery decision frontier is empty; unresolved empirical questions have
explicit briefs in the validation plan. Future findings may create a new frontier
if they require changing an approved contract.

```text
Scope: terminal-native, local-first, staged roadmap                         [settled]
├─ Daily-flow advantage + keyboard-first/mouse-complete + evolve workspace [settled]
│  ├─ Canonical diff/selection + stable context + draft persistence
│  │  └─ M1: precise commit crafting
│  ├─ Explicit graph focus + topology/chronology + complete scoped search
│  │  └─ M2: investigation, comparison, file history/blame
│  └─ Remappable commands + durable preferences + accessible presentation
│     └─ M2: full personalization and optional split diff
├─ Risk-tiered safety + bounded recovery + honest Git state                [settled]
│  ├─ Identity/staleness guards + checkpoint fidelity + recover-aside
│  │  └─ Required for every protected action enabled in M1 or later
│  ├─ Local/unpublished linear plans + branch protection
│  │  └─ M3: guided history editing and divergence handling
│  └─ Explicit conflict review/staging + clean-only worktree removal
│     └─ M3: integration and worktree lifecycle
├─ Existing providers + read-only review + same-repository identities      [settled]
│  └─ Captured revisions + comparison foundation + explicit missing-data fetch
│     └─ M4: isolated hosted review (depends on M2 comparison)
└─ Platform promise + managed runtime + quality gates                      [settled]
   ├─ Versioned GitHub bootstrap, explicit updates, no telemetry
   ├─ Named compatibility matrix, performance evidence, five-user pilot
   └─ Validation briefs, executed only on later authorization
```

The product milestone order is **M1 → M2 → M3 → M4**. Later milestones can ship
useful increments; they are not instructions to create giant all-or-nothing releases.
Safety and honest-state contracts apply across the tree rather than waiting for M3.

## 4. Product contracts

### C1. Navigation and workspace

- Keyboard-first, with complete mouse equivalents for essential workflows.
  Extend familiar bindings such as `Ctrl+P`, consistent pane/item navigation,
  Back/Escape, and `j`/`k` outside text entry. Ordinary letter shortcuts must not
  steal input while composing or searching.
- Preserve the recognizable three-pane layout when space permits. At **80×24**,
  use one primary task view with quickly summonable file/ref/detail panels,
  visible repository/branch/view context, and a clear return path.
- Single-click selects context without moving or filtering the unified graph.
  An explicit **Focus** action enters a labeled, reversible scoped view.
- **Checkout is an explicit command** available through keyboard/palette/menu
  affordances. Double-click is browsing/inspection/focus, not checkout; no opt-in
  double-click-checkout preference was selected.
- Preserve active review/selection context when files, refs, or PRs change.
  Show a changed-state indicator and require refresh/reselection before applying
  a stale action. Unaffected summaries may update. An app-initiated operation may
  deliberately refresh its resulting view.
- Resume useful global preferences, repository navigation/layout context, and
  text drafts after restart. Revalidate identities and live Git state. Never
  replay pending Git mutations merely because a session was restored.
- Maintain standalone, tmux, and SSH usability without requiring a special font.
  Clipboard limitations and terminal-request uncertainty must remain explicit.

### C2. Precise staging and diff reading

- Retain file/all-file actions and add **hunk and selected-line stage/unstage**.
- Select a visible contiguous range using keyboard or mouse, then explicitly
  Stage/Unstage. The initial selection model is not a discontiguous basket or
  editable-patch editor.
- Apply exactly the intended change against the relevant captured index/worktree
  state. Preserve unrelated staged content and working-file edits. Changed inputs
  invalidate the operation rather than silently remapping a selection.
- Unified diff is the default. Optional side-by-side inspection belongs to the
  later personalization milestone and must degrade appropriately when narrow.
- Reading aids include word-level changes, whitespace controls, search,
  hunk/line navigation, and expandable context.
- Reading transformations are not staging transformations. The UI must not stage
  invisible or different content because whitespace filtering, highlighting,
  expanded context, or split presentation changed what was displayed.
- Large, binary, generated, mode-only, and otherwise unsupported representations
  need truthful summaries, explicit loading limits, and appropriate whole-file or
  external hand-offs. Never present a clipped/unsupported view as a complete empty
  diff or pretend that binary content supports textual line selection.
- Patch representation, selection mapping, and failure behavior require V01/V03
  validation. If line selection cannot satisfy this contract, return to the
  operator; do not silently downgrade the selected M1 scope to hunk-only staging.

### C3. Commit composition and onboarding

- Provide an assistive, Git-native composer: configured templates, summary/body
  guidance, staged-change context, and durable drafts.
- Guidance is soft. Do not impose a new conventional-commit schema or mandatory
  message style. Repository hooks remain authoritative.
- Only staged content is committed. Git/hook/signing failures preserve the draft
  and useful context, and expose an understandable failure rather than success.
- Open the actual workspace immediately on first use. Orientation, contextual
  help, command discovery, and a clearly disposable demo are optional; no
  mandatory tour or setup wizard blocks entry.
- Documentation and help teach the core inspect/stage/commit path and the new
  explicit-checkout/safety behavior. Existing user conventions are not silently
  replaced without release notes.

### C4. Safety, publication, and recovery

#### Risk tiers and applicability

Routine actions remain fast. Consequential local mutations show exact targets and
consequences and require deliberate acceptance plus their required checkpoint.
Protection applies to every entry point, not just context menus.

Captured repository/worktree, branch, HEAD, target, and relevant content/index
state must be revalidated before acting. Never knowingly apply a stale plan or
selection. Surface conflicts, changed inputs, and uncertain outcomes rather than
claiming an atomic rollback or universal undo that does not exist.

- Protect the detected default branch and explicitly configured protected refs
  from rewriting and deletion. Ordinary commits and non-force pushes remain
  available. Make protection and uncertain discovery visible. This is a local
  safety policy, not replacement server-side enforcement.
- Guided rewriting is for **linear local/unpublished feature history**. Refuse
  rewrites of commits known to be reachable from remote refs and protected
  histories. No force-push is added to Rhizome.
- Local observations cannot prove that a commit has never been shared elsewhere.
  With stale/offline information, show last-verified state and require an explicit
  uncertainty acknowledgment before otherwise eligible local cleanup. Known
  publication and protection blocks still apply.
- Destructive operations must not threaten content beyond supported recovery.
  In particular, refuse a hard reset that would overwrite obstructing untracked
  content when that content cannot be protected under the chosen scope.
- Remote branch deletion retains its exact-destination and OID-lease safeguards
  and an unmistakable consequence/confirmation step. Local recovery cannot promise
  reversal of a remote side effect; do not label it universally undoable.
- A timeout or interrupted remote request can have an uncertain outcome. Do not
  automatically retry a mutation or infer failure solely from missing confirmation.

#### Checkpoint coverage and retention

The recovery journal covers app-initiated history changes and required pre-action
tracked-file/index content. It is not a general backup of all untracked files,
external actions, or remote systems. A commit identifier by itself is not proof
that its referenced content will remain available.

Approved default policy:

| Data | Retention and controls |
| --- | --- |
| Recovery content | **30 days and a 1 GiB total content budget**, configurable; clear retained-data/purge/pin controls |
| Active/pinned recovery | Never silently evicted; exhausted capacity must not silently bypass checkpoint protection |
| Commit/PR drafts | Retain until completed or explicitly cleared; private local content, not diagnostics |
| Diagnostics | **7 days / 10 MiB**, bounded and excluding source/secrets by default |
| Settings/context | Private user-owned persistence, with explicit reset, purge, and per-repository exclusion controls |

A protected action must stop if its required checkpoint cannot be saved. Retention
cleanup must not create a gap in an active operation's recovery. Accounting for
retained Git objects, file content, pins, and cleanup is an explicit validation
question; implementations must not claim a bounded backup while hiding unbounded
retained content or discarding required objects.

Recovery is **recover aside first**: recover history into a recovery branch and
file content into a reviewable copy, preserving newer work. Applying recovered
state to the current checkout is a separate guarded action with explicit choices.
No automatic in-place restoration on restart.

#### Transition rule

There are **no transitional safety exceptions**. Every action enabled in the first
new release obeys the new safety contract. If protection for an existing risky
action is not yet proven, temporarily disable it, explain why, and offer an
explicit external hand-off. Do not retain an unsafe path just for compatibility.
M1 is not required to ship all of M3's recovery UI, but any protected action M1
keeps enabled requires usable, proven recovery for its promised coverage.

### C5. History and investigation

- Default to topology-aware ordering that favors readable parent/child
  relationships; retain an explicit author-time chronological alternative.
  Ordering and Smooth/Compact density are separate concerns.
- Keep unified history as the stable default and explicit graph focus reversible.
  A selected ref is not an implicit history filter.
- Remove the fixed total-history browsing ceiling in favor of bounded, explicit
  **Load more** and jump-to-result controls. Make all available history reachable
  without rendering or eagerly indexing it all at once.
- Search the **complete active scope**, not just rendered rows. Show criteria,
  progress, incompleteness, and scope; provide an explicit way to widen to all refs.
  Include message/author/SHA/ref search and optional path/date criteria.
- Comparison defaults follow intent: branch review compares changes introduced
  since a common base; explicit two-commit comparison uses endpoint difference.
  Always display compared revisions/base and allow mode switching. Missing or
  ambiguous history must not cause an undisclosed semantic substitution.
- Add file history and blame with an easy return to the original investigation
  context. Be honest about Git's available history and shallow/rename limitations.
- Bisect assistance is outside this roadmap. No mandatory eager local index was
  selected; expensive work is progressive and bounded.

### C6. Integration, conflicts, and worktrees

- Guided interactive history plans support reword, reorder, squash/fixup, and drop
  within eligible linear ranges. Review the plan before execution and show progress,
  conflict state, and available Continue/Abort/recovery paths.
- Refuse merge-containing rewrite ranges and explain the external hand-off.
  Neither implicit flattening nor preserve-merge plan editing is included.
- Pull stays fast-forward-only. For divergence, show incoming/outgoing work and
  offer separate deliberate Merge/Rebase paths subject to the same safety rules.
  Do not silently select an integration strategy, stash, publish, or checkout.
- Provide a guided hunk-aware conflict resolver with an external editor/mergetool
  fallback, not a general-purpose embedded code editor. Label sides using actual
  commits/branches; do not rely on ambiguous `ours`/`theirs` shorthand during rebase.
- Resolution choices and closing an editor do not silently stage or continue an
  operation. Require explicit review of the result, Stage, and then Continue.
  Unsupported binary/rename/complex cases receive clear external hand-offs.
- Complete the single-repository worktree lifecycle: create/open, inspect,
  move/remove, and explain lock/prune states, with context-preserving navigation
  and deliberate editor hand-offs.
- Removal is Git-managed, clean-only, and unlocked. Refuse forced removal of
  modified/untracked content. Leave branches intact; branch deletion is a separate
  operation. Preview exact move/remove/prune targets. This is not general filesystem
  deletion or permission to kill shell/editor processes.

### C7. Hosted review

- Enhanced hosted support remains **GitHub.com and Azure DevOps Services**.
  Ordinary Git transport is not restricted to those hosts.
- Preserve existing same-repository PR creation. Add read-only inspection of PR
  changes and review/check status. Comments, approvals, review submission, and
  provider merge actions remain browser workflows.
- Capture provider repository identity and exact source/target commit identities
  for a review. Branch-name equality is not proof of repository or revision identity.
- Review in an isolated snapshot without changing the current checkout or index.
  Explicitly fetch missing objects when necessary; do not automatically create a
  worktree or check out a branch. Local object storage/private review refs may change
  as a result of that explicit fetch; “read-only review” does not mean no local Git
  storage writes whatsoever.
- Keep the reviewed revision stable, announce updates, and require deliberate
  refresh. If a faithful local comparison cannot be obtained, explain the limitation
  and hand off to the browser rather than silently showing a different revision.
- Fork/cross-repository review and creation are excluded. Recognize the distinction
  and offer a labeled browser hand-off, never reinterpret a fork's branch name as
  a branch in the target repository.
- Missing CLI/authentication, unsupported capabilities, partial/error responses,
  and unknown checks are explicit states, not empty-success or passing-check states.
  Provider capability differences must be visible.

### C8. Settings, privacy, and accessibility

- Offer remappable commands and curated preferences, not a programmable UI/plugin
  framework. The complete settings experience is an in-app UI backed by an editable
  user-owned file with validation and reset-to-defaults.
- Global defaults and per-repository overrides live outside the checkout. Preserve
  useful context without turning restoration into mutation replay. Draft identity
  must not confuse different repositories/worktrees or apply old content implicitly.
- Provide selectable light, dark, and high-contrast themes. Use best-effort terminal
  appearance matching, a remembered explicit override, and the current light style
  as the detection fallback. Failure to detect is not an application error or a reason
  to block startup.
- Require complete keyboard access, clear focus, strong contrast, non-color status
  cues, and plain-glyph fallbacks. Do not require a particular font or rely on color
  to communicate safety or selection.
- Screen-reader support is an explicit feasibility investigation, not a current
  support claim or an M1 screen-reader release gate. Report verified capabilities
  and limitations before proposing any stronger commitment.
- Diagnostics stay local. Exports are user-initiated, redacted, and previewable;
  source, credentials, and other sensitive content are excluded by default.
  Drafts/checkpoints can contain source and must not be confused with content-free
  diagnostic defaults. There is no automatic telemetry, crash upload, or sync.

### C9. Responsiveness, freshness, and activity

- Optimize everyday professional repositories with graceful, explicit behavior
  beyond the reference envelope. Prefer useful progressive views over frozen UI,
  eager whole-repository indexing, or silent truncation.
- Automatic fetch/PR metadata refresh is visible and controllable globally/per
  repository. Default: open/resume and approximately **60 seconds while active**,
  pause when hidden/offline, and back off failures. Keep manual refresh available.
- Background work yields to user work. A stalled remote request must not freeze
  local interaction. Show freshness/errors separately from active review contents.
- Never automatically pull, push, checkout, or stash. New PR object fetching and
  update checks retain their separate explicit-action contracts.
- Use a compact activity panel for current/queued work, elapsed time, useful redacted
  output, and meaningful state. Distinguish background reads from mutations.
- Offer cancellation only where interruption has a defined safe outcome. A running
  Git mutation is not generically safe to kill; Continue/Abort and uncertain-result
  reconciliation are different from cancelling a queued/read-only request.
- The precise scheduling implementation and local-refresh mechanism are engineering
  choices to validate, not a mandate to split locks without preserving consistency.

### C10. Git compatibility, installation, and updates

- Preserve **Git 2.36+** and **tmux 3.2+** floors and test minimum/current versions
  across the practical matrix. Raising a floor requires an explicit, justified
  release decision. Managed installation supplies required Python; the baseline
  project requires Python 3.13+.
- macOS, Linux/WSL2, standalone terminals, SSH, and tmux are first-class. Native
  Windows outside WSL and broad legacy-terminal support are not new targets.
- Recognize/explain submodule, LFS, sparse/shallow, and other specialized Git states
  without misrepresenting unsupported behavior. Honor hooks/signing and provide
  safe status/navigation or explicit Git/editor hand-offs. Full native specialized
  management is excluded.
- Retain Git's existing trust model. Configured hooks, filters, signing, and external
  tools can execute commands; Rhizome is not a sandbox for untrusted repositories.
  External editor/mergetool launches are deliberate and visible. Do not introduce
  automatic Rhizome-specific repository scripts or a new general trust-gating system.
- Distribute **version-pinned GitHub releases**, not moving-branch installs or PyPI
  publishing. The product/command remains `rhizome`.
- Offer a small, inspectable, release-pinned bootstrap for machines lacking uv,
  explaining its plan before provisioning uv/runtime into user-owned locations.
  Validate downloads; retain an explicit direct-uv alternative and clear uninstall
  and rollback instructions. Users remain responsible for Git and authentication.
- Do not silently edit Git, tmux, or shell configuration, install with elevated
  privileges, or take ownership of unrelated pre-existing runtimes/tools.
- Update checks and installation are explicit. Show release information and a pinned
  installation command; no background update-server traffic by default, silent
  installation, or restart of a running application.

## 5. Milestones and release boundaries

### M1 — Focused precision core

**Outcome:** review mixed edits, stage exactly the intended hunks/lines, correct the
selection, compose/commit, and retain context without leaving the terminal.

Included:

- Hunk **and line-range** stage/unstage, retaining file/all-file actions.
- Unified diff with word-level highlighting, whitespace reading controls,
  search/context/line navigation, and truthful unsupported/large-file handling.
- Assistive Git-native composition, durable text drafts/core preferences, and
  stable active review context. Existing PR composition is not a new review feature;
  draft persistence must honor the shared draft contract where exposed.
- Explicit checkout and required guards for every enabled mutation. Provide the
  necessary checkpoint/recover-aside support for protected actions kept enabled;
  explain and disable unproven risky paths instead of creating safety exceptions.
- Keyboard/mouse parity, strong baseline accessibility, task-focused 80×24 use,
  responsive feedback, compact activity visibility, and controlled background refresh.
- Version-pinned installation/bootstrap, honest diagnostics, updated release/help
  documentation, and the required compatibility/benchmark/usability evidence.

Deferred from M1:

- Optional split diff; full settings/keymap editing and adaptive themes.
- Topology/focus/search/comparison/file-history enhancements beyond what the core
  needs to function safely and honestly.
- General guided history plans, full conflict resolver, divergence workflow, expanded
  worktree lifecycle, and the complete recovery experience supporting those actions.
- New hosted PR diff/review features.

Core preferences/draft persistence are not an excuse to move the entire later
personalization milestone into M1. Conversely, deferring M3 is not an excuse to
leave enabled destructive actions unprotected. Existing unaffected features remain
available; any temporary capability loss must be explicit.

### M2 — Investigation and personalization

Deliver topology-first/chronological choices, explicit graph focus, full scoped
search, bounded deep-history pagination, intent-aware comparisons, file history and
blame, optional split diff, complete settings/keymap editing, and accessible adaptive
themes. Retain context and M1's safety/latency expectations.

The comparison foundation and revision identity handling enable M4. Screen-reader
feasibility can be investigated alongside presentation work without claiming support
in advance or making it an implicit new interface project.

### M3 — Safe integration and worktree lifecycle

Expand proven recovery coverage and its UI, eligible linear history-plan editing,
explicit divergence resolution, guided conflict resolution, and the safe worktree
lifecycle. Re-enable previously gated actions only after their protection has passed
validation. No force-push, published/protected rewriting, merge-range editing,
implicit flattening, or dirty-worktree force removal is introduced.

### M4 — Hosted read-only review

Using M2's comparison foundation, add faithful isolated same-repository review and
status for the selected providers. Keep captured revisions, explicit missing-data
fetch, no checkout/index disturbance, browser-only review submission/merge, and
fork hand-offs. Provider verification must use separately authorized environments.

## 6. Release acceptance

The gates apply to the actual candidate, not just a successful prototype.

### Integrity and core workflow

M1 has zero known data-integrity, wrong-target, stale-action, or required-recovery
violations, and zero blockers in the core workflow. Release blockers include:

- Wrong-content staging or loss of unrelated index/worktree changes.
- Wrong-repository/ref/path mutation, or applying invalidated selections/plans.
- Lost drafts, misleading success, or undisclosed incomplete/unsupported output.
- Broken promised recovery or execution after a required checkpoint fails.
- Focus traps or inability to complete the core using required input styles at 80×24.

Cover mixed index/worktree state, external changes, failure paths, and unsupported
file fallbacks with real-Git fixtures and UI evidence. Minor cosmetic defects may be
explicitly deferred. No coverage percentage or arbitrary delivery deadline was
selected as a substitute for behavioral evidence.

### Candidate performance contract

Use a recorded reference SSD laptop and reproducible fixtures within this envelope:

| Dimension | Agreed reference envelope/target |
| --- | --- |
| History | 100,000 commits |
| Tracked files | 20,000 |
| Refs | 1,000 |
| Changed files | 200 |
| Ordinary diff | Up to 200 KiB |
| App feedback | p95 at or below 100 ms |
| Usable local workspace | At or below 2 s cold / 1 s warm |
| Ordinary diff presentation | At or below 500 ms |

Record hardware, OS/tool versions, workload construction, and cold/warm definitions.
Do not claim these figures have been met. Network, hooks, and signers can take their
own time, but cannot excuse a frozen UI. A stalled network request must not freeze
local interaction. Explicitly report progress and uncertain outcomes.

Measure CPU/memory and return proposed budgets; **no numerical CPU/memory budgets
have been approved yet**. Their prerequisite is empirical measurement. If the
reference-profile targets prove infeasible, return to the operator with evidence
and options rather than silently changing the fixture or threshold.

### Usability and compatibility

- Observe **five terminal-native developers with Git basics**. At least **four of
  five** independently complete the inspect → hunk/line stage → correct/unstage →
  compose/commit flow. No unresolved observed safety misunderstanding or core blocker
  remains. Cover keyboard-only and mouse use, 80×24, and tmux/SSH cases.
- Exercise explicit checkout and enabled recovery or blocked-action explanations
  separately. Fix and retest relevant blockers. No numeric task-time limit was chosen.
- Use a named practical matrix: automated macOS/Linux checks, declared minimum/current
  Git/tmux across jobs, macOS ARM64/x86_64 install/runtime coverage, real macOS/Linux/
  WSL2 Windows Terminal smoke, and SSH/tmux paths.
- Test 80×24 and a wide layout, clipboard fallbacks, and no-special-font operation.
  Record exact versions and exceptions. Do not advertise an untested combination as
  verified or quietly weaken a first-class platform to best-effort.

Later milestones inherit these integrity, honesty, privacy, and compatibility gates,
with feature-specific contracts and evidence from their validation briefs. The full
cross-product of every terminal/OS/tool version is not required.

## 7. Engineering latitude and integration seams

Targeted, contract-driven refactoring is approved as the eventual implementation
approach, **not as authorization to implement now**. Keep complexity in deep modules
with small interfaces that include invariants, failure modes, ordering requirements,
and performance behavior. Do not merely scatter existing complexity into pass-through
wrappers or pursue a language/framework rewrite without new evidence and approval.

| Existing area | Useful concentration of future responsibility |
| --- | --- |
| `git.py`, `models.py`, `context_actions.py` | Exact identities, mutation planning/applicability, Git execution, stale-state validation, typed results |
| `app.py`, dialogs and diff presentation | User intent, focus/selection, stable views, clear progress/errors; no duplicated patch or recovery policy |
| Patch handling | Canonical selection-to-index semantics shared by unified/split presentation; interface-level real-Git tests |
| Scheduling | User/background priority and safe cancellation without weakening mutation consistency or pretending threads can undo Git |
| Durable state/recovery | Private persistence, identity, retention, failure-safe admission/cleanup, and recover-aside behavior |
| `graph.py`, sidebar/file trees | Pure rendering/navigation behavior separate from Git I/O, with visible-geometry and scope tests |
| `pull_requests.py` and dialogs | Provider adapters, immutable review identities, capability/error states, CLI-owned authentication |
| `cli.py`, `tmux.py`, distribution | Explicit launch/install/update behavior, process preservation, version/platform diagnostics |

These are responsibility recommendations, not prescribed new filenames, public method
signatures, database formats, or a plugin architecture. Exercise callers and tests
through the same interfaces. Retain real-repository verification where Git semantics
matter; mocks alone cannot prove patch fidelity, recovery, or platform behavior.

## 8. Scope exclusions and empirical hand-offs

### Excluded product branches

- AI assistance/platforms, plugin ecosystems, arbitrary workflow scripting,
  multi-repository orchestration, collaboration/cloud services, mandatory accounts,
  and desktop/web companion interfaces.
- Native Windows outside WSL, broad legacy-terminal expansion, and lower Git/tmux
  floors than the existing support promise.
- Force-push; known-published/protected rewriting; merge-containing history plans,
  implicit flattening, generic undo/redo of external or remote actions, automatic
  stash/pull/push/checkout, and dirty-worktree force removal.
- Full embedded code/three-way editing; bisect assistance; full native submodule,
  LFS, or sparse-checkout management.
- PR comments/approvals/submission/merge, fork/cross-repository review/creation,
  GitLab/enterprise/self-hosted provider expansion, and app-owned credential storage.
- Telemetry, automatic crash uploads, silent updates, PyPI publishing, speculative
  architectural modernization, or a new repository-trust sandbox.

### Empirical work, not unresolved ready preferences

The [validation queue](validation-plan.md#validation-queue) makes each hand-off
bounded by a question, fixtures, method, deliverable, and pass/fail/stop conditions.
First-release-critical validation comes first; later-feature investigations occur
just before their milestones.

First: patch/stale-state correctness; checkpoint/recovery failures; narrow-terminal
selection; reference performance; bootstrap and platform compatibility; user pilot.
Later: graph/search scaling; history/conflict/worktree workflows; provider snapshot
fidelity; terminal appearance detection; screen-reader feasibility.

Storage accounting, numerical CPU/memory budgets, exact measured environment versions,
and screen-reader capability claims await evidence. They are not silently settled
by this document. No validation brief has been executed as part of this authorization.
Staffing, execution budgets, dates, releases, publishing, and implementation execution
are not authorized by the documentation step.

## 9. Documentation deliverables and change control

The confirmed output consists only of:

1. `docs/design/world-class-rhizome.md` — approved scope, decision tree, behavioral
   contracts, exclusions, phased roadmap, acceptance criteria, and traceability.
2. `docs/design/validation-plan.md` — bounded empirical briefs, dependencies,
   evidence requirements, and stop/approval rules.

Keep implementation status and evidence separate from design approval. Do not mark
work implemented or a gate passed without recorded results. When later work reveals
an incompatible behavior/privacy/support/scope/target trade-off, return the specific
new decision to the operator and update both documents consistently after approval.
No ledger task, code change, prototype, benchmark, commit, or release is implied by
creating these files.

## 10. Decision register

The following records the operator's choices, not unaccepted recommendations.
Question numbers refer to the original interview. Contract sections above expand
these answers without changing their selected direction.

| Question | Approved decision |
| --- | --- |
| Q1 | Primary audience: terminal-native developers. |
| Q2 | Local-first terminal workspace; no mandatory hosted service or AI dependency. |
| Q3 | Include daily Git experience, trust/engineering, and adoption/ecosystem; exclude product expansion. |
| Q4 | Staged roadmap with a bounded first release. |
| Q5 | Defining advantage: exceptional daily Git flow. |
| Q6 | Risk-tiered protection, not confirmation for every mutation. |
| Q7 | Keyboard-first, mouse-complete. |
| Q8 | Everyday professional repositories, with graceful extremes. |
| Q9 | macOS, Linux/WSL2, and tmux are first-class. |
| Q10 | One-command managed-runtime installation goal; users own Git/authentication. |
| Q11 | Narrow scope with quality gates, not an arbitrary delivery deadline. |
| Q12 | Evolve the recognizable existing workspace. |
| Q13 | Hunk and selected-line staging/unstaging. |
| Q14 | Unified default with optional split diff. |
| Q15 | Guided interactive history editing. |
| Q16 | Guided conflict resolver plus external-editor fallback. |
| Q17 | Selection remains non-filtering; explicit reversible graph focus. |
| Q18 | Topology-first ordering with chronological option. |
| Q19 | Ref/range comparison, complete-history search, file history/blame; no bisect. |
| Q20 | Complete safe single-repository worktree lifecycle. |
| Q21 | Read-only in-terminal PR review; review submission/merge stay external. |
| Q22 | Automatic, visible, controllable remote refresh. |
| Q23 | Recovery checkpoints for app-initiated history and tracked-file destruction. |
| Q24 | Progressive, bounded live views rather than an eager index or waiting for completeness. |
| Q25 | Persist preferences, repository context, and drafts; never replay mutations. |
| Q26 | Remappable commands plus curated settings, not programmable extensibility. |
| Q27 | Strong keyboard/visual baseline plus separate screen-reader investigation. |
| Q28 | Versioned GitHub installs, not package-registry publishing. |
| Q29 | Workspace first, guidance optional. |
| Q30 | Local diagnostics and explicit previewable export; no telemetry. |
| Q31 | Modern 80×24 terminals with SSH/tmux and no special font. |
| Q32 | Safe specialized-Git interoperability with explicit hand-offs. |
| Q33 | Select a contiguous range, then explicitly apply. |
| Q34 | Word-level highlighting, whitespace controls, search/context/navigation, honest large/binary/generated views. |
| Q35 | Assistive Git-native composer without new mandatory message conventions. |
| Q36 | Local/unpublished cleanup only; no force-push; disclose publication uncertainty. |
| Q37 | Linear rewrite ranges only; no implicit merge flattening. |
| Q38 | Protect default/configured branches from rewriting/deletion; ordinary commits/pushes remain. |
| Q39 | Explicit conflict-result review and Stage, then separate Continue. |
| Q40 | Git-native trust with deliberate external-tool hand-offs; no new trust-gating system. |
| Q41 | Intent-aware comparison defaults, visibly labeled and switchable. |
| Q42 | Search complete active scope; explicitly widen to all refs. |
| Q43 | Clean-only worktree removal, no force, branches retained. |
| Q44 | GitHub.com and Azure DevOps Services only for enhanced hosted support. |
| Q45 | Balanced local retention: checkpoints 30 days/1 GiB total; active/pinned protection; drafts until complete/cleared; diagnostics 7 days/10 MiB; fail closed on required backup failure. |
| Q46 | Settings UI plus editable user-owned file; global/per-repository settings outside checkout. |
| Q47 | Extend familiar current keyboard conventions, preserving text entry. |
| Q48 | Adaptive light/dark/high-contrast appearance with override and current-light fallback. |
| Q49 | Task-focused narrow workspace; familiar panes when roomy. |
| Q50 | Balanced active-only remote refresh: open/resume and about 60 seconds; hidden/offline pause and failure backoff. |
| Q51 | Explicit update check and version-pinned installation; no silent update/restart. |
| Q52 | Automated tests, compatibility, benchmark evidence, and observed user completion gate releases. |
| Q53 | Explicit Checkout only; no double-click checkout. |
| Q54 | Fast-forward-only Pull plus deliberate guided divergence resolution. |
| Q55 | Guarded, recovery-bounded destruction; refuse unsupported data-loss cases; separate remote-deletion warning. |
| Q56 | Recover aside first, then separately guard application to current work. |
| Q57 | Offline local cleanup allowed with explicit uncertainty acknowledgment, retaining publication/protection blocks. |
| Q58 | Isolated captured PR snapshot, explicit missing-object fetch, no automatic checkout/worktree. |
| Q59 | Same-repository PR review first; forks receive browser hand-offs. |
| Q60 | Stable active review context plus changed-state notice. |
| Q61 | Explicit bounded pagination makes full available history reachable. |
| Q62 | Compact activity panel with honest progress and safe cancellation boundaries. |
| Q63 | Inspectable managed bootstrap with explicit direct-uv alternative. |
| Q64 | Maintain declared Git 2.36+/tmux 3.2+ floors; justified approval before raising them. |
| Q65 | First outcome: precise, confident commit crafting. |
| Q66 | No transitional safety exceptions; unproven risky paths may be disabled. |
| Q67 | Targeted contract-driven refactoring; internal choices delegated, material contract changes return to operator. |
| Q68 | After final confirmation, specification/staged plan/validation briefs only; no execution. |
| Q69 | Focused precision core, including both hunks and lines, rather than hunk-first or personalization-expanded M1. |
| Q70 | Investigation/personalization → integration/worktrees → hosted review after precision core. |
| Q71 | Two future-facing design documents at the paths listed in section 9. |
| Q72 | Safety and core-workflow defect gate; minor cosmetic issues can be explicitly deferred. |
| Q73 | Explicit candidate reference-profile latency targets; measure CPU/memory and return proposed budgets. |
| Q74 | Five-user task-based pilot; at least 4/5 independent core completion, no unresolved safety misunderstandings. |
| Q75 | Named practical compatibility matrix, not exhaustive cross-product or quiet best-effort downgrade. |
| Q76 | First-release-critical validation first; later-feature investigations just-in-time; write briefs now, do not execute them. |
