from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .common import read_json, repo_root, run_command, sha256_file, utc_run_id, write_json
from .vp import _bad_patterns
from .workloads import compare_exact_files


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


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


def _send(proc: subprocess.Popen[bytes], command: str) -> None:
    if not proc.stdin:
        return
    proc.stdin.write(command.encode("utf-8") + b"\r")
    proc.stdin.flush()


def _extract_section(log: str, name: str) -> str:
    match = re.search(
        rf"__NVDLA_SECTION_{re.escape(name)}_BEGIN__\n?(.*?)\n?__NVDLA_SECTION_{re.escape(name)}_END__",
        log.replace("\r\n", "\n"),
        flags=re.DOTALL,
    )
    return match.group(1).strip() + "\n" if match else ""


def _extract_status(log: str, name: str) -> int | None:
    match = re.search(rf"__NVDLA_STATUS_{re.escape(name)}=(\d+)", log)
    return int(match.group(1)) if match else None


def _dimg_payload_summary(path: Path, header_bytes: int = 40) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    data = path.read_bytes()
    payload = data[min(header_bytes, len(data)) :]
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": len(data),
        "header_bytes": header_bytes,
        "nonzero_bytes": sum(1 for item in data if item),
        "payload_nonzero_bytes": sum(1 for item in payload if item),
        "payload_is_all_zero": all(item == 0 for item in payload),
    }


def _workload_paths(workloads_dir: Path, name: str) -> dict[str, Path | dict[str, Any]]:
    workload_dir = workloads_dir / name
    manifest_path = workload_dir / "generated-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing workload manifest: {manifest_path}; run make workloads")
    manifest = read_json(manifest_path)
    loadable = workload_dir / manifest["loadable"]["path"]
    golden = workload_dir / manifest["golden_outputs"][0]["path"]
    if not loadable.is_file():
        raise FileNotFoundError(f"missing workload loadable: {loadable}")
    if not golden.is_file():
        raise FileNotFoundError(f"missing workload golden: {golden}")
    return {
        "dir": workload_dir,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "loadable": loadable,
        "golden": golden,
    }


def _write_ssh_client_control(out_dir: Path, host_port: int, workload_loadable: Path, timeout: int) -> Path:
    script = out_dir / "stock-ssh-client-control.py"
    root = repo_root().resolve()
    loadable_host = workload_loadable if workload_loadable.is_absolute() else root / workload_loadable
    try:
        loadable_rel = loadable_host.resolve().relative_to(root)
        loadable_in_container = Path("/repo") / loadable_rel
    except ValueError:
        loadable_in_container = workload_loadable
    script.write_text(
        f"""#!/usr/bin/env python
from __future__ import print_function

import os
import subprocess
import sys
import time

import pexpect


out_dir = "/vp-run"
runtime_output = os.path.join(out_dir, "runtime-output")
runtime_results = os.path.join(runtime_output, "results")
server_log = os.path.join(out_dir, "runtime-server.log")
client_log = os.path.join(out_dir, "runtime-client.log")
if not os.path.isdir(runtime_output):
    os.makedirs(runtime_output)
if not os.path.isdir(runtime_results):
    os.makedirs(runtime_results)


def append(path, data):
    with open(path, "ab") as f:
        if isinstance(data, unicode):
            data = data.encode("utf-8", "replace")
        f.write(data)


ssh_cmd = (
    "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
    "-o LogLevel=ERROR root@127.0.0.1 -p 6667 "
    + {shlex.quote('export LD_LIBRARY_PATH=/mnt && /mnt/nvdla_runtime -s')!r}
)
server = pexpect.spawn(ssh_cmd, timeout=30, ignore_sighup=False)
try:
    while True:
        idx = server.expect([
            "Are you sure you want to continue connecting",
            "password:",
            "Ready for Client Connection",
            pexpect.EOF,
            pexpect.TIMEOUT,
        ])
        append(server_log, server.before)
        if idx == 0:
            server.sendline("yes")
        elif idx == 1:
            server.sendline("nvdla")
        elif idx == 2:
            append(server_log, server.after + "\\n")
            break
        else:
            append(server_log, "\\nserver did not become ready\\n")
            sys.exit(2)

    client_cmd = [
        "python",
        "/repo/.work/nvdla-sw-patched/regression/scripts/dla_client.py",
        "-i",
        {str(loadable_in_container)!r},
        "-o",
        runtime_output,
        "-p",
        {str(host_port)!r},
        "-d",
    ]
    append(client_log, " ".join(client_cmd) + "\\n")
    client = subprocess.Popen(client_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    client_deadline = time.time() + {max(30, timeout - 20)!r}
    while client.poll() is None:
        try:
            chunk = server.read_nonblocking(size=4096, timeout=0.1)
            if chunk:
                append(server_log, chunk)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            break
        if time.time() > client_deadline:
            append(client_log, "\\nclient timed out waiting for upstream dla_client.py\\n")
            try:
                client.kill()
            except Exception:
                pass
            stdout, _ = client.communicate()
            if stdout:
                append(client_log, stdout)
            sys.exit(124)
        time.sleep(0.1)

    stdout, _ = client.communicate()
    if stdout:
        append(client_log, stdout)

    for _ in range(20):
        try:
            chunk = server.read_nonblocking(size=4096, timeout=0.1)
            if chunk:
                append(server_log, chunk)
        except pexpect.TIMEOUT:
            break
        except pexpect.EOF:
            break

    sys.exit(client.returncode)
finally:
    try:
        server.close(force=True)
    except Exception:
        pass
""",
        encoding="utf-8",
    )
    return script


def run_stock_sdp_control(
    lock_path: Path,
    artifacts: Path,
    workloads_dir: Path,
    timeout: int,
    host_port: int,
    workload_name: str = "sdp_regression_full",
) -> int:
    lock = read_json(lock_path)
    image = lock["docker"]["vp_latest"]["image"]
    workload = _workload_paths(workloads_dir, workload_name)
    run_id = utc_run_id("vp-stock-sdp-full")
    out_dir = artifacts / run_id
    output_dir = out_dir / "runtime-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    lua = out_dir / "stock-control.lua"
    ssh_client_control = _write_ssh_client_control(out_dir, host_port, workload["loadable"], timeout)  # type: ignore[arg-type]
    lua.write_text(
        f"""CPU = {{
    library = "libqbox-nvdla.so",
    extra_arguments = '-machine virt -cpu cortex-a57 -machine type=virt -nographic -smp 1 -m 1024 -kernel /usr/local/nvdla/Image --append "root=/dev/vda" -drive file=/usr/local/nvdla/rootfs.ext4,if=none,format=raw,id=hd0 -device virtio-blk-device,drive=hd0 -fsdev local,id=r,path=/usr/local/nvdla,security_model=none -device virtio-9p-device,fsdev=r,mount_tag=r -netdev user,id=user0,hostfwd=tcp::{host_port}-:6666,hostfwd=tcp::6667-:22 -device virtio-net-device,netdev=user0'
}}

ram = {{
    size = 1048576,
    target_port = {{
        base_addr = 0xc0000000,
        high_addr = 0xffffffff
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

    docker_id = run_command(["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=30)
    docker_image_id = docker_id.stdout.strip() if docker_id.returncode == 0 else None

    container_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", run_id)
    root = repo_root()
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        container_name,
        "-e",
        "SC_SIGNAL_WRITE_CHECK=DISABLE",
        "-v",
        f"{out_dir.resolve()}:/vp-run",
        "-v",
        f"{root.resolve()}:/repo:ro",
        image,
        "bash",
        "-lc",
        "cd /usr/local/nvdla && aarch64_toplevel -c /vp-run/stock-control.lua",
    ]

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
    client_status = 125
    client_stdout = ""
    login_seen = _drain_until(proc, chunks, serial, min(120, timeout), lambda text: "login:" in text)
    shell_seen = False
    if login_seen:
        _send(proc, "root")
        shell_seen = _drain_until(proc, chunks, serial, 20, lambda text: text.rstrip().endswith("#") or "Password:" in text)
        if "Password:" in "".join(serial) and not "".join(serial).rstrip().endswith("#"):
            _send(proc, "nvdla")
            shell_seen = _drain_until(proc, chunks, serial, 20, lambda text: text.rstrip().endswith("#"))

    if shell_seen:
        _send(
            proc,
            "mkdir -p /mnt; "
            "mount -t 9p -o trans=virtio r /mnt; "
            "echo __NVDLA_SECTION_module_load_BEGIN__; "
            "insmod /mnt/drm.ko; echo __NVDLA_STATUS_drm_load=$?; "
            "insmod /mnt/opendla_1.ko; echo __NVDLA_STATUS_module_load=$?; "
            "echo __NVDLA_SECTION_module_load_END__; "
            "echo __NVDLA_SECTION_dev_dri_BEGIN__; ls -l /dev/dri; echo __NVDLA_SECTION_dev_dri_END__; "
            "if [ -f /etc/ssh/sshd_config ]; then "
            "sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config; "
            "sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config; "
            "grep -q '^PermitRootLogin yes' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config; "
            "grep -q '^PasswordAuthentication yes' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config; "
            "fi; "
            "mkdir -p /var/run/sshd; "
            "if [ -x /etc/init.d/S50sshd ]; then /etc/init.d/S50sshd restart; "
            "elif [ -x /etc/init.d/sshd ]; then /etc/init.d/sshd restart; "
            "elif command -v sshd >/dev/null 2>&1; then sshd; "
            "else false; fi; "
            "echo __NVDLA_STATUS_sshd_start=$?",
        )
        _drain_until(proc, chunks, serial, 60, lambda text: "__NVDLA_STATUS_sshd_start=" in text)
        client_cmd = [
            "docker",
            "exec",
            container_name,
            "bash",
            "-lc",
            "python /vp-run/stock-ssh-client-control.py",
        ]
        try:
            client = run_command(client_cmd, timeout=timeout + 30)
            client_status = client.returncode
            client_stdout = client.stdout
        except subprocess.TimeoutExpired as exc:
            client_status = 124
            timeout_output = exc.stdout or exc.output or ""
            if isinstance(timeout_output, bytes):
                timeout_output = timeout_output.decode("utf-8", errors="replace")
            client_stdout = (
                f"docker exec stock SSH client control timed out after {timeout + 30} seconds\n"
                + str(timeout_output)
            )
        client_log = output_dir / "DLAClientLogger.log"
        if client_log.is_file():
            client_stdout += "\n--- DLAClientLogger.log ---\n"
            client_stdout += client_log.read_text(encoding="utf-8", errors="replace")
        runtime_client_log = out_dir / "runtime-client.log"
        if client_stdout or not runtime_client_log.is_file():
            existing = runtime_client_log.read_text(encoding="utf-8", errors="replace") if runtime_client_log.is_file() else ""
            _write_text(runtime_client_log, existing + client_stdout)
        _send(
            proc,
            "echo __NVDLA_SECTION_runtime_server_BEGIN__; "
            "if [ -f /tmp/runtime-server.log ]; then cat /tmp/runtime-server.log; fi; "
            "echo __NVDLA_SECTION_runtime_server_END__; "
            "echo __NVDLA_SECTION_dmesg_BEGIN__; dmesg | tail -n 200; echo __NVDLA_SECTION_dmesg_END__; "
            "echo __NVDLA_SCRIPT_EXIT__=0; poweroff -f",
        )
        _drain_until(proc, chunks, serial, 45, lambda text: "__NVDLA_SCRIPT_EXIT__=0" in text)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    reader.join(timeout=5)
    while not chunks.empty():
        serial.append(chunks.get())

    serial_text = "".join(serial)
    _write_text(out_dir / "serial.log", serial_text)
    logs = {
        "module_load": "module-load.log",
        "dev_dri": "dev-dri.txt",
        "runtime_server": "runtime-server.log",
        "runtime_server_serial": "runtime-server-serial.log",
        "dmesg": "dmesg.log",
        "runtime_client": "runtime-client.log",
    }
    _write_text(out_dir / logs["module_load"], _extract_section(serial_text, "module_load"))
    _write_text(out_dir / logs["dev_dri"], _extract_section(serial_text, "dev_dri"))
    runtime_server_section = _extract_section(serial_text, "runtime_server")
    runtime_server_log = out_dir / logs["runtime_server"]
    if runtime_server_log.is_file():
        _write_text(out_dir / logs["runtime_server_serial"], runtime_server_section)
    elif runtime_server_section.strip():
        _write_text(runtime_server_log, runtime_server_section)
        _write_text(out_dir / logs["runtime_server_serial"], runtime_server_section)
    else:
        _write_text(runtime_server_log, "")
        _write_text(out_dir / logs["runtime_server_serial"], "")
    _write_text(out_dir / logs["dmesg"], _extract_section(serial_text, "dmesg"))

    output = output_dir / "results" / "o_000000.dimg"
    compare = compare_exact_files(workload["golden"], output)  # type: ignore[arg-type]
    write_json(out_dir / "runtime-output-compare.json", compare)
    bad_patterns = sorted(set(_bad_patterns(serial_text) + _bad_patterns(_extract_section(serial_text, "dmesg"))))
    statuses = {
        "drm_load": _extract_status(serial_text, "drm_load"),
        "module_load": _extract_status(serial_text, "module_load"),
        "sshd_start": _extract_status(serial_text, "sshd_start"),
        "client": client_status,
    }
    status = (
        "pass"
        if shell_seen
        and statuses["sshd_start"] == 0
        and client_status == 0
        and compare["status"] == "pass"
        and not bad_patterns
        else "fail"
    )
    output_summary = _dimg_payload_summary(output)
    classification = None
    if status == "pass":
        classification = "pass"
    elif (
        statuses["drm_load"] == 0
        and statuses["module_load"] == 0
        and statuses["sshd_start"] == 0
        and client_status == 0
        and compare.get("status") == "fail"
        and compare.get("reason") == "files differ"
        and output_summary.get("payload_is_all_zero") is True
        and not bad_patterns
    ):
        classification = "runtime_pass_zero_output_golden_mismatch"
    reason = None if status == "pass" else "stock SDP control did not satisfy all pass criteria"
    if classification == "runtime_pass_zero_output_golden_mismatch":
        reason = "stock runtime reported pass and returned a zero-payload output that differs from the selected golden"
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "lane": "vp-stock",
        "mode": "sdp_runtime_control",
        "status": status,
        "classification": classification,
        "reason": reason,
        "docker": {
            "image": image,
            "image_id": docker_image_id,
            "command": command,
            "host_port": host_port,
            "lua": "stock-control.lua",
            "client_location": "docker exec upstream client, server via guest SSH",
            "ssh_client_control": ssh_client_control.name,
        },
        "workload": {
            "name": workload_name,
            "manifest": str(workload["manifest_path"]),
            "loadable_sha256": sha256_file(workload["loadable"]),  # type: ignore[arg-type]
            "golden_sha256": sha256_file(workload["golden"]),  # type: ignore[arg-type]
            "output_sha256": sha256_file(output) if output.is_file() else None,
            "compare": compare,
            "output_summary": output_summary,
        },
        "stock": {
            "kernel": "stock image /usr/local/nvdla/Image",
            "rootfs": "stock image /usr/local/nvdla/rootfs.ext4",
            "kmd": "stock image /usr/local/nvdla/opendla_1.ko",
            "runtime": "stock image /usr/local/nvdla/nvdla_runtime",
        },
        "statuses": statuses,
        "bad_patterns": bad_patterns,
        "logs": logs,
        "serial_log": "serial.log",
        "login_seen": login_seen,
        "shell_seen": shell_seen,
    }
    write_json(out_dir / "manifest.json", manifest)
    print(f"Stock SDP control status: {status}")
    print(f"Artifacts: {out_dir}")
    return 0 if status == "pass" else 1
