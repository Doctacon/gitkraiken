"""Local-time presentation of Git's offset-aware author timestamps."""

from datetime import datetime


def format_commit_date(value: str, *, full: bool = False) -> str:
    """Convert the instant, including its historical DST offset, using the OS zone."""
    try:
        recorded = datetime.fromisoformat(value)
        if recorded.tzinfo is None:
            return value  # Git supplies an offset; don't invent one for malformed data.
        local = recorded.astimezone()
    except (ValueError, OverflowError, OSError):
        return value
    return local.strftime("%Y-%m-%d %H:%M:%S %Z (%z)" if full else "%Y-%m-%d %H:%M")
