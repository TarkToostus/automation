#!/usr/bin/env python3
"""sentry_warmup — emit warmup digest of Sentry issues for the configured org.

Auth: ~/.config/sentry/config.json (mode 600, fields: token, org, api).
Callers (warmup skill, etc.) MUST NOT need to know the token path.

Usage:
    sentry_warmup                 # full digest (TOP + ERR + TRIAGE + PERF + THRU + REPLAY + SEER + PROJ)
    sentry_warmup --section TOP|ERR|TRIAGE|PERF|THRU|REPLAY|SEER|PROJ   # one section

Exit 0 always when API reachable; prints "API error: …" lines on per-call failure.
Exit 1 only on auth/config failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path.home() / ".config" / "sentry" / "config.json"


def load_cfg() -> dict:
    if not CONFIG.exists():
        sys.stderr.write(
            f"sentry_warmup: missing {CONFIG}. Seed with token/org/api keys (mode 600).\n"
        )
        sys.exit(1)
    cfg = json.loads(CONFIG.read_text())
    for k in ("token", "org", "api"):
        if not cfg.get(k):
            sys.stderr.write(f"sentry_warmup: {CONFIG} missing '{k}'\n")
            sys.exit(1)
    return cfg


def fetch(cfg: dict, path: str) -> list | dict:
    url = f"{cfg['api'].rstrip('/')}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        return {"detail": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"detail": f"connection failed: {e.reason}"}
    except json.JSONDecodeError as e:
        return {"detail": f"malformed JSON: {e}"}


def issues_query(query: str, sort: str, limit: int, period: str) -> str:
    q = urllib.parse.urlencode({"query": query, "sort": sort, "limit": limit, "statsPeriod": period})
    return f"?{q}"


def section_top(cfg: dict) -> None:
    print("Top unresolved issues (7d):")
    data = fetch(cfg, f"/organizations/{cfg['org']}/issues/{issues_query('is:unresolved', 'freq', 10, '7d')}")
    if isinstance(data, dict) and "detail" in data:
        print(f"  API error: {data['detail']}")
        return
    for i in data[:10]:
        proj = (i.get("project") or {}).get("slug", "?")
        title = (i.get("title") or "?")[:60]
        count = i.get("count", "?")
        level = i.get("level", "?")
        status = i.get("substatus", i.get("status", "?"))
        print(f"  [{level}] {proj}: {title} ({count}x) — {status}")


def section_err(cfg: dict) -> None:
    data = fetch(cfg, f"/organizations/{cfg['org']}/issues/{issues_query('is:unresolved issue.category:error', 'date', 5, '24h')}")
    if isinstance(data, dict) and "detail" in data:
        print(f"Errors (24h): API error: {data['detail']}")
        return
    if not data:
        print("Errors (24h): 0 — all clear")
        return
    print(f"Errors (24h): {len(data)} — NEEDS ATTENTION")
    for i in data[:5]:
        proj = (i.get("project") or {}).get("slug", "?")
        title = (i.get("title") or "?")[:60]
        count = i.get("count", "?")
        print(f"  {proj}: {title} ({count}x)")


def section_perf(cfg: dict) -> None:
    print("Performance issues (7d):")
    data = fetch(cfg, f"/organizations/{cfg['org']}/issues/{issues_query('issue.category:performance is:unresolved', 'freq', 5, '7d')}")
    if isinstance(data, dict) and "detail" in data:
        print(f"  API error: {data['detail']}")
        return
    for i in data[:5]:
        proj = (i.get("project") or {}).get("slug", "?")
        title = (i.get("title") or "?")[:70]
        count = i.get("count", "?")
        print(f"  {proj}: {title} ({count}x)")


def section_replay(cfg: dict) -> None:
    print("Replays (7d, recent 10):")
    data = fetch(cfg, f"/organizations/{cfg['org']}/replays/?statsPeriod=7d&per_page=10")
    if isinstance(data, dict) and "detail" in data:
        print(f"  API error: {data['detail']}")
        return
    rows = data.get("data", []) if isinstance(data, dict) else data
    if not rows:
        print("  none")
        return
    for r in rows[:10]:
        rid = str(r.get("id", "?"))[:8]
        proj = r.get("project_id", "?")
        user = (r.get("user") or {}).get("display_name") or (r.get("user") or {}).get("username", "anon")
        urls = r.get("urls") or []
        url = (urls[0] if urls else "?")[:60]
        errs = r.get("count_errors", 0)
        dur = int(r.get("duration") or 0)
        marker = " <-- has errors" if errs else ""
        print(f"  {rid} p{proj} {user} {dur}s err={errs} {url}{marker}")


def section_seer(cfg: dict) -> None:
    print("Seer autofix state (top 5 unresolved 7d):")
    issues = fetch(cfg, f"/organizations/{cfg['org']}/issues/{issues_query('is:unresolved', 'freq', 5, '7d')}")
    if isinstance(issues, dict) and "detail" in issues:
        print(f"  API error: {issues['detail']}")
        return
    if not issues:
        print("  no unresolved issues")
        return
    for i in issues[:5]:
        iid = i.get("id", "?")
        title = (i.get("title") or "?")[:50]
        state = fetch(cfg, f"/issues/{iid}/autofix/")
        if isinstance(state, dict) and "detail" in state:
            status = f"unavail ({state['detail']})"
        else:
            af = state.get("autofix") if isinstance(state, dict) else None
            if not af:
                status = "no run — trigger in UI"
            else:
                status = af.get("status", "?")
        print(f"  {iid}: {title} — {status}")


def _count_issues(cfg: dict, query: str, period: str = "7d", cap: int = 100):
    data = fetch(cfg, f"/organizations/{cfg['org']}/issues/{issues_query(query, 'freq', cap, period)}")
    if isinstance(data, dict) and "detail" in data:
        return None, data["detail"]
    return data, None


def _fmt_count(rows, cap: int = 100) -> str:
    n = len(rows)
    return f"{cap}+" if n >= cap else str(n)


def _issue_label(i: dict, n: int = 70) -> str:
    """Human label: title, else culprit, else metadata type/value — never bare '<unknown>'."""
    title = (i.get("title") or "").strip()
    if title and title != "<unknown>":
        return title[:n]
    meta = i.get("metadata") or {}
    for cand in (i.get("culprit"), meta.get("value"), meta.get("type"), i.get("shortId")):
        cand = (cand or "").strip()
        if cand:
            return cand[:n]
    return "<unknown>"


def section_triage(cfg: dict) -> None:
    """Substatus breakdown — escalating is the single most actionable line."""
    print("Issue triage (7d):")
    esc, err = _count_issues(cfg, "is:unresolved is:escalating")
    if err:
        print(f"  Escalating: API error: {err}")
    else:
        print(f"  Escalating ({len(esc)}):" + (" FIX FIRST" if esc else " none"))
        for i in esc[:5]:
            proj = (i.get("project") or {}).get("slug", "?")
            count = i.get("count", "?")
            sid = i.get("shortId", "")
            print(f"    [{proj}] {_issue_label(i, 60)} ({count}x) {sid}".rstrip())
    new, nerr = _count_issues(cfg, "is:unresolved is:new")
    reg, rerr = _count_issues(cfg, "is:unresolved is:regressed")
    n_new = f"err ({nerr})" if nerr else _fmt_count(new)
    n_reg = f"err ({rerr})" if rerr else _fmt_count(reg)
    print(f"  New: {n_new}  |  Regressed: {n_reg}")


def section_thru(cfg: dict) -> None:
    """Throughput-weighted transactions — a slow endpoint hit 4k times costs more than a 1x crash."""
    print("Throughput-weighted transactions (7d):")
    q = urllib.parse.urlencode([
        ("field", "transaction"),
        ("field", "count()"),
        ("field", "p75(transaction.duration)"),
        ("query", "event.type:transaction"),
        ("sort", "-count"),
        ("statsPeriod", "7d"),
        ("per_page", 8),
        ("project", -1),
    ])
    data = fetch(cfg, f"/organizations/{cfg['org']}/events/?{q}")
    if isinstance(data, dict) and "detail" in data:
        print(f"  API error: {data['detail']}")
        return
    rows = data.get("data", []) if isinstance(data, dict) else data
    if not rows:
        print("  none")
        return

    def _num(v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    rows = rows[:8]
    # aggregate time = throughput x latency; the biggest sink is the perf action item
    sink_idx = max(
        range(len(rows)),
        key=lambda i: _num(rows[i].get("count()", rows[i].get("count")))
        * _num(rows[i].get("p75(transaction.duration)", rows[i].get("p75"))),
    )
    for idx, r in enumerate(rows):
        txn = (r.get("transaction") or "?")[:48]
        cnt = r.get("count()", r.get("count", "?"))
        dur = _num(r.get("p75(transaction.duration)", r.get("p75")))
        dur_s = f"{dur / 1000:.2f}s"
        flag = " <-- biggest time sink" if idx == sink_idx else (" <-- slow" if dur > 1000 else "")
        print(f"  {txn} {dur_s} x{cnt}{flag}")


def section_proj(cfg: dict) -> None:
    data = fetch(cfg, f"/organizations/{cfg['org']}/projects/")
    if isinstance(data, dict) and "detail" in data:
        print(f"Projects: API error: {data['detail']}")
        return
    print(f"Sentry projects ({len(data)}):")
    for p in data:
        print(f"  {p.get('slug', '?')}: {p.get('platform', '?')}")


SECTIONS = {
    "TOP": section_top,
    "ERR": section_err,
    "TRIAGE": section_triage,
    "PERF": section_perf,
    "THRU": section_thru,
    "REPLAY": section_replay,
    "SEER": section_seer,
    "PROJ": section_proj,
}


def main() -> None:
    ap = argparse.ArgumentParser(prog="sentry_warmup", description=__doc__.splitlines()[0])
    ap.add_argument("--section", choices=sorted(SECTIONS), help="Run one section instead of all")
    args = ap.parse_args()
    cfg = load_cfg()
    if args.section:
        SECTIONS[args.section](cfg)
    else:
        for name in ("TOP", "ERR", "TRIAGE", "PERF", "THRU", "REPLAY", "SEER", "PROJ"):
            SECTIONS[name](cfg)
            print()


if __name__ == "__main__":
    main()
