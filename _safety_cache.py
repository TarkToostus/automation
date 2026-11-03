"""SHA-256 verdict cache for tark_cli safety screen.

Stores only SAFE verdicts — UNSAFE / unparseable / errors always re-screen.
Bounded LRU + TTL. flock-guarded on POSIX, lock-free on Windows (acceptable:
cache is single-user and races produce stale-but-not-corrupt JSON).

Cache key = SHA-256(SCREEN_POLICY + SAFETY_CHECK_MODEL + mode + title + body).
SCREEN_POLICY names what a SAFE verdict covered; bump it whenever the screen's
coverage changes, so verdicts recorded under a weaker screen (the first-8000-chars
truncation before full chunked screening) miss instead of passing unread text.
A text over the per-call cap is screened in overlapping line-boundary chunks;
each chunk is cached under its own key (body = the chunk), and a fully SAFE text
also under the whole-body key. A re-read of unchanged text therefore re-screens
nothing. An edit is NOT confined to its own chunk: it shifts every later chunk
boundary, so the edited chunk and usually every chunk after it re-screen; only
the chunks before the edit stay cached. SAFETY_CHECK_MODEL
selects a model only for the opt-in GEMINI_BIN=gemini enterprise CLI. The default
chain ignores it: claude runs haiku (first), agy is opt-in via GEMINI_BIN and
pinned with --model (SAFETY_CHECK_AGY_MODEL), codex is pinned
(SAFETY_CHECK_CODEX_MODEL).
For them it is a manual cache-bust knob: bump it to invalidate every cached
verdict. The key does not name the provider or its pinned model, so changing a
pin does not invalidate cached SAFE verdicts by itself.

Disable with TARK_SAFETY_CACHE=0.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    fcntl = None
    _HAS_FCNTL = False

# Long TTL is safe: cache key is SHA-256(policy + model + mode + title + body), so
# any content edit changes the key, a SCREEN_POLICY bump invalidates verdicts from
# a weaker screen, and bumping SAFETY_CHECK_MODEL invalidates prior verdicts on
# demand. 30 days; override via TARK_SAFETY_CACHE_TTL_SEC for
# stricter runs.
_DEFAULT_TTL_SEC = 30 * 24 * 60 * 60  # 30 days (was 15 min)
_DEFAULT_CAP = 10_000  # ~1.1 MB on disk at ~110 B/entry (was 1000)
# v2: every chunk of the whole text screened, 1000-char overlap (C2 #14865).
# Unversioned keys came from the first-8000-chars-only screen.
SCREEN_POLICY = 'full-chunked-overlap-v2'


def _cache_path() -> Path:
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    p = Path(base) / 'tark_cli'
    p.mkdir(parents=True, exist_ok=True)
    return p / 'safety_verdicts.json'


def _key(mode: str, title: str, body: str) -> str:
    h = hashlib.sha256()
    h.update(SCREEN_POLICY.encode('utf-8'))
    h.update(b'\n')
    h.update(os.environ.get('SAFETY_CHECK_MODEL', '').encode('utf-8'))
    h.update(b'\n')
    h.update((mode or '').encode('utf-8'))
    h.update(b'\n')
    h.update((title or '').encode('utf-8'))
    h.update(b'\n')
    h.update((body or '').encode('utf-8'))
    return h.hexdigest()


def _ttl() -> int:
    try:
        return int(os.environ.get('TARK_SAFETY_CACHE_TTL_SEC', _DEFAULT_TTL_SEC))
    except ValueError:
        return _DEFAULT_TTL_SEC


def _enabled() -> bool:
    return os.environ.get('TARK_SAFETY_CACHE') != '0'


def lookup(mode: str, title: str, body: str) -> bool:
    """Return True if a fresh SAFE verdict is cached for this payload."""
    if not _enabled():
        return False
    path = _cache_path()
    if not path.exists():
        return False
    key = _key(mode, title, body)
    try:
        with path.open('r') as f:
            if _HAS_FCNTL:
                fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    entry = data.get(key)
    if not entry:
        return False
    if time.time() - entry.get('ts', 0) > _ttl():
        return False
    return entry.get('verdict') == 'SAFE'


def record_safe(mode: str, title: str, body: str) -> None:
    """Record a SAFE verdict. Caps at _DEFAULT_CAP entries; evicts oldest by ts."""
    if not _enabled():
        return
    path = _cache_path()
    key = _key(mode, title, body)
    now = time.time()
    try:
        with path.open('a+') as f:
            if _HAS_FCNTL:
                fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data[key] = {'verdict': 'SAFE', 'ts': now}
            if len(data) > _DEFAULT_CAP:
                drop_n = max(1, _DEFAULT_CAP // 10)
                sorted_keys = sorted(data.keys(), key=lambda k: data[k].get('ts', 0))
                for k in sorted_keys[:drop_n]:
                    data.pop(k, None)
            f.seek(0)
            f.truncate()
            json.dump(data, f)
    except OSError:
        return
