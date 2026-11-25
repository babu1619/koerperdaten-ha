# Dokumentation Körperdaten

Diese Dokumentation beschreibt den Körperdaten-Tracker als Ganzes: wie er aufgebaut ist, wie die Teile zusammenspielen und warum er so gebaut ist. Sie richtet sich an alle, die am Code arbeiten, also auch an Claude.

Die Bedienung für Anwender steht in der [App-Dokumentation](../koerperdaten/DOCS.md), die Home Assistant im Reiter „Dokumentation“ der App anzeigt. Was sich je Version geändert hat, steht im [Änderungsprotokoll](../koerperdaten/CHANGELOG.md).

**Stand:** Version 2.8.0 (November 2026)

| Dokument | Inhalt |
|---|---|
| [Architektur](architektur.md) | Bausteine, Datenfluss, Dateien im Repository, Abhängigkeiten |
| [Tracker](tracker.md) | die Weboberfläche `Koerperdaten.html`: Speicherarten, Ansichten, Diagramme, Formeln, Bewertungen, Fotos, Designs |
| [Server](server.md) | `koerperdaten_server.py`: Befehle, HTTP-Schnittstelle, Datenbank, Anmeldung, Zertifikate, Sicherungen |
| [Datenformate](datenformate.md) | XML-Format, Einstellungen, CSV-Import und -Export, Messgrößen |
| [Offline und Synchronisierung](offline.md) | Offline-Kopie, Abgleich mit dem Server, Konflikte |
| [Betrieb in Home Assistant](betrieb.md) | App-Konfiguration, Startskript, Updates, Umzug, Sicherung, Sicherheit |
| [Entwicklung](entwicklung.md) | Versionsnummern, Release-Ablauf, Prüfungen, Tests (`tests/`), Wunschliste, Konventionen |
| [Entscheidungen](entscheidungen.md) | alle wichtigen Entscheidungen mit Begründung, in zeitlicher Reihenfolge |

## Kurzüberblick

- **Was:** Tracker für Gewicht, Umfänge, Körperfett, Kalorien, Schlaf, Schritte, Ruhepuls und weitere Werte, mit Diagrammen, Zielen, Bewertungen nach anerkannten Empfehlungen und einem Fotovergleich.
- **Wie:** eine einzige HTML-Datei als Oberfläche und ein Python-Server mit SQLite, ausgeliefert als App für Home Assistant über dieses GitHub-Repository.
- **Für wen:** eine Familie im Heimnetz. Jede Person hat ein eigenes Konto und sieht nur ihre eigenen Daten.
- **Wo laufen die Daten:** nur auf dem Home-Assistant-Gerät (Home Assistant Green). Nichts liegt in einer Cloud oder im Repository.
