"""Hilfen für die Tests: echten Körperdaten-Server in einem leeren Ordner starten und die API aufrufen."""
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "koerperdaten" / "koerperdaten_server.py"


def freier_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Testserver:
    """Startet den Server ohne HTTPS (Port frei gewählt) mit eigenem Datenordner.

    Ohne HTTPS gilt 127.0.0.1 im Browser trotzdem als sicherer Kontext, daher läuft auch der Service Worker.
    """

    def __init__(self, daten=None):
        self._tmp = None if daten else tempfile.TemporaryDirectory(prefix="kd-test-")
        self.daten = Path(daten or self._tmp.name)
        self.port = freier_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = None

    def cli(self, *args, eingabe=None):
        """Befehl des Servers ausführen, z. B. cli('pruefen')."""
        return subprocess.run([sys.executable, str(SERVER), *args, "--daten", str(self.daten)],
                              input=eingabe, capture_output=True, text=True, timeout=60)

    def __enter__(self):
        self.proc = subprocess.Popen([sys.executable, str(SERVER), "start", "--daten", str(self.daten),
                                      "--port", str(self.port), "--ohne-https", "--leise"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            try:
                self.get("/api/status")
                return self
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError("Server startet nicht:\n" + self.proc.stdout.read())
                time.sleep(0.1)
        raise RuntimeError("Server antwortet nicht")

    def stoppen(self):
        """Server beenden, Datenordner bleibt erhalten."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc and self.proc.stdout:
            self.proc.stdout.close()

    def __exit__(self, *exc):
        self.stoppen()
        if self._tmp:
            self._tmp.cleanup()

    # ---- HTTP
    def anfrage(self, methode, pfad, daten=None, token=None, kopf=None):
        """Gibt (Status, Kopfzeilen, Inhalt) zurück; Inhalt als dict bei JSON, sonst bytes."""
        h = dict(kopf or {})
        if token:
            h["Authorization"] = "Bearer " + token
        if isinstance(daten, (dict, list)):
            daten = json.dumps(daten).encode()
            h.setdefault("Content-Type", "application/json")
        elif isinstance(daten, str):
            daten = daten.encode()
            h.setdefault("Content-Type", "application/xml; charset=utf-8")
        req = urllib.request.Request(self.url + pfad, data=daten, method=methode, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status, headers, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, headers, body = e.code, e.headers, e.read()
        if (headers.get("Content-Type") or "").startswith("application/json"):
            body = json.loads(body or b"null")
        return status, headers, body

    def get(self, pfad, token=None):
        return self.anfrage("GET", pfad, token=token)

    def einrichten(self, name="anna", passwort="geheim123"):
        s, _, b = self.anfrage("POST", "/api/einrichtung", {"name": name, "passwort": passwort})
        assert s == 201, b
        return b["token"]

    def anmelden(self, name, passwort):
        s, _, b = self.anfrage("POST", "/api/anmelden", {"name": name, "passwort": passwort})
        assert s == 200, b
        return b["token"]


def xml(eintraege, design="modern"):
    """Minimaler gültiger Datensatz: eintraege = {'2026-11-01': {'weight': 90.0}, …}."""
    zeilen = "".join(
        f'<eintrag datum="{d}" ' + " ".join(f'{k}="{v}"' for k, v in w.items()) + "/>"
        for d, w in sorted(eintraege.items()))
    ansicht = json.dumps({"fv": 4, "design": design})
    return ('<?xml version="1.0" encoding="UTF-8"?><koerperdaten version="1">'
            '<einstellungen><koerpergroesse_cm>180</koerpergroesse_cm><geschlecht>m</geschlecht>'
            '<geburtsdatum>1980-01-01</geburtsdatum></einstellungen>'
            f'<ansicht><![CDATA[{ansicht}]]></ansicht>'
            f'<eintraege anzahl="{len(eintraege)}">{zeilen}</eintraege></koerperdaten>')
