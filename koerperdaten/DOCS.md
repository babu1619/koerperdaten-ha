# Körperdaten-Server

Tracker für Gewicht, Umfänge, Körperfett, Kalorien und weitere Körper- und Fitnessdaten. Jeder Benutzer hat ein eigenes Konto und sieht nur seine eigenen Daten. Die Verbindung läuft über HTTPS, die Daten liegen in einer SQLite-Datenbank im App-Ordner.

## Tracker öffnen

Klicke auf **Web-UI öffnen** oder rufe im Browser `https://<IP-deines-Home-Assistant>:8443/` auf. Die genaue Adresse steht im Protokoll der App.

Beim ersten Öffnen warnt der Browser vor dem Zertifikat, weil es selbst erstellt ist. Bestätige die Warnung einmal:

- Chrome/Edge: „Erweitert“ → „Weiter zu …“
- Firefox: „Erweitert“ → „Risiko akzeptieren“

Die Verbindung ist trotzdem verschlüsselt. Dauerhaft ohne Warnung und als App auf dem Handy: siehe „Als App installieren“.

## Erste Schritte

1. Im Tracker auf **Anmelden** tippen.
2. Den ersten Benutzer anlegen. Er wird Administrator.
3. Weitere Benutzer legst du unter Einstellungen → Speicherort → **Benutzerverwaltung** an.

## Als App installieren und Zertifikatswarnung abschalten

Der Server erstellt beim ersten Start eine eigene kleine Zertifizierungsstelle (CA) und damit das HTTPS-Zertifikat. Installierst du das **Stammzertifikat** einmal auf einem Gerät, vertraut es dem Server: Die Warnung verschwindet, und Chrome bietet „App installieren“ an. Das Zertifikat erneuert der Server selbst, das Stammzertifikat gilt 10 Jahre.

**Stammzertifikat laden:** Im Tracker unter Einstellungen → **Als App installieren** auf „Stammzertifikat laden“ tippen oder `https://<IP>:8443/ca.crt` aufrufen.

**Android (Chrome):**
1. Die Datei `koerperdaten-ca.crt` herunterladen.
2. Einstellungen → Sicherheit und Datenschutz → Weitere Sicherheitseinstellungen → Verschlüsselung und Anmeldedaten → **Zertifikat installieren** → **CA-Zertifikat** → „Trotzdem installieren“ → Datei wählen. (Der Weg heißt je nach Hersteller etwas anders; in den Einstellungen nach „Zertifikat“ suchen.)
3. Chrome ganz schließen und neu öffnen, `https://<IP>:8443/` aufrufen.
4. Chrome-Menü ⋮ → **App installieren** (oder „Zum Startbildschirm hinzufügen“ → Installieren).

**Windows (Chrome/Edge):**
1. `koerperdaten-ca.crt` herunterladen und doppelklicken → **Zertifikat installieren** → „Aktueller Benutzer“ → „Alle Zertifikate in folgendem Speicher speichern“ → **Vertrauenswürdige Stammzertifizierungsstellen** → Fertig stellen, Sicherheitswarnung mit Ja bestätigen.
2. Browser neu starten. In der Adressleiste erscheint das Symbol „App installieren“.

Firefox nutzt eigene Zertifikate: Einstellungen → Datenschutz & Sicherheit → Zertifikate anzeigen → Zertifizierungsstellen → Importieren.

Das Stammzertifikat erlaubt nur deinem Server, sich auszuweisen. Den privaten Schlüssel der CA (`/data/zertifikat/ca-schluessel.pem`) gibst du nicht weiter.

**Update von 2.2 oder älter:** Der Server stellt beim ersten Start ein neues Zertifikat aus. Ohne installiertes Stammzertifikat erscheint die Browser-Warnung deshalb noch einmal; einmal bestätigen oder gleich das Stammzertifikat installieren.

**Neue Adresse:** Ändert sich die IP-Adresse oder kommt unter „Zusätzliche Namen fürs Zertifikat“ ein Name hinzu, stellt der Server automatisch ein passendes Zertifikat aus. Das Stammzertifikat bleibt gleich.

## Optionen

| Option | Bedeutung |
|---|---|
| Sicherungen je Benutzer | Anzahl aufbewahrter früherer Stände (höchstens alle 10 Minuten einer) |
| Zusätzliche Namen fürs Zertifikat | weitere Adressen, z. B. `ha.fritz.box`; IP-Adresse und `homeassistant.local` sind schon enthalten |
| Eigenes Zertifikat verwenden | Zertifikat aus `/ssl` nutzen, etwa von der Let's-Encrypt-App |
| Zertifikatsdatei / Schlüsseldatei | Dateinamen in `/ssl` (Standard: `fullchain.pem`, `privkey.pem`) |
| Zugriffe protokollieren | jede Anfrage im Protokoll anzeigen, nur zur Fehlersuche |
| Umzugsdaten bereitstellen | nur für einen Umzug auf eine andere Installation dieser App (siehe unten) |

Den Port änderst du im Reiter **Netzwerk**.

## Sicherung

Die normalen Home-Assistant-Sicherungen enthalten die Datenbank dieser App. Für eine saubere Sicherung wird die App dabei kurz angehalten. Zusätzlich kann jeder Benutzer im Tracker frühere Stände seiner Daten wiederherstellen.

## Updates

Beim ersten Start einer neuen Version kopiert die App die Datenbank nach `/data/vor-update/`. Danach baut sie sie bei Bedarf auf den neuen Stand um. Schlägt das fehl, bleibt die Datenbank unverändert und die App startet nicht. Dann hilft das Zurückspielen der Home-Assistant-Sicherung von vor dem Update.

**Wichtig:** Die App nie deinstallieren, um sie neu zu installieren. Beim Deinstallieren löscht Home Assistant alle Benutzer und Messwerte.

## Passwort vergessen

Ein Administrator setzt Passwörter in der Benutzerverwaltung zurück. Hat der einzige Administrator sein Passwort vergessen, geht es über die App „Advanced SSH & Web Terminal“ mit ausgeschaltetem Schutzmodus:

```
docker exec -it $(docker ps --format '{{.Names}}' | grep koerperdaten) python3 /app/koerperdaten_server.py passwort-setzen NAME --daten /data
```

Auf dieselbe Weise prüfst oder sicherst du die Datenbank: `… koerperdaten_server.py pruefen --daten /data` bzw. `sichern --daten /data`.

## Umzug auf eine andere Installation

So kommen Benutzer, Messwerte und Zertifikat von einer Installation dieser App in eine andere, zum Beispiel von der lokalen App auf die App aus dem GitHub-Repository:

1. In der **bisherigen** App die Option **Umzugsdaten bereitstellen** einschalten, speichern und die App **stoppen**. Beim Stoppen legt sie den aktuellen Stand in `/share/koerperdaten/umzug` ab.
2. Die **neue** App installieren und starten. Sie übernimmt die Daten beim ersten Start automatisch und räumt den Ordner danach wieder auf. Im Protokoll steht „Umzug abgeschlossen“.
3. Im Tracker anmelden und prüfen. Danach die bisherige App deinstallieren.

Hat die neue App schon eigene Benutzer, übernimmt sie nichts und meldet das im Protokoll.

## Sicherheit

Die App ist für das Heimnetz gedacht. Gib den Port im Router nicht frei. Für den Zugriff von unterwegs nutze ein VPN, zum Beispiel WireGuard über die FRITZ!Box oder Tailscale.
