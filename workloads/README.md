# Workloads

Workload directories contain small, deterministic runtime tests. They are source-controlled as manifests and generators; generated loadables, tensors, and logs belong in `artifacts/`.

Each workload must define:

- `target_config`: expected NVDLA hardware configuration.
- `inputs`: deterministic inputs and hashes.
- `golden`: expected CPU result and tolerance.
- `runtime`: command or script used by the VP/board harness.
- `repeat_count`: stability-loop count.

Generated binary artifacts should be recreated by scripts and must not be committed.

