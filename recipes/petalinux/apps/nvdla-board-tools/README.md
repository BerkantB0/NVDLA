# NVDLA Board Tools Recipe

This skeleton is copied into the generated PetaLinux `meta-user` layer by
`make petalinux-board-tools`.

The build script adds the complete pinned `patches/nvdla-sw/` queue and copies
the tracked `nvdla-kmd-smoke.c` and `nvdla-board-check` sources into the
recipe's `files/` directory. Generated recipe copies and build outputs remain
outside Git.
