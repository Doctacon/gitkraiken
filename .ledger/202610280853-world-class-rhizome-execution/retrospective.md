Status: pending
Created: 2026-10-28
Updated: 2026-10-28

# Retrospective

## What Mattered

Pending completion of the undertaking.

## Learnings

Pending completion of the undertaking.

## Improvements

Pending completion of the undertaking.

## Interim lesson: normal commit scope reset

The work accumulated safety infrastructure and installer concerns while leaving a
core daily action unavailable. The operator explicitly reset the scope to restoring
ordinary commits. Keep a working end-to-end core as the unit of progress; do not
make the entire roadmap or unrelated distribution redesign a prerequisite.

Real terminal evidence mattered: the process-group safety fix broke `/dev/tty`
hooks. A bounded foreground handoff plus Textual suspension fixed that without
removing the index/descendant protections. Automated non-headless 80×24 refusal/
draft/success and real disposable-key SSH signing provide stronger evidence than
headless UI plus noninteractive signer doubles alone. These remain local automated
proofs, not release/human/platform certification.

## Interim lesson: resume without reopening deferred work

Roadmap execution resumed with a bounded composer increment while installer
crash/retry stayed deferred. Preserve the working daily flow instead of turning
every enhancement into a new availability gate. The operator selected existing
checks only: green regressions demonstrate preservation of covered behavior, not
acceptance of template and encoding cases that the suite does not exercise.
