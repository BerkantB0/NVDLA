SUMMARY = "ONNX Runtime CPU validation and performance tools"
DESCRIPTION = "Standard ONNX Runtime test runner, performance test, and shared library"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=0f7e3b1308cb5c00b372a6e78835732d"

inherit deploy

SRC_URI = " \
    file://LICENSE \
    file://libonnxruntime.so.1.18.1 \
    file://onnx_test_runner \
    file://onnxruntime_perf_test \
"

S = "${WORKDIR}"

do_compile[noexec] = "1"

do_install() {
    install -d ${D}${bindir} ${D}${libdir}
    install -m 0755 ${WORKDIR}/onnx_test_runner ${D}${bindir}/onnx_test_runner
    install -m 0755 ${WORKDIR}/onnxruntime_perf_test ${D}${bindir}/onnxruntime_perf_test
    install -m 0755 ${WORKDIR}/libonnxruntime.so.1.18.1 \
        ${D}${libdir}/libonnxruntime.so.1.18.1
    ln -s libonnxruntime.so.1.18.1 ${D}${libdir}/libonnxruntime.so.1
}

do_deploy() {
    install -d ${DEPLOYDIR}
    install -m 0755 ${D}${bindir}/onnx_test_runner ${DEPLOYDIR}/onnx_test_runner
    install -m 0755 ${D}${bindir}/onnxruntime_perf_test ${DEPLOYDIR}/onnxruntime_perf_test
    install -m 0755 ${D}${libdir}/libonnxruntime.so.1.18.1 \
        ${DEPLOYDIR}/libonnxruntime.so.1.18.1
}
addtask deploy after do_install before do_build

FILES:${PN} += "${libdir}/libonnxruntime.so.1"
RDEPENDS:${PN} += "libgcc libstdc++"
