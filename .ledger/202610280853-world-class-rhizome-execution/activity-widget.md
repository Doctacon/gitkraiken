# M1 activity widget

Implemented `src/rhizome/activity.py` as an internal, read-only `ActivityDialog`.

- The dialog takes the coordinator activity callback and separate background-read context callback.
- Coordinator records are capped at 20 and stay latest-first; they show queued/running/terminal state, duration, redacted coordinator result detail, and recovery checkpoint ID when available.
- The refresh timer preserves the selected record by ID. Arrow keys and mouse highlight inspect a record; Escape and Close only dismiss.
- Background planning/local/remote status is deliberately headed separately from Git mutations. The dialog states that closing never cancels a running operation.

Validation is limited to Ruff and Python compilation of the new module. No app wiring, Pilot test, or terminal evidence is claimed; root owns those integration surfaces.
