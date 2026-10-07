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
VERSION = "2.2"
MAX_BODY = 20 * 1024 * 1024        # XML-Daten
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

        if m == "GET" and path == "/api/status":
            return self._json(200, {"app": APP, "version": VERSION, "datenbank_version": SCHEMA_VERSION, "https": self.https,
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
                return self._json(200, {"ok": True})
            b = self._json_body()
            if "passwort" in b:
                db.set_password(target["id"], b.get("passwort"), keep_token_hash=me["token_hash"] if target["id"] == me["id"] else None)
            if "admin" in b:
                db.set_admin(target["id"], bool(b["admin"]))
            return self._json(200, {"ok": True})

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


def ensure_certificate(cert: Path, key: Path, extra_names):
    if cert.exists() and key.exists():
        return
    cert.parent.mkdir(parents=True, exist_ok=True)
    host = socket.gethostname().split(".")[0] or "koerperdaten"
    dns = sorted({host, host + ".local", host + ".fritz.box", "localhost", *[n for n in extra_names if not _is_ip(n)]})
    ips = sorted({"127.0.0.1", *local_addresses(), *[n for n in extra_names if _is_ip(n)]})
    try:
        _cert_with_cryptography(cert, key, host, dns, ips)
        how = "Python-Paket cryptography"
    except ImportError:
        if not _cert_with_openssl(cert, key, host, dns, ips):
            sys.exit("Für HTTPS wird ein Zertifikat gebraucht, es konnte aber keins erzeugt werden.\n"
                     "Installiere eins von beiden und starte erneut:\n"
                     "  Raspberry Pi:  sudo apt install python3-cryptography   (oder: openssl)\n"
                     "  Windows:       py -m pip install cryptography\n"
                     "Oder gib ein eigenes Zertifikat an: --zertifikat DATEI --schluessel DATEI")
        how = "openssl"
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass
    print(f"Neues selbst signiertes Zertifikat erzeugt ({how}) für: {', '.join(dns + ips)}")


def _is_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _cert_with_cryptography(cert, key, host, dns, ips):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Koerperdaten-Server")])
    t = now_utc()
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(t - timedelta(days=1)).not_valid_after(t + timedelta(days=825))
         .add_extension(x509.SubjectAlternativeName(san), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
         .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))


def _cert_with_openssl(cert, key, host, dns, ips):
    san = ",".join([f"DNS:{d}" for d in dns] + [f"IP:{i}" for i in ips])
    cmd = ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
           "-keyout", str(key), "-out", str(cert), "-days", "825", "-subj", f"/CN={host}/O=Koerperdaten-Server",
           "-addext", f"subjectAltName={san}", "-addext", "extendedKeyUsage=serverAuth"]
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


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
            return
        ro = Database(dbfile, 1, migrate=False)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = Path(a.ziel).expanduser() if a.ziel else folder / "vor-update" / f"koerperdaten-manuell-{stamp}.db"
        ro.backup_to(target)
        problem = integrity_problem(target)
        if problem:
            sys.exit(f"Die Sicherung ist fehlerhaft: {problem}")
        print(f"Gesichert: {target}")
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
        print("Server beendet, Datenbank sauber geschlossen.")
        if handover:
            write_handover(dbfile, handover)


if __name__ == "__main__":
    main()
