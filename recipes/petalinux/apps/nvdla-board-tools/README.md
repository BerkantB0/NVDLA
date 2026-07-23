# NVDLA Board Tools Recipe

This skeleton is copied into the generated PetaLinux `meta-user` layer by
`make petalinux-board-tools`.

The build script adds the complete pinned `patches/nvdla-sw/` queue and copies
the tracked `nvdla-kmd-smoke.c` and `nvdla-board-check` sources into the
recipe's `files/` directory. Generated recipe copies and build outputs remain
outside Git.

This recipe also installs `20-nvdla-direct.network`. That profile is specific
to this ZCU102 NVDLA bring-up image: board MAC `02:00:00:50:10:02`, board
address `192.168.50.2/24`, and expected directly connected host address
`192.168.50.1/24`. It is not a generic NVDLA runtime requirement. Replace or
omit it for routed networks, multiple boards on one Ethernet segment, other
board models, or production deployment.
