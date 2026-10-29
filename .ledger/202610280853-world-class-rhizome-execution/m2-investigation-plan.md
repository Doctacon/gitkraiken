## Milestone guard

Treat this as the next M2 implementation plan after the in-progress M1 integration reaches its gates. Do not mark M1 or M2 complete from this planning work.

## Settled product constraints

These are already decided by C5/M2/V08:

- Topology-aware history is the default; author-time chronology remains explicit.
- Ordering is independent of Smooth/Compact rendering.
- Unified history remains default. Selecting a ref does not filter; Focus is explicit and reversible.
- History has bounded pages but no 5,000-commit total ceiling.
- Search covers the complete captured scope, not only rendered rows, and can explicitly widen from focus to all refs.
- Branch review means unique merge-base → right/source; explicit commit comparison means left endpoint → right endpoint.
- Missing or ambiguous comparison history must not silently switch semantics.
- File history and blame must disclose shallow and rename limitations and preserve return context.
- No eager durable index, bisect feature, or speculative scheduling/framework work.
- The 100,000-commit target requires V04/V08 evidence; it is not currently proven.

## Smallest public `InvestigationService` contract

Construct `InvestigationService(repository: Repository)`. Keep it synchronous; `RhizomeApp` schedules calls off the UI thread.

Public queries:

1. `capture(roots=()) -> InvestigationScope`
   - Empty roots captures all commit-bearing refs plus detached `HEAD`.
   - Explicit roots accept only `HEAD`, full `refs/...` names, or full OIDs—no ambiguous short DWIM names.
   - Multiple exact roots support comparisons and future M4 snapshots.

2. `status(scope) -> ScopeState`
   - Supports passive changed-state polling without recapturing or replacing the active view.

3. `history(scope, order="topology", cursor=None, limit=300) -> HistoryPage`

4. `search(scope, query, order="topology", cursor=None, scan_limit=1000) -> SearchPage`
   - `scan_limit` bounds commits examined, not matches returned.

5. `compare(scope, left_oid, right_oid, intent) -> ComparisonResult`
   - Intents are `"branch-review"` and `"endpoints"`.

6. `file_history(scope, start_oid, path, cursor=None, limit=300, follow_renames=True) -> FileHistoryPage`

7. `blame(scope, start_oid, path, cursor=None, limit=300) -> BlamePage`

All limits accept non-boolean integers from 1 through 1,000. Cursors are opaque strings, valid only for the originating service instance, scope, query, order, path, and mode. They are not persisted across restart or recapture.

### Immutable return values

Add frozen dataclasses and literal aliases to root-owned `models.py`:

- `CapturedRef(full_name, oid)`
- `InvestigationScope`
  - repository identity, scope ID, kind, label, exact root OIDs, captured refs, relevant moving ref bindings, captured HEAD/branch, and shallow-state fingerprint.
- `ScopeState(current, available, reason)`
- `HistoryPage`
  - `scope_id`, `order`, `commits`, `next_cursor`, `has_more`, `omitted_before`, `state`.
- `SearchQuery`
  - `text`, optional literal `path`, optional inclusive timezone-aware `authored_from`/`authored_through`.
- `SearchHit`
  - `commit`, matched fields, captured matching ref names, and a history-compatible cursor for bounded jump-to-result.
- `SearchPage`
  - exact query/order, hits, page and cumulative examined counts, `next_cursor`, `complete`, `state`.
- `ComparisonResult`
  - intent, requested left/right OIDs, merge-base OID when applicable, actual diff-from/diff-to OIDs, files, stat, bounded patch, `truncated`, `state`.
- `FileHistoryEntry`
  - commit, actual path at that revision, previous path for rename/copy, and one-letter change status.
- `FileHistoryPage`
  - revision/path/follow mode, entries, cursor/completeness, limitations, `state`.
- `BlameLine`
  - commit OID, original path/line, final line, author/email/date, text, and boundary marker.
- `BlamePage`
  - revision/path, lines, cursor/completeness, limitations, `state`.

Use tuples throughout. Limitations should use explicit values such as `shallow-history` and `rename-heuristic`.

### Search semantics

Freeze these initial internal choices:

- `text` is a case-insensitive substring over the full commit message, author name/email, full SHA, and captured short/full ref names.
- Text fields are ORed; path and date filters are ANDed with that result.
- Date filtering uses author time, matching the displayed chronological field.
- Path matching is literal and considers both sides of rename/copy records, but does not itself promise rename following.
- At least one criterion is required.
- A search is complete only when `SearchPage.complete` is true. Empty intermediate pages may still have a continuation cursor.
- A search hit’s history cursor loads a bounded window starting at that result; it must not load every preceding commit.

## Repository adapter and root integration

### `git.py`

All Git processes, safe argv construction, streaming, output limits, and parsing remain in `Repository`. Do not expose a generic raw-argv runner.

Root-owned adapter hooks should cover:

- Capturing exact refs, roots, repository identity, and shallow state.
- Reading ordered bounded history records.
- Searching a bounded traversal batch, including full-message/path checks without retaining whole-repository output.
- Scope availability/staleness checks.
- Enumerating all merge bases.
- Producing a bounded two-tree diff.
- Reading paged `git log --follow` file history.
- Reading paged porcelain blame.

Implementation guidance:

- Topology mode: exact captured roots with `git log --topo-order`, deterministic root ordering, and `limit + 1`.
- Author-time mode: preserve the current global author-epoch ordering and traversal-position tie-breaker. Stream the lightweight timestamp/OID index and retain only the bounded heap needed for the requested page.
- Reuse `_resolve_commit`, `_path`, `_refs`, `_git_diff`, `_bounded_diff`, literal pathspec handling, redaction, and existing timeouts.
- Branch review uses `git merge-base --all`. Continue only for exactly one base, then diff that base → right/source.
- Endpoint mode always diffs left → right and needs no common ancestor.
- Use `git log --follow --name-status` for file history and clearly label its heuristic nature.
- Use porcelain blame with bounded line and byte output.
- Add `Repository.snapshot(include_history=False)` while preserving its existing default until App migration. M2 polling must stop rescanning legacy history every two seconds.

After App cutover, remove `app.py`’s cumulative `self.limit`, `min(5000, ...)`, and `_MAX_HISTORY` dependence from the active browsing path.

### `graph.py`

No ordering logic belongs here. `render_history` already preserves supplied order, keeps density separate, and names parents appearing above chronological children. Initially it should require no production change; extend its tests only if bounded middle windows expose a truthful-boundary defect.

### `app.py`

Root integration should:

- Create one `InvestigationService` per opened `Repository`.
- Capture unified scope once; retain it until explicit refresh/reselection.
- Store ordering separately from `graph_style`.
- Keep sidebar selection non-filtering; only explicit Focus captures a focused scope.
- Preserve the prior scope, cursor window, selected OID, search, and scroll for Exit Focus and file-history/blame Back.
- Render a bounded page window rather than accumulating 100,000 rows. Start with 300-row pages and at most three adjacent pages; tune only from V08 evidence.
- Use `HistoryPage.omitted_before` and captions to disclose a jump/deep window.
- Debounce search input, run bounded batches, publish examined counts and incomplete/complete state, and cancel safely by stopping between batches and ignoring stale worker tokens.
- Stop automatic paging/search when scope state becomes stale; retain the captured result and offer explicit refresh.
- Always display comparison intent, exact endpoints, and merge base. Mode switching reruns against the same exact OIDs.
- Keep comparison, file history, and blame as transient investigation views with an explicit return path.
- Exercise all visible behavior through Textual Pilot; do not expose private worker/parser seams to tests.

## Error and staleness rules

- Invalid limits, order, criteria, or mismatched/tampered cursors: `ValueError`.
- Invalid paths/revisions, Git failures, unavailable objects, ambiguous bases, and unsupported blame content: safe `GitError`.
- Ref movement never retargets a captured cursor.
- A relevant ref or shallow-boundary change yields `ScopeState(current=False, available=True)`; old exact results may remain visible but must be labeled changed.
- Exact-OID focus does not become stale merely because an unrelated branch moves.
- Repository replacement, a foreign scope, or missing/pruned captured objects is unavailable and must fail rather than substitute current `--all`.
- Missing or multiple branch-review bases refuse that intent and explicitly offer endpoint comparison.
- Conservatively refuse branch-review semantics in shallow repositories for the first increment; endpoint comparison remains available.
- File history and blame may return locally available data with `shallow-history`; never describe that as complete repository history.
- Binary or oversized blame must be explicitly unavailable, not returned as an empty result.
- Timeouts preserve the previous view and show an incomplete/error state. Read queries are never automatically retried as though their scope were unchanged.

## Sequential real-Git test order

One independent builder should own `investigation.py` and a new `test_investigation.py`, using disposable real repositories and no mocks of Git:

1. Capture empty, unified, detached, focused, multiple-root, exact-ref, and invalid ambiguous scopes.
2. Linear topology pagination with tiny pages; verify stable continuation and no total cap.
3. Merge, octopus, and disconnected topology against actual parent OIDs.
4. Skew author/committer dates; prove topology and author-time differ while Smooth/Compact does not reorder.
5. Move a captured ref between pages; prove old roots remain exact and results become stale rather than mixed.
6. Search beyond the rendered/first page across body, author/email, SHA, ref, literal path, and author-date filters; verify progress, empty incomplete batches, completion, and jump cursor.
7. Construct divergence where merge-base and endpoint patches differ; verify all returned OIDs and mode switching.
8. Refuse criss-cross multiple bases, disconnected bases, missing objects, and shallow branch review.
9. File history across renames and literal special paths; compare entries with Git’s oracle and paginate.
10. Blame paged lines, rename provenance, literal paths, boundaries, shallow state, and unsupported binary input.
11. Build a fast `commit-tree` chain beyond 5,000 for ordinary test coverage of the removed ceiling.
12. Root-owned Pilot tests: order/style independence, explicit focus/back, bounded older/newer navigation, progressive search cancellation/staleness, comparison labels, and file-history/blame return context.
13. Run the separately recorded 100,000-commit V04/V08 workload last; do not place it in the ordinary unit suite.

## 100,000-commit posture

The initial implementation should rely on Git traversal, bounded output, streamed parsing, and bounded UI windows. It should not create a durable index, preload all commits, or add a cache/scheduler framework.

A bounded heap limits memory, not latency; `--skip` may still traverse prefixes. Measure cold/warm first page, deep page, result jump, topology search, and author-time search. If author-time scans or repeated deep pages miss the approved responsiveness target, return evidence and options. An on-demand persistent or whole-scope index would change the approved storage/indexing posture and is not an automatic optimization.

The genuinely unresolved items are empirical: page/search batch sizes, whether exact author-time scans meet 100k latency, and whether a different indexing policy is required. The history/search/comparison semantics themselves are settled.

### Critical Files for Implementation

- /Users/crlough/Code/personal/rhizome/src/rhizome/models.py - Root-owned frozen scope, page, comparison, file-history, and blame values.
- /Users/crlough/Code/personal/rhizome/src/rhizome/git.py - Sole Git subprocess adapter and bounded query parsing.
- /Users/crlough/Code/personal/rhizome/src/rhizome/investigation.py - New independently owned public `InvestigationService` module.
- /Users/crlough/Code/personal/rhizome/src/rhizome/app.py - Root-owned scope lifecycle, scheduling, bounded windows, and return-context integration.
- /Users/crlough/Code/personal/rhizome/tests/test_investigation.py - New sequential real-Git contract tests owned with the service.