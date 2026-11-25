# Betrieb in Home Assistant

## Installation

Das GitHub-Repository ist ein App-Repository für Home Assistant (`repository.yaml`). Home Assistant baut das Image beim Installieren und bei jedem Update selbst auf dem Gerät (`Dockerfile`).

Optional lassen sich fertige Images von ghcr.io nutzen. Dafür in `config.yaml` die Zeile `image:` einschalten, dann baut `images.yaml` sie in GitHub Actions. Derzeit ist das nicht aktiv.

Die Schritte für Anwender stehen in der [App-Dokumentation](../koerperdaten/DOCS.md) und in [ANLEITUNG-GITHUB.md](../ANLEITUNG-GITHUB.md).

## `config.yaml`

| Feld | Wert | Hinweis |
|---|---|---|
| `slug` | `koerperdaten` | **nie ändern**: Home Assistant sähe sonst eine neue App ohne Daten (`pruefen.py` erzwingt das) |
| `version` | `"2.8.0"` | in Anführungszeichen; jede Erhöhung erscheint in Home Assistant als Update |
| `arch` | `aarch64`, `amd64` | Home Assistant Green ist aarch64 |
| `startup` / `boot` | `application` / `auto` | |
| `hassio_api` | `true` | für bashio (Netzwerkadressen, Hostname, Port) |
| `backup` | `cold` | die App wird für Home-Assistant-Sicherungen kurz angehalten, damit SQLite konsistent ist |
| `map` | `ssl` (nur lesen), `share` (schreiben) | `share` für das Stammzertifikat und den Umzug |
| `ports` | `8443/tcp` | änderbar im Reiter Netzwerk |
| `webui` / `watchdog` | `https://[HOST]:[PORT:8443]/` / TCP-Prüfung | |

**Optionen:**

| Option | Wirkung |
|---|---|
| `sicherungen` | Stände je Benutzer (1–1000) |
| `zusaetzliche_namen` | weitere Namen fürs Zertifikat |
| `eigenes_zertifikat`, `zertifikat`, `schluessel` | Zertifikat aus `/ssl` |
| `protokoll` | Zugriffe protokollieren |
| `umzug_bereitstellen` | Umzugsdaten in `/share/koerperdaten/umzug` schreiben |

Die Beschriftungen stehen in `translations/de.yaml` und `translations/en.yaml`. `pruefen.py` stellt sicher, dass `options`, `schema` und die Übersetzungen zusammenpassen.

## `run.sh`

1. **Umzug übernehmen:** Liegt `/share/koerperdaten/umzug/koerperdaten.db` vor und hat diese App noch keine Benutzer, übernimmt das Skript die Daten:
   - Datenbank über `wiederherstellen --ja`
   - Zertifikatsordner und `fotos.db` kopieren
   - Quelle nach `/data/vor-update/koerperdaten-umzug-<Zeit>.db` verschieben und `share` aufräumen

   Hat die App schon Benutzer, überschreibt das Skript nichts und warnt im Protokoll.
2. IPv4-Adressen und Hostname des Home-Assistant-Rechners ermitteln.
3. **Zertifikat:**
   - mit eigenem Zertifikat: die Pfade aus `/ssl` übergeben
   - sonst: Namen sammeln (`homeassistant`, `homeassistant.local`, Hostname, IPs, zusätzliche Namen). Haben sie sich gegenüber `/data/zertifikat/namen.txt` geändert, das Server-Zertifikat löschen, damit es neu ausgestellt wird. Dazu `--ca-kopie /share/koerperdaten-ca.crt`.
4. Bei `umzug_bereitstellen`: `--uebergabe /share/koerperdaten/umzug` mit Rechten 700.
5. `--leise`, wenn `protokoll` aus ist.
6. Die Adressen ins Protokoll schreiben und den Server mit `exec` starten, damit SIGTERM beim Stoppen direkt ankommt.

## Daten auf dem Gerät

```
/data/                         (App-Ordner, nur für diese App)
├── koerperdaten.db (+ -wal, -shm)
├── fotos.db
├── zertifikat/                ca.pem, ca-schluessel.pem, zertifikat.pem, schluessel.pem, ausgestellt.json, namen.txt
└── vor-update/                Sicherungen vor Updates und Umzügen (die letzten 10)
/share/
├── koerperdaten-ca.crt        öffentliches Stammzertifikat (DER), für Samba
└── koerperdaten/umzug/        nur während eines Umzugs
```

Beim **Deinstallieren** löscht Home Assistant `/data` vollständig. Deshalb gilt: nie deinstallieren, um neu zu installieren. Vorher immer erst die Daten umziehen.

## Updates

1. Eine neue Version liegt auf GitHub (`config.yaml` → `version`).
2. In Home Assistant: App Store → ⋮ → Nach Updates suchen → Aktualisieren.
3. Home Assistant baut das neue Image. Beim ersten Start sichert der Server die Datenbank nach `vor-update/` und baut sie bei Bedarf um.
4. Der Service Worker holt die neue Seite beim nächsten Öffnen. Neue Programmversion bedeutet neuer Cache-Name.

Wird kein Update angezeigt, liegt es meist an einem von drei Gründen: Die Version in `config.yaml` wurde nicht erhöht, sie steht nicht in Anführungszeichen, oder Home Assistant hat das Repository noch nicht neu gelesen. Dann hilft „Nach Updates suchen“ und anschließend die Seite neu laden.

## Umzug zwischen zwei Installationen

Der Umzug wird gebraucht, wenn eine andere Installation dieser App die Daten übernehmen soll, zum Beispiel von der lokalen App zur GitHub-App. Für Home Assistant sind das zwei verschiedene Apps mit getrennten `/data`-Ordnern.

**Automatisch über `share`:**
1. In der alten App „Umzugsdaten bereitstellen“ einschalten und die App stoppen.
2. Die neue App starten. Im Protokoll steht dann „Umzug abgeschlossen“.

**Manuell über den Tracker**, wenn die alte Version die Option noch nicht hat:
1. Die neue App auf einem zweiten Port starten, z. B. 8444.
2. Im Tracker mit der alten App verbunden die Daten anzeigen.
3. Speicherort auf den neuen Server umstellen, anmelden, „Übernehmen“ wählen.
4. Die Schritte für jeden Benutzer wiederholen.

## Sicherung

- **Home-Assistant-Sicherungen** enthalten `/data` mit allen Daten, Fotos und Zertifikaten (`backup: cold`).
- **Sicherungen je Benutzer:** im Tracker unter Einstellungen → Speicherort.
- **Von Hand:** `docker exec … koerperdaten_server.py sichern --daten /data` über die App „Advanced SSH & Web Terminal“ mit ausgeschaltetem Schutzmodus.

## Sicherheit

- Die App ist nur fürs Heimnetz gedacht. Der Port wird im Router **nicht** freigegeben.
- Von unterwegs geht der Zugriff über VPN: WireGuard über die FRITZ!Box oder Tailscale.
- Der private Schlüssel der CA (`ca-schluessel.pem`) verlässt `/data` nicht. Nach `share` kommt nur der öffentliche Teil.
- Die Umzugsdaten in `share` enthalten Passwort-Hashes und Gesundheitsdaten. Sie werden nach der Übernahme verschoben, und das Skript warnt, wenn sie liegen bleiben.
- Das Repository enthält keine Daten. `.gitignore` schließt `*.db` und `daten/` aus.
