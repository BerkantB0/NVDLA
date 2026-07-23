SUMMARY = "ZCU102 NVDLA staged bring-up and runtime tools"
DESCRIPTION = "Controlled probe, GEM, flatbuffer, and direct-link board tools"
LICENSE = "BSD-3-Clause"
LIC_FILES_CHKSUM = "file://LICENSE;md5=1139d824882ec34a56e67bf8e2b55491"

inherit deploy

SRC_URI = " \
    git://github.com/nvdla/sw.git;protocol=https;branch=master \
    file://nvdla-kmd-smoke.c \
    file://nvdla-flatbuf-client.c \
    file://nvdla-board-check \
    file://nvdla-board-workload \
    file://serial-root-autologin.conf \
    file://20-nvdla-direct.network \
"
SRCREV = "79538ba1b52b040a4a4645f630e457fa01839e90"

require nvdla-board-tools-patches.inc

S = "${WORKDIR}/git"

do_compile() {
    ${CC} ${CPPFLAGS} ${CFLAGS} ${DEBUG_PREFIX_MAP} -g0 \
        -I${S}/kmd/port/linux/include \
        ${WORKDIR}/nvdla-kmd-smoke.c \
        ${LDFLAGS} \
        -o ${B}/nvdla-kmd-smoke
    ${CC} ${CPPFLAGS} ${CFLAGS} ${DEBUG_PREFIX_MAP} -g0 \
        ${WORKDIR}/nvdla-flatbuf-client.c \
        ${LDFLAGS} \
        -o ${B}/nvdla-flatbuf-client
}

do_install() {
    install -d ${D}${bindir}
    install -d ${D}${sysconfdir}/systemd/system/serial-getty@ttyPS0.service.d
    install -d ${D}${sysconfdir}/systemd/network
    install -m 0755 ${B}/nvdla-kmd-smoke ${D}${bindir}/nvdla-kmd-smoke
    install -m 0755 ${B}/nvdla-flatbuf-client ${D}${bindir}/nvdla-flatbuf-client
    install -m 0755 ${WORKDIR}/nvdla-board-check ${D}${bindir}/nvdla-board-check
    install -m 0755 ${WORKDIR}/nvdla-board-workload ${D}${bindir}/nvdla-board-workload
    install -m 0644 ${WORKDIR}/serial-root-autologin.conf \
        ${D}${sysconfdir}/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf
    install -m 0644 ${WORKDIR}/20-nvdla-direct.network \
        ${D}${sysconfdir}/systemd/network/20-nvdla-direct.network
}

do_deploy() {
    install -d ${DEPLOYDIR}
    install -m 0755 ${D}${bindir}/nvdla-kmd-smoke ${DEPLOYDIR}/nvdla-kmd-smoke
    install -m 0755 ${D}${bindir}/nvdla-flatbuf-client ${DEPLOYDIR}/nvdla-flatbuf-client
    install -m 0755 ${D}${bindir}/nvdla-board-check ${DEPLOYDIR}/nvdla-board-check
    install -m 0755 ${D}${bindir}/nvdla-board-workload ${DEPLOYDIR}/nvdla-board-workload
}
addtask deploy after do_install before do_build

FILES:${PN} += " \
    ${sysconfdir}/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf \
    ${sysconfdir}/systemd/network/20-nvdla-direct.network \
"
