#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/petalinux_common.sh"

cd "$ROOT"
pl_start_run "power"

if ! pl_source_settings; then
  pl_finish_fail "failed to source PetaLinux settings"
fi
pl_require_project

dt_recipe="$PETALINUX_PROJECT/project-spec/meta-user/recipes-bsp/device-tree"
dt_files="$dt_recipe/files"
mkdir -p "$dt_files"

POWER_DTS_PATH="$dt_files/zcu102-power.dtsi"
cp "$ROOT/recipes/petalinux/device-tree/files/zcu102-power.dtsi" "$POWER_DTS_PATH"

system_user="$dt_files/system-user.dtsi"
if [[ ! -f "$system_user" ]]; then
  echo '/* PetaLinux user device tree additions. */' >"$system_user"
fi
include_line='/include/ "zcu102-power.dtsi"'
if ! grep -Fq "$include_line" "$system_user"; then
  printf '\n%s\n' "$include_line" >>"$system_user"
fi

dt_append="$dt_recipe/device-tree.bbappend"
if [[ ! -f "$dt_append" ]]; then
  cat >"$dt_append" <<'EOF'
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = " file://system-user.dtsi file://zcu102-power.dtsi"
EOF
elif ! grep -Fq 'zcu102-power.dtsi' "$dt_append"; then
  echo 'SRC_URI:append = " file://zcu102-power.dtsi"' >>"$dt_append"
fi

kernel_recipe="$PETALINUX_PROJECT/project-spec/meta-user/recipes-kernel/linux"
rm -rf "$kernel_recipe/linux-xlnx"
mkdir -p "$kernel_recipe/linux-xlnx/files"
cp "$ROOT/recipes/petalinux/kernel/linux-xlnx/linux-xlnx_%.bbappend" "$kernel_recipe/"
cp "$ROOT/recipes/petalinux/kernel/linux-xlnx/files/nvdla-power-monitor.cfg" \
  "$kernel_recipe/linux-xlnx/files/"
POWER_KERNEL_CONFIG_PATH="$kernel_recipe/linux-xlnx/files/nvdla-power-monitor.cfg"

export POWER_DTS_PATH POWER_KERNEL_CONFIG_PATH
{
  echo "Installed ZCU102 power monitor integration"
  echo "  device tree: $POWER_DTS_PATH"
  echo "  kernel config: $POWER_KERNEL_CONFIG_PATH"
  echo "Building kernel and device tree"
} | tee "$BUILD_LOG"

petalinux-build -p "$PETALINUX_PROJECT" -c kernel 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "PetaLinux kernel build failed"
petalinux-build -p "$PETALINUX_PROJECT" -c device-tree 2>&1 | tee -a "$BUILD_LOG" \
  || pl_finish_fail "PetaLinux device-tree build failed"

kernel_config="$(
  find "$PETALINUX_PROJECT/build/tmp/work-shared" \
    -path '*/kernel-build-artifacts/.config' -type f \
    -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d ' ' -f 2-
)"
if [[ -z "$kernel_config" || ! -f "$kernel_config" ]]; then
  pl_finish_fail "built kernel configuration was not found"
fi

required=(
  CONFIG_HWMON=y
  CONFIG_I2C_MUX=y
  CONFIG_I2C_MUX_PCA954x=y
  CONFIG_SENSORS_INA2XX=y
)
for option in "${required[@]}"; do
  if ! grep -Fxq "$option" "$kernel_config"; then
    pl_finish_fail "built kernel is missing $option"
  fi
done

cp "$kernel_config" "$RUN_DIR/kernel.config"
grep -E 'CONFIG_(HWMON|I2C_MUX|I2C_MUX_PCA954x|SENSORS_INA2XX)=' \
  "$kernel_config" | sort >"$RUN_DIR/power-kernel-options.txt"

system_dtb="$PETALINUX_PROJECT/images/linux/system.dtb"
fdtget="${FDTGET:-$PETALINUX_DIR/sysroots/x86_64-petalinux-linux/usr/bin/fdtget}"
if [[ ! -f "$system_dtb" ]]; then
  pl_finish_fail "deployed system.dtb was not found"
fi
if [[ ! -x "$fdtget" ]]; then
  pl_finish_fail "fdtget was not found at $fdtget"
fi

ps_bus='/axi/i2c@ff020000/i2c-mux@75/i2c@0'
pl_bus='/axi/i2c@ff020000/i2c-mux@75/i2c@1'
ps_rails=(
  40:VCCPSINTFP 41:VCCPSINTLP 42:VCCPSAUX 43:VCCPSPLL 44:MGTRAVCC
  45:MGTRAVTT 46:VCCO_PSDDR_504 47:VCCOPS 4a:VCCOPS3 4b:VCCPSDDRPLL
)
pl_rails=(
  40:VCCINT 41:VCCBRAM 42:VCCAUX 43:VCC1V2
  44:VCC3V3 45:VADJ_FMC 46:MGTAVCC 47:MGTAVTT
)
audit_dtb_bus() {
  local bus="$1"
  shift
  local entry address expected actual
  for entry in "$@"; do
    address="${entry%%:*}"
    expected="${entry#*:}"
    actual="$("$fdtget" "$system_dtb" "$bus/power-monitor@$address" label)"
    if [[ "$actual" != "$expected" ]]; then
      pl_finish_fail "system.dtb rail $bus/$address is '$actual', expected '$expected'"
    fi
    printf '%s,%s,%s\n' "$bus" "$address" "$actual"
  done
}
{
  echo "bus,address,label"
  audit_dtb_bus "$ps_bus" "${ps_rails[@]}"
  audit_dtb_bus "$pl_bus" "${pl_rails[@]}"
} >"$RUN_DIR/power-dtb-audit.csv"

{
  echo "PetaLinux ZCU102 power integration passed"
  cat "$RUN_DIR/power-kernel-options.txt"
  echo "Verified 10 PS and 8 PL INA226 nodes in deployed system.dtb"
} | tee -a "$BUILD_LOG"
pl_write_manifest "pass"
