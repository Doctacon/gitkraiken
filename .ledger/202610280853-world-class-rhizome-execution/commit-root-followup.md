# Native commit: root safety follow-up

Commit remains unavailable in the coordinator/UI. This is component evidence,
not composer, interactive signing, full-suite, platform, or milestone acceptance.

## Descendant lifetime and launch classification

- A killed foreground hook launcher let native Git return 0 while its child still
  needed the candidate. Both inherited-output and redirected-output host cases
  failed: **2 failed in 2.54 s**. The candidate was moved/deleted prematurely.
- Native Git now starts in a dedicated POSIX process group without `setsid`.
  After a nonnegative direct return, only an absent group permits publication.
  A present group or unprovable probe retains the lock/candidate and reports
  uncertainty. Signal 0 is only an existence probe; no process is killed,
  cancelled, timed out, or retried. Deliberate group/session escape is outside
  this boundary.
- Root chose immediate retained uncertainty rather than the consultant's proposed
  indefinite group wait: a remaining group can contain orphaned zombies or an
  unrelated reused PGID. Absence is required for publication; presence is not
  sufficient evidence for either cleanup or a promise of eventual completion.
- Public proofs cover a post-commit launcher (native status 0), redirected output,
  a configured signer launcher (native nonzero), and denied liveness probing.
  Surviving fixture children are released and observed, not killed.
- The broad `Popen`-`OSError` cleanup rule was also unsafe. A real child followed by
  an injected constructor-boundary error lost its transaction. Paired pre-spawn
  preparation/live-child tests: **2 failed in 2.21 s** before repair.
- Executable preparation failure before construction may clean normally. Once
  construction is attempted, an exception retains uncertainty even if PID/PGID
  were not returned. Recovery records include spawn-pending, PID, PGID, direct
  status, and group observations. Native direct waiting remains noncancellable.

`commit-quiescence-review.md` remains a review, not implementation evidence.
Dedicated groups change terminal job control. Interactive `/dev/tty` signing and
hook behavior, real PTY/tmux integration, and additional OS evidence remain open.
Noninteractive signer doubles are not cryptographic or interactive acceptance.

## Publication and artifact ownership

- The publication worker found a replaced candidate directory received recovery
  writes before its ownership mismatch was noticed. Root reproduced **1 failed
  in 1.81 s**. A no-follow directory identity check now precedes candidate/context
  access and publication; its descriptor stays open to pin the owned identity.
- Additional regular-file/FIFO context substitutions exposed writes to an unknown
  recovery file and a potentially blocking FIFO open: **2 failed in 1.56 s**.
  The context's original descriptor pins its identity; append opens are no-follow,
  nonblocking, regular-file/identity checked, and all descriptors close on exit.
  The FIFO test intercepts the external OS-open boundary to prevent a fixture
  deadlock while proving that no blocking FIFO open is attempted.
- These three cases preserve the original real index and lock, the test-moved
  owned artifacts, and the distinct foreign data. They do not claim atomicity
  against arbitrary concurrent same-UID filesystem writes.
- Added combined linked-worktree/split-index evidence to the existing forced
  shared-base rebuild test. Aggressive expiry is configured; real-index readers
  remain valid while the hook is held, the new shared base differs, and primary
  worktree HEAD/index/staged bytes/content remain unchanged.
- The broader fsync/rename/publication durability matrix is still incomplete.

## Validation and minimum-Git fixture correction

- Initial host process/fault/transaction slice: **22 passed in 21.58 s**.
- `task-26`: current Git **147 passed in 117.38 s**; actual Git 2.36 **146 passed,
  1 failed in 61.76 s**. The inherited-output fixture wrongly required Git 2.36
  itself to exit while a descendant held its hook-output pipe open. Git 2.36
  continues waiting for that EOF.
- Corrected only that native-lifetime premise: inherited-output execution must
  either still be running with the candidate intact, or have returned safely.
  Redirected hook/signer cases still require direct Git exit with the child alive.
  All index, candidate, ownership, late-staging and ref assertions remain.
  Fault suite then passed **9 in 8.51 s** (host) and **9 in 7.57 s** (Git 2.36).
- After artifact fixes: **32 passed in 29.98 s**. The expanded representation file
  then passed **7 in 7.84 s**. Targeted Ruff passed.
- `task-27`, final combined engine/patch/native/fetch/read-only/plan slice:
  **151 passed in 120.02 s** on current Git and **151 passed in 56.47 s** on
  actual Git 2.36.0. This includes the three publication-ownership cases and the
  linked+split case, but not coordinator/UI commit success or installer tests.

## Still required before commit enablement

- Interactive signing/TTY compatibility and native composition oracles.
- Remaining publication/durability/ownership faults and review closure.
- Unchanged coordinator/UI writer, hook/draft, 80×24 and core success contracts.
- Fresh full-suite evidence and the remaining M1 validation gates.

Separately, the installer descendant guard remains red: root reproduced a second
installer succeeding while an intermediate launcher's child was active (**1 failed
in 3.38 s**). Only its misleading safety comment was corrected; no production
quiescence fix exists. V05/live installation remain blocked.
