# Entwicklung

## Versionsnummern

Die Version steht an drei Stellen und muss zusammenpassen:

| Datei | Feld | Form | Beispiel |
|---|---|---|---|
| `koerperdaten/config.yaml` | `version` | `X.Y.Z` in Anführungszeichen | `"2.8.0"` |
| `koerperdaten/koerperdaten_server.py` | `VERSION` | `X.Y` | `"2.8"` |
| `koerperdaten/Koerperdaten.html` | `TRACKER_VERSION` | `X.Y` | `'2.8'` |

- **Patch** (2.8.0 → 2.8.1): Fehlerbehebung, kleine Anpassung.
- **Minor** (2.8.x → 2.9.0): neue Funktion.
- **Major**: nur bei einem grundlegenden Umbau.

`pruefen.py` verlangt, dass `config.yaml` mit `VERSION + "."` beginnt. Eine neue Server-Version ändert den Cache-Namen des Service Workers. Deshalb laden alle Geräte die Seite neu, sobald sich die Haupt- oder Nebenversion ändert.

**Versionsgeschichte auf GitHub:** 2.2.0 → 2.3.0 → 2.3.1 → 2.5.0 (Fotos) → 2.7.0 (Offline und modernes Design) → 2.7.1 → 2.8.0. Die Nummern 2.4.0 und 2.6.0 wurden nur als Update-Pakete gebaut und so nie veröffentlicht. Maßgeblich ist immer der Stand auf GitHub. Die nächste Version ist **2.8.1** (Korrektur) oder **2.9.0** (Funktion).

Wer die Versionsnummer setzt, prüft deshalb vorher den Stand im Repository (`git pull`, `config.yaml`) und baut nicht auf einer Nummer aus dem Gedächtnis auf.

## Release-Ablauf

**Mit `release.py`** (wenn die Version noch nicht erhöht ist):

```
python tools/release.py patch "Kurzbeschreibung"     # oder minor / major / 2.9.0
python tools/release.py minor "Text" --ohne-push      # nur lokal committen
```

Das Skript erledigt der Reihe nach:
1. Version in allen drei Dateien erhöhen
2. Eintrag oben in `CHANGELOG.md` anlegen
3. `pruefen.py` aufrufen
4. `git add -A`, Commit „Version X.Y.Z: Text“, Tag `vX.Y.Z`
5. `git push --follow-tags`

**Von Hand** (wenn die Version in den Dateien schon erhöht ist, etwa durch Claude):

```
python tools/pruefen.py
git add -A
git commit -m "Version 2.9.0: …"
git tag -a v2.9.0 -m "Version 2.9.0"
git push --follow-tags
```

Beim Changelog-Eintrag für Anwender gilt: kurze Stichpunkte in Alltagssprache, die Wirkung beschreiben statt der Technik. Home Assistant zeigt den Text beim Update an.

**Nur Dokumentation:** Änderungen außerhalb von `koerperdaten/`, etwa in `docs/`, `README.md` oder `CLAUDE.md`, brauchen keine neue Version. Home Assistant zeigt dafür kein Update an.

## Prüfungen

**`tools/pruefen.py`** (lokal und in CI):

**Fehler** (Abbruch):
- `repository.yaml` und `config.yaml` sind kein gültiges YAML oder es fehlen Pflichtfelder.
- Die Version steht nicht in Anführungszeichen oder hat nicht die Form `X.Y.Z`.
- Der Slug ist nicht `koerperdaten`.
- Unbekannte Architektur.
- Optionen, Schema und Übersetzungen passen nicht zusammen.
- `image` ohne `{arch}`.
- Eine Pflichtdatei in `koerperdaten/` fehlt.
- `run.sh` hat CRLF-Zeilenenden oder eine falsche erste Zeile.
- Der Server lässt sich nicht kompilieren.

**Hinweise** (kein Abbruch):
- Die Server-Version passt nicht zu `config.yaml`.
- `CHANGELOG.md` hat keinen Abschnitt für die Version.
- Der Platzhalter `GITHUB-BENUTZER` steht noch in einer Datei.

`TRACKER_VERSION` wird nicht geprüft. Sie muss von Hand oder über `release.py` mitgezogen werden.

**GitHub Actions `Prüfen`** (bei jedem Push):
1. `pruefen.py`
2. Server-Probelauf: starten, Status abfragen, Seite laden, Einrichtung, Daten speichern und lesen, `pruefen`
3. Docker-Image bauen wie in Home Assistant (amd64) und darin `--version`, `cryptography` und `sqlite3` prüfen
4. Warnung, wenn sich `koerperdaten/` geändert hat, die Version aber nicht

## Tests

Ein Test-Ordner liegt nicht im Repository. Neue Funktionen wurden bisher so geprüft:

- **Server:** echten Server mit einem Datenordner unter `/tmp` starten und die API mit `urllib` oder `curl` aufrufen, z. B. für Konflikte (412), Fotos, Umzug und Migrationen.
- **Tracker:** Playwright (Chromium) gegen den laufenden Server, mit Handy-Ansicht (390×844) und Desktop sowie hell und dunkel.
  - Chart.js ist dabei durch einen Platzhalter ersetzt, weil die Testumgebung das CDN nicht erreicht. Das echte Zeichnen der Diagramme ist so nicht abgedeckt.
  - Zugriff auf interne Zustände über `window.__sync` und `window.__fotos`.
  - Screenshots ansehen, bevor eine Änderung als fertig gilt.
- **Offline:** Netz in Playwright abschalten (`context.set_offline`), Fälle A–D aus [Offline und Synchronisierung](offline.md).
- **Kinder-BMI:** Stichproben gegen WHO-Tabellen, z. B. Junge 10 Jahre mit BMI 16,44 → P50.

Laufende Testserver beendest du gezielt über die PID:

```
ps -eo pid,args | awk '/python3 koerperdaten_server.py/ && !/awk/ {print $1}' | xargs -r kill
```

`pkill -f` mit einem Muster, das auch zur eigenen Shell passt, beendet sonst die Shell mit.

## Wunschliste

Wünsche sammelt der Nutzer in einer Claude-Artefakt-Seite: **Körperdaten Wunschliste**, https://claude.ai/artifact/PYEwe1hyUqF7hw35KdJxx6. Sie hat eine eigene Datensammlung `wuensche` und ist eine Kopie der Wunschliste des Finanz-Trackers.

Ablauf: Wünsche umsetzen, danach in der Liste auf „umgesetzt“ setzen, die Version eintragen und die Versionsangabe im Kopf der Liste anpassen.

## Konventionen

- **Sprache:** Oberfläche, Meldungen, Doku, Commit-Texte und Code-Kommentare auf Deutsch. Bezeichner im Tracker-JavaScript sind englisch oder abgekürzt (`rows`, `settings`, `val`), im Server oft deutsch (`benutzer`, `sicherungen`). Den vorhandenen Stil der Umgebung beibehalten.
- **Ton der Oberfläche:** du-Form, kurz, ohne Fachjargon. Fehlermeldungen sagen, was zu tun ist.
- **Eine Datei je Teil:** Der Tracker bleibt eine einzige HTML-Datei, der Server eine einzige Python-Datei. Keine Build-Werkzeuge, keine zusätzlichen Python-Pakete außer `cryptography`.
- **Datenformat:** Schlüssel von Messgrößen nie umbenennen. Neue Einstellungen kommen in den JSON-Block `<ansicht>`. Ältere Stände stellt `migrate()` um, gesteuert über `SETTINGS_FORMAT`.
- **Datenbank:** neue Struktur nur über einen neuen Eintrag in `MIGRATIONS`.
- **Zeilenenden:** LF (`.gitattributes`); `run.sh` muss ausführbar bleiben.
- **Keine Daten ins Repository**, auch keine Beispiel-Datenbanken mit echten Werten.
- **Datenschutz:** Messwerte verlassen das Heimnetz nicht. Kein Tracking, keine externen Dienste außer CDN und Schriften für die Oberfläche.
