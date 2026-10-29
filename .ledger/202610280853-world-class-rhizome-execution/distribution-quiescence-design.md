## Judgment

The reproduced failure is real and safety-relevant. `guarded_command()` removes `activity` immediately after the direct command returns (`scripts/install.sh:237-264`), while cleanup treats that absence as permission to delete staged state and release the lock (`scripts/install.sh:57-102`). The redirected surviving child in `tests/test_install.py:780-862` proves direct return and pipe EOF are insufficient.

Do not accept the current regression turning green as proof of full quiescence unless the implementation establishes descendant-group absence. The test presently proves fencing while the child lives; it does not prove safe automatic recovery after the child exits.

## Exact constraints

The design must retain:

- POSIX `/bin/sh` bootstrap on supported macOS and Linux/WSL, including headless/SSH execution.
- No host Python, `flock`, assumed `setsid`, compiler, or newly required system package.
- No new unpinned executable or dependency.
- No production `FAKE_*` or test-only bypass.
- No live installation, download, or publication in this work.
- No silent platform, shell, Git, tmux, or Python floor increase.
- No cleanup, publication, or lock takeover while an inherited writer may remain.
- Ordinary descendants that inherit the designated process group are in scope even after reparenting and after closing all standard descriptors.
- Deliberate process-group/session escape is outside scope. Arbitrary hostile same-UID activity remains outside the sandbox claim.

These follow C10’s support and bootstrap contract (`docs/design/world-class-rhizome.md:350-375`) and V05’s interruption/recovery requirement (`docs/design/validation-plan.md:268-306`).

## Recommended architecture

The smallest sound architecture is a **dedicated inherited process group per guarded command**, with the transaction marker recording its PGID before the command is allowed to mutate anything:

1. Create `activity` before launch in a versioned pending state bound to the lock nonce.
2. Start a supervisor outside the command group.
3. Create the command in a new process group, persist the exact PGID, then release it.
4. Wait for the direct command.
5. Probe the whole group with signal zero:
   - group absent (`ESRCH`): quiescence is established; clear the matching activity marker and return the direct status;
   - group present: return retained uncertainty immediately—do not wait indefinitely and do not kill it;
   - `EPERM` or any other unprovable result: retain activity and fail closed;
   - `EINTR`: retry the probe.
6. A later installer may reclaim only after the owner is dead, it has won the existing exclusive reaper claim, the activity record is unchanged, and group absence is proven again.

This survives intermediate-launcher death, reparenting, and descriptor closure. Zombies or PGID reuse may cause conservative false retention, never premature cleanup.

### POSIX-shell job control is only a conditional candidate

`set -m`, an asynchronous gated job, and `$!` could construct the group without `setsid`. Before releasing the command, the parent must positively prove that `$!` is the new PGID and that it is separate from the installer’s group.

That is not yet a full cross-platform design:

- Noninteractive job-control behavior, especially without a controlling terminal, varies among system shells.
- The shell `kill` utility does not expose `ESRCH` versus `EPERM` portably.
- A `ps` scan is not an atomic absence proof and must not authorize cleanup.
- Failure of this capability on headless Linux cannot silently become a new unsupported-platform rule.

Therefore shell job control should be prototyped on every supported system `/bin/sh`, but not presumed.

### The approved managed runtime arrives too late

The verified uv is obtained only after guarded download, checksum, and extraction (`scripts/install.sh:815-847`). Managed Python exists only after the guarded `uv python install` at line 958; the reproduced command follows at lines 959-964. It could supervise later work, but cannot establish end-to-end bootstrap safety or retroactively prove the Python-provisioning command quiescent.

Using host Python is not authorized.

## Interim fail-closed option

A minimal patch can retain `activity` on every nonzero direct result rather than executing the unconditional clear at line 260. That closes this specific SIGKILL reproduction because the launcher returns nonzero and cleanup will preserve the lock/stage.

Its behavior must be stated honestly:

- Later invocations refuse with the existing subprocess-activity diagnostic.
- No automatic cleanup or repopulation occurs.
- The marker may require manual inspection indefinitely.
- A direct status of zero with a surviving same-group descendant remains unresolved.

This is a useful safety gate, not a full quiescence solution and not sufficient to accept V05 or live installation.

## Required product decision if shell capability is insufficient

If the supported `/bin/sh` matrix cannot provide verified process-group construction and exact absence semantics, choose explicitly among:

1. Ship a small, release-pinned native supervisor available before the first guarded mutator, adding it to C10 provenance and V05/V06 platform evidence.
2. Raise a host prerequisite such as a particular shell/runtime or `setsid`—a support-contract change requiring approval.
3. Accept permanent fail-closed/manual recovery and revise V05’s reproducible-recovery expectation.
4. Keep distribution gated.

Do not quietly choose any of these.

## Failure behavior

For the full design:

- Present or unprovable group: retain lock, activity, temporary work, revision stage, and publication stage; report transaction outcome as uncertain.
- Signal or supervisor interruption: same retention.
- Proven absent group: clear only the nonce-matching regular activity record, then perform existing cleanup.
- No process is terminated, retried, or assumed dead from pipe EOF.
- A retry while the child lives must perform no download, cleanup, repopulation, or pointer change.
- Recovery after natural child exit is lazy: a later invocation re-proves absence under the exclusive stale-lock reaper before cleanup.

## Proof sequence

Root should preserve the existing regression’s sequencing and pointer/stage identity assertions, then add evidence for:

1. The killed launcher with child stdin/stdout/stderr redirected, as already reproduced.
2. Direct return zero while a same-group descendant remains.
3. Normal absent-group success and ordinary nonzero cleanup/status preservation.
4. Installer death while the command group remains active.
5. Group-present and probe-unverifiable outcomes retaining state.
6. Natural child exit followed by a new invocation that re-proves absence and safely recovers.
7. Noninteractive, no-TTY system `/bin/sh` behavior on macOS and supported Linux shells if pursuing the shell route.
8. No production test bypass and no child termination during cleanup.
9. Existing installer suite, shell syntax, and static checks.

These remain synthetic component proofs. Clean-machine/direct-uv/live-artifact V05 evidence requires separate authorization.

No files were modified and no commands, tests, downloads, installs, or process signals were run.