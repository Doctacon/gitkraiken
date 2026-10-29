# M1 five-user pilot task sheet and results template (V07)

**Status: not run. No participants or usability results are claimed.**

Run only with a usable M1 candidate after major V01–V06 blockers are resolved. The deferred installer crash/retry defect blocks installer-lifecycle live checks; it must not be hidden or retried during this pilot. Use a synthetic disposable repository, isolated application state, and no private source or credentials.

## Moderator setup

- Candidate revision/artifact: `________________`
- Date/moderator: `________________`
- Disposable fixture identity and reset command: `________________`
- Recording consent: `none / notes / screen / audio`; storage/deletion date: `________________`
- Confirm no coaching during core tasks. In-app help is allowed and observed.
- Record terminal, OS/architecture, Git, tmux, Python, dimensions, input device, SSH/local status, font/glyph fallback, and clipboard path exactly.
- Assign coverage before recruiting so the five people collectively include keyboard-only, mouse use, 80×24, wide layout, tmux, and SSH. A participant may cover more than one cell.
- Stop immediately for unintended mutation, wrong target/content, lost draft, misleading success, or safety misunderstanding.

## Participant task sheet

Read this verbatim except for neutral clarification of fixture facts:

1. Open this repository. Without a mandatory tour, find the available commands/help and identify the current repository and branch.
2. Inspect the mixed staged and unstaged edits. Explain what a commit would contain right now.
3. Stage the requested hunk, then stage the requested contiguous line range. Do not stage the neighboring change.
4. Correct the supplied mistake by unstaging the relevant part. Confirm unrelated staged and worktree changes remain.
5. Compose a summary and body using the available template/guidance. Encounter the prepared failure/reopen, recover the draft, and commit only the intended staged result.
6. Separately find explicit Checkout and respond to the prepared dirty/stale context blocker. Browsing gestures must not checkout.
7. Inspect available recovery and perform recover-aside, **or**, where the risky action is deliberately gated, explain why it is unavailable and identify the external hand-off.
8. State what you believe changed, what remains uncommitted, and whether any result is uncertain.

Do not name shortcuts or desired controls unless the participant has stopped and the observation has already been marked assisted/incomplete.

## Per-participant record

Copy once for each of P1–P5. Use anonymous IDs only.

```text
Participant ID: P__
Terminal-native with Git basics confirmed: yes / no
Consent scope: ______________________________
Environment: OS/arch ______  terminal/version ______  Git ______  tmux ______
Dimensions: ______  local/SSH: ______  keyboard/mouse: ______  glyph mode: ______

Core task                         Independent?  Completed?  Time*  Observation/assumption
Discover context/help             Y/N           Y/N         ____   ______________________
Explain staged vs unstaged        Y/N           Y/N         ____   ______________________
Stage hunk                        Y/N           Y/N         ____   ______________________
Stage contiguous line range       Y/N           Y/N         ____   ______________________
Correct/unstage                   Y/N           Y/N         ____   ______________________
Draft survives failure/reopen     Y/N           Y/N         ____   ______________________
Commit intended staged result     Y/N           Y/N         ____   ______________________

Separate checks
Explicit checkout/blocker understood: Y/N  _____________________________________________
Recovery/gated hand-off understood:   Y/N  _____________________________________________
Focus/input/resize/clipboard issue:   Y/N  _____________________________________________
Unexpected mutation or wrong target: Y/N  _____________________________________________
Safety misunderstanding:             Y/N  _____________________________________________
Assistance given (when/why):          _________________________________________________
Participant wording worth retaining: _________________________________________________
Issues (ID/severity):                 _________________________________________________

* Time is diagnostic only; there is no numeric time gate.
Overall independent core completion: Y/N
```

“Independent core completion” requires the inspect → hunk/line stage → correct/unstage → compose/commit path without moderator coaching. Assisted completion remains recorded but does not count.

## Aggregate results (leave blank until observed)

| Participant | Environment/input coverage | Independent core completion | Safety misunderstanding | Core blocker | Retest after fix |
| --- | --- | --- | --- | --- | --- |
| P1 |  |  |  |  |  |
| P2 |  |  |  |  |  |
| P3 |  |  |  |  |  |
| P4 |  |  |  |  |  |
| P5 |  |  |  |  |  |

- Independent completions: `__/5` (pass requires at least **4/5**).
- Keyboard-only covered: `yes/no`; mouse covered: `yes/no`.
- 80×24 covered: `yes/no`; wide covered: `yes/no`.
- tmux covered: `yes/no`; SSH covered: `yes/no`.
- Unresolved safety misunderstandings: `________________` (pass requires none).
- Unresolved core blockers: `________________` (pass requires none).
- Cosmetic deferrals with owner/rationale: `________________`.
- Fix/retest evidence links: `________________`.
- Final disposition: `PASS / FAIL / BLOCKED / NOT RUN`.

A five-person qualitative pilot does not estimate population-wide usability. Missing required input/environment coverage is pending, not a pass. Model-generated personas, headless automation, and the V03 prototype do not substitute for real participants.
