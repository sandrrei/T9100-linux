#!/bin/sh
# Creates 800x480 copies of the photos in /srv/fotos for the photo frame,
# and removes copies whose original no longer exists.
# Prints "ok: <photo>" for every new photo converted.
#   --reiniciar   restart the slideshow at the end if there were new photos
#                 (used by prepara-fotos.service)
# Mode: $MODO if set, otherwise the MODO= line in moldura.sh (edited by the web panel).
#   cobrir    = crop to fill the screen
#   desfocado = whole photo on a blurred background
if [ -z "$MODO" ]; then
    MODO=$(grep -o 'MODO=[a-z]*' /usr/local/bin/moldura.sh 2>/dev/null | head -1 | cut -d= -f2)
fi
MODO=${MODO:-cobrir}
SRC=/srv/fotos
DST=/var/cache/moldura
mkdir -p "$DST"
IM=$(command -v magick || command -v convert)
VALIDOS=$(mktemp)
NOVAS=$(mktemp)

find "$SRC" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) | while read -r f; do
    nome="$(printf '%s' "$f" | md5sum | cut -c1-16).jpg"
    echo "$nome" >> "$VALIDOS"
    out="$DST/$nome"
    [ -s "$out" ] && continue                 # already prepared
    tmp="$DST/.$nome.parcial"                 # name the slideshow ignores
    if [ "$MODO" = desfocado ]; then
        nice -n 19 $IM "$f" -auto-orient \
            \( -clone 0 -resize 80x48^ -gravity center -extent 80x48 -resize 800x480! \) \
            \( -clone 0 -resize 800x480 \) -delete 0 -gravity center -composite \
            -quality 90 "jpg:$tmp"
    else
        nice -n 19 $IM "$f" -auto-orient -resize 800x480^ -gravity center -extent 800x480 \
            -quality 90 "jpg:$tmp"
    fi
    if [ -s "$tmp" ]; then
        mv "$tmp" "$out" && echo "ok: $f" && echo x >> "$NOVAS"
    else
        rm -f "$tmp"
        echo "falhou / failed: $f" >&2
    fi
done

# Cleanup only if photos were found (protects the cache if the partition is not mounted)
if [ -s "$VALIDOS" ]; then
    for c in "$DST"/*.jpg; do
        [ -e "$c" ] || continue
        grep -qxF "$(basename "$c")" "$VALIDOS" || rm -f "$c"
    done
fi
rm -f "$DST"/.*.parcial

n=$(wc -l < "$NOVAS")
rm -f "$VALIDOS" "$NOVAS"
if [ "$1" = "--reiniciar" ] && [ "$n" -gt 0 ]; then
    systemctl --no-block restart moldura
fi
