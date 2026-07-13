SUMMARY = "NVDLA userspace runtime"
DESCRIPTION = "NVDLA runtime application and user-mode runtime library"
LICENSE = "BSD-3-Clause & IJG"
LIC_FILES_CHKSUM = "file://LICENSE;md5=1139d824882ec34a56e67bf8e2b55491"

inherit deploy

SRC_URI = "git://github.com/nvdla/sw.git;protocol=https;branch=master"
SRCREV = "79538ba1b52b040a4a4645f630e457fa01839e90"

require nvdla-runtime-patches.inc

S = "${WORKDIR}/git"
UMD_SRC = "${S}/umd"

do_compile() {
    oe_runmake -C ${UMD_SRC} clean
    oe_runmake -C ${UMD_SRC} runtime \
        TOP="${UMD_SRC}" \
        TOOLCHAIN_PREFIX="${TARGET_PREFIX}" \
        NVDLA_CC="${CC}" \
        NVDLA_CXX="${CXX}" \
        NVDLA_LD="${LD}" \
        NVDLA_CFLAGS="${CFLAGS} ${CPPFLAGS}" \
        NVDLA_CXXFLAGS="${CXXFLAGS} ${CPPFLAGS}" \
        NVDLA_LDFLAGS="${LDFLAGS}" \
        RUNTIME_LDFLAGS="-no-pie" \
        RUNTIME_RPATH=""
}

do_install() {
    install -d ${D}${bindir} ${D}${libdir}
    install -m 0755 ${UMD_SRC}/out/apps/runtime/nvdla_runtime/nvdla_runtime \
        ${D}${bindir}/nvdla_runtime
    install -m 0755 ${UMD_SRC}/out/core/src/runtime/libnvdla_runtime/libnvdla_runtime.so \
        ${D}${libdir}/libnvdla_runtime.so
}

do_deploy() {
    install -d ${DEPLOYDIR}
    install -m 0755 ${D}${bindir}/nvdla_runtime ${DEPLOYDIR}/nvdla_runtime
    install -m 0755 ${D}${libdir}/libnvdla_runtime.so ${DEPLOYDIR}/libnvdla_runtime.so
}
addtask deploy after do_install before do_build

# Upstream provides an ABI library without a versioned SONAME. Package the
# real .so in the runtime package rather than treating it as a development link.
SOLIBS = ".so"
FILES_SOLIBSDEV = ""
FILES:${PN} += "${libdir}/libnvdla_runtime.so"
RDEPENDS:${PN} += "libstdc++"
