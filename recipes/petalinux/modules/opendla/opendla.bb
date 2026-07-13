SUMMARY = "NVDLA kernel mode driver"
DESCRIPTION = "Out-of-tree NVDLA KMD built against the PetaLinux kernel"
LICENSE = "CLOSED"

inherit module

SRC_URI = "git://github.com/nvdla/sw.git;protocol=https;branch=master"
SRCREV = "79538ba1b52b040a4a4645f630e457fa01839e90"

require opendla-patches.inc

S = "${WORKDIR}/git"
KMD_SRC = "${S}/kmd/port/linux"
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

NVDLA_HW_CONFIG ?= "small"

EXTRA_OEMAKE += "KDIR=${STAGING_KERNEL_DIR}"
EXTRA_OEMAKE += "ARCH=${ARCH}"
EXTRA_OEMAKE += "NVDLA_HW_CONFIG=${NVDLA_HW_CONFIG}"
EXTRA_OEMAKE += 'KCFLAGS="-ffile-prefix-map=${S}=nvdla-sw -fmacro-prefix-map=${S}=nvdla-sw"'

do_compile() {
    oe_runmake -C ${STAGING_KERNEL_DIR} M=${KMD_SRC} modules
}

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra
    install -m 0644 ${KMD_SRC}/opendla.ko ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/opendla.ko
}
