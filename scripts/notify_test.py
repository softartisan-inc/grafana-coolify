#!/usr/bin/env python3
"""Send a test notification through the contact points of grafana-setup (spec 12.6.2).

Standard library only. For gc-telegram and gc-email (when present), asks Grafana to send a test
notification with the stored settings: the bot token never leaves Grafana. Checked on Grafana
13.2.2 (tools/versions.env, GRAFANA_VERSION): it removed the legacy
/api/alertmanager/grafana/config/api/v1/receivers/test (HTTP 410); its replacement is the beta
receivers API (notifications.alerting.grafana.app/v1beta1), isolated in test_receiver(): recheck
it on every Grafana upgrade.

Usage: GRAFANA_URL=https://grafana.example.com GRAFANA_SA_TOKEN=glsa_... python3 scripts/notify_test.py
Exit code 0 when every present contact point reports "sent". On the harness (harness/edge.py),
the email lands in the sink and the Telegram attempt is refused by the sink proxy (reported as
failed, as expected there).
"""

import json
import os
import sys
import urllib.error
import urllib.request

CONTACT_POINTS = ("gc-telegram", "gc-email")
RECEIVERS_API = "/apis/notifications.alerting.grafana.app/v1beta1/namespaces/{namespace}/receivers"


def call(url, token, method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode("utf-8", "replace")[:200]


def namespace(url, token):
    """Kubernetes-style namespace of the token's organisation: "default" for org 1, else org-<id>."""
    status, org = call(url, token, "GET", "/api/org")
    if status != 200:
        raise RuntimeError(f"GET /api/org: HTTP {status}")
    return "default" if org["id"] == 1 else f"org-{org['id']}"


def receivers(url, token, ns):
    """{contact point title: (receiver name, [integrations])}."""
    status, payload = call(url, token, "GET", RECEIVERS_API.format(namespace=ns))
    if status != 200:
        raise RuntimeError(f"GET receivers: HTTP {status}: {payload}")
    return {item["spec"]["title"]: (item["metadata"]["name"], item["spec"]["integrations"]) for item in payload.get("items", [])}


def test_receiver(url, token, ns, name, integration):
    """(ok, detail) of one test notification sent with the stored settings of `integration`."""
    status, payload = call(url, token, "POST", RECEIVERS_API.format(namespace=ns) + f"/{name}/test", {"integration": integration})
    if status != 200:
        return False, f"HTTP {status}: {payload}"
    if payload.get("status") != "success":
        return False, payload.get("error", payload.get("status"))
    return True, "sent"


def main(env=None):
    env = os.environ if env is None else env
    url, token = env.get("GRAFANA_URL", ""), env.get("GRAFANA_SA_TOKEN", "")
    if not url or not token:
        print("notify-test: GRAFANA_URL and GRAFANA_SA_TOKEN are required", file=sys.stderr)
        return 2
    try:
        ns = namespace(url, token)
        found = receivers(url, token, ns)
    except (RuntimeError, urllib.error.URLError, OSError) as exc:
        print(f"notify-test: ERROR: {str(exc).replace(token, '***')}", file=sys.stderr)
        return 1
    failed = 0
    for title in CONTACT_POINTS:
        if title not in found:
            print(f"notify-test: {title}: absent (not configured)")
            continue
        name, integrations = found[title]
        for integration in integrations:
            ok, detail = test_receiver(url, token, ns, name, integration)
            failed += not ok
            print(f"notify-test: {title}: {'sent' if ok else 'failed: ' + str(detail).replace(token, '***')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
