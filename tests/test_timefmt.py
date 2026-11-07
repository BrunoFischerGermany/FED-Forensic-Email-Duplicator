from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fed.core import timefmt as tf


def test_both_with_berlin(monkeypatch):
    berlin = ZoneInfo("Europe/Berlin")
    monkeypatch.setattr(tf, "local", lambda v: tf.parse(v).astimezone(berlin) if tf.parse(v) else None)
    winter = "2026-11-07T08:57:37+00:00"
    summer = "2026-07-01T10:00:00+00:00"
    assert tf.both(winter) == "2026-11-07 08:57:37 UTC / 2026-11-07 09:57:37 MEZ +01:00"
    assert tf.both(summer).endswith("12:00:00 MESZ +02:00")  # Sommerzeit korrekt
    assert tf.both_short(winter) == "2026-11-07 08:57:37 UTC\n09:57:37 MEZ +01:00"
    assert tf.both_short("2026-11-07T23:30:00+00:00").endswith("2026-11-08 00:30:00 MEZ +01:00")


def test_missing_and_naive():
    assert tf.both(None) == "–" and tf.local_str("") == ""
    assert tf.utc_str(datetime(2026, 1, 1, 12, 0)) == "2026-01-01 12:00:00 UTC"  # naiv = UTC
    assert tf.utc_str(datetime(2026, 1, 1, 13, 0, tzinfo=ZoneInfo("Europe/Berlin"))).startswith("2026-01-01 12:00")
    assert tf.parse("kaputt") is None
    assert tf.local(datetime(2026, 1, 1, tzinfo=UTC)) is not None
