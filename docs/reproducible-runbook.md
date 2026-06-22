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

## Modern VP Build Lane

The modern VP lane is intentionally split so driver changes can be tested without rebuilding everything:

```sh
make vp-kernel
make vp-rootfs
make vp-kmod
LANE=modern make vp-test
```

Set `CROSS_COMPILE` if the default `aarch64-linux-gnu-` toolchain is not available. The PetaLinux cross toolchain can also be used after sourcing `settings.sh`.

## PetaLinux KMD Lane

To build the same patched driver inside an existing PetaLinux project:

```sh
export PETALINUX_PROJECT=/path/to/project
make petalinux-kmod
```

The script installs the provided recipe skeleton into `project-spec/meta-user` and runs `petalinux-build -c opendla`. It does not create a full project automatically, because the project hardware import and board configuration should be explicit dissertation steps.

## Git Workflow

Use the branch `test/vp-driver-correctness-loop`. Commit only source, scripts, recipes, tests, documentation, and lock metadata. Do not commit `.external/`, `.work/`, `artifacts/`, kernel build trees, rootfs images, modules, logs, or generated tensors.

