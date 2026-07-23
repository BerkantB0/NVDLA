# ZCU102 First Board Bring-Up

## Scope

This procedure performs the first controlled hardware validation of the
PetaLinux 2024.1 NVDLA image. It stops after boot, KMD probe, render-node
creation, and DRM/GEM allocation and mapping. It does not submit an accelerator
task or run LeNet.

The image is specific to the ZCU102 and the checked-in
`NVDLA_FPGA_wrapper.xsa`. Do not boot it on another board.

## Build And Handoff

Run the complete host sequence in Ubuntu-22.04 WSL:

```sh
export PETALINUX_DIR=/opt/pkg/petalinux/2024.1
export PETALINUX_PROJECT=${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}

make unit
make patch-check
make abi-check
make petalinux-project
make petalinux-dts
NVDLA_KMD_CONFIG=small make petalinux-kmod
make petalinux-runtime
make petalinux-board-tools
make petalinux-image
make petalinux-rootfs-audit
make petalinux-package
make petalinux-sd-bundle
```

The last target does not access removable media. It writes:

```text
artifacts/<run-id>-petalinux-sd-bundle/
|-- manifest.json
|-- nvdla-zcu102-sd.tar.gz
`-- sd-card/
    |-- BOOT.BIN
    |-- boot.scr
    |-- image.ub
    |-- SD-BUNDLE.json
    `-- SHA256SUMS
```

Only `BOOT.BIN`, `boot.scr`, and `image.ub` are required by U-Boot. The other
two files are evidence for checking the copy.

## Prepare The SD Card

1. Use a card whose existing contents may be erased.
2. Create a primary FAT32 partition using the operating system's normal disk
   management tool.
3. Copy `BOOT.BIN`, `boot.scr`, and `image.ub` from the generated `sd-card/`
   directory into the root of that FAT32 partition.
4. Optionally copy `SHA256SUMS` and verify all three files after copying.
5. Eject the card cleanly.
6. With board power off, insert the card and set the ZCU102 boot-mode switches
   to SD boot using the board manual and silkscreen.

Do not copy the containing `sd-card` directory as a subdirectory. The three
boot files must be at the FAT filesystem root.

## Connect The Board

Connect the ZCU102 power supply and USB-UART cable. Ethernet is strongly
recommended for retrieving evidence before power-off. JTAG is optional for the
first gate.

Open the ZCU102 serial port with:

```text
115200 baud
8 data bits
no parity
1 stop bit
no flow control
```

Start terminal logging before applying power. The bring-up image automatically
logs `root` in on `ttyPS0`. This is a serial-only lab override installed by the
`nvdla-board-tools` package; it does not configure an empty SSH password. Remove
the override from any deployment image.

## Gate 1: Boot And Device Tree

Do not load the driver manually. Run:

```sh
nvdla-board-check preflight
```

The command prints an artifact path such as:

```text
NVDLA_BOARD_ARTIFACT=/tmp/nvdla-board-preflight-<timestamp>.tar.gz
```

Pass criteria:

- Linux reaches the root shell without an exception or hang.
- `/proc/device-tree/nvdla@a0000000` exists.
- Its compatible string is `nvidia,nv_small`.
- Its `reg`, `interrupt-parent`, `interrupts`, and `status` properties exist.
- `opendla` is not loaded automatically.
- Runtime, library, smoke utility, and collector hashes are captured.

Stop after a failed preflight. Preserve the complete UART log and artifact
before changing the image or device tree.

## Gate 2: Manual Driver Probe

Run:

```sh
nvdla-board-check probe
```

This executes `modprobe opendla`, then records module information, device-tree
properties, `/dev/dri`, interrupts, and the kernel-log delta.

Pass criteria:

- The driver selects `nvidia,nv_small`.
- The CSB resource is `0xa0000000..0xa000ffff`.
- IRQ registration succeeds. The Linux virtual IRQ need not numerically equal
  the device-tree SPI value `89`.
- The platform device is bound to the NVDLA driver.
- `/dev/dri/renderD*` exists.
- There is no Oops, BUG, WARNING, DMA-API report, SError, external abort, or
  scheduler/interrupt timeout.

Do not run the GEM smoke test if probe fails.

`modprobe` returning zero is not sufficient by itself: a platform module can
load successfully even when probing its device fails. The gate therefore
requires both the driver-binding symlink and a DRM render node.

## Gate 3: DRM/GEM Smoke

Run:

```sh
nvdla-board-check smoke
```

This loads the module if needed and runs `nvdla-kmd-smoke`. The utility opens
the render node, creates one 4096-byte GEM buffer, obtains its mmap offset, maps
it, verifies a deterministic byte pattern, unmaps it, and destroys the handle.
It does not program NVDLA or wait for an accelerator interrupt.

Pass criteria:

- `nvdla-kmd-smoke` exits zero.
- The output contains `GEM mmap read/write check passed`.
- The kernel-log delta contains no bad pattern.

## Retrieve Evidence

Each board command creates a timestamped archive and updates:

```text
/tmp/nvdla-board-latest.tar.gz
```

The root filesystem is an initramfs, so retrieve every required archive and the
terminal log before powering off.

For SSH retrieval, first assign or obtain an IP address and set a temporary
password for the `petalinux` account from the serial root shell if required:

```sh
passwd petalinux
ip address
```

Then, from the repository:

```sh
BOARD_HOST=<board-ip> \
BOARD_USER=petalinux \
make petalinux-board-collect
```

To import an archive copied by another method:

```sh
BOARD_ARCHIVE_LOCAL=/path/to/nvdla-board-smoke-<timestamp>.tar.gz \
BOARD_SERIAL_LOG=/path/to/full-serial.log \
make petalinux-board-collect
```

The importer rejects unsafe archive paths, preserves target pass/fail status,
copies the serial log when supplied, and writes a normal artifact
`manifest.json`.

## After GEM Smoke

Do not interpret a passing GEM smoke as inference correctness. It proves
platform probe and CPU-side GEM allocation/mapping, but not NVDLA DBB access,
physical interrupt delivery, or non-coherent HP0 DMA correctness.

The next hardware gate is the pinned `nv_small` LeNet workload:

1. Transfer the generated loadable and `seven.pgm`.
2. Record `/proc/interrupts` before execution.
3. Run `nvdla_runtime` against the discovered render node.
4. Require all ten hardware layers to complete.
5. Require output `0 2 0 0 0 0 0 124 0 0`.
6. Repeat 10 times, then 100 times, only after one clean pass.
