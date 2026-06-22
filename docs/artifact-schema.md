# Artifact Schema

Every test run writes a directory under `artifacts/<run-id>/`.

Required files:

- `manifest.json`: machine-readable run metadata and pass/fail status.
- `serial.log`: VP or board serial console log, when available.
- `dmesg.log`: kernel log captured after module load and workload execution.
- `runtime.stdout.log`: runtime stdout.
- `runtime.stderr.log`: runtime stderr.

Recommended files:

- `output.bin`: raw output tensor from the accelerator.
- `golden.bin`: golden tensor used for comparison.
- `tensor-diff.json`: per-output comparison summary.
- `environment.txt`: host, Docker, PetaLinux, and toolchain details.

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

Large generated artifacts should remain in `artifacts/` and should not be committed.

