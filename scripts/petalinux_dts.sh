#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "dts"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

files_dir="$PETALINUX_PROJECT/project-spec/meta-user/recipes-bsp/device-tree/files"
recipe_dir="$PETALINUX_PROJECT/project-spec/meta-user/recipes-bsp/device-tree"
mkdir -p "$files_dir"

generated="$RUN_DIR/nvdla-user.dtsi"
audit="$RUN_DIR/petalinux-dts-audit.json"
if ! python3 -m nvdla_test_framework petalinux-dts \
  --lock "$ROOT/repro.lock.json" \
  --xsa "$XSA_PATH" \
  --out "$generated" \
  --audit-out "$audit" 2>&1 | tee "$BUILD_LOG"; then
  pl_finish_fail "DTS generation failed"
fi

installed="$files_dir/nvdla-user.dtsi"
cp "$generated" "$installed"
ethernet_source="$ROOT/recipes/petalinux/device-tree/files/zcu102-ethernet.dtsi"
ethernet_installed="$files_dir/zcu102-ethernet.dtsi"
cp "$ethernet_source" "$ethernet_installed"

system_user="$files_dir/system-user.dtsi"
if [[ ! -f "$system_user" ]]; then
  echo '/* PetaLinux user device tree additions. */' >"$system_user"
fi
for include_line in \
  '/include/ "nvdla-user.dtsi"' \
  '/include/ "zcu102-ethernet.dtsi"'; do
  if ! grep -Fq "$include_line" "$system_user"; then
    printf '\n%s\n' "$include_line" >>"$system_user"
  fi
done

bbappend="$recipe_dir/device-tree.bbappend"
if [[ ! -f "$bbappend" ]]; then
  cat >"$bbappend" <<'EOF'
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = " file://system-user.dtsi file://nvdla-user.dtsi file://zcu102-ethernet.dtsi"
EOF
else
  if ! grep -Fq 'nvdla-user.dtsi' "$bbappend"; then
    echo 'SRC_URI:append = " file://nvdla-user.dtsi"' >>"$bbappend"
  fi
  if ! grep -Fq 'zcu102-ethernet.dtsi' "$bbappend"; then
    echo 'SRC_URI:append = " file://zcu102-ethernet.dtsi"' >>"$bbappend"
  fi
fi

export DTS_PATH="$installed"
export DTS_AUDIT_PATH="$audit"
export ETHERNET_DTS_PATH="$ethernet_installed"
{
  echo "Installed board DTS fragments"
  echo "  NVDLA: $installed"
  echo "  Ethernet: $ethernet_installed"
  echo "  system-user include: $system_user"
  echo "  audit: $audit"
} | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
