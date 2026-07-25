# ResNet-50 `nv_small` Board Gate

## Model selection

The test uses the original Caffe ResNet-50 published by Kaiming He in
[`deep-residual-networks`](https://github.com/KaimingHe/deep-residual-networks).
This is the strongest fit for the current stack:

- NVDLA's pinned [`LowPrecision.md`](https://github.com/nvdla/sw/blob/79538ba1b52b040a4a4645f630e457fa01839e90/LowPrecision.md)
  explicitly says its bundled ResNet-50 calibration table is for this model
  and can run it on NVDLA INT8.
- The [NVDLA compiler](https://nvdla.org/sw/compilation_tool.html) consumes
  Caffe models directly.
- NVIDIA publishes ResNet-50 performance estimates in the
  [NVDLA Primer](https://nvdla.org/primer.html).

`repro.lock.json` pins the model repository revision, deploy prototxt, original
Caffe sample image, NVDLA calibration table, Docker image identity, and every
file hash. The original model download is no longer reliably available from
its historical OneDrive URL, so the weights are fetched from a stable mirror
and verified against the published model SHA-1 as well as this project's
SHA-256.

## Build

Run from Ubuntu-22.04 WSL:

```sh
export SOURCES_DIR=$HOME/src/nvdla-peta-sources
make sources-resnet50
make vp-resnet50-small-workload
make petalinux-board-payload
```

The workload builder:

1. verifies the original model, prototxt, input image, and NVDLA calibration;
2. resizes the image's short side to 256 and takes a 224x224 center crop;
3. compiles with the pinned stock NVDLA compiler using:

   ```text
   --profile fast-math
   --cprecision int8
   --configtarget nv_small
   --quantizationMode per-kernel
   --informat nchw
   ```

4. verifies the generated loadable and compiler protobuf hashes;
5. records a host FP32 top-five result as contextual evidence.

The expected loadable is 25,765,680 bytes with SHA-256:

```text
1BE9C27E7D9069547A65B96368955815DC917F82E4CFFC1483435F1425C25A34
```

Copy the newly generated `nvdla-tests` directory to the FAT partition as for
the LeNet gate. Rebuild and copy the PetaLinux image first if
`nvdla-board-workload` on the board does not list `resnet50` in its usage.

## Board execution

Use a fresh boot and run:

```sh
nvdla-board-workload resnet50 \
  /run/media/ROOT-mmcblk0p1/nvdla-tests
```

The default watchdog is 180 seconds. Override it conservatively when needed:

```sh
RUNTIME_TIMEOUT=600 nvdla-board-workload resnet50 \
  /run/media/ROOT-mmcblk0p1/nvdla-tests
```

The program verifies each stage independently:

1. the full SD payload hash;
2. module load, platform binding, render node, and IRQ registration;
3. runtime start and clean exit;
4. a positive NVDLA IRQ delta;
5. at least one completed hardware operation;
6. the final `HWLs done` count equals the reported total layer count;
7. `output.dimg` contains exactly 1000 signed integer values;
8. the output hash and zero-based top-five indices are archived.

A successful first run reports:

```text
NVDLA_BOARD_CLASSIFICATION=execution-pass-oracle-pending
```

This is accelerator execution evidence, not yet an exact tensor-correctness
claim. NVDLA's INT8 test runtime maps input bytes into `[0,127]`, whereas the
host Caffe reference subtracts a BGR mean. The FP32 result therefore cannot be
used as an exact golden tensor.

## Correctness promotion

The board gate is promoted to `exact-pass` only after the same pinned loadable
and image have completed on the verified source-built `nv_small` VP and its
complete 1000-element output has been added as `golden-output.dimg`. The board
then compares the complete output byte-for-byte. This keeps the FPGA board
from acting as its own correctness oracle.

If the VP run is too slow, retain the board result as
`execution-pass-oracle-pending`. Its IRQ, operation, HWL progress, output shape,
hash, and top-five evidence are still useful for locating failures, but must
not be described as tensor correctness.

### Background VP run

Start the independent source-built `nv_small` VP run from Ubuntu-24.04 WSL:

```sh
export SOURCES_DIR=$HOME/src/nvdla-peta-sources
make vp-resnet50-small-golden-start
```

This uses the already generated `artifacts/workloads/resnet50_small` payload.
Run `make vp-resnet50-small-workload` separately only when regenerating it.

The command detaches after recording a PID and run directory. It disables
SystemC transaction tracing and applies a seven-day outer timeout. Check it
later with:

```sh
make vp-resnet50-small-golden-status
```

Status reports whether the process is still running, the latest
`HWLs done, totally ... layers` line, and the final manifest/output hash when
available. The complete serial stream is written incrementally to
`serial.log`, while `background.log` records launcher diagnostics.

A successful run is classified `golden-candidate`. Before adding its
`output.dimg` to the SD payload, verify that the manifest records 1000 integer
elements, complete HWL progress, no bad kernel/VP patterns, and the expected
input hashes.
