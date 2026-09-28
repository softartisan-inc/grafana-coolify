#!/usr/bin/env python3
"""grafana-setup (plan A): idempotently provision datasources and project folders in Grafana.

Standard library only (runs in python:3.13-alpine). Spec 10.1-10.2:
- version guard: stop cleanly if Grafana < 12.0 (read from /api/health);
- datasources with fixed UIDs: GET /api/datasources/uid/:uid -> PUT when present and different,
  POST /api/datasources otherwise; correlations of spec 7.3;
- folders gc-<project> for every project of PROJECTS;
- the service account token is never written to the output.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

MIN_MAJOR = 12
LOKI_UID = "gc-loki"
TEMPO_UID = "gc-tempo"
PROMETHEUS_UID = "gc-prometheus"
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Trace -> logs: every log of the trace, whatever its path (OTLP or Faro), via trace_id metadata.
TRACE_TO_LOGS_QUERY = '{project=~".+"} | trace_id="${__trace.traceId}"'
# Fields compared to decide whether an existing datasource must be updated. isDefault is left to
# the operator (Grafana makes the first datasource of an organisation the default one).
COMPARED_FIELDS = ("name", "type", "access", "url", "basicAuth", "jsonData")


class SetupError(Exception):
    """A clean, explained stop (exit code 1)."""


class Grafana:
    def __init__(self, url, token, timeout=15):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method, path, body=None):
        """Return (status, parsed JSON or None). Never raises on HTTP error statuses."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, parse_json(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, parse_json(exc.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise SetupError(f"{method} {path}: Grafana unreachable ({reason(exc)})") from None

    def expect(self, method, path, body=None, ok=(200,)):
        status, payload = self.request(method, path, body)
        if status not in ok:
            raise SetupError(f"{method} {path}: HTTP {status}: {message_of(payload)}")
        return payload


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
            raise SetupError(f"invalid project name in PROJECTS: {name!r} (expected [a-z0-9-]+)")
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


def ensure_folder(api, project):
    uid = f"gc-{project}"
    status, current = api.request("GET", f"/api/folders/{uid}")
    if status == 404:
        api.expect("POST", "/api/folders", {"uid": uid, "title": project})
        return "created"
    if status != 200 or not isinstance(current, dict):
        raise SetupError(f"GET /api/folders/{uid}: HTTP {status}: {message_of(current)}")
    if current.get("title") == project:
        return "unchanged"
    api.expect("PUT", f"/api/folders/{uid}", {"title": project, "overwrite": True})
    return "updated"


def required(env, name):
    value = env.get(name, "").strip()
    if not value:
        raise SetupError(f"{name} is required")
    return value


def run(env, out=print, health_timeout=120):
    api = Grafana(required(env, "GRAFANA_URL"), required(env, "GRAFANA_SA_TOKEN"))
    desired = datasources(
        required(env, "LOKI_INTERNAL_URL"),
        required(env, "TEMPO_INTERNAL_URL"),
        required(env, "PROMETHEUS_INTERNAL_URL"),
    )
    projects = parse_projects(env.get("PROJECTS", ""))
    version = check_version(wait_healthy(api, timeout=health_timeout))
    out(f"grafana-setup: Grafana {version}")
    for datasource in desired:
        out(f"grafana-setup: datasource {datasource['uid']}: {ensure_datasource(api, datasource)}")
    for project in projects:
        out(f"grafana-setup: folder gc-{project}: {ensure_folder(api, project)}")
    out("grafana-setup: done")


def main():
    token = os.environ.get("GRAFANA_SA_TOKEN", "")
    try:
        run(os.environ)
    except SetupError as exc:
        message = str(exc)
        if token:
            message = message.replace(token, "***")
        print(f"grafana-setup: ERROR: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
