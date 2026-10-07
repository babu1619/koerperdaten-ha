FROM ghcr.io/home-assistant/base:latest

# Python mit SQLite und das Paket für das HTTPS-Zertifikat
RUN apk add --no-cache python3 py3-cryptography

WORKDIR /app
COPY koerperdaten_server.py Koerperdaten.html /app/
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
