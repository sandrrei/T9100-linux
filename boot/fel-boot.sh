#!/bin/bash
# Boot U-Boot + kernel + DTB + initramfs over USB (FEL). Nothing is written to the tablet.
# The tablet must be in FEL mode (fel-sdboot card): lsusb shows 1f3a:efe8.
# Paths assume ~/u-boot, ~/linux and ~/a13-boot/uInitrd (see README, Steps 2-3).
set -e
cd "$(dirname "$0")"

cat > boot-fel.cmd <<'CMD'
setenv bootargs console=tty0 console=ttyGS0,115200 loglevel=8 ignore_loglevel panic=0 clk_ignore_unused rdinit=/init
fdt addr 0x43000000
fdt resize
fdt set /soc/usb@1c13000 dr_mode peripheral
bootz 0x42000000 0x43300000 0x43000000
CMD

mkimage -C none -A arm -T script -d boot-fel.cmd boot-fel.scr

sudo sunxi-fel -v uboot ~/u-boot/u-boot-sunxi-with-spl.bin \
  write 0x42000000 ~/linux/arch/arm/boot/zImage \
  write 0x43000000 ~/linux/arch/arm/boot/dts/allwinner/sun5i-a13-q8-tablet.dtb \
  write 0x43100000 boot-fel.scr \
  write 0x43300000 ~/a13-boot/uInitrd
