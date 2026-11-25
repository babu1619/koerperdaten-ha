# Tracker (`Koerperdaten.html`)

Der Tracker ist eine einzige Datei, rund 3.700 Zeilen. Es gibt keinen Build-Schritt und keine npm-Pakete, nur Chart.js aus dem CDN. So kann der Server die Datei direkt ausliefern, und man kann sie zum Testen auch lokal im Browser öffnen.

## Aufbau der Datei

| Bereich | Inhalt |
|---|---|
| `<head>` | Schrift (Manrope), Chart.js, das gesamte CSS mit Variablen für Hell/Dunkel und den Regeln für `html[data-design="modern"]` |
| `<body>` | Kopf mit Maßband-Logo und Speicherstatus, Reiter (`nav.tabs`), sechs Bereiche `#p-dash`, `#p-charts`, `#p-entry`, `#p-data`, `#p-fotos`, `#p-settings`, Dialoge (`<dialog>`) |
| Konstanten | `TRACKER_VERSION`, `SETTINGS_FORMAT`, `DEFAULT_METRICS`, `DERIVED`, `DEFAULT_CHARTS`, `DEFAULT_DASH`, `DEFAULT_HIDDEN`, WHO-Tabelle `WHO_BMI_LMS` |
| Zustand | `S.rows` (Datum → Werte), `S.settings`, `cfg` (Geräte-Einstellungen im `localStorage`) |
| Formel-Engine | `FX`: Parser und Auswertung der Formeln für berechnete Werte |
| Speicherung | Speicherarten, Server-API, Offline-Kopie, XML lesen und schreiben |
| Auswertung | `series`, `autoGran`, `movingAvg`, `slopePerDay`, `fitLine`, `rate`, `kidBmi`, `goalInfo`, `tileData` |
| Ansichten | `renderDash` (klassisch), `renderDashModern`, `renderCharts`, `renderEntryGrid`, `renderTable`, `renderFotos`, `renderSettings` |
| Start | `initStorage()` → `renderAll()` |

Für Tests gibt es zwei Hooks: `window.__fotos` und `window.__sync`. Sie legen interne Zustände offen, die als `let` sonst von außen nicht erreichbar sind.

## Speicherarten

`cfg.mode` legt fest, wo die Daten liegen. Wählbar unter Einstellungen → Speicherort.

| Modus | Ablage | Stand |
|---|---|---|
| `server` | Server über `/api/daten`, dazu die Offline-Kopie im `localStorage` | **aktiv weiterentwickelt** |
| `browser` | `localStorage`, Schlüssel `koerperdaten-v1` | eingefroren |
| `xml` | lokale XML-Datei über die File System Access API, Dateihandle in IndexedDB `koerperdaten-datei` (ersatzweise Download/Upload) | eingefroren |

In allen drei Modi wird dasselbe XML gespeichert (`toXml()` / `fromXml()`, siehe [Datenformate](datenformate.md)). Die Verzögerung nach `markDirty()` beträgt 300 ms im Modus `browser`, 600 ms bei `xml` und 800 ms bei `server`.

Der Speicherstatus (`srvState`) kennt die Zustände `none`, `connecting`, `setup` (noch kein Benutzer), `login`, `ok`, `offline` und `error`.

## Ansichten

| Reiter | Klassisch | Modern |
|---|---|---|
| Übersicht | Kacheln mit Wert, Trend, Bewertung, Mini-Verlauf; Ziele | „Heute“: Wochentage, Ziel-Ring, drei Kacheln, Kalorien-Ringe, Gewicht 8 Wochen, „Deine Woche“, weitere Werte |
| Diagramme | frei konfigurierbare Diagramme | „Verlauf“, gleiche Diagramme |
| Erfassen | Formular für einen Tag; ausgeblendete Messgrößen unter „Weitere Werte“ | großer „+“-Knopf in der unteren Leiste |
| Daten | Tabelle aller Tage mit Bearbeiten, CSV-Import und -Export | unter „Mehr“ |
| Fotos | Vorher/Nachher-Vergleich | eigener Reiter |
| Einstellungen | Darstellung, Speicherort, persönliche Angaben, Ziele, berechnete Werte, App-Installation, CSV-Format, Messgrößen, Zurücksetzen | unter „Mehr“ |

### Designs

- `S.settings.design` ist `modern` (Standard seit 2.7.0) oder `classic`. Der Wert wird mit den Daten gespeichert und gilt so auf allen Geräten.
- `cfg.theme` (Automatisch, Hell, Dunkel) gilt dagegen nur für das jeweilige Gerät.
- `applyLook()` setzt `data-design` und `data-theme` am `<html>`-Element. Das moderne Design ist reines CSS über der gemeinsamen Struktur. Nur die Übersicht hat einen eigenen Renderer (`renderDashModern`).
- Am Handy (schmale Ansicht) liegt die Navigation im modernen Design unten: Heute, Verlauf, „+“, Fotos, Mehr. Am PC liegt sie oben als Pill-Leiste, dort ohne „Mehr“.

### Moderne Übersicht

- **Wochentage:** Tippen wählt den Tag (`setDashDay`, Zustand `dashRef`). Alle Bereiche zeigen dann den Stand an diesem Tag, über `lastOn(k, ref)` und `goalAt`. Die Pfeile blättern wochenweise, „Zu heute“ springt zurück.
- **Anpassen:** `S.settings.mdash = {hide:[…], trio:[k1,k2,k3], week:[…]}`. Damit lassen sich Bereiche ausblenden, die drei Kacheln wählen und die Werte für „Deine Woche“ festlegen. Der Dialog dafür ist `openMDashDlg`.
- **Verlauf einer Kachel:** Doppelklick, langes Drücken (550 ms) oder Enter öffnet `#detailDlg` mit einem Diagramm (`attachDetail` → `openDetail`). Die Zeitraum-Voreinstellung merkt sich `cfg.detailPreset`.
- **Empfehlungen:** `recoFor(k)` liefert den Text für Tooltip und Detaildialog. Er setzt sich aus `rate().ref` und `RECO_EXTRA` zusammen.
- **Kalorien:** Ringe für Gegessen und Verbraucht. Darunter steht die Bilanz der letzten 7 und 30 Tage, zusammengezählt, mit dem theoretischen Fettverlust.

## Messgrößen und berechnete Werte

**Gespeicherte Messgrößen** (`DEFAULT_METRICS`): Gewicht, Taille, Bauch, Hüfte, Körperfett, BMI, Muskelmasse, Körperwasser, Viszeralfett, Brust, Oberarm, Körpergröße (`heightCm`), Oberschenkel, Ruhepuls, Schritte, Schlaf, Kalorienaufnahme, Aktivitätskalorien und gemessener Gesamtverbrauch. Unter Einstellungen → Messgrößen lassen sich eigene hinzufügen.

**Berechnete Werte** (`DERIVED`): WHR, WHtR, Grundumsatz, Kalorienverbrauch, Kalorienbilanz und theoretischer Fettverlust. Auch hier sind eigene möglich (`derivedCustom`).

Jede Messgröße hat:
- `key`, den internen Namen, der im XML als Attribut steht
- `v`, den Namen in Formeln
- `csv`, den Spaltennamen beim Export
- `alias`, weitere Spaltennamen für den Import
- `better` (−1 = sinkend günstig, 1 = steigend günstig, 0 = neutral)
- `dash`, die Art des Kachelwerts: `last`, `avg7` oder `sum30`
- optional `fx`, eine Formel

**Formeln** (`FX`) erlauben Zahlen, `+ - * / ^`, Klammern und Vergleiche. Dazu kommen deutsche Funktionen: `wenn(b;dann;sonst)`, `oder(a;b;…)`, `vorhanden(x)`, `min`, `max`, `abs`, `runden(x;n)` und `wurzel`. Kontextvariablen sind `groesse`, `alter`, `mann`, `frau` und `gewicht_letzt`. Die Standardformeln lassen sich bearbeiten (`S.settings.formulas`).

Wichtige Standardformeln:

| Wert | Formel | Hinweis |
|---|---|---|
| BMI | `gewicht / (groesse/100)^2` | nur an Tagen ohne eingetragenen BMI |
| Grundumsatz | `10*gewicht_letzt + 6.25*groesse - 5*alter + wenn(mann, 5, -161)` | Mifflin-St Jeor |
| Verbrauch | `oder(verbrauch_gemessen, aktivitaet + grundumsatz)` | ein gemessener Gesamtverbrauch hat Vorrang |
| Bilanz | `aufnahme - verbrauch` | **negativ = Defizit** |
| Fettverlust | `-bilanz / 7.7` | 7.700 kcal ≈ 1 kg Fett, positiv = abgebaut |

`groesse` ist die zuletzt gemessene Körpergröße (`heightOn(d)`), sonst der Wert aus den Einstellungen. `val(d, k)` liefert einen gespeicherten oder berechneten Wert mit Zwischenspeicher. `invalidate()` leert diesen Zwischenspeicher bei jeder Änderung.

## Auswertung

- **Zeitraum und Körnung:** Die Voreinstellungen reichen von 1 Woche bis Alles. Daneben gibt es einen freien Zeitraum. `autoGran` wählt die Körnung: bis 62 Tage Tageswerte, bis 280 Tage Wochenmittel, bis 1.100 Tage Monatsmittel, darüber Quartalsmittel. Von Hand lässt sich auch Jahr einstellen.
- **Trend:** `slopePerDay` und `fitLine` arbeiten mit linearer Regression über die letzten 30 Tage, ersatzweise über 90 Tage.
- **Diagramme:** `chartCard` zeichnet Liniendiagramme, wahlweise geglättet, mit Körperfett-Bändern und Ziellinien. `energyCard` zeigt Verbrauch und Aufnahme als Balken hintereinander, darunter die Bilanz. Die Farben sind wählbar.
- **Ziele:** `goalInfo` berechnet Fortschritt, Restwert und eine Prognose aus dem Trend. Mögliche Zustände sind `reached`, `nodata`, `away` (der Trend läuft vom Ziel weg) und eine Prognose mit Datum.

### Bewertungen (`rate`)

Jede Bewertung hat eine Stufe (`good`, `ok`, `warn`, `bad`, `info`), einen kurzen Text und eine Quelle (`ref`). Grundlagen:

| Wert | Grundlage |
|---|---|
| Gewichtstrend | DAG: 0,5–1 % pro Woche beim Abnehmen |
| BMI Erwachsene | WHO-Klassen 18,5 / 25 / 30 / 35 / 40 |
| BMI unter 19 Jahren | WHO-Perzentile über LMS (siehe unten) |
| Taille, Bauch | WHO/IDF: Männer 94/102 cm, Frauen 80/88 cm |
| WHR | WHO, getrennt nach Geschlecht |
| WHtR | NICE 2022: 0,40–0,49 gesund, ab 0,50 erhöht, ab 0,60 hoch |
| Körperfett | Gallagher 2000, nach Geschlecht und Altersgruppe (`bfBands`) |
| Viszeralfett | Tanita-Skala: Stufe 1–12 gesund |
| Körperwasser | übliche Bereiche nach Geschlecht |
| Ruhepuls | Erwachsene 60–100 bpm |
| Schlaf | AASM: 7–9 Stunden |
| Schritte | ab etwa 7.000 am Tag (Metaanalysen) |
| Bewegung (Tooltip) | WHO: 150–300 Minuten mäßig pro Woche, Krafttraining an 2 Tagen |

### BMI für Kinder und Jugendliche

- Unter 228 Monaten (19 Jahre) bewertet `kidBmi` den BMI nach WHO-Perzentilen statt nach den Grenzen für Erwachsene.
- **24–59 Monate:** WHO Child Growth Standards. Übergewichtsrisiko ab +1 SD, Übergewicht ab +2 SD, Adipositas ab +3 SD.
- **60–227 Monate:** WHO-Referenz 2007. Übergewicht ab +1 SD, Adipositas ab +2 SD.
- Untergewicht liegt unter −2 SD, starkes Untergewicht unter −3 SD.
- Unter 2 Jahren gibt es keine Bewertung, nur einen Hinweis auf die Kinderärztin oder den Kinderarzt.
- `WHO_BMI_LMS` enthält je Geschlecht L, M, S für jeden Monat ab 24. Quelle sind die WHO-Repositorys `anthro` und `anthroplus` (`data-raw/growthstandards`). Daraus berechnet der Tracker z = ((BMI/M)^L − 1)/(L·S) und das Perzentil über die Normalverteilung.
- Die Körpergröße wächst mit. Deshalb gibt es die Messgröße `heightCm`, und Formeln rechnen mit der zuletzt gemessenen Größe.

## Import und Export

- **CSV-Import** (`planImport` → Dialog „Import prüfen“ → `applyImport`):
  - Die Datumsspalte wird am Namen erkannt, sonst am Inhalt. Datum mit Uhrzeit wie im WeightFit-Export wird auf den Tag gekürzt.
  - Spalten werden über `csv`, `key`, `label` und `alias` den Messgrößen zugeordnet, Einheiten in Klammern werden ignoriert. Unbekannte Spalten können als neue Messgröße angelegt werden.
  - Die Vorschau zeigt neue, ergänzte und abweichende Tage. Bei Abweichungen wählt man zwischen Behalten und Überschreiben.
  - Erprobte Quellen: WeightFit, Garmin Connect, FatSecret.
- **CSV-Export** (`exportCSV`): Trennzeichen `;` oder `,`, berechnete Spalten wahlweise dabei.
- **XML:** „XML öffnen“ und „XML speichern“ gibt es nur im Modus `xml`. Mit Server sichern die Sicherungen je Benutzer und die Datenbanksicherungen.

## Fotos

- Der Reiter ist nur mit Server verfügbar (`/api/status` meldet `fotos: true`).
- Je Termin (Datum) gibt es drei Ansichten: `vorne`, `seite` und `hinten`.
- Vergleich zweier Termine nebeneinander, mit Schieberegler oder überblendet, dazu die Veränderung von Gewicht, Taille und Körperfett.
- **Hinzufügen:** über Kamera, Galerie oder Datei, am PC auch per Ziehen. Das Aufnahmedatum kommt aus den EXIF-Daten (`exifDate`).
- **Zuschnitt** (`feEditor`): fest 3:4, mit Verschieben, Zoom, Ausrichten, Drehen und Spiegeln. „Umriss letzte Aufnahme“ blendet das vorige Foto halb durchsichtig ein.
- **Gespeichert werden:**
  - Zuschnitt 900×1200 als JPEG
  - Vorschau 240×320
  - Original, auf höchstens 2.400 px verkleinert
  - Zuschnitt-Parameter als JSON

  Durch das Neuzeichnen im Canvas fallen alle Metadaten weg, auch der Aufnahmeort.
- Das Original bleibt erhalten, deshalb lässt sich der Zuschnitt später neu setzen.

## Als App (PWA)

Der Server liefert `manifest.webmanifest`, Icons und `sw.js`. Installierbar ist die App nur über HTTPS mit einem vertrauenswürdigen Zertifikat. Dafür gibt es das Stammzertifikat unter Einstellungen → Als App installieren (`downloadCa()`: per `fetch` als Blob-Download, dazu eine Teilen-Möglichkeit für Android). Der Service Worker ist im [Server](server.md#service-worker) beschrieben.
