# Reproducible Runbook

## Host Assumptions

Run commands from Ubuntu WSL in the repository root:

```sh
cd /mnt/c/Users/berkant/Dev/NVLDA-Peta
```

The current known environment is:

- Ubuntu 24.04 WSL2
- Docker Desktop available inside WSL
- PetaLinux installed at `/opt/pkg/petalinux/2024.1`
- PetaLinux metadata revision `b31575b49f230b006aa3193cb564368e357777ed`

PetaLinux 2024.1 warns that Ubuntu 24.04 is not a supported OS. The framework records this as an environment fact; it does not hide the warning.

## Fast Regression Gate

```sh
make test
```

This checks the host tools, validates `repro.lock.json`, audits the XSA, boots the stock NVDLA VP to the Buildroot login prompt, and verifies the PetaLinux command environment.

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

The old VP SystemC model needs `SC_SIGNAL_WRITE_CHECK=DISABLE` for the modern
runtime workload path; otherwise the SDP run can abort on a multiple-driver
signal check in the VP model. The modern Lua generated by the test framework
also maps the VP RAM target at `0x40000000..0x7fffffff`, matching QEMU `virt`
RAM. A mismatch here produces DLA DBB `TLM_ADDRESS_ERROR_RESPONSE` failures.
Threaded target userspace requires `CONFIG_FUTEX`; `make vp-kernel` enables it
because both `sshd` and `nvdla_runtime -s` use futex-backed libc primitives.

Useful controls:

```sh
VP_TIMEOUT=180 LANE=modern make vp-test
REPEAT=100 VP_TIMEOUT=300 LANE=modern make vp-test
REPEAT=100 MODE=runtime WORKLOAD=sdp_regression_small LANE=modern VP_TIMEOUT=600 make vp-test
NVDLA_RUNTIME_TIMEOUT=300 MODE=runtime WORKLOAD=sdp_regression_full LANE=modern VP_TIMEOUT=420 make vp-test
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

Clean runtime evidence from `20260702T220702Z-vp-modern-runtime` showed the VP
booted Linux 6.6, `opendla.ko` loaded, `/dev/dri/renderD128` appeared, and the
runtime server/client completed with `NVDLA_RUNTIME_TIMEOUT=300`. That run used
the primary `sdp_regression_small` workload while the driver probed
`nvidia,nvdla_os_initial`, so the failure is classified as a workload/config
mismatch. The returned tensor hash was
`A5C53563E8AB82FB6349C44902211EB04A535FC2D73A704C33035C004194548D`, which does
not match the `nv_small` golden. `sdp_regression_full` can be used to test the
stock VP as a control, but the main correctness claim requires an `nv_small` VP
or board environment that probes `nvidia,nv_small`.

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
