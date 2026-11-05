# First-boot variant: kernel console and login on the USB cable (/dev/ttyACM0 on the PC).
# Use it only until Wi-Fi + SSH work, with picocom already open on the PC.
# Leaving console=ttyGS0 permanently freezes systemd (see README, Step 4).
setenv bootargs console=tty0 console=ttyGS0,115200 root=/dev/mmcblk0p1 rootwait rw panic=10 net.ifnames=0
load mmc 0:1 ${kernel_addr_r} /boot/zImage
load mmc 0:1 ${fdt_addr_r} /boot/sun5i-a13-q8-tablet.dtb
fdt addr ${fdt_addr_r}
fdt resize
fdt set /soc/usb@1c13000 dr_mode peripheral
bootz ${kernel_addr_r} - ${fdt_addr_r}
