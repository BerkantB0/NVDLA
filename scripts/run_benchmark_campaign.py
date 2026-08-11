#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_board_benchmark.sh"


def choose(prompt: str, options: list[str]) -> str:
    print(f"\n{prompt}")
    for index, option in enumerate(options, 1):
        print(f"  {index}. {option}")
    while True:
        answer = input("> ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        print("Choose one of the listed numbers.")


def positive_int(prompt: str, default: int) -> int:
    while True:
        answer = input(f"{prompt} [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        print("Enter a positive integer.")


def benchmark_options(
    kind: str, model: str, phase: str, threads: int, precision: str
) -> list[str]:
    lenet = model == "lenet"
    if phase == "latency":
        options = [
            "--regime", "all",
            "--cold-starts", "10" if lenet else "5",
            "--warm-starts", "30" if lenet else "10",
        ]
        if kind == "nvdla":
            options += ["--warmups", "20" if lenet else "5"]
        options += ["--steady-samples", "200" if lenet else "30"]
    else:
        options = ["--regime", "steady"]
        if kind == "nvdla":
            options += ["--warmups", "1"]
        options += [
            "--steady-samples", "200" if lenet else "30",
            "--power",
            "--power-idle-seconds", "10",
            "--power-interval-ms", "50",
            "--power-sampler-cpu", "3",
        ]
    options += ["--settle-seconds", "30"]
    if kind == "nvdla":
        options += ["--benchmark-cpu", "2"]
    else:
        options = ["--precision", precision, "--threads", str(threads)] + options
    return options


def output_directory(
    kind: str, model: str, phase: str, threads: int, precision: str
) -> Path:
    if kind == "nvdla":
        return ROOT / "artifacts" / "final" / "nvdla" / model / phase
    if threads == 4:
        family = "cpu-int8" if precision == "int8" else "cpu"
        return ROOT / "artifacts" / "final" / family / model / phase
    return (
        ROOT
        / "artifacts"
        / "final"
        / "cpu-scaling"
        / f"{threads}t"
        / precision
        / model
        / phase
    )


def notify() -> None:
    powershell = shutil.which("powershell.exe")
    if powershell:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                "[System.Media.SystemSounds]::Asterisk.Play(); "
                "Start-Sleep -Milliseconds 500",
            ],
            check=False,
        )
        if result.returncode == 0:
            return
    print("\a", end="", flush=True)


def main() -> int:
    kind = choose("Implementation", ["cpu", "nvdla"])
    models = choose("Model", ["lenet", "resnet50", "both"])
    phases = choose("Measurements", ["latency", "power", "both"])
    threads = 4
    precision = "fp32"
    if kind == "cpu":
        threads = int(choose("CPU threads/cores", ["1", "2", "4"]))
        precision = choose("Precision", ["fp32", "int8"])
    sessions = positive_int("Fresh-boot sessions per test", 5)
    delay = positive_int("Seconds to wait after restart confirmation", 10)

    selected_models = ["lenet", "resnet50"] if models == "both" else [models]
    selected_phases = ["latency", "power"] if phases == "both" else [phases]
    jobs = [
        (model, phase, session)
        for model in selected_models
        for phase in selected_phases
        for session in range(1, sessions + 1)
    ]
    print(f"\nQueued {len(jobs)} fresh-boot benchmark sessions.")
    input("Ensure the board is freshly booted, then press Enter to start. ")

    for number, (model, phase, session) in enumerate(jobs, 1):
        output = output_directory(kind, model, phase, threads, precision)
        command = [
            str(RUNNER), kind, model,
            *benchmark_options(kind, model, phase, threads, precision),
            "--output", str(output),
        ]
        print(f"\n[{number}/{len(jobs)}] {kind} {model} {phase}, session {session}")
        result = subprocess.run(command, cwd=ROOT, check=False)
        notify()
        if result.returncode != 0:
            print(f"Benchmark failed with status {result.returncode}; campaign stopped.")
            return result.returncode
        if number < len(jobs):
            input("Power-cycle the board; press Enter when it has been restarted. ")
            print(f"Waiting {delay} seconds before checking the board...")
            time.sleep(delay)

    print("\nCampaign complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
