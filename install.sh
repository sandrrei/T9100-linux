#!/bin/sh
# Installs the photo frame, web panel, wifi.txt support and watchdog on the tablet.
# Run ON THE TABLET, from the repository folder:   sudo ./install.sh
# Safe to run again: keeps the slideshow mode and interval chosen in the web panel.
set -e
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo ./install.sh"; exit 1; }

echo "== Packages"
apt-get install -y fbi imagemagick python3 avahi-daemon u-boot-tools

echo "== Photo partition (FAT32, partition 2 of the card)"
mkdir -p /srv/fotos /var/cache/moldura
grep -q ' /srv/fotos ' /etc/fstab || \
    echo "/dev/mmcblk0p2 /srv/fotos vfat rw,noatime,nofail,uid=1000,gid=1000 0 0" >> /etc/fstab

echo "== Scripts"
MODO=cobrir; T=10
if [ -f /usr/local/bin/moldura.sh ]; then
    m=$(grep -o 'MODO=[a-z]*' /usr/local/bin/moldura.sh | head -1 | cut -d= -f2)
    t=$(grep -o -- '-t [0-9]*' /usr/local/bin/moldura.sh | head -1 | cut -d' ' -f2)
    [ -n "$m" ] && MODO=$m
    [ -n "$t" ] && T=$t
fi
TMP=$(mktemp)
sed "s/MODO=cobrir/MODO=$MODO/; s/-t 10 /-t $T /" frame/moldura.sh > "$TMP"
install -m 755 "$TMP" /usr/local/bin/moldura.sh
rm -f "$TMP"
install -m 755 frame/prepara-fotos.sh frame/wifi-cartao.sh panel/painel.py /usr/local/bin/

echo "== Services"
install -m 644 frame/moldura.service frame/prepara-fotos.service frame/wifi-cartao.service \
    panel/painel.service /etc/systemd/system/

echo "== Watchdog (A13 hardware max is 16 s) and journal flushing"
mkdir -p /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\nRebootWatchdogSec=2min\n' > /etc/systemd/system.conf.d/watchdog.conf
printf '[Journal]\nSyncIntervalSec=30s\n' > /etc/systemd/journald.conf.d/sync.conf

systemctl daemon-reload
systemctl disable getty@tty1 2>/dev/null || true
systemctl mask serial-getty@ttyGS0           # a getty on the USB console freezes systemd
systemctl enable moldura painel wifi-cartao avahi-daemon
mount -a 2>/dev/null || true

echo
echo "Done (mode: $MODO, interval: $T s). Reboot to apply: sudo systemctl reboot"
echo "Then open http://$(hostname).local/ from a phone or PC."
