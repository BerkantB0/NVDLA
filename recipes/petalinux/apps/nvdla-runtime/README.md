# PetaLinux `nvdla-runtime` Recipe

`scripts/petalinux_runtime.sh` copies this recipe into the generated
PetaLinux project's `meta-user` layer, adds the complete pinned NVDLA patch
queue, and builds the userspace runtime with Yocto's target compiler and
sysroot flags.

The package installs `nvdla_runtime` in `/usr/bin` and
`libnvdla_runtime.so` in `/usr/lib`. It does not install the model compiler,
start a service, or load `opendla.ko` automatically.
