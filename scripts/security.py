#!/usr/bin/env python3
"""Security checks of spec 12.4 (standard library only).

Harness mode (default): the public side is Traefik on the harness edge (harness/edge.py up).
"Unreachable from outside" means: no listener on the Traefik-facing address and no route.
Items 1, 7 and 8 are structural on the bench (listeners, emulated routers, compose settings):
only --remote makes them conclusive.
Remote mode (--remote): against a Coolify deployment; set
  GC_PUBLIC_IP, GC_FARO_PUBLIC_URL, GC_GATEWAY_PUBLIC_URL, GC_OTHER_PUBLIC_URL,
  GC_GATEWAY_USER, GC_GATEWAY_PASSWORD, GC_REVOKED_USER, GC_REVOKED_PASSWORD, GC_ORIGIN_OK,
  FARO_API_KEY, and for item 8 GC_ALLOY_CONTAINER, GC_GATEWAY_CONTAINER,
  GC_NODE_EXPORTER_CONTAINER (run on the server).

Usage: python3 scripts/security.py [--remote] [--only 1,2,...]
"""

import argparse
import concurrent.futures
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import gclib as g

ROOT = Path(__file__).resolve().parent.parent
INTERNAL_PORTS = [3100, 3200, 9090, 9100, 4317, 4318, 9095, 9096, 12345, 12347]
INTERNAL_NAMES = ["loki", "tempo", "prometheus", "node-exporter"]
EVIL_ORIGINS = ["https://evil-example.me", "https://x.example.me.attacker.com"]
HARDENED = ("alloy", "alloy-gateway")
# The only host file alloy and alloy-gateway may mount: their content-addressed config.
CONFIG_MOUNT_RE = re.compile(r"^/etc/alloy/config\.[0-9a-f]{8}\.alloy$")
BENCH_NOTE = "structural on bench, conclusive with --remote"


class Target:
    def __init__(self, remote):
        s = g.settings()
        self.remote = remote
        self.faro_key = s["FARO_API_KEY"]
        if remote:
            env = os.environ
            self.public_ip = env["GC_PUBLIC_IP"]
            self.faro = (env["GC_FARO_PUBLIC_URL"].rstrip("/"), None)
            self.gateway = (env["GC_GATEWAY_PUBLIC_URL"].rstrip("/"), None)
            self.other = (env["GC_OTHER_PUBLIC_URL"].rstrip("/"), None)
            self.user = (env["GC_GATEWAY_USER"], env["GC_GATEWAY_PASSWORD"])
            self.revoked = (env["GC_REVOKED_USER"], env["GC_REVOKED_PASSWORD"])
            self.origin_ok = env["GC_ORIGIN_OK"]
        else:
            edge = s["edge"]
            if not edge:
                raise SystemExit("security: start the edge first: python3 harness/edge.py up")
            url = edge["traefik_url"]
            self.public_ip = edge["traefik_ip"]
            self.faro = (url, edge["hosts"]["alloy"])
            self.gateway = (url, edge["hosts"]["alloy-gateway"])
            self.other = (url, edge["hosts"]["other"])
            self.user = (edge["user"], edge["password"])
            self.revoked = (edge["revocable_user"], edge["revocable_password"])
            self.origin_ok = edge["origin_ok"]

    def faro_request(self, method, body=None, headers=None):
        url, host = self.faro
        return g.http(method, url + "/collect", body, headers=headers, host=host)

    def gateway_post(self, auth=None, headers=None):
        url, host = self.gateway
        return g.http("POST", url + "/v1/logs", {"resourceLogs": []}, headers=headers, host=host, auth=auth)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def preflight_headers(origin):
    return {"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-api-key"}


def item1_unreachable(t):
    """1. Internal services and ports unreachable from outside."""
    for port in INTERNAL_PORTS:
        with socket.socket() as sock:
            sock.settimeout(2)
            reachable = sock.connect_ex((t.public_ip, port)) == 0
        expect(not reachable, f"port {port} answers on the public address {t.public_ip}")
    if not t.remote:
        url, _host = t.faro
        for name in INTERNAL_NAMES + [f"{n}.gc.test" for n in INTERNAL_NAMES]:
            status = g.http("GET", url + "/ready", host=name).status
            expect(status == 404, f"Traefik routes Host {name} (HTTP {status})")
        return BENCH_NOTE
    return None


def item2_gateway_auth(t):
    """2. OTLP gateway: 401 without credentials, 401 once revoked (hot reload), 2xx when valid."""
    expect(t.gateway_post().status == 401, "gateway without credentials is not 401")
    expect(t.gateway_post(auth=(t.user[0], "wrong-password")).status == 401, "gateway with a wrong password is not 401")
    expect(t.gateway_post(auth=t.user).status // 100 == 2, "gateway rejects valid credentials")
    if t.remote:
        expect(t.gateway_post(auth=t.revoked).status == 401, "revoked credentials still accepted")
        return
    expect(t.gateway_post(auth=t.revoked).status // 100 == 2, "revocable user should work before revocation")
    subprocess.run([sys.executable, str(ROOT / "harness" / "edge.py"), "revoke", t.revoked[0]], check=True)
    try:
        expect(t.gateway_post(auth=t.revoked).status == 401, "revoked credentials still accepted")
        expect(t.gateway_post(auth=t.user).status // 100 == 2, "revocation broke the other project")
    finally:
        subprocess.run([sys.executable, str(ROOT / "harness" / "edge.py"), "restore"], check=True)


def item3_cors(t):
    """3. Faro CORS: valid preflight headers, nothing for foreign origins, a single ACAO."""
    resp = t.faro_request("OPTIONS", headers=preflight_headers(t.origin_ok))
    expect(resp.status == 200, f"valid preflight: HTTP {resp.status}")
    expect(resp.header_values("Access-Control-Allow-Origin") == [t.origin_ok], f"ACAO {resp.header_values('Access-Control-Allow-Origin')}")
    methods = {m.strip() for m in resp.headers.get("Access-Control-Allow-Methods", "").split(",")}
    expect(methods == {"POST", "OPTIONS"}, f"methods {methods}")
    allowed = {h.strip().lower() for h in resp.headers.get("Access-Control-Allow-Headers", "").split(",")}
    expect(allowed == {"content-type", "x-api-key", "x-faro-session-id"}, f"headers {allowed}")
    expect(resp.headers.get("Access-Control-Max-Age") == "600", f"max-age {resp.headers.get('Access-Control-Max-Age')}")
    for origin in EVIL_ORIGINS:
        evil = t.faro_request("OPTIONS", headers=preflight_headers(origin))
        expect(not evil.header_values("Access-Control-Allow-Origin"), f"CORS header returned for {origin}")
    post = t.faro_request("POST", {"meta": {}}, headers={"Origin": t.origin_ok, "x-api-key": t.faro_key})
    expect(len(post.header_values("Access-Control-Allow-Origin")) == 1, f"ACAO count {post.header_values('Access-Control-Allow-Origin')}")
    expect("Origin" in ",".join(post.header_values("Vary")), f"Vary {post.header_values('Vary')}")


def item4_faro_key(t):
    """4. Faro without key or with a wrong key: rejected."""
    expect(t.faro_request("POST", {"meta": {}}).status == 401, "Faro without key accepted")
    expect(t.faro_request("POST", {"meta": {}}, headers={"x-api-key": "wrong-key"}).status == 401, "Faro with a wrong key accepted")


def item5_rate_limit(t):
    """5. A burst of POST beyond the limit gets 429 from Traefik, with the CORS headers a browser needs to read it."""
    headers = {"Origin": t.origin_ok, "x-api-key": t.faro_key}

    def one(_):
        resp = t.faro_request("POST", {"meta": {}}, headers=headers)
        return resp.status, resp.header_values("Access-Control-Allow-Origin")

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        answers = list(pool.map(one, range(600)))
    limited = [origins for status, origins in answers if status == 429]
    expect(limited, f"no 429 in a burst of 600 requests: {sorted({status for status, _ in answers})}")
    expect(all(origins == [t.origin_ok] for origins in limited), f"429 without Access-Control-Allow-Origin: {limited[:3]}")
    # Leave a full bucket to the next items (item 6 would otherwise get 429 instead of 413): wait
    # until a request passes again, then for a whole burst to refill (burst 100 / average 50 = 2s).
    g.wait_for(lambda: one(0)[0] != 429, "the rate limit bucket to refill", timeout=30, interval=1)
    time.sleep(5)


def read_status(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/") else 0


def oversized_post(t, size):
    """POST with `Expect: 100-continue`, like curl: Traefik answers 413 before the body is sent."""
    url, host = t.faro
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    raw = socket.create_connection((parts.hostname, port), timeout=15)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname) if parts.scheme == "https" else raw
    with sock:
        head = (
            f"POST /collect HTTP/1.1\r\nHost: {host or parts.netloc}\r\nContent-Type: application/json\r\n"
            f"x-api-key: {t.faro_key}\r\nContent-Length: {size}\r\nExpect: 100-continue\r\nConnection: close\r\n\r\n"
        )
        sock.sendall(head.encode())
        status = read_status(sock)
        if status == 100:
            try:
                sock.sendall(b" " * size)
            except OSError:
                pass
            status = read_status(sock)
        return status


def item6_body_size(t):
    """6. A body beyond the maximum size: 413."""
    status = oversized_post(t, 6 * 1024 * 1024)
    expect(status == 413, f"6 MiB body: HTTP {status}")


def item7_middleware_leak(t):
    """7. Coolify #9886: middlewares must not leak to other routers."""
    faro = t.faro_request("OPTIONS", headers=preflight_headers(t.origin_ok))
    expect(faro.status != 401 and "Basic" not in faro.headers.get("WWW-Authenticate", ""), "alloy asks for Basic Auth")
    gateway = t.gateway_post(auth=t.user, headers={"Origin": t.origin_ok})
    expect(not gateway.header_values("Access-Control-Allow-Origin"), "alloy-gateway returns a CORS header")
    url, host = t.other
    other = g.http("GET", url + "/api/health", headers={"Origin": t.origin_ok}, host=host)
    expect(other.status != 401 and "Basic" not in other.headers.get("WWW-Authenticate", ""), f"other domain asks for auth (HTTP {other.status})")
    expect(not other.header_values("Access-Control-Allow-Origin"), "other domain returns the package CORS header")
    return None if t.remote else BENCH_NOTE


def hardening_errors(name, user, read_only, cap_drop, security_opt, host_mounts):
    errors = []
    if not user or user.split(":")[0] in ("", "0", "root"):
        errors.append(f"{name}: runs as root (user={user!r})")
    if not read_only:
        errors.append(f"{name}: root filesystem is writable")
    if "ALL" not in [c.upper() for c in cap_drop or []]:
        errors.append(f"{name}: cap_drop ALL missing")
    if not any(o.replace("=", ":") == "no-new-privileges:true" for o in security_opt or []):
        errors.append(f"{name}: no-new-privileges missing")
    for source, target in host_mounts:
        errors.append(f"{name}: host mount {source} -> {target}")
    return errors


def docker_inspect(container):
    return json.loads(subprocess.run(["docker", "inspect", container], capture_output=True, text=True, check=True).stdout)[0]


def config_is_writable(container, path):
    """Coolify drops `read_only: true` of the content mounts: the non-root user must not be able to write."""
    result = subprocess.run(["docker", "exec", container, "sh", "-c", ': >> "$1"', "sh", path], capture_output=True, text=True, check=False)
    return result.returncode == 0


def item8_hardening(t):
    """8. alloy and alloy-gateway: no host mount except their config, non-root, read-only FS; node-exporter mounts read-only."""
    errors = []
    if t.remote:
        for name, env in (("alloy", "GC_ALLOY_CONTAINER"), ("alloy-gateway", "GC_GATEWAY_CONTAINER")):
            container = os.environ[env]
            info = docker_inspect(container)
            binds = [(m["Source"], m["Destination"]) for m in info["Mounts"] if m["Type"] == "bind"]
            others = [bind for bind in binds if not CONFIG_MOUNT_RE.match(bind[1])]
            host = info["HostConfig"]
            errors += hardening_errors(name, info["Config"]["User"], host["ReadonlyRootfs"], host["CapDrop"], host["SecurityOpt"], others)
            for _source, target in binds:
                if CONFIG_MOUNT_RE.match(target) and config_is_writable(container, target):
                    errors.append(f"{name}: {target} is writable by the service user")
        info = docker_inspect(os.environ["GC_NODE_EXPORTER_CONTAINER"])
        errors += [f"node-exporter: {m['Destination']} is mounted read-write" for m in info["Mounts"] if m["Type"] == "bind" and m.get("RW", True)]
    else:
        sys.path.insert(0, str(ROOT / "scripts"))
        import check  # noqa: PLC0415 - reuse the docker compose config helper

        services = check.compose_json()["services"]
        for name in HARDENED:
            svc = services[name]
            binds = [(v["source"], v["target"]) for v in svc.get("volumes", []) if v["type"] == "bind"]
            others = [bind for bind in binds if not CONFIG_MOUNT_RE.match(bind[1])]
            errors += hardening_errors(name, svc.get("user"), svc.get("read_only"), svc.get("cap_drop"), svc.get("security_opt"), others)
        host_binds = [v for v in services["node-exporter"].get("volumes", []) if v["type"] == "bind"]
        errors += [f"node-exporter: {v['target']} is not read-only" for v in host_binds if not v.get("read_only")]
    expect(not errors, "; ".join(errors))
    return None if t.remote else BENCH_NOTE


ITEMS = {
    "1": item1_unreachable,
    "2": item2_gateway_auth,
    "3": item3_cors,
    "4": item4_faro_key,
    "5": item5_rate_limit,
    "6": item6_body_size,
    "7": item7_middleware_leak,
    "8": item8_hardening,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify security checks (spec 12.4)")
    parser.add_argument("--remote", action="store_true", help="target a Coolify deployment (see module docstring)")
    parser.add_argument("--only", help="comma-separated item numbers")
    args = parser.parse_args(argv)
    target = Target(args.remote)
    names = args.only.split(",") if args.only else list(ITEMS)
    failed = 0
    for name in names:
        func = ITEMS[name]
        try:
            note = func(target)
            print(f"security: [PASS] {func.__doc__.splitlines()[0]}" + (f" ({note})" if note else ""))
        except (AssertionError, KeyError, OSError, subprocess.CalledProcessError) as exc:
            failed += 1
            print(f"security: [FAIL] {func.__doc__.splitlines()[0]} -> {exc}")
    print(f"security: {len(names) - failed}/{len(names)} items passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
