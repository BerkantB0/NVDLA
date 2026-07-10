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
   PetaLinux module, and produce bootable PetaLinux 2024.1 artifacts.
4. **Hardware acceptance** - boot the generated image on the target board,
   validate probe, interrupt, and non-coherent DMA behavior, then run the same
   deterministic runtime workloads used in the VP.

The VP provides strong evidence for the KMD/UMD ABI, buffer management,
scheduling, interrupt handling, and deterministic inference behavior. It
cannot prove FPGA reset behavior, physical interrupt routing, or the real HP0
non-coherent DMA path; those remain board-level acceptance criteria.

## Current Status

- The NVDLA KMD and UMD patch queue supports the modern Linux 6.6 build paths
  used by the VP and PetaLinux 2024.1.
- A source-built `nv_small` VP lane passes the LeNet/MNIST correctness gate with
  the expected digit-7 output. The stock VP remains available as a control.
- The upstream SDP regression is retained as a diagnostic workload because its
  completion timeout is not yet a reliable correctness oracle.
- A PetaLinux 2024.1 ZynqMP project can be created reproducibly from
  `NVDLA_FPGA_wrapper.xsa`; the small-config `opendla.ko`, `image.ub`,
  `system.dtb`, and `BOOT.BIN` have been built successfully.
- Physical ZCU102 probe, DMA, interrupt, and inference validation is the next
  integration stage. Host build success is not treated as hardware proof.

## Repository Structure

| Path | Purpose |
| --- | --- |
| `patches/nvdla-sw/` | Upstream-style KMD/UMD patches against the pinned `nvdla/sw` revision. |
| `scripts/` | Source fetching, VP builds and tests, PetaLinux project setup, module builds, image creation, and packaging. |
| `tools/nvdla_test_framework/` | Python validation, workload, audit, manifest, and report tooling. |
| `tests/` | Fast unit tests for the host-side framework. |
| `configs/vp/` | Modern VP kernel, rootfs, and target smoke-test configuration. |
| `recipes/petalinux/` | Local PetaLinux recipe used to build and install `opendla.ko`. |
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
        +--> upstreamable NVDLA patch queue --> Linux 6.6 KMD/UMD builds
        |                                      |
        |                                      +--> nv_small VP correctness
        |
Checked-in XSA --> audited device tree --> PetaLinux module and boot images
                                                   |
                                                   +--> ZCU102 acceptance tests
```

Use Ubuntu 24.04 WSL2 for VP work and Ubuntu 22.04 WSL2 for PetaLinux 2024.1.
Heavy Linux builds should live on the WSL ext4 filesystem rather than under
`/mnt/c`.

The fast host regression gate is:

```sh
make test
```

The main `nv_small` VP correctness gate is:

```sh
make vp-lenet-small-gate
```

The PetaLinux host build lane is:

```sh
export PETALINUX_DIR=/opt/pkg/petalinux/2024.1
export PETALINUX_PROJECT=${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}

make petalinux-project
make petalinux-dts
NVDLA_KMD_CONFIG=small make petalinux-kmod
make petalinux-image
make petalinux-package
```

Each significant run writes evidence under `artifacts/<run-id>/`, including a
`manifest.json`, environment details, source and binary hashes, logs, output
tensors where applicable, and an explicit pass, fail, or blocked result.

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
- [Artifact schema](docs/artifact-schema.md)
- [Upstreamable patch workflow](docs/upstreamable-patch-workflow.md)
- [PetaLinux compatibility analysis](docs/nvdla-petalinux-compatible-version-analysis.md)
- [PetaLinux feasibility notes](docs/nvdla-petalinux-feasibility.md)

Run `make help` for the complete set of supported build, test, audit, and report
targets.
