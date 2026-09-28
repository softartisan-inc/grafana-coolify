import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path

from support import ROOT, require_harness, run

sys.path.insert(0, str(ROOT / "scripts"))
import gclib as g  # noqa: E402

STACK = [sys.executable, str(ROOT / "harness" / "stack.py")]
ALLOY_MEM_LIMIT = 768 * 1024 * 1024


def stack(*args, timeout=400):
    result = run([*STACK, *args], timeout=timeout)
    if result.returncode != 0:
        raise AssertionError(f"stack.py {' '.join(args)} failed:\n{result.stdout}")
    return result


def pid_of(name):
    return json.loads((ROOT / ".harness" / "state.json").read_text(encoding="utf-8"))["processes"][name]["pid"]


def anon_rss_bytes(pid):
    """Anonymous resident memory (heap, stacks): what a cgroup limit cannot reclaim.

    VmRSS also counts file pages (the 570 MB alloy binary, the mmapped bbolt queue) that the
    kernel reclaims under a container limit.
    """
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("RssAnon:"):
            return int(line.split()[1]) * 1024
    return 0


class RobustnessTest(unittest.TestCase):
    """Spec 12.5, on the native harness. Methods run in name order; the last one stops the stack."""

    @classmethod
    def setUpClass(cls):
        require_harness(cls)
        run([*STACK, "down"], timeout=120)
        stack("up")
        cls.settings = g.settings()

    @classmethod
    def tearDownClass(cls):
        run([*STACK, "down"], timeout=120)
        # test_3 leaves ~1 GiB in the persistent queue: never replay it into the next runs.
        shutil.rmtree(ROOT / ".harness" / "data" / "alloy-data", ignore_errors=True)

    def send_log(self, service):
        resource = {"project": "robust", "deployment.environment.name": "prod", "service.name": service}
        g.send_otlp("http://alloy:4318", "logs", g.otlp_logs(resource, f"durable {service}"))

    def wait_log(self, service, timeout=180):
        g.wait_for(lambda: g.loki_entries("http://loki:3100", f'{{service_name="{service}"}}'), f"log {service} in Loki", timeout, interval=3)

    def test_1_loki_outage_is_absorbed(self):
        service = f"outage-{g.run_id()}"
        stack("stop", "loki")
        self.send_log(service)
        time.sleep(5)
        stack("start", "loki")
        self.wait_log(service)

    def test_2_alloy_restart_replays_the_persistent_queue(self):
        service = f"replay-{g.run_id()}"
        stack("stop", "loki")
        self.send_log(service)
        time.sleep(5)
        stack("stop", "alloy")
        stack("start", "alloy")
        stack("start", "loki")
        self.wait_log(service)

    def test_3_massive_send_stays_in_the_memory_envelope(self):
        """1 GiB of logs while Loki is down, more than the envelope: the queue must go to disk.

        The harness sets the GOMEMLIMIT that Alloy derives from mem_limit in its container; the
        memory_limiter itself, sized on the total memory of the machine here, only acts in Docker.
        """
        pid = pid_of("alloy")
        peak = [anon_rss_bytes(pid)]
        stop = threading.Event()
        watch_errors = []
        errors = []
        statuses = Counter()
        lock = threading.Lock()

        def watch():
            try:
                while not stop.is_set():
                    peak[0] = max(peak[0], anon_rss_bytes(pid))
                    time.sleep(0.2)
            except Exception as exc:  # re-raised in the test thread
                watch_errors.append(exc)

        def burst(worker):
            # 8 workers x 64 requests x 2048 records x 1 KiB = 1 GiB.
            resource = {"project": "robust", "deployment.environment.name": "prod", "service.name": f"burst-{worker}"}
            record = g.otlp_logs(resource, "x" * 1024)["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
            body = json.dumps({"resourceLogs": [{"resource": {"attributes": g.attrs(resource)}, "scopeLogs": [{"logRecords": [record] * 2048}]}]}).encode()
            for _ in range(64):
                try:
                    status = g.http("POST", "http://alloy:4318/v1/logs", body, timeout=60).status
                except Exception as exc:  # reported by the assertion below
                    with lock:
                        errors.append(f"burst-{worker}: {exc!r}")
                    return
                with lock:
                    statuses[status] += 1

        # Loki stopped during the whole burst: the full 1 GiB backlog stays in alloy's queue,
        # whatever the ingest speed of the host.
        stack("stop", "loki")
        watcher = threading.Thread(target=watch)
        watcher.start()
        workers = [threading.Thread(target=burst, args=(i,)) for i in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        stop.set()
        watcher.join()
        stack("start", "loki")
        print(f"alloy anonymous RSS peak: {peak[0] / 2**20:.0f} MiB (limit 768)", flush=True)
        if watch_errors:
            raise watch_errors[0]
        self.assertEqual(errors, [])
        self.assertEqual(sum(statuses.values()), 8 * 64, dict(statuses))
        unexpected = {code: count for code, count in statuses.items() if not (200 <= code < 300 or code in (429, 503))}
        self.assertEqual(unexpected, {}, dict(statuses))
        self.assertLess(peak[0], ALLOY_MEM_LIMIT, f"alloy anonymous RSS peaked at {peak[0] / 2**20:.0f} MiB")
        self.wait_log("burst-0")
        status = stack("status").stdout
        for name in ("loki", "tempo", "prometheus", "alloy", "alloy-gateway", "node-exporter"):
            self.assertRegex(status, rf"{name}\s+pid=\d+\s+running ready")

    def test_4_empty_prometheus_features_start_normally(self):
        flags = g.get_json("http://prometheus:9090/api/v1/status/flags")["data"]
        self.assertEqual(flags["enable-feature"], "")
        self.assertEqual(g.http("GET", "http://prometheus:9090/-/ready").status, 200)

    def test_5_config_guard_failure_blocks_every_service(self):
        stack("down")
        for breakage, message in (("directory", "is a directory"), ("altered", "differs from source"), ("empty", "empty")):
            with self.subTest(breakage=breakage), tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / "config"
                shutil.copytree(ROOT / "config", config, ignore=shutil.ignore_patterns("__pycache__"))
                target = config / "loki" / "loki.yaml"
                if breakage == "directory":
                    target.unlink()
                    target.mkdir()
                elif breakage == "altered":
                    target.write_text(target.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
                else:
                    target.write_text("", encoding="utf-8")
                result = run([*STACK, "up", "--config-dir", str(config)], timeout=120)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stdout)
                self.assertIn("no service started", result.stdout)
                with socket.socket() as sock:
                    self.assertNotEqual(sock.connect_ex(("127.0.10.2", 3100)), 0, "loki started despite config-guard")
                run([*STACK, "down"], timeout=120)
        # Review focus 2: the public Faro endpoint never runs without a real key. Empty: Compose
        # refuses the ${FARO_API_KEY:?} reference; too short: config-guard refuses it.
        for key, message in (("", "FARO_API_KEY is required"), ("short-key-01234", "FARO_API_KEY must be at least 16")):
            with self.subTest(faro_api_key=key):
                result = run([*STACK, "up", "--set", f"FARO_API_KEY={key}"], timeout=120)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stdout)
                with socket.socket() as sock:
                    self.assertNotEqual(sock.connect_ex(("127.0.10.5", 12347)), 0, "alloy started without a valid Faro key")
                run([*STACK, "down"], timeout=120)


if __name__ == "__main__":
    unittest.main()
