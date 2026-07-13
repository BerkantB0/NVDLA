# NVDLA PetaLinux Image Package Selection

This append includes the NVDLA kernel module and userspace runtime in
`petalinux-image-minimal`. It intentionally does not autoload the module or
start the runtime so initial board bring-up remains explicit.
