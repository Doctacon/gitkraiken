# M1 compatibility evidence (V05/V06)

**Status:** partial local evidence; required platform/install matrix is not complete and is not waived.

## Local host and isolated checks

Host: macOS 26.6.2, Darwin 25.6.0, arm64 Apple M2 Pro; Python 3.13.0; Ghostty invocation; current Git 2.50.1 (Apple Git-155); current tmux 3.5a. Every smoke used temporary homes/config/state, disposable repositories, and isolated tmux server names. No user installation, tmux server, repository, Git configuration, or credentials were used.

| Cell | Result | Scope/limitation |
| --- | --- | --- |
| Current Git, full reference Repository/app benchmark | Measured | See [performance evidence](m1-performance.md); this is headless, not broad correctness acceptance. |
| Git 2.36.0 boundary, reduced benchmark | Pass | 500 commits, 500 files, 50 refs, 20 changes; smoke only, not reference acceptance. Raw: [m1-compatibility-git236-smoke-20261029.json](../../.ledger/202610280853-world-class-rhizome-execution/m1-compatibility-git236-smoke-20261029.json). |
| tmux 3.5a startup/window reuse | Pass | `scripts/tmux_smoke.py`; real isolated tmux server, automated capture. |
| tmux 3.2 boundary startup/window reuse | Pass | Built from verified source into `/tmp`; same isolated smoke. Build warnings were retained in the command output and no operator installation occurred. |
| Popup smoke, tmux 3.5a | Pass, updated local run | Explicit checkout, non-mutating double-click, stable process/client identity and detach/reopen. |
| Popup hotkey smoke, tmux 3.5a | Pass, updated local run | Same journey plus prefix+g detach. |
| Pane smoke, tmux 3.5a | Pass, updated local run | Resize, independent panes, concurrent toggles, hidden/resumed remote refresh without pull, quit/crash containment. |
| Composer, current Git | Pass | All 27 dedicated cases; 40.12 s. |
| Composer + refresh, Git 2.36.0 | Pass | 27 composer and 12 refresh cases; 46.33 s. |
| Real 80×24 commit journey, current and Git 2.36.0 | Pass | Partial stage, interactive hook rejection/draft retention, successful native commit and resumed UI. |
| Installer crash/retry regression | Pass, repair follow-up | Signal-style exits retain the activity fence and staging; retries refuse while activity is unverifiable. Manual inspection remains required. |

The Git 2.36 smoke command prepended the existing disposable source build to `PATH` and ran:

```sh
uv run python scripts/benchmark_m1.py --smoke --samples 2 --warm-runs 1 \
  --output .ledger/202610280853-world-class-rhizome-execution/m1-compatibility-git236-smoke-20261029.json
```

It reported `git version 2.36.0` and verified all reduced dimensions. Reduced results do not certify the full envelope.

The minimum tmux source was `tmux-3.2.tar.gz`, SHA-256 `664d345338c11cbe429d7ff939b92a5191e231a7c1ef42f381cebacb1e08a399`. It was configured and installed only under a temporary prefix, reported `tmux 3.2`, and passed `scripts/tmux_smoke.py`.

### Updated terminal characterization

Fresh current-tmux GUI journeys use `LC_ALL=en_US.UTF-8` and disposable homes,
configuration, state, repositories and socket directories. A C-locale probe exposed
tmux replacing literal tab field separators with underscores; owned/numeric metadata
now uses printable separators. The public pane-launch unit regression covers the
C-locale sanitization behavior. This does not claim an entire non-UTF-8 terminal matrix.

The popup script now waits for actual sidebar selection before opening Actions,
and explicitly checks that double-click inspection leaves HEAD detached. Latest logs:

- [Startup/window](../../.ledger/202610280853-world-class-rhizome-execution/m1-terminal-tmux-utf8.txt)
- [Popup](../../.ledger/202610280853-world-class-rhizome-execution/m1-terminal-popup-final.txt)
- [Popup hotkey](../../.ledger/202610280853-world-class-rhizome-execution/m1-terminal-popup-hotkey-final.txt)
- [Pane](../../.ledger/202610280853-world-class-rhizome-execution/m1-terminal-pane-utf8.txt)
- [80×24 current-Git commit](../../.ledger/202610280853-world-class-rhizome-execution/m1-terminal-commit.txt)
- [80×24 Git 2.36 commit](../../.ledger/202610280853-world-class-rhizome-execution/m1-terminal-commit-git236.txt)

The earlier tmux 3.2 startup result predates the delimiter change; fresh full journeys
on that minimum remain a hosted-CI cell, not an inferred local pass.

Earlier current-tmux characterization (superseded by the updated runs above):

```text
scripts/tmux_smoke.py                 PASS
scripts/popup_smoke.py                FAIL: Graph double-click did not immediately switch to branch test
scripts/popup_smoke.py --hotkey       FAIL: same obsolete double-click expectation
scripts/pane_smoke.py                 FAIL: Reopen did not refresh remote state
```

### Installer crash/retry repair — 2026-10-29

The previously deferred regression was reproduced again (**1 failed in 3.39 s**),
then repaired in `scripts/install.sh`. A signal-style exit status greater than 128
now preserves the activity marker and staging instead of treating launcher death
as evidence that its descendants finished. Ordinary command failures remain
retryable. The fence does not automatically expire; manual inspection is required.

- Original regression plus three lifecycle neighbors: **4 passed in 8.00 s**.
- Complete installer and CLI/distribution modules: **45 passed in 42.21 s**.
- Complete installer module invoked through `/bin/dash`: **37 passed in 39.83 s**.
- System `sh` and `dash` syntax checks, root Ruff lint/format and whitespace checks pass.

No test expectations, skip or xfail changed. These are disposable, boundary-double
checks on the local macOS host, not live downloads or a Linux/platform certification.
See [repair evidence](../../.ledger/202610280853-world-class-rhizome-execution/m1-installer-repair.md).
The required complete-suite CI job remains enabled; a fresh full-suite/hosted run
has not been performed.

## Current local regression and packaging evidence

The earlier 746-case bounded partition evidence recorded **745 passes and one
installer failure**. That failure is superseded by the repair checks above, not
removed from its original log. This is not a fresh single all-green full run.
The additional C-locale case is included. After formatting, 60 affected CLI/control/
responsive/activity checks passed in 41.51 s. All 24 formatted M1-owned files retained
identical Python ASTs. Root Ruff lint passes; task-local throwaway prototypes are
excluded from product lint. Offline wheel/sdist construction passes, and the wheel
contains the M1 controls, diagnostics and stylesheet.

**Whole-tree formatting now passes (2026-10-29 follow-up):** the operator authorized
formatting the remaining 21 files from clean `ea6df96` (`feat: M1`). All 21 retain
identical Python ASTs; no installer logic, assertions, skips or failure expectations
changed. `ruff check .`, `ruff format --check .` (103 files) and `git diff --check`
pass. The normal CI gate remains enabled; no hosted-CI success is inferred.
See [file hashes / AST proof](../../.ledger/202610280853-world-class-rhizome-execution/m1-formatting-followup.json)
and [affected-test log](../../.ledger/202610280853-world-class-rhizome-execution/m1-formatting-tests.txt):
all 15 reformatted test modules ran, with **208 passed and the one known installer
failure in 265.33 s**. No new failures appeared in this affected-test run.
The earlier [static summary](../../.ledger/202610280853-world-class-rhizome-execution/m1-static-summary.txt)
retains the historical formatting failures; those are superseded, not erased.

## CI changes (not executed locally)

`.github/workflows/ci.yml` now separates:

- Linux and macOS current-Git non-installer quality/test/build jobs.
- A normal Linux complete-suite job including installer regressions.
- Linux and macOS current Git/tmux startup, popup/hotkey and pane journeys in UTF-8 locales.
- A Linux exact-boundary job provisioning build dependencies, building Git 2.36.0 and tmux 3.2 from checksum-verified sources into runner-temporary prefixes, then running non-installer tests and the same isolated tmux journeys.
- A reduced benchmark-tooling smoke explicitly labeled non-acceptance.

Pinned sources:

- Git 2.36.0: `https://www.kernel.org/pub/software/scm/git/git-2.36.0.tar.xz`, SHA-256 `af5ebfc1658464f5d0d45a2bfd884c935fb607a10cc021d95bc80778861cc1d3`.
- tmux 3.2: `https://github.com/tmux/tmux/releases/download/3.2/tmux-3.2.tar.gz`, SHA-256 `664d345338c11cbe429d7ff939b92a5191e231a7c1ef42f381cebacb1e08a399`.

The corrected popup/pane journeys are included in CI after their local runs passed.
The workflow has not been pushed or triggered; hosted-runner results remain pending.

## Required matrix still pending

| Required cell | Status |
| --- | --- |
| Hosted Linux current/minimum jobs | Pending CI execution |
| Hosted macOS current jobs | Pending CI execution |
| macOS ARM64 install/runtime | Pending; live installation not authorized/performed |
| macOS x86_64 install/runtime | Pending; environment unavailable locally |
| Named real macOS terminal human-visible smoke | Pending; headless/PTY capture is not a substitute |
| Named real Linux terminal | Pending; environment unavailable locally |
| WSL2 + Windows Terminal | Pending; environment unavailable locally |
| Real SSH path | Pending; no authorized remote host |
| Full popup/pane containment, resize, pause/resume, clipboard fallback | Current-tmux automated journeys pass locally; broader terminal/clipboard matrix remains pending |
| 80×24 and wide keyboard/mouse core journeys | Pending human/real-terminal coverage; see usability template |
| Clean install/update/rollback/uninstall lifecycle | Isolated boundary-double tests pass; live installation/platform checks remain pending |

No pending architecture, WSL, SSH, terminal, clipboard, or installer cell is waived or advertised as verified.
