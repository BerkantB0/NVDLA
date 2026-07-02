#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import socket
import sys
import time
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6666


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _bytes(payload: bytes | str) -> bytes:
    if isinstance(payload, bytes):
        return payload
    return payload.encode("ascii")


def send_frame(sock: socket.socket, payload: bytes | str) -> None:
    data = _bytes(payload)
    sock.sendall(str(len(data)).encode("ascii") + b"\n")
    sock.sendall(data)


def recv_frame(sock: socket.socket) -> bytes:
    size_bytes = bytearray()
    while True:
        byte = sock.recv(1)
        if not byte:
            raise RuntimeError("socket closed while reading frame size")
        if byte == b"\n":
            break
        size_bytes.extend(byte)
    try:
        size = int(size_bytes.decode("ascii"))
    except ValueError as exc:
        raise RuntimeError(f"invalid frame size: {size_bytes!r}") from exc

    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError("socket closed while reading frame data")
        data.extend(chunk)
    return bytes(data)


def _check_response(command: str, payload: bytes) -> None:
    text = payload.decode("utf-8", errors="replace")
    if "ERR" in text:
        raise RuntimeError(f"{command} failed: {text}")


def run_protocol(
    sock: socket.socket,
    flatbuf: Path,
    out_dir: Path,
    shutdown: bool = True,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    send_frame(sock, "GET_WELCOME")
    welcome = recv_frame(sock)
    _check_response("GET_WELCOME", welcome)
    print(f"welcome={welcome.decode('utf-8', errors='replace')}")

    flatbuf_data = flatbuf.read_bytes()
    send_frame(sock, "READ_FLATBUF")
    send_frame(sock, flatbuf_data)
    print(f"read_flatbuf={flatbuf.name} bytes={len(flatbuf_data)} sha256={sha256_file(flatbuf)}")

    send_frame(sock, "RUN_FLATBUF")
    run_result = recv_frame(sock)
    _check_response("RUN_FLATBUF", run_result)
    run_text = run_result.decode("utf-8", errors="replace")
    print(f"run_result={run_text}")
    if "PASSED" not in run_text:
        raise RuntimeError(f"RUN_FLATBUF did not report PASSED: {run_text}")

    send_frame(sock, "GET_NUMOUTPUTS")
    num_outputs_payload = recv_frame(sock)
    _check_response("GET_NUMOUTPUTS", num_outputs_payload)
    num_outputs = int(num_outputs_payload.decode("ascii").strip())
    print(f"num_outputs={num_outputs}")

    outputs = []
    for index in range(num_outputs):
        send_frame(sock, "GET_OUTPUT")
        send_frame(sock, str(index))
        output = recv_frame(sock)
        path = out_dir / f"o_{index:06d}.dimg"
        path.write_bytes(output)
        outputs.append(path)
        print(f"output[{index}]={path} bytes={len(output)} sha256={sha256_file(path)}")

    if shutdown:
        send_frame(sock, "SHUTDOWN")
        ack = recv_frame(sock)
        _check_response("SHUTDOWN", ack)
        print(f"shutdown={ack.decode('utf-8', errors='replace')}")

    return outputs


def connect_with_retry(
    host: str,
    port: int,
    timeout: float,
    retries: int,
    delay: float,
) -> socket.socket:
    last_error: OSError | None = None
    for attempt in range(1, retries + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
            if attempt < retries:
                time.sleep(delay)
    raise RuntimeError(f"could not connect to {host}:{port}: {last_error}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one NVDLA flatbuffer through nvdla_runtime -s")
    parser.add_argument("--flatbuf", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--timeout", default=120.0, type=float)
    parser.add_argument("--connect-retries", default=30, type=int)
    parser.add_argument("--connect-delay", default=1.0, type=float)
    parser.add_argument("--no-shutdown", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    with connect_with_retry(args.host, args.port, args.timeout, args.connect_retries, args.connect_delay) as sock:
        sock.settimeout(args.timeout)
        run_protocol(sock, args.flatbuf, args.out_dir, shutdown=not args.no_shutdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
