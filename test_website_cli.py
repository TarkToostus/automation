#!/usr/bin/env python3
"""Unit tests for website_cli pure content-ops. Stdlib unittest, no deps.

Run: python3 -m unittest automation.test_website_cli   (from _tark/)
  or: python3 automation/test_website_cli.py
"""

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


class EstonianLocaleIsEt(unittest.TestCase):
    """Estonian is `et`. tark-platform #969 (2026-07-23) deleted every
    docu/help/<module>/ee/ dir and build-help.py reads LOCALES=('en','et'),
    so an `ee` path here silently degrades to English or publishes nothing.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_mirror(self, locale):
        d = self.repo / "docu" / "help" / "mes-batch" / locale
        d.mkdir(parents=True, exist_ok=True)
        (d / "batch-orders.md").write_text(
            '---\ntitle: "Tee partii tellimus"\npage: "/mes-batch/plan/batch-orders"\n---\nsisu\n'
        )

    def test_et_mirror_title_is_used(self):
        self._write_mirror("et")
        _, _, en_title, et_title, _ = w.resolve_release_item(self.repo, "mes-batch/batch-orders")
        self.assertEqual(en_title, "Run a batch order")
        self.assertEqual(et_title, "Tee partii tellimus")

    def test_ee_mirror_is_ignored_not_silently_used(self):
        # A resurrected ee/ dir must NOT be picked up — it would mask the fact
        # that build-help.py never publishes it.
        self._write_mirror("ee")
        _, _, en_title, et_title, _ = w.resolve_release_item(self.repo, "mes-batch/batch-orders")
        self.assertEqual(et_title, en_title, "ee/ mirror must not resolve an Estonian title")

    def test_no_mirror_falls_back_to_english(self):
        _, _, en_title, et_title, _ = w.resolve_release_item(self.repo, "mes-batch/batch-orders")
        self.assertEqual(et_title, en_title)

    def test_source_carries_no_ee_locale_path(self):
        """Vacuity guard: assert against the file on disk, not a stub."""
        src = Path(w.__file__).read_text(encoding="utf-8")
        offenders = [
            ln
            for i, ln in enumerate(src.splitlines(), 1)
            if ('"ee"' in ln or "'ee'" in ln or "/ee/" in ln) and not ln.lstrip().startswith("#")
        ]
        self.assertEqual(offenders, [], f"stale ee locale path in website_cli.py: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
