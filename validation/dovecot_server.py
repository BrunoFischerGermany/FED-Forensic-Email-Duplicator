"""Startet einen lokalen Dovecot mit dem Testpostfach (für Tests und Validierung).

Benötigt ``dovecot`` und ``openssl`` im PATH. Läuft ohne Root (nutzt den
aktuellen Benutzer) oder als Root (nutzt die Systembenutzer dovecot/dovenull).

    python validation/dovecot_server.py      # startet und wartet, Strg+C beendet
"""

from __future__ import annotations

import getpass
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testmailbox  # noqa: E402

CONF = """\
base_dir = {d}/run
state_dir = {d}/state
log_path = {d}/dovecot.log
info_log_path = {d}/dovecot.log
protocols = imap
listen = 127.0.0.1
ssl = required
ssl_cert = <{d}/cert.pem
ssl_key = <{d}/key.pem
disable_plaintext_auth = yes
auth_mechanisms = plain login
first_valid_uid = 0
default_internal_user = {iuser}
default_internal_group = {igroup}
default_login_user = {luser}
passdb {{
  driver = passwd-file
  args = scheme=PLAIN username_format=%u {d}/users
}}
userdb {{
  driver = static
  args = uid={uid} gid={gid} home={d}/home/%u
}}
mail_location = maildir:~/Maildir
namespace inbox {{
  inbox = yes
  separator = /
  prefix =
}}
service imap-login {{
  chroot =
  inet_listener imap {{
    port = 0
  }}
  inet_listener imaps {{
    port = {port}
    ssl = yes
  }}
}}
service anvil {{
  chroot =
}}
service stats {{
  unix_listener stats-reader {{
    mode = 0666
  }}
  unix_listener stats-writer {{
    mode = 0666
  }}
}}
"""


@dataclass
class Server:
    host: str
    port: int
    cafile: Path
    user: str
    password: str
    root: Path
    proc: subprocess.Popen

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def available() -> bool:
    return bool(shutil.which("dovecot") and shutil.which("openssl"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(root: Path | None = None) -> Server:
    d = Path(root or tempfile.mkdtemp(prefix="fed-dovecot-")).resolve()
    for sub in ("run", "state", "home"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
         "-keyout", str(d / "key.pem"), "-out", str(d / "cert.pem")],
        check=True, capture_output=True,
    )
    (d / "users").write_text(f"{testmailbox.USER}:{{PLAIN}}{testmailbox.PASSWORD}::::::\n", encoding="utf-8")
    testmailbox.build(d / "home" / testmailbox.USER / "Maildir")
    root_mode = os.geteuid() == 0
    me = getpass.getuser()
    port = _free_port()
    uid, gid = os.getuid(), os.getgid()
    if root_mode:  # Dovecot liefert keine Mails an uid 0 aus: Postfach dem Systembenutzer geben
        import pwd

        pw = pwd.getpwnam("dovecot")
        uid, gid = pw.pw_uid, pw.pw_gid
        for dirpath, _dirs, files in os.walk(d / "home"):
            os.chown(dirpath, uid, gid)
            for f in files:
                os.chown(os.path.join(dirpath, f), uid, gid)
    conf = CONF.format(
        d=d, port=port, uid=uid, gid=gid,
        iuser="dovecot" if root_mode else me, igroup="dovecot" if root_mode else me,
        luser="dovenull" if root_mode else me,
    )
    (d / "dovecot.conf").write_text(conf, encoding="utf-8")
    if root_mode:
        os.chmod(d, 0o755)
    proc = subprocess.Popen(["dovecot", "-F", "-c", str(d / "dovecot.conf")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            log = (d / "dovecot.log").read_text(errors="replace") if (d / "dovecot.log").exists() else ""
            raise RuntimeError(f"Dovecot beendet: {proc.stderr.read().decode(errors='replace')}\n{log}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("Dovecot startet nicht")
    return Server("127.0.0.1", port, d / "cert.pem", testmailbox.USER, testmailbox.PASSWORD, d, proc)


if __name__ == "__main__":
    srv = start()
    print(f"Dovecot läuft: imaps://{srv.host}:{srv.port}  Benutzer {srv.user} / {srv.password}  CA {srv.cafile}")
    try:
        srv.proc.wait()
    except KeyboardInterrupt:
        srv.stop()
