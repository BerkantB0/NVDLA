from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from nvdla_test_framework import common


class DockerBackendTests(unittest.TestCase):
    @patch.object(common, "is_wsl", return_value=True)
    @patch.object(common, "run_command")
    def test_uses_docker_exe_when_wsl_shim_is_unavailable(
        self, run_command, _is_wsl
    ) -> None:
        run_command.side_effect = [
            subprocess.CompletedProcess(["docker"], 1, "unavailable"),
            subprocess.CompletedProcess(["docker.exe"], 0, "sha256:1234\n"),
        ]

        prefix, backend, image_id = common.docker_backend("nvdla/vp:latest")

        self.assertEqual(prefix, ["docker.exe"])
        self.assertEqual(backend, "windows-docker-from-wsl")
        self.assertEqual(image_id, "sha256:1234")


if __name__ == "__main__":
    unittest.main()
