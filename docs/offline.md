# Offline und Synchronisierung

Gilt seit Version 2.7.0 und nur für die Speicherart `server`.

## Ziel

Die App soll auch ohne Verbindung zum Server funktionieren, etwa unterwegs ohne VPN. Sie zeigt dann den letzten Monat an, nimmt neue Werte an und überträgt sie von selbst, sobald der Server wieder erreichbar ist. Dabei darf nichts verloren gehen, auch nicht, wenn ein anderes Gerät inzwischen gespeichert hat.

## Bausteine

| Baustein | Ort | Aufgabe |
|---|---|---|
| Service Worker | `sw.js` vom Server | Seite, Icons, Chart.js und Schriften auch offline bereitstellen |
| Offline-Kopie | `localStorage`, Schlüssel `koerperdaten-offline\|<url>\|<benutzer>` | Messwerte der letzten 31 Tage, Einstellungen, Basis-Stand, ETag |
| `base` | Arbeitsspeicher und Offline-Kopie | letzter vom Server bestätigter Stand je Tag als stabiles JSON |
| `partial` | Arbeitsspeicher | `true`, solange nur die Offline-Kopie geladen ist (nicht alle Daten) |

## Ablauf

**Normalbetrieb:**
- Nach jedem erfolgreichen Laden oder Speichern setzt `snapshotBase()` die Basis neu, und `writeOffline()` schreibt die Kopie.
- Jede Änderung schreibt die Kopie sofort (`markDirty` → `writeOffline`), noch bevor der Server gefragt wird. Zusätzlich sichern `pagehide` und `beforeunload`. So bleibt auch ein Eintrag erhalten, nach dem die App gleich geschlossen wird.

**Server nicht erreichbar** (Netzwerkfehler, Status 0):
- Sind schon Daten geladen, wechselt der Status auf `offline`, und die Arbeit geht normal weiter.
- Beim Start ohne Verbindung lädt `startOffline()` die Offline-Kopie. Dann ist `partial = true`, oben steht „Offline – Daten der letzten 31 Tage“.
- Ein neuer Verbindungsversuch kommt alle 20 s, bei `online` und beim Zurückkehren in die App (`visibilitychange`).

**Verbindung wieder da:**
- `serverConnect()` → `loadUserData()` lädt den aktuellen Server-Stand.
- Gibt es eigene ungespeicherte Änderungen oder war `partial` gesetzt, folgt `mergeSync(txt)`:
  1. `localDiff()` vergleicht den Gerätestand mit `base`. Das Ergebnis sind die geänderten oder gelöschten Tage und ob sich die Einstellungen geändert haben.
  2. Den Server-Stand laden (`fromXml`), `partial = false` setzen und die Basis neu setzen.
  3. `applyDiff(d)` spielt nur die geänderten Tage ein. Ein gelöschter Tag wird auch auf dem Server gelöscht. Haben sich die Einstellungen geändert, ersetzen sie die des Servers.
  4. Mit `If-Match` speichern. Antwortet der Server mit 412, weil sich der Stand inzwischen erneut geändert hat, beginnt der Ablauf von vorn, höchstens dreimal. Danach erscheint der Konfliktdialog.
- Am Ende erscheint die Meldung „Offline-Änderungen übertragen (n Tage)“.

**Konflikte im Normalbetrieb:** Hat ein anderes Gerät gespeichert, antwortet `PUT` mit 412. Auch dann arbeitet `mergeSync` die eigenen Änderungen tageweise ein. Nur wenn das dreimal hintereinander scheitert, fragt `onConflict()`: „Server-Stand laden“ oder „Meine Version speichern“.

## Regeln und Grenzen

- **Einheit ist der Tag.** Wurde derselbe Tag auf zwei Geräten geändert, gilt die zuletzt übertragene Version. Werte innerhalb eines Tages werden nicht einzeln zusammengeführt. Das ist bewusst einfach gehalten, weil an einem Tag praktisch nur eine Person ihre eigenen Werte einträgt.
- Ein Gerät lädt nie Daten eines anderen Benutzers hoch. `serverPut` prüft `dataOwner === srvUser.name`, und beim Wechsel des Benutzers wird der Arbeitsspeicher geleert.
- Offline nicht verfügbar sind Fotos, der CSV-Export (er würde nur 31 Tage enthalten) und Diagramme über mehr als einen Monat.
- Tage außerhalb der 31 Tage bleiben in der Kopie, solange sie noch nicht übertragene Änderungen haben.
- Beim Abmelden wird die Offline-Kopie gelöscht.
- Kann der `localStorage` nicht schreiben (voll oder privater Modus), läuft die App ohne Offline-Kopie weiter (`lsOk`).

## Getestete Fälle

Mit Playwright gegen einen echten Server geprüft (Version 2.5/2.7):

- **A:** Offline-Start, Eintrag anlegen, Verbindung wieder da → Eintrag landet auf dem Server.
- **B:** Gerät 1 offline ändert Tag X, Gerät 2 online ändert Tag Y → beide Änderungen bleiben erhalten.
- **C:** Offline einen Tag löschen und die App sofort neu laden → das Löschen geht nicht verloren (deshalb schreibt `markDirty` die Kopie sofort).
- **D:** 412 während des Speicherns → tageweises Einarbeiten statt Überschreiben.
