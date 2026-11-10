from datetime import datetime, timedelta, timezone

import pytest

from fed.core.naming import (
    decode_imap_utf7,
    eml_filename,
    folder_to_zip_path,
    sanitize_component,
    zip_member_path,
)


def test_eml_filename_utc():
    berlin = timezone(timedelta(hours=1))
    dt = datetime(2026, 11, 6, 15, 30, 12, tzinfo=berlin)
    assert eml_filename(dt, 4711) == "20261106T143012Z_UID00004711.eml"


def test_eml_filename_requires_tz():
    with pytest.raises(ValueError):
        eml_filename(datetime(2026, 1, 1), 1)


def test_decode_imap_utf7():
    assert decode_imap_utf7(b"Entw&APw-rfe") == "Entwürfe"
    assert decode_imap_utf7("Gel&APY-schte Objekte") == "Gelöschte Objekte"
    assert decode_imap_utf7("A&-B") == "A&B"
    assert decode_imap_utf7("INBOX") == "INBOX"


def test_sanitize():
    assert sanitize_component('Rech:nung?*') == "Rech_nung__"
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("Ordner. ") == "Ordner"


def test_zip_paths():
    assert folder_to_zip_path("INBOX.Rechnungen", ".") == "INBOX/Rechnungen"
    assert folder_to_zip_path("[Gmail]/Alle Nachrichten", "/") == "[Gmail]/Alle Nachrichten"
    assert (
        zip_member_path("max@example.de", "INBOX", "/", "x.eml") == "max@example.de/INBOX/x.eml"
    )


@pytest.mark.parametrize("pattern,expected", [
    ("{lfd:04}/{jahr}", "0001/2026"), ("{lfd:04}/{jahr:2}", "0001/26"), ("{jahr:04}-{lfd}", "2026-1"),
    ("AZ {jahr:2}{monat}{tag}-{lfd:03}", "AZ 260309-001"), ("{jahr:1}", "6"), ("ohne Platzhalter", "ohne Platzhalter")])
def test_suggest_case_number(pattern, expected):
    from datetime import datetime

    from fed.core.naming import suggest_case_number

    assert suggest_case_number(pattern, datetime(2026, 3, 9)) == expected


@pytest.mark.parametrize("pattern", ["{foo}", "{jahr", "{lfd:xyz}"])
def test_suggest_case_number_invalid(pattern):
    from fed.core.naming import suggest_case_number

    with pytest.raises(ValueError):
        suggest_case_number(pattern)
