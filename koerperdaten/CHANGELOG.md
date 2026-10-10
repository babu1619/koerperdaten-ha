# Änderungen

## 2.5.0

- Fotos: Vorher/Nachher-Vergleich mit Zuschnitt

## 2.4.0

- Neuer Reiter **Fotos**: Vorher/Nachher-Vergleich von vorne, von der Seite und von hinten – nebeneinander, mit Schieberegler oder überblendet, mit Veränderung von Gewicht, Taille und Körperfett
- Fotos per Kamera, aus der Galerie oder von der Festplatte (auch per Ziehen) hinzufügen; Aufnahmedatum aus dem Foto
- Zuschneiden im Format 3:4 mit Zoom, Ausrichten, Drehen, Spiegeln und eingeblendetem Umriss der letzten Aufnahme; Original bleibt erhalten, Zuschnitt später änderbar
- Fotos liegen nur auf dem Server in `fotos.db`, nur für das eigene Konto; Metadaten wie der Aufnahmeort werden entfernt
- `sichern`, `pruefen` und der Umzug berücksichtigen die Fotos

## 2.3.1

- CA-Zertifikat lässt sich auf Android zuverlässig herunterladen; zusätzlich im Ordner `share`

## 2.3.0

- Aktivitätskalorien erfassen; Kalorienverbrauch = Aktivitätskalorien + Grundumsatz (ein gemessener Gesamtverbrauch hat Vorrang)
- **Kalorienbilanz mit neuem Vorzeichen:** negativ = Defizit (mehr verbraucht als gegessen). Auch die CSV-Spalte `kalorienbilanz_kcal` dreht ihr Vorzeichen
- Neues Kalorien-Diagramm: Verbrauch und Aufnahme als Balken hintereinander, darunter die Bilanz
- Farben der Diagramme wählbar
- Ziele je Messgröße mit Fortschritt, Restwert und Prognose aus dem Trend
- Zeitraum „1 Woche“ bei den Diagrammen
- Erfassen: nicht angehakte Messgrößen liegen im einklappbaren Bereich „Weitere Werte“
- Formeln der berechneten Werte bearbeitbar, eigene berechnete Werte möglich
- Als App installierbar (PWA, Chrome auf Android und am PC)
- Eigene kleine Zertifizierungsstelle: Stammzertifikat unter `/ca.crt`, einmal auf dem Handy installieren. Nach dem Update erscheint die Zertifikatswarnung einmal neu, solange das Stammzertifikat nicht installiert ist
- Bestehende Daten und Einstellungen werden automatisch übernommen

## 2.2.0

- Auslieferung über GitHub als App-Repository
- Umzug zwischen zwei Installationen über die Option „Umzugsdaten bereitstellen“
- Import mit Vorschau: neue, ergänzte und abweichende Tage; Wahl zwischen Behalten und Überschreiben
- Kopfbereich mit Tabs bleibt beim Scrollen stehen
- Datenansicht: Kopfzeile sowie Bearbeiten-Knopf und Datum bleiben beim Scrollen stehen
- Nach dem Speichern oder Löschen eines Eintrags öffnet sich die Übersicht

## 2.1.0

- Datenbank mit Versionsnummer und automatischer Sicherung vor jedem Update
- Befehle `sichern`, `pruefen` und `wiederherstellen`
- Datenbank wird beim Stoppen sauber geschlossen

## 2.0.0

- HTTPS, Benutzerkonten und SQLite
