#!/usr/bin/with-contenv bashio
# Startet den Körperdaten-Server als Home-Assistant-App.
# Daten, Datenbank und Zertifikat liegen im dauerhaften App-Ordner /data.

UMZUG=/share/koerperdaten/umzug
ARGS=(start --daten /data --port 8443 --sicherungen "$(bashio::config 'sicherungen')")
HOST_PORT="$(bashio::addon.port 8443 2>/dev/null || true)"
[ -z "${HOST_PORT}" ] || [ "${HOST_PORT}" = "null" ] && HOST_PORT=8443

# ---- Umzug: Daten einer anderen Installation übernehmen (z. B. lokale App → GitHub-App)
# Nur wenn diese App noch keine eigene Datenbank hat; vorhandene Daten werden nie überschrieben.
has_users() {   # hat diese App schon Benutzer? (eine leere, frisch angelegte Datenbank zählt nicht)
  [ -s /data/koerperdaten.db ] || return 1
  python3 -c "import sqlite3,sys; c=sqlite3.connect('file:/data/koerperdaten.db?mode=ro', uri=True); sys.exit(0 if c.execute('SELECT COUNT(*) FROM benutzer').fetchone()[0] > 0 else 1)" 2>/dev/null
}
if [ -f "${UMZUG}/koerperdaten.db" ]; then
  if ! has_users; then
    bashio::log.info "Übernehme Daten aus ${UMZUG} …"
    if python3 /app/koerperdaten_server.py wiederherstellen "${UMZUG}/koerperdaten.db" --ja --daten /data; then
      if [ -f "${UMZUG}/zertifikat/zertifikat.pem" ]; then
        # bisheriges Zertifikat weiterverwenden – Browser und Handys kennen es schon
        rm -rf /data/zertifikat && cp -a "${UMZUG}/zertifikat" /data/zertifikat
      fi
      mkdir -p /data/vor-update
      mv "${UMZUG}/koerperdaten.db" "/data/vor-update/koerperdaten-umzug-$(date +%Y%m%d-%H%M%S).db"
      rm -rf "${UMZUG}/zertifikat" "${UMZUG}/info.txt"
      rmdir "${UMZUG}" /share/koerperdaten 2>/dev/null || true
      bashio::log.info "Umzug abgeschlossen: Benutzer und Messwerte wurden übernommen."
    else
      bashio::log.warning "Die Umzugsdaten konnten nicht übernommen werden. Die App startet ohne Daten."
    fi
  elif ! bashio::config.true 'umzug_bereitstellen'; then
    bashio::log.warning "In ${UMZUG} liegen Umzugsdaten, die nicht übernommen wurden, weil diese App schon eine Datenbank hat."
    bashio::log.warning "Sie enthalten Passwort-Hashes und Gesundheitsdaten – bitte den Ordner per Samba löschen (share/koerperdaten)."
  fi
fi

# Netzwerkadressen des Home-Assistant-Rechners (nicht des Containers)
IPS=()
for ip in $(bashio::network.ipv4_address 2>/dev/null || true); do
  IPS+=("${ip%%/*}")
done

if bashio::config.true 'eigenes_zertifikat'; then
  CERT="/ssl/$(bashio::config 'zertifikat')"
  KEY="/ssl/$(bashio::config 'schluessel')"
  if ! bashio::fs.file_exists "${CERT}" || ! bashio::fs.file_exists "${KEY}"; then
    bashio::exit.nok "Zertifikat ${CERT} oder Schlüssel ${KEY} nicht gefunden. Pfade in der Konfiguration prüfen."
  fi
  bashio::log.info "Verwende eigenes Zertifikat ${CERT}"
  ARGS+=(--zertifikat "${CERT}" --schluessel "${KEY}")
else
  # Namen und Adressen, unter denen der Tracker erreichbar ist, kommen ins selbst signierte Zertifikat
  NAMES=(homeassistant homeassistant.local)
  HA_HOST="$(bashio::info.hostname 2>/dev/null || true)"
  if [ -n "${HA_HOST}" ] && [ "${HA_HOST}" != "null" ]; then
    NAMES+=("${HA_HOST}" "${HA_HOST}.local")
  fi
  NAMES+=("${IPS[@]}")
  for n in $(bashio::config 'zusaetzliche_namen'); do
    NAMES+=("${n}")
  done
  # Haben sich die Namen geändert, wird das Zertifikat neu erzeugt
  WANT="$(printf '%s\n' "${NAMES[@]}" | sort -u)"
  mkdir -p /data/zertifikat
  if [ "$(cat /data/zertifikat/namen.txt 2>/dev/null)" != "${WANT}" ]; then
    rm -f /data/zertifikat/zertifikat.pem /data/zertifikat/schluessel.pem
    printf '%s' "${WANT}" > /data/zertifikat/namen.txt
  fi
  for n in $(printf '%s\n' "${NAMES[@]}" | sort -u); do
    ARGS+=(--name "${n}")
  done
  # Stammzertifikat zusätzlich im Samba-Ordner „share“ ablegen (nur der öffentliche Teil)
  ARGS+=(--ca-kopie /share/koerperdaten-ca.crt)
fi

# ---- Umzug: Daten für eine andere Installation bereitstellen
if bashio::config.true 'umzug_bereitstellen'; then
  mkdir -p "${UMZUG}"
  chmod 700 /share/koerperdaten "${UMZUG}" 2>/dev/null || true
  ARGS+=(--uebergabe "${UMZUG}")
  bashio::log.warning "Umzugsdaten werden in ${UMZUG} bereitgestellt (beim Start und beim Stoppen der App aktualisiert)."
  bashio::log.warning "Nach dem Umzug diese Option wieder ausschalten bzw. die alte App deinstallieren."
fi

if ! bashio::config.true 'protokoll'; then
  ARGS+=(--leise)
fi

for ip in "${IPS[@]}"; do
  bashio::log.info "Tracker im Browser öffnen: https://${ip}:${HOST_PORT}/"
done
bashio::log.info "Oder: https://homeassistant.local:${HOST_PORT}/"

exec python3 -u /app/koerperdaten_server.py "${ARGS[@]}"
