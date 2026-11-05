setenv bootargs console=tty0 root=/dev/mmcblk0p1 rootwait rw panic=10 net.ifnames=0 quiet consoleblank=0 vt.global_cursor_default=0 systemd.show_status=0
load mmc 0:1 ${kernel_addr_r} /boot/zImage
load mmc 0:1 ${fdt_addr_r} /boot/sun5i-a13-q8-tablet.dtb
fdt addr ${fdt_addr_r}
fdt resize
fdt set /soc/usb@1c13000 dr_mode peripheral
bootz ${kernel_addr_r} - ${fdt_addr_r}
