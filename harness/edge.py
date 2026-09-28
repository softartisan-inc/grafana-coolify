#!/usr/bin/env python3
"""Harness edge: Traefik emulating the routers Coolify generates, plus a test Grafana.

- Traefik (HTTP only, 127.0.10.100:8080) loads a file-provider config built from
  traefik/grafana-coolify.yaml.example (test htpasswd lines and origin regex) and one router per
  service carrying a `coolify.traefik.middlewares` label and a SERVICE_FQDN_<SERVICE>_<PORT>
  variable: Host(<service>.gc.test) -> service IP:PORT with the label's middlewares.
  A router Host(other.gc.test) -> Grafana stands for "another public domain of the server".
- Grafana OSS (127.0.10.101:3300, admin/admin, anonymous access disabled); a service account
  with the Admin role is created and its token written to .harness/runtime.env and edge.json.

Usage: python3 harness/edge.py up | down | revoke USER | restore
Needs `python3 harness/stack.py up` first (service IPs, /etc/hosts).
"""

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import yaml

import stack

ROOT = stack.ROOT
EDGE_DIR = stack.HARNESS / "edge"
DYNAMIC = EDGE_DIR / "dynamic.yml"
EDGE_JSON = stack.HARNESS / "edge.json"
RUNTIME_ENV = stack.HARNESS / "runtime.env"
TRAEFIK_IP, TRAEFIK_PORT = "127.0.10.100", 8080
GRAFANA_IP, GRAFANA_PORT = "127.0.10.101", 3300
DOMAIN = "gc.test"
USERS = {"proj-a": "harness-pass-a1", "proj-b": "harness-pass-b2"}
REVOCABLE_USER = "proj-b"
ORIGIN_REGEX = r"^https://([a-z0-9-]+\.)?example\.(me|app)$"
LABEL = "coolify.traefik.middlewares="


def htpasswd_line(user, password):
    salt = hashlib.sha256(user.encode()).hexdigest()[:8]
    result = subprocess.run(["openssl", "passwd", "-apr1", "-salt", salt, password], capture_output=True, text=True, check=True)
    return f"{user}:{result.stdout.strip()}"


def public_routes(services):
    """[(service, port, [middlewares])] for services Coolify would expose publicly."""
    routes = []
    for name, service in services.items():
        labels = service.get("labels") or []
        middlewares = [m for label in labels if label.startswith(LABEL) for m in label[len(LABEL) :].split(",")]
        prefix = "SERVICE_FQDN_" + name.upper().replace("-", "_") + "_"
        ports = [key[len(prefix) :] for key in (service.get("environment") or {}) if key.startswith(prefix)]
        for port in ports:
            routes.append((name, int(port), middlewares))
    return routes


def dynamic_config(services, users):
    example = yaml.safe_load((ROOT / "traefik" / "grafana-coolify.yaml.example").read_text(encoding="utf-8"))
    middlewares = example["http"]["middlewares"]
    middlewares["gc-otlp-auth"]["basicAuth"]["users"] = [htpasswd_line(u, USERS[u]) for u in users]
    middlewares["gc-faro-cors"]["headers"]["accessControlAllowOriginListRegex"] = [ORIGIN_REGEX]
    routers, backends = {}, {}
    for name, port, used in public_routes(services):
        # Coolify references the middlewares with their provider suffix (@file); in the harness
        # they live in the same file provider, so the suffix is kept as-is.
        routers[name] = {"rule": f"Host(`{name}.{DOMAIN}`)", "entryPoints": ["web"], "service": name, "middlewares": used}
        backends[name] = {"loadBalancer": {"servers": [{"url": f"http://{stack.SERVICE_IPS[name]}:{port}"}]}}
    routers["other"] = {"rule": f"Host(`other.{DOMAIN}`)", "entryPoints": ["web"], "service": "other"}
    backends["other"] = {"loadBalancer": {"servers": [{"url": f"http://{GRAFANA_IP}:{GRAFANA_PORT}"}]}}
    return {"http": {"middlewares": middlewares, "routers": routers, "services": backends}}


def write_dynamic(users):
    EDGE_DIR.mkdir(parents=True, exist_ok=True)
    services = stack.load_compose()["services"]
    DYNAMIC.write_text(yaml.safe_dump(dynamic_config(services, users), sort_keys=True), encoding="utf-8")


def wait(check, what, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1)
    raise stack.HarnessError(f"timeout waiting for {what}")


def gateway_status(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(
        f"http://{TRAEFIK_IP}:{TRAEFIK_PORT}/v1/logs",
        data=b'{"resourceLogs":[]}',
        headers={"Host": f"alloy-gateway.{DOMAIN}", "Content-Type": "application/json", "Authorization": "Basic " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def grafana_api(method, path, body=None):
    auth = base64.b64encode(b"admin:admin").decode()
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://{GRAFANA_IP}:{GRAFANA_PORT}{path}", data=data, method=method)
    req.add_header("Authorization", "Basic " + auth)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read() or b"null")


def service_account_token():
    found = grafana_api("GET", "/api/serviceaccounts/search?query=gc-setup")["serviceAccounts"]
    account = found[0] if found else grafana_api("POST", "/api/serviceaccounts", {"name": "gc-setup", "role": "Admin"})
    token = grafana_api("POST", f"/api/serviceaccounts/{account['id']}/tokens", {"name": f"harness-{int(time.time())}"})
    return token["key"]


def cmd_up(_args):
    state = stack.load_state()
    if not any(stack.alive(p) for p in state["processes"].values()):
        raise stack.HarnessError("start the stack first: python3 harness/stack.py up")
    stack.preflight([(TRAEFIK_IP, TRAEFIK_PORT), (GRAFANA_IP, GRAFANA_PORT)])
    write_dynamic(list(USERS))
    grafana_home = stack.BIN / "grafana"
    grafana_data = stack.HARNESS / "grafana"
    specs = {
        "traefik": {
            "args": [
                str(stack.BIN / "traefik"),
                f"--entrypoints.web.address={TRAEFIK_IP}:{TRAEFIK_PORT}",
                f"--providers.file.filename={DYNAMIC}",
                "--providers.file.watch=true",
                "--ping=true",
                "--ping.entrypoint=web",
                "--log.level=INFO",
                "--accesslog=true",
            ],
            "env": {"PATH": "/usr/bin:/bin"},
        },
        "grafana": {
            "args": [str(grafana_home / "bin" / "grafana"), "server", "--homepath", str(grafana_home)],
            "env": {
                "PATH": "/usr/bin:/bin",
                "GF_SERVER_HTTP_ADDR": GRAFANA_IP,
                "GF_SERVER_HTTP_PORT": str(GRAFANA_PORT),
                "GF_PATHS_DATA": str(grafana_data / "data"),
                "GF_PATHS_LOGS": str(grafana_data / "logs"),
                "GF_PATHS_PLUGINS": str(grafana_data / "plugins"),
                "GF_SECURITY_ADMIN_USER": "admin",
                "GF_SECURITY_ADMIN_PASSWORD": "admin",
                "GF_AUTH_ANONYMOUS_ENABLED": "false",
                "GF_ANALYTICS_REPORTING_ENABLED": "false",
                "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
            },
        },
    }
    for name, spec in specs.items():
        state["processes"][name] = {**stack.spawn(name, spec), "ip": TRAEFIK_IP if name == "traefik" else GRAFANA_IP}
        stack.save_state(state)
    wait(lambda: stack.http_ok(f"http://{TRAEFIK_IP}:{TRAEFIK_PORT}/ping"), "Traefik /ping")
    wait(lambda: gateway_status(REVOCABLE_USER, USERS[REVOCABLE_USER]) == 200, "Traefik routes to alloy-gateway")
    wait(lambda: stack.http_ok(f"http://{GRAFANA_IP}:{GRAFANA_PORT}/api/health"), "Grafana /api/health", timeout=180)
    token = service_account_token()
    grafana_url = f"http://{GRAFANA_IP}:{GRAFANA_PORT}"
    RUNTIME_ENV.write_text(f"GRAFANA_URL={grafana_url}\nGRAFANA_SA_TOKEN={token}\n", encoding="utf-8")
    RUNTIME_ENV.chmod(0o600)
    edge = {
        "traefik_url": f"http://{TRAEFIK_IP}:{TRAEFIK_PORT}",
        "traefik_ip": TRAEFIK_IP,
        "hosts": {"alloy": f"alloy.{DOMAIN}", "alloy-gateway": f"alloy-gateway.{DOMAIN}", "other": f"other.{DOMAIN}"},
        "user": "proj-a",
        "password": USERS["proj-a"],
        "revocable_user": REVOCABLE_USER,
        "revocable_password": USERS[REVOCABLE_USER],
        "origin_ok": "https://acme.example.me",
        "grafana_url": grafana_url,
        "grafana_token": token,
    }
    EDGE_JSON.write_text(json.dumps(edge, indent=2), encoding="utf-8")
    EDGE_JSON.chmod(0o600)
    print(f"edge: Traefik on {TRAEFIK_IP}:{TRAEFIK_PORT}, Grafana on {GRAFANA_IP}:{GRAFANA_PORT}")
    return 0


def cmd_down(_args):
    state = stack.load_state()
    for name in ("traefik", "grafana"):
        proc = state["processes"].pop(name, None)
        if proc:
            stack.terminate(proc)
            print(f"edge: stopped {name}")
    stack.save_state(state)
    for path in (EDGE_JSON, RUNTIME_ENV):
        if path.exists():
            path.unlink()
    return 0


def cmd_revoke(args):
    write_dynamic([u for u in USERS if u != args.user])
    wait(lambda: gateway_status(args.user, USERS[args.user]) == 401, f"Traefik reload without {args.user}", timeout=30)
    print(f"edge: {args.user} revoked (hot reload)")
    return 0


def cmd_restore(_args):
    write_dynamic(list(USERS))
    wait(lambda: gateway_status(REVOCABLE_USER, USERS[REVOCABLE_USER]) == 200, "Traefik reload with every user", timeout=30)
    print("edge: every user restored")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify harness edge (Traefik + Grafana)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("up")
    sub.add_parser("down")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("user", choices=sorted(USERS))
    sub.add_parser("restore")
    args = parser.parse_args(argv)
    handlers = {"up": cmd_up, "down": cmd_down, "revoke": cmd_revoke, "restore": cmd_restore}
    try:
        return handlers[args.command](args)
    except (stack.HarnessError, subprocess.CalledProcessError, OSError) as exc:
        print(f"edge: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
