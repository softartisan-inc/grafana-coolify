"""Shared helpers for the unittest suite (python3 -m unittest discover -s tests -v)."""

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = Path(os.environ.get("GC_BIN_DIR", str(ROOT / ".bin")))


def load_env_file(path):
    """Parse a KEY=VALUE file (comments and blank lines ignored, no quoting)."""
    values = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def versions():
    return load_env_file(ROOT / "tools" / "versions.env")


def binary(name):
    """Absolute path of a binary installed by tools/fetch-binaries.sh."""
    path = BIN / name
    if not path.exists():
        raise AssertionError(f"{path} missing: run tools/fetch-binaries.sh first")
    return path


def run(cmd, env=None, cwd=None, timeout=120):
    """Run a command and return CompletedProcess with text stdout+stderr merged."""
    return subprocess.run(
        [str(c) for c in cmd],
        env=env,
        cwd=cwd or ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def require_harness(test_case):
    """Skip unless GC_HARNESS=1: the test needs the native harness (sudo, loopback IPs)."""
    if os.environ.get("GC_HARNESS") != "1":
        raise unittest.SkipTest("needs the native harness: set GC_HARNESS=1")
