"""Shared helpers for the unittest suite (python3 -m unittest discover -s tests -v)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

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


def harness_env():
    return load_env_file(ROOT / "harness" / "harness.env")


def validator_env(tmpdir):
    """Environment for the official validators: harness values, loopback bind, temp data dirs."""
    env = dict(os.environ)
    env.update(harness_env())
    env.update(
        {
            "BIND_ADDR": "127.0.0.1",
            "LOKI_DATA_DIR": str(Path(tmpdir) / "loki"),
            "TEMPO_DATA_DIR": str(Path(tmpdir) / "tempo"),
            "ALLOY_QUEUE_DIR": str(Path(tmpdir) / "alloy-queue"),
        }
    )
    return env


def promtool_check(exprs):
    """promtool's verdict on [(name, PromQL)], checked as recording rules: (returncode, output)."""
    rules = {"groups": [{"name": "parse", "rules": [{"record": f"gc:parse_{n}", "expr": expr} for n, (_name, expr) in enumerate(exprs)]}]}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rules.yml"
        path.write_text(yaml.safe_dump(rules), encoding="utf-8")
        result = run([binary("promtool"), "check", "rules", path])
    return result.returncode, result.stdout + "\n".join(name for name, _expr in exprs)
