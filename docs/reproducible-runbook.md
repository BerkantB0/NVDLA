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

Toolchain policy:

- `CROSS_COMPILE=/path/to/prefix-` always wins when set.
- Otherwise the framework uses the pinned Buildroot compiler at `.work/vp-modern/buildroot/host/bin/aarch64-buildroot-linux-gnu-`.
- If the Buildroot compiler does not exist, the framework accepts an apt-installed `aarch64-linux-gnu-` compiler.
- If neither exists, run `make vp-toolchain` or install `gcc-aarch64-linux-gnu g++-aarch64-linux-gnu bc bison flex libssl-dev make cpio unzip`.

`make vp-toolchain` builds only the Buildroot host toolchain from the pinned Buildroot source. It is the preferred reproducible path because the resulting compiler is tied to `repro.lock.json`; the apt compiler fallback is useful for quick local compile triage.

The Buildroot calls run with a clean Linux-only `PATH` by default because WSL may
import Windows paths with spaces. Override `VP_BUILD_PATH` only if a required
host tool lives outside `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`.

Each VP build target writes a run directory under `artifacts/<timestamp>-vp-<phase>/` with `manifest.json`, `environment.txt`, and the phase log. For `vp-kernel`, the manifest records the linux-xlnx source SHA, kernel image hash, kernel release when available, and toolchain identity. For `vp-kmod`, it records the patched `nvdla/sw` SHA, patch-series hash, selected toolchain, module hash when build succeeds, and the `kmod.log` compile output when build fails.

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
