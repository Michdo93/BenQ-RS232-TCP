#!/bin/bash
# Creates a self-signed CA and a server certificate for Mosquitto (TLS on port 8883).
# The server certificate contains all names and IP addresses clients may use
# (subjectAltName), so hostname validation works in openHAB and mosquitto_sub.
#
# Usage: sudo ./create-certs.sh [hostname.local] [ip-address]
#        defaults: $(hostname).local and the first IP address of this machine
set -euo pipefail

FQDN="${1:-$(hostname).local}"
IP="${2:-$(hostname -I | awk '{print $1}')}"
SHORT="${FQDN%%.*}"
DAYS=3650
DIR=${CERT_DIR:-/etc/mosquitto/certs}

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root: sudo $0 $*" >&2
    exit 1
fi

echo "Creating certificates for: DNS:${FQDN}, DNS:${SHORT}, DNS:localhost, IP:${IP}, IP:127.0.0.1"
mkdir -p "$DIR"
cd "$DIR"

# CA
openssl req -new -x509 -days "$DAYS" -newkey rsa:2048 -nodes -sha256 \
    -keyout ca.key -out ca.crt -subj "/CN=BeamerPi-CA"

# Server key and certificate signing request
openssl req -new -newkey rsa:2048 -nodes -sha256 \
    -keyout server.key -out server.csr -subj "/CN=${FQDN}"

cat > server.ext <<EXT
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:${FQDN}, DNS:${SHORT}, DNS:localhost, IP:${IP}, IP:127.0.0.1
EXT

# Server certificate signed by the CA
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days "$DAYS" -sha256 -extfile server.ext

rm -f server.csr server.ext

# Permissions: Mosquitto needs the server key, nobody needs the CA key except root
chown mosquitto:mosquitto ca.crt server.crt server.key
chmod 644 ca.crt server.crt
chmod 600 server.key
chown root:root ca.key
chmod 600 ca.key

echo
openssl x509 -in server.crt -noout -subject -ext subjectAltName
echo
echo "Done. Copy ${DIR}/ca.crt to every client (openHAB, mosquitto_sub)."
echo "Restart Mosquitto: sudo systemctl restart mosquitto"
