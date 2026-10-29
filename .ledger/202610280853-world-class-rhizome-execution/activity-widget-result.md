Implemented `ActivityDialog` in `src/rhizome/activity.py` and documented it in the assigned ledger artifact.

- Bounded, latest-first 20-record activity inspection.
- Separate background-read context from Git mutations.
- Periodic updates preserve selected record.
- Literal result rendering, elapsed time, checkpoints, Close/Escape-only behavior.
- Responsive bounded modal with keyboard/mouse inspection.

Validation run:
- `uv run --locked ruff check src/rhizome/activity.py`
- `uv run --locked python -m py_compile src/rhizome/activity.py`

No app integration, Pilot testing, or terminal UI evidence was run or claimed.