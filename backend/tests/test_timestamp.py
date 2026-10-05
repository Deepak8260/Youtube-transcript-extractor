import pytest

from app.utils.timestamps import format_duration, format_timestamp


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00"),
        (0.99, "00:00:00"),
        (8.2, "00:00:08"),
        (59.999, "00:00:59"),
        (60, "00:01:00"),
        (754.5, "00:12:34"),
        (3599, "00:59:59"),
        (3600, "01:00:00"),
        (4354, "01:12:34"),
        (36000, "10:00:00"),
        (360000, "100:00:00"),
    ],
)
def test_format_timestamp(seconds, expected):
    assert format_timestamp(seconds) == expected


@pytest.mark.parametrize("bad", [-5, None, float("nan"), float("inf"), "abc"])
def test_format_timestamp_invalid_clamps_to_zero(bad):
    assert format_timestamp(bad) == "00:00:00"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, None), (5, "0:05"), (2538, "42:18"), (3723, "1:02:03")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected
