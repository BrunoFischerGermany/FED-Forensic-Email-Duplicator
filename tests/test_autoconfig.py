from types import SimpleNamespace

from fed.core.autoconfig import Provider, discover
from fed.core.syscheck import is_removable

XML = b"""<clientConfig version="1.1"><emailProvider id="x">
<incomingServer type="imap"><hostname>imap.provider.example</hostname><port>993</port>
<socketType>SSL</socketType><username>%EMAILLOCALPART%</username></incomingServer>
</emailProvider></clientConfig>"""

GOOGLE_XML = XML.replace(b"imap.provider.example", b"imap.gmail.com")


def test_ispdb_step3():
    def get(url):
        return XML if url.startswith("https://autoconfig.thunderbird.net/v1.1/example.de") else None

    s, log = discover("max@example.de", http_get=get, dns_lookup=lambda *a: [], port_open=lambda *a: False)
    assert s.host == "imap.provider.example" and s.username == "max" and s.source == "Thunderbird-ISPDB"
    assert len(log) == 3 and "gefunden" in log[2]


def test_mx_detects_google_workspace():
    def get(url):
        return GOOGLE_XML if url.endswith("/google.com") else None

    def dns(name, rtype):
        if rtype == "MX":
            return [SimpleNamespace(preference=1, exchange="aspmx.l.google.com.")]
        return []

    s, log = discover("max@firma.example", http_get=get, dns_lookup=dns, port_open=lambda *a: False)
    assert s.host == "imap.gmail.com" and s.provider is Provider.GOOGLE
    assert log[-1].startswith("4. MX")


def test_srv_and_guess():
    def dns(name, rtype):
        if rtype == "SRV":
            return [SimpleNamespace(priority=0, target="mail.hoster.example.", port=993)]
        return []

    s, _ = discover("a@b.example", http_get=lambda u: None, dns_lookup=dns, port_open=lambda *a: False)
    assert s.host == "mail.hoster.example" and s.source == "DNS-SRV"

    s, log = discover("a@b.example", http_get=lambda u: None, dns_lookup=lambda *a: [],
                      port_open=lambda h, p: h == "mail.b.example")
    assert s.host == "mail.b.example" and s.source == "geraten"
    assert len(log) == 6


def test_nothing_found():
    s, log = discover("a@b.example", http_get=lambda u: None, dns_lookup=lambda *a: [], port_open=lambda *a: False)
    assert s is None and len(log) == 6


def test_umlaut_domain_idna():
    seen = []
    discover("max@müller.example", http_get=lambda u: seen.append(u), dns_lookup=lambda *a: [],
             port_open=lambda *a: False)
    assert any("xn--mller-kva.example" in u for u in seen)


def test_is_removable_does_not_crash(tmp_path):
    assert is_removable(tmp_path) in (True, False, None)
