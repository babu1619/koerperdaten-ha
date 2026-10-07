#!/usr/bin/env python3
"""Neue Version veröffentlichen: Versionsnummer erhöhen, Änderung notieren, prüfen, committen, hochladen.

Aufruf (im Repository-Ordner):
    python tools/release.py patch "Tippfehler im Erfassen-Formular behoben"
    python tools/release.py minor "Neues Diagramm für Schlaf"
    python tools/release.py 2.3.0 "Text"          # Version direkt angeben
    python tools/release.py patch "Text" --ohne-push

patch = kleine Korrektur (2.2.0 → 2.2.1), minor = neue Funktion (2.2.1 → 2.3.0).
Home Assistant zeigt das Update an, sobald die neue Version auf GitHub liegt.
"""
import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "koerperdaten" / "config.yaml"
CHANGELOG = ROOT / "koerperdaten" / "CHANGELOG.md"
SERVER = ROOT / "koerperdaten" / "koerperdaten_server.py"
HTML = ROOT / "koerperdaten" / "Koerperdaten.html"


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} fehlgeschlagen:\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def main():
    p = argparse.ArgumentParser(description="Neue Version der Körperdaten-App veröffentlichen")
    p.add_argument("stufe", help="patch, minor, major oder eine Version wie 2.3.0")
    p.add_argument("text", help="kurze Beschreibung der Änderung")
    p.add_argument("--ohne-push", action="store_true", help="nur lokal committen, nicht hochladen")
    a = p.parse_args()

    text = CONFIG.read_text(encoding="utf-8")
    m = re.search(r'^version:\s*"?(\d+)\.(\d+)\.(\d+)"?\s*$', text, re.M)
    if not m:
        sys.exit("Version in koerperdaten/config.yaml nicht gefunden.")
    major, minor, patch = map(int, m.groups())
    old = f"{major}.{minor}.{patch}"
    if a.stufe == "patch":
        new = f"{major}.{minor}.{patch + 1}"
    elif a.stufe == "minor":
        new = f"{major}.{minor + 1}.0"
    elif a.stufe == "major":
        new = f"{major + 1}.0.0"
    elif re.fullmatch(r"\d+\.\d+\.\d+", a.stufe):
        new = a.stufe
        if tuple(map(int, new.split("."))) <= (major, minor, patch):
            sys.exit(f"Die neue Version {new} muss höher sein als {old}.")
    else:
        sys.exit("Stufe muss patch, minor, major oder eine Version wie 2.3.0 sein.")

    if git("rev-parse", "--is-inside-work-tree", check=False) != "true":
        sys.exit("Dieser Ordner ist kein Git-Repository.")

    # Server- und Tracker-Version mitziehen (Server: 2.3, Tracker: 2.3), wenn sich major/minor ändert
    short = ".".join(new.split(".")[:2])
    s = SERVER.read_text(encoding="utf-8")
    s2 = re.sub(r'^VERSION = "[^"]+"', f'VERSION = "{short}"', s, count=1, flags=re.M)
    if s2 != s:
        SERVER.write_text(s2, encoding="utf-8", newline="\n")
    h = HTML.read_text(encoding="utf-8")
    h2 = re.sub(r"const TRACKER_VERSION = '[^']+';", f"const TRACKER_VERSION = '{short}';", h, count=1)
    if h2 != h:
        HTML.write_text(h2, encoding="utf-8", newline="\n")

    CONFIG.write_text(text[:m.start()] + f'version: "{new}"' + text[m.end():], encoding="utf-8", newline="\n")
    log = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else "# Änderungen\n"
    head, _, rest = log.partition("\n## ")
    entry = f"## {new}\n\n- {a.text}\n"
    log = head.rstrip("\n") + "\n\n" + entry + ("\n## " + rest if rest else "")
    CHANGELOG.write_text(log, encoding="utf-8", newline="\n")

    r = subprocess.run([sys.executable, str(ROOT / "tools" / "pruefen.py")], cwd=ROOT)
    if r.returncode != 0:
        sys.exit("Prüfung fehlgeschlagen – nichts committet. Fehler beheben und erneut aufrufen "
                 "(die Versionsnummer wurde schon erhöht; dann die Version direkt angeben).")

    git("add", "-A")
    git("commit", "-m", f"Version {new}: {a.text}")
    git("tag", "-a", f"v{new}", "-m", f"Version {new}")
    print(f"Version {old} → {new} committet (Tag v{new}).")
    if a.ohne_push:
        print("Hochladen später mit:  git push --follow-tags")
        return
    git("push", "--follow-tags")
    print("Hochgeladen. In Home Assistant: Einstellungen → Apps → App Store → ⋮ → Nach Updates suchen.")


if __name__ == "__main__":
    main()
