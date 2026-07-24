SUMMARY = "Failure-only NVDLA KMD diagnostic module"
DESCRIPTION = "NVDLA KMD with local buffer and CSB access tracing"
LICENSE = "CLOSED"

inherit module deploy

SRC_URI = "git://github.com/nvdla/sw.git;protocol=https;branch=master"
SRCREV = "79538ba1b52b040a4a4645f630e457fa01839e90"

require opendla-diagnostic-patches.inc

S = "${WORKDIR}/git"
KMD_SRC = "${S}/kmd/port/linux"
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

EXTRA_OEMAKE += "KDIR=${STAGING_KERNEL_DIR}"
EXTRA_OEMAKE += "ARCH=${ARCH}"
EXTRA_OEMAKE += "NVDLA_HW_CONFIG=small"
EXTRA_OEMAKE += "NVDLA_KMD_TRACE=1"
EXTRA_OEMAKE += 'KCFLAGS="-ffile-prefix-map=${S}=nvdla-sw -fmacro-prefix-map=${S}=nvdla-sw"'

do_compile() {
    oe_runmake -C ${STAGING_KERNEL_DIR} M=${KMD_SRC} modules
}

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra
    install -m 0644 ${KMD_SRC}/opendla.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/opendla-diagnostic.ko
}

do_deploy() {
    install -m 0644 ${KMD_SRC}/opendla.ko ${DEPLOYDIR}/opendla-diagnostic.ko
}

addtask deploy after do_compile before do_build
