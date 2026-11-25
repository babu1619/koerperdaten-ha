# Server (`koerperdaten_server.py`)

Der Server ist eine einzelne Python-Datei, rund 1.500 Zeilen. Er nutzt nur die Standardbibliothek: `http.server`, `ssl`, `sqlite3` und `hashlib`. Die Zertifikate erzeugt er bevorzugt mit `cryptography`, ersatzweise ruft er `openssl` auf. Die Programmversion steht in `VERSION` (nur Haupt- und Nebenversion, derzeit `"2.8"`).

## Befehle

```
python3 koerperdaten_server.py [start] [--daten ORDNER] [--port 8443] [--name NAME …]
        [--zertifikat PEM --schluessel PEM] [--ohne-https] [--sicherungen 100]
        [--html PFAD] [--leise] [--ca-kopie DATEI] [--uebergabe ORDNER]
python3 koerperdaten_server.py benutzer-anlegen NAME [--admin]
python3 koerperdaten_server.py benutzer-liste
python3 koerperdaten_server.py passwort-setzen NAME
python3 koerperdaten_server.py benutzer-loeschen NAME
python3 koerperdaten_server.py importieren NAME DATEI.xml
python3 koerperdaten_server.py sichern [ZIEL]
python3 koerperdaten_server.py pruefen
python3 koerperdaten_server.py wiederherstellen DATEI [--ja]
python3 koerperdaten_server.py --version
```

Alle Befehle akzeptieren `--daten`. In Home Assistant ist das `/data`.

| Option | Wirkung |
|---|---|
| `--name` | weitere Namen oder IPs fürs eigene Zertifikat (mehrfach möglich) |
| `--ca-kopie` | schreibt das öffentliche Stammzertifikat (DER) zusätzlich in eine Datei; in HA nach `/share/koerperdaten-ca.crt` |
| `--uebergabe` | legt beim Start und beim Beenden eine geprüfte Kopie von Datenbank, Fotos und Zertifikat für einen Umzug ab |
| `--leise` | keine Zugriffe protokollieren |
| `--ohne-https` | nur hinter einem Reverse-Proxy sinnvoll (Port 8080) |

## Ablauf beim Start

1. Die Datenbank öffnen und bei Bedarf umbauen (`Database._migrate`). Vorher wird gesichert, siehe unten.
2. `fotos.db` öffnen und Fotos gelöschter Benutzer entfernen. Lässt sich die Fotodatenbank nicht öffnen, läuft der Server ohne Fotos weiter.
3. Port öffnen. Dann das Zertifikat sicherstellen (`ensure_certificate`), bei Bedarf das Stammzertifikat kopieren und den TLS-Kontext aufbauen (mindestens TLS 1.2). Der TLS-Handschlag läuft im Arbeits-Thread, damit ein langsamer Client den Server nicht blockiert.
4. Bei `--uebergabe` die Umzugsdaten schreiben.
5. `serve_forever()` mit `ThreadingHTTPServer`. SIGTERM beendet den Server sauber: Datenbanken schließen und Umzugsdaten erneut schreiben.

## HTTP-Schnittstelle

Alle `/api`-Aufrufe außer Status, Einrichtung und Anmeldung brauchen `Authorization: Bearer <token>`. Fehler kommen als JSON `{"fehler": "…"}` mit passendem Statuscode. CORS ist offen (Origin wird gespiegelt), damit auch die lokale HTML-Datei den Server nutzen kann.

| Methode und Pfad | Zweck |
|---|---|
| `GET /` | Tracker (`Koerperdaten.html`) |
| `GET /manifest.webmanifest`, `/sw.js`, `/icon-*.png` | PWA |
| `GET /ca.crt` | Stammzertifikat als DER, `application/octet-stream` |
| `GET /api/status` | `app`, `version`, `datenbank_version`, `https`, `ca`, `fotos`, `einrichtung_noetig` |
| `POST /api/einrichtung` | ersten Benutzer (Administrator) anlegen, nur solange es keinen gibt |
| `POST /api/anmelden` | `{name, passwort}` → `{token, name, admin}` |
| `POST /api/abmelden` | Sitzung beenden |
| `GET /api/ich` | eigener Name und Adminrecht |
| `POST /api/ich/passwort` | `{alt, neu}` |
| `GET /api/daten` | XML des Benutzers mit `ETag`; 404, wenn noch nichts gespeichert ist |
| `PUT /api/daten` | XML speichern; `If-Match: <etag>` oder `"neu"`; 412 mit aktuellem `etag` bei Konflikt |
| `GET /api/sicherungen` | frühere Stände des Benutzers |
| `POST /api/sicherungen/<id>/wiederherstellen` | Stand zurückholen; der aktuelle wird vorher gesichert |
| `GET`, `POST /api/benutzer` | Benutzer auflisten oder anlegen (nur Administratoren) |
| `POST`, `DELETE /api/benutzer/<name>` | Passwort oder Adminrecht setzen, Benutzer samt Daten und Fotos löschen (nur Administratoren) |
| `GET /api/fotos` | Liste der eigenen Fotos (Metadaten) |
| `GET /api/fotos/<id>/(bild\|vorschau\|original)` | Bilddaten; unveränderlich zwischenspeicherbar, die Adresse enthält `?v=<Änderung>` |
| `PUT`, `DELETE /api/fotos/<datum>/(vorne\|seite\|hinten)` | Foto speichern (JSON mit Base64 `bild`, `vorschau`, optional `original`, `zuschnitt`, `breite`, `hoehe`) oder löschen |

**Grenzen:**
- XML höchstens 20 MiB, Foto-Anfrage höchstens 48 MiB, sonstiges JSON höchstens 64 KiB.
- Je Foto höchstens 4 MiB für das Bild, 512 KiB für die Vorschau und 30 MiB für das Original.
- Erlaubt sind nur JPEG, PNG und WebP; der Server prüft die Magic Bytes.

## Datenbank `koerperdaten.db`

Die Datenbank läuft im WAL-Modus mit `foreign_keys=ON` und `busy_timeout=5000`.

| Tabelle | Inhalt |
|---|---|
| `benutzer` | `id`, `name` (eindeutig, ohne Groß/Klein), `passwort` (Hash), `admin`, `angelegt`, `letzte_anmeldung` |
| `sitzungen` | `token_hash` (SHA-256 des Tokens), `benutzer_id`, `angelegt`, `laeuft_ab`, `geraet` |
| `daten` | je Benutzer ein Datensatz: `xml`, `etag`, `geaendert` |
| `sicherungen` | frühere XML-Stände je Benutzer |
| `meta` | `programmversion` u. a. |

**Versionen der Datenbank:**
- Die Versionsnummer steht in `PRAGMA user_version`.
- `MIGRATIONS` ist ein Wörterbuch Nummer → SQL. Bestehende Schritte werden **nie verändert**, jede Änderung bekommt eine neue Nummer.
- Derzeit gibt es Version 2: 1 ist der Grundaufbau, 2 die Tabelle `meta`.

**Sicherung vor Updates:**
- Wechselt die Programmversion oder die Datenbankversion, kopiert der Server die Datenbank vorher nach `vor-update/koerperdaten-vor-<VERSION>-<Zeit>.db` (SQLite-Backup-API). Die letzten 10 Kopien bleiben erhalten.
- Schlägt ein Umbauschritt fehl, rollt der Server zurück und beendet sich. Die Datenbank bleibt dann unverändert.
- Ist die Datenbank neuer als das Programm, startet der Server nicht.

**Sicherungen je Benutzer:**
- Vor jedem Speichern legt der Server den bisherigen Stand in `sicherungen` ab, höchstens einmal alle 10 Minuten.
- Aufbewahrt werden die neuesten N Stände, N ist die Option `sicherungen` (Standard 100).

## Fotodatenbank `fotos.db`

Die Fotos liegen in einer eigenen Datei, damit die Hauptdatenbank, die XML-Sicherungen und der Umzug klein und schnell bleiben.

- Tabelle `fotos`: `benutzer_id`, `datum`, `ansicht`, `zuschnitt` (JSON), `original` mit `original_typ`, `bild`, `vorschau`, `breite`, `hoehe`, `geaendert`. Eindeutig ist `(benutzer_id, datum, ansicht)`.
- Ein PUT ersetzt das Foto. Kommt kein neues Original mit, bleibt das bisherige erhalten. So lässt sich nur der Zuschnitt ändern.
- `sichern` erzeugt zusätzlich `…-fotos.db`, der Umzug nimmt `fotos.db` mit.

## Anmeldung und Schutz

- **Passwörter:** scrypt (N=2¹⁴, r=8, p=1), ersatzweise PBKDF2-SHA256 mit 600.000 Runden. Format: `scrypt$N$r$p$salz$hash`. Mindestens 8 Zeichen.
- **Namen:** 2–32 Zeichen aus Buchstaben, Ziffern, `.`, `_` und `-`.
- **Sitzungen:** 32-Byte-Token, in der Datenbank nur als SHA-256 gespeichert. Gültig 90 Tage ab letzter Nutzung (gleitend, höchstens einmal am Tag verlängert).
- **Bremse:** höchstens 5 Fehlversuche je Name und 20 je IP in 10 Minuten (`LoginThrottle`). Bei unbekannten Namen wird gegen einen Dummy-Hash geprüft, damit die Antwortzeit nichts verrät.
- Ändert jemand sein eigenes Passwort, enden alle anderen Sitzungen. Die aktuelle bleibt bestehen.

## Zertifikate

`ensure_certificate` legt in `zertifikat/` eine eigene kleine Zertifizierungsstelle an:

| Datei | Inhalt |
|---|---|
| `ca.pem` / `ca-schluessel.pem` | Stammzertifikat (10 Jahre) und dessen privater Schlüssel |
| `zertifikat.pem` / `schluessel.pem` | Server-Zertifikat, 397 Tage gültig (Chrome erlaubt höchstens 398) |
| `ausgestellt.json` | für welche Namen und IPs ausgestellt wurde |
| `namen.txt` | von `run.sh` gepflegt; ändert sich der Inhalt, wird neu ausgestellt |

- Das Server-Zertifikat wird 30 Tage vor Ablauf oder bei geänderten Namen und IPs automatisch neu ausgestellt. Das Stammzertifikat bleibt dabei gleich, Geräte müssen es also nur einmal installieren.
- Enthalten sind: Hostname, `.local`, `.fritz.box`, `localhost`, alle lokalen IPs und die Namen aus `--name`.
- Mit `--zertifikat`/`--schluessel` (in HA die Option „Eigenes Zertifikat“) nutzt der Server ein fremdes Zertifikat, etwa von Let's Encrypt.

## Service Worker

`sw.js` wird mit der Programmversion im Cache-Namen ausgeliefert (`koerperdaten-<VERSION>`). Bei jeder neuen Version entsteht also ein frischer Zwischenspeicher, alte werden gelöscht.

- **Eigene Seite und Dateien:** Netzwerk zuerst. Antwortet der Server nicht innerhalb von 4 s (Seite) bzw. 6 s (übrige Dateien), kommt der gespeicherte Stand. So kommen Updates sofort an, und offline startet die App trotzdem.
- **`/api/…` und `/ca.crt`:** nie aus dem Zwischenspeicher.
- **Chart.js und Google Fonts:** aus dem Zwischenspeicher, sonst aus dem Netz und dann gespeichert. Chart.js wird schon bei der Installation geladen.

## Übernahme alter Daten

`import_legacy`: Liegt beim Anlegen des ersten Administrators noch eine XML-Datei des Servers Version 1 im Datenordner, wird sie diesem Benutzer zugeordnet und in `*.xml.uebernommen` umbenannt.
