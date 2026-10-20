import pytest

from gitkraiken.dates import format_commit_date


@pytest.mark.parametrize(
    "zone, recorded, compact, full",
    [
        (
            "America/Denver",
            "2024-02-03T04:05:06+05:30",
            "2024-02-02 15:35",
            "2024-02-02 15:35:06 MST (-0700)",
        ),
        (
            "America/Denver",
            "2024-07-03T19:08:06+00:00",
            "2024-07-03 13:08",
            "2024-07-03 13:08:06 MDT (-0600)",
        ),
        (
            "America/Phoenix",
            "2024-07-03T19:08:06Z",
            "2024-07-03 12:08",
            "2024-07-03 12:08:06 MST (-0700)",
        ),
        (
            "Asia/Kolkata",
            "2024-02-03T23:08:06-07:00",
            "2024-02-04 11:38",
            "2024-02-04 11:38:06 IST (+0530)",
        ),
        (
            "UTC",
            "2024-02-03T04:05:06+05:30",
            "2024-02-02 22:35",
            "2024-02-02 22:35:06 UTC (+0000)",
        ),
    ],
)
def test_commit_date_uses_local_zone_for_the_commit_instant(
    local_timezone, zone, recorded, compact, full
):
    local_timezone(zone)
    assert format_commit_date(recorded) == compact
    assert format_commit_date(recorded, full=True) == full


@pytest.mark.parametrize("value", ["", "not a date", "2024-02-03T04:05:06"])
def test_missing_or_malformed_dates_remain_literal(value):
    assert format_commit_date(value) == value
    assert format_commit_date(value, full=True) == value
