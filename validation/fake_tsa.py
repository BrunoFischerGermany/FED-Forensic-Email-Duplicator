"""Lokaler Zeitstempeldienst (RFC 3161) für Tests – signiert mit ``openssl ts -reply``.

Erzeugt eine Test-CA und ein TSA-Zertifikat (extendedKeyUsage timeStamping) und
beantwortet POST-Anfragen auf ``http://127.0.0.1:<port>/``. Nur für Tests.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_CNF = """\
[ req ]
distinguished_name = dn
prompt = no
[ dn ]
CN = FED Test
[ v3_ca ]
basicConstraints = critical,CA:true
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
[ v3_tsa ]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,nonRepudiation
extendedKeyUsage = critical,timeStamping
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
[ tsa ]
default_tsa = tsa_config
[ tsa_config ]
dir = {dir}
serial = $dir/serial
crypto_device = builtin
signer_cert = $dir/tsa.pem
certs = $dir/ca.pem
signer_key = $dir/tsa.key
signer_digest = sha256
default_policy = 1.3.6.1.4.1.99999.1
digests = sha256, sha512
accuracy = secs:1
ordering = no
tsa_name = yes
ess_cert_id_chain = no
ess_cert_id_alg = sha256
"""


def available() -> bool:
    if not shutil.which("openssl"):
        return False
    r = subprocess.run(["openssl", "ts", "-help"], capture_output=True, text=True)
    return "reply" in (r.stdout + r.stderr)


class FakeTsa:
    def __init__(self, reject: bool = False) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="fed-tsa-"))
        self.reject = reject
        self.requests = 0
        self._make_certs()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def ca_file(self) -> Path:
        return self.dir / "ca.pem"

    def _ossl(self, *args: str) -> None:
        subprocess.run(["openssl", *args], cwd=self.dir, check=True, capture_output=True)

    def _make_certs(self) -> None:
        cnf = self.dir / "tsa.cnf"
        cnf.write_text(_CNF.format(dir=self.dir.as_posix()), encoding="utf-8")
        (self.dir / "serial").write_text("01\n")
        self._ossl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
                   "-days", "2", "-subj", "/CN=FED Test CA", "-config", "tsa.cnf", "-extensions", "v3_ca")
        self._ossl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "tsa.key", "-out", "tsa.csr",
                   "-subj", "/CN=FED Test TSA", "-config", "tsa.cnf")
        self._ossl("x509", "-req", "-in", "tsa.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
                   "-out", "tsa.pem", "-days", "2", "-extfile", "tsa.cnf", "-extensions", "v3_tsa")

    def _handler(self):
        tsa = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_POST(self) -> None:
                tsa.requests += 1
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with tempfile.TemporaryDirectory(dir=tsa.dir) as tmp:
                    q, r = Path(tmp) / "q.tsq", Path(tmp) / "r.tsr"
                    q.write_bytes(body)
                    if tsa.reject:
                        # PKIStatusInfo rejection(2) mit Text, ohne Token
                        txt = b"test rejection"
                        status = b"\x02\x01\x02" + b"\x30" + bytes([len(txt) + 2]) + b"\x0c" + bytes([len(txt)]) + txt
                        data = b"\x30" + bytes([len(status) + 2]) + b"\x30" + bytes([len(status)]) + status
                    else:
                        subprocess.run(["openssl", "ts", "-reply", "-config", str(tsa.dir / "tsa.cnf"),
                                        "-queryfile", str(q), "-out", str(r)], check=True, capture_output=True,
                                       cwd=tsa.dir)
                        data = r.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return H

    def __enter__(self) -> FakeTsa:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)
