#!/usr/bin/env python3
"""grafana-setup: idempotently provision Grafana (spec 10). Standard library only.

1. Grafana >= 12.0; datasources (fixed UIDs, correlations of 7.3); folders. 2. Plan B content, too
big for the compose (spec 4.3): downloaded (mirror, or this repository at a fixed tag), checked
against GRAFANA_SETUP_FILES (path=sha256;...), imported only if all match; it provisions dashboards
and alerting. Tokens are never written to the output.
"""

import hashlib
import http.client
import importlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

MIN_MAJOR = 12
LOKI_UID = "gc-loki"
TEMPO_UID = "gc-tempo"
PROMETHEUS_UID = "gc-prometheus"
# Same rule as config-guard (guard.sh) for each item of PROJECTS.
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Trace -> logs: every log of the trace, whatever its path (OTLP or Faro), via trace_id metadata.
TRACE_TO_LOGS_QUERY = '{project=~".+"} | trace_id="${__trace.traceId}"'
# Fields compared to decide whether an existing datasource must be updated. isDefault is left to
# the operator (Grafana makes the first datasource of an organisation the default one).
COMPARED_FIELDS = ("name", "type", "access", "url", "basicAuth", "jsonData")
# Folder of the shared dashboards and of the alert rules.
SHARED_FOLDER_UID = "gc-grafana-coolify"
SHARED_FOLDER_TITLE = "grafana-coolify"
SECRET_VARIABLES = ("GRAFANA_SA_TOKEN", "TELEGRAM_BOT_TOKEN")
CONTENT_DIR = "config/grafana-setup"
CONTENT_RE = re.compile(r"^(?:dashboards/)?[a-z0-9_-]+\.(?:py|json)$")
CONTENT_MODULES = ("alerting.py", "dashboards.py")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Same rule as config-guard (guard.sh) for GRAFANA_URL and the *_INTERNAL_URL variables.
HTTP_URL_RE = re.compile(r"^https?://\S+$")
URL_VARIABLES = ("GRAFANA_URL", "LOKI_INTERNAL_URL", "TEMPO_INTERNAL_URL", "PROMETHEUS_INTERNAL_URL")
SCHEMES = ("https://", "http://", "file:")
MAX_CONTENT_BYTES = 2 * 1024 * 1024


class SetupError(Exception):
    """A clean, explained stop (exit code 1)."""


class InvalidRequest(SetupError):
    """A request urllib refuses to send (malformed URL): retrying cannot help."""


class Grafana:
    def __init__(self, url, token, timeout=15):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method, path, body=None, headers=None):
        """Return (status, parsed JSON or None). Never raises on HTTP error statuses."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, parse_json(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, parse_json(exc.read())
        except (ValueError, http.client.InvalidURL) as exc:
            # A malformed URL: urllib raises ValueError (unknown url type), http.client raises
            # InvalidURL (an HTTPException, hence this clause first). Retrying cannot help.
            raise InvalidRequest(f"{method} {path}: invalid request to {self.url} ({exc})") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            raise SetupError(f"{method} {path}: Grafana unreachable ({reason(exc)})") from None

    def expect(self, method, path, body=None, ok=(200,), headers=None):
        status, payload = self.request(method, path, body, headers)
        if status not in ok:
            raise self.error(method, path, status, payload)
        return payload

    @staticmethod
    def error(method, path, status, payload):
        hint = ""
        if status in (404, 410) and path.startswith("/api/v1/provisioning"):
            hint = " (legacy alerting provisioning API unavailable: see README, « Alertes »)"
        return SetupError(f"{method} {path}: HTTP {status}: {message_of(payload)}{hint}")


def parse_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")[:200]


def message_of(payload):
    return payload.get("message") if isinstance(payload, dict) else payload


def reason(exc):
    return getattr(exc, "reason", None) or exc.__class__.__name__


def wait_healthy(api, timeout=120, interval=3):
    deadline = time.monotonic() + timeout
    while True:
        try:
            status, payload = api.request("GET", "/api/health")
            if status == 200 and isinstance(payload, dict):
                return payload
        except InvalidRequest:
            raise
        except SetupError:
            if time.monotonic() >= deadline:
                raise
        if time.monotonic() >= deadline:
            raise SetupError(f"Grafana not healthy after {timeout}s")
        time.sleep(interval)


def check_version(health):
    version = str(health.get("version", ""))
    match = re.match(r"^v?(\d+)\.", version)
    if not match:
        raise SetupError(f"cannot read the Grafana version from /api/health: {version!r}")
    if int(match.group(1)) < MIN_MAJOR:
        raise SetupError(f"Grafana {version} is not supported: version {MIN_MAJOR}.0 or later is required")
    return version


def parse_projects(value):
    projects = []
    for item in (value or "").split(","):
        name = item.strip()
        if not name:
            continue
        if not PROJECT_RE.match(name):
            raise SetupError(f"invalid project name in PROJECTS: {name!r} (expected [a-z0-9][a-z0-9-]{{0,63}})")
        if name not in projects:
            projects.append(name)
    return projects


def datasources(loki_url, tempo_url, prometheus_url):
    """Desired datasources, with the correlations of spec 7.3."""
    return [
        {
            "uid": LOKI_UID,
            "name": "Loki (grafana-coolify)",
            "type": "loki",
            "access": "proxy",
            "url": loki_url,
            "basicAuth": False,
            "jsonData": {
                "derivedFields": [
                    {
                        "name": "trace_id",
                        "matcherType": "label",
                        "matcherRegex": "trace_id",
                        "url": "${__value.raw}",
                        "urlDisplayLabel": "Trace",
                        "datasourceUid": TEMPO_UID,
                    }
                ]
            },
        },
        {
            "uid": TEMPO_UID,
            "name": "Tempo (grafana-coolify)",
            "type": "tempo",
            "access": "proxy",
            "url": tempo_url,
            "basicAuth": False,
            "jsonData": {
                "tracesToLogsV2": {
                    "datasourceUid": LOKI_UID,
                    "spanStartTimeShift": "-5m",
                    "spanEndTimeShift": "5m",
                    "filterByTraceID": True,
                    "filterBySpanID": False,
                    "customQuery": True,
                    "query": TRACE_TO_LOGS_QUERY,
                },
                "tracesToMetrics": {
                    "datasourceUid": PROMETHEUS_UID,
                    "spanStartTimeShift": "-5m",
                    "spanEndTimeShift": "5m",
                    "tags": [{"key": "service.name", "value": "service"}],
                    "queries": [
                        {"name": "Request rate", "query": "sum(rate(traces_spanmetrics_calls_total{$__tags}[5m]))"},
                        {
                            "name": "p95 latency",
                            "query": "histogram_quantile(0.95, sum(rate(traces_spanmetrics_latency_bucket{$__tags}[5m])) by (le))",
                        },
                    ],
                },
                "serviceMap": {"datasourceUid": PROMETHEUS_UID},
                "nodeGraph": {"enabled": True},
                "lokiSearch": {"datasourceUid": LOKI_UID},
            },
        },
        {
            "uid": PROMETHEUS_UID,
            "name": "Prometheus (grafana-coolify)",
            "type": "prometheus",
            "access": "proxy",
            "url": prometheus_url,
            "basicAuth": False,
            "jsonData": {
                "httpMethod": "POST",
                "exemplarTraceIdDestinations": [{"name": "trace_id", "datasourceUid": TEMPO_UID}],
            },
        },
    ]


def ensure_datasource(api, desired):
    path = f"/api/datasources/uid/{desired['uid']}"
    status, current = api.request("GET", path)
    if status == 404:
        api.expect("POST", "/api/datasources", desired)
        return "created"
    if status != 200 or not isinstance(current, dict):
        raise SetupError(f"GET {path}: HTTP {status}: {message_of(current)}")
    if all(current.get(field) == desired[field] for field in COMPARED_FIELDS):
        return "unchanged"
    api.expect("PUT", path, dict(desired, isDefault=bool(current.get("isDefault"))))
    return "updated"


def folder_uid(project):
    """gc-<project>, hashed past Grafana's 40-character UID limit."""
    uid = "gc-" + project
    return uid if len(uid) <= 40 else f"gc-{project[:28]}-{hashlib.sha256(project.encode()).hexdigest()[:8]}"


def ensure_folder(api, uid, title):
    status, current = api.request("GET", f"/api/folders/{uid}")
    if status == 404:
        api.expect("POST", "/api/folders", {"uid": uid, "title": title})
        return "created"
    if status != 200 or not isinstance(current, dict):
        raise SetupError(f"GET /api/folders/{uid}: HTTP {status}: {message_of(current)}")
    if current.get("title") == title:
        return "unchanged"
    api.expect("PUT", f"/api/folders/{uid}", {"title": title, "overwrite": True})
    return "updated"


def required(env, name):
    value = env.get(name, "").strip()
    if not value:
        raise SetupError(f"{name} is required")
    return value


def required_url(env, name):
    value = required(env, name)
    if not HTTP_URL_RE.match(value) or value.rstrip("/") in ("http:", "https:"):
        raise SetupError(f"{name} must be an http:// or https:// URL, got {value[:80]!r}")
    return value


def parse_content(value):
    files = []
    for item in (value or "").split(";"):
        if not item:
            continue
        path, separator, digest = item.partition("=")
        if not separator or not CONTENT_RE.match(path) or not SHA256_RE.match(digest):
            raise SetupError(f"malformed GRAFANA_SETUP_FILES entry: {item!r}")
        files.append((path, digest))
    missing = [name for name in CONTENT_MODULES if name not in dict(files)]
    if missing:
        raise SetupError(f"GRAFANA_SETUP_FILES does not list {', '.join(missing)}")
    return files


def content_url(env):
    url = (env.get("GRAFANA_SETUP_MIRROR_URL") or "").strip() or (env.get("GRAFANA_SETUP_PINNED_URL") or "").strip()
    if not url.startswith(SCHEMES):
        raise SetupError(f"the source of the plan B content must start with one of {', '.join(SCHEMES)}")
    return f"{url.rstrip('/')}/{CONTENT_DIR}"


def fetch(url, attempts=3, timeout=30, pause=2, limit=MAX_CONTENT_BYTES):
    # Network errors and 5xx are retried; 4xx and file: errors are not.
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read(limit + 1)
            if len(data) > limit:
                raise SetupError(f"{url}: larger than {limit} bytes, refused")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == attempts:
                raise SetupError(f"{url}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            if url.startswith("file:") or attempt == attempts:
                raise SetupError(f"{url}: {getattr(exc, 'reason', exc)}") from None
        time.sleep(pause * attempt)
    raise SetupError(f"{url}: no attempt made")


def download(base_url, files, target, pause=2):
    for path, digest in files:
        data = fetch(f"{base_url}/{path}", pause=pause)
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise SetupError(f"{path}: SHA-256 {actual} differs from the pinned {digest}: nothing was run")
        destination = Path(target) / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def provision_content(api, env, projects, out, pause=2):
    files = parse_content(env.get("GRAFANA_SETUP_FILES"))
    base_url = content_url(env)
    with tempfile.TemporaryDirectory(prefix="grafana-setup-") as tmp:
        download(base_url, files, tmp, pause)
        out(f"grafana-setup: {len(files)} content files verified (SHA-256)")
        sys.path.insert(0, tmp)
        try:
            alerting = importlib.import_module("alerting")
            dashboards = importlib.import_module("dashboards")
            for module in (alerting, dashboards):
                if getattr(module, "__file__", None) is None:
                    raise SetupError(f"{module.__name__}.py is missing")
            prepared = alerting.prepare(env, PROMETHEUS_UID)
            dashboards.provision(api, projects, SHARED_FOLDER_UID, folder_uid, out)
            alerting.provision(api, prepared, out)
        finally:
            sys.path.remove(tmp)


def run(env, out=print, health_timeout=120):
    grafana_url, loki_url, tempo_url, prometheus_url = (required_url(env, name) for name in URL_VARIABLES)
    api = Grafana(grafana_url, required(env, "GRAFANA_SA_TOKEN"))
    desired = datasources(loki_url, tempo_url, prometheus_url)
    projects = parse_projects(env.get("PROJECTS", ""))
    version = check_version(wait_healthy(api, timeout=health_timeout))
    out(f"grafana-setup: Grafana {version}")
    for datasource in desired:
        out(f"grafana-setup: datasource {datasource['uid']}: {ensure_datasource(api, datasource)}")
    out(f"grafana-setup: folder {SHARED_FOLDER_UID}: {ensure_folder(api, SHARED_FOLDER_UID, SHARED_FOLDER_TITLE)}")
    for project in projects:
        out(f"grafana-setup: folder {folder_uid(project)}: {ensure_folder(api, folder_uid(project), project)}")
    out("grafana-setup: datasources and folders: done")
    try:
        provision_content(api, env, projects, out)
    except (SetupError, ValueError) as exc:
        raise SetupError(f"datasources and folders are provisioned, dashboards and alerts are not: {exc}") from None
    out("grafana-setup: done")


def redact(message, env):
    for name in SECRET_VARIABLES:
        secret = (env.get(name) or "").strip()
        if secret:
            message = message.replace(secret, "***")
    return message


def main():
    try:
        run(os.environ)
    except SetupError as exc:
        print(f"grafana-setup: ERROR: {redact(str(exc), os.environ)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
