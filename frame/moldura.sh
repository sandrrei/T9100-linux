#!/bin/sh
# Slideshow das fotos no ecrã do tablet.

# --- ecrã inicial com o endereço do painel: só uma vez por arranque (20 s) ---
INFO=/run/moldura-info.jpg
if [ ! -e /run/moldura-info.feito ]; then
    touch /run/moldura-info.feito
    IP=""
    for i in $(seq 1 30); do
        IP=$(ip -4 -o addr show wlan0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
        [ -n "$IP" ] && break
        sleep 2
    done
    if [ -n "$IP" ]; then L1="Painel: http://$IP"; L2="ou http://$(hostname).local"
    else L1="Sem rede Wi-Fi"; L2="Crie wifi.txt na particao FOTOALBUM"; fi
    magick -size 800x480 xc:black -fill white -font /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
        -gravity center -pointsize 32 -annotate +0-40 "$L1" -pointsize 24 -annotate +0+30 "$L2" \
        "$INFO" 2>/dev/null && {
        /usr/bin/fbi -T 1 -d /dev/fb0 --noverbose --nointeractive -1 --timeout 20 "$INFO" >/dev/null 2>&1
        sleep 20; pkill -x fbi; }
fi

# --- conversão das fotos novas em segundo plano ---
# Quando acaba e houve fotos novas, reinicia a moldura para as incluir no slideshow.
(
    novas=$(MODO=cobrir /usr/local/bin/prepara-fotos.sh 2>/dev/null | grep -c '^ok:')
    [ "$novas" -gt 0 ] && systemctl --no-block restart moldura
) &

# --- slideshow com as fotos já preparadas ---
LISTA=/run/fotos.txt
while true; do
    find /var/cache/moldura -type f -iname '*.jpg' ! -name '.*' > "$LISTA"
    if [ -s "$LISTA" ]; then
        exec /usr/bin/fbi -T 1 -d /dev/fb0 -a -t 10 -u --noverbose --nointeractive -l "$LISTA"
    fi
    clear > /dev/tty1        # ainda sem fotos preparadas: espera
    sleep 30
done
