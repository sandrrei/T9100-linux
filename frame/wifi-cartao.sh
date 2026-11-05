#!/bin/sh
# Applies the Wi-Fi network written in wifi.txt on the photo partition, then deletes the file.
#   wifi.txt:  SSID=NetworkName
#              PASSWORD=password      (omit for an open network)
# Existing networks are kept; the new one gets priority. Windows line endings and UTF-8 BOM are OK.
F=/srv/fotos/wifi.txt
CONF=/etc/wpa_supplicant/wpa_supplicant-wlan0.conf
[ -f "$F" ] || exit 0

valor() { sed -n "s/^\xEF\xBB\xBF//; s/^[[:space:]]*$1[[:space:]]*=//p" "$F" | tr -d '\r' | head -1; }
SSID=$(valor SSID)
PASS=$(valor PASSWORD)
[ -n "$SSID" ] || { echo "wifi.txt sem SSID / wifi.txt has no SSID" > /srv/fotos/wifi-erro.txt; exit 0; }
if [ -n "$PASS" ] && { [ ${#PASS} -lt 8 ] || [ ${#PASS} -gt 63 ]; }; then
    echo "A password tem de ter 8 a 63 caracteres / The password must be 8 to 63 characters" \
        > /srv/fotos/wifi-erro.txt
    exit 0
fi

# drop an older block for the same network, if any
TMP=$(mktemp)
awk -v s="ssid=\"$SSID\"" '
  /^network=\{/ { bloco=$0 "\n"; dentro=1; tem=0; next }
  dentro { bloco=bloco $0 "\n"; if (index($0, s)) tem=1
           if ($0 ~ /^\}/) { if (!tem) printf "%s", bloco; dentro=0 } ; next }
  { print }' "$CONF" > "$TMP" 2>/dev/null

# add the new network with priority over the others
if [ -n "$PASS" ]; then
    wpa_passphrase "$SSID" "$PASS" | grep -v '#psk=' | sed 's/^}$/\tpriority=10\n}/' >> "$TMP"
else
    printf 'network={\n\tssid="%s"\n\tkey_mgmt=NONE\n\tpriority=10\n}\n' "$SSID" >> "$TMP"
fi
cat "$TMP" > "$CONF"; chmod 600 "$CONF"; rm -f "$TMP"

# do not leave the password on the card
rm -f "$F" /srv/fotos/wifi-erro.txt
echo "Rede / Network \"$SSID\": $(date '+%Y-%m-%d %H:%M')" > /srv/fotos/wifi-aplicado.txt
