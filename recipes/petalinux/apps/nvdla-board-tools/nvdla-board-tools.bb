SUMMARY = "NVDLA board bring-up and GEM smoke tools"
DESCRIPTION = "Controlled probe evidence collector and NVDLA DRM/GEM smoke utility"
LICENSE = "BSD-3-Clause"
LIC_FILES_CHKSUM = "file://LICENSE;md5=1139d824882ec34a56e67bf8e2b55491"

inherit deploy

SRC_URI = " \
    git://github.com/nvdla/sw.git;protocol=https;branch=master \
    file://nvdla-kmd-smoke.c \
    file://nvdla-board-check \
    file://serial-root-autologin.conf \
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
}

do_install() {
    install -d ${D}${bindir}
    install -d ${D}${sysconfdir}/systemd/system/serial-getty@ttyPS0.service.d
    install -m 0755 ${B}/nvdla-kmd-smoke ${D}${bindir}/nvdla-kmd-smoke
    install -m 0755 ${WORKDIR}/nvdla-board-check ${D}${bindir}/nvdla-board-check
    install -m 0644 ${WORKDIR}/serial-root-autologin.conf \
        ${D}${sysconfdir}/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf
}

do_deploy() {
    install -d ${DEPLOYDIR}
    install -m 0755 ${D}${bindir}/nvdla-kmd-smoke ${DEPLOYDIR}/nvdla-kmd-smoke
    install -m 0755 ${D}${bindir}/nvdla-board-check ${DEPLOYDIR}/nvdla-board-check
}
addtask deploy after do_install before do_build

FILES:${PN} += "${sysconfdir}/systemd/system/serial-getty@ttyPS0.service.d/autologin.conf"
