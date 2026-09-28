#!/usr/bin/env python3
"""End-to-end checks of spec 12.3 against the running stack (standard library only).

Usage: python3 scripts/smoke.py [--only section,section] [--list]
Defaults target the native harness; GC_* variables (see scripts/gclib.py) target a deployment.
"""

import argparse
import hashlib
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

    def resource(self, service, env_attr="deployment.environment.name", env="prod", **extra):
        values = {"project": self.project, env_attr: env, "service.name": f"{service}-{self.run}"}
        values.update(extra)
        return values

    def gateway(self):
        s = self.s
        auth = (s["GC_GATEWAY_USER"], s["GC_GATEWAY_PASSWORD"]) if s["GC_GATEWAY_USER"] else None
        return s["GC_GATEWAY_URL"], (s["GC_GATEWAY_HOST"] or None), auth

    def faro_app(self, name="web", environment="preprod", namespace=None):
        return {"name": f"{name}-{self.run}", "namespace": namespace or self.faro_project, "environment": environment, "version": "1.0.0"}

    def logs(self, query, since_s=900):
        return g.loki_entries(self.s["GC_LOKI_URL"], query, since_s)

    def wait_logs(self, query, count=1, timeout=60):
        return g.wait_for(lambda: (lambda e: e if len(e) >= count else None)(self.logs(query)), f"Loki {query}", timeout)

    def wait_trace(self, trace_id, timeout=60):
        return g.wait_for(lambda: g.tempo_trace(self.s["GC_TEMPO_URL"], trace_id), f"Tempo trace {trace_id}", timeout)

    def wait_prom(self, expr, timeout=90):
        return g.wait_for(lambda: g.prom_query(self.s["GC_PROM_URL"], expr), f"Prometheus {expr}", timeout, interval=5)

    def ip_hash(self, ip):
        return hashlib.sha256((self.s["IP_HASH_SALT"] + ip).encode()).hexdigest()


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
