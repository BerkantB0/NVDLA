#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-}"
SOURCES="${SOURCES_DIR:-$ROOT/.external/sources}"
WORK="${WORK_DIR:-$ROOT/.work/vp-modern}"
ARTIFACTS="${ARTIFACTS_DIR:-$ROOT/artifacts}"
NVDLA_VP_SOURCE="${NVDLA_VP_SOURCE:-$SOURCES/nvdla-vp}"
NVDLA_HW_SOURCE="${NVDLA_HW_SOURCE:-$SOURCES/nvdla-hw}"
VP_SMALL_WORK="${VP_SMALL_WORK:-$WORK/vp-small}"
VP_HW_PROJECT="${VP_HW_PROJECT:-nv_small}"
SYSTEMC_PREFIX="${SYSTEMC_PREFIX:-${VP_SYSTEMC_PREFIX:-/usr/local/systemc-2.3.0}}"
VP_CMAKE_BUILD_TYPE="${VP_CMAKE_BUILD_TYPE:-Debug}"
VP_DISABLE_WERROR="${VP_DISABLE_WERROR:-1}"
NVDLA_CMOD_CXXFLAGS="${NVDLA_CMOD_CXXFLAGS:--Wno-error}"

HW_WORK="$VP_SMALL_WORK/hw"
VP_WORK="$VP_SMALL_WORK/vp"
VP_BUILD="$VP_SMALL_WORK/vp-build"
VP_INSTALL="$VP_SMALL_WORK/install"
CMOD_LIB="$HW_WORK/outdir/$VP_HW_PROJECT/cmod/release/lib/libnvdla_cmod.so"
CMOD_INCLUDE="$HW_WORK/outdir/$VP_HW_PROJECT/cmod/release/include/NV_nvdla.h"
VP_BINARY="$VP_INSTALL/bin/aarch64_toplevel"

CURRENT_RUN_ID=""
CURRENT_RUN_DIR=""
SYSTEMC_LIB_DIR=""

usage() {
  echo "Usage: $0 {cmod|bin|verify|all}" >&2
}

start_run() {
  local phase="$1"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  CURRENT_RUN_ID="${RUN_ID:-$stamp-vp-small-$phase}"
  CURRENT_RUN_DIR="$ARTIFACTS/$CURRENT_RUN_ID"
  mkdir -p "$CURRENT_RUN_DIR"
  echo "Artifact run: $CURRENT_RUN_DIR"
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

run_logged_in() {
  local cwd="$1"
  local log="$2"
  shift 2
  (cd "$cwd" && run_logged "$log" "$@")
}

sha_file() {
  local path="$1"
  if [[ -f "$path" ]] && command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  fi
}

git_sha() {
  local path="$1"
  if [[ -d "$path/.git" ]]; then
    git -C "$path" rev-parse HEAD 2>/dev/null || true
  fi
}

json_string() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '"%s"' "$value"
}

write_manifest() {
  local status="$1"
  local phase="$2"
  local reason="${3:-}"
  local reason_json="null"
  if [[ -n "$reason" ]]; then
    reason_json="$(json_string "$reason")"
  fi
  cat >"$CURRENT_RUN_DIR/manifest.json" <<EOF
{
  "schema_version": 1,
  "run_id": "$CURRENT_RUN_ID",
  "lane": "vp-small",
  "phase": "$phase",
  "status": "$status",
  "reason": $reason_json,
  "vp_hw_project": "$VP_HW_PROJECT",
  "paths": {
    "sources": "$SOURCES",
    "nvdla_vp_source": "$NVDLA_VP_SOURCE",
    "nvdla_hw_source": "$NVDLA_HW_SOURCE",
    "vp_small_work": "$VP_SMALL_WORK",
    "hw_work": "$HW_WORK",
    "vp_work": "$VP_WORK",
    "vp_build": "$VP_BUILD",
    "vp_install": "$VP_INSTALL",
    "systemc_prefix": "$SYSTEMC_PREFIX",
    "systemc_lib_dir": "$SYSTEMC_LIB_DIR"
  },
  "sources": {
    "nvdla_vp": "$(git_sha "$NVDLA_VP_SOURCE")",
    "nvdla_hw": "$(git_sha "$NVDLA_HW_SOURCE")",
    "nvdla_vp_work": "$(git_sha "$VP_WORK")",
    "nvdla_hw_work": "$(git_sha "$HW_WORK")"
  },
  "artifacts": {
    "cmod": {
      "path": "$CMOD_LIB",
      "sha256": "$(sha_file "$CMOD_LIB")"
    },
    "cmod_header": {
      "path": "$CMOD_INCLUDE",
      "sha256": "$(sha_file "$CMOD_INCLUDE")"
    },
    "aarch64_toplevel": {
      "path": "$VP_BINARY",
      "sha256": "$(sha_file "$VP_BINARY")"
    },
    "cmake_cache": {
      "path": "$VP_BUILD/CMakeCache.txt",
      "sha256": "$(sha_file "$VP_BUILD/CMakeCache.txt")"
    }
  },
  "logs": [
$(find "$CURRENT_RUN_DIR" -maxdepth 1 -type f -name '*.log' -printf '    "%f",\n' 2>/dev/null | sed '$ s/,$//')
  ]
}
EOF
  echo "Wrote $CURRENT_RUN_DIR/manifest.json"
}

finish_fail() {
  local phase="$1"
  local reason="$2"
  write_manifest "fail" "$phase" "$reason"
  exit 1
}

finish_blocked() {
  local phase="$1"
  local reason="$2"
  write_manifest "blocked" "$phase" "$reason"
  echo "BLOCKED: $reason" >&2
  exit 2
}

require_cmd() {
  local phase="$1"
  local tool="$2"
  if ! command -v "$tool" >/dev/null 2>&1; then
    finish_blocked "$phase" "missing required host tool: $tool"
  fi
}

resolve_systemc() {
  local phase="$1"
  if [[ ! -f "$SYSTEMC_PREFIX/include/systemc.h" ]]; then
    finish_blocked "$phase" "SYSTEMC_PREFIX does not contain include/systemc.h: $SYSTEMC_PREFIX"
  fi
  local dir name
  for dir in "$SYSTEMC_PREFIX/lib-linux64" "$SYSTEMC_PREFIX/lib64" "$SYSTEMC_PREFIX/lib-linux" "$SYSTEMC_PREFIX/lib"; do
    for name in libsystemc-2.3.0.so libsystemc.so libsystemc.a; do
      if [[ -f "$dir/$name" ]]; then
        SYSTEMC_LIB_DIR="$dir"
        return 0
      fi
    done
  done
  finish_blocked "$phase" "SYSTEMC_PREFIX does not contain a SystemC library: $SYSTEMC_PREFIX"
}

prepare_worktree() {
  local source="$1"
  local dst="$2"
  local phase="$3"
  if [[ ! -d "$source/.git" ]]; then
    finish_blocked "$phase" "missing pinned source checkout: $source; run make sources-vp"
  fi
  mkdir -p "$(dirname "$dst")"
  if [[ ! -d "$dst/.git" ]]; then
    git clone "$source" "$dst" >/dev/null
  fi
  local commit
  commit="$(git -C "$source" rev-parse HEAD)"
  git -C "$dst" fetch "$source" "$commit" >/dev/null
  git -C "$dst" checkout --detach FETCH_HEAD >/dev/null
  git -C "$dst" reset --hard FETCH_HEAD >/dev/null
  git -C "$dst" clean -fdx >/dev/null
  if [[ -f "$dst/.gitmodules" ]]; then
    local key sub_path sub_name local_submodule
    while read -r key sub_path; do
      sub_name="${key#submodule.}"
      sub_name="${sub_name%.path}"
      local_submodule="$source/$sub_path"
      if [[ -e "$local_submodule/.git" ]]; then
        git -C "$dst" config "submodule.$sub_name.url" "$local_submodule"
      fi
    done < <(git -C "$dst" config -f .gitmodules --get-regexp '^submodule\..*\.path$' || true)
    git -C "$dst" submodule update --init >/dev/null
    if [[ -e "$dst/libs/qbox/.git" && -e "$source/libs/qbox/dtc/.git" ]]; then
      git -C "$dst/libs/qbox" config submodule.dtc.url "$source/libs/qbox/dtc"
      git -C "$dst/libs/qbox" submodule update --init dtc >/dev/null
    fi
  fi
}

build_cmod() {
  local phase="cmod"
  start_run "$phase"
  local log="$CURRENT_RUN_DIR/cmod.log"
  local cpp_bin gcc_bin gxx_bin perl_bin java_bin python_bin
  require_cmd "$phase" git
  require_cmd "$phase" make
  require_cmd "$phase" gcc
  require_cmd "$phase" g++
  require_cmd "$phase" perl
  require_cmd "$phase" java
  cpp_bin="$(command -v cpp || true)"
  gcc_bin="$(command -v gcc || true)"
  gxx_bin="$(command -v g++ || true)"
  perl_bin="$(command -v perl || true)"
  java_bin="$(command -v java || true)"
  python_bin="${PYTHON_BIN:-$(command -v python || command -v python3 || true)}"
  if [[ -z "$cpp_bin" || -z "$gcc_bin" || -z "$gxx_bin" || -z "$perl_bin" || -z "$java_bin" || -z "$python_bin" ]]; then
    finish_blocked "$phase" "missing one or more HW generation tools: cpp=$cpp_bin gcc=$gcc_bin g++=$gxx_bin perl=$perl_bin java=$java_bin python=$python_bin"
  fi
  resolve_systemc "$phase"
  prepare_worktree "$NVDLA_HW_SOURCE" "$HW_WORK" "$phase"

  echo "Building $VP_HW_PROJECT CMOD from $HW_WORK"
  echo "Using SYSTEMC_PREFIX=$SYSTEMC_PREFIX"
  run_logged "$log" make -C "$HW_WORK" USE_VM_ENV=1 VM_PROJ="$VP_HW_PROJECT" VM_SYSTEMC="$SYSTEMC_PREFIX" VM_CPP="$cpp_bin" VM_GCC="$gcc_bin" VM_CXX="$gxx_bin" VM_PERL="$perl_bin" VM_JAVA="$java_bin" VM_PYTHON="$python_bin" tree.make \
    || finish_fail "$phase" "failed to generate tree.make"
  run_logged "$log" make -C "$HW_WORK/spec/defs" PROJECT="$VP_HW_PROJECT" OUTDIR=outdir CPP="$cpp_bin" PYTHON="$python_bin" \
    || finish_fail "$phase" "failed to generate nv_small project definitions"
  run_logged "$log" make -C "$HW_WORK/spec/manual" PROJECT="$VP_HW_PROJECT" OUTDIR=outdir CPP="$cpp_bin" PERL="$perl_bin" JAVA="$java_bin" PYTHON="$python_bin" \
    || finish_fail "$phase" "failed to generate nv_small manual register headers"
  if [[ ! -f "$HW_WORK/outdir/$VP_HW_PROJECT/spec/manual/opendla.uh" || ! -f "$HW_WORK/outdir/$VP_HW_PROJECT/spec/manual/opendla.h" ]]; then
    finish_fail "$phase" "manual spec generation completed but opendla headers were missing"
  fi
  run_logged "$log" make -C "$HW_WORK/cmod" PROJECT="$VP_HW_PROJECT" OUTDIR=outdir SYSTEMC="$SYSTEMC_PREFIX" CXX="$gxx_bin" CC="$gxx_bin" CPP="$cpp_bin" PERL="$perl_bin" CXXFLAGS="$NVDLA_CMOD_CXXFLAGS" \
    || finish_fail "$phase" "failed to build nv_small CMOD"

  if [[ ! -f "$CMOD_LIB" || ! -f "$CMOD_INCLUDE" ]]; then
    finish_fail "$phase" "CMOD build completed but expected release files were missing"
  fi
  sha256sum "$CMOD_LIB" "$CMOD_INCLUDE" | tee -a "$log"
  write_manifest "pass" "$phase"
}

build_bin() {
  local phase="bin"
  start_run "$phase"
  local log="$CURRENT_RUN_DIR/bin.log"
  require_cmd "$phase" git
  require_cmd "$phase" cmake
  require_cmd "$phase" make
  require_cmd "$phase" gcc
  require_cmd "$phase" g++
  resolve_systemc "$phase"
  prepare_worktree "$NVDLA_VP_SOURCE" "$VP_WORK" "$phase"

  if [[ ! -f "$CMOD_LIB" || ! -f "$CMOD_INCLUDE" ]]; then
    finish_blocked "$phase" "missing nv_small CMOD release; run make vp-small-cmod first"
  fi
  if [[ "$VP_DISABLE_WERROR" == "1" ]]; then
    perl -0pi -e 's/[ \t]-Werror\b//g' "$VP_WORK/CMakeLists.txt" "$VP_WORK/models/nvdla/CMakeLists.txt"
  fi
  sed -i 's#${CMAKE_SOURCE_DIR}/libs/tlm2c.build#${CMAKE_BINARY_DIR}/libs/tlm2c.build#g' "$VP_WORK/CMakeLists.txt"

  echo "Building $VP_HW_PROJECT VP binary from $VP_WORK"
  echo "Using NVDLA_HW_PREFIX=$HW_WORK"
  echo "Using SYSTEMC_PREFIX=$SYSTEMC_PREFIX"
  mkdir -p "$VP_BUILD" "$VP_INSTALL"
  run_logged_in "$VP_BUILD" "$log" cmake \
    -DCMAKE_INSTALL_PREFIX="$VP_INSTALL" \
    -DSYSTEMC_PREFIX="$SYSTEMC_PREFIX" \
    -DNVDLA_HW_PREFIX="$HW_WORK" \
    -DNVDLA_HW_PROJECT="$VP_HW_PROJECT" \
    -DCMAKE_BUILD_TYPE="$VP_CMAKE_BUILD_TYPE" \
    "$VP_WORK" \
    || finish_fail "$phase" "VP CMake configure failed"
  if [[ -f "$VP_WORK/libs/greenlib/greenscript/gsp_sc.i" ]]; then
    mkdir -p "$VP_BUILD/libs/greenlib/greenscript"
    ln -sf "$VP_WORK/libs/greenlib/greenscript/gsp_sc.i" "$VP_BUILD/libs/greenlib/greenscript/gsp_sc.i"
    ln -sfn "$VP_WORK/libs/greenlib/greenscript/include" "$VP_BUILD/libs/greenlib/greenscript/include"
  fi
  run_logged "$log" make -C "$VP_BUILD" -j"$(nproc)" \
    || finish_fail "$phase" "VP build failed"
  run_logged "$log" make -C "$VP_BUILD" install \
    || finish_fail "$phase" "VP install failed"

  verify_bin "$phase"
}

verify_bin() {
  local phase="${1:-verify}"
  if [[ -z "$CURRENT_RUN_DIR" ]]; then
    start_run "$phase"
  fi
  local log="$CURRENT_RUN_DIR/verify.log"
  resolve_systemc "$phase"
  if [[ ! -x "$VP_BINARY" ]]; then
    finish_blocked "$phase" "missing executable nv_small VP binary: $VP_BINARY"
  fi
  if [[ ! -f "$VP_BUILD/CMakeCache.txt" ]]; then
    finish_blocked "$phase" "missing VP CMakeCache.txt: $VP_BUILD/CMakeCache.txt"
  fi
  if ! grep -q "NVDLA_HW_PROJECT.*$VP_HW_PROJECT" "$VP_BUILD/CMakeCache.txt"; then
    finish_fail "$phase" "CMakeCache.txt does not record NVDLA_HW_PROJECT=$VP_HW_PROJECT"
  fi
  LD_LIBRARY_PATH="$VP_INSTALL/lib:$(dirname "$CMOD_LIB"):$SYSTEMC_LIB_DIR:${LD_LIBRARY_PATH:-}" ldd "$VP_BINARY" >"$CURRENT_RUN_DIR/ldd-aarch64_toplevel.log" 2>&1 \
    || finish_fail "$phase" "ldd failed for $VP_BINARY"
  if ! grep -q "libnvdla_cmod" "$CURRENT_RUN_DIR/ldd-aarch64_toplevel.log"; then
    finish_fail "$phase" "aarch64_toplevel does not link libnvdla_cmod"
  fi
  sha256sum "$VP_BINARY" "$VP_BUILD/CMakeCache.txt" "$CMOD_LIB" | tee -a "$log"
  write_manifest "pass" "$phase"
}

case "$ACTION" in
  cmod) build_cmod ;;
  bin) build_bin ;;
  verify) start_run "verify"; verify_bin "verify" ;;
  all) build_cmod; build_bin ;;
  *) usage; exit 2 ;;
esac
