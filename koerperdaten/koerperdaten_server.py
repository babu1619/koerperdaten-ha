#!/usr/bin/env python3
"""
Körperdaten-Server 2
====================

Zentrale Datenhaltung für den Körperdaten-Tracker – mit HTTPS, Benutzerkonten
und SQLite. Jeder Benutzer sieht nur seine eigenen Daten.

Läuft auf Raspberry Pi oder PC mit Python 3.8 oder neuer. Für das automatisch
erzeugte HTTPS-Zertifikat wird entweder das Python-Paket "cryptography" oder
das Programm "openssl" gebraucht (auf dem Raspberry Pi vorhanden).

Start:
    python3 koerperdaten_server.py                 # HTTPS auf Port 8443
    python3 koerperdaten_server.py --port 9443

Benutzer verwalten (alternativ im Tracker unter Einstellungen):
    python3 koerperdaten_server.py benutzer-anlegen NAME [--admin]
    python3 koerperdaten_server.py benutzer-liste
    python3 koerperdaten_server.py passwort-setzen NAME
    python3 koerperdaten_server.py benutzer-loeschen NAME
    python3 koerperdaten_server.py importieren NAME DATEI.xml

Schnittstelle:
    GET  /api/status                       ohne Anmeldung
    POST /api/einrichtung                  ersten Administrator anlegen (nur solange es keine Benutzer gibt)
    POST /api/anmelden | /api/abmelden
    GET  /api/ich | POST /api/ich/passwort
    GET  /api/daten | PUT /api/daten       XML-Daten des angemeldeten Benutzers (ETag / If-Match)
    GET  /api/sicherungen | POST /api/sicherungen/<id>/wiederherstellen
    GET  /api/benutzer | POST /api/benutzer | POST /api/benutzer/<name> | DELETE /api/benutzer/<name>   (Administratoren)
"""

import argparse
import base64
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

APP = "koerperdaten"
VERSION = "2.5"
MAX_BODY = 20 * 1024 * 1024        # XML-Daten
MAX_FOTO_BODY = 48 * 1024 * 1024   # ein Foto mit Original, Zuschnitt und Vorschau (Base64)
MAX_JSON = 64 * 1024               # Anmeldung, Verwaltung
BACKUP_INTERVAL = 10 * 60          # höchstens alle 10 Minuten eine Sicherung je Benutzer
SESSION_DAYS = 90                  # Anmeldung bleibt 90 Tage ab letzter Nutzung gültig
MIN_PASSWORD = 8
NAME_RE = re.compile(r"^[\w.\-]{2,32}$")


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def make_etag(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


# ---------------------------------------------------------------- Passwörter
def _b64(b):
    return base64.b64encode(b).decode()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    try:
        h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return f"scrypt$16384$8$1${_b64(salt)}${_b64(h)}"
    except (AttributeError, ValueError):
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 600_000)
        return f"pbkdf2$600000${_b64(salt)}${_b64(h)}"


def check_password(pw: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt":
            n, r, p = map(int, parts[1:4])
            salt, h = base64.b64decode(parts[4]), base64.b64decode(parts[5])
            calc = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=len(h))
        elif parts[0] == "pbkdf2":
            salt, h = base64.b64decode(parts[2]), base64.b64decode(parts[3])
            calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, int(parts[1]))
        else:
            return False
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


DUMMY_HASH = hash_password(secrets.token_hex(8))   # gleicht Antwortzeiten bei unbekannten Namen an


def password_problem(pw):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        return f"Das Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein"
    if len(pw) > 200:
        return "Das Passwort ist zu lang"
    return None


def name_problem(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        return "Benutzernamen: 2–32 Zeichen, Buchstaben, Ziffern, Punkt, Binde- oder Unterstrich"
    return None


def validate_xml(data: bytes):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return f"Kein gültiges XML: {e}"
    if root.tag != "koerperdaten":
        return "Wurzelelement <koerperdaten> fehlt"
    return None


class ApiError(Exception):
    def __init__(self, code, msg, extra=None):
        super().__init__(msg)
        self.code, self.msg, self.extra = code, msg, extra or {}


# ---------------------------------------------------------------- Datenbank
SCHEMA = """
CREATE TABLE IF NOT EXISTS benutzer(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  passwort TEXT NOT NULL,
  admin INTEGER NOT NULL DEFAULT 0,
  angelegt TEXT NOT NULL,
  letzte_anmeldung TEXT
);
CREATE TABLE IF NOT EXISTS sitzungen(
  token_hash TEXT PRIMARY KEY,
  benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  angelegt TEXT NOT NULL,
  laeuft_ab TEXT NOT NULL,
  geraet TEXT
);
CREATE TABLE IF NOT EXISTS daten(
  benutzer_id INTEGER PRIMARY KEY REFERENCES benutzer(id) ON DELETE CASCADE,
  xml BLOB NOT NULL,
  etag TEXT NOT NULL,
  geaendert TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sicherungen(
  id INTEGER PRIMARY KEY,
  benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  angelegt TEXT NOT NULL,
  xml BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sicherungen ON sicherungen(benutzer_id, angelegt);
CREATE INDEX IF NOT EXISTS idx_sitzungen ON sitzungen(benutzer_id);
"""

# Datenbank-Versionen. Jede Änderung an der Struktur bekommt eine neue Nummer und einen
# Umbauschritt; bestehende Schritte werden nie verändert. Die Nummer steht in PRAGMA user_version.
MIGRATIONS = {
    1: SCHEMA,                                   # Grundaufbau (Programmversion 2.0)
    2: """CREATE TABLE IF NOT EXISTS meta(       -- Programmversion 2.1
            schluessel TEXT PRIMARY KEY,
            wert TEXT NOT NULL
          );""",
}
SCHEMA_VERSION = max(MIGRATIONS)
KEEP_UPDATE_BACKUPS = 10


def integrity_problem(path: Path):
    """Prüft eine Datenbankdatei, ohne sie zu verändern. None = in Ordnung."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            res = con.execute("PRAGMA integrity_check").fetchone()[0]
            has = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='benutzer'").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"Datei nicht lesbar: {e}"
    if res != "ok":
        return f"Datenbank beschädigt: {res}"
    if not has:
        return "Keine Körperdaten-Datenbank (Tabelle benutzer fehlt)"
    return None


def write_handover(dbfile: Path, folder: Path):
    """Legt eine geprüfte Kopie der Datenbank für den Umzug auf eine andere Installation ab."""
    folder.mkdir(parents=True, exist_ok=True)
    target, tmp = folder / "koerperdaten.db", folder / "koerperdaten.db.tmp"
    src = sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()
    problem = integrity_problem(tmp)
    if problem:
        tmp.unlink(missing_ok=True)
        print(f"Umzugsdaten NICHT geschrieben: {problem}")
        return False
    os.replace(tmp, target)
    if copy_photo_db(dbfile.parent / "fotos.db", folder / "fotos.db"):
        print(f"Fotos für den Umzug geschrieben: {folder / 'fotos.db'}")
    else:
        (folder / "fotos.db").unlink(missing_ok=True)
    cert_dir = dbfile.parent / "zertifikat"      # selbst signiertes Zertifikat mitnehmen, dann entfällt die neue Browser-Warnung
    if (cert_dir / "zertifikat.pem").exists():
        import shutil
        shutil.rmtree(folder / "zertifikat", ignore_errors=True)
        shutil.copytree(cert_dir, folder / "zertifikat")
    (folder / "info.txt").write_text(f"Körperdaten-Server {VERSION}\nStand: {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    print(f"Umzugsdaten geschrieben: {target}")
    return True


FOTO_ANSICHTEN = ("vorne", "seite", "hinten")
FOTO_ARTEN = {"bild": 4 * 1024 * 1024, "vorschau": 512 * 1024, "original": 30 * 1024 * 1024}
FOTO_SCHEMA = """
CREATE TABLE IF NOT EXISTS fotos(
  id INTEGER PRIMARY KEY,
  benutzer_id INTEGER NOT NULL,
  datum TEXT NOT NULL,
  ansicht TEXT NOT NULL,
  zuschnitt TEXT NOT NULL DEFAULT '{}',
  original BLOB NOT NULL,
  original_typ TEXT NOT NULL,
  bild BLOB NOT NULL,
  vorschau BLOB NOT NULL,
  breite INTEGER,
  hoehe INTEGER,
  geaendert TEXT NOT NULL,
  UNIQUE(benutzer_id, datum, ansicht)
);
CREATE INDEX IF NOT EXISTS idx_fotos_benutzer ON fotos(benutzer_id, datum);
"""


def image_type(data: bytes):
    """Bildformat an den ersten Bytes erkennen; nur JPEG, PNG und WebP werden angenommen."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class PhotoStore:
    """Fotos liegen in einer eigenen Datei fotos.db neben der Hauptdatenbank.
    So bleiben die Sicherungen vor Updates klein, und der Umzug nimmt die Fotos trotzdem mit."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=5000")
        if self.con.execute("PRAGMA user_version").fetchone()[0] < 1:
            self.con.executescript("BEGIN;\n" + FOTO_SCHEMA + "\nPRAGMA user_version=1;\nCOMMIT;")
        _private(path)

    def q(self, sql, args=()):
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    def list(self, uid):
        return [{"id": r["id"], "datum": r["datum"], "ansicht": r["ansicht"], "zuschnitt": json.loads(r["zuschnitt"] or "{}"),
                 "breite": r["breite"], "hoehe": r["hoehe"], "geaendert": r["geaendert"], "groesse": r["groesse"]}
                for r in self.q("SELECT id, datum, ansicht, zuschnitt, breite, hoehe, geaendert, "
                                "length(original)+length(bild)+length(vorschau) AS groesse "
                                "FROM fotos WHERE benutzer_id=? ORDER BY datum, ansicht", (uid,))]

    def get(self, uid, foto_id, kind):
        col = {"bild": "bild", "vorschau": "vorschau", "original": "original"}[kind]
        rows = self.q(f"SELECT {col} AS daten, original_typ, geaendert FROM fotos WHERE id=? AND benutzer_id=?", (foto_id, uid))
        if not rows:
            raise ApiError(404, "Dieses Foto gibt es nicht")
        data = bytes(rows[0]["daten"])
        return data, (rows[0]["original_typ"] if kind == "original" else image_type(data) or "image/jpeg")

    def put(self, uid, datum, ansicht, zuschnitt, bild, vorschau, original=None, original_typ=None, breite=None, hoehe=None):
        t = iso(now_utc())
        z = json.dumps(zuschnitt, ensure_ascii=False)
        with self.lock:
            if original is None:
                cur = self.con.execute("UPDATE fotos SET zuschnitt=?, bild=?, vorschau=?, geaendert=? "
                                       "WHERE benutzer_id=? AND datum=? AND ansicht=?", (z, bild, vorschau, t, uid, datum, ansicht))
                if cur.rowcount == 0:
                    raise ApiError(400, "Für dieses Foto fehlt das Original")
            else:
                self.con.execute("""INSERT INTO fotos(benutzer_id, datum, ansicht, zuschnitt, original, original_typ, bild, vorschau, breite, hoehe, geaendert)
                                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                                    ON CONFLICT(benutzer_id, datum, ansicht) DO UPDATE SET zuschnitt=excluded.zuschnitt,
                                      original=excluded.original, original_typ=excluded.original_typ, bild=excluded.bild,
                                      vorschau=excluded.vorschau, breite=excluded.breite, hoehe=excluded.hoehe, geaendert=excluded.geaendert""",
                                 (uid, datum, ansicht, z, original, original_typ, bild, vorschau, breite, hoehe, t))
            row = self.con.execute("SELECT id FROM fotos WHERE benutzer_id=? AND datum=? AND ansicht=?", (uid, datum, ansicht)).fetchone()
        return {"id": row["id"], "geaendert": t}

    def delete(self, uid, datum, ansicht):
        with self.lock:
            return self.con.execute("DELETE FROM fotos WHERE benutzer_id=? AND datum=? AND ansicht=?", (uid, datum, ansicht)).rowcount

    def delete_user(self, uid):
        self.q("DELETE FROM fotos WHERE benutzer_id=?", (uid,))

    def remove_orphans(self, user_ids):
        """Fotos gelöschter Benutzer entfernen."""
        ids = set(user_ids)
        orphans = {r["benutzer_id"] for r in self.q("SELECT DISTINCT benutzer_id FROM fotos")} - ids
        for uid in orphans:
            self.delete_user(uid)
        if orphans:
            with self.lock:
                self.con.execute("VACUUM")
        return len(orphans)

    def stats(self):
        r = self.q("SELECT COUNT(*) AS n, COUNT(DISTINCT benutzer_id) AS b, "
                   "COALESCE(SUM(length(original)+length(bild)+length(vorschau)),0) AS groesse FROM fotos")[0]
        return r["n"], r["b"], r["groesse"]

    def backup_to(self, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        dst = sqlite3.connect(str(tmp))
        try:
            with self.lock:
                self.con.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
        os.replace(tmp, target)
        return target

    def close(self):
        with self.lock:
            try:
                self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.con.close()


def copy_photo_db(src: Path, target: Path):
    """Konsistente Kopie einer fotos.db (auch während der Server läuft). False = keine Fotos vorhanden."""
    if not src.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    b = sqlite3.connect(str(tmp))
    try:
        a.backup(b)
        b.execute("PRAGMA journal_mode=DELETE")
    finally:
        b.close()
        a.close()
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return True


class Database:
    def __init__(self, path: Path, keep_backups: int, migrate=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists() and path.stat().st_size > 0
        self.path = path
        self.update_dir = path.parent / "vor-update"
        self.keep = max(1, keep_backups)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute("PRAGMA busy_timeout=5000")
        if migrate:
            self._migrate(existed)

    # ---- Versionen und Umbau
    def schema_version(self):
        return self.con.execute("PRAGMA user_version").fetchone()[0]

    def has_table(self, name):
        return self.con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0] > 0

    def meta(self, key):
        if not self.has_table("meta"):
            return None
        row = self.con.execute("SELECT wert FROM meta WHERE schluessel=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.q("INSERT INTO meta(schluessel, wert) VALUES(?,?) ON CONFLICT(schluessel) DO UPDATE SET wert=excluded.wert", (key, value))

    def backup_to(self, target: Path):
        """Konsistente Kopie der Datenbank, auch während der Server läuft."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        dst = sqlite3.connect(str(tmp))
        try:
            with self.lock:
                self.con.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
        os.replace(tmp, target)
        return target

    def _prune_update_backups(self):
        files = sorted(self.update_dir.glob("koerperdaten-*.db"), key=lambda f: f.stat().st_mtime)
        for old in files[:-KEEP_UPDATE_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass

    def _migrate(self, existed):
        current = self.schema_version()
        has_data = existed and self.has_table("benutzer")
        if current > SCHEMA_VERSION:
            sys.exit(f"Die Datenbank {self.path} stammt von einer neueren Programmversion "
                     f"(Datenbank-Version {current}, dieses Programm kennt nur {SCHEMA_VERSION}).\n"
                     f"Bitte die neuere Programmdatei verwenden oder eine Sicherung aus {self.update_dir} zurückspielen.")
        old_program = self.meta("programmversion") if has_data else None
        if has_data and (current < SCHEMA_VERSION or old_program != VERSION):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.backup_to(self.update_dir / f"koerperdaten-vor-{VERSION}-{stamp}.db")
            self._prune_update_backups()
            print(f"Update von {old_program or 'älterer Version'} auf {VERSION}: Datenbank gesichert nach {target}")
        for v in range(current + 1, SCHEMA_VERSION + 1):
            try:
                self.con.executescript("BEGIN;\n" + MIGRATIONS[v] + f"\nPRAGMA user_version={v};\nCOMMIT;")
            except sqlite3.Error as e:
                try:
                    self.con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                sys.exit(f"Umbau der Datenbank auf Version {v} fehlgeschlagen: {e}\n"
                         f"Die Datenbank ist unverändert. Sicherungen liegen in {self.update_dir}.")
            if has_data:
                print(f"Datenbank auf Version {v} umgebaut.")
        self.set_meta("programmversion", VERSION)

    def close(self):
        """Schreibt alles aus der WAL-Datei in die Datenbank und schließt sie sauber."""
        with self.lock:
            try:
                self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.con.close()

    def q(self, sql, args=()):
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    # ---- Benutzer
    def user_count(self):
        return self.one("SELECT COUNT(*) AS n FROM benutzer")["n"]

    def admin_count(self):
        return self.one("SELECT COUNT(*) AS n FROM benutzer WHERE admin=1")["n"]

    def user(self, name):
        return self.one("SELECT * FROM benutzer WHERE name=?", (name,))

    def create_user(self, name, password, admin=False):
        p = name_problem(name) or password_problem(password)
        if p:
            raise ApiError(400, p)
        with self.lock:
            if self.user(name):
                raise ApiError(409, f"Den Benutzer „{name}“ gibt es schon")
            self.q("INSERT INTO benutzer(name, passwort, admin, angelegt) VALUES(?,?,?,?)",
                   (name, hash_password(password), 1 if admin else 0, iso(now_utc())))
            return self.user(name)

    def list_users(self):
        return self.q("""SELECT b.name, b.admin, b.angelegt, b.letzte_anmeldung,
                                d.geaendert AS daten_geaendert, LENGTH(d.xml) AS groesse
                         FROM benutzer b LEFT JOIN daten d ON d.benutzer_id=b.id ORDER BY b.name COLLATE NOCASE""")

    def set_password(self, user_id, password, keep_token_hash=None):
        p = password_problem(password)
        if p:
            raise ApiError(400, p)
        with self.lock:
            self.q("UPDATE benutzer SET passwort=? WHERE id=?", (hash_password(password), user_id))
            self.q("DELETE FROM sitzungen WHERE benutzer_id=? AND token_hash IS NOT ?", (user_id, keep_token_hash))

    def set_admin(self, user_id, admin):
        with self.lock:
            row = self.one("SELECT admin FROM benutzer WHERE id=?", (user_id,))
            if row and row["admin"] and not admin and self.admin_count() <= 1:
                raise ApiError(400, "Der letzte Administrator kann nicht herabgestuft werden")
            self.q("UPDATE benutzer SET admin=? WHERE id=?", (1 if admin else 0, user_id))

    def delete_user(self, user_id):
        with self.lock:
            row = self.one("SELECT admin FROM benutzer WHERE id=?", (user_id,))
            if row and row["admin"] and self.admin_count() <= 1:
                raise ApiError(400, "Der letzte Administrator kann nicht gelöscht werden")
            self.q("DELETE FROM benutzer WHERE id=?", (user_id,))

    # ---- Sitzungen
    def create_session(self, user_id, device=""):
        token = secrets.token_urlsafe(32)
        t = now_utc()
        with self.lock:
            self.q("INSERT INTO sitzungen VALUES(?,?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user_id, iso(t), iso(t + timedelta(days=SESSION_DAYS)), device[:200]))
            self.q("UPDATE benutzer SET letzte_anmeldung=? WHERE id=?", (iso(t), user_id))
            self.q("DELETE FROM sitzungen WHERE laeuft_ab < ?", (iso(t),))
        return token

    def session_user(self, token):
        th = hashlib.sha256(token.encode()).hexdigest()
        row = self.one("""SELECT b.*, s.laeuft_ab, s.token_hash FROM sitzungen s JOIN benutzer b ON b.id=s.benutzer_id
                          WHERE s.token_hash=?""", (th,))
        if not row:
            return None
        t = now_utc()
        expires = datetime.fromisoformat(row["laeuft_ab"])
        if expires < t:
            self.q("DELETE FROM sitzungen WHERE token_hash=?", (th,))
            return None
        if expires - t < timedelta(days=SESSION_DAYS - 1):       # gleitend verlängern, höchstens einmal am Tag
            self.q("UPDATE sitzungen SET laeuft_ab=? WHERE token_hash=?", (iso(t + timedelta(days=SESSION_DAYS)), th))
        return row

    def delete_session(self, token_hash):
        self.q("DELETE FROM sitzungen WHERE token_hash=?", (token_hash,))

    # ---- Daten und Sicherungen
    def get_data(self, user_id):
        return self.one("SELECT xml, etag, geaendert FROM daten WHERE benutzer_id=?", (user_id,))

    def _backup_current(self, user_id, force=False):
        cur = self.get_data(user_id)
        if not cur:
            return
        last = self.one("SELECT angelegt FROM sicherungen WHERE benutzer_id=? ORDER BY angelegt DESC LIMIT 1", (user_id,))
        if not force and last and (now_utc() - datetime.fromisoformat(last["angelegt"])).total_seconds() < BACKUP_INTERVAL:
            return
        self.q("INSERT INTO sicherungen(benutzer_id, angelegt, xml) VALUES(?,?,?)", (user_id, iso(now_utc()), cur["xml"]))
        self.q("""DELETE FROM sicherungen WHERE benutzer_id=? AND id NOT IN
                  (SELECT id FROM sicherungen WHERE benutzer_id=? ORDER BY angelegt DESC LIMIT ?)""",
               (user_id, user_id, self.keep))

    def put_data(self, user_id, data: bytes, if_match=None):
        with self.lock:
            cur = self.get_data(user_id)
            current = cur["etag"] if cur else None
            if if_match is not None:
                expect_new = if_match.strip() == '"neu"'
                if (expect_new and current is not None) or (not expect_new and if_match.strip() != current):
                    raise ApiError(412, "Die Daten wurden inzwischen von einem anderen Gerät geändert", {"etag": current})
            self.con.execute("BEGIN IMMEDIATE")
            try:
                self._backup_current(user_id)
                tag = make_etag(data)
                self.q("""INSERT INTO daten(benutzer_id, xml, etag, geaendert) VALUES(?,?,?,?)
                          ON CONFLICT(benutzer_id) DO UPDATE SET xml=excluded.xml, etag=excluded.etag, geaendert=excluded.geaendert""",
                       (user_id, data, tag, iso(now_utc())))
                self.con.execute("COMMIT")
            except Exception:
                self.con.execute("ROLLBACK")
                raise
            return tag

    def list_backups(self, user_id):
        return self.q("SELECT id, angelegt, LENGTH(xml) AS groesse FROM sicherungen WHERE benutzer_id=? ORDER BY angelegt DESC", (user_id,))

    def restore_backup(self, user_id, backup_id):
        with self.lock:
            row = self.one("SELECT xml FROM sicherungen WHERE id=? AND benutzer_id=?", (backup_id, user_id))
            if not row:
                raise ApiError(404, "Diese Sicherung gibt es nicht")
            self._backup_current(user_id, force=True)
            return self.put_data(user_id, bytes(row["xml"]))


# ---------------------------------------------------------------- Anmeldeschutz
class LoginThrottle:
    """Bremst Passwort-Raten: max. 5 Fehlversuche je Name und 20 je Adresse in 10 Minuten."""

    def __init__(self):
        self.lock = threading.Lock()
        self.fails = {}

    def _recent(self, key):
        t = time.time()
        lst = [x for x in self.fails.get(key, []) if t - x < 600]
        self.fails[key] = lst
        return lst

    def blocked(self, ip, name):
        with self.lock:
            return len(self._recent("ip:" + ip)) >= 20 or len(self._recent("n:" + name.lower())) >= 5

    def fail(self, ip, name):
        with self.lock:
            for k in ("ip:" + ip, "n:" + name.lower()):
                self._recent(k).append(time.time())

    def ok(self, name):
        with self.lock:
            self.fails.pop("n:" + name.lower(), None)


# ---------------------------------------------------------------- App-Installation (PWA)
PWA_MANIFEST = {
    "name": "Körperdaten",
    "short_name": "Körperdaten",
    "description": "Körper- und Fitnessdaten erfassen, auswerten und mit Zielen vergleichen",
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "lang": "de",
    "background_color": "#F2F3EF",
    "theme_color": "#1F6F78",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

# Service Worker: Seite und Bibliotheken für einen schnellen Start zwischenspeichern; Daten (/api) immer frisch vom Server
SERVICE_WORKER = """
const CACHE = 'koerperdaten-__VERSION__';
const SHELL = ['./', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET') return;
  if (url.origin === location.origin) {
    if (url.pathname.startsWith('/api/') || url.pathname === '/ca.crt') return;
    // zuerst Netzwerk (Updates kommen sofort an), ohne Verbindung der gespeicherte Stand
    e.respondWith(fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req).then(r => r || caches.match('./'))));
    return;
  }
  if (/(^|\\.)(cdnjs\\.cloudflare\\.com|fonts\\.googleapis\\.com|fonts\\.gstatic\\.com)$/.test(url.hostname)) {
    e.respondWith(caches.match(req).then(r => r || fetch(req).then(res => {
      const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res;
    })));
  }
});
"""

PWA_ICONS = {
    "icon-192.png": "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",
    "icon-512.png": "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAA5dUlEQVR42u3de5QdZ3nn+9/zvlV7727dfUVYlizb+NK2YzAYMJARZDjJmmQCyZm0wkk4ZyUZgpmxMcRcMmbI2eoJg8G3BBuTmHDJzCE5jPrkkDi3RVYmoU8ChgQMxHbbGGFZsrGQbd3dl1276n3OH7tbasmSLMm67L37+1nLLFbrUtpVtev5vU+99ZYJx8I0PBzWDA3ZmCSNjFSS/MDftPydzcEFp9cWFFO7rzCzhru/zhQyuS9TsKtUJUmSyy62YIvk7pKM3QugT7nMzJPvMfn3JEkxSMnvl9kOVyrN7GvuPl0bWPzAxLZiYsunRyYPeg1uNuMaSWPj467R0XSwazBeqJDhyDSbQVJQ52Srnlfsb2yeEavpoUz2coWwyl1XyKsLZWGxmZ0uC7IY934HPKV934iqkpxzF8B8qTw253ooWQh7y1Hnepjk7tvkabcsbjDTA0ppUyn/ThUb41vuGHn2eX/n8HDU0JBJShoZSexkAsCLHuVraMgOHOEvbzYHG7vblyfpdSa/0hUuc08XhSxbYjHr/KaU9p3IKbncJbPU+WtMcrc5X4bA7gYwv3oBnuZcA33OtTHITBaC7R04hTATDkqlstxlFh41pYdc9t0gfW16cf7glpH9OgWmZjOK7gAB4BiLfjn3F1Z98MOXqvLXetK/NU+vshBWWl6T3OWp6hT7qkqSkksyM5O7yczYzwBw9BFB7i4zd987YgqKMViMshAlM3m7kKe02S1804L+QtG+vumWjzy839/UbGaEAQLAwTWbQZddZlq7dl9rf/36uPLr332Nuf+8XGukdFWoNaLk8rKUV6W7q7JOejVJgf0JACchGEhJPjNpyhQtZmZZJsmUiulKCvfLNOZmX9r82iu/ceC1XQ895NwmmN8FqzPaX78+ddpPkobXx5Xndoq+yX9GFi61PJdXlbzdltwrmSSX0bYHgG6JBJ5k8s5dBIuW57IYZ67b6WGX/aWbfWnzE1d+Q6MzYcDdtHZtmM9dAZuHJ4ppdDTMTYQr33fTUPA4LPlaSUOW551RfrvtkipJgVY+APRIh6DTHEiSouW5WZZ1woA0Ltn6ZNXo5ttvHt+vKzA8vG8wSADoMwe0+S9897vrZb7kpzyl62X6iZDXolfPK/qM8gGg17sDc8NAzJTaRSXX31kIn8zau7684a67WnuDwDy6PdD/AaDZDBoft9lH91bfcNPZnsV3yf1tlmWXuFxeFJ32vmjtA0CfhwGXWbRaTSaTl+UjMvuildXvb7zz5q2SZh8p7Psg0L8BoNkMGr/MZu/3rLjhpstjFm+Q/C0hz8+eafHPtnyYwAcA8ygKqDOR0CzPg2WZUru9VbJ7q7K688k7b36wEwTWRw31b0eg/4revokdlSSdf8NNl6dgN7rpl0OtVktFIa+qyhjtAwDck0tuMcZQqykVRWGuPwrJ73hsbxAYjvtNGCcAdOFnGV4f5o74s9nCn9dqqTUtdy9NijMT+gAAmA0C7lJlZlmoN5TanSBQJr9jv47A6Nq+eWqgPwrh8HDcO+J/X/OsKrU/LLNrQ5bXUmt65vE9o80PAHjBKNB5rNBiqDeUynYh93tiyD/y2O0jTx9YcwgApy6xmdatM42MpKHh4drkuRde6wofDll2Vmq1KPwAgOMQBOpKZfm0KX1k8IkN94yPjhZqNoPWrfNevi3Qu4Wx2cxml+td9Z4P/RsFuyXk2eXebitVVWUUfgDA8RhquqcQY7Q8V2qXDyr5Bzd94qN/fWAtIgCc+MLfmbg3MpLOve4DL415frvl2ds8JXm7PXfRHgAAjlsMkJQsz6OFIG+XX6za7fc9cfetT82tSwSAkzDqP++9N73bQ/xwyOJZaXq6s9OZ1Q8AOLFBIElSaDRCKqunLVUfefx3b76rF7sB1jP/zuHhoNHRavV1N16UagN3hlr+U14Us+3+yFkJADiJDYEqxBitVlMq2l8OxdQNG+++49GZCYI98aRA9xfO4eGo8fGk8XFf9Rs3vU1Z7U9Cnl2RpqdLuZsx6gcAnOxRqVlQSu5lWYV67SIP8ZeWvPYNm3d99p4H5tSurg4B3d0BmGmnXPxrH1g0vax2R4j5O1J7ZiEfRv0AgC7pBliMMeQ1par9mcaO4sbvfe7WPd1+S8C69t810/Jfed0HrgqN2mcsq70iTU0yyQ8A0JUpQFIKA4PRy+Lbabp4x+a7b72/m28JdF/7fHY25ehotfK9H3pHaNS/IguvqKYmS5mxih8AoAuHrWYyi51aFV4RGvWvrHzvh96xd8Gg2dpGB+AQZldXajazVbvanwz12rWp1ZJSqkTLHwDQG92ASiF0FhBqFfdsWpJfr5GRsttWEOyeANBZY7la8e9/47S4eODeUKu9Pk1PV3Kn5Q8A6LUQ4DJLodGIqSi+Wu2eesuTn/2d7bO1rhv+id3Rkmg2M42urVbc8L6r45LBr4cse301NVWJF/cAAHpRp3bFamqqCln2+rhk8Osrbnjf1RpdW6nZzOgAzBb/kZFy5fXvf32o1f/cQlyWilYps4wzCADQB92AMtTqmadqRypaP7v5k7d9tRueEDilHYA1s8X/PR/89VBvfEXuy1LRqij+AIA+6gZkqWhVcl8W6o2vrHzPB39dIyPlmlPcCThlE+vWNJvZ2Ezxj7XGp70sTSk5k/0AAH0YAoJSSpJCrNXfsvjq1zz1Lx//6DfXNJvZprGxU/IOgVNSbA8s/qndrpSSsZY/AKCPQ4DJXe6eYq3+1lMdAk56ADhE8WemPwBgvoQA64YQcFIDAMUfAEAI6I4QcNICAMUfAIDuCQEnJwA0m9kmij8AAC8YAtRsZjoJIeDEB4C9z/l/8Ndjg+IPAMAhQ8BVr3lq160nJwSc2CI8s+Thquvef43V61/1lHxmtj/FHwCAWe6uENxCMG+1Xr/p7tvuO9HLBp+4x+6Gh6NG11bnXveBV1mj/hdyd6Ukij8AAAfpBKQkubs16n9x7nUfeJVG11YaHj5hnfoTU4xnXnu4YvfupVGNb4aYrU7topJY5AcAgMO0AqqQ12Kqyo2Vpl/15OLFOyVJIyPH/XbACegAuGl83DSyzkOq/2XI8tWpoPgDAHAE4/KYiqIKWb46pPpfamSda3zcJD/uA/bjX5SbyvSpT1Wrbpi6Jw4MvDVNT/NiHwAAjjgDWPCyLOPAwMolr/zr5bs+/wf3qqnjPinw+CaKNc1MYyPlqhs++KthYPBzaXq6lETxBwDg6JWh0cjS1OSvbbrzls/P1tjj9Zcfv1sAzWbQ2Ei5+j+8/8csq93Vafs7bX8AAI6Jx1QUlWW1u1b/h/f/mMZGytk5dl3UAXDT8Npw9sqVjUZVuy9k2RWpaCVZ4OU+AAAcc3lNKdTqIZXlA9OxuGbr5s3TGl2fJPPu6AA010WNjlb1Irsr1BtXpKIoKf4AALzYYXoIqSjKUG9cUS+yuzQ6Wqm57rh01198B2BmoYJz3/3Btdng4P9IU1NM+gMA4Lh2ArwMAwNZOTn5i0/cdcv647FI0ItLEc1m0N3X+7nb//MFsRb/TGWqyVNksR8AAI5vL0CuFLL45kWv+ld/svtzN2yXmkFjY8d8K+DFtenHx00mD6G8x2J2mlclK/0BAHDcy7+ZV6UsZqeFUN4j08z6AKeiAzA8HDU6Wq18929eFwcGrud5fwAATmgICF6WZRgYuHDxVa95dtfnP/0NDQ9HjY8fUxfgGANAM+ihu/38rc+d5fX8/1VV1SUPEqN/AABOXAiQKbkrhNed/vJXf37Hf/uDCY0oSEd/K+DYbgEMj5vMvKrHT4Y8X5aqyin+AACc+DZAqioPeb6sqsdPysw1fGy3Ao7+D820/s9793/6STVqX/aiqHQilhQGAACHUlmtFjVd/NTjd33sb2Zr84kMAKZm09ZIYeOu1oMhZhd5u+0y45l/AABOFvdkeW6pKh9dvaR++ZiUNDLiko74VsDRFe7h4aCRkbRx+/S1sVa/2NvtRPEHAOAkMwvebqdYq1+8cfv0tRoZSRoePqp6fBQdADe5tPI/3rTU6vYDM1vqqRL3/gEAODVtAAtR7r7TW37B5k/dvLNT1Y9smeAjTwvDa4PM3PL026FWW+ZVlSj+AACcujaAV1UKtdoyy9NvdyYErj3iun5kv7HZDBodTee958MXK2bXplaL1j8AAKc+A4TUaiXF7Nrz3vPhizU6mo70jYFHVsQ7qw25e/GhkNcyd3cdtzcJAgCAY40A7u6d2lx8SDryFQJfOADMHf1n+S+m1nQyRv8AAHRJE8BCak0nZfkvHk0X4IUL+dzRf5bXGf0DANCFXYAsrx9NFyAcyeh/1fU3XcLoHwCA7u8CrLr+pkuOpAtw+GJ+WWf0r5jeH/K87u6J0T8AAF3ZBUghz+uK6f2SfKaGH/oPHPqX3CTT8nfeeHptsP6wmZ3Oc/8AAHSrvesCbCsmW5du+fQd2zoLAx58XYBDdwCa66Ikrzdq14V644zEc/8AAHRzE8BSVaVQb5xRb9Suk+QztfyoOgAmd6248cZGrOoPWp6t9rLtEvf/AQDo4i5Asiw3b5cbq9i6/Mk77pieGbv7kXUAhoeDzDxW9TdZvX6+l2Wi+AMA0PVdgOBlmaxePz9W9Td1Vgc8+DsCDl7Uh4ZczWaQ+Y125C8WAgAA3RAD5JL5jWo2g4aGjnQOQDNoZCSteKZ1vkJck4rCJUV2JwAAPSGmonCFuGbFM63zNTKSpOc/Evj8ANDs/Czm/vaZZX8r9iUAAL3D3auQ17KY+9vn1vb9uwTP/2M2NLwuf2751EMxr12Yyjb3/wEA6K0IkEKWh6pdbFi4ZeCy8dF17QMfB9y/sA8PR8l84iVTV4UsPz+1Kf4AAPQeC6ndTiHLz594ydRVknmnxh8iAKwZGup0BIL9O8trwaXETgQAoAd7AFKyvBYU7N/tV+NnI8KBkWFouJk/t3zqgZjXLqL9DwBAz0aA2dsAjy7cMnDF+OhIW3PWA9hX3DutAZ98aesVIcsvpP0PAEAv23sb4MLJl7ZeIWm/2wB7C/xsayBJw5bntP8BAOj1HoCULM9Dkobn1npp7i0Ad9O6dbZqZ+ufQp6/MrXblXj+HwCAXlaFPI+p3f7WpqX1V2vdOpd1ngaY6QA0g8x89bOtCxXC5d5uu+QUfwAAersHEL3ddoVw+epnWxd2in9nUaAgSWtmFghImb8h5LV6ck+HfVMwAADoAabknkJeq6fM3zC35gdJGhsfd0ly+Vskl/HWXwAA+iMCmHUqvPwtc2u+yd1k5me//X0LGqfnj1iMK7wqXSIFAADQ+9wtZuZV9eT0tvYlW79w+4TcLWhdp9efn5YPKYvLvaoo/gAA9FEPwKvKlcXl+Wn5kCRpnSy8csu1UZIy0zUhq0Ve/gMAQL/1ALwKWS1mpmsk6ZVbro1h4fLlnVWBTK+QO3sJAID+TAGdWi9p4fLlHsakpOH1MbldqVTJDvaKYAAA0LNMCkqVktuVGl4fx6RkknTRjc0zpsvWYyGGRZ6YAwAAQL8N/y1ES1Xa08jq5z96x8izQZKmU3FxrOULvaoSxR8AgL7rAZhXVYq1fOF0Ki6W9rb708uVZcb6/wAA9GkPQErKMpPSy/cFALeXsWsAAJgPSaBT84MkmTSklGQsAQgAQF8yM1NKnZovyU7/wAcWLWyF71qWr/aqnSTjKQAAAPpv6J8s5sHL9sbn6unKsLjVGjRp2cwaAHQAAADo0yaA3GXSssWt1mAofMGQYlzM+v8AAPR1/TevSleMiwtfMBSip0EZbX8AAOZJDgjR02BQ0DUWM8l4BBAAgP4u/koWMynomhBY+hcAgHklSCG47HReAgQAwDzhLpedHlzpavckOU8AAADQ38Vf5p7kSlcHk1XsEQAA5g+TVUGs/w8AwHyTMpddqqqSWAYYAIA+H/qbeVXJZZcGC7aIVQABAJgfEUDusmCLgpxHAAAAmFfcPTDyBwBg/nUCWAQIAIB5iAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAAAQAAABAAAAAAAQAAABAAAAAAAQAAABAAAAAAAQAAABAAAAAAAQAAABAAAAAAAQAAABAAAAAAAQAAABAAAAAAAQAAABAAAAAAAQAAAAIAAAAgAAAAAAIAAAAgAAAAAAIAAAAgAAAAAAIAAAAoAtl8/vju+SH/lUzKdhhf8sRMUnJJT8J25I62znctoLN/KPY1iGl9ALJ+ThG577clnfO+cOd72a9ta2T+T3u12112zXj+J2EBIDeK/8hk4co8/1POPfOeVGUrnbbj8u28syU57b37z6R26rXgmLQIbc1WbiqxLYOd01o1ILM9t/W7P93lyaLdPgLyzzfVgymeu3Q53uZpFaRjst5cTK3dTK/x/26rW64ZsglNylUpeTH59wgAPTQyD+EoDi5XWFyZ2fY4/tfKMvKtfKMmpa/pKay8mMOiu5SFk1bthXasrVQFm2/C+zx3JZcCsH0gx9Oac9kpXBgZDcpJdel5zS0aDBTSn7sSbsft+X7jsejT0zvOxa+bzuzx/Oqcxv7jiXbet6x2jNZ6gc/nD7ksVo0GHXBOQPH5bw4Gds6md/jft1Wt10zlFzV4rPktYZScrnmXzdgXgUAkxSsky53aaHOePJresn3x1TV4n4pMAbTzueS/refO0vvWvMS7dhTKoZjOzmq5Fq2KNPvf+lHuuerT2vpwrBfuj2e20ouDdaDbrzzMX3joQktGLD9Wr7BpMmW60PvOU+vGVqkial0zC3fftzW7MVw10Spd/zVBu2aKPe7IM5eDJcsyPTJn71QSxZkx3xB7NdtpSQtGAj6xvge3fgnj2uwbvu150OQJqZcr7lsge5Ye74mW0nHeLqf1G2dzO9xv26rm64ZMlMokzZe9b9r4syLtDBNKI8ul5R8/gSBeRMAormKFDTRzrUgFnrjWT/Sgh079MiGqFpWk88JAB5MnpUqlOu5dqaJUi/qC5a3MxXK5VnW+W/OF+x4biu55DGotJo8K+RZ2K+75SZ5lTSdck2UmSbKF/cF67dtuUuZmyZLKWW1znbmjohMcnOlLNNkmSkrX2RR7sNtpSSpDJpOuTzLO9uaU5Q9SJ4llVbTRJlpsnxxAeBkbetkfo/7dVvddM2QTEUq9WsX/ECbFzX0tS1L9ExrQPVYaTBWSm5y9b++DwDRXKWbdhQ1nTM4qf91xWb9woqNeu05z+mz27bq25XUUFK135Wjc9U0uYLN/neMnaiZP2/yfTNSTtC2NNPh2H9bz68GbOuQXUMF62xrv+34AdXU527nGItyn25L9gLH6nie7ydxWyfze9yv2+qma0Yw11SSfnL5U3r1pbv1yLaG7n1qpf7qqRV6ZM8SDYRKjVip7PNuQN8GAJNk5trVzrUgK3XDRQ/rl8/bqJcOTGqyNE2XDU2Vmeb3HFAAmJ9M0nPtXLvbuc5uTOu9Fz+sXz1/g/76qRW66/uXaNPEQi3JCwXzvr0t0JcBYLbdP1Xmeus5m/WuCx/V0JKdmiwz7ShqSsk1MJMwAQDzUzBXNNdUCiqKqGiut63aqDVn/UhfePx8fe6xl2m6ilqYlX3ZDei7hYCiuXbPjPo/cdU/6Xev+mddsGi3dhQ1td0UKfwAgAO6AXGmLmwvalqct/Wblz6oL1zzD7p0yS5tK+qK1n/PCfRVAMjMtbOo6VWnbdMXX/f/6edWbNaudq6pMuvLgwcAOP51pHTTs0VDVy7dri+89h/0S6se0652bW/XgADQZektmGvrdEO/dN5j+sI1/6BzBye0vagx4gcAHHVNySxpT5krC0m3vPxb+u0rvq09ZaZ2Cn1TU0I/HKgkabLM9MFLH9R/ueI7KpNpqorKKPwAgGMUzVXNPEX2f6z+gT75ym9oMFaarmJfhIDeDwDmalVRI1d8R7859KAmy0yVbO/9HAAAXswgM5pre1HTz614Qn/w6q9pcd7ui05ATweAzFzPTDd0/UWP6FfP36AfTQ3MPPsJAMDxrTdPTzd09WnP6raXf1PTVVRy6+l607MBIJprR1HTr5y/Qde97BE9M91QHhJnKQDghMhD0rNFXWvO2qqP/Ni3NVVFqYe7AD0ZAKK59rRzvfr0Z7Xu8u+qqKKMlj8A4CR0Ana1c/3K6g36ldU/0M6ZRwQJACeBSSpS0Gn1lm6+8n6ZpLLH2zAAgB4qnObaVtT1/ksf0jWnP609M4+aEwBOdAAw11SV6bcu+xe9bNFuTfTJbEwAQO8MRN1NUUn/9cpv750U2GsD0Z4KAJ1V/mp66zmb9ZZzNmtHUeNRPwDAKekCTFS5Ll60W++96GFNlFnPDUZ7KgBUblqQtfWuCx/tybQFAOgfmSXtaucaXvm4Lpt530wvhYCeCQCza/z/6uoNnRf7VBmtfwDAKR+YDsRKN14yrl57Dq0nAsDsxL9zBif1S+dt7LmUBQDoT7NPpb3prC16w5lP67l23jP1qScCQDDXRJnpJ87+kc4ZmFSL9j8AoEu4pBhcbznnCVU99FRaTwSAyk2L8rZ+ceXGTvFn9A8A6LIuwJvP3qKhJTs1VcWeCAFdHwCCuSarTFcs2aFLFu3WVJn11zuMAQA9r3LTslpLa87aqqkemaPW9bXUJLVT0BvP3qp6LMXYHwDQjbWqSFFrzvqRBmLZExMCuz4AVG5amrf042duVYuZ/wCAbiym5pqqoi5fslMvW7Rb02WUdfl9gNALO/SSxbt1wcI9mu6R+yoAgPmnctOSvNDrznhGrZQpdHnPuqsDwGxL5YqlO3qmpQIAmJ9MUnLTjy3doWDdX7G6/hZANNcli3fJGfsDALo5AFhn0Hr+wj1anLVVeXfXra4OAC4pD5VWDEz21LOVAID5yNV205n1lpbkRde/qbZrA8Ds7P/T8kIrFkyoqCLP/wMAurcDIKlMQUvzQisXdn/d6oEOQFItJB7/AwD0hGiuRg/Ure7tAJirqKLOW/icTssL3v4HAOh6s7euh5bsVOmBWwAvZkdm5jz7DwDoKRkrAR6fEAAAQK91AggAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAI5UNt93gNm+/w78Wc9+Jj5X35yDffG5XuBnXDP4XFwzCAAnnbtUVq6yclXJn/dz79FlCKvkez9XSvt+HozP1Y1mP9PsuTd7gZr7817+bs35ainM/Hzu941rBp+LawYB4KSf8PXctGRBpsULsv1O+hhs76/32gnikgYbUUsWZBocCM876fMsKYvWc0ss9+vnMpMWDcbOlzHa8wLAosHYcyMVn/ksSxZkGqiH/QNAkLKQNNiIvXcO9us1g2shAWA+icE0MV3pp19/mt786qUHbUe6pFoeNDFdKYbeuAIHk4oi6b1ve6nKyg/5uQbqQa1WUuiRGSD9+LnMOqOTgXrU7e9eLT/E1dXMNFAPqpL3RBAIQWq1ki5a2dBnPnThIY9VFk1FkdQjX62+vWZwLeytayEB4Din3oHaoXdBcu/Z1Hu4rymfqzs7AIfSa+3yuR2Aw/2e1Gufq1+vGVwL6QDM1xBQHuYi1KuTRKrkh30VFZ+ru7zQff5e/Fwv9N1Sj04E7NdrBtdCAsC81I8ngO39Hz4X5yCfi8/FtRAHxzoAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAIAAAAIB5ImMXnDxm+/478GfonWPF8eri4/UCP0MXHCuuhQSA+cZdKitXWbmq5M/7uTv7qJvMHqvZYzR7gZr7c3Tfd2vOV0th5udzv2849ark+45XmnO8jGshAaBPL1D13LRkQabFC7L9Lkgx2N5f58TvntHkosHY+YJEe14AWDQYGal0y3dr5hgtWZBpoB72DwBBykLSYCOKr1b3HK/BRtSSBZkGB8LzAkCepc53jl1FAOgHMZgmpiv99OtP05tfvfSg7UiXVMuDJqYrxUBlOZWFv0qugXrU7e9eLT9EIjMzDdSDquQEgVMoBKnVSrpoZUOf+dCFh/xuZdFUFEl8tU7x8TKpKJLe+7aXqqz8kMdroB7UaiUFZqgRAPqpAzBQO/TuTk7rqxs7AIdCW7n7OgCH+z2J49VVHYDDZTGuhQSAvgwB5WEuQowku8sL3efnePXOd0tMBOwqVXIdrsfPd4sA0LcjS3CswPGa18dq7//gVOMuCwAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACgq2TsArwYZlKY+U82J1nO/gzdlfjnHC8/2DFEV363/CDfLeN4gQCAU6lVuCZbSRaklPa/SE220n4/w6k31UqabCVl0eS+r9CUlSvPOFhdU/wltcvOd6uWJ1XJ9/5aDKbJVlK7dJEBQADAKRmdFKXr1996tt72v5yhcJDhY0quC86pq9V2Riun+FgldzXqQc1/v1JllWQHHBB3VxaDGvWg5ByvUymYaaKV9Oarl+qy1YOdsHZAOCgr1/IzappoJQUOFggAONkjlJRcl6waVIzaO5o8sPBMF66UKCinmruUBdNVFy847O+bbqWDHkuc3MBWlq5zzqzpvOX1Q3632qWrIFyDAIBTZapIM9X/YFchlwWjTdktIUDSxHSS/NCpjnkA3RMCirarVaRDf7fMKP4gAODU6Uz+s8P0CdB9x4v90CshwPhu4UReD9gFAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAADAvAoBxjAAAPaYXalfo9h1Yuik5MQAA0DvKHqhbXRsA3E21WOnx5xZqe7umPCQ55xQAoMsHru0UNb5rqTLr7roVun9HBhUpcCsAANATKjdN90Dd6t4OgKQ8JG1v1/TkxALVYiXnVgAAoIvrVhaSdrZr2vxc99etHugARD05Nahozi0AAEBXV63cXM+06trVrinr8rrV9U8BVG56ZPcSGeUfANDNHQCXaqHSY88t0u4yV7TurltdHQBcnZ35wM5lmqoyFi0AAHR1zQrm+pedy5S8+ytWV/8Lk5sGYqVHdi/WD55bpEas6AMAALpSNNeudk1fe/ZM1UOp1OXTAEMv7NCd7br+4ZmzVY8lawIAALp2wPrgrqX6/p7FamSVvMtHrF0fAGafBvjK1rPVqjIeBwQAdGWtqoVKY0+/pGduWXf9vzG5aTCWemDXMj2yZ7EGslKJcw0A0EWiuXYUdY09fbYGeqRbHXplx+5p5/ofm1erHhLrAQAAukblpkV5W3+7dbnGdy3VQI/MV+uJAJDctCAr9XdbX6IfTg12QgDnHACgC5ikKpnu/eG5PbVmTU8EgM69laQfTg7qjx9frcGMyYAAgO4Z/f/908v1j8+cpYV5u2fqU+ilnbw4b+vzGy/U+K6lGuSJAADAKRbNNVVF3fHIUM+tVRN6bUdPlLl+f8NFvB0QAHBKlR60JG9rdPN5emjX0p7rTvdUAOh0AQr92Q9X6t4frtSyWtET71wGAPSX5KYFsa3v7Vms3330Ui3owVvTPbe6rrtpIJb67Yd+TN/fs1gLYsWtAADAyatDksxclYL+83dfod3tvCe70qEXd3wtJG1v1XXTd6/qvH6RNwUCAE7i6P/0Wku3PXyZ7tt2lhZlpaoeHIj25Pt1Zmdd/tO2M7TuwSu7/p3LAID+ULppSd7WH268UH+48QItrbV6svj3bACYDQHLaoX+8LELdff3L9GZjWm1E+8LBACcGO0UdEatpbGnz9aH/+UVGoiV1MODz56umKWbzmxM65OPXqLPP3ahXjIwpeTG7QAAwHGvN2c1pvXP28/Q+7/zKjVipdDjt597fsjsbqrHSs0HXq6Pj1+uwaxUlPdsSwYA0EU1Rp2O82m1Qn/65Ln69X963d5Jf70+AT30w8EJkgazUrc8fLn+zwderiy4BmLFI4IAgGNWuSmaa1mt0H/feIGu/9ZrNFlFNfrk6bO+uGnu6szKPLsxrT9+/Hy9/b4f1xOTC3RarVDlxmOCAICjqimlBy3K2ipT0Ae/80r91gOv0KKs7IuRf18FgFmlm5bWCn1z++l629f+lf70yZVakrc1MPOIBnMDAAAvVEcyc51Rm9Z3d56mt3/9x/XHm87XkryQZgab/SIufe0b1vVXcjMNxErTVdSfP3WuHp9YqAsWPqeVCyaUPKjtQZLJaAoAALSvixxMWlpra1urrt/bcLF+64FXaOv0gJbm7b6cV2ar3vOf+nJgbOqs1LS7nWtBVupXV2/QL5+3US8dmFSrCppOmZJ3fl8wegMAMB+Lvkmqx0oDsdKudq6/fmqF7vr+Jdo0sVBL8kLBvG9vI/dtAJgVzVW6aU871zmDk/qJs36kX1y1UZcs2qV6TDNhIO49wLPBYfb/AwB6u9BLnSfGfM4AsTZT9Cs3/XBygb705Ln6q6dW6JE9SzQQKjXmwUTyvg8Ac4NAkYImykyL8rauWLJTbzz7R/rxM7fqgoV7NBAruXdmfRYpdiaBpMC8AQDoYVlInYIfkqIlRZOKFPTDyUHdt+1M/f3TL9G3tp+up6cbqsdKgzMz/OfDtT+bLydBNTOxY9nMkwH/tP0MffXZM7W0drEuWbRbly/doUsX79KKgUmdOzihPCSdVm+JtQUBoEdHuHJtL+pqp6Cnpgf01OSgvrdnicZ3L9FDO5fqR9MDCuYajJWW1Yq9z/zPm3A0n06GuQd3YdaWqTPj8/4dp+nr285QNHUKf62l3JLOW/gcLxoCgB694AdzbZpcqMky0652rqkq7h0MNmaKvtSZCzAfF4/L5uu5Mfee/4Ks1MLZn0va3c7lkp56ZpDiDwA9rBaSgrmiuZbk7b2DQZ+nRZ8AcIgwsHenzEwCrM2cLACAHm0EzFzf51t7nwBwrCfMAScOAAD9hjluAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAAAEAAAAQAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAAACAAAAIAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAAAABAAAAEAAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACAAAAAAAgAAACgWwKAsxsAAJhXPMjM2A8AAMwjZhY8+R51MgCdAAAA+nzkLzN58j3B5A9bjJI7AQAAgL4u/+4Wo0z+cBATAQEAmG9CcHlkPwAAMI8aAfIYTOGfzYJkzAEAAKCvmdwsyBT+OZh8m3gQAACAeRICTCbfFpKU2BsAAMwfSUpBSfd5VUrOZEAAAPqaK3hVSkn3hcrCpNzpAgAAMC9CgKfKwmSo2cS4qmq3xcwk1gIAAKBfK7/FzFRVu2s2MR521+uTLu1gNUAAAPo7AchMLu3YXa9Phm233rpHZhssi5KMAAAAQF8ytyxKZhu23XrrnjAz7B9XCHKWAwYAoD+H/+6uEDo1X7PLAJt/n10DAMC8aAR8f18AUPiOytKN9wIAANCfdV8KKkuXwnf2BoBGqH2vKtrPWYyBJwEAAOg37hZjqIr2c41Q+14nADSb4dEnLtshsw2d1wLzJAAAAP1V/+UWOxMAH33ish1qNkNYIwWNrq2C+XcVopylgQEA6LP6r6QQFcy/q9G11RophOe2bLGZX/02LwUCAKBPmXVqvaTntmyx8K3l91SSVLruS2VRmVlkLwEA0E+132Iqi6p03SdJ31p+TxW0rnPPv729Pa6y2mIxsiQwAAB9w91iNJXVlvb29rgkaZ08yMw1PBy3fuH2CVf6lmWZJGMeAAAA/TH+T5ZlcqVvbf3C7RMaHo4y8yBJa4aGTJJMdq9kYkFAAAD6ZfzvnQovu3duzQ+SNDbSmfkfSvvH1C5awSzwXiAAAHq+/CuYhdQuWqG0f9yv5nd+w0iSu208o75BKT1oeW6SVew4AAB6mVWW56aUHtx4Rn2D3E0aSZK0d8b/GinbNDJSLXntGy4KtdrrUrudzIylgQEA6NXxv3sKtVpIZfl/7/r4f/3yGinbNDY2twMgjY2P+8wPRr3dTrwXAACAHh//S8Hb7RSk0bm1fr8AoNHRSpINPlX/dirbG0KeB8l5GgAAgN4c/6eQ5yGV7Q2DT9W/Lclmav0BAUDSmmYzjo+OFGZ2r7Jc7iwLDABAT5Z/V1KWy8zuHR8dKdY0m/st9LdfANjbGkj+J94uuA0AAECP6rT/i6Tkf7JfjT9YAOi0BtwW/Gjg/lS2H+M2AAAAPTn+n23/P7bgRwP3S75f+//5AUCSmutmbwP8kWLGbQAAAHqt/LuSYiYz+6Px0ZFCzXXPe8/P8wPAzAIBVdu+kNpFycuBAADoLWYWU7soq7Z9YW5t3+/3HPRPNptBklbtnP6bUKv/61QUleasGQAAALpWFWq1mIrW/9y0tPGTnQAwkl64AyBJ4+OmkZEktzv8EBkBAAB0J5dJbndoZCRpfPyghfzgAWB0NMndqtj6e2+1HrMsYzIgAADdX/qTZVnwVuuxKrb+Xu6m0dGD1u/DtfWz3R/7WLHsNW9YZvX6m1gaGACALi//rhTqjaCy/MTmT9z2t5IyzSz9e6DD9PfdJNPyd954em2w/rCZne6pkmTcEwAAoAvLv4Uod99WTLYu3fLpO7Z13uxrB32972FG9OZaPxy2fPqOZ+Xpz6xWM3fxhkAAALpz9F9ZrWby9GdbPn3Hs1o/HA5V/F8gAEh6aMglmapwW2q3WzO3AJzdDABAd9V/Mwup3W6pCrdJspkafkiHf7RvbMw1PBx3/eE9zyy9+pqXhcbAy1PJXAAAALpr9O8pNgaiF60/3nTXxz+j4eGoT33qsJP3X7iQD3W6AGa1j6ay3TIzowsAAEBXjf6tU6NrH5VkM7Vbx94BmNMF2Pm536cLAABAN4/+77z5iEb/R9YBOLAL0FkemC4AAADdMvpvF+XRjP6PrANwYBfg1decHRqN13hZJtEFAADgVA7/U2g0oreL39t0583/15GO/iUdzTq/bnJp5X+8aanV7QdmtpR1AQAAOHXVf+a5/53e8gs2f+rmnZ2qbkfUATiKEby51q4Nm3/vYzu8Sr9leW7iVcEAAJyi+q9keW5epd/a/Hsf26G1a8ORFv+j7ADM/P5m09ZIYeOu1oMhZhd5u+3cCgAA4KQO/pPluaWqfHT1kvrlY1LSyIjrKObnHW3hdo2P29jISBkq3aAQTGZMBgQA4GQyc4VgodINYyMj5cwb/46qHh/9yH10tNLwcHz8ro/9jRfTo6Fej+7OEsEAAJyUwb9XoV6PXkyPPn7Xx/5Gw8NRo6NHXYePrXU/OuRyt9iqrk/t9o4Qo0lOJwAAgBNc/kOMltrtHbFVXd953e/QMdXfeGz/gDHX+Hjc8d8/89ySq1/3XGg0fsbbZcVcAAAATmT9VxUajZhaxQce/9StX9H4eNT4p45pQv6Le4Rvpu2w6j2/+beh3vjXaXq6klnkCAEAcNwH/zPFf/p/bvrEx998rK3/WS9uxD405HJZStm1XpXbLWaScysAAIDjXPzdYiavyu0pZdfKj3zFv0N5caP1sTHX+Pq4+/M3bFv0qmsej43GWi+5FQAAwHFWhUY9q6Zbv/LEXR/7R42vj/rU9S9qLZ4XX6hH11ZqNrMn7rplfTU59fkwMJjJveRYAQBwXEb/ZRgYzKrJqc8/cdct69VsZhpd+6KfvjtOy/i6aXhtOHvlykajqt0XsuyKVLSSLNAJAADgmMtrSqFWD6ksH5iOxTVbN2+e1uj6dDQr/p24DkAnR7iGhnzr7bdPhFb77Z58QjFzHg0EAODYh/6KmXvyidBqv33r7bdPdO77H58F+I7fjP2xMdeaZrbz/7l5y5KrX7slNBo/72VVHb+QAQDAfGJVqNczL6bf9fjdt35Za5qZ/tvIcVt47/gW57GRUs1mtunOWz6fpiY/HRoN5gMAAHD0g/8yNBpZmpr89KY7b/m8ms1MYyPHtZ6egFf5duYDaHR9Wvmem74Wa7XXplaL9QEAADiy4l+Fej1WRfH1zZ+4+XWzNfV4tf5PYACQ1GwGSVqxe/fSqMY3Q8xWp3ZRSYQAAAAOU/2rkNdiqsqNlaZf9eTixTslSSMj6Xhv6cQU5LEx11lnhd2f/ezk4lde81XLwi+YbEApucyMAwwAwPNG/sliNJl2pFb7rU/eefsPdNZZQZ/6VDoRmzuxxXh4fdTo2mrVde+/xur1r3pKrpSMEAAAwH7F3xWCWwjmrdbrN919232zNfREbfLEztCfWSRo09233ZeK9rUhz4NCSCwXDADAfsU/hTwPqWhfu+nu2+47Xov9HM6Jvyc/NpbUbGa7bv3oNxdf/ZqnYq3+VndPcqcTAACg+HeKf6yK6XduvuuWP1CzmWlk5IQ/QXdyJuWNjaU1zWb2Lx8nBAAAcNDi/4lb/mBNs5ltOgnF/+QFAEmbCAEAABy2+I+dpOJ/UgMAIQAAgO4o/ic9ABACAAAU/1Nf/E9JADhMCOjsGEIAAKA/i39SCBbyPJzq4n/KAsDBQkDI8p82s6iUKpnxAiEAQD8V/8pijBZjldqtd53q4i+d6IWAjsTM4w4rr3//60Ot/ucW4rJUtEqZZZwxAIA+KP5lqNUzT9WOVLR+dvMnb/vqyXrUrys7AHvtXSfg5k0Lr37135nFN4Y8PzOVZWV0AgAAPV37vYr1epZS9f2qnP75J+66/evdUPy7owMwa2bJwxX//jdOi4sH7g212uvT9HQl98C8AABAr1V+maXQaMRUFF+tdk+95cnP/s72E72879HonhH26NpKw8Pxyc/+zvZNi/M3plZxT2g0okIwuVecTQCAHin+lUKw0GjE1Cru2bQ4f2On+A93TfHvrg7ArGYzaGTEJfnK937oHSGGO2S2KBVFacwLAAB098C/DLVaJvc9qUo3bv7dj35GkqnZtBPxSt/+CgCz/67h4aDR0WrldR+4KjRqn7Gs9oo0NVlJ4pYAAKDrKr+kFAYGo5fFt9N08Y7Nd996f2fUP5okdd1L8Lp1kp1rdLRSs5ltvvvW++s7ijWpLD4TGgNRMZpzSwAA0D21v1KMFhoDMZXFZ+o7ijWb7771/s4b/Uarbiz+3dwB2KeTnipJWvUbN73NLHzSsuz0ND1dSop0AwAAp3DUX4VGI/Oy3Oaert/0Ozd/8cDa1a1i1+/g8XFX55ZA3PXZex5YdtXVf5osXhQa9Yvkbp4SjwsCAE76qD/EGEKjEVK7/HIopn7h8TtvHdPwcNT4uDQ+nrr9M/TW6HnOs5Pnvfemd3uIHw5ZPCtNT3d2NEEAAHBiK3+SpNBohFRWT1uqPvL4795814E1qhf0Xvu82ewU+ZGRdO51H3hpzPPbLc/e5inJ220mCQIATsiQX1KyPI8WgrxdfrFqt9/3xN23PjW3LvXSR+rdQjknaa16z4f+jYLdEvLscm+3lapq9rYAQQAA8CJLv6cQY7Q8V2qXDyr5Bzd94qN/3Yuj/rlizx6SsbHOK4SlsOvjH/n+Oee+5LPtRUuecQuvjLXaIq+qzgJCnW4AQQAAcFSFX+5JZiE2GsHdn1Yqb1rw5PffueFz93xPzWbQV74ivelNqVc/YH8UxjmzLc9/X/OsKrU/LLNrQ5bXUmtaM0GAjgAA4EgLfwz1hlLZLuR+Twz5Rx67feTpA2tOL+ungmgaXh9ml1lcccNNl2fBbnTTL4e8Vkutabl7aTw6CAB4Xtl3d6kysyzUG0rtojDXH5XJ73jyzpsf7BT+9VGja7tyUZ/5HgBmD6Jp7dqwtyNww02Xp9kgUKvVUlHIq6oyyXhqAADmfeFPLrnFGEOtplR0Cn9Ifsdjewv/cNT69Ulm3k8fvX9Hws1m0PhlNrcjELN4g+RvCXl+tpelvN2ePaDcHgCAeVT2JSW5m+V5sCxTare3SnZvVVZ37jfiH3rIe212PwFgvyAwbrMdgdU33HS2Z/Fdcn+bZdklLpcXhWbeOEhXAAD6eLQvyWUWrVaTyeRl+YjMvmhl9fsb77x5694R/9BQ3xb++RMA5gaByy4zre10BC5897vrZb7kpzyl62X6iZDXolelvN12SbPrCRAGAKD3i36SFC3PzWKm1C4quf7OQvhk1t715Q133dWSJK1fH/XQQ31f+OdfANh3MphGR8NsEJCkle+7aSh4HJZ8raQhy3PN3CKYGwZ4nBAAeuAqP7toz96in2XydluSxiVbn6wa3Xz7zeN7/8T69VHDw313j58AcLjPPjwc9pvYMbw+rjz3u68x9583+c/IwqWW5/Kq6pw87pVMknOrAAC6apRvcrkks2h5Lotx5rqdHnbZX7rZlzY/ceU3ZueFzZkw3jez+gkAx+KA2wOziXDl1zthQK41Uroq1BpR8k53oCrdXZVJmukOMJEQAE7GCL8zgc879V7RYmaWZZJMqZiupHC/TGNu9qXNr73yGwde2+dTm58AcLRdgaEhO3Bpx1Uf/PClqvy1nvRvzdOrLISVltckd3mq5FUlVVWSlDonpZncjVsHAHCMhd7dZebunWVfJQXFGCxGWYiSmbxdyFPa7Ba+aUF/oWhf33TLRx4+YJCXaXzc5/NonwBw7GGgmnvSLG82Bxu725cn6XUmv9IVLnNPF4UsW2Ix6/ymlDqhwJM8JZe7ZJY6f41Je89n8RZDAPOwvHuacw30OdfGIDNZCCYLshil0LlEelUqleUus/CoKT3ksu8G6WvTi/MHt4yMTO53/W42I0WfAHB8dN72FGZOqOctAbn8xuYZsZoeymQvVwir3HWFvLpQFhab2el7T+TZYJv2nfudoMD5CWC+VB6bcz2ULOy7g7p34OS+TZ52y+IGMz2glDaV8u9UsTG+5Y6RZ5/3d3Ye3TNJifY+AeCEdwfWDA3ZmKQDOwR7Q8E7m4MLTq8tKKZ2X2FmDXd/nSlkcl+mYFepSjNxwC62YItmZq5yTAD07bhfZubJ95j8e5KkGKTk98tshyuVZvY1d5+uDSx+YGJbMbHl0/uN7Pcb4a+RNMYo/5j9/4ylidqBkadWAAAAAElFTkSuQmCC",
    "icon-maskable-512.png": "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AEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAABAAAAAAgAAEAAAgAAAAAQAACAAAAABAAAIAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAACAAAAABAAAIAAAAAEAAAgAAEAAAAACAAAQAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAACAAAQAAAAAIAABAAAIAAAAAEAAAgAAAAAQAAAgAAEAAAwDXp/wNaBoLPUTOT9gAAAABJRU5ErkJggg==",
}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"Koerperdaten/{VERSION}"
    timeout = 30
    db: Database = None
    throttle = LoginThrottle()
    html_file: Path = None
    https = True
    quiet = False
    legacy_xml: Path = None
    ca_file: Path = None
    photos: PhotoStore = None

    # ---- Hilfen
    def _cors(self):
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, If-Match")
        self.send_header("Access-Control-Expose-Headers", "ETag")
        self.send_header("Access-Control-Max-Age", "600")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not any(k.lower() == "cache-control" for k in (headers or {})):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, headers=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", headers)

    def _body(self, limit):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            raise ApiError(413, "Anfrage zu groß")
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        try:
            data = json.loads(self._body(MAX_JSON) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise ApiError(400, "Ungültige Anfrage")

    def _user(self, admin=False):
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        user = self.db.session_user(token) if token else None
        if not user:
            raise ApiError(401, "Bitte anmelden")
        if admin and not user["admin"]:
            raise ApiError(403, "Nur für Administratoren")
        return user

    def _session_reply(self, user, code=200):
        token = self.db.create_session(user["id"], self.headers.get("User-Agent", ""))
        self._json(code, {"token": token, "benutzer": {"name": user["name"], "admin": bool(user["admin"])}})

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stdout.write("%s  %s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.address_string(), fmt % args))
            sys.stdout.flush()

    def _dispatch(self, method):
        path = urlsplit(self.path).path
        try:
            self.route(method, path)
        except ApiError as e:
            self._json(e.code, {"fehler": e.msg, **e.extra}, {"ETag": e.extra["etag"]} if e.extra.get("etag") else None)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass
        except Exception as e:  # unerwartet: protokollieren, aber keine Interna ausliefern
            sys.stderr.write(f"Fehler bei {method} {path}: {e!r}\n")
            try:
                self._json(500, {"fehler": "Interner Serverfehler"})
            except Exception:
                pass

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # ---- Routen
    def route(self, m, path):
        db = self.db
        if m == "GET" and path in ("/", "/index.html", "/Koerperdaten.html", "/koerperdaten.html"):
            if self.html_file and self.html_file.exists():
                return self._send(200, self.html_file.read_bytes(), "text/html; charset=utf-8")
            return self._send(200, "Körperdaten-Server läuft. Lege Koerperdaten.html neben das Skript, dann wird der Tracker hier ausgeliefert.\n")

        if m == "GET" and path == "/manifest.webmanifest":
            return self._send(200, json.dumps(PWA_MANIFEST, ensure_ascii=False), "application/manifest+json; charset=utf-8")
        if m == "GET" and path == "/sw.js":
            return self._send(200, SERVICE_WORKER.replace("__VERSION__", VERSION), "application/javascript; charset=utf-8",
                              {"Service-Worker-Allowed": "/"})
        if m == "GET" and path.lstrip("/") in PWA_ICONS:
            return self._send(200, base64.b64decode(PWA_ICONS[path.lstrip("/")]), "image/png")
        if m == "GET" and path == "/ca.crt":
            if not (self.ca_file and self.ca_file.exists()):
                raise ApiError(404, "Dieser Server verwendet kein eigenes CA-Zertifikat")
            # DER-Format als neutraler Download: Android würde einen als CA-Zertifikat gekennzeichneten
            # Download sofort an den Installer geben und dort abweisen, statt ihn zu speichern
            der = ssl.PEM_cert_to_DER_cert(self.ca_file.read_text(encoding="ascii"))
            return self._send(200, der, "application/octet-stream",
                              {"Content-Disposition": 'attachment; filename="koerperdaten-ca.crt"', "Cache-Control": "no-store"})

        if m == "GET" and path == "/api/status":
            return self._json(200, {"app": APP, "version": VERSION, "datenbank_version": SCHEMA_VERSION, "https": self.https,
                                    "ca": bool(self.ca_file and self.ca_file.exists()),
                                    "fotos": self.photos is not None,
                                    "einrichtung_noetig": db.user_count() == 0})

        if m == "POST" and path == "/api/einrichtung":
            b = self._json_body()
            with db.lock:
                if db.user_count() > 0:
                    raise ApiError(403, "Die Einrichtung ist bereits abgeschlossen")
                user = db.create_user(str(b.get("name", "")).strip(), b.get("passwort"), admin=True)
                import_legacy(db, user, self.legacy_xml)
            return self._session_reply(user, 201)

        if m == "POST" and path == "/api/anmelden":
            b = self._json_body()
            name, pw = str(b.get("name", "")).strip(), b.get("passwort") or ""
            ip = self.client_address[0]
            if self.throttle.blocked(ip, name):
                raise ApiError(429, "Zu viele Fehlversuche. Bitte in einigen Minuten erneut versuchen.")
            user = db.user(name) if name else None
            if not check_password(pw, user["passwort"] if user else DUMMY_HASH) or not user:
                self.throttle.fail(ip, name)
                raise ApiError(401, "Benutzername oder Passwort falsch")
            self.throttle.ok(name)
            return self._session_reply(user)

        if m == "POST" and path == "/api/abmelden":
            u = self._user()
            db.delete_session(u["token_hash"])
            return self._json(200, {"ok": True})

        if m == "GET" and path == "/api/ich":
            u = self._user()
            return self._json(200, {"name": u["name"], "admin": bool(u["admin"])})

        if m == "POST" and path == "/api/ich/passwort":
            u = self._user()
            b = self._json_body()
            if not check_password(b.get("alt") or "", u["passwort"]):
                raise ApiError(400, "Das bisherige Passwort stimmt nicht")
            db.set_password(u["id"], b.get("neu"), keep_token_hash=u["token_hash"])
            return self._json(200, {"ok": True})

        if path == "/api/daten":
            u = self._user()
            if m == "GET":
                row = db.get_data(u["id"])
                if not row:
                    raise ApiError(404, "Noch keine Daten gespeichert")
                return self._send(200, bytes(row["xml"]), "application/xml; charset=utf-8", {"ETag": row["etag"]})
            if m == "PUT":
                data = self._body(MAX_BODY)
                if not data:
                    raise ApiError(400, "Keine Daten übermittelt")
                p = validate_xml(data)
                if p:
                    raise ApiError(400, p)
                tag = db.put_data(u["id"], data, self.headers.get("If-Match"))
                return self._json(200, {"etag": tag}, {"ETag": tag})

        if m == "GET" and path == "/api/sicherungen":
            u = self._user()
            return self._json(200, [dict(r) for r in db.list_backups(u["id"])])

        mt = re.fullmatch(r"/api/sicherungen/(\d+)/wiederherstellen", path)
        if m == "POST" and mt:
            u = self._user()
            tag = db.restore_backup(u["id"], int(mt.group(1)))
            return self._json(200, {"etag": tag}, {"ETag": tag})

        if path == "/api/benutzer":
            self._user(admin=True)
            if m == "GET":
                return self._json(200, [{**dict(r), "admin": bool(r["admin"])} for r in db.list_users()])
            if m == "POST":
                b = self._json_body()
                user = db.create_user(str(b.get("name", "")).strip(), b.get("passwort"), bool(b.get("admin")))
                return self._json(201, {"name": user["name"], "admin": bool(user["admin"])})

        mt = re.fullmatch(r"/api/benutzer/([^/]+)", path)
        if mt and m in ("POST", "DELETE"):
            me = self._user(admin=True)
            target = db.user(unquote(mt.group(1)))
            if not target:
                raise ApiError(404, "Diesen Benutzer gibt es nicht")
            if m == "DELETE":
                db.delete_user(target["id"])
                if self.photos:
                    self.photos.delete_user(target["id"])
                return self._json(200, {"ok": True})
            b = self._json_body()
            if "passwort" in b:
                db.set_password(target["id"], b.get("passwort"), keep_token_hash=me["token_hash"] if target["id"] == me["id"] else None)
            if "admin" in b:
                db.set_admin(target["id"], bool(b["admin"]))
            return self._json(200, {"ok": True})

        if path.startswith("/api/fotos"):
            return self._fotos(m, path)

        raise ApiError(404, "Nicht gefunden")

    # ---- Fotos (nur für das eigene Konto)
    def _fotos(self, m, path):
        u = self._user()
        ph = self.photos
        if ph is None:
            raise ApiError(404, "Fotos sind auf diesem Server nicht verfügbar")
        if path == "/api/fotos" and m == "GET":
            return self._json(200, ph.list(u["id"]))
        mt = re.fullmatch(r"/api/fotos/(\d+)/(bild|vorschau|original)", path)
        if mt and m == "GET":
            data, typ = ph.get(u["id"], int(mt.group(1)), mt.group(2))
            # Adresse enthält ?v=<Änderungszeit>, deshalb darf der Browser das Bild privat zwischenspeichern
            return self._send(200, data, typ, {"Cache-Control": "private, max-age=31536000, immutable"})
        mt = re.fullmatch(r"/api/fotos/(\d{4}-\d{2}-\d{2})/(vorne|seite|hinten)", path)
        if mt and m in ("PUT", "DELETE"):
            datum, ansicht = mt.groups()
            try:
                datetime.strptime(datum, "%Y-%m-%d")
            except ValueError:
                raise ApiError(400, "Ungültiges Datum")
            if m == "DELETE":
                if not ph.delete(u["id"], datum, ansicht):
                    raise ApiError(404, "Dieses Foto gibt es nicht")
                return self._json(200, {"ok": True})
            try:
                b = json.loads(self._body(MAX_FOTO_BODY) or b"{}")
                if not isinstance(b, dict):
                    raise ValueError
            except ValueError:
                raise ApiError(400, "Ungültige Anfrage")
            parts = {}
            for kind, limit in FOTO_ARTEN.items():
                v = b.get(kind)
                if v is None:
                    continue
                try:
                    raw = base64.b64decode(str(v), validate=True)
                except ValueError:
                    raise ApiError(400, f"{kind}: keine gültigen Bilddaten")
                if not raw or len(raw) > limit:
                    raise ApiError(413, f"{kind}: Bild zu groß")
                if not image_type(raw):
                    raise ApiError(400, f"{kind}: nur JPEG, PNG oder WebP")
                parts[kind] = raw
            if "bild" not in parts or "vorschau" not in parts:
                raise ApiError(400, "Zuschnitt und Vorschau fehlen")
            z = b.get("zuschnitt") if isinstance(b.get("zuschnitt"), dict) else {}
            if len(json.dumps(z)) > 2000:
                raise ApiError(400, "Ungültiger Zuschnitt")
            dims = [b.get(k) if isinstance(b.get(k), int) and 0 < b.get(k) < 100000 else None for k in ("breite", "hoehe")]
            orig = parts.get("original")
            res = ph.put(u["id"], datum, ansicht, z, parts["bild"], parts["vorschau"],
                         orig, image_type(orig) if orig else None, *dims)
            return self._json(200, res)
        raise ApiError(404, "Nicht gefunden")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionResetError, BrokenPipeError, TimeoutError, socket.timeout)):
            return            # z. B. Browser lehnt das selbst signierte Zertifikat ab – kein Grund für eine Fehlermeldung
        super().handle_error(request, client_address)


# ---------------------------------------------------------------- Zertifikat
def local_addresses():
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))      # sendet nichts, ermittelt nur die eigene Netzwerkadresse
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        addrs.update(a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


LEAF_DAYS = 397            # Gültigkeit des Server-Zertifikats (Chrome akzeptiert höchstens 398 Tage)
CA_DAYS = 3650             # eigene Zertifizierungsstelle: 10 Jahre
RENEW_DAYS = 30            # Server-Zertifikat so viele Tage vor Ablauf automatisch erneuern


def ensure_certificate(cert: Path, key: Path, extra_names):
    """Sorgt für eine eigene kleine Zertifizierungsstelle (CA) und ein davon ausgestelltes Server-Zertifikat.

    Die CA (ca.pem) kann auf Handy und PC als vertrauenswürdig installiert werden. Danach gibt es keine
    Browser-Warnung mehr, und Chrome kann den Tracker als App (PWA) installieren. Das Server-Zertifikat
    wird automatisch neu ausgestellt, wenn es bald abläuft oder sich Name bzw. IP-Adresse ändern.
    """
    folder = cert.parent
    folder.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key, marker = folder / "ca.pem", folder / "ca-schluessel.pem", folder / "ausgestellt.json"
    host = socket.gethostname().split(".")[0] or "koerperdaten"
    dns = sorted({host, host + ".local", host + ".fritz.box", "localhost", *[n for n in extra_names if not _is_ip(n)]})
    ips = sorted({"127.0.0.1", *local_addresses(), *[n for n in extra_names if _is_ip(n)]})
    backend = _cert_backend()

    if not (ca_cert.exists() and ca_key.exists()):
        if backend is None:
            if cert.exists() and key.exists():
                print("Hinweis: Für die eigene Zertifizierungsstelle (App-Installation am Handy) fehlt das Paket "
                      "'cryptography' bzw. 'openssl'. Das bisherige Zertifikat wird weiter verwendet.")
                return
            sys.exit("Für HTTPS wird ein Zertifikat gebraucht, es konnte aber keins erzeugt werden.\n"
                     "Installiere eins von beiden und starte erneut:\n"
                     "  Raspberry Pi:  sudo apt install python3-cryptography   (oder: openssl)\n"
                     "  Windows:       py -m pip install cryptography\n"
                     "Oder gib ein eigenes Zertifikat an: --zertifikat DATEI --schluessel DATEI")
        (_crypto_ca if backend == "cryptography" else _openssl_ca)(ca_cert, ca_key, host)
        _private(ca_key)
        cert.unlink(missing_ok=True)
        print(f"Neue Zertifizierungsstelle erzeugt ({backend}): {ca_cert}")

    wanted = {"ca": _fingerprint(ca_cert), "namen": dns + ips}
    need = not (cert.exists() and key.exists())
    if not need:
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
            bis = datetime.fromisoformat(info["bis"])
            need = (info.get("ca") != wanted["ca"] or sorted(info.get("namen", [])) != sorted(wanted["namen"])
                    or bis - now_utc() < timedelta(days=RENEW_DAYS))
        except Exception:
            need = True
    if not need:
        return
    if backend is None:
        print("Hinweis: Das Server-Zertifikat müsste erneuert werden, dafür fehlt 'cryptography' bzw. 'openssl'.")
        return
    (_crypto_leaf if backend == "cryptography" else _openssl_leaf)(cert, key, ca_cert, ca_key, host, dns, ips)
    _private(key)
    bis = now_utc() + timedelta(days=LEAF_DAYS)
    marker.write_text(json.dumps({**wanted, "bis": iso(bis)}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Server-Zertifikat ausgestellt (gültig bis {bis:%d.%m.%Y}) für: {', '.join(dns + ips)}")


def _is_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _private(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _fingerprint(pem_file: Path):
    return hashlib.sha256(pem_file.read_bytes()).hexdigest()


def _cert_backend():
    try:
        import cryptography.x509  # noqa: F401
        return "cryptography"
    except ImportError:
        pass
    try:
        if subprocess.run(["openssl", "version"], capture_output=True).returncode == 0:
            return "openssl"
    except FileNotFoundError:
        pass
    return None


def ca_display_name(host):
    return f"Koerperdaten CA ({host}, {now_utc():%Y-%m-%d})"


def _crypto_ca(ca_cert, ca_key, host):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ca_display_name(host)),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Koerperdaten-Server")])
    t = now_utc()
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(t - timedelta(days=1)).not_valid_after(t + timedelta(days=CA_DAYS))
         .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                      data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                      encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
         .sign(k, hashes.SHA256()))
    ca_key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ca_cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))


def _crypto_leaf(cert, key, ca_cert, ca_key, host, dns, ips):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    ca = x509.load_pem_x509_certificate(ca_cert.read_bytes())
    cak = serialization.load_pem_private_key(ca_key.read_bytes(), password=None)
    k = ec.generate_private_key(ec.SECP256R1())
    t = now_utc()
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    c = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                                  x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Koerperdaten-Server")]))
         .issuer_name(ca.subject).public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(t - timedelta(days=1)).not_valid_after(t + timedelta(days=LEAF_DAYS))
         .add_extension(x509.SubjectAlternativeName(san), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                      data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                      encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(cak.public_key()), critical=False)
         .sign(cak, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    # Kette: Server-Zertifikat + CA
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM) + ca_cert.read_bytes())


def _openssl(args, cwd):
    r = subprocess.run(["openssl", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"openssl {' '.join(args[:2])} fehlgeschlagen: {r.stderr.strip()}")


def _openssl_ca(ca_cert, ca_key, host):
    import tempfile
    ca_cert, ca_key = Path(ca_cert).resolve(), Path(ca_key).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        cnf = Path(tmp) / "ca.cnf"
        cnf.write_text("[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=v3\n"
                       f"[dn]\nCN={ca_display_name(host)}\nO=Koerperdaten-Server\n"
                       "[v3]\nbasicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
                       "subjectKeyIdentifier=hash\n", encoding="utf-8")
        _openssl(["req", "-x509", "-config", str(cnf), "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                  "-nodes", "-keyout", str(ca_key), "-out", str(ca_cert), "-days", str(CA_DAYS), "-sha256"], tmp)


def _openssl_leaf(cert, key, ca_cert, ca_key, host, dns, ips):
    import tempfile
    cert, key, ca_cert, ca_key = (Path(x).resolve() for x in (cert, key, ca_cert, ca_key))
    san = ",".join([f"DNS:{d}" for d in dns] + [f"IP:{i}" for i in ips])
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "ext.cnf").write_text("[v3]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                                   f"extendedKeyUsage=serverAuth\nsubjectAltName={san}\n"
                                   "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n", encoding="utf-8")
        _openssl(["req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                  "-keyout", str(key), "-out", "server.csr", "-subj", f"/CN={host}/O=Koerperdaten-Server"], tmp)
        _openssl(["x509", "-req", "-in", "server.csr", "-CA", str(ca_cert), "-CAkey", str(ca_key),
                  "-set_serial", "0x" + secrets.token_hex(16), "-days", str(LEAF_DAYS), "-sha256",
                  "-extfile", "ext.cnf", "-extensions", "v3", "-out", "server.pem"], tmp)
        cert.write_bytes((t / "server.pem").read_bytes() + ca_cert.read_bytes())


# ---------------------------------------------------------------- alte Daten (Version 1)
def import_legacy(db, user, legacy: Path):
    """Übernimmt die XML-Datei des Servers Version 1 beim Anlegen des ersten Administrators."""
    if not legacy or not legacy.exists() or db.get_data(user["id"]):
        return False
    data = legacy.read_bytes()
    if validate_xml(data):
        return False
    db.put_data(user["id"], data)
    legacy.rename(legacy.with_suffix(".xml.uebernommen"))
    print(f"Daten aus {legacy.name} wurden dem Benutzer {user['name']} zugeordnet.")
    return True


# ---------------------------------------------------------------- Kommandozeile
def ask_password(prompt="Passwort: "):
    while True:
        pw = getpass.getpass(prompt)
        p = password_problem(pw)
        if p:
            print(p)
            continue
        if getpass.getpass("Passwort wiederholen: ") != pw:
            print("Die Passwörter stimmen nicht überein.")
            continue
        return pw


def main():
    here = Path(__file__).resolve().parent
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--daten", default=str(here / "daten"), help="Ordner für Datenbank, Zertifikat und alte Daten")

    p = argparse.ArgumentParser(description="Datenhaltung mit Benutzerkonten für den Körperdaten-Tracker", parents=[common])
    sub = p.add_subparsers(dest="befehl")
    s = sub.add_parser("start", parents=[common], help="Server starten (Standard)")
    for target in (p, s):
        target.add_argument("--host", default="0.0.0.0", help="Netzwerkadresse (Standard: alle)")
        target.add_argument("--port", type=int, default=None, help="Port (Standard: 8443, ohne HTTPS 8080)")
        target.add_argument("--zertifikat", help="eigenes Zertifikat (PEM), z. B. von mkcert oder Let's Encrypt")
        target.add_argument("--schluessel", help="privater Schlüssel zum eigenen Zertifikat (PEM)")
        target.add_argument("--name", action="append", default=[], help="zusätzlicher Name oder IP für das selbst signierte Zertifikat")
        target.add_argument("--ohne-https", action="store_true", help="unverschlüsselt (nur hinter einem Reverse-Proxy)")
        target.add_argument("--sicherungen", type=int, default=100, help="Sicherungen je Benutzer (Standard: 100)")
        target.add_argument("--html", default=str(here / "Koerperdaten.html"), help="Pfad zur Tracker-Seite")
        target.add_argument("--leise", action="store_true", help="keine Zugriffe protokollieren")
        target.add_argument("--ca-kopie", help="Datei, in die eine Kopie des CA-Zertifikats (öffentlicher Teil) geschrieben wird")
        target.add_argument("--uebergabe", help="Ordner, in den beim Start und beim Beenden eine Kopie der Datenbank für einen Umzug geschrieben wird")
    a1 = sub.add_parser("benutzer-anlegen", parents=[common], help="Benutzer anlegen")
    a1.add_argument("name")
    a1.add_argument("--admin", action="store_true", help="als Administrator")
    sub.add_parser("benutzer-liste", parents=[common], help="Benutzer anzeigen")
    a3 = sub.add_parser("passwort-setzen", parents=[common], help="Passwort neu setzen")
    a3.add_argument("name")
    a4 = sub.add_parser("benutzer-loeschen", parents=[common], help="Benutzer mit allen Daten löschen")
    a4.add_argument("name")
    a5 = sub.add_parser("importieren", parents=[common], help="XML-Datei des Trackers einem Benutzer zuordnen")
    a5.add_argument("name")
    a5.add_argument("datei")
    a6 = sub.add_parser("sichern", parents=[common], help="konsistente Kopie der Datenbank anlegen (auch bei laufendem Server)")
    a6.add_argument("ziel", nargs="?", help="Zieldatei (Standard: daten/vor-update/koerperdaten-manuell-…db)")
    sub.add_parser("pruefen", parents=[common], help="Datenbank prüfen und Kennzahlen anzeigen")
    a8 = sub.add_parser("wiederherstellen", parents=[common], help="Datenbank aus einer Sicherung zurückspielen (Server vorher stoppen)")
    a8.add_argument("datei")
    a8.add_argument("--ja", action="store_true", help="ohne Rückfrage")
    p.add_argument("--version", action="version", version=f"Körperdaten-Server {VERSION} (Datenbank-Version {SCHEMA_VERSION})")
    a = p.parse_args()

    folder = Path(a.daten).expanduser().resolve()
    dbfile = folder / "koerperdaten.db"
    cmd = a.befehl or "start"

    if cmd in ("sichern", "pruefen"):
        if not dbfile.exists():
            sys.exit(f"Keine Datenbank gefunden: {dbfile}")
        if cmd == "pruefen":
            problem = integrity_problem(dbfile)
            if problem:
                sys.exit(problem)
            ro = Database(dbfile, 1, migrate=False)
            n_users = ro.one("SELECT COUNT(*) AS n FROM benutzer")["n"]
            n_data = ro.one("SELECT COUNT(*) AS n FROM daten")["n"] if ro.has_table("daten") else 0
            print(f"Datenbank in Ordnung: {dbfile}")
            print(f"  Datenbank-Version {ro.schema_version()} (Programm erwartet {SCHEMA_VERSION}), "
                  f"zuletzt genutzt von Programmversion {ro.meta('programmversion') or '2.0 oder älter'}")
            print(f"  {n_users} Benutzer, {n_data} mit Daten")
            pf = folder / "fotos.db"
            if pf.exists():
                pc = sqlite3.connect(f"file:{pf}?mode=ro", uri=True)
                try:
                    res = pc.execute("PRAGMA integrity_check").fetchone()[0]
                    n, size = pc.execute("SELECT COUNT(*), COALESCE(SUM(length(original)+length(bild)+length(vorschau)),0) FROM fotos").fetchone()
                finally:
                    pc.close()
                if res != "ok":
                    sys.exit(f"Fotodatenbank beschädigt: {res}")
                print(f"  Fotos in Ordnung: {n} Bilder, {size / 1048576:.1f} MB")
            return
        ro = Database(dbfile, 1, migrate=False)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = Path(a.ziel).expanduser() if a.ziel else folder / "vor-update" / f"koerperdaten-manuell-{stamp}.db"
        ro.backup_to(target)
        problem = integrity_problem(target)
        if problem:
            sys.exit(f"Die Sicherung ist fehlerhaft: {problem}")
        print(f"Gesichert: {target}")
        ftarget = target.with_name(target.stem + "-fotos.db")
        if copy_photo_db(folder / "fotos.db", ftarget):
            print(f"Fotos gesichert: {ftarget}")
        return

    if cmd == "wiederherstellen":
        src = Path(a.datei).expanduser().resolve()
        problem = integrity_problem(src)
        if problem:
            sys.exit(f"Diese Sicherung kann nicht verwendet werden: {problem}")
        print(f"Stoppe den Server, bevor du fortfährst. {dbfile} wird durch {src.name} ersetzt.")
        if not a.ja and input("Fortfahren? (ja/nein) ").strip().lower() != "ja":
            sys.exit("Abgebrochen.")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if dbfile.exists():
            keep = Database(dbfile, 1, migrate=False).backup_to(folder / "vor-update" / f"koerperdaten-vor-wiederherstellung-{stamp}.db")
            print(f"Bisheriger Stand gesichert: {keep}")
        for suffix in ("-wal", "-shm"):
            Path(str(dbfile) + suffix).unlink(missing_ok=True) if sys.version_info >= (3, 8) else None
        folder.mkdir(parents=True, exist_ok=True)
        tmp = dbfile.with_suffix(".tmp")
        srcdb = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dst = sqlite3.connect(str(tmp))
        try:
            srcdb.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
            srcdb.close()
        os.replace(tmp, dbfile)
        print("Wiederhergestellt. Server wieder starten; beim Start wird die Datenbank bei Bedarf auf den aktuellen Stand gebracht.")
        return

    db = Database(dbfile, getattr(a, "sicherungen", 100))
    legacy = folder / "koerperdaten.xml"

    if cmd == "benutzer-anlegen":
        first = db.user_count() == 0
        try:
            user = db.create_user(a.name, ask_password(), admin=a.admin or first)
        except ApiError as e:
            sys.exit(e.msg)
        if first:
            import_legacy(db, user, legacy)
        print(f"Benutzer {user['name']} angelegt{' (Administrator)' if user['admin'] else ''}.")
        return
    if cmd == "benutzer-liste":
        rows = db.list_users()
        if not rows:
            print("Noch keine Benutzer. Lege den ersten im Tracker oder mit 'benutzer-anlegen NAME' an.")
        for r in rows:
            print(f"{r['name']:<24} {'Admin' if r['admin'] else '     '}  angelegt {r['angelegt'][:10]}  "
                  f"letzte Anmeldung {(r['letzte_anmeldung'] or '–')[:16]}  Daten {(r['groesse'] or 0) // 1024} KB")
        return
    if cmd in ("passwort-setzen", "benutzer-loeschen", "importieren"):
        user = db.user(a.name)
        if not user:
            sys.exit(f"Den Benutzer {a.name} gibt es nicht.")
        try:
            if cmd == "passwort-setzen":
                db.set_password(user["id"], ask_password("Neues Passwort: "))
                print("Passwort gesetzt. Alle Anmeldungen dieses Benutzers wurden beendet.")
            elif cmd == "benutzer-loeschen":
                if input(f"{user['name']} und alle Daten wirklich löschen? (ja/nein) ").strip().lower() == "ja":
                    db.delete_user(user["id"])
                    print("Gelöscht.")
            else:
                data = Path(a.datei).expanduser().read_bytes()
                problem = validate_xml(data)
                if problem:
                    sys.exit(problem)
                db.put_data(user["id"], data)
                print(f"Daten aus {a.datei} gehören jetzt {user['name']}. Der bisherige Stand liegt in den Sicherungen.")
        except ApiError as e:
            sys.exit(e.msg)
        return

    # ---- Server starten
    use_https = not a.ohne_https
    port = a.port or (8443 if use_https else 8080)
    Handler.db = db
    try:
        Handler.photos = PhotoStore(folder / "fotos.db")
        removed = Handler.photos.remove_orphans(r["id"] for r in db.q("SELECT id FROM benutzer"))
        if removed:
            print(f"Fotos von {removed} gelöschten Benutzer(n) entfernt")
    except sqlite3.Error as e:
        print(f"Hinweis: Fotodatenbank nicht verfügbar ({e}) – der Tracker läuft ohne Fotos weiter")
        Handler.photos = None
    Handler.html_file = Path(a.html).expanduser()
    Handler.quiet = a.leise
    Handler.https = use_https
    Handler.legacy_xml = legacy
    try:
        httpd = Server((a.host, port), Handler)
    except OSError as e:
        sys.exit(f"Port {port} kann nicht geöffnet werden: {e}. Läuft der Server schon, oder ist der Port belegt?")
    if use_https:
        if a.zertifikat or a.schluessel:
            if not (a.zertifikat and a.schluessel):
                sys.exit("Für ein eigenes Zertifikat bitte --zertifikat UND --schluessel angeben.")
            cert, key = Path(a.zertifikat).expanduser(), Path(a.schluessel).expanduser()
        else:
            cert, key = folder / "zertifikat" / "zertifikat.pem", folder / "zertifikat" / "schluessel.pem"
            ensure_certificate(cert, key, a.name)
            Handler.ca_file = folder / "zertifikat" / "ca.pem"
            if getattr(a, "ca_kopie", None) and Handler.ca_file.exists():
                try:      # nur das öffentliche Stammzertifikat, nie den Schlüssel
                    dest = Path(a.ca_kopie).expanduser()
                    dest.write_bytes(ssl.PEM_cert_to_DER_cert(Handler.ca_file.read_text(encoding="ascii")))
                except OSError as e:
                    print(f"Hinweis: CA-Zertifikat konnte nicht nach {a.ca_kopie} kopiert werden: {e}")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.load_cert_chain(str(cert), str(key))
        except (OSError, ssl.SSLError) as e:
            sys.exit(f"Zertifikat konnte nicht geladen werden: {e}")
        # Handschlag erst im Arbeits-Thread, damit ein langsamer Client den Server nicht blockiert
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)

    scheme = "https" if use_https else "http"
    print(f"Körperdaten-Server {VERSION}")
    print(f"  Datenbank:    {db.path}")
    print(f"  Benutzer:     {db.user_count() or 'noch keine – beim ersten Öffnen im Browser anlegen'}")
    print(f"  Verbindung:   {'HTTPS' if use_https else 'HTTP (unverschlüsselt!)'}")
    print(f"  Tracker-Seite: {'gefunden' if Handler.html_file.exists() else 'nicht gefunden – nur Datenhaltung'}")
    if legacy.exists() and db.user_count() == 0:
        print(f"  Alte Daten:   {legacy.name} wird dem ersten Administrator zugeordnet")
    if not os.environ.get("SUPERVISOR_TOKEN"):      # in Home Assistant nennt run.sh die richtigen Adressen
        for addr in local_addresses() or ["<IP-Adresse dieses Rechners>"]:
            print(f"  Im Browser öffnen: {scheme}://{addr}:{port}/")
    if Handler.ca_file and Handler.ca_file.exists():
        print("  Handy-App:    CA-Zertifikat unter /ca.crt laden und installieren, dann im Browser „App installieren“")
    handover = Path(a.uebergabe).expanduser() if getattr(a, "uebergabe", None) else None
    if handover:
        write_handover(dbfile, handover)
    print("Beenden mit Strg+C")
    def _stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        db.close()
        if Handler.photos:
            Handler.photos.close()
        print("Server beendet, Datenbank sauber geschlossen.")
        if handover:
            write_handover(dbfile, handover)


if __name__ == "__main__":
    main()
