# PetaLinux `opendla` Recipe Skeleton

`scripts/petalinux_kmod.sh` copies this directory into:

```text
project-spec/meta-user/recipes-modules/opendla/
```

The recipe fetches the pinned upstream `nvdla/sw` revision and builds the KMD as an out-of-tree kernel module. Forward-port patches should be added beside the recipe and appended to `SRC_URI` in commit-sized steps.

Keep the ioctl header used by UMD and KMD identical. The framework's ABI checks are designed to catch accidental divergence before runtime tests are run.

