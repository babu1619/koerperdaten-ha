# Entscheidungen

Hier steht, was festgelegt wurde und warum, in zeitlicher Reihenfolge. Neue Entscheidungen kommen unten dazu. Wird eine frühere Entscheidung geändert, bleibt der alte Eintrag stehen und bekommt einen Verweis auf den neuen.

Aufbau je Eintrag: **Entscheidung**, *Begründung*, Folgen.

---

## Grundlagen (Version 1, Oktober 2026)

### E1 – Eigener Tracker statt fertiger App
**Ein eigenes Programm für Gewicht, Umfänge, Körperfett, BMI und weitere Werte, mit CSV-Import und -Export, konfigurierbaren Diagrammen, frei wählbarem Zeitraum und zeitraumabhängigen Mittelwerten.**
*Die Daten kommen aus mehreren Apps (WeightFit zum Wiegen, Garmin Connect, FatSecret). Keine davon führt alles zusammen, und die Daten sollen beim Nutzer bleiben.*
Der Import arbeitet tolerant mit Aliasnamen für Spalten und Datumsformaten, statt nur ein festes Format zu kennen.

### E2 – Oberfläche als eine einzige HTML-Datei
**Der Tracker ist eine einzelne HTML-Datei ohne Build-Schritt. Einzige Fremdbibliothek ist Chart.js aus dem CDN.**
*Lässt sich ohne Installation öffnen, kopieren und ausliefern; der Server muss nur eine Datei bereitstellen. Kein Werkzeugwechsel für den Nutzer, Änderungen sind als eine Datei leicht zu übergeben.*
Die Datei ist groß (rund 3.700 Zeilen). Struktur und Kommentare im Code sind deshalb wichtig.

### E3 – Daten als XML-Datensatz, nicht im Browser
**Die Daten liegen in einem XML-Dokument. Ursprünglich war das eine eigene Datei mit frei wählbarem Speicherort (Wunsch: nicht im Browser).**
*Lesbar, ohne Programm zu sichern und unabhängig vom Browser. Einstellungen und Daten bleiben in einem Stück zusammen.*
Das XML blieb das Austauschformat für alle Speicherarten, auch für den Server (siehe E5).

### E4 – Zentraler Server mit Benutzerkonten
**Ein Python-Server speichert die Daten zentral, mit HTTPS, Benutzerkonten und SQLite (Version 2.0). Jede Person sieht nur ihre eigenen Daten.**
*Handy und PC sollen dieselben Daten sehen, die Familie soll mitmachen können. Eine Datei auf einem Gerät reicht dafür nicht.*
Der Server läuft nur mit der Standardbibliothek (plus `cryptography`). Damit läuft er auf dem Raspberry Pi, auf Windows und in Home Assistant ohne weitere Pakete.

### E5 – Server speichert je Benutzer das ganze XML
**Die Datenbank speichert pro Benutzer ein XML-Dokument als BLOB mit ETag. Messwerte stehen nicht als einzelne Tabellenzeilen.**
*Tracker und Server bleiben entkoppelt, denn die ganze Fachlogik liegt im Tracker. Neue Messgrößen oder Einstellungen brauchen keine Datenbankänderung. Sicherungen sind einfach vollständige frühere Stände.*
Der Server kennt die einzelnen Werte nicht und kann keine Auswertungen über Benutzer hinweg machen. Gleichzeitiges Schreiben regelt ein ETag mit `If-Match` (E19).

### E6 – Sicherheit: nur Heimnetz, Zugriff von außen per VPN
**Die App ist nicht fürs Internet gedacht. Es gibt keine Portfreigabe, von unterwegs geht der Zugriff über VPN (WireGuard/FRITZ!Box oder Tailscale).**
*Es handelt sich um Gesundheitsdaten. Ein selbst betriebener Server ohne laufende Sicherheitspflege gehört nicht offen ins Netz.*
Trotzdem gibt es HTTPS, gehashte Passwörter (scrypt), gehashte Tokens und eine Bremse gegen Passwort-Raten.

## Home Assistant (2.1 – 2.2)

### E7 – Betrieb auf dem Home Assistant Green
**Der Server läuft als App (Add-on) auf dem Home Assistant Green, nicht auf einem zusätzlichen Rechner.**
*Das Gerät läuft ohnehin rund um die Uhr, wird gesichert und hat den Supervisor für Updates.*
Die App öffnet einen eigenen HTTPS-Port (8443) und ist nicht in die Seitenleiste eingebettet. Ingress hätte die eigene Anmeldung, die PWA und das eigene Zertifikat erschwert.

### E8 – Sichere Updates der Datenbank
**Die Datenbank hat eine Versionsnummer (`PRAGMA user_version`) und nummerierte, unveränderliche Umbauschritte. Vor jedem Versionswechsel sichert der Server nach `/data/vor-update/`, die letzten 10 Kopien bleiben. Schlägt ein Umbau fehl, startet er nicht (2.1).**
*Ein Update darf nie Daten kosten. Ein Fehler soll sichtbar werden, statt halb umgebaute Daten zu hinterlassen.*
Dazu kommen die Befehle `sichern`, `pruefen` und `wiederherstellen`, außerdem `backup: cold` für Home-Assistant-Sicherungen.

### E9 – Auslieferung über ein GitHub-App-Repository
**Statt die App per Samba als lokale App zu kopieren, kommt sie aus dem Repository `babu1619/koerperdaten-ha` (2.2.0).**
*Updates mit einem Klick im App Store, die Versionsgeschichte steht in Git, und GitHub Actions prüft vor jedem Update.*
Home Assistant sieht die GitHub-App als **neue** App mit eigenem `/data`. Deshalb gibt es den Umzug (E10). Der Slug `koerperdaten` darf sich nie ändern.

### E10 – Umzug über `share`, nie überschreiben
**Die Option „Umzugsdaten bereitstellen“ legt eine geprüfte Kopie in `/share/koerperdaten/umzug` ab. Die neue App übernimmt sie nur, wenn sie selbst noch keine Benutzer hat.**
*Kein Verlust, egal in welcher Reihenfolge der Nutzer vorgeht. Die alte App bleibt lauffähig, bis die Übernahme geprüft ist.*
Grundregel für Anwender: **nie deinstallieren, bevor die Daten umgezogen sind**, denn Deinstallieren löscht `/data`. Ohne die Option (alte Version) geht der Umzug von Hand über den Tracker mit einer zweiten Instanz auf einem anderen Port.

### E11 – Release-Werkzeuge
**`tools/release.py` erhöht die Version an allen Stellen, ergänzt den Changelog, prüft, committet, taggt und pusht. `tools/pruefen.py` läuft lokal und in GitHub Actions.**
*Die häufigste Fehlerquelle war ein Update, das Home Assistant nicht anzeigte (Version nicht erhöht oder nicht in Anführungszeichen).*
Das Schema wurde später für den Finanz-Tracker übernommen.

### E12 – Import mit Vorschau
**Ein CSV-Import zeigt vorher neue, ergänzte und abweichende Tage. Bei Abweichungen wählt man zwischen Behalten und Überschreiben (2.2.0).**
*Exporte aus verschiedenen Apps überlappen sich; nichts soll unbemerkt überschrieben werden.*

## Kalorien, Ziele, App-Installation (2.3)

### E13 – Kalorienbilanz: negativ ist Defizit
**Die Bilanz ist `Aufnahme − Verbrauch`. Ein negativer Wert bedeutet ein Defizit (2.3.0).**
*So rechnen die meisten Apps, und so ist es intuitiv: weniger gegessen als verbraucht ergibt einen negativen Wert.*
Das Vorzeichen der CSV-Spalte `kalorienbilanz_kcal` hat sich geändert, im Changelog steht ein Hinweis.

### E14 – Verbrauch = Aktivitätskalorien + Grundumsatz
**Aktivitätskalorien werden getrennt erfasst. Der Gesamtverbrauch errechnet sich aus Aktivität plus Grundumsatz (Mifflin-St Jeor, mit dem zuletzt gemessenen Gewicht). Ein gemessener Gesamtverbrauch hat Vorrang (2.3.0).**
*Garmin liefert Aktivitätskalorien, andere Quellen den Gesamtverbrauch. Beides soll richtig zusammenpassen.*
Die alte Einstellung `burnMode=active` wird beim Laden umgestellt (`migrate`, `fv < 3`).

### E15 – Formeln bearbeitbar, mit deutschen Funktionsnamen
**Berechnete Werte beruhen auf Formeln, die der Nutzer ändern und um eigene ergänzen kann (`wenn`, `oder`, `vorhanden`, `runden` …).**
*Der Nutzer ist technisch versiert und möchte Berechnungen nachvollziehen und anpassen, ohne den Code zu ändern.*

### E16 – Ziele mit Prognose
**Ziele je Messgröße zeigen Fortschritt, Restwert und eine Prognose aus dem Trend (lineare Regression über 30, ersatzweise 90 Tage).**

### E17 – Eigene Zertifizierungsstelle statt selbst signiertem Zertifikat
**Der Server erzeugt eine eigene CA (10 Jahre) und stellt damit das Server-Zertifikat aus (397 Tage, automatisch erneuert). Das Stammzertifikat steht unter `/ca.crt` (2.3.0).**
*Chrome installiert eine PWA nur mit einem vertrauenswürdigen Zertifikat. Ein einmal installiertes Stammzertifikat überdauert neue Server-Zertifikate, IP-Wechsel und Umzüge.*
Nach dem Update von 2.2 erscheint die Browser-Warnung einmal neu. Der private Schlüssel der CA verlässt `/data` nicht.

### E18 – Stammzertifikat als neutraler Download (2.3.1)
**`/ca.crt` wird als DER mit `application/octet-stream` ausgeliefert. Der Tracker lädt es per `fetch` als Blob herunter, bietet es zum Teilen an und legt es zusätzlich in `share` ab.**
*Android hat den Download mit dem MIME-Typ `x-x509-ca-cert` direkt an den Installer gegeben und dort abgewiesen, statt ihn zu speichern.*

### E19 – Gleichzeitige Änderungen über ETag
**Gespeichert wird immer mit `If-Match`. Bei 412 zeigt der Tracker einen Konfliktdialog (2.0), seit 2.7.0 arbeitet er vorher tageweise ein (E24).**
*Handy und PC können gleichzeitig offen sein, ohne dass ein Gerät die Änderungen des anderen still überschreibt.*

## Fotos (2.5.0, intern 2.4)

### E20 – Vorher/Nachher-Fotos in eigener Datenbank
**Fotos liegen in `fotos.db` neben der Hauptdatenbank, nicht im XML und nicht in `koerperdaten.db`.**
*Bilder sind groß. Im XML würden sie jede Sicherung je Benutzer (bis zu 100 Stände) und jeden Speichervorgang aufblähen.*
Die Fotos sind nur mit Server verfügbar und nicht im CSV-Export. `sichern` und der Umzug berücksichtigen `fotos.db`.

### E21 – Drei Ansichten, festes Format, Original bleibt
**Je Termin gibt es die Ansichten vorne, Seite und hinten, alle im Format 3:4 (900×1200). Gespeichert werden Zuschnitt, Vorschau und das Original (höchstens 2.400 px) mit den Zuschnitt-Parametern.**
*Gleiche Formate machen den Vergleich sauber. Mit dem Original lässt sich der Zuschnitt später korrigieren. Der „Umriss der letzten Aufnahme“ hilft bei gleicher Haltung und gleichem Abstand.*

### E22 – Metadaten werden entfernt
**Bilder werden im Browser per Canvas neu gerechnet. Dabei fallen EXIF-Daten wie der Aufnahmeort weg. Das Aufnahmedatum wird vorher ausgelesen.**
*Datenschutz: Körperfotos mit GPS-Daten sollen nicht entstehen.*

### E23 – Hinzufügen aus Kamera, Galerie oder Datei
**Neben der Kamera gibt es das Hochladen vom Handy und von der Festplatte, am PC auch per Ziehen. Zuschneiden folgt direkt nach dem Hinzufügen.**
*Wunsch nach dem ersten Mockup.*
Der Ablauf wurde vorher als Mockup abgestimmt (Design-Canvas „Körperdaten – Fotos vorher/nachher“).

## Offline und neues Design (2.7.0, intern 2.5 und 2.6)

### E24 – Offline-fähige PWA mit tageweisem Abgleich
**Auf dem Gerät liegen immer die letzten 31 Tage samt Einstellungen und Basis-Stand. Änderungen werden sofort lokal gesichert und beim nächsten Kontakt tageweise in den aktuellen Server-Stand eingearbeitet. Bei Konflikten am selben Tag gilt die zuletzt übertragene Version.**
*Unterwegs ohne VPN soll Eintragen möglich sein. Ein Monat deckt das ab und hält den `localStorage` klein. Ein feineres Zusammenführen (je Wert) wäre aufwendiger, ohne praktischen Nutzen, weil jede Person nur ihre eigenen Daten pflegt.*
Einzelheiten unter [Offline und Synchronisierung](offline.md). Beim Abmelden wird die Kopie gelöscht.

### E25 – Nur noch die Home-Assistant-App wird weiterentwickelt
**Der eigenständige Server (Raspberry Pi/Windows) und die reine XML-Variante bleiben auf Stand 2.4.**
*Der Nutzer betreibt nur noch die Home-Assistant-App. Drei Varianten zu pflegen kostet Zeit, ohne Nutzen.*
Neue Funktionen dürfen den Server voraussetzen. Der alte Code wird nicht entfernt, muss aber auch nicht weiter funktionieren.

### E26 – Modernes Design als Standard, klassisches umschaltbar
**Neue Startseite „Heute“, weniger technisch:**
- **Basis:** Entwurf A
- **aus Entwurf B:** Kalorien-Ringe und „Deine Woche“
- **aus Entwurf C:** die Wochentage oben
- **Navigation:** am Handy unten mit großem „+“, am PC oben

**Das Design ist in den Einstellungen umschaltbar (Modern/Klassisch), Standard ist Modern.**
*Wunsch: moderner und weniger technisch. Aus drei Entwürfen hat der Nutzer diese Kombination gewählt (Design-Canvas „Körperdaten – Dashboard neu“).*
Das Design wird mit den Daten gespeichert und gilt so auf allen Geräten. Die Farben (Hell/Dunkel/Automatisch) gelten je Gerät.

### E27 – Versionsnummern: GitHub ist maßgeblich
**Weil die Update-Pakete 2.4.0 und 2.6.0 nie so veröffentlicht wurden, ist der Stand auf GitHub maßgeblich: Fotos erschienen als 2.5.0, Offline und Design als 2.7.0. Weitergezählt wird ab dem letzten veröffentlichten Stand.**
*Es war zu einer Verwechslung der Nummern gekommen; der Nutzer hat 2.7.0 als gültigen Stand festgelegt.*
Vor jeder neuen Version die Nummer in `config.yaml` auf GitHub prüfen.

## Übersicht und Auswertung (2.7.1 – 2.8.0)

### E28 – Wochentage zeigen die Übersicht des Tages (2.7.1)
**Ein Tipp auf einen Wochentag zeigt die Übersicht mit dem Stand an diesem Tag, nicht mehr das Erfassen-Formular. Pfeile blättern wochenweise, „Zu heute“ führt zurück.**
*So hatte es der Nutzer erwartet. Eintragen geht über den „+“-Knopf.*

### E29 – Wunschliste als Arbeitsliste
**Wünsche sammelt der Nutzer in einer Artefakt-Seite mit eigener Datensammlung, übernommen vom Finanz-Tracker. Umgesetzte Punkte bekommen den Status „umgesetzt“ und die Version.**
*Ein Ort für Ideen zwischen den Sitzungen. Umgesetzt werden mehrere Punkte zusammen in einer Version.*

### E30 – Anpassbare moderne Übersicht (2.8.0)
**Bereiche ein- und ausblenden, die drei Kacheln wählen, die Werte für „Deine Woche“ wählen. Gespeichert wird das in `S.settings.mdash`.**
*Nicht jede Person erfasst dieselben Werte, etwa Kinder ohne Kalorien.*

### E31 – Verlauf per Doppelklick oder langem Drücken (2.8.0)
**Jede Kachel öffnet ihren Verlauf als Diagramm in einem Dialog. Der Zeitraum ist wählbar und wird je Gerät gemerkt.**
*Einfaches Tippen bleibt frei fürs Scrollen und Auswählen. Langes Drücken ist am Handy die übliche Geste, Doppelklick am PC. Enter macht die Funktion auch per Tastatur erreichbar.*

### E32 – Empfehlungen mit Quelle (2.8.0)
**Tooltips und Detaildialog zeigen die Empfehlung samt Quelle (WHO, DAG, NICE, AASM, Gallagher, Tanita).**
*Bewertungen sollen nachvollziehbar sein und keine Ratschläge ohne Grundlage geben.*

### E33 – Summierte Kalorienbilanz (2.8.0)
**Unter „Gegessen & verbraucht“ steht die Bilanz der letzten 7 und 30 Tage, zusammengezählt, mit dem theoretischen Fettverlust (7.700 kcal ≈ 1 kg).**
*Ein einzelner Tag sagt wenig, die Summe zeigt die Richtung.*

### E34 – BMI für Kinder nach WHO-Perzentilen (2.8.0)
**Unter 19 Jahren bewertet der Tracker den BMI nach den WHO-LMS-Daten:**
- 2–5 Jahre: Child Growth Standards
- 5–19 Jahre: WHO-Referenz 2007
- unter 2 Jahren: keine Bewertung

**Die Tabellen sind in den Tracker eingebettet. Neu ist die Messgröße Körpergröße (`heightCm`), Formeln rechnen mit der zuletzt gemessenen Größe.**
*Die Kinder der Familie sollen eigene Konten bekommen. Die Grenzwerte für Erwachsene wären für sie falsch. Die WHO-Daten sind international anerkannt und frei verfügbar. Eingebettet funktionieren sie auch offline.*
Jedes Kind braucht ein eigenes Konto mit Geburtsdatum und Geschlecht. `heightCm` steht zunächst unter „Weitere Werte“ (`migrate`, `fv < 4`).

### E35 – Claude darf auf Anweisung direkt pushen
**Ab 2.8.0 committet und pusht Claude selbst in `main`, wenn der Nutzer das ausdrücklich verlangt. Sonst liefert Claude die Dateien bzw. ein Update-Paket, und der Nutzer veröffentlicht selbst.**
*Spart das Kopieren von Dateien. Die Kontrolle bleibt beim Nutzer, weil jeder Push ein Update in Home Assistant auslöst.*
Autor der Commits ist „Claude“. Der Nutzer holt vor eigenen Änderungen den Stand mit `git pull`.

### E36 – Dokumentation im Repository
**Architektur, Abläufe und Entscheidungen stehen unter `docs/`, Arbeitsanweisungen für Claude in `CLAUDE.md` (November 2026).**
*Damit spätere Änderungen, auch in neuen Sitzungen ohne den bisherigen Gesprächsverlauf, auf dem gleichen Wissensstand aufbauen.*
Bei neuen Entscheidungen diese Datei ergänzen.
