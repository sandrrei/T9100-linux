# Mainline Linux on an Allwinner A13 tablet ("nuclear-f900" / T9100)

A learning project: repurposing a generic Allwinner A13 tablet (Android 4.0.4, 2012), currently used
as a photo frame, to run **mainline Linux** (upstream U-Boot + upstream kernel + Debian).

If you own a similar cheap A13 tablet (often sold as "Q8" / "Q88" style 7" tablets), most of this
should apply to you too. Check your own `script.fex` before copying any values.

Golden rule: **never touch the NAND**. Everything boots over **USB (FEL mode)** or from a **microSD card**.
Remove the card and the original Android boots as before.

> **Disclaimer:** you do this at your own risk. Wrong DRAM or PMIC settings can, in rare cases,
> damage hardware. Start with the conservative values below and keep a backup of your
> original boot partition.

---

## Status

| Step | Description | Status |
|---|---|---|
| 1 | Hardware discovery (adb, script.fex, dmesg, modules) | ✅ done |
| 2 | Mainline U-Boot, FEL boot, working LCD | ✅ done |
| 3 | Mainline kernel + initramfs, USB serial console, DRM display | ✅ done |
| 4 | Debian 13 armhf rootfs on microSD, standalone boot, Wi-Fi + SSH | ✅ done |
| 5 | Remaining hardware (touch, sleep, GPU userspace) | ⏳ |

### What works on mainline (kernel 7.4-rc1, U-Boot 2027.01-rc1)

| Component | Status | Notes |
|---|---|---|
| U-Boot (FEL and SD) | ✅ | `q8_a13_tablet_defconfig` + DRAM/LCD tweaks |
| Kernel boot | ✅ | `sunxi_defconfig` + options below |
| LCD (DRM `sun4i-drm`) | ✅ | stock Q8 DTS panel timings work, sharp and centred |
| Backlight (PWM) | ✅ | brightness 0–10 |
| GPU Mali-400 (`lima`) | ✅ | driver loads |
| PMIC AXP209 | ✅ | battery, USB/AC power supplies, power key |
| microSD | ✅ | |
| Volume keys (`lradc`) | ✅ | |
| Audio codec | ✅ | detected (`sun4i-codec`), not tested |
| USB serial console (`ttyGS0` → `ttyACM0`) | ⚠️ | great for initramfs/first boot; as a permanent `console=` it freezes systemd (see Step 4) |
| Hardware watchdog (`sunxi-wdt`) | ✅ | systemd `RuntimeWatchdogSec=15s` (16 s hardware max) |
| Wi-Fi RTL8188C family (`0bda:018a`) | ✅ | `rtl8192cu` driver + `firmware-realtek`, DHCP via systemd-networkd |
| Debian 13 (trixie) armhf | ✅ | boots standalone from microSD, SSH over Wi-Fi |
| NTP time sync | ✅ | no RTC battery: time comes from `systemd-timesyncd` |
| Photo frame + web control panel | ✅ | `fbi` slideshow, `painel.py` on port 80, `a13-tablet.local` |
| Touchscreen GT82x | ❌ | no mainline driver yet |

## Repository layout

| Path | Contents |
|---|---|
| [`configs/f900_a13_defconfig`](configs/f900_a13_defconfig) | U-Boot defconfig (Q8 A13 + this tablet's DRAM/LCD values) |
| [`configs/kernel-a13-tablet.config`](configs/kernel-a13-tablet.config) | kernel config fragment on top of `sunxi_defconfig` |
| [`boot/boot.cmd`](boot/boot.cmd) | final U-Boot boot script on the SD card |
| [`boot/boot-firstboot.cmd`](boot/boot-firstboot.cmd) | first-boot variant with the USB serial console |
| [`boot/fel-boot.sh`](boot/fel-boot.sh), [`boot/initramfs-init`](boot/initramfs-init) | boot over USB (FEL) with a debugging initramfs |
| [`frame/`](frame/) | photo frame scripts and services |
| [`panel/`](panel/) | web control panel |
| [`install.sh`](install.sh) | installs `frame/` and `panel/` on the tablet |

---

## Device identification

```
ro.board.platform    = exDroid
ro.hardware          = sun5i
ro.product.board     = nuclear
ro.build.fingerprint = softwinners/nuclear_f900/nuclear-f900:4.0.4/IMM76D/20120829:eng/test-keys
```

- **eng / test-keys** build → `adb root` works out of the box.
- `adb reboot bootloader` and `adb reboot efex` just reboot normally on this unit (not supported).
- The board matches the **"Q8 A13 tablet"** profile in mainline U-Boot and Linux.

## Hardware map

| Component | Details (from script.fex / dmesg) | Mainline |
|---|---|---|
| SoC | Allwinner **A13** (sun5i), Cortex-A8 | ✅ |
| GPU | Mali-400 | ✅ `lima` |
| RAM | 512 MB DDR3, 432 MHz (fex), 16-bit bus, ZQ 0x7b, EMR1 0x4 | ✅ |
| PMIC | **AXP209** (chip ID 0x21) on I2C0 @ 0x34 | ✅ `axp20x` |
| Display | 800×480 parallel RGB, 18-bit (`lcd_frm=1`), 33 MHz | ✅ `sun4i-drm` |
| LCD power | `power0` → **AXP GPIO0** | ✅ |
| Backlight enable | `power1` → **AXP GPIO1** | ✅ |
| Backlight PWM | **PB2** (PWM0) | ✅ |
| Touchscreen | **Goodix GT82x** on I2C1 (PB15/PB16) @ 0x5d, INT **PG11**, reset **PB3**, X/Y swapped, Y inverted | ❌ needs porting (`gt82x_up.ko` in Android) |
| Wi-Fi | Realtek **`0bda:018a`** (RTL8188C family) on `usbc1`, powered by AXP LDO3 | ✅ `rtl8192cu` |
| USB OTG | `usbc0`: ID **PG2**, VBUS detect **PG1**, VBUS drive **PG12** | ✅ |
| Battery | declared 5550 mAh, reads correctly (capacity, voltage) | ✅ |

Note: the fex declares `ctp_name = "ft5x_ts"`, but `dmesg` shows the detected chip is the **gt82x**.

---

## Step 1 — Hardware discovery

```bash
adb root
mkdir a13-info && cd a13-info
adb shell dmesg > dmesg.txt
adb shell lsmod > lsmod.txt
adb shell cat /proc/partitions > partitions.txt
adb shell cat /proc/cpuinfo > cpuinfo.txt

# boot partition (vfat) containing script.bin
adb shell mkdir /data/local/tmp/nanda
adb shell mount -t vfat /dev/block/nanda /data/local/tmp/nanda
adb pull /data/local/tmp/nanda ./nanda
adb shell umount /data/local/tmp/nanda

# original kernel modules
adb pull /system/vendor/modules ./modules

sudo apt install sunxi-tools
bin2fex nanda/script.bin > script.fex
```

Useful fex sections: `[lcd0_para]`, `[ctp_para]`, `[dram_para]`, `[pmu_para]`, `[twi1_para]`,
`[usbc0]`, `[usbc1]`, `[usb_wifi_para]`.

> Before publishing your `dmesg.txt`, redact Wi-Fi SSIDs and MAC addresses.

---

## Step 2 — Mainline U-Boot over FEL

### Getting into FEL mode: microSD "fel-sdboot" card

`adb reboot efex` does not work on this tablet. The reliable way is a microSD card carrying a tiny
image that tells the boot ROM to enter FEL:

```bash
sudo apt install gcc-arm-none-eabi u-boot-tools
git clone https://github.com/linux-sunxi/sunxi-tools.git
cd sunxi-tools
make fel-sdboot.sunxi CROSS_COMPILE=arm-none-eabi-   # builds fel-sdboot.bin, then fails on mksunxiboot
mkimage -T sunxi_egon -d fel-sdboot.bin fel-sdboot.sunxi   # mksunxiboot is gone; mkimage does it
```

Notes:
- `arm-linux-gnueabihf-gcc` fails with `-mfloat-abi=hard: selected architecture lacks an FPU`
  (armv5te). Use `arm-none-eabi-` or add `-mfloat-abi=soft`.
- `mksunxiboot` no longer exists; `mkimage -T sunxi_egon` adds the eGON header.

Write it to a card **with a partition table whose first partition starts at 1 MiB**
(otherwise it overwrites the filesystem):

```bash
lsblk                                   # identify the card!
sudo dd if=fel-sdboot.sunxi of=/dev/sdX bs=1024 seek=8 conv=fsync
sudo dd if=/dev/sdX bs=1024 skip=8 count=1 2>/dev/null | hexdump -C | head -2   # expect eGON.BT0
```

Card in the tablet, USB cable to the PC, press Power → screen stays black:

```bash
lsusb | grep 1f3a          # 1f3a:efe8 Allwinner ... FEL/flashing mode
sudo sunxi-fel version     # AWUSBFEX soc=00001625(A13)
```

Optional udev rule to use `sunxi-fel` without sudo:
```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1f3a", ATTR{idProduct}=="efe8", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/50-sunxi-fel.rules
sudo udevadm control --reload-rules
```

### Build U-Boot

```bash
sudo apt install gcc-arm-linux-gnueabihf bison flex swig python3-dev \
     python3-setuptools python3-pyelftools libssl-dev libgnutls28-dev
git clone --depth 1 https://source.denx.de/u-boot/u-boot.git ~/u-boot
cd ~/u-boot
cp /path/to/this/repo/configs/f900_a13_defconfig configs/
make CROSS_COMPILE=arm-linux-gnueabihf- f900_a13_defconfig
make CROSS_COMPILE=arm-linux-gnueabihf- -j$(nproc)
sudo sunxi-fel -v uboot u-boot-sunxi-with-spl.bin
```

Result: U-Boot console on the LCD ("Model: Q8 A13 Tablet", "DRAM: 512 MiB").
"No ethernet found" is normal (the tablet has none).

In current U-Boot the USB0 VBUS/ID pins come from the device tree, not the defconfig.

### LCD timing conversion (fex → U-Boot, sun5i)

Fex: `lcd_x=800 lcd_y=480 lcd_dclk_freq=33 lcd_ht=1055 lcd_hbp=215 lcd_hv_hspw=48 lcd_vt=1050 lcd_vbp=34 lcd_hv_vspw=3`

| Field | Formula | Value |
|---|---|---|
| pclk_khz | lcd_dclk_freq × 1000 | 33000 |
| hs / vs | lcd_hv_hspw / lcd_hv_vspw (min 1) | 48 / 3 |
| le | lcd_hbp − hs | 167 |
| ri | lcd_ht − lcd_x − lcd_hbp | 40 |
| up | lcd_vbp − vs | 31 |
| lo | lcd_vt/2 − lcd_y − lcd_vbp | 11 |
| depth | lcd_frm = 1 → 18-bit | 18 |

---

## Step 3 — Mainline kernel

### Kernel config

`sunxi_defconfig` lacks several things this tablet needs:

```bash
git clone --depth 1 https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git ~/linux
cd ~/linux
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- sunxi_defconfig
./scripts/kconfig/merge_config.sh -m .config /path/to/this/repo/configs/kernel-a13-tablet.config
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- olddefconfig
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- -j$(nproc) zImage dtbs modules
```

| Option | Why |
|---|---|
| `FB_SIMPLE`, `FRAMEBUFFER_CONSOLE` | without them the screen freezes on "Starting kernel ..." |
| `PINCTRL_AXP209` | AXP GPIOs: backlight enable and LCD power; without it DRM never binds |
| `USB_G_SERIAL`, `U_SERIAL_CONSOLE` | console over the OTG cable (`/dev/ttyACM0` on the PC) |
| `BLK_DEV_INITRD` | initramfs debugging shell |
| Wi-Fi options | RTL8188C family as modules |
| `CGROUPS`, `AUTOFS_FS`, `FHANDLE`, ... | required by systemd (Step 4) |

### Minimal initramfs (BusyBox) for debugging

```bash
git clone --depth 1 https://git.busybox.net/busybox ~/busybox
cd ~/busybox
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- defconfig
sed -i 's/# CONFIG_STATIC is not set/CONFIG_STATIC=y/' .config
sed -i 's/CONFIG_TC=y/# CONFIG_TC is not set/' .config      # tc fails with new kernel headers
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- -j$(nproc)
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- CONFIG_PREFIX=$HOME/a13-boot/initramfs install

cd ~/a13-boot/initramfs
mkdir -p proc sys dev tmp
cat > init <<'EOF'
#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev
echo "=== A13 Linux alive ==="
while true; do setsid cttyhack sh; done
EOF
chmod +x init
find . | cpio -o -H newc | gzip > ../initramfs.cpio.gz
cd ~/a13-boot
mkimage -A arm -T ramdisk -C none -d initramfs.cpio.gz uInitrd
```

### FEL boot script (`~/a13-boot/fel-boot.sh`)

```bash
#!/bin/bash
set -e
cd ~/a13-boot

cat > boot.cmd <<'CMD'
setenv bootargs console=tty0 console=ttyGS0,115200 loglevel=8 ignore_loglevel panic=0 clk_ignore_unused rdinit=/init
fdt addr 0x43000000
fdt resize
fdt set /soc/usb@1c13000 dr_mode peripheral
bootz 0x42000000 0x43300000 0x43000000
CMD

mkimage -C none -A arm -T script -d boot.cmd boot.scr

sudo sunxi-fel -v uboot ~/u-boot/u-boot-sunxi-with-spl.bin \
  write 0x42000000 ~/linux/arch/arm/boot/zImage \
  write 0x43000000 ~/linux/arch/arm/boot/dts/allwinner/sun5i-a13-q8-tablet.dtb \
  write 0x43100000 ~/a13-boot/boot.scr \
  write 0x43300000 ~/a13-boot/uInitrd
```

When booted over FEL, U-Boot runs the script found at `0x43100000`.

### Console on the PC

```bash
ls -l /dev/serial/by-id/            # Linux-USB Serial Gadget → ttyACM0
sudo picocom -b 115200 --imap lfcrlf --logfile ~/a13-boot/a13-console.log /dev/ttyACM0
```

The kernel replays its log when the console attaches; `[missed N bytes]` during the replay is harmless.
Use `dmesg` in the shell for the full log.

### Debugging lessons learned

- **Screen frozen on "Starting kernel ..."** → no framebuffer driver in the kernel (`FB_SIMPLE`).
- **No logs without UART** → slow the boot (`CONFIG_BOOT_PRINTK_DELAY` + `boot_delay=100`) or,
  better, use the USB gadget serial console with an initramfs.
- **Random blackouts when booting without a rootfs**: the kernel panics mid-way through driver
  probing; results of "disable node X" experiments were not reproducible. With an initramfs,
  boot is stable. Always test with a rootfs or initramfs.
- **DRM never binds** (`pwm-backlight: failed to acquire enable GPIO`) → missing `PINCTRL_AXP209`.
- Nodes can be toggled at boot from U-Boot without rebuilding the DTB:
  `fdt addr ...; fdt resize; fdt set /path status disabled`.
- DRM currently binds at ~10 s (deferred probe timeout); harmless, still to investigate.
- **Random freezes after boot on Debian** → `console=ttyGS0` with nothing reading the USB
  serial port; see "USB console" in Step 4.

---

## Step 4 — Debian on microSD (standalone boot)

### Card layout

| Region | Size | Use |
|---|---|---|
| 8 KiB offset (no partition) | ~1 MiB | U-Boot (`u-boot-sunxi-with-spl.bin`) |
| p1 ext4 `a13root` | 8 GiB | Debian rootfs + `/boot` |
| p2 FAT32 `FOTOALBUM` | rest | photos, readable on any PC |

### Build the rootfs (on the PC)

```bash
sudo apt install debootstrap qemu-user-static binfmt-support rsync
sudo debootstrap --arch=armhf --foreign --components=main,non-free-firmware \
  --include=systemd-sysv,udev,kmod,iproute2,iputils-ping,wpasupplicant,firmware-realtek,openssh-server,sudo,nano,ca-certificates,locales \
  trixie ~/a13-rootfs http://deb.debian.org/debian
sudo chroot ~/a13-rootfs /debootstrap/debootstrap --second-stage
```

### Configure it

```bash
sudo chroot ~/a13-rootfs /bin/bash -c '
echo a13-tablet > /etc/hostname
echo "127.0.1.1 a13-tablet" >> /etc/hosts
echo "/dev/mmcblk0p1 / ext4 defaults,noatime 0 1" > /etc/fstab
systemctl enable serial-getty@ttyGS0.service
useradd -m -s /bin/bash -G sudo user
'
sudo chroot ~/a13-rootfs passwd user
```

The minimal debootstrap has **no dbus**, so `systemd-logind` cannot run and login hangs.
Install it (plus time sync) from the PC:

```bash
for d in proc sys dev; do sudo mount --bind /$d ~/a13-rootfs/$d; done
sudo cp /etc/resolv.conf ~/a13-rootfs/etc/resolv.conf
sudo chroot ~/a13-rootfs apt update
sudo chroot ~/a13-rootfs apt install -y dbus libpam-systemd systemd-timesyncd
for d in dev sys proc; do sudo umount ~/a13-rootfs/$d; done
```

### Wi-Fi and SSH (configured from the PC, before first boot)

```bash
sudo sh -c 'wpa_passphrase "YOUR_SSID" "YOUR_PASSWORD" > ~/a13-rootfs/etc/wpa_supplicant/wpa_supplicant-wlan0.conf'
sudo chmod 600 ~/a13-rootfs/etc/wpa_supplicant/wpa_supplicant-wlan0.conf
sudo tee ~/a13-rootfs/etc/systemd/network/25-wlan.network <<'EOF'
[Match]
Name=wlan0

[Network]
DHCP=yes
EOF
echo "nameserver 1.1.1.1" | sudo tee ~/a13-rootfs/etc/resolv.conf
sudo chroot ~/a13-rootfs systemctl enable wpa_supplicant@wlan0 systemd-networkd ssh
```

`net.ifnames=0` in the boot arguments (below) keeps the interface named `wlan0`; otherwise
Debian names it `wlx<mac>` and `wpa_supplicant@wlan0` times out.

### USB console: first boot only, then disable it

> ⚠️ **Do not leave `console=ttyGS0` in the boot arguments.** The last `console=` becomes
> `/dev/console`. With nothing reading on the PC side, a systemd child preparing that TTY gets
> stuck in `gs_close` (`WARNING ... u_serial.c ... gs_close`) **while holding an `flock` on
> `/dev/console`**. PID 1 then blocks on that lock the next time it writes to the console:
> `systemctl` answers "Connection timed out", zombies pile up, the system eventually freezes.
> Diagnosis: `cat /proc/1/wchan` → `locks_lock_inode_wait`; `cat /proc/locks` shows PID 1
> waiting (`->`) on the inode of `/dev/console` held by an `(agetty)` process.

Use the USB console only to get Wi-Fi + SSH working on the first boot (with `picocom` already
open on the PC). Login there did not accept keyboard input on this setup; autologin works:

```bash
sudo mkdir -p ~/a13-rootfs/etc/systemd/system/serial-getty@ttyGS0.service.d
sudo tee ~/a13-rootfs/etc/systemd/system/serial-getty@ttyGS0.service.d/override.conf <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty -L --autologin user 115200 %I $TERM
EOF
```

Once SSH works, switch to the final boot arguments (below) and mask the getty
(systemd auto-creates one for every `console=` on the kernel command line):
```bash
sudo systemctl mask serial-getty@ttyGS0
```

### Kernel, modules and boot script into the rootfs

```bash
cd ~/linux
sudo make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- INSTALL_MOD_PATH=$HOME/a13-rootfs modules_install
sudo cp arch/arm/boot/zImage $HOME/a13-rootfs/boot/
sudo cp arch/arm/boot/dts/allwinner/sun5i-a13-q8-tablet.dtb $HOME/a13-rootfs/boot/

cat > ~/a13-boot/sd-boot.cmd <<'EOF'
setenv bootargs console=tty0 console=ttyGS0,115200 root=/dev/mmcblk0p1 rootwait rw panic=10 net.ifnames=0
load mmc 0:1 ${kernel_addr_r} /boot/zImage
load mmc 0:1 ${fdt_addr_r} /boot/sun5i-a13-q8-tablet.dtb
fdt addr ${fdt_addr_r}
fdt resize
fdt set /soc/usb@1c13000 dr_mode peripheral
bootz ${kernel_addr_r} - ${fdt_addr_r}
EOF
sudo mkimage -C none -A arm -T script -d ~/a13-boot/sd-boot.cmd $HOME/a13-rootfs/boot/boot.scr
```

That is the first-boot version. **Final `/boot/boot.cmd` on the tablet** (no USB console,
clean screen for the photo frame):
```
setenv bootargs console=tty0 root=/dev/mmcblk0p1 rootwait rw panic=10 net.ifnames=0 quiet consoleblank=0 vt.global_cursor_default=0 systemd.show_status=0
load mmc 0:1 ${kernel_addr_r} /boot/zImage
load mmc 0:1 ${fdt_addr_r} /boot/sun5i-a13-q8-tablet.dtb
fdt addr ${fdt_addr_r}
fdt resize
fdt set /soc/usb@1c13000 dr_mode peripheral
bootz ${kernel_addr_r} - ${fdt_addr_r}
```
Rebuild with `sudo mkimage -C none -A arm -T script -d /boot/boot.cmd /boot/boot.scr` and check
with `grep -a bootargs /boot/boot.scr` (`strings` is not installed).

### Partition the card and install

```bash
lsblk                                   # identify the card! (here /dev/sdX)
sudo umount /dev/sdX* 2>/dev/null
sudo wipefs -a /dev/sdX
sudo parted -s /dev/sdX mklabel msdos \
  mkpart primary ext4 1MiB 8GiB \
  mkpart primary fat32 8GiB 100% \
  set 1 boot on
sudo mkfs.ext4 -L a13root /dev/sdX1
sudo mkfs.vfat -F 32 -n FOTOALBUM /dev/sdX2
sudo dd if=$HOME/u-boot/u-boot-sunxi-with-spl.bin of=/dev/sdX bs=1024 seek=8 conv=fsync
sudo mount /dev/sdX1 /mnt
sudo rsync -aHAX $HOME/a13-rootfs/ /mnt/
sudo umount /mnt
sync
```

### Boot

Card in the tablet, press Power. The tablet boots Debian on its own (~1 min).

- USB console (first boot only): `sudo picocom -b 115200 /dev/ttyACM0` (stop `ModemManager`
  on the PC first, it grabs new `ttyACM` devices)
- SSH over Wi-Fi: `ssh user@<tablet-ip>` or `ssh user@a13-tablet.local`

First things to run on the tablet:
```bash
sudo timedatectl set-timezone Europe/Lisbon
sudo timedatectl set-ntp true
```

### Robustness: hardware watchdog and journal

The A13 watchdog (`sunxi-wdt`) has a **16 s maximum**, so use 15 s. If the system hangs, it
reboots by itself; the journal is flushed every 30 s so the cause survives the reboot:
```bash
sudo mkdir -p /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\nRebootWatchdogSec=2min\n' | sudo tee /etc/systemd/system.conf.d/watchdog.conf
printf '[Journal]\nSyncIntervalSec=30s\n' | sudo tee /etc/systemd/journald.conf.d/sync.conf
sudo systemctl reboot
systemctl show -p RuntimeWatchdogUSec        # after boot: 15s
```
If PID 1 does not answer (`Call to Reboot failed: Connection timed out`):
`sudo sync; sudo systemctl reboot -ff`, or `sudo sh -c 'sync; echo b > /proc/sysrq-trigger'`.
Avoid `^C` on `systemctl daemon-reexec` / `systemctl restart`: on a single core they can take
a while; wait for them.

---

## Photo frame, web panel and Wi-Fi from the card

Everything below is installed by [`install.sh`](install.sh). Copy (or `git clone`) this
repository to the tablet and run, **on the tablet**:

```bash
sudo apt install git
git clone https://github.com/<you>/a13-tablet-linux.git && cd a13-tablet-linux
sudo ./install.sh
sudo systemctl reboot
```

It installs the packages (`fbi`, `imagemagick`, `python3`, `avahi-daemon`, `u-boot-tools`),
mounts the FAT32 photo partition on `/srv/fotos`, installs the scripts and services, masks the
USB-console getty, and sets up the hardware watchdog. Running it again keeps the slideshow mode
and interval chosen in the web panel.

| File | Installed to | What it does |
|---|---|---|
| [`frame/moldura.sh`](frame/moldura.sh) | `/usr/local/bin/` | start-up screen with the panel address (once per boot), photo conversion in the background, `fbi` slideshow |
| [`frame/prepara-fotos.sh`](frame/prepara-fotos.sh) | `/usr/local/bin/` | 800×480 copies in `/var/cache/moldura` (`cobrir` = crop to fill, `desfocado` = fit on a blurred background), EXIF rotation, removes copies of deleted photos |
| [`frame/moldura.service`](frame/moldura.service) | `/etc/systemd/system/` | runs the slideshow on `tty1` |
| [`panel/painel.py`](panel/painel.py) | `/usr/local/bin/` | web control panel (Portuguese / English) |
| [`panel/painel.service`](panel/painel.service) | `/etc/systemd/system/` | runs the panel on port 80 |
| [`frame/wifi-cartao.sh`](frame/wifi-cartao.sh) | `/usr/local/bin/` | applies `wifi.txt` from the photo partition at boot |
| [`frame/wifi-cartao.service`](frame/wifi-cartao.service) | `/etc/systemd/system/` | runs it before `wpa_supplicant` |

Photos can be copied from a PC (`rsync -rh ./photos/ user@a13-tablet.local:/srv/fotos/`), put
on the `FOTOALBUM` partition with a card reader, or uploaded from the web panel. New folders are
picked up automatically.

### Web control panel

Without a touch driver the tablet has no on-screen input, so it is controlled from a phone or PC
browser: **http://a13-tablet.local** (or the IP shown on screen at start-up).

- screen on/off and brightness; **schedule** (off/on times, also over midnight)
- next photo, slideshow interval, mode (fill screen / blurred background)
- photo **upload** from the phone (to `/srv/fotos/Enviadas`)
- **gallery**: browse by folder, select and delete photos (original + cached copy)
- status (photos prepared, battery, temperature, uptime, IP), restart and shut down
- **PT / EN** button; the first time it follows the browser language

It uses only the Python standard library. Uploads are streamed to the card in 64 KB chunks: an
earlier version parsed `multipart/form-data` with Python's `email` parser and froze the tablet
with phone-sized photos. The panel has **no password**: fine on a home network, never expose
port 80 to the Internet.

For testing on a PC, all paths can be overridden: `PAINEL_PORTA=8080 PAINEL_FOTOS=./test
PAINEL_CACHE=./cache PAINEL_BACKLIGHT=./bl PAINEL_SCRIPT=./moldura.sh PAINEL_HORARIO=./h.json
PAINEL_SYSTEMCTL=true python3 panel/painel.py`.

### `fbi` gotchas

- `--nointeractive` is required: without a keyboard, `fbi` reads EOF from the console and quits
  after the first image (the screen keeps showing it, so it looks "stuck").
- `fbi` forks into the background, hence `Type=forking` in the service.
- `drm: no dumb buffer support` on `/dev/dri/card0` is harmless (that node is the `lima` GPU);
  `-d /dev/fb0` makes it use the framebuffer.
- `fbi -a` letterboxes and cannot crop, hence the pre-rendered 800×480 copies.
- The start-up screen uses `--timeout 20` rather than `-t 20` on purpose: the panel edits the
  first `-t N` in `moldura.sh` to change the interval.

### Changing Wi-Fi without SSH (`wifi.txt`)

To move house or give the tablet away, create `wifi.txt` on the `FOTOALBUM` partition from any
PC or phone with a card reader:

```
SSID=NetworkName
PASSWORD=password
```

At boot the network is added with priority (existing networks are kept), the file is deleted so
the password does not stay on the card, and `wifi-aplicado.txt` (or `wifi-erro.txt`) is written
as feedback. `SSID=` alone adds an open network. Windows line endings and UTF-8 BOM are handled.

### Before giving the tablet away

- remove your own network from `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`
- change the user password (`passwd`)
- delete your photos and the cache: `sudo rm -rf /srv/fotos/* /var/cache/moldura/*`

The new owner only needs a card reader (`wifi.txt` + photos) and then uses `http://a13-tablet.local`.

---

## Step 5 — Remaining hardware

1. GT82x touchscreen: port `gt82x.c` from the Allwinner 3.0 vendor kernel (linux-sunxi `sunxi-3.0`)
   or add support to mainline `goodix.c`
2. Kernel: add `EXT4_FS_POSIX_ACL` (journald ACL warning)
3. Own DTS (`sun5i-a13-f900.dts`) once touch / accelerometer are known
4. Find out why DRM waits for the 10 s deferred-probe timeout
5. Mesa + `lima` for GPU acceleration

---

## Tips

- **UART:** on the A13, UART0 is muxed on the microSD pins; a "microSD breakout" board plus a 3.3 V
  USB-serial adapter gives a real serial console without opening the tablet (enter FEL with buttons then).
- Keep the `nanda/` backup and your `script.fex`.
- Do not publish vendor binaries (`nanda/`, `modules/*.ko`) or personal data (photos, Wi-Fi names,
  `wpa_supplicant-wlan0.conf`).
- The A13 has a single core: while ImageMagick converts photos, `systemctl` and the web panel
  respond slowly. That is expected.
- Web uploads are streamed straight to the card in 64 KB chunks. An earlier version parsed
  `multipart/form-data` with Python's `email` parser; with phone-sized photos that froze the
  tablet (memory and CPU).

## Contributing

Have a similar A13 tablet? Issues and pull requests are welcome, especially:
- `script.fex` / `dmesg` from other board variants
- working device tree changes
- progress on the GT82x touchscreen driver

## References

- linux-sunxi wiki: https://linux-sunxi.org (A13, FEL, LCD, Fex Guide pages)
- U-Boot: `configs/q8_a13_tablet_defconfig`, `board/sunxi/README.sunxi`
- fex → `CONFIG_VIDEO_LCD_MODE` conversion table (Hans de Goede, U-Boot mailing list, Dec 2014)
- sunxi-tools: https://github.com/linux-sunxi/sunxi-tools

## License

Documentation in this repository is released under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
Code and device tree files follow the license of the upstream project they target (GPL-2.0 for kernel/U-Boot).
