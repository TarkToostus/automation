#!/usr/bin/env python3
"""Unit tests for website_cli pure content-ops. Stdlib unittest, no deps.

Run: python3 -m unittest automation.test_website_cli   (from _tark/)
  or: python3 automation/test_website_cli.py
"""

import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import website_cli as w


def make_repo(tmp):
    """Minimal fake tark-platform checkout for the pure functions."""
    repo = Path(tmp)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "build-help.py").write_text("# stub\n")
    help_dir = repo / "docu" / "help"
    (help_dir / "mes-batch").mkdir(parents=True)
    (help_dir / "mes-batch" / "batch-orders.md").write_text(
        '---\ntitle: "Run a batch order"\npage: "/mes-batch/plan/batch-orders"\norder: 5\n---\n\nbody\n'
    )
    (help_dir / "mes-batch" / "_index.md").write_text('---\ntitle: "MES"\n---\n')
    (help_dir / "workforce").mkdir(parents=True)
    (help_dir / "workforce" / "employees.md").write_text(
        '---\npage: "/workforce/people/employees"\n---\nbody\n'
    )
    return repo


class RouteMapping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_match(self):
        module, slug, art = w.route_to_module_page(self.repo, "/mes-batch/plan/batch-orders")
        self.assertEqual((module, slug), ("mes-batch", "batch-orders"))
        self.assertIsNotNone(art)

    def test_trailing_slash_normalized(self):
        module, slug, _ = w.route_to_module_page(self.repo, "/mes-batch/plan/batch-orders/")
        self.assertEqual((module, slug), ("mes-batch", "batch-orders"))

    def test_deeper_route_prefix_matches_article(self):
        module, slug, art = w.route_to_module_page(self.repo, "/mes-batch/plan/batch-orders/123/edit")
        self.assertEqual((module, slug), ("mes-batch", "batch-orders"))
        self.assertIsNotNone(art)

    def test_no_match_derives_from_route(self):
        module, slug, art = w.route_to_module_page(self.repo, "/iot/sensors/new")
        self.assertEqual((module, slug), ("iot", "new"))
        self.assertIsNone(art)

    def test_index_md_skipped(self):
        routes = [page for _, _, page in w.iter_help_articles(self.repo)]
        self.assertIn("/mes-batch/plan/batch-orders", routes)
        self.assertNotIn("", [r for r in routes if r == "_index"])


class CopyShots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proof = Path(self.tmp.name) / "proof"
        self.proof.mkdir()
        for n in ("01-new-order.png", "02-confirm.png", "03-shopfloor.png"):
            (self.proof / n).write_bytes(b"\x89PNG")
        self.dest = Path(self.tmp.name) / "desktop"

    def tearDown(self):
        self.tmp.cleanup()

    def test_renames_with_module_page_prefix(self):
        names = w.copy_shots(self.proof, self.dest, "mes-batch", "batch-orders")
        self.assertIn("mes-batch--batch-orders--01-new-order.png", names)
        self.assertIn("mes-batch--batch-orders--02-confirm.png", names)
        self.assertTrue((self.dest / "mes-batch--batch-orders--03-shopfloor.png").exists())

    def test_hero_created_from_lowest_nn(self):
        w.copy_shots(self.proof, self.dest, "mes-batch", "batch-orders")
        hero = self.dest / "mes-batch--batch-orders.png"
        self.assertTrue(hero.exists())
        self.assertEqual(hero.read_bytes(), (self.proof / "01-new-order.png").read_bytes())

    def test_no_hero_when_disabled(self):
        w.copy_shots(self.proof, self.dest, "mes-batch", "batch-orders", make_hero=False)
        self.assertFalse((self.dest / "mes-batch--batch-orders.png").exists())


class Scaffold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)
        self.proof = Path(self.tmp.name) / "proof"
        self.proof.mkdir()
        for n in ("01-open.png", "02-confirm.png"):
            (self.proof / n).write_bytes(b"\x89PNG")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scaffolds_when_absent(self):
        art, created = w.scaffold_article(self.repo, "iot", "sensors", "/iot/sensors", self.proof)
        self.assertTrue(created)
        text = art.read_text()
        self.assertIn('page: "/iot/sensors"', text)
        self.assertIn("![Confirm](02-confirm)", text)
        self.assertIn("## Steps", text)

    def test_does_not_clobber_existing(self):
        art, created = w.scaffold_article(
            self.repo, "mes-batch", "batch-orders", "/mes-batch/plan/batch-orders", self.proof
        )
        self.assertFalse(created)
        self.assertIn("Run a batch order", art.read_text())  # original authored content intact


class ProofLocate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_local_proof(self):
        d = self.repo / ".proof" / "4654" / "screenshots"
        d.mkdir(parents=True)
        (d / "01-x.png").write_bytes(b"\x89PNG")
        self.assertEqual(w.find_proof_dir(self.repo, "4654"), d)

    def test_missing_returns_none(self):
        self.assertIsNone(w.find_proof_dir(self.repo, "9999"))

    def test_override_dir(self):
        d = Path(self.tmp.name) / "elsewhere"
        d.mkdir()
        self.assertEqual(w.find_proof_dir(self.repo, "1", override=str(d)), d.resolve())


class PageIdResolution(unittest.TestCase):
    """coverage's PAGE_ID accepts 4 forms: route, module/slug, module--slug,
    and a bare usePagePath-style slug key."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_route_form(self):
        module, slug, art = w.resolve_page_id(self.repo, "/mes-batch/plan/batch-orders")
        self.assertEqual((module, slug), ("mes-batch", "batch-orders"))
        self.assertIsNotNone(art)

    def test_module_slash_slug_form(self):
        module, slug, art = w.resolve_page_id(self.repo, "workforce/employees")
        self.assertEqual((module, slug), ("workforce", "employees"))
        self.assertIsNotNone(art)

    def test_module_dashdash_slug_form(self):
        module, slug, art = w.resolve_page_id(self.repo, "mes-batch--batch-orders")
        self.assertEqual((module, slug), ("mes-batch", "batch-orders"))
        self.assertIsNotNone(art)

    def test_bare_usepagepath_key_form(self):
        module, slug, art = w.resolve_page_id(self.repo, "employees")
        self.assertEqual((module, slug), ("workforce", "employees"))
        self.assertIsNotNone(art)

    def test_unresolvable_id_has_no_article(self):
        module, slug, art = w.resolve_page_id(self.repo, "/nowhere/at-all")
        self.assertIsNone(art)


class CoverageProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_page_fails_the_gate(self):
        result = w.coverage_for_page(self.repo, "/nowhere/at-all", 28, 50)
        self.assertEqual(result["status"], "MISSING")
        self.assertFalse(result["exists"])
        self.assertTrue(result["reasons"])

    def test_brand_new_uncommitted_doc_reads_fresh(self):
        # the fixture repo is NOT a git checkout, so `git log` finds no history
        # for the article -- the same signal a real just-scaffolded, uncommitted
        # doc gives. Contract: reads FRESH, not STALE, by design.
        result = w.coverage_for_page(self.repo, "/mes-batch/plan/batch-orders", 28, 50)
        self.assertEqual(result["status"], "FRESH")
        self.assertIsNone(result["age_days"])

    def test_human_renders_one_line_per_page_and_exit_1_on_any_miss(self):
        args = argparse.Namespace(
            repo=str(self.repo),
            pages=["/mes-batch/plan/batch-orders", "/nowhere/at-all"],
            human=True,
            max_age_days=28,
            max_loc_delta=50,
            source=None,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = w.cmd_coverage(args)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertIn("[OK]", lines[0])
        self.assertIn("[MISS]", lines[1])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
