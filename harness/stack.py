#!/usr/bin/env python3
"""Native test bench without Docker (spec 12): run the stack of compose.template.yaml as processes.

Every service runs the official binary (installed by tools/fetch-binaries.sh) with the SAME
command arguments and environment as in the compose, on its own loopback IP. Volume targets are
mapped to local paths (.harness/data/<volume>, .harness/tmpfs/<service>, and .harness/coolify/
for ./config), and "0.0.0.0" is replaced by the service IP. /etc/hosts maps the service names to
those IPs (sudo), so the configs keep their in-stack names (loki:3100, tempo:4317...).

.harness/coolify/ plays the directory where Coolify writes the content: files. Each one is copied
from ./config (or --config-dir) to its content-addressed name (config/loki/loki.<sha8>.yaml), the
name the deployed compose mounts; a directory or a missing file is reproduced as such, so
config-guard sees what it would see after a Coolify regression.

Usage:
  python3 harness/stack.py up [--set KEY=VALUE]... [--config-dir DIR] [--only a,b]
  python3 harness/stack.py down [--purge]
  python3 harness/stack.py status
  python3 harness/stack.py stop SERVICE | start SERVICE
  python3 harness/stack.py oneshot SERVICE [--set KEY=VALUE]...
  python3 harness/stack.py logs SERVICE [-n LINES]
"""

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import render  # noqa: E402

HARNESS = ROOT / ".harness"
STATE = HARNESS / "state.json"
COOLIFY = HARNESS / "coolify"
BIN = Path(os.environ.get("GC_BIN_DIR", str(ROOT / ".bin")))
HOSTS = Path("/etc/hosts")
HOSTS_BEGIN = "# BEGIN grafana-coolify harness"
HOSTS_END = "# END grafana-coolify harness"
SERVICE_IPS = {
    "loki": "127.0.10.2",
    "tempo": "127.0.10.3",
    "prometheus": "127.0.10.4",
    "alloy": "127.0.10.5",
    "alloy-gateway": "127.0.10.6",
    "node-exporter": "127.0.10.7",
    "config-guard": "127.0.10.8",
    "grafana-setup": "127.0.10.9",
}
# Image repository -> binary in .bin/ (the image ENTRYPOINT). Other images run `command:` as is.
BINARIES = {
    "grafana/alloy": "alloy",
    "grafana/loki": "loki",
    "grafana/tempo": "tempo",
    "prom/prometheus": "prometheus",
    "quay.io/prometheus/node-exporter": "node_exporter",
}
# Images whose binary sizes its Go heap from the memory limit of its container (cgroup): Alloy
# (automemlimit) and Prometheus 3 (--auto-gomemlimit) set GOMEMLIMIT to 90% of mem_limit. The
# bench has no cgroup: it sets that GOMEMLIMIT itself, unless `--set GOMEMLIMIT=...` overrides it
# (`off` disables it).
AUTO_GOMEMLIMIT = {"grafana/alloy", "prom/prometheus"}
MEMORY_UNITS = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
READY = {
    "loki": "http://{ip}:3100/ready",
    "tempo": "http://{ip}:3200/ready",
    "prometheus": "http://{ip}:9090/-/ready",
    "alloy": "http://{ip}:12345/-/ready",
    "alloy-gateway": "http://{ip}:12345/-/ready",
    "node-exporter": "http://{ip}:9100/metrics",
}
ONESHOTS = ("config-guard", "grafana-setup")
START_ORDER = ("loki", "tempo", "prometheus", "node-exporter", "alloy", "alloy-gateway")
VAR_RE = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class HarnessError(Exception):
    pass


# ------------------------------------------------------------------ compose model
def template_text():
    return (ROOT / "compose.template.yaml").read_text(encoding="utf-8")


def load_compose():
    versions = render.load_versions(ROOT / "tools" / "versions.env")
    return yaml.safe_load(render.render_text(template_text(), ROOT, versions, strip_content=True))


def content_sources():
    """{content-addressed source: repository source} of every content: volume of the compose."""
    return {item.hashed_source: item.source for item in render.content_items(ROOT, template_text())}


def materialize(source, config_dir):
    """Write what Coolify writes for one content: volume: the config_dir file under its hashed name."""
    repository = content_sources()[source]
    origin = Path(config_dir) / repository[len("./config/") :]
    written = COOLIFY / source[len("./") :]
    if written.is_dir() and not written.is_symlink():
        shutil.rmtree(written)
    elif written.exists() or written.is_symlink():
        written.unlink()
    written.parent.mkdir(parents=True, exist_ok=True)
    if origin.is_dir():
        written.mkdir()
    elif origin.is_file():
        shutil.copyfile(origin, written)
    return written


def extra_ports(name):
    """Internal gRPC listeners declared by the YAML configs of a service (server.grpc_listen_*)."""
    ports = []
    for item in render.content_items(ROOT, template_text()):
        if item.service == name and item.source.endswith((".yaml", ".yml")):
            server = (yaml.safe_load(item.text) or {}).get("server") or {}
            if "grpc_listen_port" in server:
                ports.append((str(server.get("grpc_listen_address", SERVICE_IPS[name])), int(server["grpc_listen_port"])))
    return ports


def load_env_values(sets):
    values = {}
    for raw in (ROOT / "harness" / "harness.env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    # Written by harness/edge.py (e.g. the Grafana service account token of the test Grafana).
    runtime = HARNESS / "runtime.env"
    if runtime.exists():
        for raw in runtime.read_text(encoding="utf-8").splitlines():
            if "=" in raw:
                key, value = raw.split("=", 1)
                values[key] = value
    for item in sets:
        key, _, value = item.partition("=")
        values[key] = value
    return values


def interpolate(value, env):
    """Compose-style interpolation: $$, $VAR, ${VAR}, ${VAR:-default}, ${VAR-default}, ${VAR:?error}."""

    def replace(match):
        if match.group(0) == "$$":
            return "$"
        name = match.group(1) or match.group(4)
        op, arg = match.group(2), match.group(3)
        current = env.get(name)
        if op in (":-", "-"):
            empty = current is None or (op == ":-" and current == "")
            return arg if empty else current
        if op in (":?", "?"):
            if current is None or (op == ":?" and current == ""):
                raise HarnessError(f"required variable {name}: {arg}")
            return current
        return current or ""

    return VAR_RE.sub(replace, str(value))


def memory_bytes(value):
    """Compose memory sizes: 768m, 1536m, 1g, 64m..."""
    text = str(value).strip().lower()
    unit = text[-1] if text[-1] in MEMORY_UNITS else "b"
    return int(float(text.rstrip("bkmg")) * MEMORY_UNITS[unit])


def image_repository(image):
    name, _, tag = image.rpartition(":")
    return name if name and "/" not in tag else image


def mounts(name, service, config_dir):
    """[(container_target, local_path)] for every volume and tmpfs of a service."""
    result = []
    for volume in service.get("volumes", []):
        if isinstance(volume, str):
            source, target = volume.split(":")[:2]
            spec = {"type": "volume" if not source.startswith((".", "/")) else "bind", "source": source, "target": target}
        else:
            spec = volume
        source, target = spec["source"], spec["target"]
        if spec.get("type") == "bind" and source == "./config":
            # config-guard reads the whole directory: write every content file first.
            for hashed in content_sources():
                materialize(hashed, config_dir)
            local = COOLIFY / "config"
        elif spec.get("type") == "bind" and source.startswith("./config/"):
            local = materialize(source, config_dir)
        elif spec.get("type") == "bind":
            local = Path(source)
        else:
            local = HARNESS / "data" / source
            local.mkdir(parents=True, exist_ok=True)
        result.append((target, str(local)))
    for entry in service.get("tmpfs", []) or []:
        target = entry.split(":")[0]
        local = HARNESS / "tmpfs" / name / target.strip("/").replace("/", "_")
        local.mkdir(parents=True, exist_ok=True)
        result.append((target, str(local)))
    return result


def rewriter(mapping, ip):
    """Replace container paths (whole path segments only) and 0.0.0.0 in a command/env value."""
    targets = sorted((t for t, _ in mapping), key=len, reverse=True)
    local = dict(mapping)
    pattern = re.compile(r"(?<![\w/.:-])(" + "|".join(re.escape(t) for t in targets) + r")(?=$|[/;,\s'\"])") if targets else None

    def rewrite(value):
        if pattern:
            value = pattern.sub(lambda m: local[m.group(1)], value)
        return value.replace("0.0.0.0", ip)

    return rewrite


def build(name, service, env_values, config_dir):
    ip = SERVICE_IPS[name]
    rewrite = rewriter(mounts(name, service, config_dir), ip)
    command = service.get("command") or []
    if isinstance(command, str):
        raise HarnessError(f"{name}: use the list form of command:")
    args = [rewrite(interpolate(arg, env_values)) for arg in command]
    repository = image_repository(service["image"])
    if repository in BINARIES:
        binary = BIN / BINARIES[repository]
        if not binary.exists():
            raise HarnessError(f"{binary} missing: run tools/fetch-binaries.sh")
        args = [str(binary), *args]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(HARNESS / "work" / name), "LANG": "C.UTF-8"}
    for key, value in (service.get("environment") or {}).items():
        if value is not None:
            env[key] = rewrite(interpolate(value, env_values))
    if repository in AUTO_GOMEMLIMIT and service.get("mem_limit"):
        env["GOMEMLIMIT"] = env_values.get("GOMEMLIMIT") or str(int(memory_bytes(service["mem_limit"]) * 0.9))
    return {"args": args, "env": env, "ip": ip}


# ------------------------------------------------------------------ host plumbing
def hosts_block():
    lines = [HOSTS_BEGIN] + [f"{ip} {name}" for name, ip in SERVICE_IPS.items()] + [HOSTS_END]
    return "\n".join(lines) + "\n"


def without_block(text):
    return re.sub(rf"(?ms)^{re.escape(HOSTS_BEGIN)}\n.*?^{re.escape(HOSTS_END)}\n?", "", text)


def write_hosts(text):
    subprocess.run(["sudo", "-n", "tee", str(HOSTS)], input=text, text=True, stdout=subprocess.DEVNULL, check=True)


def install_hosts():
    current = HOSTS.read_text(encoding="utf-8")
    wanted = without_block(current).rstrip("\n") + "\n" + hosts_block()
    if wanted != current:
        write_hosts(wanted)


def remove_hosts():
    current = HOSTS.read_text(encoding="utf-8")
    if HOSTS_BEGIN in current:
        write_hosts(without_block(current))


def ports_of(name, service):
    ports = [(SERVICE_IPS[name], int(p)) for p in service.get("expose", []) or []]
    return ports + extra_ports(name)


def preflight(pairs):
    """Fail loudly when an address is taken (also by a 0.0.0.0 listener of another program)."""
    busy = []
    for ip, port in pairs:
        with socket.socket() as sock:
            # Same option as the Go servers: TIME_WAIT leftovers are fine, a listener is not.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((ip, port))
                sock.listen(1)
            except OSError as exc:
                busy.append(f"{ip}:{port} ({exc.strerror})")
    if busy:
        raise HarnessError("addresses already in use: " + ", ".join(busy))


def http_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ------------------------------------------------------------------ processes
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"processes": {}, "sets": [], "config_dir": str(ROOT / "config")}


def save_state(state):
    HARNESS.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def proc_stat(pid):
    """Fields of /proc/<pid>/stat after the command name (field 3 onwards), or None."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().rpartition(")")[2].split()
    except OSError:
        return None


def start_time(pid):
    """Start time of a process (field 22 of /proc/<pid>/stat): with the PID, its identity."""
    fields = proc_stat(pid)
    return fields[19] if fields else None


def alive(entry):
    """True while the state entry still designates the process the harness spawned.

    A PID alone is not enough: after a crash or a reboot, state.json may hold a PID reused by an
    unrelated process. An entry without the start time recorded at spawn is never ours.
    """
    fields = proc_stat(entry["pid"])
    return bool(fields) and entry.get("start") is not None and fields[19] == entry["start"] and fields[0] != "Z"


def log_path(name):
    return HARNESS / "logs" / f"{name}.log"


def tail(name, lines=30):
    path = log_path(name)
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]) if path.exists() else ""


def spawn(name, spec):
    """Start a service in its own session; return its state entry {"pid", "start"}."""
    (HARNESS / "logs").mkdir(parents=True, exist_ok=True)
    workdir = HARNESS / "work" / name
    workdir.mkdir(parents=True, exist_ok=True)
    with open(log_path(name), "ab") as log:
        proc = subprocess.Popen(
            spec["args"], env=spec["env"], cwd=workdir, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True
        )
    return {"pid": proc.pid, "start": start_time(proc.pid)}


def run_oneshot(name, spec, timeout=300):
    (HARNESS / "logs").mkdir(parents=True, exist_ok=True)
    with open(log_path(name), "ab") as log:
        result = subprocess.run(
            spec["args"], env=spec["env"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout, check=False
        )
    return result.returncode


def wait_ready(name, entry, timeout=180):
    url = READY[name].format(ip=SERVICE_IPS[name])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(entry):
            raise HarnessError(f"{name} exited during startup:\n{tail(name)}")
        if http_ok(url):
            return
        time.sleep(1)
    raise HarnessError(f"{name} not ready after {timeout}s ({url}):\n{tail(name)}")


def terminate(entry, grace=15):
    """Stop the process group of a state entry; an entry that is not ours (gone, reused) is left alone."""
    if not alive(entry):
        return
    try:
        os.killpg(entry["pid"], signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and alive(entry):
        time.sleep(0.2)
    if alive(entry):
        try:
            os.killpg(entry["pid"], signal.SIGKILL)
        except OSError:
            pass


# ------------------------------------------------------------------ commands
def cmd_up(args):
    state = load_state()
    if any(alive(p) for p in state["processes"].values()):
        raise HarnessError("harness already running: python3 harness/stack.py down first")
    compose = load_compose()["services"]
    env_values = load_env_values(args.set)
    config_dir = str(Path(args.config_dir).resolve()) if args.config_dir else str(ROOT / "config")
    names = [n for n in START_ORDER if n in compose]
    if args.only:
        names = [n for n in names if n in args.only.split(",")]
    specs = {name: build(name, compose[name], env_values, config_dir) for name in ["config-guard", *names]}
    preflight([pair for name in names for pair in ports_of(name, compose[name])])
    install_hosts()
    state = {"processes": {}, "sets": args.set, "config_dir": config_dir}
    save_state(state)
    code = run_oneshot("config-guard", specs["config-guard"])
    print(f"stack: config-guard exited with {code}")
    if code != 0:
        raise HarnessError(f"config-guard failed, no service started:\n{tail('config-guard')}")
    for name in names:
        state["processes"][name] = {**spawn(name, specs[name]), "ip": SERVICE_IPS[name]}
        save_state(state)
    for name in names:
        wait_ready(name, state["processes"][name])
        print(f"stack: {name} ready on {SERVICE_IPS[name]}")
    return 0


def cmd_down(args):
    state = load_state()
    for name, proc in state["processes"].items():
        terminate(proc)
        print(f"stack: stopped {name}")
    remove_hosts()
    if args.purge and HARNESS.exists():
        shutil.rmtree(HARNESS)
        return 0
    # STATE plus the files harness/edge.py writes for the edge it started (now stopped).
    for path in (STATE, HARNESS / "edge.json", HARNESS / "runtime.env"):
        if path.exists():
            path.unlink()
    return 0


def cmd_stop(args):
    state = load_state()
    proc = state["processes"].get(args.service)
    if not proc:
        raise HarnessError(f"{args.service} is not managed by the harness")
    terminate(proc)
    print(f"stack: stopped {args.service}")
    return 0


def cmd_start(args):
    state = load_state()
    compose = load_compose()["services"]
    spec = build(args.service, compose[args.service], load_env_values(state["sets"] + args.set), state["config_dir"])
    if args.service in state["processes"] and alive(state["processes"][args.service]):
        raise HarnessError(f"{args.service} is already running")
    state["processes"][args.service] = {**spawn(args.service, spec), "ip": SERVICE_IPS[args.service]}
    save_state(state)
    wait_ready(args.service, state["processes"][args.service])
    print(f"stack: {args.service} ready on {SERVICE_IPS[args.service]}")
    return 0


def cmd_oneshot(args):
    if args.service not in ONESHOTS:
        raise HarnessError(f"{args.service} is not a one-shot service")
    state = load_state()
    compose = load_compose()["services"]
    spec = build(args.service, compose[args.service], load_env_values(state["sets"] + args.set), state["config_dir"])
    code = run_oneshot(args.service, spec)
    print(tail(args.service, 20))
    print(f"stack: {args.service} exited with {code}")
    return code


def cmd_status(_args):
    state = load_state()
    for name, proc in state["processes"].items():
        running = alive(proc)
        ready = running and name in READY and http_ok(READY[name].format(ip=proc["ip"]))
        print(f"{name:15} pid={proc['pid']:<8} {'running' if running else 'stopped'}{' ready' if ready else ''}")
    return 0


def cmd_logs(args):
    print(tail(args.service, args.n))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify native harness")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up")
    up.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    up.add_argument("--config-dir", help="use this directory instead of ./config (robustness tests)")
    up.add_argument("--only", help="comma-separated subset of services")
    down = sub.add_parser("down")
    down.add_argument("--purge", action="store_true", help="also delete .harness (data, logs)")
    for name in ("stop", "start", "oneshot"):
        command = sub.add_parser(name)
        command.add_argument("service")
        command.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    sub.add_parser("status")
    logs = sub.add_parser("logs")
    logs.add_argument("service")
    logs.add_argument("-n", type=int, default=50)
    args = parser.parse_args(argv)
    handlers = {"up": cmd_up, "down": cmd_down, "stop": cmd_stop, "start": cmd_start, "oneshot": cmd_oneshot, "status": cmd_status, "logs": cmd_logs}
    try:
        return handlers[args.command](args)
    except (HarnessError, render.RenderError, subprocess.CalledProcessError) as exc:
        print(f"stack: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
