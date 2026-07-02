from __future__ import annotations

import importlib.util
import io
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType


def _load_client() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "runtime" / "nvdla_flatbuf_client.py"
    spec = importlib.util.spec_from_file_location("nvdla_flatbuf_client", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeClientTests(unittest.TestCase):
    def test_run_protocol_writes_output_and_shutdown(self) -> None:
        client = _load_client()
        server_sock, client_sock = socket.socketpair()
        errors: list[BaseException] = []

        def server() -> None:
            try:
                self.assertEqual(client.recv_frame(server_sock), b"GET_WELCOME")
                client.send_frame(server_sock, b"WELCOME")
                self.assertEqual(client.recv_frame(server_sock), b"READ_FLATBUF")
                self.assertEqual(client.recv_frame(server_sock), b"flatbuf")
                self.assertEqual(client.recv_frame(server_sock), b"RUN_FLATBUF")
                client.send_frame(server_sock, b"PASSED")
                self.assertEqual(client.recv_frame(server_sock), b"GET_NUMOUTPUTS")
                client.send_frame(server_sock, b"1")
                self.assertEqual(client.recv_frame(server_sock), b"GET_OUTPUT")
                self.assertEqual(client.recv_frame(server_sock), b"0")
                client.send_frame(server_sock, b"output-dimg")
                self.assertEqual(client.recv_frame(server_sock), b"SHUTDOWN")
                client.send_frame(server_sock, b"ACK_SHUTDOWN")
            except BaseException as exc:
                errors.append(exc)
            finally:
                server_sock.close()

        thread = threading.Thread(target=server)
        thread.start()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            flatbuf = root / "loadable.fbuf"
            flatbuf.write_bytes(b"flatbuf")
            with redirect_stdout(io.StringIO()):
                outputs = client.run_protocol(client_sock, flatbuf, root / "out")
            client_sock.close()
            thread.join(timeout=5)

            self.assertFalse(errors)
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0].read_bytes(), b"output-dimg")


if __name__ == "__main__":
    unittest.main()
