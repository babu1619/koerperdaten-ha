# CLAUDE.md – Körperdaten-Tracker für Home Assistant

Körper- und Fitnessdaten-Tracker einer Familie. Er läuft als Home-Assistant-App auf einem Home Assistant Green (aarch64) und wird über dieses Repository ausgeliefert. Der Nutzer schreibt Deutsch und arbeitet am Windows-PC und am Android-Handy.

Vor größeren Änderungen `docs/` lesen, vor allem `docs/entscheidungen.md`.

## Aufbau

```
koerperdaten/
  Koerperdaten.html        Tracker: gesamte Oberfläche und Logik in EINER Datei (HTML+CSS+JS, Chart.js 4.4.1 vom CDN)
  koerperdaten_server.py   Server: EINE Python-Datei, nur Standardbibliothek + cryptography
  config.yaml              App-Beschreibung für Home Assistant, enthält die Version
  run.sh                   Startskript (bashio), Umzug, Zertifikatsnamen
  Dockerfile               ghcr.io/home-assistant/base + python3 + py3-cryptography
  DOCS.md, CHANGELOG.md    für Anwender, in Home Assistant sichtbar
  translations/de|en.yaml  Beschriftungen der Optionen
tools/release.py           Version erhöhen, Changelog, prüfen, committen, taggen, pushen
tools/pruefen.py           Prüfung (lokal und in GitHub Actions)
tests/                     Server- und Playwright-Tests gegen einen echten Server (siehe docs/entwicklung.md)
docs/                      Entwickler-Doku: Architektur, Tracker, Server, Datenformate, Offline, Betrieb, Entwicklung, Entscheidungen
```

- Die ganze Fachlogik (Messgrößen, Formeln, Bewertungen, Diagramme, Import und Export) liegt im Tracker.
- Der Server speichert je Benutzer ein XML-Dokument (BLOB mit ETag) und Fotos in einer eigenen `fotos.db`. Außerdem verwaltet er Konten, Sitzungen, Sicherungen und Zertifikate.

## Versionen – wichtig

- **Aktuell veröffentlicht: 2.8.0.** Nächste Version: 2.8.1 (Korrektur) bzw. 2.9.0 (Funktion).
- **Vor jeder Versionsänderung den echten Stand prüfen:** `git pull`, dann `koerperdaten/config.yaml` lesen. Nie eine Nummer aus dem Gedächtnis nehmen, denn 2.4.0 und 2.6.0 wurden nie veröffentlicht.
- **Die Version steht an drei Stellen:**
  - `config.yaml` → `version: "X.Y.Z"` (Anführungszeichen!)
  - `koerperdaten_server.py` → `VERSION = "X.Y"`
  - `Koerperdaten.html` → `const TRACKER_VERSION = 'X.Y';`
- Jede Änderung in `koerperdaten/` braucht eine höhere Version und einen Eintrag oben in `CHANGELOG.md`: kurze Stichpunkte für Anwender, die Wirkung statt Technik beschreiben. Sonst zeigt Home Assistant kein Update an.
- Änderungen nur in `docs/`, `README.md` oder `CLAUDE.md` brauchen keine neue Version.
- **Nach einem Release:** in der Wunschliste (https://claude.ai/artifact/PYEwe1hyUqF7hw35KdJxx6, Datensammlung `wuensche`) umgesetzte Punkte auf „umgesetzt“ setzen und die Version eintragen.

## Befehle

```
python tools/pruefen.py                                   # vor jedem Commit
python -m unittest discover -s tests -v                   # alle Tests (Playwright-Tests werden ohne Playwright übersprungen)
python tools/release.py patch|minor "Text" [--ohne-push]  # Version erhöhen und veröffentlichen
python koerperdaten/koerperdaten_server.py start --daten /tmp/kd --port 8443 --leise   # Testserver
python koerperdaten/koerperdaten_server.py pruefen --daten /tmp/kd
```

Testserver gezielt über die PID beenden, nicht mit `pkill -f`, denn das trifft auch die eigene Shell:

```
ps -eo pid,args | awk '/python3 koerperdaten_server.py/ && !/awk/ {print $1}' | xargs -r kill
```

## Regeln, die nie gebrochen werden

- `slug: koerperdaten` in `config.yaml` nie ändern, sonst sieht Home Assistant eine neue App ohne Daten.
- **Datenbank:** Strukturänderungen nur als **neuer** Eintrag in `MIGRATIONS`. Bestehende Schritte nie ändern.
- **Datenformat:** Schlüssel (`key`) von Messgrößen nie umbenennen.
  - Neue Einstellungen kommen in den JSON-Block `<ansicht>` (in `toXml`/`fromXml`, `defaultSettings`, `mergeSettings`).
  - Ältere Stände stellt `migrate()` um: `SETTINGS_FORMAT` erhöhen, neuen `if (fv0 < N)`-Block anlegen.
- Kalorienbilanz = Aufnahme − Verbrauch, **negativ = Defizit**.
- Keine Daten, Datenbanken oder echten Messwerte ins Repository.
- Messwerte verlassen das Heimnetz nicht. Keine neuen externen Dienste. Erlaubt sind nur Chart.js vom CDN und Google Fonts, beide werden vom Service Worker zwischengespeichert.
- Die App ist nur fürs Heimnetz bzw. VPN gedacht. Keine Hinweise auf Portfreigaben.
- Anwendern nie raten, die App zu deinstallieren, bevor die Daten umgezogen sind. Deinstallieren löscht `/data`.
- Tracker bleibt eine HTML-Datei, Server eine Python-Datei. Kein Build-System, keine neuen Python-Abhängigkeiten.
- LF-Zeilenenden (`.gitattributes`), `run.sh` beginnt mit `#!/usr/bin/with-contenv bashio`.

## Umfang

- Weiterentwickelt wird **nur die Home-Assistant-App**.
- Die eigenständige Server-Variante (Raspberry Pi/Windows) und die reine XML-Variante sind auf Stand 2.4 eingefroren. Ihr Code (Speicherarten `browser`/`xml`, Start ohne HA) bleibt, neue Funktionen dürfen aber den Server voraussetzen.

## Stil

- **Oberfläche, Meldungen, Doku, Commit-Texte, Kommentare:** Deutsch, du-Form, kurz, alltagsnah, ohne Fachjargon. Fehlermeldungen sagen, was zu tun ist.
- **Bezeichner:** im Tracker englisch/kurz (`S.rows`, `S.settings`, `val`, `rate`), im Server oft deutsch (`benutzer`, `sicherungen`). Den Stil der Umgebung beibehalten.
- **Bewertungen und Empfehlungen** immer mit Quelle (WHO, DAG, NICE, AASM …) in `ref` bzw. `RECO_EXTRA`.
- **Designs:** `S.settings.design` (`modern` Standard, `classic`) gilt für alle Geräte, `cfg.theme` nur für das jeweilige Gerät. Neue Oberflächenteile in beiden Designs und in Hell und Dunkel prüfen.

## Testen, bevor etwas als fertig gilt

- `python -m unittest discover -s tests -v` muss grün sein. Neue Funktionen bekommen einen Test in `tests/test_server.py` bzw. `tests/test_tracker.py`.
- **Tracker-Tests** laufen mit Playwright (Chromium) gegen einen echten Server, am Handy (390×844) und am PC, hell und dunkel, in beiden Designs.
  - Danach die Screenshots in `tests/bilder/` ansehen.
  - Chart.js ist durch einen Platzhalter ersetzt. Das im Ergebnis erwähnen.
  - Funktionen des Trackers sind von außen nicht erreichbar, deshalb über Klicks bedienen. Zustände liefern `window.__sync` und `window.__fotos`.
- **Offline-Änderungen:** Fälle A–D aus `docs/offline.md` durchspielen.
- `python tools/pruefen.py` muss „In Ordnung“ melden.

## Git und Veröffentlichen

- Committen und pushen **nur, wenn der Nutzer es ausdrücklich verlangt**. Jeder Push auf `main` mit neuer Version erscheint in Home Assistant als Update.
- Sonst die geänderten Dateien bzw. ein Update-Paket (`update-X.Y.Z.zip` mit dem Ordner `koerperdaten/`) liefern. Der Nutzer kopiert es in sein Repository und veröffentlicht selbst.
- **Beim Push:**
  - auf `main`
  - Commit-Text „Version X.Y.Z: Kurzbeschreibung“
  - Tag `vX.Y.Z` (`git push --follow-tags`). Aus der Cloud-Umgebung von Claude wird das Pushen von Tags abgelehnt (HTTP 403). Dann nur `main` pushen und dem Nutzer den Befehl für den Tag geben: `git pull`, `git tag -a vX.Y.Z -m "Version X.Y.Z"`, `git push origin vX.Y.Z`.
  - danach prüfen, ob die GitHub Actions „Prüfen“ und „Images bauen“ grün sind
- Nach einem Push den Nutzer daran erinnern, vor eigenen Änderungen `git pull` auszuführen.
- In Anleitungen für den Nutzer Windows-Befehle verwenden (`python tools\release.py …`).

## Doku pflegen

- **Neue Funktion für Anwender** → `koerperdaten/DOCS.md` und `CHANGELOG.md`.
- **Technische Änderung** → passende Datei in `docs/`.
- **Neue oder geänderte Entscheidung** → neuer Eintrag in `docs/entscheidungen.md` (E38, E39 …). Alte Einträge bleiben stehen und bekommen einen Verweis.
- **Neue Version** → „Stand“ in `docs/README.md` sowie die Versionsangaben in dieser Datei anpassen.
