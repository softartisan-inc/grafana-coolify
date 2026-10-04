#!/usr/bin/env python3
"""Build the six dashboards of spec 10.3 into config/grafana-setup/dashboards/<uid>.json.

Standard library only. The JSON files are generated (never edited by hand) and committed:
grafana-setup downloads them at a pinned commit (spec 4.3). `--check` fails when a committed
file differs from what this script builds.

Label names per store (spec 6.2): the dashboard variable "service" is `service` in the
span-metrics, `job` in the OTLP metrics and `service_name` in Loki.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "grafana-setup" / "dashboards"
SCHEMA_VERSION = 41
TAG = "grafana-coolify"
PROM = {"type": "prometheus", "uid": "gc-prometheus"}
LOKI = {"type": "loki", "uid": "gc-loki"}
# Request spans only: client and internal spans would count every outgoing call twice.
REQUEST_KINDS = 'span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER"'
SPANMETRICS_FILTER = 'project=~"$project", env=~"$env", service=~"$service", tenant=~"$tenant"'
# Real file systems of the host (node-exporter), shared with the disk alert of alerting.py.
HOST_FS = 'fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs|autofs|fuse.*"'
ERRORS = ', status_code="STATUS_CODE_ERROR"'
LOKI_SELECTOR = '{project=~"$project", env=~"$env", service_name=~"$service"} | tenant=~"$tenant"'
# OTLP application metrics: the service is `job` (spec 6.2); span-metrics (traces_*) excluded.
OTLP_SERIES = 'count by (job) ({project=~"$project", env=~"$env", job=~"$service", tenant=~"$tenant", __name__!~"traces_.+"})'
STACK_JOBS = "prometheus|loki|tempo|alloy|alloy-gateway|node-exporter"
QUEUE = 'max by (component_id, data_type) (otelcol_exporter_queue_size{job="alloy"})'
TRACE_LINK = "Chaque log porte trace_id : le lien « Trace » ouvre la trace dans Tempo."


# ------------------------------------------------------------------ variables
def prom_variable(name, label, match, all_value=".*", multi=True):
    return {
        "name": name,
        "label": name,
        "type": "query",
        "datasource": PROM,
        "query": {"qryType": 1, "query": f"label_values({match}, {label})", "refId": "PrometheusVariableQueryEditor-VariableQuery"},
        "definition": f"label_values({match}, {label})",
        "refresh": 2,
        "sort": 1,
        "includeAll": True,
        "allValue": all_value,
        "multi": multi,
        "current": {"text": "All", "value": "$__all"},
    }


def loki_variable(name, label, stream, all_value=".*", multi=True):
    return {
        "name": name,
        "label": name,
        "type": "query",
        "datasource": LOKI,
        "query": {"type": 1, "label": label, "stream": stream, "refId": "LokiVariableQueryEditor-VariableQuery"},
        "definition": f"label_values({stream}, {label})",
        "refresh": 2,
        "sort": 1,
        "includeAll": True,
        "allValue": all_value,
        "multi": multi,
        "current": {"text": "All", "value": "$__all"},
    }


def textbox(name, default, label):
    return {"name": name, "label": label, "type": "textbox", "query": default, "current": {"text": default, "value": default}}


def spanmetrics_variables():
    metric = "traces_spanmetrics_calls_total"
    return [
        prom_variable("project", "project", metric, all_value=".+", multi=False),
        prom_variable("env", "env", f'{metric}{{project=~"$project"}}'),
        prom_variable("service", "service", f'{metric}{{project=~"$project", env=~"$env"}}'),
        prom_variable("tenant", "tenant", f'{metric}{{project=~"$project", env=~"$env", service=~"$service"}}'),
    ]


# ------------------------------------------------------------------ panels
def prom_target(ref, expr, legend=""):
    return {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend, "range": True}


def loki_target(ref, expr, legend=""):
    return {"refId": ref, "datasource": LOKI, "expr": expr, "legendFormat": legend, "queryType": "range"}


def panel(kind, title, targets, unit="short", w=12, h=8, description=""):
    datasource = targets[0]["datasource"]
    item = {"type": kind, "title": title, "datasource": datasource, "targets": targets, "gridPos": {"w": w, "h": h}}
    if description:
        item["description"] = description
    if kind in ("timeseries", "stat"):
        item["fieldConfig"] = {"defaults": {"unit": unit}, "overrides": []}
    if kind == "logs":
        item["options"] = {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True}
    return item


def layout(panels):
    """Assign ids and grid positions: panels flow left to right on a 24-column grid."""
    x = y = row_height = 0
    for number, item in enumerate(panels, 1):
        grid = item["gridPos"]
        if x + grid["w"] > 24:
            x, y, row_height = 0, y + row_height, 0
        grid.update(x=x, y=y)
        item["id"] = number
        x += grid["w"]
        row_height = max(row_height, grid["h"])
    return panels


def dashboard(uid, title, description, variables, panels):
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": [TAG],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,
        "refresh": "1m",
        "schemaVersion": SCHEMA_VERSION,
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": variables},
        "annotations": {"list": []},
        "links": [],
        "panels": layout(panels),
    }


# ------------------------------------------------------------------ the six dashboards
def calls(extra=""):
    selector = f"{SPANMETRICS_FILTER}, {REQUEST_KINDS}{extra}"
    return f"traces_spanmetrics_calls_total{{{selector}}}"


def latency_bucket():
    return f"traces_spanmetrics_latency_bucket{{{SPANMETRICS_FILTER}, {REQUEST_KINDS}}}"


def rate(selector, by=None):
    grouping = f" by ({by})" if by else ""
    return f"sum{grouping} (rate({selector}[$__rate_interval]))"


def error_ratio(by=None):
    return f"{rate(calls(ERRORS), by)} / {rate(calls(), by)}"


def p95_ms(by=None):
    grouping = f"le, {by}" if by else "le"
    return f"1000 * histogram_quantile(0.95, sum by ({grouping}) (rate({latency_bucket()}[$__rate_interval])))"


def project_overview():
    panels = [
        panel("stat", "Débit (requêtes/s)", [prom_target("A", rate(calls()))], "reqps", w=8, h=5),
        panel("stat", "Taux d'erreur", [prom_target("A", error_ratio())], "percentunit", w=8, h=5),
        panel("stat", "Latence p95 (ms)", [prom_target("A", p95_ms())], "ms", w=8, h=5),
        panel("timeseries", "Débit par service", [prom_target("A", rate(calls(), "service"), "{{service}}")], "reqps", w=24),
        panel("timeseries", "Taux d'erreur par service", [prom_target("A", error_ratio("service"), "{{service}}")], "percentunit"),
        panel("timeseries", "Latence p95 par service", [prom_target("A", p95_ms("service"), "{{service}}")], "ms"),
        panel("timeseries", "Métriques applicatives OTLP (séries par service)", [prom_target("A", OTLP_SERIES, "{{job}}")], w=24),
    ]
    return dashboard("gc-project", "Vue projet", "Débit, taux d'erreur et p95 par service (span-metrics de Tempo).", spanmetrics_variables(), panels)


def service_detail():
    error_logs = '{project=~"$project", env=~"$env", service_name=~"$service"} | detected_level=~"error|fatal|critical" | tenant=~"$tenant"'
    panels = [
        panel("timeseries", "Débit par route", [prom_target("A", rate(calls(), "service, http_route"), "{{service}} {{http_route}}")], "reqps", w=24),
        panel("timeseries", "Erreurs par route", [prom_target("A", rate(calls(ERRORS), "service, http_route"), "{{service}} {{http_route}}")], "reqps"),
        panel("timeseries", "Latence p95 par route", [prom_target("A", p95_ms("service, http_route"), "{{service}} {{http_route}}")], "ms"),
        panel("logs", "Logs d'erreur", [loki_target("A", error_logs)], w=24, h=12, description=TRACE_LINK),
    ]
    return dashboard("gc-service", "Erreurs et latence par service", "Détail par route et logs d'erreur liés.", spanmetrics_variables(), panels)


def frontend():
    variables = [
        loki_variable("project", "project", '{project=~".+"}', all_value=".+", multi=False),
        loki_variable("env", "env", '{project=~"$project"}'),
        loki_variable("service", "service_name", '{project=~"$project", env=~"$env"}'),
        textbox("tenant", ".*", "tenant (regex)"),
    ]

    def vital(name, quantile="0.75"):
        return (
            f"quantile_over_time({quantile}, {LOKI_SELECTOR} | logfmt kind, type, {name} "
            f'| kind="measurement" | type="web-vitals" | {name}!="" | unwrap {name} [$__auto]) by (service_name)'
        )

    exceptions = f'{LOKI_SELECTOR} | logfmt kind | kind="exception"'
    panels = [
        panel("timeseries", "Erreurs JS", [loki_target("A", f"sum by (service_name) (count_over_time({exceptions} [$__auto]))", "{{service_name}}")], w=12),
        panel(
            "timeseries",
            "Sessions",
            [loki_target("A", f'count(sum by (session_id) (count_over_time({LOKI_SELECTOR} | logfmt session_id | session_id!="" [$__auto])))', "sessions")],
            w=12,
        ),
        panel(
            "timeseries",
            "LCP et INP p75",
            [loki_target("A", vital("lcp"), "LCP {{service_name}}"), loki_target("B", vital("inp"), "INP {{service_name}}")],
            "ms",
        ),
        panel("timeseries", "CLS p75", [loki_target("A", vital("cls"), "CLS {{service_name}}")]),
        panel("logs", "Dernières erreurs JS", [loki_target("A", exceptions)], w=24, h=12),
    ]
    return dashboard("gc-frontend", "Frontend", "Erreurs JS, Web Vitals et sessions (données Faro dans Loki).", variables, panels)


def host():
    filesystems = f"max by (device, fstype) (100 * (1 - node_filesystem_avail_bytes{{{HOST_FS}}} / node_filesystem_size_bytes{{{HOST_FS}}}))"
    panels = [
        panel("timeseries", "CPU", [prom_target("A", '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))', "CPU")], "percent"),
        panel("timeseries", "Mémoire", [prom_target("A", "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)", "RAM")], "percent"),
        panel("timeseries", "Disque (usage par système de fichiers)", [prom_target("A", filesystems, "{{device}}")], "percent"),
        panel(
            "timeseries",
            "Réseau",
            [
                prom_target("A", 'sum by (device) (rate(node_network_receive_bytes_total{device!="lo"}[$__rate_interval]))', "reçu {{device}}"),
                prom_target("B", 'sum by (device) (rate(node_network_transmit_bytes_total{device!="lo"}[$__rate_interval]))', "émis {{device}}"),
            ],
            "Bps",
        ),
    ]
    return dashboard("gc-host", "Hôte", "CPU, mémoire, disque et réseau du serveur (node-exporter).", [], panels)


def pipeline():
    def increase(metric, by=None):
        grouping = f" by ({by})" if by else ""
        return f"sum{grouping} (increase({metric}[$__rate_interval]))"

    panels = [
        panel(
            "timeseries",
            "Ingestion OTLP (alloy)",
            [
                prom_target("A", 'sum(rate(otelcol_receiver_accepted_spans_total{job="alloy"}[$__rate_interval]))', "spans"),
                prom_target("B", 'sum(rate(otelcol_receiver_accepted_log_records_total{job="alloy"}[$__rate_interval]))', "logs"),
                prom_target("C", 'sum(rate(otelcol_receiver_accepted_metric_points_total{job="alloy"}[$__rate_interval]))', "points de métriques"),
            ],
            "ops",
        ),
        panel(
            "timeseries",
            "Ingestion Faro",
            [
                prom_target("A", "sum(rate(faro_receiver_logs_total[$__rate_interval]))", "logs"),
                prom_target("B", "sum(rate(faro_receiver_exceptions_total[$__rate_interval]))", "exceptions"),
                prom_target("C", "sum(rate(faro_receiver_measurements_total[$__rate_interval]))", "mesures"),
            ],
            "ops",
        ),
        panel(
            "timeseries",
            "Rejets (§ 6.3)",
            [
                prom_target("A", increase("otelcol_processor_filter_spans_filtered_total"), "OTLP traces"),
                prom_target("B", increase("otelcol_processor_filter_logs_filtered_total"), "OTLP logs"),
                prom_target("C", increase("otelcol_processor_filter_datapoints_filtered_total"), "OTLP métriques"),
                prom_target("D", increase("loki_process_dropped_lines_total", "reason"), "Faro {{reason}}"),
            ],
        ),
        panel("timeseries", "File d'envoi d'alloy", [prom_target("A", QUEUE, "{{component_id}} {{data_type}}")]),
        panel("stat", "Services de la stack (up)", [prom_target("A", f'up{{job=~"{STACK_JOBS}"}}', "{{job}}")], w=24, h=5),
        panel(
            "timeseries",
            "Stockages",
            [
                prom_target("A", "sum(rate(loki_distributor_lines_received_total[$__rate_interval]))", "Loki lignes/s"),
                prom_target("B", "sum(rate(tempo_distributor_spans_received_total[$__rate_interval]))", "Tempo spans/s"),
                prom_target("C", "sum(rate(prometheus_tsdb_head_samples_appended_total[$__rate_interval]))", "Prometheus échantillons/s"),
            ],
            "ops",
            w=24,
        ),
    ]
    return dashboard("gc-pipeline", "Santé du pipeline", "Ingestion, rejets, file d'envoi et santé de Loki, Tempo et Prometheus.", [], panels)


def cardinality():
    variables = [prom_variable("project", "project", '{project=~".+"}', all_value=".+", multi=False)]
    panels = [
        panel("stat", "Séries actives (Prometheus)", [prom_target("A", "max(prometheus_tsdb_head_series)")], w=8, h=5),
        panel("stat", "Séries du metrics-generator (Tempo)", [prom_target("A", "sum(tempo_metrics_generator_registry_active_series)")], w=8, h=5),
        panel("stat", "Flux en mémoire (Loki)", [prom_target("A", "sum(loki_ingester_memory_streams)")], w=8, h=5),
        panel("timeseries", "Séries par projet", [prom_target("A", 'count by (project) ({project=~".+"})', "{{project}}")], w=24),
        panel(
            "timeseries",
            "Séries par service",
            [
                prom_target("A", 'topk(20, count by (service) ({project=~"$project", service=~".+"}))', "span-metrics {{service}}"),
                prom_target("B", f'topk(20, count by (job) ({{project=~"$project", job!~"{STACK_JOBS}"}}))', "OTLP {{job}}"),
            ],
        ),
        panel("timeseries", "Séries par tenant", [prom_target("A", 'topk(20, count by (tenant) ({project=~"$project", tenant=~".+"}))', "{{tenant}}")]),
    ]
    return dashboard("gc-cardinality", "Cardinalité", "Séries par projet, service et tenant (garde-fous du § 8.2).", variables, panels)


BUILDERS = (project_overview, service_detail, frontend, host, pipeline, cardinality)


def render(item):
    return json.dumps(item, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def build():
    return {OUT / f"{item['uid']}.json": render(item) for item in (builder() for builder in BUILDERS)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a committed dashboard is stale")
    args = parser.parse_args(argv)
    built = build()
    stale = sorted(path.name for path, text in built.items() if not path.exists() or path.read_text(encoding="utf-8") != text)
    extra = sorted(path.name for path in OUT.glob("*.json") if path not in built) if OUT.exists() else []
    if args.check:
        for name in stale:
            print(f"build_dashboards: stale {name} (run python3 scripts/build_dashboards.py)", file=sys.stderr)
        for name in extra:
            print(f"build_dashboards: unexpected {name} (not built by this script)", file=sys.stderr)
        return 1 if stale or extra else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for path, text in built.items():
        if path.name in stale:
            path.write_text(text, encoding="utf-8")
            print(f"build_dashboards: wrote {path.relative_to(ROOT)}")
    for name in extra:
        (OUT / name).unlink()
        print(f"build_dashboards: removed {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
