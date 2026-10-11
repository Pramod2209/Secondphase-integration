"""Parser worker processes must not outlive a force-killed server (no orphaned processes)."""
from tests import helpers  # noqa: F401

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from ats.orchestrator import _get_pool, warm_pool
from ats.worker import ping
warm_pool(2)
pids = {f.result() for f in [_get_pool(2).submit(ping) for _ in range(8)]}
print(",".join(map(str, sorted(pids))), flush=True)
os._exit(0)  # hard exit: atexit pool shutdown does NOT run (like Stop-Process -Force)
"""


class OrphanWorkerTests(unittest.TestCase):
    def test_regression_workers_exit_when_parent_is_killed(self):
        # Workers inherit the child's stdout, so never wait for EOF: read only the pid line.
        proc = subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            line = proc.stdout.readline().strip()
            proc.wait(timeout=120)
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
        self.assertTrue(line and line[0].isdigit(), f"child did not report worker pids: {line!r}")
        pids = [int(p) for p in line.split(",")]
        deadline = time.time() + 20
        while time.time() < deadline and any(psutil.pid_exists(p) for p in pids):
            time.sleep(0.5)
        alive = [p for p in pids if psutil.pid_exists(p)]
        for p in alive:  # never leave stragglers behind, even if the test fails
            try:
                psutil.Process(p).kill()
            except psutil.Error:
                pass
        self.assertEqual(alive, [], "worker processes outlived their parent")


if __name__ == "__main__":
    unittest.main()
