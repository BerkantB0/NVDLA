from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "runtime" / "nvdla-flatbuf-client.c"


def _send_frame(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(f"{len(payload)}\n".encode("ascii") + payload)


def _recv_frame(sock: socket.socket) -> bytes:
    length = bytearray()
    while True:
        byte = sock.recv(1)
        if byte == b"\n":
            break
        if not byte:
            raise RuntimeError("client disconnected while reading frame length")
        length.extend(byte)
    remaining = int(length.decode("ascii"))
    data = bytearray()
    while len(data) < remaining:
        chunk = sock.recv(remaining - len(data))
        if not chunk:
            raise RuntimeError("client disconnected while reading frame payload")
        data.extend(chunk)
    return bytes(data)


@unittest.skipUnless(shutil.which("cc"), "host C compiler is required")
class FlatbufClientCTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "nvdla-flatbuf-client"
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(SOURCE), "-o", str(self.binary)],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_internal_framing_self_test(self) -> None:
        result = subprocess.run(
            [str(self.binary), "--self-test"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("framing self-test passed", result.stdout)

    def test_runs_complete_runtime_server_protocol(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        errors: list[BaseException] = []

        def server() -> None:
            try:
                connection, _ = listener.accept()
                with connection:
                    self.assertEqual(_recv_frame(connection), b"GET_WELCOME")
                    _send_frame(connection, b"WELCOME")
                    self.assertEqual(_recv_frame(connection), b"READ_FLATBUF")
                    self.assertEqual(_recv_frame(connection), b"flatbuffer")
                    self.assertEqual(_recv_frame(connection), b"RUN_FLATBUF")
                    _send_frame(connection, b"[OK] Test PASSED!")
                    self.assertEqual(_recv_frame(connection), b"GET_NUMOUTPUTS")
                    _send_frame(connection, b"1")
                    self.assertEqual(_recv_frame(connection), b"GET_OUTPUT")
                    self.assertEqual(_recv_frame(connection), b"0")
                    _send_frame(connection, b"dimg-output")
                    self.assertEqual(_recv_frame(connection), b"SHUTDOWN")
                    _send_frame(connection, b"ACK_SHUTDOWN")
            except BaseException as exc:
                errors.append(exc)
            finally:
                listener.close()

        thread = threading.Thread(target=server)
        thread.start()
        flatbuf = self.root / "loadable.fbuf"
        flatbuf.write_bytes(b"flatbuffer")
        out = self.root / "out"
        result = subprocess.run(
            [
                str(self.binary),
                "--flatbuf",
                str(flatbuf),
                "--out-dir",
                str(out),
                "--port",
                str(port),
                "--timeout",
                "5",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        thread.join(timeout=5)

        self.assertFalse(errors)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((out / "o_000000.dimg").read_bytes(), b"dimg-output")
