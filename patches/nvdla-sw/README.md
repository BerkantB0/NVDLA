# NVDLA Software Patch Queue

This directory stores upstream-style patches against pinned upstream
`nvdla/sw` commit `79538ba1b52b040a4a4645f630e457fa01839e90`.

Rules:

- Keep `.external/sources/nvdla-sw` pristine.
- Use `.work/nvdla-sw-patched` for editable driver/runtime work.
- Generate patches with `make patch-format`.
- Verify patches with `make patch-check`.
- Keep board-specific PetaLinux, VP, and XSA details out of these patches.

The goal is that these patches can later be applied unchanged to a maintained
fork of `nvdla/sw`.

