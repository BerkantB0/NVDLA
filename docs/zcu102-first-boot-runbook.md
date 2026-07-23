# ZCU102 First Board Bring-Up

## Scope

This procedure performs controlled hardware validation of the PetaLinux 2024.1
NVDLA image. The first three gates cover boot, KMD probe, render-node creation,
and DRM/GEM mapping. Later gates submit an SDP diagnostic and LeNet in stages
so a hardware or integration failure can be localized.

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
make petalinux-board-payload
```

The SD-bundle target does not access removable media. It writes:

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

The payload target separately writes:

```text
artifacts/<run-id>-petalinux-board-payload/
|-- manifest.json
|-- nvdla-tests.tar.gz
`-- nvdla-tests/
    |-- PAYLOAD.json
    |-- SHA256SUMS
    |-- sdp_regression_small/
    `-- lenet_small/
```

## Prepare The SD Card

1. Use a card whose existing contents may be erased.
2. Create a primary FAT32 partition using the operating system's normal disk
   management tool.
3. Copy `BOOT.BIN`, `boot.scr`, and `image.ub` from the generated `sd-card/`
   directory into the root of that FAT32 partition.
4. Copy the complete generated `nvdla-tests` directory into the FAT32 root.
5. Optionally copy the boot `SHA256SUMS` and verify all three boot files.
6. Eject the card cleanly.
7. With board power off, insert the card and set the ZCU102 boot-mode switches
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

For a direct Ethernet link, the board image uses the locally administered MAC
address `02:00:00:50:10:02`. The board DT fragment identifies the ZCU102
DP83867 PHY at MDIO address 12 and applies the Xilinx Rev1.0/Rev1.1 RGMII delay
settings. A boot log that reports `Generic PHY`, an invalid/random MAC address,
or increasing receive errors is a device-tree integration failure.

The board-tools package installs a persistent direct-link profile for `eth0`:

```text
board: 192.168.50.2/24
host:  192.168.50.1/24
```

The profile deliberately contains no gateway or DNS server. Configure the host
address on the dedicated wired adapter and keep normal internet routing on a
separate interface. Do not attach multiple images using the fixed MAC address
to the same Ethernet segment.

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

## Gate 4: Mount The Workload Payload

Use a fresh boot. Identify the FAT boot partition rather than assuming its
device name:

```sh
lsblk -f
mkdir -p /mnt/sdboot
mount -o ro /dev/mmcblk0p1 /mnt/sdboot
cd /mnt/sdboot/nvdla-tests
sha256sum -c SHA256SUMS
```

Replace `/dev/mmcblk0p1` if `lsblk` reports a different FAT partition. Keep the
mount read-only. Workload outputs and evidence are written only under `/tmp`.

## Gate 5: SDP Execution Diagnostic

Run:

```sh
nvdla-board-workload sdp /mnt/sdboot/nvdla-tests
```

This starts `nvdla_runtime -s`, submits the pinned flatbuffer through the C
client, and checks protocol completion, task initiation, IRQ activity, SDP
completion, output retrieval, and bad kernel patterns.

An exact golden match is a full correctness pass. A successfully retrieved
all-zero DIMG payload is reported as
`diagnostic-pass-oracle-inconclusive`: it proves execution progress but not
tensor correctness. No output, no IRQ, timeout, unexpected mismatch, or kernel
error is a hard failure.

Retrieve the printed archive before continuing. Power-cycle the board after
this gate whether it passes or fails.

## Gate 6: Single LeNet Correctness

After a fresh boot and read-only payload mount, run:

```sh
nvdla-board-workload lenet /mnt/sdboot/nvdla-tests
```

Pass requires an increased NVDLA IRQ count, these ten ordered completions,
runtime exit zero, and exact output `0 2 0 0 0 0 0 124 0 0`:

```text
Convolution 0
SDP 1
PDP 2
Convolution 3
SDP 4
PDP 5
Convolution 6
SDP 7
Convolution 8
SDP 9
```

Retrieve the archive and power-cycle after the gate.

## Gate 7: Repeat Stability

Only after a clean single run, use a fresh boot for 10 repeats:

```sh
REPEAT=10 RUNTIME_TIMEOUT=120 \
  nvdla-board-workload lenet /mnt/sdboot/nvdla-tests
```

Only after all ten pass, use another fresh boot for 100 repeats:

```sh
REPEAT=100 RUNTIME_TIMEOUT=120 \
  nvdla-board-workload lenet /mnt/sdboot/nvdla-tests
```

The module and programmable logic are deliberately not reset between repeats.
This exposes stale state, interrupt clearing, scheduler cleanup, and retained
accelerator-state defects. The runner stops on the first failure.

## Failure Handling

Do not run a later gate after a timeout or failure. Retrieve its archive, save
the complete UART log, and power-cycle. Do not unload/reload the production
driver or write registers through `/dev/mem` as part of normal validation.

The archive importer classifies the first failing stage:

- runtime start or dependency failure;
- task initiation failure;
- initiation without IRQ;
- IRQ without completion handling;
- partial operation sequence, including last completed and next expected;
- completed operations with missing output;
- completed operations with wrong output;
- repeat-only state retention or cleanup failure;
- kernel-log failure.

Import a UART-retrieved archive from the host with:

```sh
BOARD_ARCHIVE_LOCAL=/path/to/nvdla-board-<mode>-<timestamp>.tar.gz \
BOARD_SERIAL_LOG=/path/to/full-serial.log \
make petalinux-board-collect
```

The host writes `manifest.json` and, for runtime gates,
`workload-analysis.json`. A diagnostic driver is considered only after this
normal production-driver evidence remains ambiguous; it is never accepted as
production correctness evidence.
