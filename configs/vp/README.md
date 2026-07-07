# VP Build Configuration

This directory contains reproducible configuration inputs for the modern VP lane.

- `linux.fragment` lists kernel options needed by the NVDLA KMD/runtime path.
- `nvdla-vp-modern.dtsi` captures the VP NVDLA node derived from the stock `aarch64_nvdla.lua`.
- `nvdla-vp-modern-extmem-pool.dts` is the stock `nvdla/vp:latest` QEMU `virt`
  DTB source with the NVDLA node attached to a no-map `shared-dma-pool` in the
  VP extmem aperture at `0xc0000000`.
- `buildroot_external/` provides a minimal Buildroot external tree for a test rootfs.

The stock VP reports NVDLA CSB at `0x10200000..0x1021ffff` and IRQ 176. In a Linux GIC interrupt specifier this normally maps to SPI `144` because SPI numbering is offset by 32. Treat the generated final DTS as the source of truth and verify with boot logs.

Build the extmem DTB with:

```sh
WORK_DIR=$HOME/build/nvdla-peta/vp-modern make vp-extmem-dtb
```
