from email.message import EmailMessage

from fed.core.mailmeta import parse


def _mail(**headers) -> EmailMessage:
    m = EmailMessage()
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content("Hallo")
    return m


def test_to_and_cc():
    m = _mail(From="a@example.org", To="b@example.de", Cc="c@example.de, d@example.de", Subject="Test")
    meta = parse(m.as_bytes())
    assert meta.msg_to == ["b@example.de"]
    assert len(meta.msg_cc) == 2
    assert meta.recipient_note == ""
    assert meta.display_recipient() == "b@example.de +2 CC"


def test_only_cc():
    meta = parse(_mail(From="a@example.org", Cc="c@example.de").as_bytes())
    assert meta.display_recipient() == "CC: c@example.de"
    assert meta.recipient_note == ""


def test_only_bcc_in_sent_copy():
    meta = parse(_mail(From="a@example.org", Bcc="x@example.de, y@example.de").as_bytes())
    assert meta.recipient_note == "nur BCC"
    assert meta.display_recipient() == "nur BCC: 2 Empfänger"


def test_received_via_bcc_uses_delivered_to():
    m = _mail(From="a@example.org", Delivered_To="max@example.de")
    meta = parse(m.as_bytes())
    assert meta.recipient_note == "kein To/CC"
    assert meta.delivered_to == ["max@example.de"]
    assert meta.display_recipient() == "kein To/CC – Delivered-To: max@example.de"


def test_undisclosed_recipients():
    meta = parse(_mail(From="a@example.org", To="undisclosed-recipients:;").as_bytes())
    assert meta.msg_to == []
    assert meta.recipient_note == "undisclosed-recipients"


def test_no_recipient_at_all():
    meta = parse(_mail(From="a@example.org").as_bytes())
    assert meta.display_recipient() == "kein Empfänger im Header"


def test_attachments_and_inline():
    m = _mail(From="a@example.org", To="b@example.de", Subject="Anlagen")
    m.add_alternative("<p>Bild: <img src='cid:logo'></p>", subtype="html")
    html = m.get_payload()[1]
    html.add_related(b"\x89PNG...", maintype="image", subtype="png", cid="<logo>")
    m.add_attachment(b"%PDF-1.7 test", maintype="application", subtype="pdf", filename="Rechnung.pdf")
    m.add_attachment(b"a;b\n1;2\n", maintype="text", subtype="csv", filename="daten.csv")
    meta = parse(m.as_bytes())
    assert meta.attachment_count == 2
    assert [a.name for a in meta.attachments] == ["Rechnung.pdf", "daten.csv"]
    assert meta.inline_count == 1
    assert meta.attachments[0].size == len(b"%PDF-1.7 test")


def test_attached_email_counts():
    inner = _mail(From="x@example.org", To="y@example.org", Subject="Innen")
    outer = _mail(From="a@example.org", To="b@example.de", Subject="Weiterleitung")
    outer.add_attachment(inner)
    meta = parse(outer.as_bytes())
    assert meta.attachment_count == 1
    assert meta.attachments[0].mime == "message/rfc822"


def test_parse_does_not_change_bytes():
    raw = _mail(From="a@example.org", To="b@example.de").as_bytes()
    before = bytes(raw)
    parse(raw)
    assert raw == before
