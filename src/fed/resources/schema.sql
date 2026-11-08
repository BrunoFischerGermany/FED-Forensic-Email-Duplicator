-- FED Case-Datei (case.sqlite), Schema-Version 1
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_info (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    case_number  TEXT NOT NULL,
    examiner     TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    created_utc  TEXT NOT NULL,
    created_local TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    tool_sha256  TEXT,
    closed_utc   TEXT
);

CREATE TABLE IF NOT EXISTS config_snapshot (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    toml_text   TEXT NOT NULL,
    logo_file   TEXT,                       -- Original-Dateiname
    logo_mime   TEXT,                       -- image/png | image/jpeg
    logo_blob   BLOB,                       -- Logo selbst (max. 2 MB), Bericht nutzt nur diese Kopie
    logo_sha256 TEXT
);

CREATE TABLE IF NOT EXISTS system_check (
    id               INTEGER PRIMARY KEY,
    ts_utc           TEXT NOT NULL,
    user             TEXT,
    hostname         TEXT,
    os               TEXT,
    local_time       TEXT,
    utc_time         TEXT,
    tz               TEXT,
    ntp_offset_s     REAL,
    online           INTEGER,
    proxy            TEXT,
    target_path      TEXT,
    target_removable INTEGER,
    free_bytes       INTEGER
);

CREATE TABLE IF NOT EXISTS account (
    id                INTEGER PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    evidence_number   TEXT NOT NULL,
    server            TEXT NOT NULL,
    port              INTEGER NOT NULL,
    security          TEXT NOT NULL,          -- ssl | starttls
    auth_method       TEXT NOT NULL,          -- password | app_password_google | oauth_google | oauth_microsoft
    username          TEXT NOT NULL,
    autoconfig_source TEXT,
    tls_cert_sha256   TEXT,
    capabilities      TEXT,
    server_id         TEXT,                   -- Antwort auf ID
    namespaces        TEXT,
    quota             TEXT,
    date_from         TEXT,                   -- optionaler Zeitraum-Filter
    date_to           TEXT,
    notes             TEXT                    -- Notiz des Examiners zum Konto (Änderungen im Log)
);

CREATE TABLE IF NOT EXISTS attempt (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES account(id),
    nr          INTEGER NOT NULL,
    mode        TEXT NOT NULL,                -- resume | restart
    reason      TEXT,
    started_utc TEXT NOT NULL,
    ended_utc   TEXT,
    status      TEXT NOT NULL DEFAULT 'running', -- running | done | cancelled | failed
    phase       TEXT,
    moved_to    TEXT,
    deleted     INTEGER NOT NULL DEFAULT 0,
    avg_bytes_s REAL,
    UNIQUE (account_id, nr)
);

CREATE TABLE IF NOT EXISTS folder (
    id           INTEGER PRIMARY KEY,
    account_id   INTEGER NOT NULL REFERENCES account(id),
    raw_name     TEXT NOT NULL,
    display_name TEXT NOT NULL,
    delimiter    TEXT,
    flags        TEXT,
    namespace    TEXT NOT NULL DEFAULT 'personal', -- personal | other | shared
    uidvalidity  INTEGER,
    count_server INTEGER,
    size_server  INTEGER,
    selected     INTEGER NOT NULL DEFAULT 1,
    count_saved  INTEGER NOT NULL DEFAULT 0,
    status_before TEXT,                       -- JSON: MESSAGES, UNSEEN, UIDNEXT, UIDVALIDITY
    status_after  TEXT,
    UNIQUE (account_id, raw_name)
);

CREATE TABLE IF NOT EXISTS message (
    id              INTEGER PRIMARY KEY,
    attempt_id      INTEGER NOT NULL REFERENCES attempt(id),
    folder_id       INTEGER NOT NULL REFERENCES folder(id),
    uid             INTEGER NOT NULL,
    zip_path        TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    md5             TEXT,
    sha512          TEXT,
    size            INTEGER NOT NULL,
    size_server     INTEGER,
    internaldate    TEXT,
    date_header     TEXT,
    zip_time_source TEXT,
    flags           TEXT,
    gm_msgid        TEXT,
    gm_labels       TEXT,
    message_id      TEXT,
    msg_from        TEXT,
    msg_to          TEXT,                    -- JSON-Liste, dekodiert
    msg_cc          TEXT,                    -- JSON-Liste
    msg_bcc         TEXT,                    -- JSON-Liste (meist nur in gesendeten Mails vorhanden)
    delivered_to    TEXT,                    -- Delivered-To / X-Original-To / Envelope-To, falls vorhanden
    recipient_note  TEXT,                    -- z. B. 'nur BCC', 'kein To/CC', 'undisclosed-recipients'
    attachment_count INTEGER NOT NULL DEFAULT 0, -- echte Anlagen inkl. angehängter Mails
    inline_count    INTEGER NOT NULL DEFAULT 0,  -- eingebettete Bilder (Content-ID, inline)
    attachments     TEXT,                    -- JSON: [{name, mime, size, sha256}]
    subject         TEXT,
    subject_raw     TEXT,
    saved_utc       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'valid', -- valid | discarded
    dkim_result     TEXT,                    -- pass | fail | permerror | temperror | none (NULL = nicht geprüft)
    dkim_domain     TEXT,
    dkim_selector   TEXT,
    dkim_detail     TEXT,
    auth_results    TEXT                     -- JSON: Authentication-Results des Anbieters (spf, dkim, dmarc)
);
CREATE INDEX IF NOT EXISTS idx_message_folder_uid ON message (folder_id, uid);

-- Bei der DKIM-Prüfung abgefragte DNS-Einträge (öffentliche Schlüssel), für spätere Nachprüfung
CREATE TABLE IF NOT EXISTS dns_record (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,                -- <selector>._domainkey.<domain>
    record      TEXT,                         -- TXT-Inhalt (NULL = nicht vorhanden)
    sha256      TEXT,
    status      TEXT NOT NULL,                -- ok | nxdomain | error
    error       TEXT,
    fetched_utc TEXT NOT NULL,
    attempt_id  INTEGER REFERENCES attempt(id)
);

-- Nur anhängend; entry_hash = SHA-256 über Inhalt + prev_hash
CREATE TABLE IF NOT EXISTS log (
    id         INTEGER PRIMARY KEY,
    ts_utc     TEXT NOT NULL,
    attempt_id INTEGER,
    level      TEXT NOT NULL,                 -- INFO | WARNING | ERROR
    event      TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '',
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS log_no_update BEFORE UPDATE ON log
BEGIN SELECT RAISE(ABORT, 'log ist nur anhängend'); END;
CREATE TRIGGER IF NOT EXISTS log_no_delete BEFORE DELETE ON log
BEGIN SELECT RAISE(ABORT, 'log ist nur anhängend'); END;

CREATE TABLE IF NOT EXISTS output (
    id          INTEGER PRIMARY KEY,
    file_name   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    md5         TEXT,
    sha512      TEXT,
    size        INTEGER NOT NULL,
    created_utc TEXT NOT NULL,
    account_id  INTEGER                      -- gesetzt bei Dateien eines einzelnen Kontos (ZIP je Konto)
);
