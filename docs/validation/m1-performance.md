# M1 performance evidence (V04)

**Status:** the measured **1.51 s startup is accepted for M1 by the operator**.
The remaining V04 evidence is incomplete and was subsequently **explicitly deferred
for full M1 release acceptance**, not marked passed; see the
[acceptance decision](m1-release-acceptance.md).
The measurements below used `0a1ea6638846fb8ebed382b27ccdf066945e4700` plus the
then-uncommitted M1 changes. They are local measurements, not hosted-CI or full release acceptance.

## Operator acceptance — 2026-10-29

The operator explicitly accepted this measured startup time while authorizing the
remaining formatting cleanup. The **1.509 s maximum** on the recorded five-run
reference profile is therefore no longer an M1 blocker. This is a scoped acceptance
of the measured candidate, not a new general latency threshold. The original 1 s
target, raw measurements and benchmark failure flags remain unchanged for traceability.
That initial startup decision did not waive cache-cold startup, resource/slow-provider
evidence or other release gates. The later full-M1 acceptance explicitly deferred
the named missing measurements; it did not change the original raw results,
thresholds or proposed resource budgets.

## Latest local follow-up (17:08 UTC)

Raw evidence: [five-sample follow-up](../../.ledger/202610280853-world-class-rhizome-execution/m1-performance-candidate.json).
The fixture, thresholds, five-process sampling and headless protocol below are unchanged.
The command completed in **30.5 seconds**, under a 180-second command limit.

| Measurement (seconds) | min | median | p95 / max | Assessment |
| --- | ---: | ---: | ---: | --- |
| `Repository.snapshot(300)` | 0.834 | 0.842 | 0.857 | Diagnostic |
| Process launch → usable app | 1.426 | 1.440 | 1.509 | **Accepted M1 exception**; original 1 s target not met |
| Same-process refresh → paint | 0.926 | 0.936 | 0.942 | Meets 1 s in these samples |
| Feedback → paint proxy | 0.0199 | 0.0206 | 0.0215 | Meets p95 ≤100 ms |
| Large diff → paint proxy | 0.308 | 0.312 | 0.319 | Meets ≤500 ms |

The causal change removed an immediate duplicate local snapshot after open/manual refresh.
Remote scheduling reuses the snapshot already presented. Periodic local polling and the
**post-fetch** snapshot/read/apply remain; a real bare-remote test asserts the fetched ref
and commit appear in the sidebar/history without changing local HEAD. A UI regression
first reproduced two scans for one refresh and now asserts one.

A [targeted profile](../../.ledger/202610280853-world-class-rhizome-execution/m1-startup-profile.json)
measured a 0.921 s snapshot: the complete author-date history-index read took 0.517 s,
status 0.101 s, and ref listing 0.039 s. No history-order semantic change or new
persistent repository cache was introduced. Further startup optimization is not
required to close this measured M1 gap after operator acceptance. Cache-cold startup
remains unestablished.

Peak RSS was 98.1 MiB; startup used at most 0.727 process CPU seconds and diff at most
0.195 seconds. The original resource budgets below remain proposals. The benchmark waits
for all workers to become idle before its paint barrier, so background polling can inflate
these endpoints; it does not isolate first useful paint from unrelated background work.
Real-terminal latency, slow-provider responsiveness and long-running resource behavior
are not established by these measurements; further validation is explicitly deferred
for M1 under the later acceptance decision.

## Original baseline protocol and evidence

Historical raw evidence: [m1-performance-20261029.json](../../.ledger/202610280853-world-class-rhizome-execution/m1-performance-20261029.json).

### Environment and protocol

- MacBook Pro `Mac14,9`, Apple M2 Pro (10 logical CPUs), 16 GiB RAM, internal solid-state APFS.
- macOS 26.6.2 (Darwin 25.6.0, arm64), Git 2.50.1 (Apple Git-155), Python 3.13.0, Textual 1.0.0.
- Invoking terminal: Ghostty (`TERM=xterm-ghostty`, true color), not tmux. App measurements used Textual's **headless 170×48 driver**, not a real terminal paint clock.
- `HOME`, XDG directories, Rhizome state, Git global/system configuration, credential variables, and prompts were isolated under one temporary root. The fixture had no remotes and was deleted. The operator checkout was read only.
- Command:

  ```sh
  uv run python scripts/benchmark_m1.py --samples 5 --warm-runs 1 \
    --output .ledger/202610280853-world-class-rhizome-execution/m1-performance-20261029.json
  ```

Five independent fresh Python processes provide nearest-rank p95 distributions. Fixture verification necessarily warmed filesystem caches. “Process-cold/cache-warm” means a new Python process after that verification. No OS caches were dropped or controlled, so OS-cache-cold evidence is **not established**. Same-process warm is one explicit refresh after startup. Non-p95 targets require every sample to meet the threshold. Feedback and diff action-to-next-paint timings call `Pilot.pause(0)` without deliberate sleeps; they are labeled headless proxies.

An earlier `--samples 20 --warm-runs 5` attempt hit its 3,600-second command bound after 20 repository and 8 app samples and emitted no JSON. It is a failed execution, not part of the distributions. Reducing repeated refreshes, not the fixture envelope or targets, produced the completed bounded run above.

## Verified workload

`git fast-import` made a linear 100,000-commit history. Every commit reuses one immutable tree containing 19,999 files and changes only `history/tick.txt`; this avoids rebuilding a 20,000-entry tree per commit. The checkout then modifies 200 tracked files. `tracked/f00000.txt` is ordinary 80-column text.

| Dimension | Verified |
| --- | ---: |
| Commits | 100,000 |
| Tracked files | 20,000 |
| Refs | 1,000 |
| Changed files | 200 |
| Large ordinary repository diff | 203,371 bytes (198.60 KiB) |
| Canonical diff presented by the app | 203,431 bytes, 2,514 lines, in every sample |
| Fixture remotes | 0 |
| Fixture construction | 8.24 s wall; immutable tree OID recorded in raw evidence |

## Results

Times are seconds. The full distributions and raw samples are in the JSON evidence.

| Measurement | min | median | p95 | max | Assessment |
| --- | ---: | ---: | ---: | ---: | --- |
| Public `Repository.snapshot(300)` | 0.793 | 0.805 | 0.830 | 0.830 | Diagnostic |
| Public `Repository.diff` | 0.114 | 0.117 | 0.118 | 0.118 | Diagnostic |
| Process launch → usable RhizomeApp | 2.186 | 2.221 | 2.269 | 2.269 | **Fails 1 s warm target** |
| Same-process app refresh → paint | 1.745 | 1.772 | 2.647 | 2.647 | **Fails 1 s warm target** |
| Feedback action → next paint proxy | 0.0051 | 0.0052 | 0.0072 | 0.0072 | **Meets p95 ≤100 ms** |
| 198.6 KiB diff action → next paint proxy | 0.292 | 0.298 | 0.316 | 0.316 | **Meets ≤500 ms** |

The ≤2 s cold-workspace target is **not established** because OS-cache-cold conditions were not measured. Process-cold/cache-warm startup also exceeded 2 s, but that does not substitute for an honest cache-cold result.

### CPU and memory

- App startup used 0.984–1.008 process CPU seconds (about 48–49% of one core-equivalent over wall time).
- The large diff used 0.173–0.179 process CPU seconds (about 57–59% of one core-equivalent).
- Warm refresh used 0.688–1.029 process CPU seconds (about 39% of one core-equivalent).
- Peak app RSS was 89.6–92.6 MiB. Current RSS changed by 0.38–4.06 MiB after one repeated refresh; one refresh is not a leak/soak test.

These are process CPU seconds and RSS bytes, not host-wide CPU, energy, allocation, or I/O counters. A provisional evidence-derived budget proposal for this exact headless reference profile is **128 MiB peak RSS**, **1.2 process CPU seconds for startup**, **1.2 for refresh**, and **0.25 for a 200 KiB diff**. These numbers include margin over observed maxima; they are proposals, not approved product budgets or cross-platform claims. No idle/background CPU budget is proposed because it was not measured.

## Bottlenecks and gaps

The direct snapshot max was 0.830 s, while internal app startup was 2.053–2.097 s and process launch added more. The root area is therefore the app startup/refresh path around Git snapshot work, snapshot application/rendering, and process/import overhead—not fixture generation or `Repository` construction. Warm refresh alone remained 1.745–2.647 s; the high sample also used 1.029 CPU seconds versus about 0.69 in the other samples, showing extra work, although this protocol does not attribute it more narrowly. No production optimization had been attempted at the time of this original baseline.

The large diff's public Git read was 0.114–0.118 s and full app parse/presentation 0.292–0.316 s. The app layer added roughly 0.18 s but remained within target in all five samples.

This run does not establish real-terminal paint latency, OS-cache-cold startup, slow/stalled remotes, hook/signer responsiveness, background idle usage, long-run memory growth, merge/skew-date stress, binary/truncation stress, or loaded-system behavior. Those remain unverified V04 cells, explicitly deferred for M1; no target or envelope
was lowered.
