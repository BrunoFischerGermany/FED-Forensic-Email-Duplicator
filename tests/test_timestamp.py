import hashlib
import shutil
import subprocess

import pytest

from fed.core import timestamp as ts
from validation.fake_tsa import FakeTsa, available

needs_openssl = pytest.mark.skipif(not available(), reason="openssl ts nicht verfügbar")


def test_request_is_valid_der_for_openssl(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl fehlt")
    digest = hashlib.sha256(b"x").digest()
    q = tmp_path / "q.tsq"
    q.write_bytes(ts.build_request(digest, 12345))
    out = subprocess.run(["openssl", "ts", "-query", "-in", str(q), "-text"], capture_output=True, text=True,
                         check=True).stdout
    assert "sha256" in out and "0x3039" in out and "Certificate required: yes" in out


def test_request_rejects_wrong_digest_length():
    with pytest.raises(ValueError):
        ts.build_request(b"short", 1)


def test_der_parser_rejects_garbage():
    with pytest.raises(ts.TimestampError):
        ts.parse_response(b"\x30\x80\x00\x00")


@needs_openssl
def test_timestamp_roundtrip_and_openssl_verify(tmp_path):
    data = tmp_path / "case.zip"
    data.write_bytes(b"forensic" * 1000)
    h = hashlib.sha256(data.read_bytes()).hexdigest()
    tsr = tmp_path / "case.zip.tsr"
    with FakeTsa() as tsa:
        info = ts.request_timestamp(h, tsa.url, tsr)
        assert info.status == 0 and info.hashed_message == h and info.gen_time.endswith("Z")
        assert info.accuracy == "1 s" and info.policy == "1.3.6.1.4.1.99999.1"
        assert (tmp_path / "case.zip.tsq").exists()
        r = subprocess.run(["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(data),
                            "-CAfile", str(tsa.ca_file)], capture_output=True, text=True)
        assert "Verification: OK" in r.stdout, r.stderr


@needs_openssl
def test_rejected_timestamp_raises(tmp_path):
    with FakeTsa(reject=True) as tsa, pytest.raises(ts.TimestampError, match="abgelehnt"):
        ts.request_timestamp("00" * 32, tsa.url, tmp_path / "x.tsr")
    assert not (tmp_path / "x.tsr").exists()


def test_unreachable_tsa_raises(tmp_path):
    with pytest.raises(ts.TimestampError, match="nicht erreichbar"):
        ts.request_timestamp("00" * 32, "http://127.0.0.1:9/", tmp_path / "x.tsr", timeout=2)
