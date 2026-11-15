"""Konfigurierbarer ZIP-Dateiname (Platzhalter, leere Werte, Eindeutigkeit)."""

from datetime import UTC, datetime

from fed.core.naming import DEFAULT_ZIP_PATTERN, zip_basename

WHEN = datetime(2026, 11, 15, 8, 30, 0, tzinfo=UTC)


def test_placeholders_filled():
    n = zip_basename("{aktenzeichen}_{asservat}_{email}", case_number="2026-0001",
                     evidence="A-01", email="max@example.de", when=WHEN)
    assert n == "2026-0001_A-01_max@example.de"


def test_empty_placeholder_collapses():
    # Gesamt-ZIP: kein Asservat/keine Adresse -> keine doppelten Trenner, kein führender/abschließender
    n = zip_basename("{aktenzeichen}_{asservat}_{email}", case_number="2026-0001", when=WHEN)
    assert n == "2026-0001"


def test_time_and_date_year():
    assert zip_basename("{jahr}", case_number="x", when=WHEN) == "2026"
    assert zip_basename("{datum}", case_number="x", when=WHEN) == "2026-11-15"
    assert zip_basename("{zeit}", case_number="x", when=WHEN) == "20261115T083000Z"


def test_sanitizes_and_unknown_kept():
    # Aktenzeichen mit Slash wird bereinigt; unbekannter Platzhalter bleibt stehen
    n = zip_basename("{aktenzeichen}_{foo}", case_number="VG-12/26", when=WHEN, spaces_to_underscore=True)
    assert "/" not in n and "{foo}" in n


def test_default_pattern_has_time_for_uniqueness():
    assert "{uhrzeit}" in DEFAULT_ZIP_PATTERN or "{zeit}" in DEFAULT_ZIP_PATTERN


def test_uhrzeit_is_time_only():
    # {uhrzeit} = nur Uhrzeit (HHMMSS), {zeit} = voller Zeitstempel
    assert zip_basename("{uhrzeit}", case_number="2026-0001", when=WHEN) == "083000"
    assert zip_basename("{datum}_{uhrzeit}", case_number="2026-0001", when=WHEN) == "2026-11-15_083000"


def test_never_empty():
    assert zip_basename("{asservat}", case_number="2026-0001", when=WHEN) == "2026-0001"
    assert zip_basename("", case_number="2026-0001", when=WHEN)


def test_build_zip_uses_pattern_and_is_unique(acquired):
    import zipfile

    from fed.core.config import Config
    from fed.core.finalize import build_zip

    aid = acquired.conn.execute("SELECT id, email FROM account").fetchone()
    cfg = Config()
    cfg.programm.zip_name_muster = "{aktenzeichen}_{asservat}_{email}"
    z1 = build_zip(acquired, cfg, account_id=aid[0])
    assert z1.path.name == f"2026-0042_A-01_{aid[1]}.zip"
    with zipfile.ZipFile(z1.path) as zf:
        assert any(n.endswith(".eml") for n in zf.namelist())
    # zweiter Lauf, gleiches Muster -> _2 (keine Überschreibung)
    z2 = build_zip(acquired, cfg, account_id=aid[0])
    assert z2.path.name == f"2026-0042_A-01_{aid[1]}_2.zip"


def test_preview_gesamt_matches_and_writes_nothing(acquired):
    from fed.core.config import Config
    from fed.core.finalize import preview_zip_names

    cfg = Config()
    cfg.programm.zip_name_muster = "{aktenzeichen}"
    # ein Konto -> Modus "gesamt" -> ein Archiv, leere Bezeichnung
    assert preview_zip_names(acquired, cfg) == [("", "2026-0042.zip")]
    # Vorschau ist seiteneffektfrei
    assert not list(acquired.root.glob("*.zip"))


def test_preview_je_konto_and_uniqueness(acquired, imap_server):
    from fed.core.acquisition import AccountSpec, register_account
    from fed.core.config import Config
    from fed.core.finalize import preview_zip_names
    from fed.core.imap_reader import ReadOnlyImap

    c = ReadOnlyImap(imap_server.host, imap_server.port, cafile=str(imap_server.cafile))
    c.connect()
    c.login_password(imap_server.user, imap_server.password)
    register_account(acquired, AccountSpec("zweite@example.com", "A-02", imap_server.host, imap_server.port), c)
    c.logout()
    acquired.set_zip_mode("je_konto")

    cfg = Config()
    cfg.programm.zip_name_muster = "{aktenzeichen}_{asservat}"
    pv = preview_zip_names(acquired, cfg)
    assert pv == [("A-01", "2026-0042_A-01.zip"), ("A-02", "2026-0042_A-02.zip")]

    # schon vorhandenes Archiv -> Vorschau zeigt _2 (spiegelt _unique_zip, ohne zu schreiben)
    (acquired.root / "2026-0042_A-01.zip").write_bytes(b"x")
    pv2 = preview_zip_names(acquired, cfg)
    assert pv2[0] == ("A-01", "2026-0042_A-01_2.zip")
    assert pv2[1] == ("A-02", "2026-0042_A-02.zip")
