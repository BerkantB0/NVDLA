# NVDLA Runtime Integration for PetaLinux 2024.1

This project modernizes the open-source NVDLA software stack for Linux 6.6 and
integrates it with PetaLinux 2024.1 for an FPGA implementation of the
`nv_small` NVDLA configuration on Zynq UltraScale+ MPSoC hardware.

The work is intended to support an MSc dissertation, so the emphasis is on a
simple, reproducible engineering process with clear correctness evidence. The
repository does not vendor large upstream projects or generated images. It
instead pins their revisions, maintains the NVDLA changes as an upstream-style
patch queue, automates the VP and PetaLinux build lanes, and records each build
or test as a machine-readable artifact.

## Project Scope

The project covers four connected areas:

1. **Modern Linux support** - forward-port the NVDLA kernel-mode driver (KMD)
   and user-mode runtime (UMD) to current DRM, GEM, DMA-BUF, and kernel APIs
   while preserving the existing userspace ABI.
2. **Virtual-platform validation** - build a modern ARM64 Linux environment and
   use the NVDLA Virtual Platform (VP) to test module loading, render-node
   creation, GEM operations, runtime execution, output correctness, and repeat
   stability.
3. **PetaLinux integration** - import the checked-in FPGA hardware description,
   install an XSA-derived device-tree node, build the patched driver as a
   PetaLinux module, package the ARM64 UMD/runtime, and produce bootable
   PetaLinux 2024.1 artifacts.
4. **Hardware acceptance** - boot the generated image on the target board,
   validate probe, interrupt, and non-coherent DMA behavior, then run the same
   deterministic runtime workloads used in the VP.

The VP provides strong evidence for the KMD/UMD ABI, buffer management,
scheduling, interrupt handling, and deterministic inference behavior. It
cannot prove FPGA reset behavior, physical interrupt routing, or the real HP0
non-coherent DMA path; those remain board-level acceptance criteria.

## Validated Status And Boundaries

- The upstream-style NVDLA KMD/UMD patch queue builds against Linux 6.6 in the
  VP and PetaLinux 2024.1 lanes while preserving the existing ioctl ABI.
- A source-built `nv_small` VP provides the primary software correctness
  environment. It has validated module loading, GEM handling, runtime
  execution, deterministic LeNet and ResNet-50 outputs, and repeat stability.
  Stock VP runs remain useful controls where their hardware configuration is
  known.
- The checked-in XSA can reproducibly create a ZynqMP PetaLinux project with an
  audited NVDLA device-tree node. BitBake packages `opendla.ko`,
  `nvdla_runtime`, `libnvdla_runtime.so`, board test tools, and their runtime
  dependencies into the generated image.
- Physical ZCU102 gates have validated boot, KMD probe, render-node creation,
  GEM mapping, interrupt-driven execution, exact LeNet output, repeat runs,
  and exact ResNet-50 output through the real FPGA and memory path.
- The test image supports quiet, correctness-qualified latency and monitored
  PS/PL power collection. A pinned ONNX Runtime CPU lane provides a standard
  Cortex-A53 comparison for the same model families. These facilities are for
  controlled evaluation and are not deployment policy.
- The standalone upstream SDP regression is retained as a diagnostic rather
  than a tensor-correctness oracle. Its behavior must not override the passing
  end-to-end model gates.

The current evidence is specific to the checked-in `nv_small` hardware handoff,
ZCU102 integration, Linux/PetaLinux versions, and pinned software revisions.
Other boards, clocks, memory ports, or NVDLA configurations require their own
device-tree audit and hardware acceptance runs.

## Repository Structure

| Path | Purpose |
| --- | --- |
| `patches/nvdla-sw/` | Upstream-style KMD/UMD patches against the pinned `nvdla/sw` revision. |
| `scripts/` | Source fetching, VP and PetaLinux builds, packaging, board execution, and artifact collection. |
| `tools/nvdla_test_framework/` | Python validation, workload, audit, manifest, analysis, and report tooling. |
| `tests/` | Fast unit tests for the host-side framework. |
| `configs/vp/` | Modern VP kernel, rootfs, and target smoke-test configuration. |
| `recipes/petalinux/` | Local recipes for the driver, runtimes, board tools, hardware monitors, and image composition. |
| `workloads/` | Tracked workload definitions and target-side test utilities. |
| `docs/` | Test strategy, reproducible runbook, artifact schema, feasibility analysis, and patch workflow. |
| `repro.lock.json` | Pinned source commits, Docker identities, XSA facts, PetaLinux revision, and workload hashes. |
| `NVDLA_FPGA_wrapper.xsa` | FPGA hardware handoff used to derive and configure the PetaLinux project. |

Generated sources, worktrees, logs, kernels, root filesystems, modules, and
test evidence are kept under `.external/`, `.work/`, external WSL build
directories, and `artifacts/`. These locations are intentionally ignored by
Git.

## Workflow Overview

```text
Pinned upstream sources
        |
        +--> upstreamable patch queue --> Linux 6.6 KMD/UMD builds
        |                                  |
        |                                  +--> nv_small VP correctness
        |
Checked-in XSA --> audited device tree --> PetaLinux module + runtime + image
                                                        |
                                                        +--> staged ZCU102 gates
                                                                  |
                                                                  +--> evidence and reports
```

Use Ubuntu 24.04 WSL2 for VP work and Ubuntu 22.04 WSL2 for PetaLinux 2024.1.
Heavy Linux builds should live on the WSL ext4 filesystem rather than under
`/mnt/c`.

The fast host regression gate is:

```sh
make test
```

The primary `nv_small` VP correctness gate is:

```sh
make vp-lenet-small-gate
```

The full PetaLinux lab-image build lane is:

```sh
export PETALINUX_DIR=/opt/pkg/petalinux/2024.1
export PETALINUX_PROJECT=${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}

make petalinux-project
make petalinux-dts
make petalinux-power
NVDLA_KMD_CONFIG=small make petalinux-kmod
make petalinux-runtime

# Standard ARM CPU comparison support used by the current audited image.
make petalinux-cpu-sdk
make cpu-onnxruntime
make cpu-model-workloads
make petalinux-cpu-runtime

make petalinux-board-tools
make petalinux-image
make petalinux-rootfs-audit
make petalinux-package
make petalinux-board-payload
make petalinux-sd-bundle
```

The SD bundle contains the boot files; `petalinux-board-payload` produces the
separate `nvdla-tests` directory that is copied beside them on the FAT
partition.

The board payload includes pinned `nv_small` LeNet and ResNet-50 workloads,
their inputs, expected outputs, manifests, and hashes. ResNet-50 can be built
independently with:

```sh
make vp-resnet50-small-workload
```

See [docs/resnet50-board-gate.md](docs/resnet50-board-gate.md) for model
provenance and pass criteria. The general staged procedure, from preflight and
manual module insertion through inference, is in the
[ZCU102 bring-up runbook](docs/zcu102-first-boot-runbook.md).

The same board image can also run pinned FP32 and INT8 ONNX Runtime CPU
baselines for LeNet and ResNet-50. See
[docs/arm-cpu-comparison-methodology.md](docs/arm-cpu-comparison-methodology.md)
for the build, correctness, latency, power, and final campaign protocol.

The image deliberately does not autoload the module or start a runtime service.
Initial acceptance therefore remains explicit and staged. Model assets stay in
the separate, hash-verified `nvdla-tests` FAT payload rather than the root
filesystem.

The `nvdla-board-tools` package in this repository also installs a deliberately
configuration-specific direct-link profile: ZCU102 MAC
`02:00:00:50:10:02`, board address `192.168.50.2/24`, and host address
`192.168.50.1/24`. This is a convenience for the single-board dissertation
bring-up setup, not part of the portable NVDLA software stack. Replace or omit
it for a routed network, multiple boards, a different ZCU102 revision, or
another platform.

The profile uses the direct-link host at `192.168.50.1` as its NTP source and
starts time synchronization during boot. NTP improves artifact timestamps but
is not a benchmark prerequisite: latency and power integration use monotonic
clocks, and Linux boot IDs identify independent sessions.

Target-side gates create self-contained archives under `/tmp`; the host tools
copy and validate them under `artifacts/`. Evidence includes manifests,
environment and boot identity, source and binary hashes, logs, outputs,
correctness classification, and raw timing or power records where applicable.
The shared SSH collector is:

```sh
scripts/run_board_benchmark.sh {nvdla|cpu} {lenet|resnet50} [options]
```

It waits for the board, rejects accidental reuse of the previous benchmark
boot, runs one requested campaign, and downloads the resulting archive. The
analysis tools reject mixed provenance and preserve raw samples and flagged
outliers. See the performance methodology documents for timing boundaries,
power scope, and final-session rules.

A supplementary `--input-set multi20` NVDLA mode cycles deterministic,
balanced input sets through one loaded runtime context. It separates prepared
input-buffer update time from runtime execution, requires repeat-stable output
for every image, and reports classification accuracy separately. This tests
the primary single-image results for input-selection sensitivity without
replacing the frozen campaign.

The complementary `--input-set stream20` mode feeds the same 20 inputs as a
looped MJPEG stream through GStreamer and a bounded runtime queue. Frame
decoding and tensor preparation overlap accelerator execution, so the reported
sustained frame rate represents a simple camera-like pipeline rather than
batch preprocessing. The file-backed producer is deterministic; it can later
be replaced by a live GStreamer source without changing the NVDLA runtime
interface.

## Reproducibility and Upstreamability

The pristine pinned `nvdla/sw` checkout is kept separate from the patched work
tree. Driver and runtime changes are stored as numbered `git format-patch`
files so they can later be applied unchanged to a maintained NVDLA fork. Board
addresses, PetaLinux recipes, XSA-derived device-tree details, and test harness
code remain local to this integration repository.

Useful patch checks are:

```sh
make patch-apply
make patch-check
make abi-check
```

Generated images, build trees, modules, logs, and runtime artifacts must not be
committed. Only source code, scripts, recipes, documentation, tests, patch
files, and pinned metadata belong in Git.

## Documentation

- Milestone 1: modern Linux software stack report ([source](docs/report-modern-linux-software-stack-milestone.md), [PDF](output/pdf/modern-linux-software-stack-milestone.pdf))
- [Reproducible runbook](docs/reproducible-runbook.md)
- [Driver correctness strategy](docs/driver-correctness-test-strategy.md)
- [Differential VP trace strategy](docs/differential-vp-trace-strategy.md)
- [Artifact schema](docs/artifact-schema.md)
- [Upstreamable patch workflow](docs/upstreamable-patch-workflow.md)
- [PetaLinux compatibility analysis](docs/nvdla-petalinux-compatible-version-analysis.md)
- [PetaLinux feasibility notes](docs/nvdla-petalinux-feasibility.md)
- [ZCU102 first board bring-up](docs/zcu102-first-boot-runbook.md)
- [NVDLA performance methodology](docs/nvdla-performance-methodology.md)
- [ARM CPU comparison methodology](docs/arm-cpu-comparison-methodology.md)
- [ResNet-50 board gate](docs/resnet50-board-gate.md)

Run `make help` for the complete set of supported build, test, audit, and report
targets.
