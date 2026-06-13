#!/usr/bin/env python3
"""log_analyzer -- warmup-style digest of the fleet's Loki logs + Prometheus metrics.

The observe.tarktoostus.ee stack (Grafana + Loki + Prometheus) collects every VM's
nginx access logs, container logs and django-prometheus metrics -- but nothing reads
them programmatically. Sentry only sees SDK-instrumented exceptions + sampled perf
spans; it never sees the nginx status-code distribution (5xx/429/499), the offending
paths, or per-view latency percentiles. This closes that gap.

Auth: orchestrator/shared/secrets.env (LOKI_PASSWORD, GRAFANA_ADMIN_PASSWORD).
Callers (the /warmup + /log-analyzer skills) MUST NOT need to know the creds.

Sources (both publicly reachable through observe.tarktoostus.ee):
  - Loki        https://observe.tarktoostus.ee/loki/api/v1/...        (basic auth loki:LOKI_PASSWORD)
  - Prometheus  via Grafana datasource proxy                          (basic auth admin:GRAFANA_ADMIN_PASSWORD)
                prometheus.observe.* has NO public DNS -- proxy is the only path.

Sections:
  STATUS  HTTP 4xx/5xx/429/499 by status x environment (nginx, Loki)
  PATHS   top offending paths for 5xx + 429 (nginx, Loki)
  ERR     backend tracebacks / ERROR lines by env + sample exceptions (container logs, Loki)
  SLOW    slowest views by p95 latency = slow-endpoint / N+1 candidates (Prometheus)
  APP5XX  django-side 5xx by status x env, cross-checks nginx (Prometheus)

Usage:
    log_analyzer                          # all sections, 24h, all envs
    log_analyzer --since 6h               # narrow the window
    log_analyzer --env ionix              # one environment
    log_analyzer --section STATUS         # one section
    log_analyzer --json                   # machine-readable dict (for the dashboard)

Exit 0 whenever the stack is reachable (prints "API error:" lines on per-call failure).
Exit 1 only on auth/config failure. ASCII-only output (Windows/Docker-log safe).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OBSERVE = "https://observe.tarktoostus.ee"
SECRETS = Path.home() / "_tark" / "orchestrator" / "shared" / "secrets.env"
SECTIONS = ["STATUS", "PATHS", "ERR", "SLOW", "APP5XX"]

# nginx log_format `main` (orchestrator/shared/nginx/nginx.conf):
#   $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent ...
NGINX_PATTERN = r'<_> - <_> [<_>] "<method> <path> <_>" <status> <_>'

# Status code -> (short label, action class). Drives the [ATTN] lines.
STATUS_MEANING = {
    "500": ("SERVER ERROR (bug)", "attn"),
    "502": ("BAD GATEWAY (upstream down)", "attn"),
    "503": ("UNAVAILABLE (overload/deploy)", "attn"),
    "504": ("GATEWAY TIMEOUT (slow upstream)", "attn"),
    "429": ("RATE LIMITED", "attn"),
    "499": ("CLIENT ABORTED (slow upstream)", "watch"),
    "404": ("NOT FOUND (broken link / scan)", "watch"),
    "403": ("FORBIDDEN (authz / cred-stuffing)", "watch"),
    "401": ("UNAUTHORIZED", "info"),
    "400": ("BAD REQUEST", "info"),
    "405": ("METHOD NOT ALLOWED", "info"),
}

# Thresholds that promote a counter to an [ATTN] action item in the dashboard.
THRESH = {
    "5xx_any": 1,  # any server error in window
    "429": 50,  # rate-limit pressure / abuse
    "499": 1000,  # slow-upstream aborts on one env
    "404": 5000,  # broken-link wave or aggressive bot scan
    "403": 2000,  # authz misconfig or credential stuffing
    "backend_err": 100,  # backend tracebacks/ERROR per env
    "slow_p95_s": 1.0,  # view p95 latency
}


def load_creds() -> dict:
    if not SECRETS.exists():
        sys.stderr.write(
            f"log_analyzer: missing {SECRETS} (LOKI_PASSWORD, GRAFANA_ADMIN_PASSWORD)\n"
        )
        sys.exit(1)
    creds = {}
    for line in SECRETS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        creds[k.strip()] = v.strip()
    for k in ("LOKI_PASSWORD", "GRAFANA_ADMIN_PASSWORD"):
        if not creds.get(k):
            sys.stderr.write(f"log_analyzer: {SECRETS} missing '{k}'\n")
            sys.exit(1)
    return creds


def _get(url: str, user: str, pw: str, timeout: int = 90) -> dict:
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {tok}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- Loki helpers


def loki_instant(creds: dict, query: str) -> list:
    """Run a Loki INSTANT query (single evaluation at 'now'); return the result array (or [] on error).

    An instant query evaluates a range-vector like count_over_time({...}[24h]) EXACTLY ONCE,
    looking back `24h` from now -- one window, no double-count. A range query (query_range) with
    step=since evaluates the same vector at BOTH endpoints (now-since AND now), so summing its
    samples spans ~2x the intended window. That bug surfaced already-resolved incidents as current:
    the aburg-prelive redis.exceptions.TimeoutError storm (bounded 2026-06-11..12, zero in the
    real last-24h) was reported as 3674 ongoing. The [since] selector lives inside `query`, so an
    instant query needs no start/end/step math.
    """
    url = f"{OBSERVE}/loki/api/v1/query?{urllib.parse.urlencode({'query': query})}"
    try:
        d = _get(url, "loki", creds["LOKI_PASSWORD"])
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"  API error (loki): {str(e)[:120]}")
        return []
    if d.get("status") != "success":
        print(f"  API error (loki): {str(d)[:120]}")
        return []
    return d.get("data", {}).get("result", [])


def loki_sum(creds: dict, query: str, since: str) -> list:
    """Total per series for a count_over_time([since]) metric -> [(total:int, labels:dict), ...] desc.

    `since` documents the window the caller baked into `query` as its [since] selector; the
    instant query reads the window from there, so this arg is signature-only (kept so call sites
    read `loki_sum(creds, q, since)` at a glance). See loki_instant for why this is instant, not range.
    """
    rows = []
    for s in loki_instant(creds, query):
        # instant (vector) result: ONE [ts, value] pair per series ("value"), not a "values" list.
        val = s.get("value", [None, "0"])
        try:
            total = int(float(val[1]))
        except (ValueError, TypeError, IndexError):
            total = 0
        rows.append((total, s.get("metric", {})))
    rows.sort(key=lambda x: x[0], reverse=True)
    return rows


# ------------------------------------------------------ Prometheus (via Grafana proxy)


def prom_uid(creds: dict) -> str:
    try:
        ds = _get(
            f"{OBSERVE}/api/datasources",
            "admin",
            creds["GRAFANA_ADMIN_PASSWORD"],
            timeout=20,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return ""
    for x in ds:
        if x.get("type") == "prometheus":
            return x.get("uid", "")
    return ""


def prom_query(creds: dict, uid: str, promql: str) -> list:
    if not uid:
        return []
    url = (
        f"{OBSERVE}/api/datasources/proxy/uid/{uid}/api/v1/query"
        f"?{urllib.parse.urlencode({'query': promql})}"
    )
    try:
        d = _get(url, "admin", creds["GRAFANA_ADMIN_PASSWORD"], timeout=40)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"  API error (prometheus): {str(e)[:120]}")
        return []
    if d.get("status") != "success":
        print(f"  API error (prometheus): {str(d)[:120]}")
        return []
    out = []
    for s in d.get("data", {}).get("result", []):
        val = s.get("value", [None, "NaN"])[1]
        if val in ("NaN", "+Inf", "-Inf", None):
            continue
        out.append((float(val), s.get("metric", {})))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


# ----------------------------------------------------------------- selectors


def nginx_sel(env: str) -> str:
    return '{job="nginx"' + (f', environment="{env}"' if env else "") + "}"


def backend_sel(env: str) -> str:
    return '{container=~".*backend.*"' + (f', environment="{env}"' if env else "") + "}"


def prom_envsel(env: str) -> str:
    return f'{{environment="{env}"}}' if env else ""


# ------------------------------------------------------------------ sections


def section_status(creds, since, env, out):
    # Pre-filter to 4xx/5xx lines with a cheap |~ before the pattern parse (the status always
    # appears as `" <code> ` in log_format `main`). Cuts ~400k lines/h down to the error lines
    # so the parse stays cheap and well under Loki's query timeout.
    q = (
        f"sum by (status, environment) (count_over_time("
        f'{nginx_sel(env)} |~ `" [45][0-9][0-9] ` | pattern `{NGINX_PATTERN}` '
        f'| path!="/metrics" | status=~"4..|5.." [{since}]))'
    )
    rows = loki_sum(creds, q, since)
    out["status"] = [
        {"n": n, "status": m.get("status"), "env": m.get("environment")}
        for n, m in rows
    ]
    print(f"== STATUS: HTTP 4xx/5xx by status x env ({since}, nginx, excl /metrics) ==")
    if not rows:
        print("  (no 4xx/5xx -- clean, or nginx logs not flowing)")
        return
    for n, m in rows[:30]:
        st = m.get("status", "?")
        label, cls = STATUS_MEANING.get(st, ("", "info"))
        flag = "[ATTN] " if cls == "attn" else ""
        # promote on threshold even for 'watch' classes
        if st.startswith("5") and n >= THRESH["5xx_any"]:
            flag = "[ATTN] "
        elif st == "429" and n >= THRESH["429"]:
            flag = "[ATTN] "
        elif st == "499" and n >= THRESH["499"]:
            flag = "[ATTN] "
        elif st == "404" and n >= THRESH["404"]:
            flag = "[ATTN] "
        elif st == "403" and n >= THRESH["403"]:
            flag = "[ATTN] "
        print(f"  {flag}{n:>8}  {st}  {m.get('environment', '?'):16} {label}")


def section_paths(creds, since, env, out):
    # Pre-filter cheaply with |~ before the (expensive) pattern parse: cut ~400k lines/h
    # down to the handful of 5xx/429 lines, then parse those precisely.
    q = (
        f"topk(20, sum by (path, status, environment) (count_over_time("
        f'{nginx_sel(env)} |~ `" (5[0-9][0-9]|429) ` '
        f'| pattern `{NGINX_PATTERN}` | path!="/metrics" '
        f'| status=~"5..|429" [{since}])))'
    )
    rows = loki_sum(creds, q, since)
    out["paths"] = [
        {
            "n": n,
            "status": m.get("status"),
            "path": m.get("path"),
            "env": m.get("environment"),
        }
        for n, m in rows
    ]
    print(f"\n== PATHS: top offending paths for 5xx + 429 ({since}, nginx) ==")
    if not rows:
        print("  (no 5xx/429 paths -- clean)")
        return
    for n, m in rows[:20]:
        print(
            f"  [ATTN] {n:>6}  {m.get('status', '?')}  {m.get('environment', '?'):14} {m.get('path', '?')[:70]}"
        )


# The last line of a Python traceback is `dotted.path.ExceptionType: message`. We capture
# that class in-query and group by it -- far more actionable than counting "Traceback" lines
# (the type lives on a different line than the word "Traceback"/"ERROR").
EXC_CAPTURE = (
    r"(?P<exc>[A-Za-z_][A-Za-z0-9_.]*"
    r"(?:Error|Exception|DisallowedHost|DoesNotExist|PermissionDenied|"
    r"Throttled|Timeout|NotAuthenticated|ValidationError|IntegrityError)):"
)
# Types driven by internet scanners / spoofed Host headers -- log noise, not a fleet bug.
NOISE_EXC = {"django.core.exceptions.DisallowedHost"}


def section_err(creds, since, env, out):
    print(f"\n== ERR: backend exceptions by type x env ({since}, container logs) ==")
    # Primary: top exception TYPES by count -- separates real bugs from scanner noise.
    q = (
        f"sum by (environment, exc) (count_over_time("
        f"{backend_sel(env)} | regexp `{EXC_CAPTURE}` | exc!=`` [{since}]))"
    )
    rows = loki_sum(creds, q, since)
    out["exceptions"] = [
        {"n": n, "exc": m.get("exc"), "env": m.get("environment")} for n, m in rows
    ]
    if not rows:
        print(
            "  (no recognizable exceptions -- clean, or backend not shipping to Loki)"
        )
    for n, m in rows[:25]:
        exc = m.get("exc", "?")
        noise = exc in NOISE_EXC
        flag = "[ATTN] " if (n >= THRESH["backend_err"] and not noise) else ""
        tag = "  (scanner noise)" if noise else ""
        print(f"  {flag}{n:>8}  {m.get('environment', '?'):16} {exc}{tag}")
    # Secondary: coarse total error-line volume per env (catches errors logged w/o a typed exception).
    qv = (
        f"sum by (environment) (count_over_time("
        f"{backend_sel(env)} |~ `(?i)traceback|\\bERROR\\b|\\bCRITICAL\\b` [{since}]))"
    )
    vol = loki_sum(creds, qv, since)
    out["backend_err_volume"] = [{"n": n, "env": m.get("environment")} for n, m in vol]
    if vol:
        top = ", ".join(f"{m.get('environment', '?')}={n}" for n, m in vol[:6])
        print(f"  -- raw error-line volume (incl. tracebacks/untyped): {top}")


def section_slow(creds, since, env, out, uid):
    win = since if since.endswith(("m", "h")) else "1h"
    es = prom_envsel(env)
    q = (
        f"topk(15, histogram_quantile(0.95, sum by (le, view, environment) "
        f"(rate(django_http_requests_latency_seconds_by_view_method_bucket{es}[{win}]))))"
    )
    rows = prom_query(creds, uid, q)
    # ignore the metrics scrape view -- it is the Prometheus poller, not app traffic
    rows = [(v, m) for v, m in rows if m.get("view") != "prometheus-django-metrics"]
    out["slow_views"] = [
        {"p95_s": round(v, 3), "view": m.get("view"), "env": m.get("environment")}
        for v, m in rows
    ]
    print(
        f"\n== SLOW: slowest views by p95 latency ({win}, Prometheus) -- slow-endpoint / N+1 candidates =="
    )
    if not rows:
        print("  (no view latency data -- Prometheus proxy unreachable or no traffic)")
        return
    for v, m in rows[:15]:
        flag = "[ATTN] " if v >= THRESH["slow_p95_s"] else ""
        print(f"  {flag}{v:7.2f}s  {m.get('environment', '?'):16} {m.get('view', '?')}")
    print(
        "  note: true N+1 needs per-request query-count instrumentation (Sentry's N+1 detector);"
    )
    print(
        "        these are CANDIDATES -- a high-p95 list endpoint is the classic N+1 tell. Check with /review 4.11."
    )


def section_app5xx(creds, since, env, out, uid):
    es = env and f'environment="{env}",' or ""
    q = f'sum by (environment, status) (increase(django_http_responses_total_by_status_total{{{es}status=~"5.."}}[{since}]))'
    rows = prom_query(creds, uid, q)
    rows = [(v, m) for v, m in rows if v >= 1]
    out["app_5xx"] = [
        {"n": round(v), "status": m.get("status"), "env": m.get("environment")}
        for v, m in rows
    ]
    print(
        f"\n== APP5XX: django-side 5xx by status x env ({since}, Prometheus) -- cross-check vs nginx =="
    )
    if not rows:
        print("  (no app-level 5xx -- django served zero server errors in window)")
        return
    for v, m in rows[:20]:
        print(
            f"  [ATTN] {round(v):>6}  {m.get('status', '?')}  {m.get('environment', '?')}"
        )


def main():
    ap = argparse.ArgumentParser(description="Loki/Prometheus log digest for the fleet")
    ap.add_argument(
        "--section", choices=SECTIONS, help="run one section (default: all)"
    )
    ap.add_argument(
        "--since", default="24h", help="window, e.g. 6h, 24h, 7d (default 24h)"
    )
    ap.add_argument(
        "--env",
        default="",
        help="filter to one environment (e.g. ionix, demo, website)",
    )
    ap.add_argument("--json", action="store_true", help="emit machine-readable dict")
    args = ap.parse_args()

    creds = load_creds()
    out: dict = {"since": args.since, "env": args.env or "all"}
    want = [args.section] if args.section else SECTIONS
    need_prom = any(s in want for s in ("SLOW", "APP5XX"))
    uid = prom_uid(creds) if need_prom else ""

    # In --json mode, capture the human report into out["report"] so nothing is lost
    # but stdout stays pure JSON for the dashboard to parse.
    import contextlib
    import io

    buf = io.StringIO()
    sink = contextlib.redirect_stdout(buf) if args.json else contextlib.nullcontext()
    with sink:
        if "STATUS" in want:
            section_status(creds, args.since, args.env, out)
        if "PATHS" in want:
            section_paths(creds, args.since, args.env, out)
        if "ERR" in want:
            section_err(creds, args.since, args.env, out)
        if "SLOW" in want:
            section_slow(creds, args.since, args.env, out, uid)
        if "APP5XX" in want:
            section_app5xx(creds, args.since, args.env, out, uid)

    if args.json:
        out["report"] = buf.getvalue()
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
