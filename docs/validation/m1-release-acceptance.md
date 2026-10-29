# M1 full-release acceptance

**Decision: accepted for a full M1 release on 2026-10-29.** This is not a preview
classification. The operator explicitly selected **“Accept full M1 readiness”**:
passing CI and real macOS/Linux lifecycles are sufficient for M1, with the named
missing validation below recorded as deferred, not passed. No M1 features were
removed and no Git safety guarantees were relaxed.

Validated implementation: `19086a362dd9ac48bdffdd461816add47ca7c6ed` on
`feat/hardening-pass`. Subsequent documentation-only acceptance records do not
change that implementation. Acceptance does **not** merge, tag, publish a GitHub
release, or alter a live user installation; those remain separate actions.

## Evidence supporting acceptance

[Hosted CI on the validated implementation](https://github.com/Doctacon/rhizome/actions/runs/40954621009)
completed all **seven jobs successfully**:

- Linux complete suite: **746 passed**, including installer regressions.
- Linux current-Git and macOS ARM64 non-installer checks and distribution builds.
- Linux and macOS current Git/tmux integration journeys.
- Checksum-verified **Git 2.36.0 / tmux 3.2** tests and integration journeys.
- Reduced benchmark-tooling smoke, explicitly not reference performance acceptance.

Real pinned install → update → rollback → uninstall also passes on:

| Environment | Result and scope |
| --- | --- |
| macOS 26.6.2 ARM64 | Complete real lifecycle in a disposable home |
| Ubuntu 24.04 ARM64 | Complete real lifecycle inside the already-running Linux VM |
| Ubuntu 24.04 AMD64 | Complete real lifecycle using x86_64 binaries under existing emulation; not native x86_64 performance evidence |

These checks use actual downloads, private **uv 0.11.7**, managed **Python 3.13.13**,
paths containing spaces, exact current/previous revision assertions and CLI startup.
They preserve synthetic application state, shell/Git/tmux configuration and an
unrelated tool. The downloaded bootstrap is byte-equal to its reviewed Git object.
Successful installation sandboxes and containers were removed without changing
VM/daemon settings or unrelated resources.

The Linux bind-mount failure was a harness transport problem, resolved with
Docker's copy API. A subsequent AMD64 probe separated stdout from recorded stderr
so expected emulation diagnostics did not corrupt a version assertion. Original
failures remain retained; neither correction changes the application or installer.

The measured **1.509 s startup maximum** was separately accepted earlier. Original
benchmark thresholds, measurements and failure flags remain unchanged in the
[performance record](m1-performance.md).

## Explicit M1 validation deferrals

The operator accepted full M1 readiness while deferring these still-unrun items:

- Native Intel macOS installation/runtime coverage.
- WSL2, real SSH, clipboard and named human-visible terminal checks.
- The five-person usability pilot and its independent-completion criterion.
- Remaining cold-cache, slow-provider, real-terminal latency and resource measurements.

These are **unverified coverage**, not successful test results. The decision does
not lower version floors, remove intended platform support, approve new numerical
resource budgets, change future general performance thresholds, or waive an
observed integrity/core-workflow defect. The original validation briefs remain
available for later execution; this is a scoped M1 acceptance decision, not a
blanket waiver for later milestones.

## Release boundary

M1's focused precision core is complete and accepted. M2–M4 work remains deferred.
Unproven destructive/integration actions stay unavailable; native reviewed-index
commits, hooks/signing, draft preservation, staged-content fidelity and explicit
network behavior retain their implemented safeguards. No known failure remains in
the completed candidate CI and lifecycle checks.
