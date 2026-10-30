#!/usr/bin/env python3
"""Synthetic data for the plan B checks (spec 12.6): every dashboard panel and every alert rule
gets something to show or to fire on. Standard library only, on top of gclib.

For `duration` seconds, every `tick` seconds:
- service synth-api-<run> (env prod, tenant acme): server spans on a normalised route, half of
  them in error and all of them slow (600 ms), error logs carrying their trace_id, an app metric;
- service synth-quiet-<run> (env preprod): server spans during the first `quiet_after` seconds
  only, then silence (the "Service muet" rule);
- Faro (allowed service of FARO_SERVICES / HOST_MAP): a log, a JS exception and Web Vitals for one
  browser session;
- the same Faro service as a mobile client (dashboard gc-client, default event names): a system
  exit with a crash reason, a fatal error of the previous launch, a handled error, API-call and
  screen-ready events, a screen-ready measurement, os/os_version session attributes; and a
  client span synth-mobile-<run> (http.route, 1.2 s) parent of a synth-api server span, for the
  client span-metrics and the client -> server service graph;
- at the start and again after 35 s (two Prometheus scrapes apart, so increase() sees them): an
  OTLP log without project and a Faro log of an unlisted project (the "Rejets" rule).

Usage: python3 scripts/synth.py [--duration 90] (defaults target the native harness, like smoke.py)
"""

import argparse
import sys
import time

import gclib as g

ROUTE = "/{tenant}/assets/{id}"
SLOW_NS = 600_000_000
CLIENT_NS = 1_200_000_000
# Default names of the gc-client dashboard variables (api_event, screen_event and their keys).
API_EVENT = "api_call"
SCREEN_EVENT = "screen_ready"


def slow_span(trace_id, name, error):
    item = g.span(trace_id, name, {"http.route": ROUTE, "http.request.method": "GET"})
    item["startTimeUnixNano"] = str(int(item["endTimeUnixNano"]) - SLOW_NS)
    item["status"] = {"code": 2 if error else 1}
    return item


def faro_app(s):
    services = [x for x in s.get("FARO_SERVICES", "").split(",") if x]
    services += [e.split("=", 1)[1].split(":", 1)[0] for e in s.get("HOST_MAP", "").split(",") if "=" in e]
    if not services:
        raise AssertionError("synthetic Faro data needs an allowed service: set FARO_SERVICES (or HOST_MAP)")
    return {"name": services[0], "namespace": project_of(s), "environment": "prod", "version": "1.0.0"}


def project_of(s):
    allowed = [p for p in s.get("PROJECTS", "").split(",") if p]
    return allowed[0] if allowed else "synth"


def faro_batch(s, session_id, token):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    payload = g.faro_payload(faro_app(s), "https://inconnu.autre.org/assets", logs=[g.faro_log(f"{token} page view")])
    payload["meta"]["session"]["id"] = session_id
    frames = [{"filename": "app.js", "function": "render", "lineno": 12, "colno": 7}]
    error = f"{token} cannot read properties of undefined"
    payload["exceptions"] = [{"type": "TypeError", "value": error, "timestamp": stamp, "stacktrace": {"frames": frames}}]
    payload["measurements"] = [{"type": "web-vitals", "values": {"lcp": 2600.0, "inp": 240.0, "cls": 0.14, "fcp": 900.0, "ttfb": 300.0}, "timestamp": stamp}]
    return payload


def client_span(server):
    """A mobile client span (kind CLIENT) around `server`, which becomes its child."""
    item = g.span(server["traceId"], "GET", {"http.route": ROUTE, "http.request.method": "GET", "http.response.status_code": 500}, kind=3)
    item["startTimeUnixNano"] = str(int(server["startTimeUnixNano"]) - (CLIENT_NS - SLOW_NS) // 2)
    item["endTimeUnixNano"] = str(int(item["startTimeUnixNano"]) + CLIENT_NS)
    item["status"] = {"code": 2}
    server["parentSpanId"] = item["spanId"]
    return item


def mobile_batch(s, session_id, token, elapsed):
    """A mobile session for gc-client: exits, fatal and handled errors, API and screen events."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    app = dict(faro_app(s), version="2.0.0")
    attributes = {"os": "android", "os_version": "14", "tenant": "acme"}
    events = [
        g.faro_event(API_EVENT, {"route": ROUTE, "method": "GET", "status": status, "duration_ms": str(duration)})
        for status, duration in (("200", 180), ("500", 640), ("0", 3000))
    ]
    events.append(g.faro_event(SCREEN_EVENT, {"screen": "asset-list", "duration_ms": "420"}))
    payload = g.faro_payload(app, "app://mobile-app/assets", logs=[g.faro_log(f"{token} mobile")], session_attributes=attributes, events=events)
    payload["meta"]["session"]["id"] = f"{session_id}-{int(elapsed)}"
    frames = [{"filename": "index.bundle", "function": "load", "lineno": 3, "colno": 9}]
    payload["exceptions"] = [
        {"type": "AppExit", "value": "crash", "timestamp": stamp, "context": {"fatal": "true"}},
        {"type": "AppExit", "value": "anr", "timestamp": stamp, "context": {"fatal": "true"}},
        {"type": "Error", "value": f"{token} fatal", "timestamp": stamp, "stacktrace": {"frames": frames}, "context": {"fatal": "true"}},
        {
            "type": "Error",
            "value": f"{token} fatal",
            "timestamp": stamp,
            "stacktrace": {"frames": frames},
            "context": {"fatal": "true", "previous_launch": "true"},
        },
        {"type": "Error", "value": f"{token} handled", "timestamp": stamp, "stacktrace": {"frames": frames}, "context": {"handled": "true"}},
    ]
    payload["measurements"] = [{"type": SCREEN_EVENT, "values": {"duration_ms": 380}, "context": {"screen": "asset-detail"}, "timestamp": stamp}]
    return payload


def reject(s, run):
    g.send_otlp(s["GC_OTLP_URL"], "logs", g.otlp_logs({"deployment.environment.name": "prod", "service.name": f"synth-noproject-{run}"}, "rejected"))
    app = dict(faro_app(s), namespace=f"unlisted-{run}")
    g.send_faro(s["GC_FARO_URL"], g.faro_payload(app, "https://inconnu.autre.org/", logs=[g.faro_log(f"rejected-{run}")]), s["FARO_API_KEY"])


def generate(s, run, duration=90, tick=5, quiet_after=40, out=print):
    project = project_of(s)
    # service.namespace as an OTel SDK sets it: job must still be service.name (dashboards filter on it).
    api = {"project": project, "deployment.environment.name": "prod", "service.namespace": project, "service.name": f"synth-api-{run}", "tenant": "acme"}
    quiet = {"project": project, "deployment.environment.name": "preprod", "service.name": f"synth-quiet-{run}"}
    mobile = {"project": project, "deployment.environment.name": "prod", "service.name": f"synth-mobile-{run}", "tenant": "acme"}
    session_id = f"synth-{run}"
    started = time.monotonic()
    rejected_twice = False
    reject(s, run)
    while (elapsed := time.monotonic() - started) < duration:
        trace_ids = [g.new_trace_id() for _ in range(4)]
        spans = [slow_span(trace_id, "GET " + ROUTE, error=index % 2 == 0) for index, trace_id in enumerate(trace_ids)]
        client = client_span(spans[0])
        g.send_otlp(s["GC_OTLP_URL"], "traces", g.otlp_traces(api, spans))
        g.send_otlp(s["GC_OTLP_URL"], "traces", g.otlp_traces(mobile, [client]))
        g.send_otlp(s["GC_OTLP_URL"], "logs", g.otlp_logs(api, f"synth-{run} asset lookup failed", trace_id=trace_ids[0], severity="ERROR"))
        g.send_otlp(s["GC_OTLP_URL"], "metrics", g.otlp_sum(api, f"synth_{run}_orders", int(elapsed) + 1))
        if elapsed < quiet_after:
            g.send_otlp(s["GC_OTLP_URL"], "traces", g.otlp_traces(quiet, [slow_span(g.new_trace_id(), "GET " + ROUTE, error=False)]))
        g.send_faro(s["GC_FARO_URL"], faro_batch(s, session_id, f"synth-{run}"), s["FARO_API_KEY"])
        g.send_faro(s["GC_FARO_URL"], mobile_batch(s, session_id, f"synth-{run}", elapsed), s["FARO_API_KEY"])
        if not rejected_twice and elapsed >= 35:
            reject(s, run)
            rejected_twice = True
        time.sleep(tick)
    out(f"synth: {duration}s of synthetic data sent (run {run}, project {project})")
    return {"project": project, "api": api["service.name"], "quiet": quiet["service.name"], "mobile": mobile["service.name"], "session": session_id}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration", type=int, default=90)
    args = parser.parse_args(argv)
    generate(g.settings(), g.run_id(), duration=args.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
