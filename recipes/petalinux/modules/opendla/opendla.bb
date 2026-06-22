SUMMARY = "NVDLA kernel mode driver"
DESCRIPTION = "Out-of-tree NVDLA KMD built against the PetaLinux kernel"
LICENSE = "CLOSED"

inherit module

SRC_URI = "git://github.com/nvdla/sw.git;protocol=https;branch=master"
SRCREV = "79538ba1b52b040a4a4645f630e457fa01839e90"

S = "${WORKDIR}/git/kmd/port/linux"

EXTRA_OEMAKE += "KDIR=${STAGING_KERNEL_DIR}"
EXTRA_OEMAKE += "ARCH=${ARCH}"

do_compile() {
    oe_runmake
}

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra
    install -m 0644 ${S}/opendla.ko ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/opendla.ko
}

FILES:${PN} += "${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/opendla.ko"

