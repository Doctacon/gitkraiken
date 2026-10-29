# V03 prototype verdict

## Recommendation: A — Familiar three-pane

Adopt **A** as the M1 interaction direction, subject to V01 semantics and real-terminal/user validation. It best preserves Rhizome's recognizable roomy workspace at 170×48: changed files, the selected contiguous diff range, explicit Stage/Unstage, and the commit draft are visible together. The visual order also makes the safety story legible: inspect first, then act, then compose.

At 80×24, A correctly becomes a single primary diff task with visible Files/Commit summons and Back. It avoids the failure mode of squeezing three token-starved panes into a narrow terminal.

## Concrete trade-offs

- **A:** Best comparison and least relearning; the wide composer is narrower than C, and narrow mode requires a deliberate panel summon.
- **B:** Makes the workflow sequence unusually clear and gives the diff vertical room, but file/diff/composer context cannot be compared at once. It reads like a guided form rather than a workspace.
- **C:** Gives the composer more visual authority and keeps a compact file rail, but reduces the reading width where precise line decisions happen. That is the wrong trade for a precision-staging core.

## No adoption claim

This is only a throwaway interaction prototype. It demonstrates a proposed surface, not Git/index fidelity, durable drafts, recovery, accessibility support, performance, human usability, or acceptance in a real terminal.
