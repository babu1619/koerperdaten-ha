# Körperdaten-Apps für Home Assistant

Tracker für Gewicht, Umfänge, Körperfett, Kalorien und weitere Körper- und Fitnessdaten. Läuft als App auf Home Assistant, mit HTTPS, Benutzerkonten und SQLite. Jeder Benutzer sieht nur seine eigenen Daten.

[![Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbabu1619%2Fkoerperdaten-ha)

## Installation

1. In Home Assistant: Einstellungen → Apps → App Store → ⋮ → **Repositories**.
2. `https://github.com/babu1619/koerperdaten-ha` eintragen und **Hinzufügen**.
3. **Körperdaten-Server** auswählen, installieren und starten.
4. **Web-UI öffnen**, die Zertifikatswarnung einmal bestätigen und den ersten Benutzer anlegen.

Ausführliche Hinweise stehen in der [App-Dokumentation](koerperdaten/DOCS.md).

## Inhalt

| Pfad | Zweck |
|---|---|
| `repository.yaml` | macht den Ordner zu einem App-Repository für Home Assistant |
| `koerperdaten/` | die App: Konfiguration, Dockerfile, Startskript, Server, Tracker |
| `tools/release.py` | neue Version veröffentlichen |
| `tools/pruefen.py` | Prüfung vor dem Hochladen |
| `.github/workflows/` | automatische Prüfung und optional vorgebaute Images |

Die Daten der Benutzer liegen nur auf dem Home-Assistant-Gerät, nie im Repository.
