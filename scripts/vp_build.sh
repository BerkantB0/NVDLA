#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-}"
ARCH="${ARCH:-arm64}"
WORK="${WORK_DIR:-$ROOT/.work/vp-modern}"
SOURCES="${SOURCES_DIR:-$ROOT/.external/sources}"
LINUX="$SOURCES/linux-xlnx"
BUILDROOT="$SOURCES/buildroot"
NVDLA_SW="${PATCHED_NVDLA_SW:-$ROOT/.work/nvdla-sw-patched}"
ARTIFACTS="${ARTIFACTS_DIR:-$ROOT/artifacts}"
BUILDROOT_CROSS="$WORK/buildroot/host/bin/aarch64-buildroot-linux-gnu-"
APT_CROSS="aarch64-linux-gnu-"
USER_CROSS_COMPILE="${CROSS_COMPILE:-}"
VP_BUILD_PATH="${VP_BUILD_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

CURRENT_RUN_ID=""
CURRENT_RUN_DIR=""
RESOLVED_CROSS_COMPILE=""
TOOLCHAIN_SOURCE=""
TOOLCHAIN_GCC=""
TOOLCHAIN_GXX=""
TOOLCHAIN_MACHINE=""
TOOLCHAIN_VERSION=""
TOOLCHAIN_CXX_VERSION=""

usage() {
  echo "Usage: $0 {toolchain|kernel|rootfs|kmod|all}" >&2
}

start_run() {
  local phase="$1"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  CURRENT_RUN_ID="${RUN_ID:-$stamp-vp-$phase}"
  CURRENT_RUN_DIR="$ARTIFACTS/$CURRENT_RUN_ID"
  mkdir -p "$CURRENT_RUN_DIR"
  echo "Artifact run: $CURRENT_RUN_DIR"
}

require_dir() {
  local dir="$1"
  local hint="$2"
  if [[ ! -d "$dir" ]]; then
    echo "ERROR: missing $dir" >&2
    echo "       $hint" >&2
    return 2
  fi
}

run_logged() {
  local log="$1"
  shift
  mkdir -p "$(dirname "$log")"
  {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  } >>"$log"
  set +e
  "$@" 2>&1 | tee -a "$log"
  local status=${PIPESTATUS[0]}
  set -e
  return "$status"
}

refresh_toolchain_metadata() {
  local prefix="$1"
  TOOLCHAIN_GCC="$(command -v "${prefix}gcc" 2>/dev/null || true)"
  TOOLCHAIN_GXX="$(command -v "${prefix}g++" 2>/dev/null || true)"
  TOOLCHAIN_MACHINE="$("${prefix}gcc" -dumpmachine 2>/dev/null || true)"
  TOOLCHAIN_VERSION="$("${prefix}gcc" --version 2>/dev/null | head -n 1 || true)"
  TOOLCHAIN_CXX_VERSION="$("${prefix}g++" --version 2>/dev/null | head -n 1 || true)"
}

verify_cross_compile() {
  local prefix="$1"
  if ! command -v "${prefix}gcc" >/dev/null 2>&1; then
    return 1
  fi
  refresh_toolchain_metadata "$prefix"
}

verify_cross_compile_cxx() {
  local prefix="$1"
  if ! verify_cross_compile "$prefix"; then
    return 1
  fi
  if ! command -v "${prefix}g++" >/dev/null 2>&1; then
    return 1
  fi
  refresh_toolchain_metadata "$prefix"
}

resolve_cross_compile() {
  local quiet="${1:-}"
  if [[ -n "$USER_CROSS_COMPILE" ]]; then
    if verify_cross_compile "$USER_CROSS_COMPILE"; then
      RESOLVED_CROSS_COMPILE="$USER_CROSS_COMPILE"
      TOOLCHAIN_SOURCE="user"
      export CROSS_COMPILE="$RESOLVED_CROSS_COMPILE"
      return 0
    fi
    echo "ERROR: CROSS_COMPILE is set but ${USER_CROSS_COMPILE}gcc was not found" >&2
    return 2
  fi

  if verify_cross_compile "$BUILDROOT_CROSS"; then
    RESOLVED_CROSS_COMPILE="$BUILDROOT_CROSS"
    TOOLCHAIN_SOURCE="buildroot"
    export CROSS_COMPILE="$RESOLVED_CROSS_COMPILE"
    return 0
  fi

  if verify_cross_compile "$APT_CROSS"; then
    RESOLVED_CROSS_COMPILE="$APT_CROSS"
    TOOLCHAIN_SOURCE="apt"
    export CROSS_COMPILE="$RESOLVED_CROSS_COMPILE"
    return 0
  fi

  if [[ "$quiet" == "quiet" ]]; then
    return 2
  fi

  cat >&2 <<EOF
ERROR: no ARM64 Linux cross compiler found.

Tried:
  - Buildroot: $BUILDROOT_CROSS
  - apt fallback: ${APT_CROSS}

Fix one of these ways:
  - run: make vp-toolchain
  - install apt packages: gcc-aarch64-linux-gnu g++-aarch64-linux-gnu bc bison flex libssl-dev make
  - export CROSS_COMPILE=/path/to/aarch64-linux-prefix-
EOF
  return 2
}

resolve_cross_compile_cxx() {
  resolve_cross_compile "$@" || return $?
  if command -v "${RESOLVED_CROSS_COMPILE}g++" >/dev/null 2>&1; then
    refresh_toolchain_metadata "$RESOLVED_CROSS_COMPILE"
    return 0
  fi

  cat >&2 <<EOF
ERROR: ARM64 C++ cross compiler not found for:
  CROSS_COMPILE=$RESOLVED_CROSS_COMPILE

Runtime builds require ${RESOLVED_CROSS_COMPILE}g++.

Fix one of these ways:
  - run: make vp-toolchain
  - install apt packages: gcc-aarch64-linux-gnu g++-aarch64-linux-gnu
  - export CROSS_COMPILE=/path/to/aarch64-linux-prefix-
EOF
  return 2
}

require_buildroot_host_tools() {
  local missing=()
  local tool
  for tool in cpio unzip; do
    if ! PATH="$VP_BUILD_PATH" command -v "$tool" >/dev/null 2>&1; then
      missing+=("$tool")
    fi
  done
  if ((${#missing[@]} > 0)); then
    echo "ERROR: missing Buildroot host tools: ${missing[*]}" >&2
    echo "       Install in WSL: sudo apt-get install -y cpio unzip" >&2
    return 2
  fi
}

require_rootfs_postprocess_tools() {
  local missing=()
  local tool
  for tool in debugfs; do
    if ! PATH="$VP_BUILD_PATH" command -v "$tool" >/dev/null 2>&1; then
      missing+=("$tool")
    fi
  done
  if ((${#missing[@]} > 0)); then
    echo "ERROR: missing rootfs postprocess tools: ${missing[*]}" >&2
    echo "       Install in WSL: sudo apt-get install -y e2fsprogs" >&2
    return 2
  fi
}

write_environment() {
  local phase="$1"
  {
    echo "run_id=$CURRENT_RUN_ID"
    echo "phase=$phase"
    echo "arch=$ARCH"
    echo "work_dir=$WORK"
    echo "toolchain_source=$TOOLCHAIN_SOURCE"
    echo "cross_compile=$RESOLVED_CROSS_COMPILE"
    echo "toolchain_gcc=$TOOLCHAIN_GCC"
    echo "toolchain_gxx=$TOOLCHAIN_GXX"
    echo "toolchain_machine=$TOOLCHAIN_MACHINE"
    echo "toolchain_version=$TOOLCHAIN_VERSION"
    echo "toolchain_cxx_version=$TOOLCHAIN_CXX_VERSION"
  } >"$CURRENT_RUN_DIR/environment.txt"
}

write_manifest() {
  local status="$1"
  local phase="$2"
  local reason="${3:-}"
  write_environment "$phase"
  export MANIFEST_PATH="$CURRENT_RUN_DIR/manifest.json"
  export RUN_ID_CURRENT="$CURRENT_RUN_ID"
  export PHASE="$phase"
  export STATUS="$status"
  export REASON="$reason"
  export ROOT WORK LINUX BUILDROOT NVDLA_SW ARCH
  export TOOLCHAIN_SOURCE RESOLVED_CROSS_COMPILE TOOLCHAIN_GCC TOOLCHAIN_GXX TOOLCHAIN_MACHINE TOOLCHAIN_VERSION TOOLCHAIN_CXX_VERSION
  python3 - <<'PY'
import hashlib
import json
import os
import subprocess
from pathlib import Path


def env_path(name: str) -> Path:
    return Path(os.environ[name])


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def patch_series_sha(root: Path) -> str | None:
    patches = sorted((root / "patches" / "nvdla-sw").glob("*.patch"))
    if not patches:
        return None
    digest = hashlib.sha256()
    for patch in patches:
        digest.update(patch.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(patch.read_bytes())
    return digest.hexdigest()


def kernel_release(work: Path) -> str | None:
    release = work / "kernel" / "include" / "config" / "kernel.release"
    if release.is_file():
        return release.read_text(encoding="utf-8", errors="replace").strip()
    return None


root = env_path("ROOT")
work = env_path("WORK")
run_dir = Path(os.environ["MANIFEST_PATH"]).parent

known_artifacts = {
    "kernel_image": work / "kernel" / "arch" / "arm64" / "boot" / "Image",
    "kernel_image_vp2m": work / "kernel" / "arch" / "arm64" / "boot" / "Image.vp2m",
    "kernel_config": work / "kernel" / ".config",
    "rootfs_ext4": work / "buildroot" / "images" / "rootfs.ext4",
    "rootfs_smoke_ext4": work / "buildroot" / "images" / "rootfs-smoke.ext4",
    "module": work / "modules" / "opendla.ko",
}

manifest = {
    "schema_version": 1,
    "run_id": os.environ["RUN_ID_CURRENT"],
    "lane": "vp-modern",
    "phase": os.environ["PHASE"],
    "status": os.environ["STATUS"],
    "reason": os.environ.get("REASON") or None,
    "arch": os.environ["ARCH"],
    "sources": {
        "linux_xlnx": git_sha(env_path("LINUX")),
        "buildroot": git_sha(env_path("BUILDROOT")),
        "nvdla_sw_patched": git_sha(env_path("NVDLA_SW")),
        "nvdla_patch_series_sha256": patch_series_sha(root),
    },
    "toolchain": {
        "source": os.environ.get("TOOLCHAIN_SOURCE") or None,
        "cross_compile": os.environ.get("RESOLVED_CROSS_COMPILE") or None,
        "gcc": os.environ.get("TOOLCHAIN_GCC") or None,
        "gxx": os.environ.get("TOOLCHAIN_GXX") or None,
        "machine": os.environ.get("TOOLCHAIN_MACHINE") or None,
        "version": os.environ.get("TOOLCHAIN_VERSION") or None,
        "cxx_version": os.environ.get("TOOLCHAIN_CXX_VERSION") or None,
    },
    "kernel": {
        "version": kernel_release(work),
        "image_sha256": sha256(known_artifacts["kernel_image"]),
    },
    "driver": {
        "module_sha256": sha256(known_artifacts["module"]),
    },
    "artifacts": {
        name: {
            "path": str(path),
            "sha256": sha256(path),
        }
        for name, path in known_artifacts.items()
        if path.exists()
    },
    "logs": sorted(path.name for path in run_dir.glob("*.log")),
}

Path(os.environ["MANIFEST_PATH"]).write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
  echo "Wrote $CURRENT_RUN_DIR/manifest.json"
}

finish_fail() {
  local phase="$1"
  local reason="$2"
  write_manifest "fail" "$phase" "$reason"
  exit 1
}

build_toolchain() {
  start_run "toolchain"
  local log="$CURRENT_RUN_DIR/toolchain.log"

  if [[ -n "$USER_CROSS_COMPILE" ]]; then
    if verify_cross_compile_cxx "$USER_CROSS_COMPILE"; then
      RESOLVED_CROSS_COMPILE="$USER_CROSS_COMPILE"
      TOOLCHAIN_SOURCE="user"
      export CROSS_COMPILE="$RESOLVED_CROSS_COMPILE"
      write_manifest "pass" "toolchain" "using user-provided CROSS_COMPILE"
      return 0
    fi
    finish_fail "toolchain" "user-provided CROSS_COMPILE did not provide gcc and g++"
  fi

  if verify_cross_compile_cxx "$BUILDROOT_CROSS"; then
    RESOLVED_CROSS_COMPILE="$BUILDROOT_CROSS"
    TOOLCHAIN_SOURCE="buildroot"
    export CROSS_COMPILE="$RESOLVED_CROSS_COMPILE"
    write_manifest "pass" "toolchain" "existing Buildroot C/C++ cross compiler found"
    return 0
  fi
  local clean_stale_toolchain=0
  if verify_cross_compile "$BUILDROOT_CROSS"; then
    echo "Existing Buildroot cross compiler is missing g++; rebuilding with C++ enabled"
    clean_stale_toolchain=1
  fi

  require_dir "$BUILDROOT" "Run: make sources-heavy" || finish_fail "toolchain" "missing Buildroot source"
  require_buildroot_host_tools || finish_fail "toolchain" "missing Buildroot host tools"
  mkdir -p "$WORK/buildroot"
  if ((clean_stale_toolchain)); then
    run_logged "$log" env PATH="$VP_BUILD_PATH" make -C "$BUILDROOT" O="$WORK/buildroot" clean \
      || finish_fail "toolchain" "Buildroot clean of stale C-only toolchain failed"
  fi
  echo "Building VP Buildroot toolchain from $BUILDROOT"
  run_logged "$log" env PATH="$VP_BUILD_PATH" make -C "$BUILDROOT" O="$WORK/buildroot" BR2_EXTERNAL="$ROOT/configs/vp/buildroot_external" nvdla_vp_modern_defconfig \
    || finish_fail "toolchain" "Buildroot defconfig failed"
  run_logged "$log" env PATH="$VP_BUILD_PATH" make -C "$BUILDROOT" O="$WORK/buildroot" -j"$(nproc)" toolchain \
    || finish_fail "toolchain" "Buildroot toolchain build failed"

  if verify_cross_compile_cxx "$BUILDROOT_CROSS"; then
    RESOLVED_CROSS_COMPILE="$BUILDROOT_CROSS"
    TOOLCHAIN_SOURCE="buildroot"
    export CROSS_COMPILE="$RESOLVED_CROSS_COMPILE"
    write_manifest "pass" "toolchain" "Buildroot toolchain ready"
    return 0
  fi
  finish_fail "toolchain" "Buildroot completed but gcc/g++ was not found"
}

build_kernel() {
  start_run "kernel"
  local log="$CURRENT_RUN_DIR/kernel.log"
  require_dir "$LINUX" "Run: make sources-heavy" || finish_fail "kernel" "missing linux-xlnx source"
  resolve_cross_compile || finish_fail "kernel" "no ARM64 Linux cross compiler"
  mkdir -p "$WORK/kernel"
  echo "Building VP kernel from $LINUX"
  echo "Using CROSS_COMPILE=$RESOLVED_CROSS_COMPILE ($TOOLCHAIN_SOURCE)"
  run_logged "$log" make -C "$LINUX" O="$WORK/kernel" ARCH="$ARCH" CROSS_COMPILE="$RESOLVED_CROSS_COMPILE" tinyconfig \
    || finish_fail "kernel" "kernel tinyconfig failed"
  run_logged "$log" "$LINUX/scripts/config" --file "$WORK/kernel/.config" \
    --enable CONFIG_EFI \
    --enable CONFIG_EFI_STUB \
    --enable CONFIG_MODULES \
    --enable CONFIG_PRINTK \
    --enable CONFIG_KALLSYMS \
    --enable CONFIG_BINFMT_ELF \
    --enable CONFIG_BINFMT_SCRIPT \
    --enable CONFIG_BLOCK \
    --enable CONFIG_BLK_DEV \
    --enable CONFIG_EXT4_FS \
    --enable CONFIG_DEVTMPFS \
    --enable CONFIG_DEVTMPFS_MOUNT \
    --enable CONFIG_PROC_FS \
    --enable CONFIG_SYSFS \
    --enable CONFIG_TMPFS \
    --enable CONFIG_TTY \
    --enable CONFIG_SERIAL_AMBA_PL011 \
    --enable CONFIG_SERIAL_AMBA_PL011_CONSOLE \
    --enable CONFIG_NET \
    --enable CONFIG_UNIX \
    --enable CONFIG_INET \
    --enable CONFIG_VIRTIO_MENU \
    --enable CONFIG_VIRTIO \
    --enable CONFIG_VIRTIO_MMIO \
    --enable CONFIG_VIRTIO_BLK \
    --enable CONFIG_NET_9P \
    --enable CONFIG_NET_9P_VIRTIO \
    --enable CONFIG_9P_FS \
    --enable CONFIG_9P_FS_POSIX_ACL \
    --enable CONFIG_DRM \
    --enable CONFIG_DRM_ARCPGU \
    --enable CONFIG_DMA_SHARED_BUFFER \
    --enable CONFIG_CMA \
    --enable CONFIG_DMA_CMA \
    --set-val CONFIG_CMA_SIZE_MBYTES 256 \
    || finish_fail "kernel" "kernel config update failed"
  run_logged "$log" make -C "$LINUX" O="$WORK/kernel" ARCH="$ARCH" CROSS_COMPILE="$RESOLVED_CROSS_COMPILE" olddefconfig \
    || finish_fail "kernel" "kernel olddefconfig failed"
  run_logged "$log" make -C "$LINUX" O="$WORK/kernel" ARCH="$ARCH" CROSS_COMPILE="$RESOLVED_CROSS_COMPILE" -j"$(nproc)" Image modules \
    || finish_fail "kernel" "kernel build failed"
  if ! run_logged "$log" python3 - "$WORK/kernel/arch/arm64/boot/Image" "$WORK/kernel/arch/arm64/boot/Image.vp2m" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
data = bytearray(src.read_bytes())
if len(data) < 0x40 or data[0x38:0x3c] != b"ARMd":
    raise SystemExit(f"{src} is not an ARM64 Image")
data[8:16] = (0x200000).to_bytes(8, "little")
dst.write_bytes(data)
PY
  then
    finish_fail "kernel" "VP-compatible Image header patch failed"
  fi
  write_manifest "pass" "kernel"
}

write_rootfs_autorun_script() {
  local script="$1"
  cat >"$script" <<'EOF'
#!/bin/sh
echo "__NVDLA_AUTORUN_BEGIN__"
mkdir -p /mnt/r
mount -t 9p -o trans=virtio,version=9p2000.L r /mnt/r || mount -t 9p -o trans=virtio r /mnt/r
STATUS=$?
echo "__NVDLA_STATUS_payload_mount=$STATUS"
if [ "$STATUS" -eq 0 ]; then
    sh /mnt/r/run-modern-smoke.sh
    STATUS=$?
fi
echo "__NVDLA_SCRIPT_EXIT__=$STATUS"
sync
poweroff -f
EOF
  chmod 0755 "$script"
}

build_smoke_rootfs() {
  local log="$1"
  local base="$WORK/buildroot/images/rootfs.ext4"
  local smoke="$WORK/buildroot/images/rootfs-smoke.ext4"
  local script="$WORK/buildroot/images/S99nvdla-smoke"
  require_rootfs_postprocess_tools || return 2
  if [[ ! -f "$base" ]]; then
    echo "ERROR: missing Buildroot rootfs image: $base" >&2
    return 2
  fi
  cp "$base" "$smoke"
  write_rootfs_autorun_script "$script"
  run_logged "$log" env PATH="$VP_BUILD_PATH" debugfs -w -R "write $script /etc/init.d/S99nvdla-smoke" "$smoke" \
    || return 1
  run_logged "$log" env PATH="$VP_BUILD_PATH" debugfs -w -R "set_inode_field /etc/init.d/S99nvdla-smoke mode 0100755" "$smoke" \
    || return 1
}

build_rootfs() {
  start_run "rootfs"
  local log="$CURRENT_RUN_DIR/rootfs.log"
  require_dir "$BUILDROOT" "Run: make sources-heavy" || finish_fail "rootfs" "missing Buildroot source"
  require_buildroot_host_tools || finish_fail "rootfs" "missing Buildroot host tools"
  require_rootfs_postprocess_tools || finish_fail "rootfs" "missing rootfs postprocess tools"
  mkdir -p "$WORK/buildroot"
  echo "Building VP rootfs from $BUILDROOT"
  run_logged "$log" env PATH="$VP_BUILD_PATH" make -C "$BUILDROOT" O="$WORK/buildroot" BR2_EXTERNAL="$ROOT/configs/vp/buildroot_external" nvdla_vp_modern_defconfig \
    || finish_fail "rootfs" "Buildroot defconfig failed"
  run_logged "$log" env PATH="$VP_BUILD_PATH" make -C "$BUILDROOT" O="$WORK/buildroot" -j"$(nproc)" \
    || finish_fail "rootfs" "Buildroot rootfs build failed"
  build_smoke_rootfs "$log" || finish_fail "rootfs" "rootfs smoke autorun image creation failed"
  resolve_cross_compile "quiet" || true
  write_manifest "pass" "rootfs"
}

build_kmod() {
  start_run "kmod"
  local log="$CURRENT_RUN_DIR/kmod.log"
  if [[ ! -d "$NVDLA_SW" ]]; then
    "$ROOT/scripts/nvdla_patch_queue.sh" apply
  fi
  require_dir "$NVDLA_SW" "Run: make patch-apply" || finish_fail "kmod" "missing patched nvdla/sw worktree"
  require_dir "$WORK/kernel" "Run: make vp-kernel" || finish_fail "kmod" "missing VP kernel build directory"
  require_dir "$LINUX" "Run: make sources-heavy" || finish_fail "kmod" "missing linux-xlnx source"
  resolve_cross_compile || finish_fail "kmod" "no ARM64 Linux cross compiler"

  local kmd="$NVDLA_SW/kmd/port/linux"
  require_dir "$kmd" "Run: make patch-apply" || finish_fail "kmod" "missing NVDLA KMD path"
  echo "Building opendla.ko against $WORK/kernel"
  echo "Using CROSS_COMPILE=$RESOLVED_CROSS_COMPILE ($TOOLCHAIN_SOURCE)"
  run_logged "$log" make -C "$LINUX" O="$WORK/kernel" M="$kmd" ARCH="$ARCH" CROSS_COMPILE="$RESOLVED_CROSS_COMPILE" modules \
    || finish_fail "kmod" "opendla.ko build failed; see kmod.log"
  mkdir -p "$WORK/modules"
  cp "$kmd/opendla.ko" "$WORK/modules/opendla.ko"
  sha256sum "$WORK/modules/opendla.ko" | tee -a "$log"
  write_manifest "pass" "kmod"
}

case "$ACTION" in
  toolchain) build_toolchain ;;
  kernel) build_kernel ;;
  rootfs) build_rootfs ;;
  kmod) build_kmod ;;
  all) build_toolchain; build_kernel; build_rootfs; build_kmod ;;
  *) usage; exit 2 ;;
esac
