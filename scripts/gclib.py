"""Shared helpers for scripts/smoke.py and scripts/security.py (standard library only).

Endpoints default to the native harness (service names resolved by /etc/hosts) and can be
overridden with GC_* environment variables to run against a real deployment.
"""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env_file(path):
    values = {}
    if Path(path).exists():
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


def settings():
    """Stack values: harness/harness.env, then .harness/edge.json, then GC_* overrides."""
    values = env_file(ROOT / "harness" / "harness.env")
    edge_path = ROOT / ".harness" / "edge.json"
    edge = json.loads(edge_path.read_text(encoding="utf-8")) if edge_path.exists() else {}
    defaults = {
        "GC_OTLP_URL": "http://alloy:4318",
        "GC_GATEWAY_URL": edge.get("traefik_url", "http://alloy-gateway:4318"),
        "GC_GATEWAY_HOST": edge.get("hosts", {}).get("alloy-gateway", ""),
        "GC_GATEWAY_USER": edge.get("user", ""),
        "GC_GATEWAY_PASSWORD": edge.get("password", ""),
        "GC_FARO_URL": "http://alloy:12347",
        "GC_LOKI_URL": "http://loki:3100",
        "GC_TEMPO_URL": "http://tempo:3200",
        "GC_PROM_URL": "http://prometheus:9090",
        "GC_ALLOY_METRICS_URL": "http://alloy:12345",
    }
    for key, value in defaults.items():
        values[key] = os.environ.get(key, value)
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    values["edge"] = edge
    return values


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body)

    def header_values(self, name):
        return self.headers.get_all(name) or []


def http(method, url, body=None, headers=None, host=None, auth=None, timeout=15):
    """HTTP request that never raises on status codes. `host` overrides the Host header."""
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None and not (headers and "Content-Type" in headers):
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if host:
        req.add_header("Host", host)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.headers, resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers, exc.read())


def get_json(url, params=None, headers=None):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    resp = http("GET", full, headers=headers)
    if resp.status != 200:
        raise AssertionError(f"GET {full}: HTTP {resp.status}: {resp.body[:300]!r}")
    return resp.json()


def wait_for(fetch, what, timeout=60, interval=2):
    """Call fetch() until it returns a truthy value; AssertionError after timeout."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fetch()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timeout after {timeout}s waiting for {what} (last: {last!r})")


def run_id():
    return os.urandom(4).hex()


def new_trace_id():
    return os.urandom(16).hex()


def new_span_id():
    return os.urandom(8).hex()


def now_ns():
    return time.time_ns()


# ------------------------------------------------------------------ OTLP/HTTP JSON builders
def any_value(value):
    """OTLP/JSON AnyValue: lists become arrayValue, dicts kvlistValue, scalars keep their type."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, list):
        return {"arrayValue": {"values": [any_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"kvlistValue": {"values": attrs(value)}}
    return {"stringValue": str(value)}


def attrs(values):
    return [{"key": key, "value": any_value(value)} for key, value in values.items()]


def scoped(key, items, scope=None):
    """One scopeLogs/scopeSpans entry; `scope` (attributes of the instrumentation scope) is optional."""
    entry = {key: items}
    if scope is not None:
        entry["scope"] = {"name": "gc-smoke", "attributes": attrs(scope)}
    return entry


def otlp_logs(resource, body, attributes=None, trace_id=None, span_id=None, severity="INFO", scope=None):
    record = {"timeUnixNano": str(now_ns()), "severityText": severity, "body": any_value(body), "attributes": attrs(attributes or {})}
    if trace_id:
        record["traceId"] = trace_id
        record["spanId"] = span_id or new_span_id()
    return {"resourceLogs": [{"resource": {"attributes": attrs(resource)}, "scopeLogs": [scoped("logRecords", [record], scope)]}]}


def otlp_traces(resource, spans, scope=None):
    return {"resourceSpans": [{"resource": {"attributes": attrs(resource)}, "scopeSpans": [scoped("spans", spans, scope)]}]}


def span(trace_id, name, attributes=None, span_id=None, kind=2, events=None):
    end = now_ns()
    return {
        "traceId": trace_id,
        "spanId": span_id or new_span_id(),
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(end - 5_000_000),
        "endTimeUnixNano": str(end),
        "attributes": attrs(attributes or {}),
        "events": events or [],
    }


def otlp_sum(resource, name, value, attributes=None):
    point = {"asInt": str(value), "timeUnixNano": str(now_ns()), "startTimeUnixNano": str(now_ns() - 10**9), "attributes": attrs(attributes or {})}
    metric = {"name": name, "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": [point]}}
    return {"resourceMetrics": [{"resource": {"attributes": attrs(resource)}, "scopeMetrics": [{"metrics": [metric]}]}]}


def send_otlp(base_url, signal, payload, host=None, auth=None):
    resp = http("POST", f"{base_url}/v1/{signal}", payload, host=host, auth=auth)
    if resp.status // 100 != 2:
        raise AssertionError(f"OTLP {signal} to {base_url}: HTTP {resp.status}: {resp.body[:300]!r}")
    return resp


# ------------------------------------------------------------------ Faro
def faro_payload(app, page_url, logs=None, traces=None, session_attributes=None, events=None, browser=None):
    meta = {"app": app, "session": {"id": "gc-" + run_id(), "attributes": session_attributes or {}}, "page": {"url": page_url}}
    if browser:
        meta["browser"] = browser
    payload = {"meta": meta, "logs": logs or [], "events": events or [], "measurements": [], "exceptions": []}
    if traces:
        payload["traces"] = traces
    return payload


def faro_event(name, attributes=None):
    """A Faro event: its attributes become event_data_<key> in the logfmt line."""
    return {"name": name, "domain": "gc", "attributes": attributes or {}, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}


def faro_log(message, level="info", trace_id=None, span_id=None, context=None):
    entry = {"message": message, "level": level, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "context": context or {}}
    if trace_id:
        entry["trace"] = {"trace_id": trace_id, "span_id": span_id or new_span_id()}
    return entry


def send_faro(base_url, payload, api_key, host=None, headers=None):
    all_headers = {"x-api-key": api_key, **(headers or {})}
    resp = http("POST", f"{base_url}/collect", payload, headers=all_headers, host=host)
    if resp.status // 100 != 2:
        raise AssertionError(f"Faro to {base_url}: HTTP {resp.status}: {resp.body[:300]!r}")
    return resp


# ------------------------------------------------------------------ stores
def loki_entries(loki_url, query, since_s=900):
    """[(indexed_labels, line, structured_metadata)] with labels and metadata kept apart."""
    params = {"query": query, "start": str(now_ns() - since_s * 10**9), "limit": "500", "direction": "forward"}
    data = get_json(loki_url + "/loki/api/v1/query_range", params, headers={"X-Loki-Response-Encoding-Flags": "categorize-labels"})
    entries = []
    for stream in data["data"]["result"]:
        for value in stream["values"]:
            meta = value[2].get("structuredMetadata", {}) if len(value) > 2 else {}
            entries.append((stream["stream"], value[1], meta))
    return entries


def tempo_trace(tempo_url, trace_id):
    """Tempo v2 trace, or None when absent (Tempo answers 404 or an empty trace)."""
    resp = http("GET", f"{tempo_url}/api/v2/traces/{trace_id}")
    if resp.status == 404:
        return None
    if resp.status != 200:
        raise AssertionError(f"Tempo trace {trace_id}: HTTP {resp.status}: {resp.body[:200]!r}")
    trace = resp.json().get("trace") or {}
    return trace if trace.get("resourceSpans") else None


def otlp_attr_map(attributes):
    out = {}
    for item in attributes or []:
        value = item.get("value", {})
        out[item["key"]] = next(iter(value.values()), None) if value else None
    return out


def trace_resources_and_spans(trace):
    """[(resource_attrs, [span_attrs...])] of a Tempo v2 trace."""
    result = []
    for resource_spans in (trace or {}).get("resourceSpans", []):
        resource = otlp_attr_map(resource_spans.get("resource", {}).get("attributes"))
        spans = [otlp_attr_map(s.get("attributes")) for scope in resource_spans.get("scopeSpans", []) for s in scope.get("spans", [])]
        result.append((resource, spans))
    return result


def prom_query(prom_url, expr):
    return get_json(prom_url + "/api/v1/query", {"query": expr})["data"]["result"]


def metric_value(metrics_text, name, labels=None):
    """Sum of the samples of `name` in a Prometheus text exposition matching `labels`."""
    total = 0.0
    pattern = re.compile(r"^" + re.escape(name) + r"(\{(?P<labels>[^}]*)\})? (?P<value>\S+)$")
    for line in metrics_text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        found = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', match.group("labels") or ""))
        if all(found.get(k) == v for k, v in (labels or {}).items()):
            total += float(match.group("value"))
    return total


def scrape(url):
    resp = http("GET", url.rstrip("/") + "/metrics")
    if resp.status != 200:
        raise AssertionError(f"scrape {url}: HTTP {resp.status}")
    return resp.body.decode("utf-8", "replace")


DURATION_RE = re.compile(r"(\d+)(ms|y|w|d|h|m|s)")
DURATION_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def duration_seconds(text):
    """Parse Go/Prometheus durations: 168h, 1w, 30d, 720h0m0s, 90d."""
    parts = DURATION_RE.findall(text or "")
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"not a duration: {text!r}")
    return sum(int(n) * DURATION_UNITS[u] for n, u in parts)


SIZE_RE = re.compile(r"^(\d+)([KMGTPE]?)(i?)B$")


def size_bytes(text):
    """Prometheus byte sizes are base 2 whatever the spelling: 100GB == 100GiB."""
    match = SIZE_RE.match(text or "")
    if not match:
        raise ValueError(f"not a size: {text!r}")
    return int(match.group(1)) * 1024 ** "_KMGTPE".index(match.group(2) or "_")


def yaml_section_value(text, section, key):
    """Value of `key` inside the top-level `section:` block of a YAML dump (first occurrence)."""
    match = re.search(rf"(?m)^{re.escape(section)}:\n(?P<body>(?:[ \-].*\n?)*)", text)
    if not match:
        return None
    found = re.search(rf"(?m)^\s+-?\s*{re.escape(key)}: ?(.*)$", match.group("body"))
    return found.group(1).strip().strip("'\"") if found else None
