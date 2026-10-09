#!/usr/bin/env python3
"""End-to-end checks of spec 12.3 against the running stack (standard library only).

Usage: python3 scripts/smoke.py [--only section,section] [--list]
Defaults target the native harness; GC_* variables (see scripts/gclib.py) target a deployment.
"""

import argparse
import hashlib
import json
import sys
import time
import traceback

import gclib as g

SECTIONS = {}


def section(name):
    def register(func):
        SECTIONS[name] = func
        return func

    return register


class Ctx:
    """Values shared by the sections of one run."""

    def __init__(self):
        self.s = g.settings()
        self.run = g.run_id()
        self.project = f"smoke-{self.run}"
        # Faro logs only accept the projects of PROJECTS (any well-formed one when it is empty).
        allowed = [p for p in self.s.get("PROJECTS", "").split(",") if p]
        self.faro_project = allowed[0] if allowed else self.project
        # Faro service names are closed: FARO_SERVICES plus the services of HOST_MAP.
        services = [x for x in self.s.get("FARO_SERVICES", "").split(",") if x]
        services += [e.split("=", 1)[1].split(":", 1)[0] for e in self.s.get("HOST_MAP", "").split(",") if "=" in e]
        self.faro_service = services[0] if services else None

    def resource(self, service, env_attr="deployment.environment.name", env="prod", **extra):
        values = {"project": self.project, env_attr: env, "service.name": f"{service}-{self.run}"}
        values.update(extra)
        return values

    def gateway(self):
        s = self.s
        auth = (s["GC_GATEWAY_USER"], s["GC_GATEWAY_PASSWORD"]) if s["GC_GATEWAY_USER"] else None
        return s["GC_GATEWAY_URL"], (s["GC_GATEWAY_HOST"] or None), auth

    def faro_app(self, environment="preprod", namespace=None):
        if not self.faro_service:
            raise AssertionError("Faro needs an allowed service name: set FARO_SERVICES (or HOST_MAP) like the deployment")
        return {"name": self.faro_service, "namespace": namespace or self.faro_project, "environment": environment, "version": "1.0.0"}

    def logs(self, query, since_s=900):
        return g.loki_entries(self.s["GC_LOKI_URL"], query, since_s)

    def wait_logs(self, query, count=1, timeout=60):
        return g.wait_for(lambda: (lambda e: e if len(e) >= count else None)(self.logs(query)), f"Loki {query}", timeout)

    def wait_trace(self, trace_id, timeout=60):
        return g.wait_for(lambda: g.tempo_trace(self.s["GC_TEMPO_URL"], trace_id), f"Tempo trace {trace_id}", timeout)

    def wait_prom(self, expr, timeout=90):
        return g.wait_for(lambda: g.prom_query(self.s["GC_PROM_URL"], expr), f"Prometheus {expr}", timeout, interval=5)

    def ip_hash(self, ip):
        salt = self.s.get("IP_HASH_SALT")
        if not salt:
            raise AssertionError("IP_HASH_SALT is not set: export the salt of the deployment to check IP digests")
        return hashlib.sha256((salt + ip).encode()).hexdigest()


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


INDEXED = {"project", "env", "service_name"}


# ================================================================== sections
@section("otlp-names")
def otlp_names(c):
    """12.3.1 + 12.3.8: OTLP log/trace/metric via the internal path and alloy-gateway."""
    gateway_url, gateway_host, gateway_auth = c.gateway()
    paths = {"internal": (c.s["GC_OTLP_URL"], None, None), "gateway": (gateway_url, gateway_host, gateway_auth)}
    for path, (url, host, auth) in paths.items():
        trace_id = g.new_trace_id()
        resource = c.resource(f"api-{path}", tenant="acme")
        resource["service.instance.id"] = "instance-1"
        g.send_otlp(url, "logs", g.otlp_logs(resource, f"hello from {path}", trace_id=trace_id, severity="ERROR"), host, auth)
        g.send_otlp(url, "traces", g.otlp_traces(resource, [g.span(trace_id, "GET /x", {"http.route": "/x"})]), host, auth)
        g.send_otlp(url, "metrics", g.otlp_sum(resource, f"smoke_{c.run}_{path}_requests", 3), host, auth)
        service = resource["service.name"]

        labels, line, meta = c.wait_logs(f'{{service_name="{service}"}}')[0]
        expect(set(labels) == INDEXED, f"{path}: Loki indexed labels {sorted(labels)} != {sorted(INDEXED)}")
        expect(labels == {"project": c.project, "env": "prod", "service_name": service}, f"{path}: labels {labels}")
        expect(meta.get("tenant") == "acme", f"{path}: tenant metadata {meta}")
        expect(meta.get("trace_id") == trace_id, f"{path}: trace_id metadata {meta}")
        expect(meta.get("detected_level") == "error", f"{path}: detected_level {meta}")

        resource_attrs, _spans = g.trace_resources_and_spans(c.wait_trace(trace_id))[0]
        expect(resource_attrs.get("env") == "prod" and resource_attrs.get("project") == c.project, f"{path}: Tempo resource {resource_attrs}")
        expect("deployment.environment.name" not in resource_attrs, f"{path}: original env attribute kept in Tempo")

        series = c.wait_prom(f'smoke_{c.run}_{path}_requests_total{{job="{service}"}}')
        metric = series[0]["metric"]
        expect((metric.get("project"), metric.get("env"), metric.get("tenant")) == (c.project, "prod", "acme"), f"{path}: metric labels {metric}")
    series = g.get_json(c.s["GC_LOKI_URL"] + "/loki/api/v1/series", {"match[]": f'{{project="{c.project}"}}', "start": str(g.now_ns() - 900 * 10**9)})["data"]
    extra = {key for labels in series for key in labels} - INDEXED
    expect(not extra, f"12.3.8: unexpected Loki indexed labels {sorted(extra)}")


@section("reject")
def reject(c):
    """12.3.5: data without project or env, empty or with an env other than prod/preprod, is dropped and counted."""
    alloy = c.s["GC_ALLOY_METRICS_URL"]
    names = {
        "logs": "otelcol_processor_filter_logs_filtered_total",
        "traces": "otelcol_processor_filter_spans_filtered_total",
        "metrics": "otelcol_processor_filter_datapoints_filtered_total",
    }
    before = {signal: g.metric_value(g.scrape(alloy), metric) for signal, metric in names.items()}
    resource = {"deployment.environment.name": "prod", "service.name": f"noproject-{c.run}"}
    trace_id = g.new_trace_id()
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(resource, "no project"))
    g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(resource, [g.span(trace_id, "orphan")]))
    g.send_otlp(c.s["GC_OTLP_URL"], "metrics", g.otlp_sum(resource, f"smoke_{c.run}_orphan", 1))
    invalid = {
        "noenv": {"project": c.project},
        "emptyproject": {"project": "", "deployment.environment.name": "prod"},
        "emptyenv": {"project": c.project, "deployment.environment.name": ""},
        "staging": {"project": c.project, "deployment.environment.name": "staging"},
    }
    for name, values in invalid.items():
        g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs({**values, "service.name": f"{name}-{c.run}"}, f"invalid {name}"))

    def increased():
        text = g.scrape(alloy)
        now = {signal: g.metric_value(text, metric) for signal, metric in names.items()}
        return now if now["logs"] >= before["logs"] + 1 + len(invalid) and all(now[k] > before[k] for k in names) else None

    g.wait_for(increased, "filter counters", timeout=30)
    time.sleep(5)
    expect(not c.logs(f'{{service_name="noproject-{c.run}"}}'), "log without project reached Loki")
    for name in invalid:
        expect(not c.logs(f'{{service_name="{name}-{c.run}"}}'), f"log {name} reached Loki")
    expect(g.tempo_trace(c.s["GC_TEMPO_URL"], trace_id) is None, "trace without project reached Tempo")
    expect(not g.prom_query(c.s["GC_PROM_URL"], f"smoke_{c.run}_orphan_total"), "metric without project reached Prometheus")


@section("masking")
def masking(c):
    """12.3.4 (OTLP path): secret keys dropped, Bearer/secrets redacted, emails, cards in free text, IPs hashed; no false positives."""
    epoch_ms = "1790559058622"
    body = (
        f"run {c.run} mail bob@example.com from 203.0.113.9 and 2001:db8::7 "
        f"card 4111 1111 1111 1111 at {epoch_ms} time 01:30:29 App\\User::find header Bearer abc.def-ghi password=hunter2x "
        'Authorization: Basic dXNlcjpwYXNz access_token=at7Kx2q {"refresh_token":"rt9Zq4w"} api_key=ak3Rt5y '
        "input_tokens=42 tokenizer=bpe"
    )
    attributes = {
        "created_ms": epoch_ms,
        "event.timestamp": epoch_ms,
        "user_id": "4111111111111111",
        "message": f"attr message {epoch_ms}",
        "http.request.header.authorization": ["Basic dXNlcjpwYXNz"],
        "http.request.header.cookie": ["session=ck5Tn8e"],
        "db.password": "hunter2",
        "client.address": "198.51.100.23",
        "user_agent.original": "Mozilla/5.0 Chrome/128.0.0.0 Safari/537.36",
        "browser.version": "128.0.0.0",
        "net.peer.name": "128.0.0.0",
        # Secret-key boundary: a word of the list ending the key, or followed by a separator or a
        # capital, is a secret; inside a longer word it is not.
        "access_token": "sk1Access",
        "client_secret": "sk2Client",
        "x-api-token": "sk3Xapi",
        "api_key": "sk4Apikey",
        "accessToken": "sk5Camel",
        "gen_ai.usage.input_tokens": "42",
        "tokenizer": "bpe",
        "secretary": "alice",
    }
    map_body = {
        "message": f"run {c.run} card 4111 1111 1111 1111 from eve@example.com",
        "client_ip": "203.0.113.77",
        "api_token": "tk9Vb3m",
        "note": "Bearer mb7Hs1k",
        "user_agent": "Mozilla/5.0 Chrome/128.0.0.0",
        "created_ms": epoch_ms,
        "clientSecret": "sk6Map",
        "tokenizer": "bpe",
    }
    resource = c.resource("masking")
    map_resource = c.resource("masking-map")
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(resource, body, attributes))
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(map_resource, map_body))
    _labels, line, meta = c.wait_logs(f'{{service_name="{resource["service.name"]}"}}')[0]
    _labels, map_line, _meta = c.wait_logs(f'{{service_name="{map_resource["service.name"]}"}}')[0]

    problems = []

    def check(condition, message):
        if not condition:
            problems.append(message)

    # String body: every secret gone, every marker present, no false positive.
    secrets = ("bob@example.com", "203.0.113.9", "2001:db8::7", "4111 1111 1111 1111", "abc.def-ghi", "hunter2x", epoch_ms)
    for secret in (*secrets, "dXNlcjpwYXNz", "at7Kx2q", "rt9Zq4w", "ak3Rt5y"):
        check(secret not in line, f"{secret!r} survived in the log body")
    markers = ("[email]", "[card]", "Bearer [redacted]", "password=[redacted]", "Authorization: [redacted]")
    markers += ("access_token=[redacted]", '"refresh_token":"[redacted]"', "api_key=[redacted]")
    for marker in (*markers, c.ip_hash("203.0.113.9"), c.ip_hash("2001:db8::7")):
        check(marker in line, f"{marker!r} missing from the log body")
    for kept in ("01:30:29", "App\\User::find", "input_tokens=42", "tokenizer=bpe"):
        check(kept in line, f"false positive: {kept!r} was altered in the log body")

    # Attributes: secret keys dropped whatever their type (header values are string arrays).
    for key in ("http_request_header_authorization", "http_request_header_cookie", "db_password"):
        check(key not in meta, f"secret attribute {key} survived: {meta.get(key)!r}")
    check(meta.get("created_ms") == epoch_ms, f"epoch under *_ms was masked: {meta.get('created_ms')!r}")
    check(meta.get("event_timestamp") == epoch_ms, f"epoch under *timestamp* was masked: {meta.get('event_timestamp')!r}")
    check(meta.get("user_id") == "4111111111111111", f"*_id value was masked: {meta.get('user_id')!r}")
    check(meta.get("message") == "attr message [card]", f"epoch in message attribute not masked: {meta.get('message')!r}")
    check(meta.get("client_address") == c.ip_hash("198.51.100.23"), f"client.address not hashed: {meta.get('client_address')!r}")
    # Review focus 1: a browser version is not an address, whatever the key holding it says.
    check(meta.get("user_agent_original") == attributes["user_agent.original"], f"user agent hashed: {meta.get('user_agent_original')!r}")
    check(meta.get("browser_version") == "128.0.0.0", f"version hashed: {meta.get('browser_version')!r}")
    check(meta.get("net_peer_name") == c.ip_hash("128.0.0.0"), f"same value under another key not hashed: {meta.get('net_peer_name')!r}")
    for value in ("sk1Access", "sk2Client", "sk3Xapi", "sk4Apikey", "sk5Camel"):
        check(value not in meta.values(), f"secret-named attribute kept: {value!r}")
    for key, value in (("gen_ai_usage_input_tokens", "42"), ("tokenizer", "bpe"), ("secretary", "alice")):
        check(meta.get(key) == value, f"false positive: attribute {key} = {meta.get(key)!r}, expected {value!r}")

    # Map body (top level): same rules as attributes.
    for secret in ("eve@example.com", "203.0.113.77", "tk9Vb3m", "mb7Hs1k", "4111 1111 1111 1111"):
        check(secret not in map_line, f"{secret!r} survived in the map body")
    try:
        mapped = json.loads(map_line)
    except ValueError:
        mapped = {}
        problems.append(f"map body is not a JSON line: {map_line!r}")
    check("api_token" not in mapped, f"secret key api_token survived in the map body: {mapped.get('api_token')!r}")
    check(mapped.get("message") == f"run {c.run} card [card] from [email]", f"map body message: {mapped.get('message')!r}")
    check(mapped.get("client_ip") == c.ip_hash("203.0.113.77"), f"map body client_ip: {mapped.get('client_ip')!r}")
    check(mapped.get("note") == "Bearer [redacted]", f"map body note: {mapped.get('note')!r}")
    check(mapped.get("user_agent") == map_body["user_agent"], f"map body user agent altered: {mapped.get('user_agent')!r}")
    check(mapped.get("created_ms") == epoch_ms, f"map body epoch under *_ms masked: {mapped.get('created_ms')!r}")
    check("clientSecret" not in mapped, f"camelCase secret key survived in the map body: {mapped.get('clientSecret')!r}")
    check(mapped.get("tokenizer") == "bpe", f"false positive: map body tokenizer {mapped.get('tokenizer')!r}")

    expect(not problems, f"{len(problems)} problem(s):\n    " + "\n    ".join(problems) + f"\n  body: {line}\n  map body: {map_line}\n  metadata: {meta}")


@section("masking-nested")
def masking_nested(c):
    """12.3.4 (OTLP path, nested values): nested map bodies, string arrays and scope attributes are masked."""
    epoch_ms = "1790559058622"
    nested_body = {
        "message": f"run {c.run} nested",
        "user": {"profile": {"email": "nest@example.com", "ip": "203.0.113.88", "note": "Bearer nb4Kd2p"}, "credentials": {"password": "pw7Nested"}},
        "tags": ["tag-a", "list@example.com"],
        "client": {"browser": {"version": "128.0.0.0"}, "stats": {"count": 3, "created_ms": epoch_ms}},
        "request": {"headers": {"x-trace-token": "xt6Nest"}},
        "error": {"message": "charge 4111 1111 1111 1111 refused"},
    }
    attributes = {
        "emails": ["arr@example.com", "plain"],
        "peers": ["198.51.100.44"],
        "ctx": {"inner": {"mail": "deep@example.com"}},
        "tokenizer": "bpe",
        "error.message": "charge 4111 1111 1111 1111 refused",
    }
    scope = {"owner.email": "scope@example.com", "scope.token": "st5Scope", "scope.host": "198.51.100.45", "lib.version": "128.0.0.0"}
    resource = c.resource("masking-nested")
    trace_id = g.new_trace_id()
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(resource, nested_body, attributes, scope=scope))
    list_resource = c.resource("masking-list")
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(list_resource, [f"run {c.run}", "listbody@example.com", {"api_key": "lk8List"}]))
    span_attributes = {"http.request.header.accept": ["text/html", "span@example.com"], "db.meta": {"conn": {"password": "sp9Span"}}}
    span_attributes["error.message"] = "charge 4111 1111 1111 1111 refused"
    g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(resource, [g.span(trace_id, "GET /nested", span_attributes)], scope=scope))
    # Nothing sensitive: every map keeps its original shape (arrays stay arrays, nested maps stay nested).
    clean_resource = c.resource("masking-clean")
    clean_resource["process.command_args"] = ["/usr/bin/app", "--port", "8080"]
    clean_attributes = {"tags": ["alpha", "beta"], "ctx": {"inner": {"step": "checkout"}}}
    clean_trace_id = g.new_trace_id()
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(clean_resource, f"run {c.run} clean", clean_attributes))
    clean_span = {"http.request.header.accept": ["text/html", "application/json"], "http.route": "/clean"}
    g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(clean_resource, [g.span(clean_trace_id, "GET /clean", clean_span)]))
    _labels, line, meta = c.wait_logs(f'{{service_name="{resource["service.name"]}"}}')[0]
    _labels, list_line, _meta = c.wait_logs(f'{{service_name="{list_resource["service.name"]}"}}')[0]
    _labels, _clean_line, clean_meta = c.wait_logs(f'{{service_name="{clean_resource["service.name"]}"}}')[0]
    trace = c.wait_trace(trace_id)
    clean_trace = c.wait_trace(clean_trace_id)

    problems = []

    def check(condition, message):
        if not condition:
            problems.append(message)

    # Map body, two levels deep: the rules see the dotted leaves.
    for secret in ("nest@example.com", "203.0.113.88", "nb4Kd2p", "pw7Nested", "list@example.com", "xt6Nest"):
        check(secret not in line, f"{secret!r} survived in the nested map body")
    try:
        body = json.loads(line)
    except ValueError:
        body = {}
        problems.append(f"nested map body is not a JSON line: {line!r}")
    check(body.get("user.profile.email") == "[email]", f"nested email: {body.get('user.profile.email')!r}")
    check(body.get("user.profile.ip") == c.ip_hash("203.0.113.88"), f"nested IP: {body.get('user.profile.ip')!r}")
    check(body.get("user.profile.note") == "Bearer [redacted]", f"nested Bearer: {body.get('user.profile.note')!r}")
    check(not any("password" in key or "token" in key for key in body), f"nested secret key survived: {sorted(body)}")
    check(body.get("tags.1") == "[email]" and body.get("tags.0") == "tag-a", f"body string array: {body.get('tags.0')!r}, {body.get('tags.1')!r}")
    # False-positive guards, nested: a version is not an address, numbers keep their type, *_ms is no card.
    check(body.get("client.browser.version") == "128.0.0.0", f"nested version hashed: {body.get('client.browser.version')!r}")
    check(body.get("client.stats.count") == 3, f"nested int altered: {body.get('client.stats.count')!r}")
    check(body.get("client.stats.created_ms") == epoch_ms, f"nested epoch masked: {body.get('client.stats.created_ms')!r}")

    check(body.get("error.message") == "charge [card] refused", f"nested error.message card: {body.get('error.message')!r}")
    check(meta.get("error_message") == "charge [card] refused", f"error.message attribute card: {meta.get('error_message')!r}")

    # Attributes: string arrays and nested maps are masked element-wise (flattened: emails.0...).
    check(meta.get("emails_0") == "[email]" and meta.get("emails_1") == "plain", f"attribute string array: {meta}")
    check(meta.get("peers_0") == c.ip_hash("198.51.100.44"), f"attribute IP array: {meta.get('peers_0')!r}")
    check(meta.get("ctx_inner_mail") == "[email]", f"nested attribute map: {meta.get('ctx_inner_mail')!r}")
    check(meta.get("tokenizer") == "bpe", f"false positive: tokenizer {meta.get('tokenizer')!r}")
    # Scope attributes (Loki stores them as structured metadata).
    for secret in ("scope@example.com", "st5Scope", "198.51.100.45", "arr@example.com", "deep@example.com"):
        check(secret not in meta.values(), f"{secret!r} survived in the log metadata")
    check(meta.get("owner_email") == "[email]", f"scope email: {meta.get('owner_email')!r}")
    check(meta.get("scope_host") == c.ip_hash("198.51.100.45"), f"scope IP: {meta.get('scope_host')!r}")
    check(meta.get("lib_version") == "128.0.0.0", f"scope version hashed: {meta.get('lib_version')!r}")

    # A list body becomes its JSON text, then the string rules apply.
    for secret in ("listbody@example.com", "lk8List"):
        check(secret not in list_line, f"{secret!r} survived in the list body: {list_line!r}")

    # Traces: span arrays, nested span maps, scope attributes.
    for resource_spans in trace.get("resourceSpans", []):
        for scope_spans in resource_spans.get("scopeSpans", []):
            scope_attrs = g.otlp_attr_map(scope_spans.get("scope", {}).get("attributes"))
            check(scope_attrs.get("owner.email") == "[email]", f"Tempo scope email: {scope_attrs}")
            check("scope.token" not in scope_attrs, f"Tempo scope secret key kept: {scope_attrs}")
            for span in scope_spans.get("spans", []):
                span_attrs = g.otlp_attr_map(span.get("attributes"))
                check(span_attrs.get("http.request.header.accept.1") == "[email]", f"Tempo span array: {span_attrs}")
                check(span_attrs.get("http.request.header.accept.0") == "text/html", f"Tempo span array: {span_attrs}")
                check("sp9Span" not in json.dumps(span_attrs), f"Tempo nested secret kept: {span_attrs}")
                check(span_attrs.get("error.message") == "charge [card] refused", f"Tempo span error.message: {span_attrs}")

    # Clean record: nothing is reshaped (a masked record only is written back flattened).
    check(clean_meta.get("tags") == '["alpha","beta"]' and "tags_0" not in clean_meta, f"clean log array reshaped: {clean_meta}")
    check(clean_meta.get("ctx_inner_step") == "checkout", f"clean nested attribute lost: {clean_meta}")
    for resource_attrs, spans in g.trace_resources_and_spans(clean_trace):
        args = resource_attrs.get("process.command_args")
        check(isinstance(args, dict) and len(args.get("values", [])) == 3, f"clean process.command_args reshaped in Tempo: {resource_attrs}")
        check("process.command_args.0" not in resource_attrs, f"clean resource flattened in Tempo: {resource_attrs}")
        for span_attrs in spans:
            accept = [v.get("stringValue") for v in (span_attrs.get("http.request.header.accept") or {}).get("values", [])]
            check(accept == ["text/html", "application/json"], f"clean span array reshaped in Tempo: {span_attrs}")

    expect(not problems, f"{len(problems)} problem(s):\n    " + "\n    ".join(problems) + f"\n  body: {line}\n  list body: {list_line}\n  metadata: {meta}")


# 12.3.2: the reference table of spec 6.4 (harness values of HOST_MAP, RESERVED_SUBDOMAINS,
# TENANT_HOST_REGEX), plus the validation of the client tenant on an unknown host (spec 6.5):
# (page host, tenant sent by the client, service_name, env, stored tenant). None = absent;
# "client" = the app name (an allowed Faro service); the client environment is "preprod".
HOST_CASES = [
    ("example.me", "clienttenant", "web-front", "prod", None),
    ("www.example.me", "clienttenant", "client", "prod", None),
    ("api-dev.example.me", "clienttenant", "client", "preprod", None),
    ("acme.example.me", "clienttenant", "client", "prod", "acme"),
    ("acme-dev.example.app", "clienttenant", "client", "preprod", "acme"),
    ("inconnu.autre.org", "clienttenant", "client", "preprod", "clienttenant"),
    ("ACME.Example.ME", "clienttenant", "client", "prod", "acme"),
    ("inconnu.autre.org", "Bad Tenant", "client", "preprod", None),
    ("inconnu.autre.org", "www", "client", "preprod", None),
    ("198.51.100.7", "clienttenant", "client", "preprod", "clienttenant"),
]


@section("faro-hosts")
def faro_hosts(c):
    """12.3.2: env/tenant/service deduced from the page host (spec 6.4), validated client values otherwise."""
    for index, (host, client_tenant, service, env, tenant) in enumerate(HOST_CASES):
        token = f"host-{index}-{c.run}"
        payload = g.faro_payload(c.faro_app(), f"https://{host}/path?q=1", logs=[g.faro_log(token)], session_attributes={"tenant": client_tenant})
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
        labels, line, meta = c.wait_logs(f'{{project="{c.faro_project}"}} |= "{token}"')[0]
        expected_service = c.faro_service if service == "client" else service
        expect(labels.get("service_name") == expected_service, f"{host}: service_name {labels.get('service_name')} != {expected_service}")
        expect(labels.get("env") == env, f"{host}: env {labels.get('env')} != {env}")
        expect(meta.get("tenant") == tenant, f"{host} ({client_tenant}): tenant {meta.get('tenant')} != {tenant}")
        if host[0].isdigit():
            expect(host not in line and c.ip_hash(host) in line, f"page_url IP not hashed: {line}")


# Desktop client (Electron): page_url is app://<service>/<route>, a host that is neither in
# HOST_MAP nor matched by TENANT_HOST_REGEX, so env and tenant are the client's, validated
# (spec 6.4 last row, 6.5). (client environment, client tenant, stored tenant); None = absent.
DESKTOP_CASES = [
    ("prod", "acme", "acme"),
    ("preprod", "acme", "acme"),
    ("prod", None, None),
    ("prod", "Acme Corp", None),
    ("prod", "RESERVED", None),
]


@section("faro-desktop")
def faro_desktop(c):
    """12.3.2 (desktop): an app:// page keeps the client env and tenant once validated; a forged tenant is
    removed, a forged env or an unlisted service is dropped with its reason, on logs and traces."""
    page_url = f"app://{c.faro_service}/actifs/{{id}}"
    reserved = next((r for r in c.s.get("RESERVED_SUBDOMAINS", "").split(",") if r), None)
    for index, (env, client_tenant, tenant) in enumerate(DESKTOP_CASES):
        if client_tenant == "RESERVED":
            if not reserved:
                continue
            client_tenant = reserved
        token = f"desktop-{index}-{c.run}"
        attributes = {"tenant": client_tenant} if client_tenant else {}
        payload = g.faro_payload(c.faro_app(environment=env), page_url, logs=[g.faro_log(token)], session_attributes=attributes)
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
        labels, line, meta = c.wait_logs(f'{{project="{c.faro_project}"}} |= "{token}"')[0]
        expect(labels == {"project": c.faro_project, "env": env, "service_name": c.faro_service}, f"desktop ({env}, {client_tenant}): labels {labels}")
        expect(meta.get("tenant") == tenant, f"desktop ({env}, {client_tenant}): tenant {meta.get('tenant')} != {tenant}")
        expect(f"page_url={page_url}" in line, f"desktop page_url rewritten: {line}")
    alloy = c.s["GC_ALLOY_METRICS_URL"]
    metric = "loki_process_dropped_lines_total"
    app = c.faro_app(environment="prod")
    cases = {
        "invalid_env": dict(app, environment="staging"),
        "missing_env": {k: v for k, v in app.items() if k != "environment"},
        "unknown_service": dict(app, name=f"unlisted-{c.run}"),
    }
    before = {reason: g.metric_value(g.scrape(alloy), metric, {"reason": reason}) for reason in cases}
    for reason, case_app in cases.items():
        payload = g.faro_payload(case_app, page_url, logs=[g.faro_log(f"desktop-{reason}-{c.run}")], session_attributes={"tenant": "acme"})
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    def counted():
        text = g.scrape(alloy)
        return all(g.metric_value(text, metric, {"reason": r}) >= before[r] + 1 for r in cases)

    g.wait_for(counted, f"{metric} +1 for {sorted(cases)}", timeout=30)
    for reason in cases:
        stored = [e for label in sorted(INDEXED) for e in c.logs(f'{{{label}=~".+"}} |= "desktop-{reason}-{c.run}"')]
        expect(not stored, f"desktop line {reason} stored: {stored}")
    # Traces: the desktop rewrites its resource (deployment.environment.name, service.*, tenant).
    trace_id = g.new_trace_id()
    resource = {"service.name": c.faro_service, "service.namespace": c.faro_project, "deployment.environment.name": "preprod", "tenant": "acme"}
    payload = g.faro_payload(app, page_url, traces=g.otlp_traces(resource, [g.span(trace_id, "GET", kind=3)]))
    g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    resource_attrs, _ = g.trace_resources_and_spans(c.wait_trace(trace_id))[0]
    expect(resource_attrs.get("env") == "preprod" and resource_attrs.get("tenant") == "acme", f"desktop trace resource: {resource_attrs}")
    expect(resource_attrs.get("project") == c.faro_project, f"desktop trace project: {resource_attrs}")
    expect(resource_attrs.get("service.name") == c.faro_service, f"desktop trace service: {resource_attrs}")
    # A forged env or tenant on a trace: the span is dropped (env) or the tenant removed.
    filtered, default_filter = "otelcol_processor_filter_spans_filtered_total", {"component_id": "otelcol.processor.filter.default"}
    before_spans = g.metric_value(g.scrape(alloy), filtered, default_filter)
    staging_id, forged_id = g.new_trace_id(), g.new_trace_id()
    for tid, env, tenant in ((staging_id, "staging", "acme"), (forged_id, "prod", "Acme Corp")):
        forged = dict(resource, **{"deployment.environment.name": env, "tenant": tenant})
        payload = g.faro_payload(app, page_url, traces=g.otlp_traces(forged, [g.span(tid, "GET", kind=3)]))
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    forged_attrs, _ = g.trace_resources_and_spans(c.wait_trace(forged_id))[0]
    expect("tenant" not in forged_attrs, f"forged desktop tenant kept: {forged_attrs}")
    g.wait_for(lambda: g.metric_value(g.scrape(alloy), filtered, default_filter) >= before_spans + 1, f"{filtered} {default_filter} +1", timeout=30)
    expect(g.tempo_trace(c.s["GC_TEMPO_URL"], staging_id) is None, "desktop trace with env=staging reached Tempo")


@section("faro-names")
def faro_names(c):
    """12.3.1 + 12.3.9 (logs): Faro logs use the Loki names of the OTLP path; app.* mapping."""
    trace_id = g.new_trace_id()
    token = f"names-{c.run}"
    payload = g.faro_payload(c.faro_app(environment="prod"), "https://inconnu.autre.org/", logs=[g.faro_log(token, "warn", trace_id)])
    g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    labels, _line, meta = c.wait_logs(f'{{project="{c.faro_project}"}} |= "{token}"')[0]
    expect(set(labels) == INDEXED, f"Faro indexed labels {sorted(labels)}")
    expect(labels == {"project": c.faro_project, "env": "prod", "service_name": c.faro_service}, f"Faro labels {labels}")
    expect(meta.get("trace_id") == trace_id, f"trace_id not normalised from traceID: {meta}")
    expect(meta.get("detected_level") == "warn", f"detected_level: {meta}")
    # A traceID that is not 32 lowercase hex digits is not stored (it would carry client text).
    bad_token = f"badtrace-{c.run}"
    payload = g.faro_payload(c.faro_app(environment="prod"), "https://inconnu.autre.org/", logs=[g.faro_log(bad_token, "info", "bob@example.com")])
    g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    _labels, _line, meta = c.wait_logs(f'{{project="{c.faro_project}"}} |= "{bad_token}"')[0]
    expect("trace_id" not in meta, f"unvalidated trace_id stored: {meta}")


@section("faro-reject")
def faro_reject(c):
    """12.3.5 (Faro path): lines with a missing, malformed or unknown project, a missing or unexpected env, or
    a malformed, missing or unlisted service name are dropped, each with its reason; a listed service is kept."""
    alloy = c.s["GC_ALLOY_METRICS_URL"]
    metric = "loki_process_dropped_lines_total"
    app = {k: v for k, v in c.faro_app(environment="prod").items() if k != "version"}
    # case name -> (expected reason, app); no app.name on a host absent from HOST_MAP leaves no
    # service_name at all, which must still be counted as invalid_service.
    cases = {
        "missing_project": ("missing_project", {k: v for k, v in app.items() if k != "namespace"}),
        "invalid_project": ("invalid_project", dict(app, namespace="Bad_Project")),
        "missing_env": ("missing_env", {k: v for k, v in app.items() if k != "environment"}),
        "invalid_env": ("invalid_env", dict(app, environment="staging")),
        "invalid_service": ("invalid_service", dict(app, name="bad name!")),
        "missing_service": ("invalid_service", {k: v for k, v in app.items() if k != "name"}),
        # A well-formed name the client chose, absent from FARO_SERVICES and HOST_MAP.
        "unknown_service": ("unknown_service", dict(app, name=f"unlisted-{c.run}")),
    }
    if c.s.get("PROJECTS"):
        cases["unknown_project"] = ("unknown_project", dict(app, namespace=f"unlisted-{c.run}"))
    reasons = {reason for reason, _app in cases.values()}
    before = {reason: g.metric_value(g.scrape(alloy), metric, {"reason": reason}) for reason in reasons}
    for name, (_reason, case_app) in cases.items():
        payload = g.faro_payload(case_app, "https://inconnu.autre.org/", logs=[g.faro_log(f"{name}-{c.run}")])
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    expected = {reason: sum(1 for r, _app in cases.values() if r == reason) for reason in reasons}

    def counted():
        text = g.scrape(alloy)
        return all(g.metric_value(text, metric, {"reason": r}) >= before[r] + expected[r] for r in reasons)

    g.wait_for(counted, f"{metric} increments {expected}", timeout=30)
    # Positive control: the same line with a listed service is stored.
    payload = g.faro_payload(app, "https://inconnu.autre.org/", logs=[g.faro_log(f"listed_service-{c.run}")])
    g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    labels, _line, _meta = c.wait_logs(f'{{project="{c.faro_project}"}} |= "listed_service-{c.run}"')[0]
    expect(labels.get("service_name") == c.faro_service, f"listed service stored as {labels}")
    for name in cases:
        # A stored line lacks one of the labels: search by each of them.
        stored = [e for label in sorted(INDEXED) for e in c.logs(f'{{{label}=~".+"}} |= "{name}-{c.run}"')]
        expect(not stored, f"Faro line {name} stored: {stored}")


@section("ip-parity")
def ip_parity(c):
    """12.3.4: an IP gets the same digest in Faro and OTLP logs, in every Faro value but the technical keys."""
    ip = "192.0.2.77"
    token = f"parity-{c.run}"
    secrets = "Authorization: Basic dXNlcjpwYXNz access_token=at7Kx2q Bearer fb3Qw8r mail bob@example.com"
    context = {"ip": ip, "db_password": "hunter2x", "a:b": f"{ip} password=hunter2"}
    # Secret-key boundary, same rule as the OTLP path.
    context.update({"client_secret": "fk1Client", "x-api-token": "fk2Xapi", "apiKey": "fk3Camel"})
    context.update({"input_tokens": "42", "tokenizer": "bpe", "secretary": "alice"})
    log = g.faro_log(f"{token} from {ip} card 4111 1111 1111 1111 {secrets}", context=context)
    event = g.faro_event(f"{token}-event", {"x": ip})
    browser = {"name": "chrome", "version": "128.0.0.0"}
    faro = g.faro_payload(c.faro_app(environment="prod"), f"https://{ip}/login", logs=[log], events=[event], browser=browser)
    g.send_faro(c.s["GC_FARO_URL"], faro, c.s["FARO_API_KEY"])
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(c.resource("parity"), f"{token} from {ip}"))
    lines = [line for _l, line, _m in c.wait_logs(f'{{project=~"{c.project}|{c.faro_project}"}} |= "{token}"', count=3)]
    digest = c.ip_hash(ip)
    for line in lines:
        expect(ip not in line and digest in line, f"IP not hashed as sha256(salt+ip): {line}")
    faro_log = next(line for line in lines if "kind=log" in line)
    expect(f"{token} from {digest} card [card] " in faro_log, f"card not masked in Faro message: {faro_log}")
    # A logfmt key outside [A-Za-z0-9_.-] is still one pair: IP hashed, quoted value kept whole.
    expect(f'context_a:b="{digest} password=[redacted]"' in faro_log, f"context_a:b value rewritten: {faro_log}")
    expect(f"context_ip={digest}" in faro_log, f"context_ip not hashed: {faro_log}")
    # Same free-text secret rules as the OTLP path; a secret-named key is dropped, not redacted.
    for secret in ("dXNlcjpwYXNz", "at7Kx2q", "fb3Qw8r", "bob@example.com", "hunter2x", "context_db_password", "fk1Client", "fk2Xapi", "fk3Camel"):
        expect(secret not in faro_log, f"{secret!r} survived in the Faro line: {faro_log}")
    for marker in ("Authorization: [redacted]", "access_token=[redacted]", "Bearer [redacted]", "[email]", "context_input_tokens=42", "context_tokenizer=bpe"):
        expect(marker in faro_log, f"{marker!r} missing from the Faro line: {faro_log}")
    expect("context_secretary=alice" in faro_log, f"false positive: context_secretary: {faro_log}")
    expect(f"page_url=https://{digest}/login" in faro_log, f"page_url not hashed: {faro_log}")
    expect("browser_version=128.0.0.0" in faro_log, f"review focus 1: browser version hashed: {faro_log}")
    faro_event = next(line for line in lines if "kind=event" in line)
    expect(f"event_data_x={digest}" in faro_event, f"event_data_x not hashed: {faro_event}")


@section("faro-traces")
def faro_traces(c):
    """12.3.3 + 12.3.9 (traces): tenant/env validation, legacy deployment.environment -> env; a trace whose
    project is absent from PROJECTS or whose service is not an allowed Faro service is dropped."""
    alloy = c.s["GC_ALLOY_METRICS_URL"]
    metric, faro_filter = "otelcol_processor_filter_spans_filtered_total", {"component_id": "otelcol.processor.filter.faro"}
    before = g.metric_value(g.scrape(alloy), metric, faro_filter)
    dropped = {"unknown_service": (c.faro_project, f"unlisted-{c.run}")}
    if c.s.get("PROJECTS"):
        dropped["unknown_project"] = (f"unlisted-{c.run}", c.faro_service)
    hm_services = [e.split("=", 1)[1].split(":", 1)[0] for e in c.s.get("HOST_MAP", "").split(",") if "=" in e]
    if hm_services:
        # The dots of a HOST_MAP host are literal in the derived pattern.
        host, _rest = c.s["HOST_MAP"].split(",")[0].split("=", 1)
        dropped["regex_service"] = (c.faro_project, host.replace(".", "X") + "=" + hm_services[0])
    dropped_ids = {}
    for name, (project, service) in dropped.items():
        dropped_ids[name] = g.new_trace_id()
        bad = {"service.name": service, "service.namespace": project, "deployment.environment": "prod"}
        traces = g.otlp_traces(bad, [g.span(dropped_ids[name], name, kind=3)])
        payload = g.faro_payload(c.faro_app(environment="prod"), "https://acme.example.me/", traces=traces)
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    g.wait_for(lambda: g.metric_value(g.scrape(alloy), metric, faro_filter) >= before + len(dropped), f"{metric} {faro_filter} +{len(dropped)}", timeout=30)
    trace_id = g.new_trace_id()
    resource = {"service.name": c.faro_service, "service.namespace": c.faro_project, "deployment.environment": "prod", "tenant": "ACME"}
    spans = [
        g.span(trace_id, "valid", {"tenant": "acme"}, kind=3),
        g.span(trace_id, "reserved", {"tenant": "www"}, kind=3),
        g.span(trace_id, "badformat", {"tenant": "Acme Corp"}, kind=3),
    ]
    payload = g.faro_payload(c.faro_app(environment="prod"), "https://acme.example.me/", traces=g.otlp_traces(resource, spans))
    g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    resource_attrs, _ = g.trace_resources_and_spans(c.wait_trace(trace_id))[0]
    expect(resource_attrs.get("project") == c.faro_project, f"project not mapped from service.namespace: {resource_attrs}")
    expect(resource_attrs.get("env") == "prod", f"env not moved from deployment.environment: {resource_attrs}")
    expect("deployment.environment" not in resource_attrs, f"legacy attribute kept: {resource_attrs}")
    expect("tenant" not in resource_attrs, f"malformed resource tenant kept: {resource_attrs}")
    trace = g.tempo_trace(c.s["GC_TEMPO_URL"], trace_id)
    by_name = {s["name"]: g.otlp_attr_map(s.get("attributes")) for rs in trace["resourceSpans"] for sc in rs["scopeSpans"] for s in sc["spans"]}
    expect(by_name["valid"].get("tenant") == "acme", f"valid tenant removed: {by_name['valid']}")
    expect("tenant" not in by_name["reserved"], f"reserved tenant kept: {by_name['reserved']}")
    expect("tenant" not in by_name["badformat"], f"malformed tenant kept: {by_name['badformat']}")
    for name, dropped_id in dropped_ids.items():
        expect(g.tempo_trace(c.s["GC_TEMPO_URL"], dropped_id) is None, f"Faro trace with {name} reached Tempo")
    if hm_services:
        # A service of HOST_MAP is allowed on Faro traces too.
        hm_trace = g.new_trace_id()
        hm_resource = {"service.name": hm_services[0], "service.namespace": c.faro_project, "deployment.environment.name": "prod"}
        traces = g.otlp_traces(hm_resource, [g.span(hm_trace, "hm", kind=3)])
        payload = g.faro_payload(c.faro_app(environment="prod"), "https://acme.example.me/", traces=traces)
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
        hm_attrs, _ = g.trace_resources_and_spans(c.wait_trace(hm_trace))[0]
        expect(hm_attrs.get("service.name") == hm_services[0], f"HOST_MAP service trace: {hm_attrs}")


@section("spanmetrics")
def spanmetrics(c):
    """12.3.6: span-metrics carry tenant whether it is a resource or a span attribute."""
    cases = {"res": ({"tenant": "t-res"}, {}), "span": ({}, {"tenant": "t-span"})}
    for name, (resource_extra, span_attrs) in cases.items():
        trace_id = g.new_trace_id()
        resource = c.resource(f"sm-{name}", **resource_extra)
        attributes = {"http.route": "/{tenant}/assets/{id}", **span_attrs}
        g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(resource, [g.span(trace_id, "GET /{tenant}/assets/{id}", attributes)]))
    for name, tenant in (("res", "t-res"), ("span", "t-span")):
        expr = f'traces_spanmetrics_calls_total{{service="sm-{name}-{c.run}", tenant="{tenant}"}}'
        metric = c.wait_prom(expr, timeout=150)[0]["metric"]
        expect(metric.get("project") == c.project and metric.get("env") == "prod", f"span-metrics labels {metric}")
        expect(metric.get("http_route") == "/{tenant}/assets/{id}", f"http.route dimension {metric}")


@section("otlp-metrics")
def otlp_metrics(c):
    """12.3.7: project, env, tenant are labels of the metric (not only target_info); service is job.

    The resource carries service.namespace like an OTel SDK does: Prometheus would otherwise turn
    job into "namespace/name" and break the dashboards filtering on job."""
    resource = c.resource("metrics", tenant="acme", **{"service.namespace": c.project})
    name = f"smoke_{c.run}_orders"
    g.send_otlp(c.s["GC_OTLP_URL"], "metrics", g.otlp_sum(resource, name, 7))
    metric = c.wait_prom(f"{name}_total")[0]["metric"]
    expect(metric.get("job") == resource["service.name"], f"job != service.name: {metric}")
    expect((metric.get("project"), metric.get("env"), metric.get("tenant")) == (c.project, "prod", "acme"), f"labels {metric}")
    expect("service_name" not in metric, f"service.name promoted as a duplicate label: {metric}")
    # Masking on data point attributes: an email inside a string array is masked (the attribute map is
    # then written back flattened); a clean array keeps its shape (one label holding the whole array).
    masked_name, clean_name = f"smoke_{c.run}_masked", f"smoke_{c.run}_clean"
    g.send_otlp(c.s["GC_OTLP_URL"], "metrics", g.otlp_sum(resource, masked_name, 1, {"owner.emails": ["ops@example.com", "plain"]}))
    g.send_otlp(c.s["GC_OTLP_URL"], "metrics", g.otlp_sum(resource, clean_name, 1, {"zones": ["eu-1", "eu-2"]}))
    masked = c.wait_prom(f"{masked_name}_total")[0]["metric"]
    clean = c.wait_prom(f"{clean_name}_total")[0]["metric"]
    expect(not any("ops@example.com" in value for value in masked.values()), f"email in a data point array survived: {masked}")
    expect(masked.get("owner_emails_0") == "[email]" and masked.get("owner_emails_1") == "plain", f"data point array not masked element-wise: {masked}")
    expect("zones_0" not in clean and "eu-1" in clean.get("zones", ""), f"clean data point array reshaped: {clean}")


@section("correlation")
def correlation(c):
    """12.3.10: from a log of each path, trace_id finds the trace; from the trace, the logs."""
    sys.path.insert(0, str(g.ROOT / "config" / "grafana-setup"))
    import setup  # noqa: PLC0415 - the query Grafana is provisioned with

    trace_id = g.new_trace_id()
    resource = c.resource("corr")
    g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(resource, [g.span(trace_id, "server")]))
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(resource, f"otlp-corr-{c.run}", trace_id=trace_id))
    faro = g.faro_payload(c.faro_app(environment="prod"), "https://inconnu.autre.org/", logs=[g.faro_log(f"faro-corr-{c.run}", trace_id=trace_id)])
    g.send_faro(c.s["GC_FARO_URL"], faro, c.s["FARO_API_KEY"])
    for token in (f"otlp-corr-{c.run}", f"faro-corr-{c.run}"):
        _labels, _line, meta = c.wait_logs(f'{{project=~"{c.project}|{c.faro_project}"}} |= "{token}"')[0]
        expect(meta.get("trace_id") == trace_id, f"{token}: trace_id metadata {meta}")
        expect(c.wait_trace(meta["trace_id"]), f"{token}: trace not found from the log")
    query = setup.TRACE_TO_LOGS_QUERY.replace("${__trace.traceId}", trace_id)
    lines = [line for _l, line, _m in c.wait_logs(query, count=2)]
    expect(any(f"otlp-corr-{c.run}" in line for line in lines), f"trace -> logs misses the OTLP log: {lines}")
    expect(any(f"faro-corr-{c.run}" in line for line in lines), f"trace -> logs misses the Faro log: {lines}")


@section("retention")
def retention(c):
    """12.3.11: effective retention of Loki, Tempo and Prometheus matches the variables."""
    s = c.s
    loki = g.http("GET", s["GC_LOKI_URL"] + "/config").body.decode()
    limits = loki[loki.index("\nlimits_config:") :]
    default = g.yaml_section_value(limits.lstrip("\n"), "limits_config", "retention_period")
    expect(g.duration_seconds(default) == g.duration_seconds(s["LOKI_RETENTION_DEFAULT"]), f"Loki default retention {default}")
    stream_period = g.yaml_section_value(limits.lstrip("\n"), "limits_config", "period")
    expect(g.duration_seconds(stream_period) == g.duration_seconds(s["LOKI_RETENTION_PROD"]), f"Loki prod retention {stream_period}")
    expect("selector: '{env=\"prod\"}'" in limits, "Loki retention_stream selector for env=prod missing")
    tempo = g.http("GET", s["GC_TEMPO_URL"] + "/status/config").body.decode()
    block = g.yaml_section_value(tempo, "compactor", "block_retention")
    expect(g.duration_seconds(block) == g.duration_seconds(s["TEMPO_RETENTION"]), f"Tempo block_retention {block}")
    flags = g.get_json(s["GC_PROM_URL"] + "/api/v1/status/flags")["data"]
    expect(flags["storage.tsdb.retention.time"] == s["PROM_RETENTION_TIME"], f"Prometheus retention.time {flags['storage.tsdb.retention.time']}")
    size = flags["storage.tsdb.retention.size"]
    expect(g.size_bytes(size) == g.size_bytes(s["PROM_RETENTION_SIZE"]), f"Prometheus retention.size {size}")


# --- end of sections ---


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify end-to-end checks (spec 12.3)")
    parser.add_argument("--only", help="comma-separated sections")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(SECTIONS))
        return 0
    names = args.only.split(",") if args.only else list(SECTIONS)
    unknown = [n for n in names if n not in SECTIONS]
    if unknown:
        print(f"smoke: unknown sections {unknown}; known: {list(SECTIONS)}", file=sys.stderr)
        return 2
    ctx = Ctx()
    failed = 0
    for name in names:
        started = time.monotonic()
        try:
            SECTIONS[name](ctx)
            print(f"smoke: [PASS] {name} ({time.monotonic() - started:.1f}s)")
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            failed += 1
            print(f"smoke: [FAIL] {name}: {exc}")
            if not isinstance(exc, AssertionError):
                traceback.print_exc()
    print(f"smoke: {len(names) - failed}/{len(names)} sections passed (run {ctx.run})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
