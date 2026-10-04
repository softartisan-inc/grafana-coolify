"""grafana-setup (plan B): contact points, notification policy and alert rules (spec 10.4).

Standard library only; downloaded and verified by setup.py, which then calls prepare() (every
variable validated before the first write of plan B) and provision(). Every call goes through the legacy provisioning API
/api/v1/provisioning/* (deprecated, still served by Grafana 12 and 13), one function per object
type, so that the future switch to its replacement only touches these functions. Every write
carries X-Disable-Provenance: true, so the objects stay editable in the Grafana UI.
"""

import re

PROVENANCE = {"X-Disable-Provenance": "true"}
PROVISIONING = "/api/v1/provisioning"
FOLDER_UID = "gc-grafana-coolify"
RULE_GROUP = "gc-base"
TELEGRAM_UID = "gc-telegram"
EMAIL_UID = "gc-email"
DEFAULT_GROUP_BY = ["grafana_folder", "alertname"]
# Routing (spec D9): severity=critical AND env=prod -> Telegram; everything else -> email.
# Grafana stores object_matchers sorted by label name: the desired route uses the same order.
TELEGRAM_MATCHERS = [["env", "=", "prod"], ["severity", "=", "critical"]]
DEFAULTS = {
    "ALERT_ERROR_RATE": "0.05",
    "ALERT_P95_MS": "1500",
    "ALERT_SILENCE_MIN": "15",
    "ALERT_DISK_PCT": "80",
    "CARDINALITY_ALERT_THRESHOLD": "200000",
}
ENVS = ("prod", "preprod")
EMAIL_RE = re.compile(r"^[^@\s;,]+@[^@\s;,]+\.[^@\s;,]+$")
CHAT_ID_RE = re.compile(r"^(-?[0-9]+|@[A-Za-z0-9_]{5,})$")
# Request spans only (server, consumer): client and internal spans would count calls twice.
REQUEST_KINDS = 'span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER"'
# Real file systems of the host, same filter as the Hôte dashboard (scripts/build_dashboards.py).
HOST_FS = 'fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs|autofs|fuse.*"'
# Rejection counters of spec 6.3. Each term is computed per metric (increase() over several
# metric names would clash on identical label sets); `m unless m offset 15m` adds the series born
# in the window, whose first sample increase() cannot see (loki_process_dropped_lines_total
# appears at 1 on the first drop of a reason).
REJECTION_METRICS = (
    "otelcol_processor_filter_spans_filtered_total",
    "otelcol_processor_filter_logs_filtered_total",
    "otelcol_processor_filter_datapoints_filtered_total",
    "loki_process_dropped_lines_total",
)
# Fields of an alert rule compared to decide whether it must be rewritten (Grafana adds id,
# updated, keep_firing_for...). The desired data carries the defaults Grafana stores
# (intervalMs, maxDataPoints, queryType, relativeTimeRange), so that equal means unchanged.
RULE_FIELDS = ("title", "folderUID", "ruleGroup", "condition", "data", "noDataState", "execErrState", "for", "labels", "annotations", "isPaused")


class AlertingError(ValueError):
    """Invalid alerting variable (reported by setup.py as a clean stop)."""


# ------------------------------------------------------------------ variables
def number(env, name, kind, low, high):
    raw = (env.get(name) or "").strip() or DEFAULTS[name]
    try:
        value = kind(raw)
    except ValueError:
        raise AlertingError(f"{name}={raw!r} is not a valid {kind.__name__}") from None
    if not low < value <= high:
        raise AlertingError(f"{name}={raw!r} is out of range ({low} < value <= {high})")
    return value


def thresholds(env):
    return {
        "error_rate": number(env, "ALERT_ERROR_RATE", float, 0, 1),
        "p95_ms": number(env, "ALERT_P95_MS", int, 0, 600_000),
        "silence_min": number(env, "ALERT_SILENCE_MIN", int, 0, 1440),
        "disk_pct": number(env, "ALERT_DISK_PCT", int, 0, 99),
        "cardinality": number(env, "CARDINALITY_ALERT_THRESHOLD", int, 0, 100_000_000),
        "host_env": host_env(env),
    }


def host_env(env):
    """env label of the host-level rules (disk): HOST_ENV, prod by default (same rule as config-guard)."""
    value = (env.get("HOST_ENV") or "").strip() or "prod"
    if value not in ENVS:
        raise AlertingError(f"HOST_ENV={value!r} must be prod or preprod")
    return value


def notifications(env):
    """{"telegram": {"token", "chat_id"} or None, "emails": [...]} from the environment."""
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    emails = [item.strip() for item in re.split(r"[;,]", env.get("ALERT_EMAILS") or "") if item.strip()]
    for email in emails:
        if not EMAIL_RE.match(email):
            raise AlertingError(f"ALERT_EMAILS: invalid address {email!r}")
    if bool(token) != bool(chat_id):
        raise AlertingError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID go together: set both or neither")
    if chat_id and not CHAT_ID_RE.match(chat_id):
        raise AlertingError(f"TELEGRAM_CHAT_ID={chat_id!r}: expected a numeric chat id (-100...) or @channel")
    if token and not emails:
        raise AlertingError("ALERT_EMAILS is required when Telegram is set: every alert that is not critical+prod goes by email")
    telegram = {"chat_id": chat_id, "token": token} if token else None
    return {"telegram": telegram, "emails": emails}


# ------------------------------------------------------------------ contact points
def contact_points(notify):
    """Desired contact points (an empty list when ALERT_EMAILS is empty)."""
    points = []
    telegram = notify["telegram"]
    if telegram:
        settings = {"bottoken": telegram["token"], "chatid": telegram["chat_id"]}
        points.append({"uid": TELEGRAM_UID, "name": TELEGRAM_UID, "type": "telegram", "settings": settings, "disableResolveMessage": False})
    if notify["emails"]:
        settings = {"addresses": ";".join(notify["emails"]), "singleEmail": True}
        points.append({"uid": EMAIL_UID, "name": EMAIL_UID, "type": "email", "settings": settings, "disableResolveMessage": False})
    return points


def current_contact_points(api):
    """{uid: contact point} with decrypted settings, so that the bot token is compared too."""
    payload = api.expect("GET", f"{PROVISIONING}/contact-points/export?decrypt=true&format=json")
    current = {}
    for point in (payload or {}).get("contactPoints", []):
        for receiver in point.get("receivers", []):
            current[receiver["uid"]] = {
                "uid": receiver["uid"],
                "name": point["name"],
                "type": receiver["type"],
                "settings": receiver.get("settings") or {},
                "disableResolveMessage": bool(receiver.get("disableResolveMessage")),
            }
    return current


def ensure_contact_point(api, desired, current):
    existing = current.get(desired["uid"])
    if existing is None:
        api.expect("POST", f"{PROVISIONING}/contact-points", desired, ok=(200, 201, 202), headers=PROVENANCE)
        return "created"
    if existing == desired:
        return "unchanged"
    api.expect("PUT", f"{PROVISIONING}/contact-points/{desired['uid']}", desired, ok=(200, 202), headers=PROVENANCE)
    return "updated"


# ------------------------------------------------------------------ notification policy
def managed_route(route):
    """Our route is any route to gc-telegram, whatever its matchers: a stale one (matchers changed)
    is replaced. Routes added by hand must target their own contact points, not gc-telegram."""
    return route.get("receiver") == TELEGRAM_UID


def desired_policy(current, notify):
    """The policy tree with our root receiver and Telegram route first; routes added by hand are kept
    after ours, and the settings added by hand to our route (continue, group_wait, mute_time_intervals...)
    are kept too: only its receiver and matchers are managed."""
    current_routes = current.get("routes") or []
    existing = next((route for route in current_routes if managed_route(route)), {})
    routes = [route for route in current_routes if not managed_route(route)]
    if notify["telegram"]:
        routes.insert(0, dict(existing, receiver=TELEGRAM_UID, object_matchers=TELEGRAM_MATCHERS))
    policy = {key: value for key, value in current.items() if key not in ("routes", "provenance")}
    policy["receiver"] = EMAIL_UID
    policy["group_by"] = current.get("group_by") or DEFAULT_GROUP_BY
    if routes:
        policy["routes"] = routes
    return policy


def ensure_policy(api, notify):
    current = api.expect("GET", f"{PROVISIONING}/policies") or {}
    desired = desired_policy(current, notify)
    comparable = {key: value for key, value in current.items() if key != "provenance"}
    if comparable == desired:
        return "unchanged"
    api.expect("PUT", f"{PROVISIONING}/policies", desired, ok=(200, 202), headers=PROVENANCE)
    return "updated"


# ------------------------------------------------------------------ alert rules
def query(expr, window_s, datasource_uid):
    model = {"refId": "A", "expr": expr, "instant": True, "range": False, "intervalMs": 1000, "maxDataPoints": 43200}
    return {"refId": "A", "queryType": "", "relativeTimeRange": {"from": window_s, "to": 0}, "datasourceUid": datasource_uid, "model": model}


def threshold(value):
    model = {
        "refId": "C",
        "type": "threshold",
        "expression": "A",
        "conditions": [{"evaluator": {"type": "gt", "params": [value]}}],
        "intervalMs": 1000,
        "maxDataPoints": 43200,
    }
    return {"refId": "C", "queryType": "", "relativeTimeRange": {"from": 0, "to": 0}, "datasourceUid": "__expr__", "model": model}


def rule(uid, title, expr, above, severity, pending, summary, datasource_uid, window_s=900, labels=None):
    return {
        "uid": uid,
        "title": title,
        "folderUID": FOLDER_UID,
        "ruleGroup": RULE_GROUP,
        "condition": "C",
        "data": [query(expr, window_s, datasource_uid), threshold(above)],
        # No data is not an incident (a quiet night); a broken query is (Error state).
        "noDataState": "OK",
        "execErrState": "Error",
        "for": pending,
        "labels": dict(labels or {}, severity=severity),
        "annotations": {"summary": summary},
        "isPaused": False,
    }


def rejection_expr(window="15m"):
    terms = []
    for metric in REJECTION_METRICS:
        terms.append(f"(sum(increase({metric}[{window}])) or vector(0))")
        terms.append(f"(sum({metric} unless {metric} offset {window}) or vector(0))")
    return " + ".join(terms)


def silence_expr(minutes):
    """Services seen during the lookback window (at least one hour) but without any span for `minutes`."""
    lookback = max(60, 4 * minutes)
    by = "sum by (project, env, service)"
    seen = f"{by} (increase(traces_spanmetrics_calls_total[{lookback}m])) > 0"
    alive = f"{by} (increase(traces_spanmetrics_calls_total[{minutes}m])) > 0"
    return f"{seen} unless {alive}"


def rules(limits, prometheus_uid):
    """The six base rules of spec 10.4, thresholds from the environment."""
    calls = f"traces_spanmetrics_calls_total{{{REQUEST_KINDS}}}"
    errors = f'traces_spanmetrics_calls_total{{{REQUEST_KINDS}, status_code="STATUS_CODE_ERROR"}}'
    by = "sum by (project, env, service)"
    error_rate = f"{by} (rate({errors}[5m])) / {by} (rate({calls}[5m]))"
    p95 = f"1000 * histogram_quantile(0.95, sum by (le, project, env, service) (rate(traces_spanmetrics_latency_bucket{{{REQUEST_KINDS}}}[10m])))"
    disk = f"max by (device, fstype) (100 * (1 - node_filesystem_avail_bytes{{{HOST_FS}}} / node_filesystem_size_bytes{{{HOST_FS}}}))"
    rate_pct = f"{limits['error_rate'] * 100:g} %"
    return [
        rule(
            "gc-error-rate",
            "Taux d'erreur",
            error_rate,
            limits["error_rate"],
            "critical",
            "2m",
            f"{{{{ $labels.service }}}} ({{{{ $labels.env }}}}) : plus de {rate_pct} de requêtes en erreur sur 5 min",
            prometheus_uid,
        ),
        rule(
            "gc-latency",
            "Latence p95",
            p95,
            limits["p95_ms"],
            "warning",
            "2m",
            f"{{{{ $labels.service }}}} ({{{{ $labels.env }}}}) : p95 au-dessus de {limits['p95_ms']} ms sur 10 min",
            prometheus_uid,
        ),
        rule(
            "gc-silence",
            "Service muet",
            silence_expr(limits["silence_min"]),
            0,
            "critical",
            "0s",
            f"{{{{ $labels.service }}}} ({{{{ $labels.env }}}}) : aucune donnée depuis {limits['silence_min']} min",
            prometheus_uid,
            window_s=max(3600, 240 * limits["silence_min"]),
        ),
        # The host serves HOST_ENV (prod by default): a full disk is an incident of that env
        # (env=prod, hence Telegram).
        rule(
            "gc-disk",
            "Disque",
            disk,
            limits["disk_pct"],
            "critical",
            "2m",
            f"Disque {{{{ $labels.device }}}} au-dessus de {limits['disk_pct']} %",
            prometheus_uid,
            labels={"env": limits["host_env"]},
        ),
        rule(
            "gc-cardinality",
            "Cardinalité",
            "max(prometheus_tsdb_head_series)",
            limits["cardinality"],
            "warning",
            "2m",
            f"Plus de {limits['cardinality']} séries actives dans Prometheus",
            prometheus_uid,
        ),
        rule(
            "gc-rejections",
            "Rejets",
            rejection_expr(),
            0,
            "warning",
            "0s",
            "Des données ont été rejetées sur 15 min (tableau « Santé du pipeline »)",
            prometheus_uid,
        ),
    ]


def ensure_rule(api, desired):
    path = f"{PROVISIONING}/alert-rules/{desired['uid']}"
    status, current = api.request("GET", path)
    if status == 404:
        api.expect("POST", f"{PROVISIONING}/alert-rules", desired, ok=(200, 201), headers=PROVENANCE)
        return "created"
    if status != 200 or not isinstance(current, dict):
        raise api.error("GET", path, status, current)
    if all(current.get(field) == desired[field] for field in RULE_FIELDS):
        return "unchanged"
    api.expect("PUT", path, desired, ok=(200,), headers=PROVENANCE)
    return "updated"


# ------------------------------------------------------------------ entry points (setup.py)
def prepare(env, prometheus_uid):
    """Validate every alerting variable: (notify, rules). Raises AlertingError before any write."""
    return notifications(env), rules(thresholds(env), prometheus_uid)


def provision(api, prepared, out):
    notify, desired_rules = prepared
    points = contact_points(notify)
    if points:
        current = current_contact_points(api)
        for point in points:
            out(f"grafana-setup: contact point {point['uid']}: {ensure_contact_point(api, point, current)}")
        out(f"grafana-setup: notification policy: {ensure_policy(api, notify)}")
    else:
        out("grafana-setup: notifications: skipped (ALERT_EMAILS is empty: the rules notify nobody)")
    for item in desired_rules:
        out(f"grafana-setup: alert rule {item['uid']}: {ensure_rule(api, item)}")
