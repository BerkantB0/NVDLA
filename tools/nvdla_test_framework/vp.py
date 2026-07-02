from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .common import read_json, repo_root, run_command, sha256_file, utc_run_id, write_json
from .patches import patch_series_fingerprint
from .workloads import compare_exact_files


KERNEL_BAD_PATTERNS = [
    r"\bOops\b",
    r"\bBUG:",
    r"\bWARNING:",
    r"DMA-API",
    r"scheduler timeout",
    r"interrupt timeout",
    r"TLM_ADDRESS_ERROR_RESPONSE",
    r"sc_signal<.*cannot have more than one driver",
    r"Error: \(E[0-9]+\)",
]

SMOKE_SOURCE = repo_root() / "tools" / "smoke" / "nvdla-kmd-smoke.c"
RUNTIME_CLIENT = repo_root() / "tools" / "runtime" / "nvdla_flatbuf_client.py"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _expand_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(os.fspath(value))))


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return _expand_path(value) if value else default


def _path_from_env_or_first(name: str, candidates: list[Path]) -> Path:
    value = os.environ.get(name)
    if value:
        return _expand_path(value)
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _git_sha(path: Path) -> str | None:
    if not path.exists():
        return None
    cp = run_command(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=15)
    if cp.returncode != 0:
        return None
    return cp.stdout.strip()


def _bad_patterns(text: str) -> list[str]:
    return [pat for pat in KERNEL_BAD_PATTERNS if re.search(pat, text, flags=re.IGNORECASE)]


def _stock_vp_boot(lock: dict[str, Any], timeout: int, out_dir: Path) -> dict[str, Any]:
    image = lock["docker"]["vp_latest"]["image"]
    command = [
        "docker",
        "run",
        "--rm",
        image,
        "bash",
        "-lc",
        f"cd /usr/local/nvdla && timeout {timeout}s aarch64_toplevel -c aarch64_nvdla.lua",
    ]
    cp = run_command(command, timeout=timeout + 10)
    log = cp.stdout
    _write_text(out_dir / "serial.log", log)

    reached_login = "Welcome to Buildroot" in log and "nvdla login:" in log
    bad = _bad_patterns(log)
    status = "pass" if reached_login and not bad and cp.returncode in {0, 124} else "fail"
    return {
        "status": status,
        "returncode": cp.returncode,
        "reached_login": reached_login,
        "bad_patterns": bad,
        "serial_log": "serial.log",
    }


def _compiler_smoke(lock: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    image = lock["docker"]["vp_latest"]["image"]
    cp = run_command(
        [
            "docker",
            "run",
            "--rm",
            image,
            "bash",
            "-lc",
            "LD_LIBRARY_PATH=/usr/local/nvdla /usr/local/nvdla/nvdla_compiler -h",
        ],
        timeout=20,
    )
    _write_text(out_dir / "compiler.stdout.log", cp.stdout)
    ok = cp.returncode == 0 and "--configtarget <nv_full|nv_large|nv_small>" in cp.stdout
    return {
        "status": "pass" if ok else "fail",
        "returncode": cp.returncode,
        "log": "compiler.stdout.log",
    }


def _existing_command(path_or_name: str) -> str | None:
    path = Path(path_or_name)
    if path.is_file():
        return str(path)
    found = shutil.which(path_or_name)
    return found


def _resolve_cross_compile(work_dir: Path) -> dict[str, str | None]:
    candidates = []
    if os.environ.get("CROSS_COMPILE"):
        candidates.append(("user", os.environ["CROSS_COMPILE"]))
    candidates.append(
        (
            "buildroot",
            str(work_dir / "buildroot" / "host" / "bin" / "aarch64-buildroot-linux-gnu-"),
        )
    )
    candidates.append(("apt", "aarch64-linux-gnu-"))

    for source, prefix in candidates:
        gcc = _existing_command(f"{prefix}gcc")
        if gcc:
            machine = run_command([gcc, "-dumpmachine"], timeout=10).stdout.strip()
            version = run_command([gcc, "--version"], timeout=10).stdout.splitlines()
            return {
                "source": source,
                "cross_compile": prefix,
                "gcc": gcc,
                "machine": machine,
                "version": version[0] if version else None,
            }

    return {
        "source": None,
        "cross_compile": None,
        "gcc": None,
        "machine": None,
        "version": None,
    }


def _modern_paths(
    work_dir: Path | None,
    sources_dir: Path | None,
) -> dict[str, Path | None]:
    root = repo_root()
    work = work_dir or _path_from_env("WORK_DIR", root / ".work" / "vp-modern")
    sources = sources_dir or _path_from_env("SOURCES_DIR", root / ".external" / "sources")
    patched = _path_from_env("PATCHED_NVDLA_SW", root / ".work" / "nvdla-sw-patched")

    dtb_candidates = [
        _path_from_env("VP_MODERN_DTB", work / "kernel" / "arch" / "arm64" / "boot" / "dts" / "nvdla-vp-modern.dtb"),
        work / "kernel" / "arch" / "arm64" / "boot" / "dts" / "qemu" / "nvdla-vp-modern.dtb",
        work / "kernel" / "arch" / "arm64" / "boot" / "dts" / "xilinx" / "nvdla-vp-modern.dtb",
    ]
    dtb = next((path for path in dtb_candidates if path and path.exists()), None)

    return {
        "work_dir": work,
        "sources_dir": sources,
        "linux": sources / "linux-xlnx",
        "buildroot": sources / "buildroot",
        "patched_nvdla_sw": patched,
        "kernel": _path_from_env_or_first(
            "VP_MODERN_KERNEL",
            [
                work / "kernel" / "arch" / "arm64" / "boot" / "Image.vp2m",
                work / "kernel" / "arch" / "arm64" / "boot" / "Image",
            ],
        ),
        "rootfs": _path_from_env_or_first(
            "VP_MODERN_ROOTFS",
            [
                work / "buildroot" / "images" / "rootfs-smoke.ext4",
                work / "buildroot" / "images" / "rootfs.ext4",
            ],
        ),
        "module": _path_from_env("VP_MODERN_KO", work / "modules" / "opendla.ko"),
        "runtime_binary": _path_from_env("VP_RUNTIME_BIN", work / "runtime" / "nvdla_runtime"),
        "runtime_library": _path_from_env("VP_RUNTIME_LIB", work / "runtime" / "libnvdla_runtime.so"),
        "workloads_dir": _path_from_env("WORKLOADS_DIR", root / "artifacts" / "workloads"),
        "dtb": dtb,
    }


def _path_hash(path: Path | None) -> str | None:
    if path and path.is_file():
        return sha256_file(path)
    return None


def _check_required_paths(paths: dict[str, Path | None], required: list[str]) -> list[str]:
    missing = []
    for name in required:
        path = paths.get(name)
        if not path or not path.exists():
            missing.append(f"{name}: {path}")
    return missing


def _resolve_workload(paths: dict[str, Path | None], workload_name: str) -> dict[str, Any]:
    workloads_dir = paths["workloads_dir"]
    assert isinstance(workloads_dir, Path)
    workload_dir = workloads_dir / workload_name
    manifest_path = workload_dir / "generated-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing workload manifest: {manifest_path}; run make workloads")

    manifest = read_json(manifest_path)
    loadable = workload_dir / manifest["loadable"]["path"]
    golden_outputs = manifest.get("golden_outputs") or []
    if not golden_outputs:
        raise FileNotFoundError(f"workload manifest has no golden_outputs: {manifest_path}")
    golden = workload_dir / golden_outputs[0]["path"]
    missing = []
    if not loadable.is_file():
        missing.append(f"loadable: {loadable}")
    if not golden.is_file():
        missing.append(f"golden: {golden}")
    if missing:
        raise FileNotFoundError("missing workload files:\n" + "\n".join(missing))

    return {
        "name": workload_name,
        "dir": workload_dir,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "loadable": loadable,
        "golden": golden,
        "output_name": golden_outputs[0].get("name", "o_000000.dimg"),
    }


def _build_smoke_binary(paths: dict[str, Path | None], out_dir: Path) -> dict[str, Any]:
    work_dir = paths["work_dir"]
    patched = paths["patched_nvdla_sw"]
    assert isinstance(work_dir, Path)
    assert isinstance(patched, Path)

    toolchain = _resolve_cross_compile(work_dir)
    include_dir = patched / "kmd" / "port" / "linux" / "include"
    output = out_dir / "payload" / "nvdla-kmd-smoke"
    log_path = out_dir / "smoke-build.log"

    if not toolchain["gcc"]:
        return {
            "status": "blocked",
            "reason": "no ARM64 cross compiler found for smoke utility",
            "toolchain": toolchain,
            "log": "smoke-build.log",
        }
    if not include_dir.exists():
        return {
            "status": "blocked",
            "reason": f"NVDLA KMD include directory not found: {include_dir}",
            "toolchain": toolchain,
            "log": "smoke-build.log",
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(toolchain["gcc"]),
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-O2",
        "-I",
        str(include_dir),
        "-o",
        str(output),
        str(SMOKE_SOURCE),
    ]
    cp = run_command(command, timeout=60)
    _write_text(log_path, "+" + " ".join(command) + "\n" + cp.stdout)
    if cp.returncode != 0:
        return {
            "status": "fail",
            "reason": "nvdla-kmd-smoke build failed",
            "returncode": cp.returncode,
            "toolchain": toolchain,
            "log": "smoke-build.log",
        }

    output.chmod(0o755)
    return {
        "status": "pass",
        "path": str(output),
        "sha256": sha256_file(output),
        "toolchain": toolchain,
        "log": "smoke-build.log",
    }


def _write_payload(
    paths: dict[str, Path | None],
    out_dir: Path,
    repeat: int,
    mode: str,
    workload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = out_dir / "payload"
    payload.mkdir(parents=True, exist_ok=True)

    module = paths["module"]
    assert isinstance(module, Path)
    _copy_file(module, payload / "opendla.ko")

    if mode == "runtime":
        runtime_binary = paths["runtime_binary"]
        runtime_library = paths["runtime_library"]
        assert isinstance(runtime_binary, Path)
        assert isinstance(runtime_library, Path)
        assert workload is not None
        _copy_file(runtime_binary, payload / "nvdla_runtime")
        _copy_file(runtime_library, payload / "libnvdla_runtime.so")
        _copy_file(RUNTIME_CLIENT, payload / "nvdla_flatbuf_client.py")
        _copy_file(workload["loadable"], payload / "loadable.fbuf")
        _copy_file(workload["golden"], payload / "golden" / "o_000000.dimg")

        script = payload / "run-modern-smoke.sh"
        script.write_text(
            f"""#!/bin/sh
set +e

repeat={max(1, repeat)}
runtime_timeout="${{NVDLA_RUNTIME_TIMEOUT:-120}}"
server_start_timeout="${{NVDLA_SERVER_START_TIMEOUT:-45}}"

cat_section() {{
    name="$1"
    file="$2"
    echo "__NVDLA_SECTION_${{name}}_BEGIN__"
    if [ -f "$file" ]; then
        cat "$file"
    fi
    echo "__NVDLA_SECTION_${{name}}_END__"
}}

echo "__NVDLA_RUNTIME_BEGIN__"
echo "__NVDLA_SECTION_uname_BEGIN__"
uname -a
echo "__NVDLA_SECTION_uname_END__"

mkdir -p /mnt/w
mount -t 9p -o trans=virtio,version=9p2000.L w /mnt/w || mount -t 9p -o trans=virtio w /mnt/w
WRITE_STATUS=$?
echo "__NVDLA_STATUS_writable_mount=$WRITE_STATUS"
if [ "$WRITE_STATUS" -eq 0 ]; then
    mkdir -p /mnt/w/runtime-output
fi

if command -v modinfo >/dev/null 2>&1; then
    modinfo -F vermagic /mnt/r/opendla.ko >/tmp/module-vermagic.txt 2>&1
else
    strings /mnt/r/opendla.ko | sed -n 's/^vermagic=//p' | head -n 1 >/tmp/module-vermagic.txt 2>&1
fi
VERMAGIC_STATUS=$?
cat_section module_vermagic /tmp/module-vermagic.txt
echo "__NVDLA_STATUS_module_vermagic=$VERMAGIC_STATUS"

insmod /mnt/r/opendla.ko >/tmp/module-load.log 2>&1
MODULE_STATUS=$?
cat_section module_load /tmp/module-load.log
echo "__NVDLA_STATUS_module_load=$MODULE_STATUS"

sleep 1
ls -l /dev/dri >/tmp/dev-dri.txt 2>&1
DRI_STATUS=$?
cat_section dev_dri /tmp/dev-dri.txt
echo "__NVDLA_STATUS_dev_dri=$DRI_STATUS"

NODE="${{NVDLA_DEVICE_NODE:-}}"
if [ -z "$NODE" ]; then
    NODE="$(ls /dev/dri/renderD* 2>/dev/null | head -n 1)"
fi
echo "__NVDLA_RENDER_NODE__=$NODE"

RUNTIME_STATUS=97
CLIENT_STATUS=97
COMPARE_STATUS=97
SERVER_READY=1
i=1
while [ "$i" -le "$repeat" ]; do
    rm -rf /tmp/nvdla-runtime
    mkdir -p /tmp/nvdla-runtime/outputs
    : >/tmp/runtime-server.log
    : >/tmp/runtime-client.log
    : >/tmp/runtime-compare.log

    if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ -n "$NODE" ]; then
        NVDLA_DEVICE_NODE="$NODE" LD_LIBRARY_PATH=/mnt/r /mnt/r/nvdla_runtime -s \
            >/tmp/runtime-server.log 2>&1 &
        SERVER_PID=$!
        elapsed=0
        SERVER_READY=1
        while [ "$elapsed" -lt "$server_start_timeout" ]; do
            if grep -q "Ready for Client Connection" /tmp/runtime-server.log; then
                SERVER_READY=0
                break
            fi
            if ! kill -0 "$SERVER_PID" 2>/dev/null; then
                break
            fi
            sleep 1
            elapsed=$((elapsed + 1))
        done
        echo "__NVDLA_STATUS_runtime_server_ready_$i=$SERVER_READY"

        if [ "$SERVER_READY" -eq 0 ]; then
            LD_LIBRARY_PATH=/mnt/r python3 /mnt/r/nvdla_flatbuf_client.py \
                --flatbuf /mnt/r/loadable.fbuf \
                --out-dir /tmp/nvdla-runtime/outputs \
                --timeout "$runtime_timeout" \
                >/tmp/runtime-client.log 2>&1
            CLIENT_STATUS=$?
        else
            echo "runtime server did not become ready" >>/tmp/runtime-client.log
            CLIENT_STATUS=125
        fi

        elapsed=0
        while kill -0 "$SERVER_PID" 2>/dev/null && [ "$elapsed" -lt 10 ]; do
            sleep 1
            elapsed=$((elapsed + 1))
        done
        if kill -0 "$SERVER_PID" 2>/dev/null; then
            kill "$SERVER_PID" 2>/dev/null
        fi
        wait "$SERVER_PID" 2>/dev/null

        if [ "$CLIENT_STATUS" -eq 0 ]; then
            cmp -s /mnt/r/golden/o_000000.dimg /tmp/nvdla-runtime/outputs/o_000000.dimg
            COMPARE_STATUS=$?
            if [ "$COMPARE_STATUS" -ne 0 ]; then
                cmp -l /mnt/r/golden/o_000000.dimg /tmp/nvdla-runtime/outputs/o_000000.dimg \
                    | head -n 20 >/tmp/runtime-compare.log 2>&1
            else
                echo "exact match" >/tmp/runtime-compare.log
            fi
        else
            COMPARE_STATUS=126
            echo "client failed with status $CLIENT_STATUS" >/tmp/runtime-compare.log
        fi
    else
        echo "module_status=$MODULE_STATUS dri_status=$DRI_STATUS node=$NODE" >/tmp/runtime-client.log
        CLIENT_STATUS=98
        COMPARE_STATUS=98
    fi

    if [ "$WRITE_STATUS" -eq 0 ]; then
        cp /tmp/runtime-server.log /mnt/w/runtime-output/runtime-server.log 2>/dev/null
        cp /tmp/runtime-client.log /mnt/w/runtime-output/runtime-client.log 2>/dev/null
        cp /tmp/runtime-compare.log /mnt/w/runtime-output/runtime-compare.log 2>/dev/null
        cp /tmp/nvdla-runtime/outputs/o_000000.dimg /mnt/w/runtime-output/o_000000.dimg 2>/dev/null
    fi

    echo "__NVDLA_STATUS_runtime_client_$i=$CLIENT_STATUS"
    echo "__NVDLA_STATUS_runtime_compare_$i=$COMPARE_STATUS"
    if [ "$CLIENT_STATUS" -eq 0 ] && [ "$COMPARE_STATUS" -eq 0 ]; then
        RUNTIME_STATUS=0
    else
        RUNTIME_STATUS=1
        break
    fi
    i=$((i + 1))
done

cat_section runtime_server /tmp/runtime-server.log
cat_section runtime_client /tmp/runtime-client.log
cat_section runtime_compare /tmp/runtime-compare.log
echo "__NVDLA_STATUS_runtime_server_ready=$SERVER_READY"
echo "__NVDLA_STATUS_runtime_client=$CLIENT_STATUS"
echo "__NVDLA_STATUS_runtime_compare=$COMPARE_STATUS"
echo "__NVDLA_STATUS_runtime=$RUNTIME_STATUS"

dmesg 2>&1 | tail -n 200 >/tmp/dmesg.log
cat_section dmesg /tmp/dmesg.log

echo "__NVDLA_RESULT__ module=$MODULE_STATUS dri=$DRI_STATUS runtime=$RUNTIME_STATUS repeat=$repeat"
echo "__NVDLA_RUNTIME_END__"

if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ "$RUNTIME_STATUS" -eq 0 ]; then
    exit 0
fi
exit 1
""",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return {
            "path": str(payload),
            "mode": mode,
            "module": str(payload / "opendla.ko"),
            "runtime_binary": str(payload / "nvdla_runtime"),
            "runtime_library": str(payload / "libnvdla_runtime.so"),
            "client": str(payload / "nvdla_flatbuf_client.py"),
            "loadable": str(payload / "loadable.fbuf"),
            "golden": str(payload / "golden" / "o_000000.dimg"),
            "script": str(script),
        }

    script = payload / "run-modern-smoke.sh"
    script.write_text(
        f"""#!/bin/sh
set +e

repeat={max(1, repeat)}
smoke_timeout="${{NVDLA_SMOKE_TIMEOUT:-30}}"

cat_section() {{
    name="$1"
    file="$2"
    echo "__NVDLA_SECTION_${{name}}_BEGIN__"
    if [ -f "$file" ]; then
        cat "$file"
    fi
    echo "__NVDLA_SECTION_${{name}}_END__"
}}

echo "__NVDLA_SMOKE_BEGIN__"
echo "__NVDLA_SECTION_uname_BEGIN__"
uname -a
echo "__NVDLA_SECTION_uname_END__"

if command -v modinfo >/dev/null 2>&1; then
    modinfo -F vermagic /mnt/r/opendla.ko >/tmp/module-vermagic.txt 2>&1
else
    strings /mnt/r/opendla.ko | sed -n 's/^vermagic=//p' | head -n 1 >/tmp/module-vermagic.txt 2>&1
fi
VERMAGIC_STATUS=$?
cat_section module_vermagic /tmp/module-vermagic.txt
echo "__NVDLA_STATUS_module_vermagic=$VERMAGIC_STATUS"

insmod /mnt/r/opendla.ko >/tmp/module-load.log 2>&1
MODULE_STATUS=$?
cat_section module_load /tmp/module-load.log
echo "__NVDLA_STATUS_module_load=$MODULE_STATUS"

sleep 1
ls -l /dev/dri >/tmp/dev-dri.txt 2>&1
DRI_STATUS=$?
cat_section dev_dri /tmp/dev-dri.txt
echo "__NVDLA_STATUS_dev_dri=$DRI_STATUS"

NODE="${{NVDLA_DEVICE_NODE:-}}"
if [ -z "$NODE" ]; then
    NODE="$(ls /dev/dri/renderD* 2>/dev/null | head -n 1)"
fi
echo "__NVDLA_RENDER_NODE__=$NODE"

SMOKE_STATUS=97
i=1
while [ "$i" -le "$repeat" ]; do
    if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ -n "$NODE" ]; then
        rm -f /tmp/runtime.status
        (
            NVDLA_DEVICE_NODE="$NODE" /mnt/r/nvdla-kmd-smoke \
                >/tmp/runtime.stdout.log 2>/tmp/runtime.stderr.log
            echo "$?" >/tmp/runtime.status
        ) &
        SMOKE_PID=$!
        elapsed=0
        while [ ! -f /tmp/runtime.status ] && [ "$elapsed" -lt "$smoke_timeout" ]; do
            sleep 1
            elapsed=$((elapsed + 1))
        done
        if [ -f /tmp/runtime.status ]; then
            SMOKE_STATUS="$(cat /tmp/runtime.status)"
        else
            SMOKE_STATUS=124
            kill "$SMOKE_PID" 2>/dev/null
            echo "nvdla-kmd-smoke timed out after ${{smoke_timeout}}s" \
                >>/tmp/runtime.stderr.log
        fi
    else
        echo "module_status=$MODULE_STATUS dri_status=$DRI_STATUS node=$NODE" \
            >/tmp/runtime.stdout.log
        : >/tmp/runtime.stderr.log
        SMOKE_STATUS=98
    fi
    echo "__NVDLA_STATUS_smoke_run_${{i}}=$SMOKE_STATUS"
    if [ "$SMOKE_STATUS" -ne 0 ]; then
        break
    fi
    i=$((i + 1))
done

cat_section smoke_stdout /tmp/runtime.stdout.log
cat_section smoke_stderr /tmp/runtime.stderr.log
echo "__NVDLA_STATUS_smoke=$SMOKE_STATUS"

dmesg 2>&1 | tail -n 200 >/tmp/dmesg.log
cat_section dmesg /tmp/dmesg.log

echo "__NVDLA_RESULT__ module=$MODULE_STATUS dri=$DRI_STATUS smoke=$SMOKE_STATUS repeat=$repeat"
echo "__NVDLA_SMOKE_END__"

if [ "$MODULE_STATUS" -eq 0 ] && [ "$DRI_STATUS" -eq 0 ] && [ "$SMOKE_STATUS" -eq 0 ]; then
    exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    script.chmod(0o755)

    return {
        "path": str(payload),
        "mode": mode,
        "module": str(payload / "opendla.ko"),
        "script": str(script),
    }


def _write_modern_lua(paths: dict[str, Path | None], out_dir: Path) -> Path:
    kernel = paths["kernel"]
    rootfs = paths["rootfs"]
    dtb = paths["dtb"]
    assert isinstance(kernel, Path)
    assert isinstance(rootfs, Path)

    dtb_arg = ""
    if isinstance(dtb, Path):
        dtb_arg = f" -dtb /vp-dtb/{dtb.name}"

    extra_arguments = (
        f"-machine virt -cpu cortex-a57 -machine type=virt -nographic -smp 1 -m 1024 "
        f"-kernel /vp-kernel/{kernel.name}{dtb_arg} "
        "--append \"root=/dev/vda\" "
        f"-drive file=/vp-rootfs/{rootfs.name},if=none,format=raw,id=hd0,snapshot=on "
        "-device virtio-blk-device,drive=hd0 "
        "-fsdev local,id=r,path=/payload,security_model=none "
        "-device virtio-9p-device,fsdev=r,mount_tag=r "
        "-fsdev local,id=w,path=/vp-run,security_model=none "
        "-device virtio-9p-device,fsdev=w,mount_tag=w "
        "-netdev user,id=user0,hostfwd=tcp::6666-:6666,hostfwd=tcp::6667-:22 "
        "-device virtio-net-device,netdev=user0"
    )
    lua = out_dir / "modern-vp.lua"
    lua.write_text(
        f"""CPU = {{
    library = "libqbox-nvdla.so",
    extra_arguments = {extra_arguments!r}
}}

ram = {{
    size = 1048576,
    target_port = {{
        base_addr = 0x40000000,
        high_addr = 0x7fffffff
    }}
}}

nvdla = {{
    irq_number = 176,
    csb_port = {{
        base_addr = 0x10200000,
        high_addr = 0x1021ffff
    }}
}}
""",
        encoding="utf-8",
    )
    return lua


def _docker_mount(path: Path, target: str, readonly: bool = False) -> str:
    suffix = ":ro" if readonly else ""
    return f"{path.resolve()}:{target}{suffix}"


def _reader_thread(proc: subprocess.Popen[bytes], chunks: "queue.Queue[str]") -> None:
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(1)
        if chunk == b"":
            break
        chunks.put(chunk.decode("utf-8", errors="replace"))


def _drain_until(
    proc: subprocess.Popen[bytes],
    chunks: "queue.Queue[str]",
    output: list[str],
    timeout: int,
    predicate: Any | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            output.append(chunks.get(timeout=0.1))
        except queue.Empty:
            pass
        text = "".join(output)
        if predicate and predicate(text):
            return True
        if proc.poll() is not None and chunks.empty():
            return bool(predicate and predicate(text))
    return False


def _run_modern_serial(command: list[str], timeout: int, out_dir: Path) -> dict[str, Any]:
    serial: list[str] = []
    chunks: "queue.Queue[str]" = queue.Queue()
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    reader = threading.Thread(target=_reader_thread, args=(proc, chunks), daemon=True)
    reader.start()

    def has_login_shell_or_script_exit(text: str) -> bool:
        return "login:" in text or text.rstrip().endswith("#") or "__NVDLA_SCRIPT_EXIT__=" in text

    _drain_until(proc, chunks, serial, min(90, timeout), has_login_shell_or_script_exit)
    initial_text = "".join(serial)
    login_seen = "login:" in initial_text or initial_text.rstrip().endswith("#")
    if login_seen and proc.stdin:
        text = "".join(serial)
        if "login:" in text:
            proc.stdin.write(b"root\r")
            proc.stdin.flush()
            _drain_until(proc, chunks, serial, 20, lambda value: value.rstrip().endswith("#"))
        proc.stdin.write(
            b"mkdir -p /mnt/r; "
            b"mount -t 9p -o trans=virtio,version=9p2000.L r /mnt/r || "
            b"mount -t 9p -o trans=virtio r /mnt/r; "
            b"sh /mnt/r/run-modern-smoke.sh; "
            b"echo __NVDLA_SCRIPT_EXIT__=$?; "
            b"poweroff -f\r"
        )
        proc.stdin.flush()

    completed = _drain_until(proc, chunks, serial, timeout, lambda value: "__NVDLA_SCRIPT_EXIT__=" in value)
    if completed:
        _drain_until(proc, chunks, serial, 20)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    reader.join(timeout=5)
    while not chunks.empty():
        serial.append(chunks.get())
    _drain_until(proc, chunks, serial, 2)

    log = "".join(serial)
    completed = completed or "__NVDLA_SCRIPT_EXIT__=" in log
    autorun_seen = "__NVDLA_AUTORUN_BEGIN__" in log
    _write_text(out_dir / "serial.log", log)
    return {
        "returncode": proc.returncode,
        "login_seen": login_seen,
        "autorun_seen": autorun_seen,
        "userspace_seen": login_seen or autorun_seen or completed,
        "script_completed": completed,
        "serial_log": "serial.log",
    }


def _extract_section(log: str, name: str) -> str:
    text = log.replace("\r\n", "\n")
    match = re.search(
        rf"__NVDLA_SECTION_{re.escape(name)}_BEGIN__\n?(.*?)\n?__NVDLA_SECTION_{re.escape(name)}_END__",
        text,
        flags=re.DOTALL,
    )
    return match.group(1).strip() + "\n" if match else ""


def _extract_status(log: str, name: str) -> int | None:
    match = re.search(rf"__NVDLA_STATUS_{re.escape(name)}=(\d+)", log)
    return int(match.group(1)) if match else None


def _extract_script_exit(log: str) -> int | None:
    match = re.search(r"__NVDLA_SCRIPT_EXIT__=(\d+)", log)
    return int(match.group(1)) if match else None


def _extract_render_node(log: str) -> str | None:
    match = re.search(r"__NVDLA_RENDER_NODE__=([^\r\n]*)", log)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _write_modern_logs(out_dir: Path) -> dict[str, str]:
    serial = (out_dir / "serial.log").read_text(encoding="utf-8", errors="replace")
    outputs = {
        "module_vermagic": ("module-vermagic.txt", "module_vermagic"),
        "module_load": ("module-load.log", "module_load"),
        "dev_dri": ("dev-dri.txt", "dev_dri"),
        "runtime_stdout": ("runtime.stdout.log", "smoke_stdout"),
        "runtime_stderr": ("runtime.stderr.log", "smoke_stderr"),
        "runtime_server": ("runtime-server.log", "runtime_server"),
        "runtime_client": ("runtime-client.log", "runtime_client"),
        "runtime_compare": ("runtime-compare.log", "runtime_compare"),
        "dmesg": ("dmesg.log", "dmesg"),
    }
    written = {}
    for key, (filename, section) in outputs.items():
        text = _extract_section(serial, section)
        _write_text(out_dir / filename, text)
        written[key] = filename
    return written


def _run_modern_vp(
    lock: dict[str, Any],
    timeout: int,
    out_dir: Path,
    work_dir: Path | None,
    sources_dir: Path | None,
    docker_image: str | None,
    repeat: int,
    mode: str,
    workload_name: str,
) -> dict[str, Any]:
    paths = _modern_paths(work_dir, sources_dir)
    required_missing = _check_required_paths(paths, ["kernel", "rootfs", "module"])
    smoke_build: dict[str, Any] | None = None
    workload: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    lua: Path | None = None

    if mode == "smoke" and not SMOKE_SOURCE.exists():
        required_missing.append(f"smoke_source: {SMOKE_SOURCE}")
    if mode == "runtime":
        required_missing.extend(_check_required_paths(paths, ["runtime_binary", "runtime_library"]))
        if not RUNTIME_CLIENT.is_file():
            required_missing.append(f"runtime_client: {RUNTIME_CLIENT}")
        try:
            workload = _resolve_workload(paths, workload_name)
        except FileNotFoundError as exc:
            required_missing.append(str(exc))

    if required_missing:
        _write_text(
            out_dir / "modern-lane.blocked.txt",
            "Missing required modern VP artifacts:\n" + "\n".join(required_missing) + "\n",
        )
        return {
            "status": "blocked",
            "reason": "missing required modern VP artifacts",
            "missing": required_missing,
            "paths": {name: str(path) if path else None for name, path in paths.items()},
            "mode": mode,
        }

    if mode == "smoke":
        smoke_build = _build_smoke_binary(paths, out_dir)
        if smoke_build["status"] != "pass":
            return {
                "status": smoke_build["status"],
                "reason": smoke_build["reason"],
                "paths": {name: str(path) if path else None for name, path in paths.items()},
                "smoke_build": smoke_build,
                "mode": mode,
            }

    payload = _write_payload(paths, out_dir, repeat, mode, workload)
    lua = _write_modern_lua(paths, out_dir)

    kernel = paths["kernel"]
    rootfs = paths["rootfs"]
    dtb = paths["dtb"]
    assert isinstance(kernel, Path)
    assert isinstance(rootfs, Path)
    image = docker_image or lock["docker"]["vp_latest"]["image"]

    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "-e",
        "SC_SIGNAL_WRITE_CHECK=DISABLE",
        "-v",
        _docker_mount(out_dir, "/vp-run"),
        "-v",
        _docker_mount(kernel.parent, "/vp-kernel", readonly=True),
        "-v",
        _docker_mount(rootfs.parent, "/vp-rootfs", readonly=True),
        "-v",
        _docker_mount(out_dir / "payload", "/payload", readonly=True),
    ]
    if isinstance(dtb, Path):
        command.extend(["-v", _docker_mount(dtb.parent, "/vp-dtb", readonly=True)])
    command.extend(
        [
            "-w",
            "/vp-run",
            image,
            "bash",
            "-lc",
            "cd /vp-run && aarch64_toplevel -c /vp-run/modern-vp.lua",
        ]
    )

    try:
        run = _run_modern_serial(command, timeout, out_dir)
    except FileNotFoundError as exc:
        return {
            "status": "blocked",
            "reason": f"docker command not available: {exc}",
            "paths": {name: str(path) if path else None for name, path in paths.items()},
            "smoke_build": smoke_build,
            "payload": payload,
            "lua": str(lua),
            "mode": mode,
        }

    logs = _write_modern_logs(out_dir)
    serial = (out_dir / "serial.log").read_text(encoding="utf-8", errors="replace")
    dmesg = (out_dir / "dmesg.log").read_text(encoding="utf-8", errors="replace")
    bad = sorted(set(_bad_patterns(serial) + _bad_patterns(dmesg)))
    statuses = {
        "module_vermagic": _extract_status(serial, "module_vermagic"),
        "module_load": _extract_status(serial, "module_load"),
        "dev_dri": _extract_status(serial, "dev_dri"),
        "smoke": _extract_status(serial, "smoke"),
        "runtime_server_ready": _extract_status(serial, "runtime_server_ready"),
        "runtime_client": _extract_status(serial, "runtime_client"),
        "runtime_compare": _extract_status(serial, "runtime_compare"),
        "runtime": _extract_status(serial, "runtime"),
        "writable_mount": _extract_status(serial, "writable_mount"),
        "script_exit": _extract_script_exit(serial),
    }
    render_node = _extract_render_node(serial)
    runtime_output = out_dir / "runtime-output" / "o_000000.dimg"
    host_compare: dict[str, Any] | None = None
    workload_records: list[dict[str, Any]] = []
    if mode == "runtime" and workload is not None:
        host_compare = compare_exact_files(workload["golden"], runtime_output)
        write_json(out_dir / "runtime-output-compare.json", host_compare)
        workload_records.append(
            {
                "name": workload["name"],
                "manifest": str(workload["manifest_path"]),
                "loadable_sha256": sha256_file(workload["loadable"]),
                "golden_sha256": sha256_file(workload["golden"]),
                "output_path": str(runtime_output),
                "output_sha256": _path_hash(runtime_output),
                "tolerance": workload["manifest"].get("tolerance", {"type": "exact"}),
                "repeat": max(1, repeat),
                "compare": host_compare,
                "status": host_compare["status"],
            }
        )

    if mode == "runtime":
        pass_conditions = [
            run["userspace_seen"],
            run["script_completed"],
            statuses["module_load"] == 0,
            statuses["dev_dri"] == 0,
            statuses["runtime_server_ready"] == 0,
            statuses["runtime_client"] == 0,
            statuses["runtime_compare"] == 0,
            statuses["runtime"] == 0,
            statuses["script_exit"] == 0,
            host_compare is not None and host_compare["status"] == "pass",
            not bad,
        ]
    else:
        pass_conditions = [
            run["userspace_seen"],
            run["script_completed"],
            statuses["module_load"] == 0,
            statuses["dev_dri"] == 0,
            statuses["smoke"] == 0,
            statuses["script_exit"] == 0,
            not bad,
        ]
    status = "pass" if all(pass_conditions) else "fail"
    reason = None
    if status != "pass":
        reason = f"modern VP {mode} did not satisfy all pass criteria"

    return {
        "status": status,
        "reason": reason,
        "mode": mode,
        "paths": {name: str(path) if path else None for name, path in paths.items()},
        "artifact_hashes": {
            "kernel": _path_hash(kernel),
            "rootfs": _path_hash(rootfs),
            "module": _path_hash(paths["module"]),
            "dtb": _path_hash(dtb if isinstance(dtb, Path) else None),
            "smoke": smoke_build.get("sha256") if smoke_build else None,
            "runtime_binary": _path_hash(paths["runtime_binary"]),
            "runtime_library": _path_hash(paths["runtime_library"]),
            "runtime_client": _path_hash(RUNTIME_CLIENT),
            "loadable": _path_hash(workload["loadable"]) if workload else None,
            "golden": _path_hash(workload["golden"]) if workload else None,
            "output": _path_hash(runtime_output),
        },
        "runtime": {
            "binary_sha256": _path_hash(paths["runtime_binary"]),
            "library_sha256": _path_hash(paths["runtime_library"]),
            "server_log": "runtime-server.log",
            "client_log": "runtime-client.log",
            "compare_log": "runtime-compare.log",
            "host_compare": "runtime-output-compare.json" if host_compare else None,
        },
        "docker": {
            "image": image,
            "locked": lock["docker"].get("vp_latest", {}),
            "command": command,
        },
        "run": run,
        "statuses": statuses,
        "render_node": render_node,
        "bad_patterns": bad,
        "logs": logs,
        "smoke_build": smoke_build,
        "payload": payload,
        "lua": str(lua),
        "repeat": max(1, repeat),
        "workloads": workload_records,
    }


def run_vp_test(
    lane: str,
    lock_path: Path,
    timeout: int,
    out_dir: Path | None,
    work_dir: Path | None = None,
    sources_dir: Path | None = None,
    docker_image: str | None = None,
    repeat: int = 1,
    mode: str = "smoke",
    workload: str = "sdp_regression_small",
) -> int:
    lock = read_json(lock_path)
    run_id = utc_run_id(f"vp-{lane}" if lane == "reference" else f"vp-{lane}-{mode}")
    out = out_dir or Path("artifacts") / run_id
    out.mkdir(parents=True, exist_ok=True)

    if lane == "reference":
        boot = _stock_vp_boot(lock, timeout, out)
        compiler = _compiler_smoke(lock, out)
        status = "pass" if boot["status"] == "pass" and compiler["status"] == "pass" else "fail"
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "lane": "vp-reference",
            "status": status,
            "boot": boot,
            "compiler": compiler,
            "sources": {"nvdla_sw": lock["sources"]["nvdla_sw"]["commit"]},
            "patch_series": patch_series_fingerprint(),
            "docker": lock["docker"]["vp_latest"],
            "workloads": [],
        }
    else:
        modern = _run_modern_vp(lock, timeout, out, work_dir, sources_dir, docker_image, repeat, mode, workload)
        paths = modern.get("paths", {})
        patched = Path(paths["patched_nvdla_sw"]) if paths.get("patched_nvdla_sw") else None
        linux = Path(paths["linux"]) if paths.get("linux") else None
        buildroot = Path(paths["buildroot"]) if paths.get("buildroot") else None
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "lane": "vp-modern",
            "status": modern["status"],
            "reason": modern.get("reason"),
            "modern": modern,
            "sources": {
                "nvdla_sw": lock["sources"]["nvdla_sw"]["commit"],
                "nvdla_sw_patched": _git_sha(patched) if patched else None,
                "linux_xlnx": _git_sha(linux) if linux else lock["sources"]["linux_xlnx"]["commit"],
                "buildroot": _git_sha(buildroot) if buildroot else lock["sources"]["buildroot"]["commit"],
            },
            "patch_series": patch_series_fingerprint(),
            "workloads": modern.get("workloads", []),
        }

    write_json(out / "manifest.json", manifest)
    print(f"VP {lane} status: {manifest['status']}")
    print(f"Artifacts: {out}")
    return 0 if manifest["status"] == "pass" else 1
