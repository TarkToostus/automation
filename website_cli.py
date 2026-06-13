#!/usr/bin/env python3
"""website — CLI for the Tark Help Center / website content pipeline.

Standalone Python (stdlib only, no pip deps), a peer of `tark_cli`. It owns the
*content operations* that used to be inline bash in `/help-center` and `/pr`
Step 7b. Git/PR/worktree orchestration stays in the skills — this tool never
runs `git commit` / `gh pr` (the deliberate content-ops-only boundary).

    website build                         # rebuild manifest (wraps scripts/build-help.py)
    website sync [--commit] [--push]       # mirror to ../website (wraps sync-help-to-website.py)
    website capture [--user U] [--pass P]  # hero screenshots (wraps pnpm test:baselines)
    website reseed --yes                   # db_init --with-demo (DESTRUCTIVE dev-DB reseed)
    website from-proof TASK [--page R]     # reuse /verify .proof shots as help step shots
                       [--dest DIR] [--no-article] [--build] [--sync]
    website persist-proof TASK --page R --dest DIR   # copy+rename shots only (/pr Step 7b step a)
    website refresh [--from-proof TASK --page R] [--clean]
                    [--skip-capture] [--skip-build] [--skip-website] [--commit] [--push]

Checkout resolution: every command operates on a *tark-platform checkout*. It is
taken from `--repo PATH`, else the git toplevel of CWD. The checkout must contain
`scripts/build-help.py` (that's the validation). Because the wrapped scripts
self-locate via `__file__`, the tool just invokes the right checkout's copy.

Naming contract (must match scripts/build-help.py):
    module  = docu/help/<module>/ dir name
    slug    = the article's `page:` frontmatter route, last path segment
    hero    = {module}--{slug}.png
    step    = {module}--{slug}--NN-name.png   (NN drives order)
All land in <repo>/tests/baselines/screenshots/desktop/ (gitignored).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# checkout resolution + small helpers
# ---------------------------------------------------------------------------


def _run(cmd, cwd=None, env=None, check=False):
    """Run a subprocess, streaming output. Returns the CompletedProcess."""
    print(f"  $ {' '.join(str(c) for c in cmd)}" + (f"   (cwd={cwd})" if cwd else ""))
    return subprocess.run(cmd, cwd=cwd, env=env, check=check)


def resolve_repo(args):
    """Resolve the tark-platform checkout to operate on. Exit on failure."""
    if getattr(args, "repo", None):
        repo = Path(args.repo).expanduser().resolve()
    else:
        try:
            top = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            repo = Path(top)
        except subprocess.CalledProcessError:
            sys.exit("[ERROR] not inside a git checkout; pass --repo <tark-platform path>")
    if not (repo / "scripts" / "build-help.py").exists():
        sys.exit(f"[ERROR] {repo} is not a tark-platform checkout (no scripts/build-help.py)")
    return repo


def baselines_dir(repo):
    return repo / "tests" / "baselines" / "screenshots" / "desktop"


def parse_frontmatter(md_path):
    """Return the frontmatter dict of a help article (stdlib, no yaml dep)."""
    text = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    meta = {}
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("\"'")
    return meta


def iter_help_articles(repo):
    """Yield (module, md_path, page_route) for every help article."""
    help_dir = repo / "docu" / "help"
    if not help_dir.exists():
        return
    for module_dir in sorted(help_dir.iterdir()):
        if not module_dir.is_dir():
            continue
        for md in sorted(module_dir.glob("*.md")):
            if md.name == "_index.md":
                continue
            yield module_dir.name, md, parse_frontmatter(md).get("page", "")


def route_to_module_page(repo, route):
    """Map a verified route to (module, slug, existing_article_path|None).

    Matches the article whose `page:` is the longest shared prefix of `route`.
    Falls back to deriving module=first segment, slug=last segment when nothing
    matches (caller surfaces the guess so the human can correct placement).
    """
    route = (route or "").rstrip("/")
    best = None  # (score, module, slug, md_path)
    for module, md, page in iter_help_articles(repo):
        p = (page or "").rstrip("/")
        if not p:
            continue
        if route == p or route.startswith(p + "/") or p.startswith(route + "/"):
            score = len(os.path.commonprefix([route, p]))
            if best is None or score > best[0]:
                best = (score, module, p.split("/")[-1], md)
    if best:
        return best[1], best[2], best[3]
    segs = [s for s in route.split("/") if s]
    return (segs[0] if segs else "core"), (segs[-1] if segs else "index"), None


def find_proof_dir(repo, task, override=None):
    """Locate .proof/<task>/screenshots across this repo + its worktrees."""
    if override:
        d = Path(override).expanduser().resolve()
        return d if d.is_dir() else None
    candidates = [repo]
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            text=True, stderr=subprocess.DEVNULL,
        )
        candidates += [
            Path(ln[len("worktree ") :]) for ln in out.splitlines() if ln.startswith("worktree ")
        ]
    except subprocess.CalledProcessError:
        pass
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        d = c / ".proof" / str(task) / "screenshots"
        if d.is_dir() and any(d.glob("*.png")):
            return d
    return None


def copy_shots(proof_dir, dest, module, slug, make_hero=True):
    """Copy+rename proof PNGs into the baselines dir. Returns list of dest names.

    The lowest-NN shot is ALSO copied to the bare hero name {module}--{slug}.png,
    because build-help.py's manifest currently surfaces the hero (see --help note).
    """
    dest.mkdir(parents=True, exist_ok=True)
    pngs = sorted(proof_dir.glob("*.png"))
    copied = []
    for f in pngs:
        target = dest / f"{module}--{slug}--{f.name}"
        shutil.copy2(f, target)
        copied.append(target.name)
    if make_hero and pngs:
        hero = dest / f"{module}--{slug}.png"
        shutil.copy2(pngs[0], hero)
        copied.append(hero.name)
    return copied


ARTICLE_TEMPLATE = """---
title: "{title}"
description: "TODO one-line: the goal, and when you'd do this"
page: "{route}"
order: 99
---

One line: the goal, and when you'd do this.

## Steps

{steps}

## What you'll see

Short reference on tabs / badges / columns — the page tour, *after* the process.
"""


def scaffold_article(repo, module, slug, route, proof_dir):
    """Write docu/help/<module>/<slug>.md from proof slugs if it doesn't exist.

    Returns (path, created: bool). Existing articles are left untouched (the
    skill refreshes prose by hand — we never clobber authored content).
    """
    art = repo / "docu" / "help" / module / f"{slug}.md"
    if art.exists():
        return art, False
    steps = []
    for i, f in enumerate(sorted(proof_dir.glob("*.png")), start=1):
        ref = f.stem  # e.g. "02-confirm"
        label = re.sub(r"^\d+-", "", ref).replace("-", " ").strip().capitalize() or f"Step {i}"
        steps.append(f"{i}. **{label}** — TODO describe the action and result.\n\n![{label}]({ref})")
    body_steps = "\n\n".join(steps) if steps else "1. **TODO** — describe the first step."
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text(
        ARTICLE_TEMPLATE.format(title=slug.replace("-", " ").title(), route=route, steps=body_steps),
        encoding="utf-8",
    )
    return art, True


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_build(args):
    repo = resolve_repo(args)
    r = _run([sys.executable, str(repo / "scripts" / "build-help.py")], cwd=str(repo))
    return r.returncode


def cmd_sync(args):
    repo = resolve_repo(args)
    cmd = [sys.executable, str(repo / "scripts" / "sync-help-to-website.py")]
    if args.commit:
        cmd.append("--commit")
    if args.dry_run:
        cmd.append("--dry-run")
    rc = _run(cmd, cwd=str(repo)).returncode
    if rc == 0 and args.push:
        website = repo.parent / "website"
        if not (website / ".git").exists():
            print(f"[WARN] {website} is not a git repo — skipping --push")
        else:
            rc = _run(["git", "-C", str(website), "push"]).returncode
    return rc


def cmd_capture(args):
    repo = resolve_repo(args)
    env = dict(os.environ)
    env["TEST_USERNAME"] = args.user
    env["TEST_PASSWORD"] = args.password
    print(f"[capture] pnpm test:baselines as {args.user} (needs dev server on :5173 + :8000)")
    return _run(["pnpm", "test:baselines"], cwd=str(repo), env=env).returncode


def cmd_reseed(args):
    repo = resolve_repo(args)
    if not args.yes:
        sys.exit("[ERROR] reseed wipes the shared dev DB. Re-run with --yes to confirm.")
    return _run(
        [sys.executable, str(repo / "backend" / "manage.py"), "db_init", "--with-demo"],
        cwd=str(repo),
    ).returncode


def _do_from_proof(args, repo, dest):
    """Shared core for from-proof / persist-proof. Returns (module, slug, copied, art)."""
    proof = find_proof_dir(repo, args.task, getattr(args, "proof_dir", None))
    if not proof:
        sys.exit(f"[ERROR] no .proof/{args.task}/screenshots in {repo} or its worktrees — run /verify first")
    route = args.page
    if not route:
        sys.exit("[ERROR] --page <route> required (the verified URL path, e.g. /mes-batch/plan/batch-orders)")
    module, slug, existing = route_to_module_page(repo, route)
    if existing is None:
        print(f"[WARN] route '{route}' matched no help article — guessing module='{module}', slug='{slug}'. "
              f"Correct placement after scaffold if wrong.")
    copied = copy_shots(proof, dest, module, slug)
    print(f"[from-proof] {len(copied)} shot(s) -> {dest} as {module}--{slug}--*")
    return module, slug, copied, proof


def cmd_from_proof(args):
    repo = resolve_repo(args)
    dest = Path(args.dest).expanduser().resolve() if args.dest else baselines_dir(repo)
    module, slug, copied, proof = _do_from_proof(args, repo, dest)
    if not args.no_article:
        art, created = scaffold_article(repo, module, slug, args.page, proof)
        print(f"[article] {'scaffolded' if created else 'exists (left as-is)'}: {art.relative_to(repo)}")
    rc = 0
    if args.build:
        rc = cmd_build(args) or rc
    if args.sync:
        rc = cmd_sync(args) or rc
    return rc


def cmd_persist_proof(args):
    """Copy+rename proof shots into an explicit dest (no article, no build/sync).

    Used by /pr Step 7b step (a): persist shots into the MAIN checkout's
    baselines dir so they survive the feature-worktree reap.
    """
    repo = resolve_repo(args)
    dest = Path(args.dest).expanduser().resolve()
    _do_from_proof(args, repo, dest)
    return 0


def cmd_refresh(args):
    repo = resolve_repo(args)
    if args.clean:
        args.yes = True
        if cmd_reseed(args):
            return 1
    if args.from_proof:
        args.task = args.from_proof
        args.dest = None
        args.no_article = False
        args.proof_dir = None
        module, slug, copied, proof = _do_from_proof(args, repo, baselines_dir(repo))
        scaffold_article(repo, module, slug, args.page, proof)
    elif not args.skip_capture:
        if cmd_capture(args):
            return 1
    if not args.skip_build:
        if cmd_build(args):
            return 1
    if not args.skip_website:
        return cmd_sync(args)
    print("[refresh] --skip-website: manifest rebuilt, no ../website sync")
    return 0


# ---------------------------------------------------------------------------
# arg parsing
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(prog="website", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="tark-platform checkout (default: git toplevel of CWD)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="rebuild manifest (wraps scripts/build-help.py)").set_defaults(func=cmd_build)

    s = sub.add_parser("sync", help="mirror to ../website (wraps sync-help-to-website.py)")
    s.add_argument("--commit", action="store_true")
    s.add_argument("--push", action="store_true", help="git push the website repo after --commit")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("capture", help="hero screenshots (wraps pnpm test:baselines)")
    s.add_argument("--user", default="helpcenter_admin")
    s.add_argument("--password", default="pass.admin.pass")
    s.set_defaults(func=cmd_capture)

    s = sub.add_parser("reseed", help="db_init --with-demo (DESTRUCTIVE dev-DB reseed)")
    s.add_argument("--yes", action="store_true", help="confirm the destructive reseed")
    s.set_defaults(func=cmd_reseed)

    s = sub.add_parser("from-proof", help="reuse /verify .proof shots as help step shots")
    s.add_argument("task")
    s.add_argument("--page", help="verified route, e.g. /mes-batch/plan/batch-orders")
    s.add_argument("--dest", help="screenshot dest (default: <repo> baselines desktop dir)")
    s.add_argument("--proof-dir", dest="proof_dir", help="explicit .proof screenshots dir override")
    s.add_argument("--no-article", action="store_true", help="copy shots only, don't scaffold markdown")
    s.add_argument("--build", action="store_true", help="run build after ingest")
    s.add_argument("--sync", action="store_true", help="run sync after build")
    s.add_argument("--commit", action="store_true")
    s.add_argument("--push", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_from_proof)

    s = sub.add_parser("persist-proof", help="copy+rename proof shots into --dest only (no article/build/sync)")
    s.add_argument("task")
    s.add_argument("--page", required=True, help="verified route")
    s.add_argument("--dest", required=True, help="durable baselines dir (e.g. MAIN checkout's)")
    s.add_argument("--proof-dir", dest="proof_dir", help="explicit .proof screenshots dir override")
    s.set_defaults(func=cmd_persist_proof)

    s = sub.add_parser("refresh", help="full pipeline: [reseed] -> capture|from-proof -> build -> sync")
    s.add_argument("--from-proof", dest="from_proof", help="reuse this task's proofs instead of capturing")
    s.add_argument("--page", help="route (required with --from-proof)")
    s.add_argument("--clean", action="store_true", help="reseed the dev DB first (DESTRUCTIVE)")
    s.add_argument("--skip-capture", action="store_true")
    s.add_argument("--skip-build", action="store_true")
    s.add_argument("--skip-website", action="store_true")
    s.add_argument("--commit", action="store_true")
    s.add_argument("--push", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--user", default="helpcenter_admin")
    s.add_argument("--password", default="pass.admin.pass")
    s.set_defaults(func=cmd_refresh)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
