# Architektur

## Bausteine

```
 Handy (PWA)        PC-Browser
     │                  │
     └──── HTTPS :8443 ─┘           nur Heimnetz oder VPN
               │
 ┌─────────────▼───────────────────────────────────────────┐
 │ Home Assistant Green – App „Körperdaten-Server“ (Docker) │
 │                                                          │
 │  run.sh ──► koerperdaten_server.py                       │
 │               │  liefert Koerperdaten.html, sw.js,        │
 │               │  Manifest, Icons, ca.crt                  │
 │               │  REST-API /api/…                          │
 │               ▼                                          │
 │   /data/koerperdaten.db   Benutzer, Sitzungen,           │
 │                           Daten (XML je Benutzer),       │
 │                           Sicherungen, meta              │
 │   /data/fotos.db          Fotos (Original, Zuschnitt,    │
 │                           Vorschau)                      │
 │   /data/zertifikat/       eigene CA + Server-Zertifikat  │
 │   /data/vor-update/       Sicherungen vor jedem Update   │
 └──────────────────────────────────────────────────────────┘
```

Das System hat zwei Teile:

1. **Tracker** (`koerperdaten/Koerperdaten.html`): eine einzige Datei mit HTML, CSS und JavaScript. Darin stecken die ganze Oberfläche und die ganze Auswertung: Diagramme, Formeln, Bewertungen, Import und Export. Der Tracker kann auch ohne Server laufen (Speicherart „Browser“ oder „XML-Datei“). Weiterentwickelt wird aber nur noch der Betrieb mit dem Server.
2. **Server** (`koerperdaten/koerperdaten_server.py`): ein Python-Programm, das nur die Standardbibliothek braucht. Fürs Zertifikat kommt `cryptography` dazu, ersatzweise `openssl`. Der Server liefert den Tracker aus, verwaltet Benutzer und Anmeldungen und speichert je Benutzer einen XML-Datensatz mit Versionsstand und Sicherungen. Außerdem speichert er die Fotos.

Die Rechenlogik liegt vollständig im Tracker. Der Server kennt die einzelnen Messwerte nicht. Er speichert den XML-Datensatz, prüft nur, ob er wohlgeformt ist, und schützt ihn vor gleichzeitigen Änderungen (ETag).

## Datenfluss

1. Beim Start fragt der Tracker `/api/status` ab und meldet sich mit einem Bearer-Token an. Das Token liegt im `localStorage`, Schlüssel `koerperdaten-config`.
2. `GET /api/daten` liefert das XML samt ETag. Der Tracker wandelt es in `S.rows` (Messwerte je Tag) und `S.settings` (Einstellungen) um.
3. Jede Änderung ruft `markDirty()` auf. Nach kurzer Verzögerung speichert `saveNow()` das komplette XML mit `PUT /api/daten` und `If-Match: <ETag>`.
4. Hat ein anderes Gerät inzwischen gespeichert, antwortet der Server mit 412. Der Tracker arbeitet dann seine Änderungen tageweise in den neuen Stand ein (siehe [Offline und Synchronisierung](offline.md)).
5. Parallel dazu hält der Tracker eine Offline-Kopie der letzten 31 Tage im `localStorage`.
6. Fotos laufen getrennt über `/api/fotos` und liegen in `fotos.db`, nicht im XML.

## Dateien im Repository

| Pfad | Zweck |
|---|---|
| `repository.yaml` | macht das Repository zu einem App-Repository für Home Assistant |
| `koerperdaten/config.yaml` | App-Beschreibung für Home Assistant: **Version**, Optionen, Ports, Ordner |
| `koerperdaten/Dockerfile` | Image auf Basis `ghcr.io/home-assistant/base` mit `python3` und `py3-cryptography` |
| `koerperdaten/run.sh` | Startskript (bashio): Optionen lesen, Umzug, Zertifikatsnamen, Server starten |
| `koerperdaten/koerperdaten_server.py` | der Server |
| `koerperdaten/Koerperdaten.html` | der Tracker |
| `koerperdaten/DOCS.md` | Anwender-Doku, in Home Assistant sichtbar |
| `koerperdaten/CHANGELOG.md` | Änderungen je Version, in Home Assistant beim Update sichtbar |
| `koerperdaten/translations/` | Beschriftungen der App-Optionen (de, en) |
| `koerperdaten/icon.png`, `logo.png` | Bilder im App Store |
| `tools/release.py` | Version erhöhen, Changelog ergänzen, prüfen, committen, taggen, pushen |
| `tools/pruefen.py` | Prüfung des Repositorys (lokal und in GitHub Actions) |
| `tools/einrichten.py` | einmalig: GitHub-Namen in die Dateien eintragen |
| `.github/workflows/pruefen.yaml` | Prüfung, Server-Probelauf, Image-Bau, Hinweis bei vergessener Versionserhöhung |
| `.github/workflows/images.yaml` | optional vorgebaute Images auf ghcr.io (nur mit `image:` in `config.yaml`) |
| `.gitattributes` | erzwingt LF-Zeilenenden (sonst startet `run.sh` nicht) |
| `.gitignore` | hält Datenbanken und Daten aus dem Repository |
| `ANLEITUNG-GITHUB.md` | Einrichtung von Repository und App, Umzug von der lokalen App |
| `docs/` | diese Dokumentation |
| `CLAUDE.md` | Arbeitsanweisungen für Claude |

## Externe Abhängigkeiten

| Was | Woher | Wofür |
|---|---|---|
| Chart.js 4.4.1 | cdnjs.cloudflare.com | Diagramme; der Service Worker legt die Bibliothek im Zwischenspeicher ab |
| Schrift Manrope | Google Fonts | modernes Design; wird ebenfalls zwischengespeichert |
| `cryptography` | Alpine-Paket `py3-cryptography` | Zertifikate; ersatzweise `openssl` |
| WHO-Wachstumsdaten | als LMS-Tabelle in den Tracker eingebettet | BMI-Perzentile für Kinder |

Darüber hinaus nimmt der Tracker keine Verbindung ins Internet auf. Messwerte verlassen das Heimnetz nie.

## Eingefrorene Varianten

Bis Version 2.4 gab es zwei weitere Pakete außerhalb dieses Repositorys. Beide werden nicht weiterentwickelt:

- **Eigenständiger Server** (`koerperdaten-server`): derselbe Server für Raspberry Pi (systemd-Dienst `koerperdaten.service`) und Windows (`start-koerperdaten.bat`).
- **Reine XML-Variante:** nur die HTML-Datei, Daten in einer lokalen XML-Datei, deren Ort man selbst wählt (File System Access API).

Der Code für beide Betriebsarten steckt weiterhin im Tracker und im Server, etwa die Speicherart `xml` und das Starten ohne Home Assistant. Neue Funktionen müssen dort aber nicht mehr funktionieren. Offline-Betrieb, Fotos und das moderne Design setzen den Server voraus.
