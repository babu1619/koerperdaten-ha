#!/usr/bin/env python3
"""Prüft das Repository vor dem Hochladen (läuft auch automatisch auf GitHub).

Aufruf:  python tools/pruefen.py
"""
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "koerperdaten"
errors, warnings = [], []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


try:
    import yaml
except ImportError:
    sys.exit("Bitte zuerst installieren:  python -m pip install pyyaml")


def load(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        err(f"{path.relative_to(ROOT)} fehlt")
    except yaml.YAMLError as e:
        err(f"{path.relative_to(ROOT)} ist kein gültiges YAML: {e}")
    return None


# ---- repository.yaml
repo = load(ROOT / "repository.yaml")
if repo is not None and not repo.get("name"):
    err("repository.yaml: 'name' fehlt")

# ---- config.yaml
cfg = load(APP / "config.yaml") or {}
for key in ("name", "version", "slug", "description", "arch"):
    if not cfg.get(key):
        err(f"config.yaml: '{key}' fehlt")
version = str(cfg.get("version", ""))
if not isinstance(cfg.get("version"), str):
    err("config.yaml: version muss in Anführungszeichen stehen, z. B. version: \"2.2.0\"")
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    err(f"config.yaml: Version '{version}' hat nicht die Form 1.2.3")
if cfg.get("slug") != "koerperdaten":
    err("config.yaml: slug muss 'koerperdaten' bleiben, sonst sieht Home Assistant eine neue App ohne Daten")
for arch in cfg.get("arch") or []:
    if arch not in ("aarch64", "amd64"):
        err(f"config.yaml: unbekannte Architektur '{arch}'")
options, schema = cfg.get("options") or {}, cfg.get("schema") or {}
if set(options) != set(schema):
    err(f"config.yaml: options und schema passen nicht zusammen: {sorted(set(options) ^ set(schema))}")
if "GITHUB-BENUTZER" in (APP / "config.yaml").read_text(encoding="utf-8"):
    warn("config.yaml enthält noch den Platzhalter GITHUB-BENUTZER (python tools/einrichten.py NAME)")
if cfg.get("image") and "{arch}" not in str(cfg["image"]):
    err("config.yaml: image muss den Platzhalter {arch} enthalten")

# ---- Übersetzungen
for lang in ("de", "en"):
    tr = load(APP / "translations" / f"{lang}.yaml") or {}
    missing = set(schema) - set((tr.get("configuration") or {}).keys())
    if missing:
        err(f"translations/{lang}.yaml: Beschreibung fehlt für {sorted(missing)}")

# ---- Dateien
for name in ("Dockerfile", "run.sh", "koerperdaten_server.py", "Koerperdaten.html", "DOCS.md", "CHANGELOG.md"):
    if not (APP / name).exists():
        err(f"koerperdaten/{name} fehlt")
run = (APP / "run.sh").read_bytes() if (APP / "run.sh").exists() else b""
if b"\r\n" in run:
    err("run.sh hat Windows-Zeilenenden (CRLF) und startet so auf Home Assistant nicht. .gitattributes prüfen.")
if run and not run.startswith(b"#!/usr/bin/with-contenv bashio"):
    err("run.sh: erste Zeile muss '#!/usr/bin/with-contenv bashio' sein")

# ---- Server
server = APP / "koerperdaten_server.py"
if server.exists():
    try:
        py_compile.compile(str(server), doraise=True, cfile=str(ROOT / ".pruefen.pyc"))
    except py_compile.PyCompileError as e:
        err(f"koerperdaten_server.py enthält einen Fehler: {e.msg}")
    finally:
        (ROOT / ".pruefen.pyc").unlink(missing_ok=True)
    m = re.search(r'^VERSION = "([^"]+)"', server.read_text(encoding="utf-8"), re.M)
    if not m:
        err("koerperdaten_server.py: VERSION nicht gefunden")
    elif not version.startswith(m.group(1) + "."):
        warn(f"App-Version {version} passt nicht zur Server-Version {m.group(1)} (erwartet {m.group(1)}.x)")

# ---- Änderungsprotokoll
changelog = APP / "CHANGELOG.md"
if changelog.exists() and f"## {version}" not in changelog.read_text(encoding="utf-8"):
    warn(f"CHANGELOG.md enthält keinen Abschnitt '## {version}'")

for w in warnings:
    print(f"Hinweis: {w}")
for e in errors:
    print(f"FEHLER:  {e}")
if errors:
    sys.exit(1)
print(f"In Ordnung: Körperdaten-Server {version}")
