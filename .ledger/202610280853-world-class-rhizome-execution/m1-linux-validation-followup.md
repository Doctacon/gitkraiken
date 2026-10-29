# Hosted Linux validation follow-up — 2026-10-29

Bounded increment began at 20:35 UTC; installer validation was postponed after
hosted CI exposed failures. The operator additionally confirmed the existing
Repository.execute(Action('commit')) / real-Git test boundary for fixture repair.

Original CI run: https://github.com/Doctacon/rhizome/actions/runs/40949349588
Candidate: `80f5e8f7586ee7e3630b0dbc43aeef00b26eed95`.

- Linux complete suite: **744 passed, 2 failed in 333.10 s**.
- Linux non-installer suite: **707 passed, 2 failed in 385.49 s**.
- Both failures were the custom SHA-256/sparse-index repositories lacking local
  author identity. Test setup via `git_run` supplied command-scoped identity, but
  the application's actual native commit did not inherit those `-c` flags. macOS
  could infer an OS identity; Linux's runner could not. This is not a reason to
  change the application's handling of unknown authors or user configuration.
- Minimum Git build failed: GCC 15's C23 `unreachable()` macro collided with Git
  2.36's historical function of that name. No minimum-version increase or source
  checksum change was made.
- Current-tmux jobs on Linux/macOS and the benchmark tooling smoke passed. The
  first status snapshot still had macOS non-installer checks running.

Raw completed-job logs were retrieved through the GitHub job-log API because
`gh run view --log-failed` refuses while another job is still running. Both those
initial refusals and the raw logs remain in `m1-release-validation-80f5e8f/`.

## Local reproduction and focused fixes

A disposable PATH shim added `git -c user.useConfigOnly=true` without touching any
user configuration. The existing representation module reproduced exactly the two
identity failures (**2 failed, 5 passed in 12.45 s**). After setting local name,
email and signing policy in the two custom fixture constructors, all **7 passed**
under that same strict driver. No fidelity assertions changed.

The minimum Git build/install commands now both use `CFLAGS='-O2 -std=gnu99'` so
the verified historical source is built in its supported dialect rather than a
new compiler default. Actual hosted GCC 15 verification remains pending the rerun.

Root lint, whole-tree formatting and whitespace checks pass. The focused changes
were committed as `0a2805f35a45bec51cad6f277d3f0e40f914e084` and submitted for branch
push. Only the representation fixtures and CI build flags changed; production
application/installer behavior is unchanged. No merge, tag, release or live user
installation change was performed.
