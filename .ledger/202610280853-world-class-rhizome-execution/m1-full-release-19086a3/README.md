# M1 full-release validation — 19086a3

Candidate: `19086a362dd9ac48bdffdd461816add47ca7c6ed`, pushed to
`origin/feat/hardening-pass`. On 2026-10-29 the operator requested a **full M1
release**, not a preview, and specifically asked to finish Linux lifecycle
validation. No merge, tag, GitHub release, or live user installation change has
been performed.

## Verified engineering results

- Hosted CI: [all seven jobs passed](https://github.com/Doctacon/rhizome/actions/runs/40954621009)
  on this exact candidate. The Linux complete suite reports **746 passed in
  409.35 s**. macOS reports **709 non-installer cases passed in 684.93 s**.
- Linux and macOS current-tool tmux journeys pass. The checksum-verified Git
  **2.36.0** / tmux **3.2** job also passes its tests and full tmux journeys.
- The actual hosted macOS image is `macos-14-arm64`, with Git 2.55.0 and Python
  3.13.15. It is **not** evidence for native Intel macOS.
- Real pinned bootstrap lifecycle passes on **macOS ARM64**, **Ubuntu 24.04 ARM64
  in the existing Linux VM**, and **Ubuntu 24.04 AMD64 under existing emulation**.
  The latter is an actual execution of x86_64 uv and managed Python, not a native
  x86_64 performance result.
- Every lifecycle installs `44cdd16`, updates to `19086a3`, rolls back, and
  uninstalls. It checks current/previous pointers, CLI startup, private uv
  **0.11.7**, managed Python **3.13.13**, paths containing spaces, and preservation
  of synthetic drafts, shell/Git/tmux configuration and an unrelated tool.
- The downloaded bootstrap is byte-equal to the Git object, SHA-256
  `8a8e14855e67f198652ee0ab842b57f12225d4dfcf570760ec323dc1a2e4e408`.
- Successful disposable installation homes, containers and host staging paths
  were removed. No daemon/VM settings, user installation or unrelated containers
  were changed. Public image downloads remain ordinary Docker cache entries.

## Linux execution and preserved failures

The previous bind-mount failure was not an unavailable Docker engine: Colima's
engine was running but could not see the macOS `/private/tmp` path. The revised
harness uses `docker cp` to transfer only its driver and reviewed bootstrap bytes
into an owned container, and copies evidence out after execution. It mounts no
host directory and does not reconfigure the VM.

- ARM64: **pass**, 30.97 s including container setup; actual lifecycle 15.67 s.
- First AMD64 attempt: install, CLI help and managed Python succeeded. The harness
  stopped because it combined stderr with stdout, so QEMU/jemalloc diagnostics
  preceded the otherwise correct `uv 0.11.7` version line.
- Second AMD64 attempt: **pass**, 99.61 s including setup. Version assertions now
  consume stdout while stderr remains fully recorded. No product behavior,
  expected version, timeout gate, checksum or installation assertion was weakened.
- The original bind-mount and AMD64 probe failures remain preserved. The first
  AMD64 container was confirmed stopped and retained with its evidence.

Evidence under the task directory:

- `m1-live-installer-19086a3/attempt-2/summary.json`: macOS lifecycle.
- `m1-live-installer-19086a3/github-jobs-final.json`: exact-candidate CI statuses.
- `m1-linux-live-installer-19086a3/copy-api-arm64/`: passing ARM64 lifecycle.
- `m1-linux-live-installer-19086a3/copy-api-amd64/`: retained probe failure.
- `m1-linux-live-installer-19086a3/copy-api-amd64-2/`: passing AMD64 lifecycle.
- `ci-macos-extract.txt`, `ci-complete-extract.txt`: source-linked CI log extracts.

No product source changed during this Linux follow-up. Its execution began at
21:42:32 UTC; the last result check was at 21:56:33 UTC. The following checkpoint
was recorded at 21:58:26 UTC, so that checkpoint interval exceeded the requested
15-minute bound. Subsequent work must use an earlier checkpoint margin.

## Full M1 acceptance — explicit operator decision

After reviewing the engineering evidence and remaining obligations, the operator
selected **“Accept full M1 readiness.”** The choice explicitly accepts passing CI
and real macOS/Linux lifecycles as sufficient for M1, with these still-unrun items
deferred—not claimed passed:

- Native macOS x86_64 installation/runtime coverage.
- Named human-visible terminal/clipboard, WSL2 + Windows Terminal and real SSH
  coverage, including the required keyboard/mouse workflows.
- The five-person pilot with at least four independent core-flow completions.
- Remaining V04 OS-cache-cold, stalled-provider, real-terminal latency and resource
  measurements; only the recorded 1.509 s startup result has explicit acceptance.

M1 is therefore accepted for a full release, not a preview. No feature or integrity
safeguard is removed, no support promise is silently narrowed and no unrun check
is marked passed. The decision does not merge, tag or publish. The public record
is `docs/validation/m1-release-acceptance.md`; later milestones remain out of scope.
