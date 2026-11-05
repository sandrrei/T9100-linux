# Out-of-tree build of the GT827 touchscreen driver against your kernel tree.
#   make            -> gt827.ko
#   make clean
KDIR          ?= $(HOME)/linux
ARCH          ?= arm
CROSS_COMPILE ?= arm-linux-gnueabihf-

obj-m := gt827.o

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) ARCH=$(ARCH) CROSS_COMPILE=$(CROSS_COMPILE) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) ARCH=$(ARCH) CROSS_COMPILE=$(CROSS_COMPILE) clean
