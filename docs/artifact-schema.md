# Artifact Schema

Every test run writes a directory under `artifacts/<run-id>/`.

Required files:

- `manifest.json`: machine-readable run metadata and pass/fail status.
- `environment.txt`: host, Docker, PetaLinux, and toolchain details.

Runtime test runs also include, when available:

- `serial.log`: VP or board serial console log.
- `dmesg.log`: kernel log captured after module load and workload execution.
- `module-load.log`: output from loading `opendla.ko`.
- `module-vermagic.txt`: target-side `modinfo -F vermagic` output.
- `dev-dri.txt`: target-side `/dev/dri` render-node listing.
- `runtime.stdout.log`: runtime stdout.
- `runtime.stderr.log`: runtime stderr.
- `runtime-server.log`: target-side `nvdla_runtime -s` server log.
- `runtime-client.log`: target-side flatbuffer client log.
- `runtime-compare.log`: target-side golden comparison summary.
- `runtime-output-compare.json`: host-side exact comparison summary.
- `runtime-output/o_000000.dimg`: output tensor returned by the runtime server.
- `lenet-analysis.json`: LeNet/MNIST correctness classification, repeat
  results, layer/HWL summary, config proof, and bad-pattern summary.
- `sdp-small-diagnostic.json`: classification for the currently non-blocking
  `sdp_regression_small` diagnostic result, including the known completion
  timeout and zero-output/golden-mismatch cases.

Build-phase runs include one phase log, such as `toolchain.log`, `kernel.log`, `rootfs.log`, or `kmod.log`.

Recommended files:

- `output.bin`: raw output tensor from the accelerator.
- `golden.bin`: golden tensor used for comparison.
- `tensor-diff.json`: per-output comparison summary.

`manifest.json` must include:

```json
{
  "schema_version": 1,
  "run_id": "20260623T000000Z-vp-reference",
  "lane": "vp-reference",
  "status": "pass",
  "sources": {
    "nvdla_sw": "79538ba1b52b040a4a4645f630e457fa01839e90"
  },
  "kernel": {
    "version": "4.13.3",
    "image_sha256": "..."
  },
  "driver": {
    "module_sha256": "...",
    "vermagic": "..."
  },
  "workloads": []
}
```

Modern VP build manifests additionally include `phase`, `toolchain`,
`sources.nvdla_patch_series_sha256`, `artifacts`, and `logs`. The
`driver.kmd_config` field records the KMD register-header build selection
(`initial` or `small`). A failed `vp-kmod` compile is valid evidence when the
manifest status is `fail` and `kmod.log` contains the actionable Linux 6.6
compiler diagnostics.

Modern VP smoke manifests use `lane: "vp-modern"` and include a `modern` object
with discovered artifact paths, kernel/rootfs/module/smoke hashes, Docker
command, module-load status, render-node status, smoke status, bad kernel log
patterns, and repeat count. A missing kernel/rootfs/module is recorded as
`blocked` rather than `fail`.

Modern VP runtime manifests set `modern.mode` to `runtime` and add runtime
binary/library/client hashes, the workload loadable and golden hashes, output
hashes, `modern.probe_config`, `runtime.server_log`, `runtime.client_log`,
`runtime.compare_log`, payload timeout settings, and `workloads[]` comparison
records. Generated workloads include target-compatible metadata, and runtime
mode rejects a run when the probed KMD config does not match the workload
target. Runtime mode passes only when the VP boots, the KMD loads, a render node
exists, the runtime server is ready, the flatbuffer client exits cleanly, the
workload target matches the probed config, the output `.dimg` exactly matches
the pinned golden, and serial plus `dmesg` contain no bad kernel or VP patterns.

LeNet gate manifests use `mode: "lenet_small_control"` for the primary
`nv_small` correctness gate. They include `repeat_count`, `pass_count`,
`repeat_results[]`, `probe_config`, `render_node`, `layer_summary`,
`first_failure`, and an `analysis` pointer to `lenet-analysis.json`. The gate
passes only when every repeat produces the expected digit-7 vector, the KMD
probes `nvidia,nv_small`, the loadable is tagged `nv_small`, layer/HWL progress
reaches the expected count, and bad-pattern logs are empty.

`vp-small-config-audit` manifests use `mode: "config_audit"` and record the
source-built VP binary, CMOD, DTB, and KMD hashes, the VP CMake
`NVDLA_HW_PROJECT`, the Docker `ldd` CMOD resolution, and the latest
`nvidia,nv_small` probe artifact.

PetaLinux manifests use lanes such as `petalinux-project`, `petalinux-dts`,
`petalinux-kmod`, `petalinux-runtime`, `petalinux-image`,
`petalinux-board-tools`, `petalinux-rootfs-audit`, `petalinux-package`, and
`petalinux-sd-bundle`. They include the Ubuntu WSL
host facts, PetaLinux install path, default or explicit project path, settings
log, XSA hash, patch-series hash, kernel version when discoverable, logs, and
pass/fail/block reason. The DTS phase records the generated `nvdla-user.dtsi`
hash and audit JSON; the KMD phase records `NVDLA_HW_CONFIG`, recipe files,
`opendla.ko` path/hash, and module `vermagic`; image/package phases record
produced boot artifact hashes.

The runtime phase records the pinned NVDLA source revision, patch queue, recipe,
RPM, executable, and shared-library hashes. The rootfs audit stores
`rootfs-audit.json` plus extracted copies of the three NVDLA ELF files. Its
manifest records rootfs archive hashes, installed paths, AArch64 machine type,
`NEEDED` libraries, RPATH results, dependency closure, module `vermagic`, and
binary/library/module hashes. A missing component or dependency, wrong
architecture, RPATH/RUNPATH, or embedded host build path makes this lane fail.
The default project path is `$HOME/build/nvdla-peta/petalinux/zcu102-nvdla` so
generated builds stay on WSL ext4 unless `PETALINUX_PROJECT` is overridden.

Board-tool manifests record the recipe, RPM, smoke binary, collector, and patch
hashes. Rootfs audit manifests additionally require the executable collector
and explicit `ttyPS0` serial-autologin override. For this ZCU102 direct-link
image, the audit also requires the project-specific `eth0` profile containing
MAC `02:00:00:50:10:02` and address `192.168.50.2/24`. That requirement
describes this bring-up image, not a generic NVDLA runtime or KMD dependency.
SD-bundle manifests record the three source and copied boot-file hashes plus a
deterministic archive hash.

Imported board archives use lanes `petalinux-board-preflight`,
`petalinux-board-probe`, `petalinux-board-smoke`,
`petalinux-board-runtime-sdp`, or `petalinux-board-lenet`. They record target
status, archive hash, member list, bad kernel patterns, and the optional full
serial log. Preflight requires the NVDLA DT resource and interrupt properties.
Probe additionally requires a bound platform driver and `/dev/dri/renderD*`; a
successful module insertion alone is not a pass. The importer rejects absolute
paths, traversal paths, and links.

Runtime imports also write `workload-analysis.json`. SDP analysis records
server/client exit status, protocol completion, task initiation, IRQ delta, SDP
completion, output hash, comparison, and whether tensor correctness is
`pass`, `fail`, or `inconclusive`. LeNet analysis records every repeat's
runtime status, IRQ delta, ordered operation completions, output/hash, first
failure, next expected engine, total passes, and distinct output hashes. The
host recreates these classifications from raw evidence rather than accepting
the target's summary without checking it.

Large generated artifacts should remain in `artifacts/` and should not be committed.
