#!/bin/sh
# Cria cópias 800x480 das fotos em /srv/fotos para a moldura,
# e apaga as cópias cujo original já não existe.
# Escreve "ok: <foto>" por cada foto nova convertida.
MODO=${MODO:-cobrir}            # cobrir | desfocado
SRC=/srv/fotos
DST=/var/cache/moldura
mkdir -p "$DST"
IM=$(command -v magick || command -v convert)
VALIDOS=$(mktemp)

find "$SRC" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) | while read -r f; do
    nome="$(printf '%s' "$f" | md5sum | cut -c1-16).jpg"
    echo "$nome" >> "$VALIDOS"
    out="$DST/$nome"
    [ -s "$out" ] && continue                 # já preparada
    tmp="$DST/.$nome.parcial"                 # nome que o slideshow ignora
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
        mv "$tmp" "$out" && echo "ok: $f"
    else
        rm -f "$tmp"
    fi
done

# Limpeza: só corre se foram encontradas fotos (protege contra o cartão não montado)
if [ -s "$VALIDOS" ]; then
    for c in "$DST"/*.jpg; do
        [ -e "$c" ] || continue
        grep -qxF "$(basename "$c")" "$VALIDOS" || rm -f "$c"
    done
fi
rm -f "$VALIDOS" "$DST"/.*.parcial
