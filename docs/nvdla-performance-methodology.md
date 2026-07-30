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

The paper and earlier feasibility notes describe a 100 MHz implementation, but
the checked-in XSA used for this board declares both `csb_clk` and `m_axi_clk`
as 149,985,016 Hz. Linux reports the corresponding `pl0_ref` as 149,985,000 Hz.
The benchmark therefore uses the XSA value carried in the hashed payload, with
a pinned 1,000 Hz tolerance for Linux clock-framework representation.

## Timing Boundaries

All clocks use integer nanoseconds from `CLOCK_MONOTONIC_RAW`.

| Regime | Process and cache state | Reported latency |
|---|---|---|
| Cold | New process after `sync` and a page-cache drop | Parent launch to child exit |
| Warm | New process after model, input, runtime, and library cache priming | Parent launch to child exit |
| Steady | One loaded runtime context and one bound buffer set | Runtime execution latency |

Runtime execution latency is the blocking `IRuntime::submit()` interval. It
includes UMD submission, the DRM ioctl, KMD
scheduling, hardware execution, interrupt completion, and any loadable
emulator work. It excludes model file reading, loadable deserialization, image
decode, buffer allocation, output file writing, and process startup.

The pinned UMD patch queue replaces the upstream emulator worker's 500 ms
task-queue polling interval with a condition variable. The change is confined
to the emulator implementation, preserves blocking-submit behavior, and also
synchronizes the previously unprotected task queue. Steady measurements made
with an earlier runtime can contain polling delays in 500 ms increments and
must not be combined with or substituted for final campaign results.

Performance profile schema 2 records the interval as
`runtime_execution_ns`; schema 1 pilot archives are intentionally incompatible
and must not be mixed with the final campaign.

The runtime also records context creation, loadable file read, runtime load,
emulator initialization, input and output setup, output extraction, DIMG
generation, buffer cleanup, unload, emulator shutdown, runtime destruction,
and test and process totals.

The importer retains those detailed phases and also reports these aggregates:

| Aggregate | Components |
|---|---|
| Runtime initialization | Runtime context creation and emulator initialization |
| Model loading | Loadable file read plus deserialization, GEM allocation, and population in `runtime->load()` |
| Buffer preparation | Input decode/conversion/binding plus output allocation/binding |
| Runtime execution | Blocking `IRuntime::submit()` |
| Result handling | Output extraction plus final DIMG generation |
| Teardown | Buffer cleanup, emulator stop, unload, and runtime destruction |

Cold and warm aggregate percentages use external launch-to-exit latency as the
denominator. Any process startup or other time outside the instrumented UMD
phases is reported as unprofiled time rather than redistributed. In the steady
regime, model and buffer setup occur once, so they are reported as one-time
context costs; only runtime execution and output extraction are expressed per
measured inference.

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
- pins the runtime and its worker thread to A53 CPU 2 by default;
- records and restores console log level and CPU governors;
- selects the `performance` governor when supported;
- redirects runtime output away from UART;
- waits for the configured settling period;
- verifies the active NVDLA clock against the frequency pinned from the XSA;
- requires a positive NVDLA IRQ delta and exact tensor output;
- rejects kernel error patterns;
- records process user/system CPU time, page faults, and voluntary/involuntary
  context switches after each process exits;
- archives raw profiles, outputs, environment, hashes, and logs.

### Power measurement

The ZCU102 INA226 monitors are sampled by the native
`nvdla-power-sampler` utility. It opens every labelled hwmon power file once,
uses `CLOCK_MONOTONIC_RAW` timestamps, groups rails as PS or PL from the
device-tree topology, and buffers CSV output away from UART. The default
50 ms interval is deliberately moderate to limit I2C and CPU observer
effects. The sampler runs on A53 CPU 3 while the runtime remains on CPU 2.

Power is sampled concurrently with the correctness-qualified steady run:

- idle sampling observes the driver-loaded board immediately before the steady
  phase, without a runtime process;
- active sampling starts immediately before the existing `steady-1` runtime
  process and ends after it exits;
- `--steady-samples` controls both the latency sample count and measured active
  batch size;
- warm-ups and measured steady iterations are both counted because both
  consume energy inside the sampled window;
- no second runtime process or separate power workload is launched.

The resulting energy per inference is therefore **steady-batch end-to-end
energy**.
It includes model setup and teardown amortized over the batch, PS runtime/KMD
activity, memory-system activity visible on monitored rails, and PL execution.
It is not presented as NVDLA-core-only energy.

The importer reports each rail plus these first-class domains:

- `PS`: processing system and software-stack delivery cost;
- `PL`: FPGA fabric power, including NVDLA and other instantiated PL logic;
- `MONITORED`: all exposed rails, explicitly not total 12 V board power.

Incremental power and energy are active minus idle and remain signed. Negative
values are retained as measurement evidence rather than silently clamped.

Set `--benchmark-cpu none` only for an explicitly labelled scheduling control.
Process scheduler totals are also normalized by all executed inferences,
including warm-ups, because the process-level `wait4()` evidence includes
their cost. CPU migration counts are reported as unavailable unless a later
kernel facility provides them directly; they are not inferred.

Correctness and diagnostic runners set `firmware_log=1` because their
operation-sequence classifiers depend on progress messages.

## Build And Audit

Run the PetaLinux build in Ubuntu 22.04 WSL:

```sh
make patch-check
make abi-check
make petalinux-power
NVDLA_KMD_CONFIG=small make petalinux-kmod
make petalinux-runtime
make petalinux-board-tools
make petalinux-image
make petalinux-rootfs-audit
make vp-resnet50-small-golden-promote
make petalinux-board-payload
```

`make petalinux-power` installs the board-local ZCU102 PCA9544/INA226 device
tree and enables the Linux mux and sensor drivers. Its build gate verifies the
resolved kernel configuration and all 18 named monitor nodes in the deployed
`system.dtb`. The primary accelerator-oriented subtotal uses `VCCINT`,
`VCCBRAM`, and `VCCAUX`; PS and other board rails remain separate so monitored
rails are not mistaken for total 12 V board-input power.

Copy the generated `nvdla-tests` directory to the SD FAT partition. Use a fresh
boot for every independent session. Do not run a correctness workload before
a performance session because that changes cache and accelerator state.
Before starting a session, verify that the configured direct-link NTP source
has synchronized the board:

```sh
timedatectl show -p NTPSynchronized --value
date -u '+%Y-%m-%dT%H:%M:%SZ'
```

Wall-clock synchronization provides meaningful artifact timestamps;
`CLOCK_MONOTONIC_RAW` remains the measurement clock.

## Pilot

Use reduced counts first. `nvdla-board-benchmark --help` lists every
experiment-defining option and its default; environment variables are not used
for these controls.

```sh
nvdla-board-benchmark lenet /run/media/ROOT-mmcblk0p1/nvdla-tests \
  --cold-starts 1 \
  --warm-starts 3 \
  --warmups 2 \
  --steady-samples 5 \
  --settle-seconds 10

nvdla-board-benchmark resnet50 /run/media/ROOT-mmcblk0p1/nvdla-tests \
  --cold-starts 1 \
  --warm-starts 2 \
  --warmups 1 \
  --steady-samples 3 \
  --settle-seconds 10
```

The pilot must produce `exact-performance-pass`, no kernel bad patterns, an
XSA-matched clock, and an archive in `/tmp`.

The importer reports two same-session sanity comparisons automatically:

- cold versus cached warm process medians;
- the first measured steady inference versus the median of later measured
  inferences.

For an observer-effect check, run an otherwise identical short pilot once
without `--firmware-log` and once with it. Report both, but use quiet runs only
in the primary campaign. Analyze the two logging modes separately because the
importer correctly rejects mixed `firmware_log` provenance.
Compare an instrumented and legacy single-execution pilot separately as well;
a median difference above 2 percent requires investigation before the final
campaign.

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

Power measurement can be added to a latency run whose selected regime includes
the steady phase:

```sh
nvdla-board-benchmark resnet50 /run/media/ROOT-mmcblk0p1/nvdla-tests \
  --regime steady \
  --power
```

For a short power pilot:

```sh
nvdla-board-benchmark resnet50 /run/media/ROOT-mmcblk0p1/nvdla-tests \
  --regime steady \
  --steady-samples 3 \
  --warmups 1 \
  --power \
  --power-idle-seconds 5
```

The sampler can also be checked independently:

```sh
nvdla-power-sampler --list
nvdla-power-sampler \
  --output /tmp/power-idle.csv \
  --duration-ms 10000 \
  --interval-ms 50 \
  --cpu 3
```

With `--power`, the runner records the path-to-label map plus raw idle and
active samples while it performs the steady latency measurement. The selected
regime must therefore be `steady` or `all`. Missing or unlabelled PS/PL rails
fail the run so incomplete energy evidence cannot be accepted silently.
Latency-only runs keep power sampling disabled and are unaffected.

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
- `phase-breakdown.svg`;
- `throughput-comparison.svg`;
- `session-variability.svg`.

The latency figure retains every observation and overlays the median,
interquartile range, and p5-p95 interval. Each timing regime uses an independent
linear scale so small within-regime differences remain visible; printed medians
provide the valid cross-regime comparison.

The phase figure uses one shared absolute-time axis. Bar lengths therefore
compare total latency directly, while stacked colours show how each total is
composed. The throughput figure keeps the three measured timing definitions
visually separate from the analytical stage-bottleneck upper bound.

With two or more independent fresh-boot sessions, the reproducibility figure
shows each session median as a percentage difference from the cross-session
median on one shared scale. With only one session, between-boot variability is
not statistically defined, so the figure records that limitation instead of
drawing a zero-spread result.

It reports count, mean, median, standard deviation, coefficient of variation,
minimum, maximum, IQR, p5, p95, aggregate and detailed phases, scheduler
context, workload complexity, and per-session results. A deterministic 95
percent percentile bootstrap confidence interval is calculated over
independent session medians.

Four throughput quantities are kept distinct:

- cold deployment throughput from mean cold launch-to-exit latency;
- warm end-to-end throughput from mean warm launch-to-exit latency;
- steady runtime execution throughput from mean runtime execution latency;
- a theoretical stage-bottleneck upper bound,
  `1 / max(warm input preparation, steady runtime execution)`.

The last quantity is not measured pipelined throughput. The current runtime is
blocking and the steady test reuses one prepared input, so it does not prove
that input preparation and accelerator execution can overlap.

Software overhead is calculated independently for each fresh-boot session as:

```text
overhead = median(warm end-to-end) - median(steady runtime execution)
overhead percentage = overhead / median(warm end-to-end)
```

Only then are the session overheads summarized. This avoids subtracting pooled
statistics that do not preserve session pairing.

The report also multiplies runtime execution latency by the verified NVDLA
clock. The result is labelled a host-observed NVDLA-clock-equivalent interval,
not accelerator cycles, because it includes UMD, ioctl, scheduler, interrupt,
and emulator overhead.

No outlier is removed. Tukey 1.5 IQR fences flag observations while retaining
them in every statistic. Mixed model, input, loadable, module, runtime, runtime
library, kernel, clock, or payload provenance is rejected.

## Interpretation Limits

Runtime execution latency is not a per-layer hardware cycle count. Per-HWL
instrumentation is deferred because kernel tracepoints or NVDLA statistic
descriptors could perturb the baseline or alter the loadable. Each payload
instead records loadable size, NCHW input dimensions, output size, HWL count,
and operation count per engine. ResNet-50 counts are deduplicated by operation
index from the verified source-built `nv_small` VP oracle.

Comparisons with prior work must state clock frequency, hardware
configuration, memory path, software stack, precision, batch size,
preprocessing, and timing boundary. Different boundaries are contextual
rather than directly comparable.
