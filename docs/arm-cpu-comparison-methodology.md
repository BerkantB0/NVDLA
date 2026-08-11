# ARM CPU Comparison Methodology

## Purpose

This lane measures the ZCU102's four Cortex-A53 cores with the standard ONNX
Runtime CPU execution provider. It provides a reproducible software baseline
for the same LeNet and ResNet-50 source weights used by the NVDLA study. It is
not intended to claim that independently quantized CPU INT8 and NVDLA INT8
graphs perform identical arithmetic.

## Software And Models

- ONNX Runtime is pinned to version 1.18.1 and cross-built with the PetaLinux
  2024.1 SDK for ARM64.
- `onnx_test_runner` provides target-side correctness validation.
- `onnxruntime_perf_test` provides model loading, one built-in warm-up, raw
  per-inference latency, CPU usage, and peak working-set evidence.
- Caffe LeNet and ResNet-50 are converted once to pinned FP32 ONNX graphs.
- Static QDQ S8S8 INT8 variants are generated with pinned ORT quantization
  tooling and deterministic calibration inputs.
- Every model, input protobuf, and expected-output protobuf is hash recorded.

The FP32 lane is the principal same-weights CPU comparison. The INT8 lane is a
useful optimized CPU implementation comparison, but its calibration and
quantization policy differ from the NVDLA compiler and must be labelled as
such in results.

## Build And Package

Run from Ubuntu-22.04 WSL with generated work on WSL ext4:

```sh
export SOURCES_DIR=$HOME/src/nvdla-peta-sources
export PETALINUX_PROJECT=$HOME/build/nvdla-peta/petalinux/zcu102-nvdla
export CPU_RUNTIME_WORK_DIR=$HOME/build/nvdla-peta/cpu-onnxruntime

make cpu-model-workloads
make petalinux-cpu-sdk
JOBS=2 make cpu-onnxruntime
make petalinux-cpu-runtime
make petalinux-board-tools
make petalinux-image
make petalinux-rootfs-audit
make petalinux-board-payload
make petalinux-sd-bundle
```

The rootfs audit requires AArch64 binaries, complete dynamic dependencies,
safe runtime search paths, and no host build paths. The workload payload audit
checks all ONNX graphs and serialized test tensors before producing the FAT
partition handoff.

## Measurement Definitions

The target command is:

```sh
nvdla-board-cpu-benchmark MODEL PAYLOAD_ROOT \
  --precision PRECISION --threads THREADS [OPTIONS]
```

`MODEL` is `lenet` or `resnet50`; `PRECISION` is `fp32` or `int8`.

- **Cold deployment:** a new process after `sync` and dropping Linux page
  caches. Elapsed time covers process launch, file reads, session creation,
  ORT's mandatory warm-up, one measured inference, and process shutdown.
- **Warm deployment:** the same new-process boundary with primed file caches.
- **Steady inference:** raw per-inference latency from one loaded ORT session,
  after its built-in warm-up.
- **Four-thread primary:** `--threads 4`, representing the board's practical
  CPU implementation.
- **Single-thread secondary:** `--threads 1`, separating parallel scaling from
  model/runtime effects.

The wrapper does not change CPU frequency policy. It requires the inherited
`userspace` governor to hold all measured cores at the same nominal 1.2 GHz
frequency, records the exact reported value before and after measurement, and
rejects a session if that value changes. This keeps the board setup explicit
without introducing a governor transition into the benchmark. It also
suppresses console noise and records scheduling/resource evidence. Standard
`onnx_test_runner` checks the pinned output with absolute and relative
tolerances of `1e-5` before and after every measurement session.

`systemd-timesyncd` starts during early boot and uses the pinned direct-link
host source when Ethernet becomes usable. The wrapper records synchronization
status but does not wait for it or require it: latency uses
`CLOCK_MONOTONIC_RAW`, while Linux boot IDs establish independent sessions.

## Power

`--power` adds concurrent INA226 sampling around the steady process. PS, PL,
and monitored-total rails remain separate in the evidence. Idle and active
energy are integrated against `CLOCK_MONOTONIC_RAW`; incremental energy removes
the measured idle baseline.

On a four-thread CPU run the sampler necessarily consumes some A53 time. Final
latency should therefore come from uninstrumented sessions, with powered
sessions reported separately and an observer-effect comparison retained. A
powered run still performs the normal steady workload; there is no synthetic
power-only regime.

## Pilot Commands

After copying the new `nvdla-tests` directory to the FAT partition:

```sh
nvdla-board-cpu-benchmark lenet /run/media/ROOT-mmcblk0p1/nvdla-tests \
  --precision fp32 --threads 4 --cold-starts 1 --warm-starts 3 \
  --steady-samples 20 --settle-seconds 10

nvdla-board-cpu-benchmark resnet50 /run/media/ROOT-mmcblk0p1/nvdla-tests \
  --precision int8 --threads 4 --regime steady --steady-samples 30 \
  --settle-seconds 30 --power
```

Use a fresh boot for every independent final session. Run separate campaigns
for every model, precision, thread count, and power setting. The importer will
reject mixed provenance and duplicate Linux boot IDs; NTP status remains
reported metadata rather than an acceptance condition:

```sh
ARCHIVES="session1.tar.gz session2.tar.gz session3.tar.gz session4.tar.gz session5.tar.gz" \
  make cpu-performance-report
```

The output contains raw CSV, a machine-readable statistical summary, a compact
CSV table, and a Markdown report. The importer rejects sessions with different
governors or fixed frequencies. Cold/warm and steady throughput are reported
under their distinct timing boundaries and must not be conflated.

## SSH Collection

The test-only image uses the deliberately public credential `root` / `nvdla`
on the isolated `192.168.50.0/24` direct Ethernet link. Root login and password
authentication are enabled, while empty passwords remain disabled. This policy
must not be carried into a deployed image.

Install the single host dependency in Ubuntu-22.04 WSL:

```sh
sudo apt install sshpass
```

After a fresh board boot, run exactly one model:

```sh
scripts/run_board_benchmark.sh cpu lenet
# Power-cycle or reboot the board before the next command.
scripts/run_board_benchmark.sh cpu resnet50
```

The shared host runner waits for SSH, records the Linux boot ID, rejects reuse
of the previous successful CPU benchmark boot, and downloads the validated
archive to `artifacts/cpu-board-ssh/`. With no additional arguments, the target
uses its standard FP32 four-thread campaign defaults. Target options can be
passed directly, including a correctness-qualified power run:

```sh
scripts/run_board_benchmark.sh cpu resnet50 \
  --precision fp32 --threads 4 --regime steady \
  --steady-samples 30 --power
```

Host controls use dedicated names such as `--ssh-host`, `--payload`, and
`--output`; all other options are forwarded to `nvdla-board-cpu-benchmark`.
The runner does not reboot the board.

For sequential fresh-boot campaigns, use the interactive wrapper from the
Ubuntu-22.04 WSL repository root:

```sh
python3 scripts/run_benchmark_campaign.py
```

It selects CPU or NVDLA presets, model, latency and/or power, and the CPU thread
count and precision where applicable. After each successful session it sounds
a notification, waits for manual power-cycle confirmation, pauses for the
selected delay, and then lets the existing SSH runner wait for the board.

## Final Campaign Matrix

For each model, first collect five uninstrumented fresh-boot sessions for FP32
with four threads. Then collect the matching INT8 campaign. Repeat the steady
regime with one thread to report scaling. Finally collect power-enabled steady
sessions for the configurations used in the energy comparison.

Retain all samples. Report mean, median, standard deviation, coefficient of
variation, IQR, p5, p95, throughput, and the deterministic bootstrap 95%
confidence interval across independent session medians. Any failed correctness
check, timeout, kernel error, or provenance mismatch invalidates that session.
Wall-clock synchronization is reported metadata, not an acceptance condition.
