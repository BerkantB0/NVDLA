# Reproducible Runbook

## Host Assumptions

Run commands from Ubuntu WSL in the repository root:

```sh
cd /mnt/c/Users/berkant/Dev/NVLDA-Peta
```

The current known environment is:

- Ubuntu 24.04 WSL2 for VP work
- Ubuntu 22.04 WSL2 for PetaLinux 2024.1 work
- Docker Desktop available inside WSL
- PetaLinux installed at `/opt/pkg/petalinux/2024.1`
- PetaLinux project default at `$HOME/build/nvdla-peta/petalinux/zcu102-nvdla`
- PetaLinux metadata revision `b31575b49f230b006aa3193cb564368e357777ed`

PetaLinux 2024.1 may warn that the WSL host is not a supported OS. The
framework records this as an environment fact; it does not hide the warning.

## Fast Regression Gate

```sh
make test
```

This checks the host tools, validates `repro.lock.json`, audits the XSA, boots the stock NVDLA VP to the Buildroot login prompt, and verifies the PetaLinux command environment.

## PetaLinux Project Lane

Run PetaLinux commands from the Ubuntu-22.04 WSL distro:

```sh
cd /mnt/c/Users/berkant/Dev/NVLDA-Peta
export PETALINUX_DIR=/opt/pkg/petalinux/2024.1
export PETALINUX_PROJECT=${PETALINUX_PROJECT:-$HOME/build/nvdla-peta/petalinux/zcu102-nvdla}
make petalinux-project
make petalinux-dts
NVDLA_KMD_CONFIG=small make petalinux-kmod
make petalinux-runtime
make petalinux-image
make petalinux-rootfs-audit
make petalinux-package
```

`make petalinux-project` creates a ZynqMP project when needed and imports the
checked-in `NVDLA_FPGA_wrapper.xsa`. `make petalinux-dts` installs a local
`nvdla-user.dtsi` fragment included from `system-user.dtsi`; it uses
`compatible = "nvidia,nv_small"`, CSB `0xA0000000` size `0x10000`, interrupt
`<0 89 4>`, and leaves coherent-DMA absent for the audited HP0 path.

`make petalinux-runtime` installs the tracked BitBake runtime recipe and image
append, applies the same pinned NVDLA patch queue as the KMD recipe, and builds
the runtime with PetaLinux's ARM64 compiler and sysroot. The image append adds
both `opendla` and `nvdla-runtime` to `petalinux-image-minimal`.

The resulting rootfs contains:

```text
/usr/bin/nvdla_runtime
/usr/lib/libnvdla_runtime.so
/lib/modules/<kernel>/extra/opendla.ko
```

`make petalinux-rootfs-audit` checks the generated `rootfs.tar.gz` without a
board. It requires all three files, confirms AArch64 ELF identity, resolves each
dynamic dependency inside the rootfs, rejects RPATH/RUNPATH and embedded host
build paths, and records extracted binary hashes. The runtime build also treats
Yocto `rpaths`, `textrel`, `file-rdeps`, `already-stripped`, and `buildpaths` QA
findings as failures.

These targets write manifests under `artifacts/<run-id>/` with the project path,
XSA and patch-series hashes, PetaLinux settings log, recipe/package hashes, DT
fragment, module hash/vermagic, runtime ELF metadata, rootfs audit, image hashes,
and pass/fail/block reason.

The image does not autoload `opendla.ko` or start a runtime service. Model
loadables and input/golden data remain separate generated test assets. After
the board probe, IRQ, and GEM/DMA gates pass, copy or package the pinned
`nv_small` LeNet assets and run `nvdla_runtime` manually against the discovered
render node.

## Fetching Sources

Fetch only NVDLA software:

```sh
make sources
```

Fetch NVDLA software plus the heavier kernel/rootfs sources:

```sh
make sources-heavy
```

Fetched repositories are stored under `.external/sources/` and are intentionally ignored by git.

If the repository is checked out on a Windows-backed filesystem such as
`/mnt/c`, place heavy Linux sources on the WSL ext4 filesystem instead:

```sh
export SOURCES_DIR=$HOME/src/nvdla-peta-sources
export WORK_DIR=$HOME/build/nvdla-peta/vp-modern
make sources-heavy
```

Use the same `SOURCES_DIR` and `WORK_DIR` values for `make vp-toolchain`,
`make vp-kernel`, and `make vp-kmod`. The `linux-xlnx` tree contains paths such
as `aux.c`, which Windows reserves and may refuse to check out on NTFS-backed
paths. Kernel and Buildroot builds are also much faster on the WSL ext4
filesystem than under `/mnt/c`.

## Upstreamable Patch Queue

Keep `.external/sources/nvdla-sw` pristine. Apply the tracked patch queue into the ignored worktree:

```sh
make patch-apply
make patch-status
make patch-check
make abi-check
```

Edit and commit upstreamable NVDLA changes inside `.work/nvdla-sw-patched`, then regenerate the patch queue with:

```sh
make patch-format
```

Local integration work belongs in this repository; KMD/UMD changes intended for a future `nvdla/sw` fork belong in `patches/nvdla-sw/*.patch`.

## Modern VP Build Lane

The modern VP lane is intentionally split so driver changes can be tested without rebuilding everything:

```sh
make vp-toolchain
make vp-kernel
make vp-rootfs
make vp-kmod
LANE=modern make vp-test
```

`LANE=modern make vp-test` auto-discovers these files from `WORK_DIR` unless
they are overridden explicitly:

- `VP_MODERN_KERNEL`: defaults to `$WORK_DIR/kernel/arch/arm64/boot/Image.vp2m`, then falls back to `Image`
- `VP_MODERN_ROOTFS`: defaults to `$WORK_DIR/buildroot/images/rootfs-smoke.ext4`, then falls back to `rootfs.ext4`
- `VP_MODERN_KO`: defaults to `$WORK_DIR/modules/opendla.ko`
- `VP_MODERN_DTB`: optional; when present, the runner passes it with `-dtb`

The old NVDLA VP/QEMU wrapper cannot boot the raw Linux 6.6 ARM64 `Image`
produced by the minimal kernel config because its header uses `text_offset=0`,
which overlaps the wrapper's loader region. `make vp-kernel` therefore also
writes `Image.vp2m`, a generated image with the ARM64 header text offset set to
`0x200000`. The raw `Image` remains available for inspection, but the smoke
lane uses `Image.vp2m`.

`make vp-rootfs` writes the normal Buildroot `rootfs.ext4` and a generated
`rootfs-smoke.ext4` that adds `/etc/init.d/S99nvdla-smoke`. The autorun hook
mounts the VP payload share, runs the smoke script, prints deterministic status
markers, and powers off the VP. This avoids relying on interactive serial login
timing during automated smoke tests.

The modern VP test builds a small target-side `nvdla-kmd-smoke` utility with the
same cross compiler policy as the VP build lane. The utility opens
`NVDLA_DEVICE_NODE` or the first `/dev/dri/renderD*`, then exercises GEM create,
map-offset, `mmap`, read/write, and destroy. It does not submit accelerator
workloads yet.

Runtime workload mode builds on the same boot/module path and adds the ARM64
UMD runtime server, a small target-side Python client, and the pinned upstream
SDP regression flatbuffer:

```sh
make workloads
make vp-runtime
MODE=runtime WORKLOAD=sdp_regression_small LANE=modern VP_TIMEOUT=240 make vp-test
```

Runtime mode packages `nvdla_runtime`, `libnvdla_runtime.so`,
`nvdla_flatbuf_client.py`, `loadable.fbuf`, the golden `o_000000.dimg`, and
`opendla.ko` into the VP payload share. The target script loads the KMD, verifies
`/dev/dri/renderD*`, starts `nvdla_runtime -s`, submits the flatbuffer over the
upstream length-prefixed protocol, captures `GET_OUTPUT`, compares it against
the golden output with exact tolerance, and copies logs plus output tensors back
to the run artifact directory.

The initial dissertation scope is `nv_small`, matching the FPGA design, so
`sdp_regression_small` remains the default runtime workload. The stock modern
VP DTB currently advertises `nvidia,nvdla_os_initial` and `nvidia,nv_full`,
which is useful as a control experiment but not sufficient for the main
`nv_small` gate. Use the explicit full-config workload only when validating the
stock VP path:

```sh
MODE=runtime WORKLOAD=sdp_regression_full LANE=modern VP_TIMEOUT=240 make vp-test
```

The KMD register header is also a build-time hardware choice. `make vp-kmod`
defaults to `NVDLA_KMD_CONFIG=initial`, matching the current prebuilt
`nvdla/vp:latest` CMOD register map. Use `NVDLA_KMD_CONFIG=small make vp-kmod`
only with a true `nv_small` VP/CMOD or board bitstream. A diagnostic run with
`NVDLA_KMD_CONFIG=small` against the current Docker VP reached `Program SDP`
but the CMOD decoded small-map SDP writes as initial-map CACC/CMAC registers,
confirming that this Docker image cannot be used as the final `nv_small`
correctness gate without rebuilding/replacing the VP hardware model.

Manual LeNet/MNIST controls on 2026-07-03 confirmed the same configuration
rule with stock software only. The locally compiled `nv_small` LeNet loadable
and the Columbia prebuilt loadable were byte-identical
(`4b6aa87846329e46e9468a37ffdc0a111884fa63390cdfbea26a95a455edb29d`).
With `nvdla/vp:latest`, that `nv_small` loadable plus stock `opendla_2.ko`
exited the VP at the first convolution before writing an output. The same
loadable plus stock `opendla_1.ko` progressed further, then emitted CMOD
invalid CSC/CDMA configuration messages and hung for more than eight minutes.
By contrast, `nvdla/vp:latest` with a locally compiled `nv_full` LeNet loadable
and stock `opendla_1.ko` completed successfully with output
`0 2 0 0 0 0 0 124 0 0`, predicting digit 7. The older local
`nvdla/vp:1.3` image with its stock `opendla.ko` ran the same `nv_small`
loadable for an extended window before `nvdla_runtime` segfaulted and produced
no output. These runs are archived under
`artifacts/20260703T115149Z-vp-stock-lenet/` and
`artifacts/20260703T120700Z-vp-stock13-lenet/`.

The practical consequence is that `nvdla/vp:latest` is currently useful as an
`initial`/`nv_full` stock control, not as the dissertation `nv_small` oracle.
The `nv_small` gate must use either a known-good stock VP binary whose CMOD is
verified as `nv_small`, a reproducibly source-built `nv_small` VP, or the real
FPGA bitstream. Stock regression flatbuffers should also be treated carefully;
NVIDIA issue https://github.com/nvdla/sw/issues/140 records similar reports of
stock flatbuffer tests failing on stock VP/module combinations.

The source-built `nv_small` VP lane is pinned in `repro.lock.json`:

- `nvdla/vp` at `f7ce663b95adf4f381de186b665becae28df26ed`
- `nvdla/hw` `nv_small` branch at `771f20cc9e69759d7277978eb41e8d47f1547374`

Fetch and build the `nv_small` VP/CMOD lane with:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources make sources-vp

SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
SYSTEMC_PREFIX=/path/to/systemc-2.3.0 \
make vp-small-cmod vp-small-bin vp-small-dtb
```

If WSL does not have a host SystemC install, use the official VP image as the
build container. It contains `/usr/local/systemc-2.3.0` and the old native tool
versions expected by upstream VP/HW:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
make vp-small-cmod-docker vp-small-bin-docker vp-small-dtb
```

`sources-vp` fetches the pinned VP/HW repos and the top-level VP submodules.
It intentionally avoids recursive qbox ROM submodules, because the upstream
qbox tree still points several nested submodules at legacy `git://` URLs that
are not needed for this VP build and may refuse connections.

`vp-small-cmod` and `vp-small-bin` keep the pinned checkouts pristine by cloning
them into `$WORK_DIR/vp-small/`. If `SYSTEMC_PREFIX` does not contain
`include/systemc.h` and a SystemC library, the host targets write a `blocked`
manifest under `artifacts/` with the missing prerequisite rather than
pretending the lane was tested.

Build and test the modern small-config driver with:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
NVDLA_KMD_CONFIG=small \
make vp-kmod

VP_HW_CONFIG=small \
VP_RUNNER=source-docker \
VP_MODERN_DTB=$HOME/build/nvdla-peta/vp-modern/dtb/nvdla-vp-modern-small-extmem-pool.dtb \
MODE=runtime \
WORKLOAD=sdp_regression_small \
LANE=modern \
VP_TIMEOUT=900 \
make vp-test
```

`VP_RUNNER=source-docker` runs the pinned source-built `aarch64_toplevel`
from `$WORK_DIR/vp-small/install/bin` inside the stock VP Docker image. This
keeps the `nv_small` hardware model under test while reusing the image's
SystemC installation. If WSL has a compatible host SystemC install, set
`VP_RUNNER=host` to run the same binary directly.

A valid `nv_small` correctness artifact must show all three layers agreeing:
the source-built VP/CMOD was configured as `nv_small`, the KMD probe log reports
`nvidia,nv_small`, and the workload manifest targets `nv_small`. The stock
`nvdla/vp:latest` Docker image remains a negative/control lane for `nv_small`;
it must not be used as the dissertation `nv_small` oracle.

The first source-built `nv_small` smoke gate passed on 2026-07-08 under
`artifacts/20260708T144613Z-vp-modern-smoke/`: it booted the pinned
`aarch64_toplevel`, loaded the small-config KMD, probed `nvidia,nv_small`,
created `/dev/dri/renderD128`, and passed the GEM mmap smoke utility with no
bad kernel patterns. The same lane then ran LeNet/MNIST successfully under
`artifacts/20260708T150245Z-vp-modern-lenet-small/`, using the locally compiled
`nv_small` loadable from `artifacts/20260703T115149Z-vp-stock-lenet/`; the
output matched the stock digit-7 vector exactly:
`0 2 0 0 0 0 0 124 0 0`.

The upstream `sdp_regression_small` flatbuffer is not yet the `nv_small`
correctness gate. The run under
`artifacts/20260708T144704Z-vp-modern-runtime/` matched the `nv_small` probe
and inserted the KMD cleanly, but the VP timed out after programming and
enabling the SDP operation, before an SDP completion event or output file was
produced. Treat that artifact as the next workload-specific debug input, not as
evidence that the source-built `nv_small` VP or KMD is generally broken.

Use the direct LeNet control below to compare patched modern-kernel behavior
against the passing stock `nv_full` VP result:

```sh
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
VP_RUNTIME_BIN=$PWD/artifacts/20260703T115149Z-vp-stock-lenet/nvdla_runtime \
VP_RUNTIME_LIB=$PWD/artifacts/20260703T115149Z-vp-stock-lenet/libnvdla_runtime.so \
VP_TIMEOUT=900 \
scripts/run_modern_lenet_full_control.sh
```

The 2026-07-03 clean run with `NVDLA_KMD_CONFIG=initial` loaded the patched KMD,
created `/dev/dri/renderD128`, completed all ten LeNet hardware layers, and had
`nvdla_runtime` report `Test pass`, but the output tensor was
`12 12 12 12 12 12 12 12 12 12` instead of the stock
`0 2 0 0 0 0 0 124 0 0`. The evidence is archived under
`artifacts/20260703T193840Z-vp-modern-lenet-full/`, with the clean module build
under `artifacts/20260703T193404Z-vp-kmod/`. Local experiments removing
`DMA_ATTR_WRITE_COMBINE`, forcing the KMD coherent flag, and adding explicit
GEM DMA sync around task execution did not change this output, so they were not
kept in the patch queue.

The 2026-07-07 debug run under
`artifacts/20260707T144535Z-vp-modern-lenet-full/` used an opt-in local KMD
trace module. It confirmed that the CPU-visible GEM buffers contain the LeNet
loadable/input data and that the KMD programs DMA addresses in the normal
Linux RAM/CMA range around `0x6ec00000..0x6ed00000`, but the accelerator still
returns the all-12 output after completing all ten layers. A negative control
with `VP_RAM_BASE=0xc0000000 VP_RAM_HIGH=0xffffffff` under
`artifacts/20260707T144942Z-vp-modern-lenet-full/` failed with
`TLM_ADDRESS_ERROR_RESPONSE` when the NVDLA tried to read those same
`0x6ed...` addresses. This strongly suggests the modern VP needs an explicit
DMA memory aperture equivalent to the stock VP's old
`dma_declare_coherent_memory()`/`0xc0000000` path, implemented through modern
reserved-memory or VP device-tree integration rather than hardcoded in common
KMD code.

The first reserved-memory follow-up under
`artifacts/20260707T151942Z-vp-modern-lenet-full/` added an experimental DTB
with a `no-map` `shared-dma-pool` inside ordinary QEMU RAM at
`0x70000000..0x77ffffff` and used patch
`0009-kmd-attach-DT-reserved-memory-on-modern-kernels.patch` to attach a
`memory-region` on the NVDLA device. The kernel confirmed both the reserved
pool and `NVDLA 10200000.nvdla: assigned reserved memory node
nvdla-dma-pool@70000000`, and a traced rerun showed GEM DMA addresses moving
to `0x70010000..0x70100000`. The output still remained
`12 12 12 12 12 12 12 12 12 12`, proving that generic reserved-memory
attachment was necessary but not sufficient when the pool lived in normal QEMU
RAM.

The passing run under `artifacts/20260707T202443Z-vp-modern-lenet-full/`
instead used `configs/vp/nvdla-vp-modern-extmem-pool.dts`, which places the
same no-map `shared-dma-pool` in the VP extmem aperture at
`0xc0000000..0xc7ffffff` and runs the VP RAM target as
`0xc0000000..0xffffffff`. The clean, non-debug `opendla.ko` loaded, the kernel
reported `assigned reserved memory node nvdla-dma-pool@c0000000`, all ten
LeNet layers completed, `nvdla_runtime` exited with status 0, and the output
matched stock exactly: `0 2 0 0 0 0 0 124 0 0`. `lenet-compare` classified
the artifact as `pass`. This indicates that the stock VP's correctness path
depends on CPU/runtime buffers being allocated from the VP extmem/SystemC RAM
aperture, not ordinary QEMU RAM.

Reproduce the passing VP-local LeNet gate with:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
make vp-kmod vp-runtime vp-extmem-dtb

SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
VP_MODERN_DTB=$HOME/build/nvdla-peta/vp-modern/dtb/nvdla-vp-modern-extmem-pool.dtb \
VP_RAM_BASE=0xc0000000 \
VP_RAM_HIGH=0xffffffff \
VP_RCU_CPU_STALL_TIMEOUT=300 \
VP_TIMEOUT=900 \
make vp-lenet-full
```

## Differential `nv_small` CSB Trace Gate

The differential gate runs the pinned Linux 4.13 software stack and the modern
Linux 6.6 stack on the same source-built `nv_small` VP/CMOD. Both lanes use the
same pinned LeNet loadable and `seven.pgm` input. The legacy capture is accepted
as a reference only after exact output and all ten HWLs pass.

Run the complete gate from WSL:

```sh
export SOURCES_DIR="$HOME/src/nvdla-peta-sources"
export WORK_DIR="$HOME/build/nvdla-peta/vp-modern"

make vp-trace-reference-small
make vp-trace-modern-small
make vp-trace-compare

# Equivalent aggregate command:
make vp-trace-small-gate
```

To compare existing captures explicitly:

```sh
REFERENCE_ARTIFACT=/path/to/reference \
CANDIDATE_ARTIFACT=/path/to/candidate \
make vp-trace-compare
```

Each capture retains `systemc.log`, split `csb.raw.log` and `dbb.raw.log`,
canonical `csb-events.jsonl`, `trace-summary.json`, serial/runtime/dmesg output,
and a hash-bearing manifest. The differential artifact contains
`trace-diff.json`, `trace-diff.md`, and its own evidence manifest. Generated
artifacts remain ignored.

The automated capture uses `sc_high`, the minimum verbosity that emits every
adaptor transaction. `VP_TRACE_VERBOSITY=sc_debug` is available for exploratory
CMOD diagnostics but is substantially slower and is not the reproducible gate
default.

The JSONL schema is source-neutral. A future ILA CSV importer should set
`source` to `ila` and emit the same operation, relative offset, length, data,
response, and optional register fields. DBB comparison and the ILA importer are
separate follow-on milestones.

For the `nv_small` LeNet control, first provide a loadable compiled with
`--configtarget nv_small --cprecision int8`; then run:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
make sources-lenet vp-lenet-small-workload

SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
VP_HW_CONFIG=small \
VP_RUNNER=source-docker \
VP_MODERN_DTB=$HOME/build/nvdla-peta/vp-modern/dtb/nvdla-vp-modern-small-extmem-pool.dtb \
LENET_LOADABLE=$PWD/artifacts/workloads/lenet_small/lenet_mnist.nv_small.nvdla \
EXPECTED_OUTPUT_FILE=$PWD/artifacts/workloads/lenet_small/expected-output.txt \
VP_TIMEOUT=900 \
make vp-lenet-small
```

The formal primary `nv_small` correctness gate wraps those steps:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
VP_TIMEOUT=900 \
make vp-lenet-small-gate
```

It uses the locked Columbia LeNet/MNIST files from `repro.lock.json`, compiles
the `nv_small` loadable with the pinned stock VP Docker compiler, runs the
source-built `nv_small` VP through `VP_RUNNER=source-docker`, and writes
`lenet-analysis.json` beside the VP artifact. A valid pass requires the KMD to
probe `nvidia,nv_small`, the loadable to be tagged `nv_small`, all 10 HWLs to
complete, the output to match `0 2 0 0 0 0 0 124 0 0`, and bad-pattern logs to
be empty.

For repeat stability, run:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
make vp-lenet-small-stability
```

This expands to `REPEAT=100 VP_TIMEOUT=7200 make vp-lenet-small-gate`. The
manifest records `repeat_results`, `pass_count`, `first_failure`, the probed
compatible string, render node, and layer/HWL summary.

Record the matching VP/KMD configuration proof with:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
make vp-small-config-audit
```

The audit captures `CMakeCache.txt`'s `NVDLA_HW_PROJECT`, the `ldd` CMOD
resolution inside the VP Docker image, VP binary/CMOD/DTB/KMD hashes, and the
latest `nvidia,nv_small` probe artifact.

`sdp_regression_small` is still useful, but only as a diagnostic until the
upstream regression/golden relationship is explained:

```sh
SOURCES_DIR=$HOME/src/nvdla-peta-sources \
WORK_DIR=$HOME/build/nvdla-peta/vp-modern \
make vp-sdp-small-diagnostic
```

That target accepts a real SDP pass, the earlier known timeout shape, or the
now observed stock-comparable zero-output mismatch shape. In the zero-output
case, module and render node are healthy, the KMD probes `nvidia,nv_small`, SDP
is programmed/enabled/completed, the runtime client receives
`[OK] Test PASSED!`, the output `.dimg` is returned, no bad kernel/VP pattern is
present, but the payload bytes after the `.dimg` header are all zero and do not
match the pinned upstream golden. Treat this as diagnostic evidence only, not a
driver correctness pass.

The stock `nvdla/vp:latest` control with stock `opendla_1.ko` and stock
`nvdla_runtime -s` shows the same important caveat for `sdp_regression_full`:
the runtime reports `[OK] Test PASSED!` and returns an output, but the returned
payload is zero and does not match the selected upstream golden. This makes the
SDP regression useful for KMD/runtime protocol, probe, IRQ, and scheduler
health, but weak as a tensor-correctness oracle.

`VP_RCU_CPU_STALL_TIMEOUT=300` is a VP simulation timing control. Without it,
the slow SystemC/VP run can trigger Linux RCU stall diagnostics even though the
runtime eventually completes and the tensor matches. The harness records the
applied value in `rcu-cpu-stall-timeout.txt` and still fails on any remaining
bad kernel patterns.

The old VP SystemC model needs `SC_SIGNAL_WRITE_CHECK=DISABLE` for the modern
runtime workload path; otherwise the SDP run can abort on a multiple-driver
signal check in the VP model. The default modern Lua generated by the test
framework maps the VP RAM target at `0x40000000..0x7fffffff`, matching QEMU
`virt` RAM for smoke tests. Runtime correctness tests that need the stock VP
extmem path must override this to `0xc0000000..0xffffffff` together with the
extmem DTB above. A mismatch here produces DLA DBB
`TLM_ADDRESS_ERROR_RESPONSE` failures.
Threaded target userspace requires `CONFIG_FUTEX`; `make vp-kernel` enables it
because both `sshd` and `nvdla_runtime -s` use futex-backed libc primitives.

Useful controls:

```sh
VP_TIMEOUT=180 LANE=modern make vp-test
REPEAT=100 VP_TIMEOUT=300 LANE=modern make vp-test
REPEAT=100 MODE=runtime WORKLOAD=sdp_regression_small LANE=modern VP_TIMEOUT=900 NVDLA_RUNTIME_TIMEOUT=600 make vp-test
NVDLA_RUNTIME_TIMEOUT=600 MODE=runtime WORKLOAD=sdp_regression_full LANE=modern VP_TIMEOUT=900 make vp-test
```

`NVDLA_RUNTIME_TIMEOUT` and `NVDLA_SERVER_START_TIMEOUT` are host-side controls
embedded into the target payload script at generation time. Increase
`NVDLA_RUNTIME_TIMEOUT` for slow VP workload execution, and keep `VP_TIMEOUT`
larger than the target runtime timeout so the harness has time to collect logs
and power off.

Every modern VP run archives `serial.log`, `dmesg.log`, `module-load.log`,
`dev-dri.txt`, runtime stdout/stderr, the generated VP Lua file, payload files,
and `manifest.json`. A run is marked `blocked` when required artifacts are not
available, `fail` when boot/module/smoke checks fail, and `pass` only when all
modern VP smoke or runtime criteria are satisfied.

Runtime evidence from `20260709T192642Z-vp-modern-runtime` showed the
source-built `nv_small` VP booted Linux 6.6, `opendla.ko` loaded,
`/dev/dri/renderD128` appeared, the KMD probed `nvidia,nv_small`, SDP completed,
and the runtime server/client completed with `NVDLA_RUNTIME_TIMEOUT=600`. The
returned tensor hash was
`A5C53563E8AB82FB6349C44902211EB04A535FC2D73A704C33035C004194548D`, which has a
zero payload and does not match the `nv_small` golden. Stock control evidence
from `20260709T191532Z-vp-stock-sdp-full` reached the same conclusion for the
full SDP regression: runtime protocol success and output return, but zero
payload rather than the selected upstream golden. Use LeNet/MNIST as the
current `nv_small` correctness gate while SDP remains diagnostic.

Toolchain policy:

- `CROSS_COMPILE=/path/to/prefix-` always wins when set.
- Otherwise the framework uses the pinned Buildroot compiler at `.work/vp-modern/buildroot/host/bin/aarch64-buildroot-linux-gnu-`.
- If the Buildroot compiler does not exist, the framework accepts an apt-installed `aarch64-linux-gnu-` compiler.
- If neither exists, run `make vp-toolchain` or install `gcc-aarch64-linux-gnu g++-aarch64-linux-gnu bc bison flex libssl-dev make cpio unzip e2fsprogs`.

`make vp-toolchain` builds only the Buildroot host toolchain from the pinned Buildroot source. It is the preferred reproducible path because the resulting compiler is tied to `repro.lock.json`; the apt compiler fallback is useful for quick local compile triage.

PetaLinux's host package checks may require `gcc-multilib`, which can conflict with Ubuntu's apt `aarch64-linux-gnu-` cross compiler packages. That is acceptable for this framework: keep `make vp-toolchain` as the default reproducible compiler path and treat apt cross compilers as disposable local convenience packages.

The Buildroot calls run with a clean Linux-only `PATH` by default because WSL may
import Windows paths with spaces. Override `VP_BUILD_PATH` only if a required
host tool lives outside `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`.

Each VP build target writes a run directory under `artifacts/<timestamp>-vp-<phase>/` with `manifest.json`, `environment.txt`, and the phase log. For `vp-kernel`, the manifest records the linux-xlnx source SHA, raw and VP-compatible kernel image hashes, kernel release when available, and toolchain identity. For `vp-rootfs`, it records the normal and smoke rootfs hashes. For `vp-kmod`, it records the patched `nvdla/sw` SHA, patch-series hash, selected toolchain, module hash when build succeeds, and the `kmod.log` compile output when build fails.

The first Linux 6.6 milestone is considered useful even when `make vp-kmod` fails: the failure must be captured in `artifacts/*-vp-kmod/kmod.log` and should point to the next small upstreamable compatibility patch under `patches/nvdla-sw/`.

## PetaLinux KMD Lane

To build the same patched driver inside an existing PetaLinux project:

```sh
export PETALINUX_PROJECT=/path/to/project
make petalinux-kmod
```

The script installs the provided recipe skeleton into `project-spec/meta-user` and runs `petalinux-build -c opendla`. It does not create a full project automatically, because the project hardware import and board configuration should be explicit dissertation steps.

## Git Workflow

Use the branch `test/vp-driver-correctness-loop`. Commit only source, scripts, recipes, tests, documentation, and lock metadata. Do not commit `.external/`, `.work/`, `artifacts/`, kernel build trees, rootfs images, modules, logs, or generated tensors.
