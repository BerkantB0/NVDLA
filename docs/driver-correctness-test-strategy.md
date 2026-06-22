# NVDLA Driver Correctness Test Strategy

## Goal

The NVDLA runtime port needs a fast feedback loop that catches driver regressions before each board bring-up attempt. Kernel API compatibility alone is not enough: a patched `opendla.ko` must still preserve the DRM ioctl ABI, allocate and map GEM buffers correctly, schedule work, receive interrupts, and return deterministic output tensors.

The test framework therefore has three layers:

1. Host reproducibility checks: XSA audit, pinned source checks, Docker VP availability, and PetaLinux tool discovery.
2. Virtual platform correctness checks: boot an NVDLA-capable VP, load the candidate KMD, run deterministic workloads through UMD/runtime, and compare outputs to golden tensors.
3. ZCU102 hardware checks: repeat the same driver/runtime tests on the real FPGA implementation, adding real HP0 non-coherent DMA and reset/timing evidence.

## VP Lanes

`vp-reference` uses the stock `nvdla/vp` Linux 4.13 environment. It validates the harness, model artifacts, expected runtime behavior, and artifact collection before any driver forward-porting work is involved.

`vp-modern` uses the same NVDLA VP device model with a newer ARM64 kernel and patched KMD. This is the main correctness gate for forward-port changes. It is intentionally separate from the PetaLinux board image so driver changes can be tested rapidly without waiting for full FPGA image iterations.

The VP does not prove board-level DMA coherency or FPGA reset behavior. Those remain ZCU102 acceptance criteria, but VP tests should catch most ioctl, GEM, scheduler, interrupt, and runtime ABI regressions.

## Runtime Workloads

The first two workloads are deliberately small:

- `sdp_passthrough`: minimal read/write sanity workload for the DBB/DMA path.
- `tiny_conv_int8`: deterministic small CNN/loadable targeting `nv_small`, with fixed inputs and a CPU golden result.

Each workload must define its input hash, loadable hash, expected output hash, acceptable tolerance, and repeat count. The default stability gate is 100 repeated runs with identical results and no kernel warnings.

## Failure Policy

A run fails if any of the following occurs:

- Runtime exits non-zero.
- Output differs from the golden tensor by more than the workload tolerance.
- Kernel log contains `Oops`, `BUG`, `WARNING`, `DMA-API`, scheduler timeout, interrupt timeout, or unexpected reset.
- `/dev/dri/renderD*` is not created.
- KMD and UMD ioctl headers disagree.
- A repeated-run test produces non-deterministic output.

All failures must preserve the serial log, `dmesg`, runtime stdout/stderr, manifest, and tensor diff summary under `artifacts/<run-id>/`.

