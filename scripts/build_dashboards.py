#!/usr/bin/env python3
"""Build the seven dashboards of spec 10.3 into config/grafana-setup/dashboards/<uid>.json.

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
TEMPO = {"type": "tempo", "uid": "gc-tempo"}
MIXED = {"type": "datasource", "uid": "-- Mixed --"}
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
def prom_target(ref, expr, legend="", instant=False):
    """A range query, or with `instant` a single value over the dashboard range ($__range)."""
    item = {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend, "range": True}
    if instant:
        item.update(instant=True, range=False)
    return item


def loki_target(ref, expr, legend="", instant=False):
    return {"refId": ref, "datasource": LOKI, "expr": expr, "legendFormat": legend, "queryType": "instant" if instant else "range"}


def tempo_search(ref, query, limit=20):
    return {"refId": ref, "datasource": TEMPO, "queryType": "traceql", "query": query, "limit": limit, "tableType": "traces"}


def tempo_service_map(ref, matchers):
    """Service graph of the Tempo datasource (built by Grafana from gc-prometheus, front end only)."""
    return {"refId": ref, "datasource": TEMPO, "queryType": "serviceMap", "serviceMapQuery": matchers}


# Instant series side by side in one table: labels as columns, one row per series.
TABLE_TRANSFORMS = [
    {"id": "labelsToFields", "options": {}},
    {"id": "merge", "options": {}},
    {"id": "organize", "options": {"excludeByName": {"Time": True}}},
]


def panel(kind, title, targets, unit="short", w=12, h=8, description="", custom=None):
    sources = {target["datasource"]["uid"] for target in targets}
    datasource = targets[0]["datasource"] if len(sources) == 1 else MIXED
    item = {"type": kind, "title": title, "datasource": datasource, "targets": targets, "gridPos": {"w": w, "h": h}}
    if description:
        item["description"] = description
    if kind in ("timeseries", "stat", "bargauge", "table", "piechart"):
        item["fieldConfig"] = {"defaults": {"unit": unit}, "overrides": []}
        if custom:
            item["fieldConfig"]["defaults"]["custom"] = custom
    if kind == "logs":
        item["options"] = {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True}
    if kind == "table" and datasource is not TEMPO:
        item["transformations"] = TABLE_TRANSFORMS
    return item


def row(title):
    return {"type": "row", "title": title, "collapsed": False, "panels": [], "gridPos": {"w": 24, "h": 1}}


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


# ------------------------------------------------------------------ the seven dashboards
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


# ------------------------------------------------------------------ client applications (Faro)
# Faro lines are logfmt: kind, type, value, stacktrace, event_name, event_data_<attr>,
# value_<key>, context_<key>, session_id, session_attr_<key>, app_version, browser_os.
# Always `| logfmt` without a key list: with one, Loki 3.7 returns wrong results in some metric
# queries (empty `type`, filters matching nothing). Textbox regexes go between backticks.
CLIENT_PARSE = (
    " | logfmt"
    " | label_format os=`{{ or .session_attr_os .browser_os }}`, os_version=`{{ or .session_attr_os_version .browser_os }}`"
    " | app_version=~`$version` | os=~`$os`"
)
CLIENT_LINES = LOKI_SELECTOR + CLIENT_PARSE
CLIENT_EXCEPTIONS = LOKI_SELECTOR + ' |= "kind=exception"' + CLIENT_PARSE + ' | kind="exception"'
CLIENT_EXITS = CLIENT_EXCEPTIONS + ' | type="$exit_type"'
# A crash: a system exit whose reason is a crash, or a fatal exception reported live (the same
# crash reported again at the next launch, context_previous_launch, is not counted twice).
CLIENT_CRASHES = (
    CLIENT_EXCEPTIONS
    + ' | (type="$exit_type" and value=~`$crash_reasons`)'
    + ' or (type!="$exit_type" and context_fatal="true" and context_previous_launch!="true")'
)
CLIENT_API = (
    LOKI_SELECTOR
    + ' |= "kind=event"'
    + CLIENT_PARSE
    + ' | kind="event" | event_name="$api_event"'
    + " | label_format route=event_data_${api_route_key}, status=event_data_${api_status_key},"
    + " method=event_data_${api_method_key}, duration_ms=event_data_${api_duration_key}"
)
# A screen sent either as an event (event_data_<key>) or as a measurement (context_<key>, value_<key>).
CLIENT_SCREENS = (
    CLIENT_LINES
    + ' | (kind="event" and event_name="$screen_event") or (kind="measurement" and type="$screen_event")'
    + " | label_format screen=`{{ or .event_data_${screen_key} .context_${screen_key} }}`,"
    + " screen_ms=`{{ or .event_data_${screen_duration_key} .value_${screen_duration_key} }}`"
)
CRASH_LINE = (
    "`{{ .type }}: {{ .value }} | v{{ .app_version }} | {{ .os }} {{ .os_version }}"
    "{{ if .context_previous_launch }} | lancement précédent{{ end }}\n{{ .stacktrace }}`"
)
SEVERITY = '`{{ if eq .context_fatal "true" }}fatale{{ else if eq .context_handled "true" }}gérée{{ else }}non gérée{{ end }}`'
CLIENT_SPANS = 'project=~"$project", env=~"$env", service=~"$service", tenant=~"$tenant", span_kind="SPAN_KIND_CLIENT"'
CLIENT_CALLS = f"traces_spanmetrics_calls_total{{{CLIENT_SPANS}}}"
CLIENT_ERRORS = f'traces_spanmetrics_calls_total{{{CLIENT_SPANS}, status_code="STATUS_CODE_ERROR"}}'
CLIENT_BUCKET = f"traces_spanmetrics_latency_bucket{{{CLIENT_SPANS}}}"
SERVICE_GRAPH = 'project=~"$project", env=~"$env", client=~"$service"'
GLOBAL = "Global : ces métriques d'alloy ne portent ni projet ni service."
CRASH_FREE = (
    "Approximation : un crash est rapporté au lancement suivant (session et version de ce "
    "lancement) et un crash JS fatal peut aussi produire une sortie système de raison crash, "
    "d'où une borne haute des sessions touchées."
)
NEXT_LAUNCH = (
    "Une sortie système porte la version et l'OS du lancement qui la rapporte : après une mise à jour, un crash de l'ancienne version compte dans la nouvelle."
)


def sessions(window, by=""):
    """Distinct session_id over `window`, optionally per `by`."""
    inner = f'sum by ({by + ", " if by else ""}session_id) (count_over_time({CLIENT_LINES} | session_id!="" [{window}]))'
    return f"count by ({by}) ({inner})" if by else f"count({inner})"


def client_quantile(q, lines, value, window, by):
    """quantile_over_time with its own `by`: a sum around it would add quantiles of distinct series."""
    return f'quantile_over_time({q}, {lines} | unwrap {value} | __error__="" [{window}]) by ({by})'


def client_p_ms(q, by="le, http_route", window="$__rate_interval"):
    return f"1000 * histogram_quantile({q}, sum by ({by}) (rate({CLIENT_BUCKET}[{window}])))"


def client_variables():
    text = [
        ("version", ".*", "version (regex)"),
        ("os", ".*", "os (regex)"),
        ("exit_type", "AppExit", "type de sortie système"),
        ("crash_reasons", "(?i)crash|crash_native", "raisons de crash (regex)"),
        ("api_event", "api_call", "événement d'appel API"),
        ("api_route_key", "route", "clé route"),
        ("api_status_key", "status", "clé statut"),
        ("api_method_key", "method", "clé méthode"),
        ("api_duration_key", "duration_ms", "clé durée (ms)"),
        ("screen_event", "screen_ready", "événement écran prêt"),
        ("screen_key", "screen", "clé écran"),
        ("screen_duration_key", "duration_ms", "clé durée écran (ms)"),
        ("slow_trace", "1s", "trace lente au-delà de"),
    ]
    return [
        loki_variable("project", "project", '{project=~".+"}', all_value=".+", multi=False),
        loki_variable("env", "env", '{project=~"$project"}'),
        loki_variable("service", "service_name", '{project=~"$project", env=~"$env"}'),
        # Loki does not list structured metadata: tenants come from the client spans' metrics.
        prom_variable("tenant", "tenant", 'traces_spanmetrics_calls_total{project=~"$project", env=~"$env"}'),
        *(textbox(name, default, label) for name, default, label in text),
    ]


def client_apps():
    bars = {"drawStyle": "bars", "fillOpacity": 80, "stacking": {"mode": "normal", "group": "A"}}
    stacked = {"stacking": {"mode": "normal", "group": "A"}, "fillOpacity": 30}
    api_errors = f'sum(count_over_time({CLIENT_API} | status=~"5..|0" [$__range])) / sum(count_over_time({CLIENT_API} [$__range]))'
    crashed_sessions = f"count(sum by (session_id) (count_over_time({CLIENT_CRASHES} [$__range])))"
    crashed_sessions_by_version = f"count by (app_version) (sum by (app_version, session_id) (count_over_time({CLIENT_CRASHES} [$__range])))"
    slow_traces = (
        # Explicit :pipe format (a|b), independent of how the Tempo datasource formats multi-values.
        # The project's All value (.+) is a regex too, hence =~ and not =.
        '{ resource.project =~ "${project:pipe}" && resource.env =~ "${env:pipe}" && resource.service.name =~ "${service:pipe}"'
        " && kind = client && duration > $slow_trace }"
        " | select(span.http.route, span.http.response.status_code, span.http.request.method)"
    )

    def api_ratio(status):
        return f"sum(rate({CLIENT_API} | {status} [$__auto])) / sum(rate({CLIENT_API} [$__auto]))"

    def graph_rate(metric, by="client, server"):
        return f"sum by ({by}) (rate({metric}{{{SERVICE_GRAPH}}}[$__rate_interval]))"

    panels = [
        row("État de santé"),
        panel("stat", "Sessions", [loki_target("A", sessions("$__range"), "sessions", instant=True)], w=4, h=5),
        panel(
            "stat",
            "Sessions sans crash",
            [loki_target("A", f"1 - (({crashed_sessions}) or vector(0)) / {sessions('$__range')}", "sans crash", instant=True)],
            "percentunit",
            w=4,
            h=5,
            description=CRASH_FREE,
        ),
        panel(
            "stat", "Crashs par raison", [loki_target("A", f"sum by (value) (count_over_time({CLIENT_EXITS} [$__range]))", "{{value}}", instant=True)], w=4, h=5
        ),
        panel(
            "stat",
            "Taux d'erreur API",
            [loki_target("A", api_errors, "5xx ou sans réponse", instant=True)],
            "percentunit",
            w=4,
            h=5,
            description="Appels API vus du client : statut 5xx, ou 0 (pas de réponse, hors ligne).",
        ),
        panel(
            "stat",
            "Latence API p95",
            [
                prom_target("A", client_p_ms("0.95", "le", "$__range"), "traces", instant=True),
                loki_target("B", client_quantile("0.95", CLIENT_API, "duration_ms", "$__range", "service_name"), "événements", instant=True),
            ],
            "ms",
            w=4,
            h=5,
        ),
        panel(
            "stat",
            "Versions actives",
            [loki_target("A", f'count(sum by (app_version) (count_over_time({CLIENT_LINES} | app_version!="" [$__range])))', "versions", instant=True)],
            w=4,
            h=5,
        ),
        row("Crashs et sorties"),
        panel(
            "timeseries",
            "Sorties système par raison",
            [loki_target("A", f"sum by (value) (count_over_time({CLIENT_EXITS} [$__auto]))", "{{value}}")],
            w=24,
            custom=bars,
        ),
        panel(
            "bargauge",
            "Crashs par version",
            [loki_target("A", f"sum by (app_version) (count_over_time({CLIENT_CRASHES} [$__range]))", "{{app_version}}", instant=True)],
            w=8,
            description=NEXT_LAUNCH,
        ),
        panel(
            "bargauge",
            "Taux de crash par version",
            [loki_target("A", f"{crashed_sessions_by_version} / {sessions('$__range', 'app_version')}", "{{app_version}}", instant=True)],
            "percentunit",
            w=8,
            description="Part des sessions de la version touchées par un crash. " + NEXT_LAUNCH,
        ),
        panel(
            "bargauge",
            "Crashs par OS",
            [loki_target("A", f"sum by (os, os_version) (count_over_time({CLIENT_CRASHES} [$__range]))", "{{os}} {{os_version}}", instant=True)],
            w=8,
            description=NEXT_LAUNCH,
        ),
        panel(
            "timeseries",
            "ANR et mémoire",
            [
                loki_target(
                    "A", f"sum by (value) (count_over_time({CLIENT_EXITS} | value=~`(?i)anr|low_memory|excessive_resource_usage` [$__auto]))", "{{value}}"
                )
            ],
            w=24,
        ),
        panel(
            "logs",
            "Derniers crashs et erreurs fatales",
            [loki_target("A", f'{CLIENT_EXCEPTIONS} | type="$exit_type" or context_fatal="true" | line_format {CRASH_LINE}')],
            w=24,
            h=12,
        ),
        row("Erreurs"),
        panel(
            "table",
            "Exceptions les plus fréquentes",
            [loki_target("A", f'topk(10, sum by (type, value) (count_over_time({CLIENT_EXCEPTIONS} | type!="$exit_type" [$__range])))', instant=True)],
        ),
        panel(
            "timeseries",
            "Exceptions par type",
            [loki_target("A", f'sum by (type) (count_over_time({CLIENT_EXCEPTIONS} | type!="$exit_type" [$__auto]))', "{{type}}")],
        ),
        panel(
            "timeseries",
            "Gérées, non gérées, fatales",
            [
                loki_target(
                    "A",
                    f'sum by (severity) (count_over_time({CLIENT_EXCEPTIONS} | type!="$exit_type" | label_format severity={SEVERITY} [$__auto]))',
                    "{{severity}}",
                )
            ],
            custom=stacked,
            description="Le SDK Faro web ne pose pas context_fatal : ses exceptions tombent en « non gérée ».",
        ),
        panel(
            "timeseries",
            "Fatales du lancement précédent",
            [loki_target("A", f'sum by (type) (count_over_time({CLIENT_EXCEPTIONS} | context_previous_launch="true" [$__auto]))', "{{type}}")],
        ),
        row("API vue du client"),
        panel(
            "timeseries",
            "Débit par route",
            [
                prom_target("A", f"sum by (http_route, span_name) (rate({CLIENT_CALLS}[$__rate_interval]))", "traces {{http_route}} {{span_name}}"),
                loki_target("B", f"sum by (method, route) (rate({CLIENT_API} [$__auto]))", "événements {{method}} {{route}}"),
            ],
            "reqps",
            description="Les spans fetch du web n'ont pas de http.route : ils sont regroupés par nom de span.",
        ),
        panel(
            "timeseries",
            "Taux d'erreur",
            [
                prom_target("A", f"sum(rate({CLIENT_ERRORS}[$__rate_interval])) / sum(rate({CLIENT_CALLS}[$__rate_interval]))", "traces (statut ERROR)"),
                loki_target("B", api_ratio('status=~"5.."'), "événements 5xx"),
                loki_target("C", api_ratio('status="0"'), "événements sans réponse"),
            ],
            "percentunit",
            description="Le code HTTP n'est pas une dimension des span-metrics : 5xx et 0 (hors ligne) viennent des événements.",
        ),
        panel(
            "timeseries",
            "Latence par route",
            [
                prom_target("A", client_p_ms("0.5"), "p50 {{http_route}}"),
                prom_target("B", client_p_ms("0.95"), "p95 {{http_route}}"),
                prom_target("C", client_p_ms("0.99"), "p99 {{http_route}}"),
                loki_target("D", client_quantile("0.95", CLIENT_API, "duration_ms", "$__auto", "route"), "p95 événements {{route}}"),
            ],
            "ms",
            w=24,
        ),
        panel(
            "table",
            "Routes les plus lentes (traces)",
            [
                prom_target("A", f"topk(10, {client_p_ms('0.95', 'le, http_route', '$__range')})", "p95 ms", instant=True),
                prom_target("B", f"sum by (http_route) (increase({CLIENT_CALLS}[$__range]))", "appels", instant=True),
            ],
            description="Value #A : p95 en ms ; Value #B : appels sur la période.",
        ),
        panel(
            "table",
            "Routes les plus lentes (événements)",
            [loki_target("A", f"topk(10, {client_quantile('0.95', CLIENT_API, 'duration_ms', '$__range', 'method, route')})", instant=True)],
            "ms",
        ),
        panel(
            "timeseries",
            "Codes de statut",
            [
                loki_target("A", f"sum by (status) (rate({CLIENT_API} [$__auto]))", "HTTP {{status}}"),
                prom_target("B", f"sum by (status_code) (rate({CLIENT_CALLS}[$__rate_interval]))", "traces {{status_code}}"),
            ],
            "reqps",
            w=24,
            custom=stacked,
        ),
        row("Écrans"),
        panel(
            "timeseries",
            "Écran prêt p50 et p95",
            [
                loki_target("A", client_quantile("0.5", CLIENT_SCREENS, "screen_ms", "$__auto", "screen"), "p50 {{screen}}"),
                loki_target("B", client_quantile("0.95", CLIENT_SCREENS, "screen_ms", "$__auto", "screen"), "p95 {{screen}}"),
            ],
            "ms",
            w=24,
        ),
        panel(
            "table",
            "Écrans les plus lents (p95)",
            [loki_target("A", f"topk(15, {client_quantile('0.95', CLIENT_SCREENS, 'screen_ms', '$__range', 'screen')})", instant=True)],
            "ms",
            w=24,
        ),
        row("Traces"),
        panel(
            "table",
            "Traces client lentes",
            [tempo_search("A", slow_traces)],
            w=24,
            h=10,
            description="Spans client plus longs que $slow_trace ; trier par durée dans le tableau. Pas de filtre tenant : un span sans tenant serait exclu.",
        ),
        panel("nodeGraph", "Graphe client → API", [tempo_service_map("A", '{project=~"$project", env=~"$env", client=~"$service"}')], w=12, h=10),
        panel(
            "timeseries",
            "Débit client → serveur",
            [prom_target("A", graph_rate("traces_service_graph_request_total"), "{{client}} → {{server}}")],
            "reqps",
            w=12,
            h=10,
        ),
        panel(
            "timeseries",
            "Échecs client → serveur",
            [
                prom_target(
                    "A",
                    f"{graph_rate('traces_service_graph_request_failed_total')} / {graph_rate('traces_service_graph_request_total')}",
                    "{{client}} → {{server}}",
                )
            ],
            "percentunit",
            description="Un span client mis en file plus de 10 s après son span serveur ne forme pas de paire.",
        ),
        panel(
            "timeseries",
            "Latence serveur p95 client → serveur",
            [
                prom_target(
                    "A",
                    f"1000 * histogram_quantile(0.95, {graph_rate('traces_service_graph_request_server_seconds_bucket', 'le, client, server')})",
                    "{{client}} → {{server}}",
                )
            ],
            "ms",
        ),
        row("Adoption"),
        panel("timeseries", "Sessions par version", [loki_target("A", sessions("$__auto", "app_version"), "{{app_version}}")], w=24, custom=stacked),
        panel("piechart", "Sessions par OS", [loki_target("A", sessions("$__range", "os, os_version"), "{{os}} {{os_version}}", instant=True)]),
        panel("bargauge", "Sessions par tenant", [loki_target("A", sessions("$__range", "tenant"), "{{tenant}}", instant=True)]),
        row("Santé de l'ingestion"),
        panel(
            "timeseries",
            "Lignes Faro rejetées par raison",
            [
                prom_target(
                    "A",
                    'sum by (reason) (increase(loki_process_dropped_lines_total{job="alloy", component_id="loki.process.faro"}[$__rate_interval]))',
                    "{{reason}}",
                )
            ],
            description=GLOBAL,
        ),
        panel(
            "timeseries",
            "Requêtes Faro par code",
            [
                prom_target(
                    "A", 'sum by (status_code) (rate(faro_receiver_request_duration_seconds_count{job="alloy"}[$__rate_interval]))', "HTTP {{status_code}}"
                )
            ],
            "reqps",
            description="429 : débit limité (FARO_RATE, FARO_BURST). " + GLOBAL,
        ),
        panel(
            "timeseries",
            "Éléments Faro reçus",
            [
                prom_target("A", 'sum(rate(faro_receiver_logs_total{job="alloy"}[$__rate_interval]))', "logs"),
                prom_target("B", 'sum(rate(faro_receiver_exceptions_total{job="alloy"}[$__rate_interval]))', "exceptions"),
                prom_target("C", 'sum(rate(faro_receiver_events_total{job="alloy"}[$__rate_interval]))', "événements"),
                prom_target("D", 'sum(rate(faro_receiver_measurements_total{job="alloy"}[$__rate_interval]))', "mesures"),
            ],
            "ops",
            description=GLOBAL,
        ),
        panel(
            "timeseries",
            "Pertes à l'écriture Loki",
            [
                prom_target(
                    "A",
                    'sum by (reason) (increase(loki_write_dropped_entries_total{job="alloy", component_id="loki.write.loki"}[$__rate_interval])) or vector(0)',
                    "{{reason}}",
                )
            ],
            description=GLOBAL,
        ),
        panel(
            "timeseries",
            "Volume de logs du service",
            [loki_target("A", 'sum by (service_name) (rate({project=~"$project", env=~"$env", service_name=~"$service"} [$__auto]))', "{{service_name}}")],
            "ops",
            w=24,
        ),
    ]
    description = "Crashs, erreurs, API vue du client, écrans, traces et adoption des applications clientes (Faro, traces client)."
    return dashboard("gc-client", "Applications clientes", description, client_variables(), panels)


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


BUILDERS = (project_overview, service_detail, frontend, client_apps, host, pipeline, cardinality)


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
