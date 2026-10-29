# Hosted result identity regression

## Signal

The second full discovery run (`integration-test-run-2.txt`) finished **1 failed,
586 passed in 691.80 s**. The unchanged failure was
`test_changed_remote_during_listing_discards_old_results`. It reproduced alone:
**1 failed in 3.91 s**. This was a real stale-context bug, not a policy migration.

## Diagnosis

Ranked hypotheses were: an explicit request dropped during polling; a follow-up
refresh republishing old data; or an overlapping old response applied last.
Post-mortem debugger inspection of the disposable fixture showed:

- Git and the accepted snapshot both named `https://github.com/org/other`.
- The foreground status was already `Remotes changed while loading PRs. Load again
  for the new destinations.`
- A later background fetch diagnostic was present, and no worker remained active.

The dropped-request hypothesis was false. The snapshot boundary already cleared
PR caches/maps and rebuilt the sidebar on changed remotes. A subsequent automatic
read could repopulate it because validation compared before/after snapshots but
never checked the actual hosted identity carried by the listing result. The legacy
fixture returns the old hosted result, exposing that missing boundary check.

## Independent real-Git proof

Added `test_pr_results_are_bound_to_the_captured_remote_even_after_an_aba_change`
through RhizomeApp/Pilot. Git remains real. Only the external subprocess boundary
is controlled: after capturing remote A, the Git URL changes to B before the hosted
read; the fake `gh` response restores A before completion. Thus both snapshots name
A, while the actual requested API and listing belong to B. The rendered tree
incorrectly contained B's PR title: **1 failed in 2.13 s**. No private provider was
contacted or mutated.

## Fix and verification

Both automatic and explicit result paths now verify the current snapshot context
and each listing's remote name plus canonical hosted identity against the captured
scope. Snapshot-boundary clearing is retained. A mismatched result is discarded;
an unchanged-but-mismatched automatic context backs off rather than tight-looping.

The original regression remains unchanged. It and the new ABA regression passed
together: **2 passed in 5.50 s**. Ruff passed. `task-10` then finished **2 failed,
25 passed in 77.75 s**: two legacy fixtures returned GitHub listings for a file-only
remote. The fixture now declares the actual hosted identity while routing Git
fetches to the disposable bare repository. Existing cache assertions were retained;
initial cache/selection assertions were strengthened to prevent vacuous success.
`task-11` passed the **27-test** refresh/sidebar/background batch.

Explicit hosted reads now run outside the local read queue and global UI busy
state as well. A stalled disposable `gh` executable proves local diff inspection
remains available. The prior modal test now requires local non-busy state and its
own disabled load control, rather than obsolete global blocking. `task-13` passed
**36 tests in 103.95 s** across refresh, sidebar, background inspection, refresh
workspace, draft state and operation workspace files. That run predates the later
ordinary-commit gate and is not current whole-candidate acceptance evidence.

No production debug logging or temporary instrumentation was added. The debugger
used only public fake repository URLs and transient test data.
