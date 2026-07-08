from __future__ import annotations

import re
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from .common import read_json, run_command, sha256_file, write_json
from .vp import _bad_patterns


DEFAULT_STOCK_DIR = Path("artifacts/20260703T115149Z-vp-stock-lenet")
LENET_WORKLOAD = "lenet_mnist"
LENET_SMALL_OUTPUT = "0 2 0 0 0 0 0 124 0 0"


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize_output(text: str) -> str:
    return " ".join(text.split())


def _docker_image_id(image: str) -> str | None:
    try:
        cp = run_command(["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=30)
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def _lenet_lock(lock_path: Path) -> dict[str, Any]:
    lock = read_json(lock_path)
    try:
        return lock["workloads"][LENET_WORKLOAD]
    except KeyError as exc:
        raise KeyError(f"missing workloads.{LENET_WORKLOAD} in {lock_path}") from exc


def _lenet_source_dir(lock_path: Path, sources_dir: Path) -> Path:
    spec = _lenet_lock(lock_path)
    return sources_dir / spec.get("source_dir", "lenet-mnist")


def _verify_file(path: Path, expected_sha256: str) -> str:
    actual = sha256_file(path)
    if actual.upper() != expected_sha256.upper():
        raise ValueError(f"sha256 mismatch for {path}: expected {expected_sha256}, got {actual}")
    return actual


def fetch_lenet_sources(lock_path: Path, sources_dir: Path) -> int:
    spec = _lenet_lock(lock_path)
    out = _lenet_source_dir(lock_path, sources_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest_files = []
    for item in spec["files"]:
        dst = out / item["name"]
        expected = item["sha256"]
        if dst.is_file():
            try:
                actual = _verify_file(dst, expected)
            except ValueError:
                dst.unlink()
            else:
                manifest_files.append(
                    {"name": item["name"], "url": item["url"], "sha256": actual, "status": "cached"}
                )
                continue
        print(f"Fetching {item['url']}")
        urllib.request.urlretrieve(item["url"], dst)
        actual = _verify_file(dst, expected)
        manifest_files.append(
            {"name": item["name"], "url": item["url"], "sha256": actual, "status": "downloaded"}
        )

    write_json(
        out / "manifest.json",
        {
            "schema_version": 1,
            "name": LENET_WORKLOAD,
            "source_dir": str(out),
            "files": manifest_files,
        },
    )
    print(f"LeNet sources ready: {out}")
    return 0


def build_lenet_small_workload(lock_path: Path, sources_dir: Path, out_dir: Path) -> int:
    spec = _lenet_lock(lock_path)
    source = _lenet_source_dir(lock_path, sources_dir)
    missing = [item["name"] for item in spec["files"] if not (source / item["name"]).is_file()]
    if missing:
        raise FileNotFoundError(f"missing LeNet source files under {source}: {', '.join(missing)}")
    for item in spec["files"]:
        _verify_file(source / item["name"], item["sha256"])

    lock = read_json(lock_path)
    image = lock["docker"]["vp_latest"]["image"]
    image_id = _docker_image_id(image)
    if image_id is None:
        raise RuntimeError(f"Docker image is not available or inspect failed: {image}")

    out_dir.mkdir(parents=True, exist_ok=True)
    compiler = spec["compiler"]
    compiler_candidates = [
        "/usr/local/nvdla/nvdla_compiler",
        "/usr/local/nvdla/sw/prebuilt/linux/nvdla_compiler",
        "/usr/local/nvdla/sw/prebuilt/x86/nvdla_compiler",
        "/usr/local/nvdla/sw/prebuilt/x86_64-linux/nvdla_compiler",
        "/usr/local/nvdla/sw/outdir/ubuntu/nvdla_compiler",
        "/usr/local/nvdla/sw/outdir/x86_64-linux/nvdla_compiler",
    ]
    candidate_text = " ".join(compiler_candidates)
    command_text = (
        "set -eu; "
        "rm -rf /work/wisdom.dir /work/fast-math.nvdla /work/output.protobuf; "
        "cp /src/lenet_mnist.prototxt /src/lenet_mnist.caffemodel /src/lenet_mnist.json /src/seven.pgm /work/; "
        "cd /work; "
        "compiler_bin=''; "
        f"for candidate in {candidate_text}; do "
        "if [ -x \"$candidate\" ]; then compiler_bin=\"$candidate\"; break; fi; "
        "done; "
        "if [ -z \"$compiler_bin\" ]; then "
        "compiler_bin=$(find /usr/local/nvdla -type f -name nvdla_compiler -print -quit 2>/dev/null || true); "
        "fi; "
        "if [ -z \"$compiler_bin\" ] || [ ! -x \"$compiler_bin\" ]; then "
        "echo 'nvdla_compiler not found in Docker image' >&2; exit 127; "
        "fi; "
        "compiler_dir=$(dirname \"$compiler_bin\"); "
        "export LD_LIBRARY_PATH=$compiler_dir:/usr/local/nvdla:/usr/local/nvdla/sw/prebuilt/linux:${LD_LIBRARY_PATH:-}; "
        "echo \"__NVDLA_COMPILER__=$compiler_bin\"; "
        "\"$compiler_bin\" "
        "--prototxt lenet_mnist.prototxt "
        "--caffemodel lenet_mnist.caffemodel "
        "-o . "
        f"--profile {compiler['profile']} "
        f"--cprecision {compiler['cprecision']} "
        f"--configtarget {compiler['configtarget']} "
        "--calibtable lenet_mnist.json "
        f"--quantizationMode {compiler['quantizationMode']} "
        f"--informat {compiler['informat']}; "
        "mv fast-math.nvdla lenet_mnist.nv_small.nvdla"
    )

    # The stock VP image keeps nvdla_compiler executable only by root, so use
    # the image default user for this reproducible compiler step.
    docker_cmd = ["docker", "run", "--rm", "-e", "HOME=/tmp"]
    docker_cmd.extend(
        [
            "-v",
            f"{source.resolve()}:/src:ro",
            "-v",
            f"{out_dir.resolve()}:/work",
            "-w",
            "/work",
            image,
            "bash",
            "-lc",
            command_text,
        ]
    )
    try:
        cp = run_command(docker_cmd, timeout=300)
    except OSError as exc:
        raise RuntimeError(f"LeNet compiler Docker command could not start: {exc}") from exc
    (out_dir / "compiler.log").write_text(cp.stdout, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(f"LeNet compiler failed with status {cp.returncode}; see {out_dir / 'compiler.log'}")
    compiler_match = re.search(r"^__NVDLA_COMPILER__=(.+)$", cp.stdout, flags=re.MULTILINE)
    compiler_path = compiler_match.group(1).strip() if compiler_match else None

    loadable = out_dir / "lenet_mnist.nv_small.nvdla"
    expected_loadable = spec.get("expected_loadable_sha256")
    loadable_sha = sha256_file(loadable)
    if expected_loadable and loadable_sha.upper() != expected_loadable.upper():
        raise ValueError(
            f"compiled loadable hash mismatch: expected {expected_loadable}, got {loadable_sha}"
        )

    for item in spec["files"]:
        shutil.copy2(source / item["name"], out_dir / item["name"])
    (out_dir / "expected-output.txt").write_text(spec["expected_output"] + "\n", encoding="utf-8")

    generated = {
        "schema_version": 1,
        "name": "lenet_small",
        "kind": "compiled_caffe_lenet_mnist",
        "source": {
            "source_dir": str(source),
            "files": [
                {
                    "name": item["name"],
                    "url": item["url"],
                    "sha256": sha256_file(source / item["name"]),
                }
                for item in spec["files"]
            ],
        },
        "compiler": {
            "docker_image": image,
            "docker_image_id": image_id,
            "path": compiler_path,
            "discovery_candidates": compiler_candidates,
            "command": command_text,
            **compiler,
        },
        "loadable": {
            "path": "lenet_mnist.nv_small.nvdla",
            "sha256": loadable_sha,
            "size_bytes": loadable.stat().st_size,
        },
        "output_protobuf": {
            "path": "output.protobuf",
            "sha256": sha256_file(out_dir / "output.protobuf"),
            "size_bytes": (out_dir / "output.protobuf").stat().st_size,
        },
        "image": {
            "path": "seven.pgm",
            "sha256": sha256_file(out_dir / "seven.pgm"),
        },
        "expected_output": spec["expected_output"],
        "tolerance": {"type": "exact"},
        "target": spec["target"],
    }
    write_json(out_dir / "generated-manifest.json", generated)
    print(f"LeNet nv_small workload ready: {out_dir}")
    return 0


def _latest_modern_artifact(root: Path) -> Path:
    candidates = sorted(root.glob("*-vp-modern-lenet-full"))
    if not candidates:
        raise FileNotFoundError(f"no modern LeNet artifacts under {root}")
    return candidates[-1]


def _completed_layers(text: str) -> list[dict[str, Any]]:
    layers = []
    pattern = re.compile(r"Completed\s+(\w+)\s+operation index\s+(\d+)\s+ROI\s+(\d+)")
    for match in pattern.finditer(text):
        layers.append(
            {
                "processor": match.group(1),
                "index": int(match.group(2)),
                "roi": int(match.group(3)),
            }
        )
    return layers


def _hwl_progress(text: str) -> dict[str, int | None]:
    matches = re.findall(r"(\d+)\s+HWLs done,\s+totally\s+(\d+)\s+layers", text)
    if not matches:
        return {"done": None, "total": None}
    done, total = matches[-1]
    return {"done": int(done), "total": int(total)}


def _hwl_pass_count(text: str) -> int:
    return len(re.findall(r"\b10\s+HWLs done,\s+totally\s+10\s+layers", text))


def _pre_dmesg_text(text: str) -> str:
    return text.split("__NVDLA_SECTION_dmesg_BEGIN__", 1)[0]


def _extract_probe_config(text: str) -> str | None:
    match = re.search(r"Probe NVDLA config\s+([^\s\r\n]+)", text)
    return match.group(1).strip() if match else None


def _extract_render_node(text: str) -> str | None:
    match = re.search(r"__NVDLA_RENDER_NODE__=([^\r\n]*)", text)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _repeat_output_paths(artifact: Path) -> list[tuple[int, Path]]:
    repeat_root = artifact / "runtime-output"
    paths = []
    for path in sorted(repeat_root.glob("repeat-*/output.txt")):
        match = re.search(r"repeat-(\d+)$", path.parent.name)
        if match:
            paths.append((int(match.group(1)), path))
    if paths:
        return sorted(paths)
    return [(1, repeat_root / "output.txt")]


def _trace_summary(text: str) -> dict[str, Any]:
    tags: dict[str, int] = {}
    hashes: list[dict[str, Any]] = []
    pattern = re.compile(
        r"nvdla-trace\s+(\S+)\s+index=(\d+)\s+fd=(\d+).*?"
        r"hash=0x([0-9a-fA-F]+)\s+first=([0-9a-fA-F]*)"
    )
    for match in pattern.finditer(text):
        tag = match.group(1)
        tags[tag] = tags.get(tag, 0) + 1
        hashes.append(
            {
                "tag": tag,
                "index": int(match.group(2)),
                "fd": int(match.group(3)),
                "hash": match.group(4).lower(),
                "first": match.group(5).lower(),
            }
        )
    return {
        "line_count": sum(tags.values()),
        "tags": tags,
        "hashes": hashes,
    }


def _bad_pattern_lines(path: Path) -> list[str]:
    return [line.strip() for line in _read_text(path).splitlines() if line.strip()]


def _stock_summary(stock_dir: Path) -> dict[str, Any]:
    output = stock_dir / "output-nvfull-visible.txt"
    dmesg = stock_dir / "dmesg-nvfull-visible.log"
    serial_text = _read_text(dmesg)
    return {
        "dir": str(stock_dir),
        "output": _normalize_output(_read_text(output)),
        "output_sha256": sha256_file(output) if output.is_file() else None,
        "completed_layers": _completed_layers(serial_text),
        "hwl_progress": _hwl_progress(serial_text),
        "bad_patterns": _bad_patterns(serial_text),
        "trace": _trace_summary(serial_text),
        "log": str(dmesg),
    }


def _modern_summary(modern_dir: Path) -> dict[str, Any]:
    manifest_path = modern_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    output_path = modern_dir / "runtime-output" / "output.txt"
    serial = modern_dir / "serial.log"
    dmesg = modern_dir / "dmesg.log"
    bad_patterns = modern_dir / "bad-patterns.log"
    serial_text = _read_text(serial)
    dmesg_text = _read_text(dmesg)
    analysis_text = dmesg_text or serial_text
    output = manifest.get("actual_output") or _normalize_output(_read_text(output_path))
    expected = manifest.get("expected_output")
    bad_pattern_hits = sorted(set(_bad_patterns(analysis_text) + _bad_pattern_lines(bad_patterns)))
    return {
        "dir": str(modern_dir),
        "status": manifest.get("status"),
        "expected_output": expected,
        "actual_output": _normalize_output(output),
        "output_sha256": sha256_file(output_path) if output_path.is_file() else None,
        "completed_layers": _completed_layers(analysis_text),
        "hwl_progress": _hwl_progress(analysis_text),
        "bad_patterns": bad_pattern_hits,
        "trace": _trace_summary(analysis_text),
        "manifest": str(manifest_path),
        "module_sha256": ((manifest.get("inputs") or {}).get("module") or {}).get("sha256"),
        "runtime_sha256": ((manifest.get("inputs") or {}).get("runtime") or {}).get("sha256"),
        "loadable_sha256": ((manifest.get("inputs") or {}).get("loadable") or {}).get("sha256"),
        "vp_ram": manifest.get("vp_ram"),
    }


def _classify(stock: dict[str, Any], modern: dict[str, Any]) -> str:
    if modern["bad_patterns"]:
        return "kernel_log_bad_pattern"
    if not modern["actual_output"]:
        return "missing_modern_output"
    if modern["actual_output"] == stock["output"]:
        return "pass"
    progress = modern.get("hwl_progress") or {}
    if progress.get("done") == progress.get("total") and progress.get("done"):
        return "runtime_clean_output_mismatch"
    if modern["completed_layers"]:
        return "partial_execution_output_mismatch"
    return "inconclusive_output_mismatch"


def analyze_lenet_artifact(artifact: Path, expected_output: str | None = None, out: Path | None = None) -> int:
    artifact = artifact.resolve()
    manifest_path = artifact / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    expected = expected_output or manifest.get("expected_output") or LENET_SMALL_OUTPUT
    expected = _normalize_output(expected)

    serial = _read_text(artifact / "serial.log")
    dmesg = _read_text(artifact / "dmesg.log")
    bad_file = _bad_pattern_lines(artifact / "bad-patterns.log")
    analysis_text = _pre_dmesg_text(serial) or dmesg
    bad_patterns = sorted(set(_bad_patterns(serial + "\n" + dmesg) + bad_file))

    repeat_results = []
    first_failure = None
    for index, output_path in _repeat_output_paths(artifact):
        actual = _normalize_output(_read_text(output_path))
        output_sha = sha256_file(output_path) if output_path.is_file() else None
        status = "pass" if actual == expected and output_sha else "fail"
        result = {
            "index": index,
            "status": status,
            "expected_output": expected,
            "actual_output": actual,
            "output_sha256": output_sha,
            "output_path": str(output_path.relative_to(artifact)) if output_path.exists() else str(output_path),
        }
        if status != "pass":
            result["reason"] = "missing output" if not actual else "output mismatch"
            if first_failure is None:
                first_failure = result
        repeat_results.append(result)

    pass_count = sum(1 for item in repeat_results if item["status"] == "pass")
    requested_repeat = int(manifest.get("repeat_count") or manifest.get("repeat") or len(repeat_results) or 1)
    layer_summary = {
        "completed_operations": len(_completed_layers(analysis_text)),
        "hwl_pass_markers": _hwl_pass_count(analysis_text),
        "expected_hwl_pass_markers": requested_repeat,
        "hwl_progress": _hwl_progress(analysis_text),
    }
    probe_config = _extract_probe_config(serial + "\n" + dmesg)
    render_node = _extract_render_node(serial)
    loadable_config = ((manifest.get("inputs") or {}).get("loadable") or {}).get("config")
    config_proof = {
        "vp_hw_config": manifest.get("vp_hw_config"),
        "vp_runner": manifest.get("vp_runner"),
        "probe_config": probe_config,
        "render_node": render_node,
        "loadable_config": loadable_config,
        "vp_ram": manifest.get("vp_ram"),
    }

    classification = "pass"
    if bad_patterns:
        classification = "kernel_or_vp_bad_pattern"
    elif config_proof["vp_hw_config"] != "small" or probe_config != "nvidia,nv_small" or loadable_config != "nv_small":
        classification = "config_mismatch"
    elif pass_count != requested_repeat or pass_count != len(repeat_results):
        classification = "output_mismatch_or_missing"
    elif layer_summary["hwl_pass_markers"] < requested_repeat:
        classification = "incomplete_layer_execution"

    status = "pass" if classification == "pass" else "fail"
    analysis = {
        "schema_version": 1,
        "artifact": str(artifact),
        "status": status,
        "classification": classification,
        "repeat_count": requested_repeat,
        "pass_count": pass_count,
        "first_failure": first_failure,
        "repeat_results": repeat_results,
        "layer_summary": layer_summary,
        "bad_patterns": bad_patterns,
        "config_proof": config_proof,
    }

    out_path = out or (artifact / "lenet-analysis.json")
    write_json(out_path, analysis)
    if manifest_path.is_file():
        manifest["status"] = status
        manifest["reason"] = None if status == "pass" else classification
        manifest["repeat_count"] = requested_repeat
        manifest["pass_count"] = pass_count
        manifest["first_failure"] = first_failure
        manifest["repeat_results"] = repeat_results
        manifest["probe_config"] = probe_config
        manifest["render_node"] = render_node
        manifest["layer_summary"] = layer_summary
        manifest["analysis"] = str(out_path.relative_to(artifact)) if out_path.parent == artifact else str(out_path)
        write_json(manifest_path, manifest)

    print(f"LeNet analysis status: {status}")
    print(f"Classification: {classification}")
    print(f"Analysis: {out_path}")
    return 0 if status == "pass" else 1


def compare_lenet_control(stock_dir: Path, modern_dir: Path | None, out: Path | None) -> int:
    stock_dir = stock_dir.resolve()
    modern_dir = (modern_dir.resolve() if modern_dir else _latest_modern_artifact(Path("artifacts"))).resolve()
    stock = _stock_summary(stock_dir)
    modern = _modern_summary(modern_dir)
    output_match = bool(stock["output"]) and stock["output"] == modern["actual_output"]
    layer_sequence_match = stock["completed_layers"] == modern["completed_layers"]
    result = {
        "schema_version": 1,
        "status": "pass" if output_match and not modern["bad_patterns"] else "fail",
        "classification": _classify(stock, modern),
        "stock": stock,
        "modern": modern,
        "comparisons": {
            "output_match": output_match,
            "layer_sequence_match": layer_sequence_match,
            "stock_layer_count": len(stock["completed_layers"]),
            "modern_layer_count": len(modern["completed_layers"]),
        },
    }
    out_path = out or (modern_dir / "lenet-compare.json")
    write_json(out_path, result)
    print(f"LeNet comparison status: {result['status']}")
    print(f"Classification: {result['classification']}")
    print(f"Comparison: {out_path}")
    return 0 if result["status"] == "pass" else 1
