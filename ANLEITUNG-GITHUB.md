# Körperdaten-Server über GitHub ausliefern

Mit dieser Anleitung kommt der Körperdaten-Server als App aus deinem GitHub-Repository auf den Home Assistant Green. Updates holst du danach mit einem Klick im App Store, ohne Samba.

**Ablauf im Überblick**
1. Repository auf GitHub anlegen und hochladen (einmalig).
2. Repository in Home Assistant hinzufügen (einmalig).
3. Daten von der bisherigen lokalen App übernehmen (einmalig).
4. Updates: ändern, `python tools/release.py …`, in Home Assistant aktualisieren.

---

## 1. Repository auf GitHub anlegen

**Voraussetzungen am Windows-PC:** [Git für Windows](https://git-scm.com/download/win) und Python mit `python -m pip install pyyaml`.

1. Auf github.com → **New repository**:
   - Name: `koerperdaten-ha`
   - Sichtbarkeit: **Public** (Hinweise zu privat siehe unten)
   - Kein README, keine .gitignore, keine Lizenz anlegen lassen.
2. Den Ordner `koerperdaten-ha-repo` aus dem Paket an einen festen Ort legen, z. B. `C:\Projekte\koerperdaten-ha`.
3. In diesem Ordner die Eingabeaufforderung öffnen (im Explorer in die Adresszeile `cmd` tippen) und den eigenen GitHub-Namen eintragen lassen:
   ```
   python tools\einrichten.py DEIN-GITHUB-NAME
   python tools\pruefen.py
   ```
4. Hochladen:
   ```
   git init -b main
   git add -A
   git commit -m "Körperdaten-Server 2.2.0"
   git remote add origin https://github.com/DEIN-GITHUB-NAME/koerperdaten-ha.git
   git push -u origin main
   ```
   Beim ersten `git push` öffnet sich ein Anmeldefenster für GitHub.
5. Auf GitHub unter **Actions** läuft „Prüfen“ los: Server-Probelauf und Bau des Images. Ein grüner Haken heißt, alles ist in Ordnung.

> Die Datei `.gitattributes` sorgt dafür, dass `run.sh` unter Windows nicht mit Windows-Zeilenenden gespeichert wird. Sonst startet die App auf dem Green nicht. Bitte nicht löschen.

## 2. Repository in Home Assistant hinzufügen

1. Einstellungen → Apps → App Store → ⋮ → **Repositories**.
2. `https://github.com/DEIN-GITHUB-NAME/koerperdaten-ha` eintragen → **Hinzufügen**.
3. Im App Store erscheint ein neuer Abschnitt **Körperdaten-Apps** mit dem **Körperdaten-Server**. **Noch nicht installieren**, erst Schritt 3 lesen.

## 3. Daten von der lokalen App übernehmen

Für Home Assistant ist die App aus GitHub eine **andere App** als die lokale, mit eigenem Datenordner. Benutzer und Messwerte ziehen daher nicht von selbst um. So geht es ohne Verlust:

1. **Sicherung anlegen:** Einstellungen → System → Sicherungen → Sicherung erstellen. Die lokale App „Körperdaten-Server“ muss enthalten sein.
2. **Lokale App auf 2.2.0 bringen**, letztmalig per Samba: den Ordner `koerperdaten` aus diesem Repository nach `addons/koerperdaten` kopieren und die vorhandenen Dateien überschreiben. Dann im App Store „Nach Updates suchen“ → lokale App **aktualisieren**.
3. In der **lokalen** App unter **Konfiguration** die Option **Umzugsdaten bereitstellen** einschalten → **Speichern**. Die App startet neu.
4. Die lokale App **stoppen** und unter **Info** „Beim Start ausführen“ ausschalten. Beim Stoppen legt sie den aktuellen Stand in `/share/koerperdaten/umzug` ab.
5. Die **GitHub-App** installieren, „Beim Start ausführen“ und „Watchdog“ einschalten, **starten**. Im Protokoll muss stehen: **„Umzug abgeschlossen: Benutzer und Messwerte wurden übernommen.“**
6. Den Tracker öffnen, anmelden und die Daten prüfen. Das bisherige Zertifikat wird mit übernommen, die Browser-Warnung erscheint deshalb nicht erneut.
7. Erst danach die **lokale App deinstallieren** und per Samba den Ordner `addons/koerperdaten` löschen.

Die Kopie für den Umzug enthält Passwort-Hashes und Gesundheitsdaten. Die neue App verschiebt sie nach der Übernahme in ihren eigenen, geschützten Datenordner. In `share` bleibt nichts zurück.

**Falls etwas schiefgeht:** Die lokale App läuft unverändert weiter, solange sie nicht deinstalliert ist. Einfach wieder starten. Die neue App lässt sich bedenkenlos deinstallieren und neu versuchen, solange sie noch keine Daten hat.

## 4. Updates veröffentlichen

1. Dateien im Ordner `koerperdaten` ändern, z. B. eine neue `Koerperdaten.html` oder `koerperdaten_server.py` von Claude hineinkopieren.
2. Im Repository-Ordner:
   ```
   python tools\release.py patch "Kurzbeschreibung der Änderung"
   ```
   - `patch` für Korrekturen (2.2.0 → 2.2.1), `minor` für neue Funktionen (2.2.1 → 2.3.0)
   - Das Skript erhöht die Version, ergänzt `CHANGELOG.md`, prüft alles, committet und lädt hoch.
3. In Home Assistant: App Store → ⋮ → **Nach Updates suchen** → beim Körperdaten-Server **Aktualisieren**. Home Assistant prüft auch von selbst regelmäßig. Das Update-Fenster zeigt die Einträge aus `CHANGELOG.md`.

**Wichtig:**
- Ohne höhere Versionsnummer zeigt Home Assistant kein Update an. Die GitHub-Prüfung warnt, wenn das vergessen wurde.
- Vor jedem Versionswechsel sichert die App ihre Datenbank selbst nach `/data/vor-update/`. Eine Home-Assistant-Sicherung vor größeren Updates schadet trotzdem nicht. Sie ist nötig, wenn man zurück auf die alte Version will.
- Den `slug: koerperdaten` in `config.yaml` nie ändern. Sonst hält Home Assistant die App für eine neue, ohne Daten.
- Die App nie deinstallieren, um ein Problem zu lösen. Das löscht alle Daten.

## 5. Optional: vorgebaute Images

Normalerweise baut der Green die App bei jedem Update selbst, das dauert einige Minuten. Alternativ baut GitHub die Images vor, und der Green lädt sie nur noch herunter:

1. In `koerperdaten/config.yaml` vor der Zeile `# image: "ghcr.io/…/koerperdaten-{arch}"` das `# ` entfernen.
2. Mit `python tools\release.py patch "Vorgebaute Images"` veröffentlichen. Unter **Actions** läuft „Images bauen“, das dauert etwa 5–10 Minuten.
3. Auf GitHub unter deinem Profil → **Packages** → `koerperdaten-aarch64` (und `-amd64`) → Package settings → **Change visibility → Public**. Sonst kann Home Assistant die Images nicht laden. Das ist nur beim ersten Mal nötig.
4. Erst wenn „Images bauen“ fertig ist, in Home Assistant aktualisieren. Vorher findet der Green das Image noch nicht.

## Privates Repository?

Der Code enthält keine Passwörter und keine Daten. Ein öffentliches Repository ist daher unkritisch und am einfachsten. Soll es trotzdem privat sein: In Home Assistant die Adresse mit einem GitHub-Token angeben (`https://DEIN-GITHUB-NAME:TOKEN@github.com/DEIN-GITHUB-NAME/koerperdaten-ha`). Den Token erstellst du unter GitHub → Settings → Developer settings → Fine-grained token, nur Leserecht auf dieses eine Repository. Das funktioniert in der Regel, ist aber nicht offiziell dokumentiert, und der Token steht im Klartext in der Home-Assistant-Konfiguration. Vorgebaute Images aus einem privaten Repository kann Home Assistant ohne weitere Einrichtung nicht laden.
