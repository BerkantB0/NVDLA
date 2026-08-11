# Lab bring-up image: keep module loading and accelerator execution manual.
IMAGE_INSTALL:append = " opendla nvdla-runtime nvdla-board-tools onnxruntime-cpu-tools gstreamer1.0 gstreamer1.0-plugins-base-videoconvert gstreamer1.0-plugins-good-jpeg gstreamer1.0-plugins-bad-jpegformat"

# Deliberately public credential for the isolated, test-only board image.
inherit extrausers
NVDLA_TEST_ROOT_PASSWORD_HASH = "\$6\$nvdlatest\$Bt1voKTDGyA6E/Kr.2BRpnPder7XkMw6TzrTWhHAl7ZT/4QwePA2i05NlLe.XMHjw/oVBFznvoIPxc9eF1rBN0"
EXTRA_USERS_PARAMS = "usermod -p '${NVDLA_TEST_ROOT_PASSWORD_HASH}' root;"
