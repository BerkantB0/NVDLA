# NVDLA Performance Measurement Methodology

## Purpose

This framework measures the `nv_small` FPGA implementation with production
logging disabled while retaining exact output checks. It separates deployment
cost from accelerator execution so results can be interpreted without
conflating SD-card I/O, model loading, and inference.

LeNet/MNIST and ResNet-50 use the same loadables, inputs, and exact output
oracles validated in the source-built `nv_small` VP. Performance evidence is
accepted only when module, runtime, kernel, payload, model, input, loadable,
and NVDLA clock provenance match.

## Timing Boundaries

All clocks use integer nanoseconds from `CLOCK_MONOTONIC_RAW`.

| Regime | Process and cache state | Reported latency |
|---|---|---|
| Cold | New process after `sync` and a page-cache drop | Parent launch to child exit |
| Warm | New process after model, input, runtime, and library cache priming | Parent launch to child exit |
| Steady | One loaded runtime context and one bound buffer set | Blocking `IRuntime::submit()` |

The steady submit interval includes UMD submission, the DRM ioctl, KMD
scheduling, hardware execution, interrupt completion, and any loadable
emulator work. It excludes model file reading, loadable deserialization, image
decode, buffer allocation, output file writing, and process startup.

The runtime also records context creation, loadable read and load, emulator
initialization, input and output setup, output extraction, DIMG generation,
buffer cleanup, unload, emulator shutdown, runtime destruction, and test and
process totals.

Metrics are buffered in memory and written after measured work and teardown.
Warm-up samples are identified and excluded from statistics. Every repeated
output is compared in memory with the first output.

Each profile calibrates the minimum cost of 1,000 back-to-back timing pairs.
The importer rejects evidence when that cost exceeds 1% of any measured submit
latency and reports the maximum observed fraction.

## Production Controls

The production KMD parameter `firmware_log` defaults to `0`. It suppresses only
`dla_debug()` and `dla_info()`. Warnings, errors, probe diagnostics, DMA,
scheduling, register access, IRQ behavior, and the public ABI are unchanged.

The board benchmark:

- sets `firmware_log=0` and console log level 3;
- records and restores console log level and CPU governors;
- selects the `performance` governor when supported;
- redirects runtime output away from UART;
- waits for the configured settling period;
- verifies a recorded 100 MHz NVDLA clock;
- requires a positive NVDLA IRQ delta and exact tensor output;
- rejects kernel error patterns;
- archives raw profiles, outputs, environment, hashes, and logs.

Correctness and diagnostic runners set `firmware_log=1` because their
operation-sequence classifiers depend on progress messages.

## Build And Audit

Run the PetaLinux build in Ubuntu 22.04 WSL:

```sh
make patch-check
make abi-check
NVDLA_KMD_CONFIG=small make petalinux-kmod
make petalinux-runtime
make petalinux-board-tools
make petalinux-image
make petalinux-rootfs-audit
make vp-resnet50-small-golden-promote
make petalinux-board-payload
```

Copy the generated `nvdla-tests` directory to the SD FAT partition. Use a fresh
boot for every independent session. Do not run a correctness workload before
a performance session because that changes cache and accelerator state.

## Pilot

Use reduced counts first:

```sh
COLD_STARTS=1 WARM_STARTS=3 WARMUPS=2 STEADY_SAMPLES=5 SETTLE_SECONDS=10 \
  nvdla-board-benchmark lenet /run/media/ROOT-mmcblk0p1/nvdla-tests

COLD_STARTS=1 WARM_STARTS=2 WARMUPS=1 STEADY_SAMPLES=3 SETTLE_SECONDS=10 \
  nvdla-board-benchmark resnet50 /run/media/ROOT-mmcblk0p1/nvdla-tests
```

The pilot must produce `exact-performance-pass`, no kernel bad patterns, a
verified 100 MHz clock, and an archive in `/tmp`.

For an observer-effect check, run an otherwise identical short pilot once with
`FIRMWARE_LOG=0` and once with `FIRMWARE_LOG=1`. Report both, but use quiet
runs only in the primary campaign. Compare an instrumented and legacy
single-submit pilot as well; a median difference above 2 percent requires
investigation before the final campaign.

## Final Campaign

Run five fresh-boot sessions per model and keep model archive sets separate:

```sh
nvdla-board-benchmark lenet /run/media/ROOT-mmcblk0p1/nvdla-tests
nvdla-board-benchmark resnet50 /run/media/ROOT-mmcblk0p1/nvdla-tests
```

| Model | Cold | Warm | Warm-ups | Steady samples |
|---|---:|---:|---:|---:|
| LeNet | 10 | 30 | 20 | 200 |
| ResNet-50 | 5 | 10 | 5 | 30 |

Power measurement is a separate optional run:

```sh
POWER_SAMPLE=1 REGIME=steady \
  nvdla-board-benchmark resnet50 /run/media/ROOT-mmcblk0p1/nvdla-tests
```

When explicitly labelled hwmon power rails exist, the runner records the
path-to-label map plus raw idle and active samples. Unlabelled or missing
sensors are reported as unavailable and do not fail latency measurement.

## Host Analysis

Transfer each archive without unpacking it, then analyze one model:

```sh
ARCHIVES="session1.tar.gz session2.tar.gz session3.tar.gz session4.tar.gz session5.tar.gz" \
PERFORMANCE_OUT=artifacts/performance-resnet50 \
  make performance-report
```

The importer creates:

- `performance-raw.csv`;
- `performance-summary.json`;
- `performance-summary.csv`;
- `performance-report.md`;
- `latency-distribution.svg`;
- `phase-breakdown.svg`.

It reports count, mean, median, standard deviation, coefficient of variation,
minimum, maximum, IQR, p5, p95, throughput, phase percentages, and per-session
results. A deterministic 95 percent percentile bootstrap confidence interval
is calculated over independent session medians.

No outlier is removed. Tukey 1.5 IQR fences flag observations while retaining
them in every statistic. Mixed model, input, loadable, module, runtime, runtime
library, kernel, clock, or payload provenance is rejected.

## Interpretation Limits

Host-observed submit latency is not a per-layer hardware cycle count. Per-HWL
instrumentation is deferred because kernel tracepoints or NVDLA statistic
descriptors could perturb the baseline or alter the loadable. Existing verbose
correctness artifacts provide operation counts and engine composition.

Comparisons with prior work must state clock frequency, hardware
configuration, memory path, software stack, precision, batch size,
preprocessing, and timing boundary. Different boundaries are contextual
rather than directly comparable.
