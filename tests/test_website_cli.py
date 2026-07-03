"""Golden tests for website_cli release-note scaffolding.

Pins the user-decided release-notes vocabulary (2026-07-03, /help-center
Rollout): ee title "Uuendused <date>", heading "## Uuendused", bullet
"Loe lähemalt: [Title](url)." — a colon lead-in keeps the article title
nominative ("Vaata [Title]" is wrong case government; vaadata governs
partitive). The en surface is unchanged ("Release <date>" / "## Highlights"
/ "- See [...]."). The initial scaffold shipped the old vocabulary
(Väljalase/Esiletõstetud/Vaata) and was renamed by hand — these tests stop
it creeping back.
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

import website_cli


def _make_fake_repo(root: Path) -> Path:
    """Minimal tark-platform-shaped checkout: build-help.py + one en/ee article."""
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "build-help.py").write_text("# stub\n", encoding="utf-8")
    core = root / "docu" / "help" / "core"
    (core / "ee").mkdir(parents=True)
    (core / "time-tracking.md").write_text(
        '---\ntitle: "Time Tracking"\npage: "/workforce/time-tracking"\norder: 1\n---\n\nbody\n',
        encoding="utf-8",
    )
    (core / "ee" / "time-tracking.md").write_text(
        '---\ntitle: "Ajajälgimine"\npage: "/workforce/time-tracking"\norder: 1\n---\n\nkeha\n',
        encoding="utf-8",
    )
    return root


class ReleaseNoteScaffoldTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = _make_fake_repo(Path(tmp.name))
        self.en_path = self.repo / "docu" / "help" / "releases" / "2099-01-02-time-tracking.md"
        self.ee_path = self.repo / "docu" / "help" / "releases" / "ee" / "2099-01-02-time-tracking.md"

    def _run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = website_cli.main([
                "--repo", str(self.repo), "release-note",
                "--date", "2099-01-02", "--slugs", "core/time-tracking",
            ])
        return rc, out.getvalue()

    def test_scaffold_vocabulary_en_and_ee(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        en = self.en_path.read_text(encoding="utf-8")
        ee = self.ee_path.read_text(encoding="utf-8")

        # en surface: unchanged by the ee vocabulary decision
        self.assertIn('title: "Release 2099-01-02"', en)
        self.assertIn("## Highlights", en)
        self.assertIn("- See [Time Tracking](/help-center/core/time-tracking).", en)

        # ee surface: Uuendused vocabulary, never Väljalase/Esiletõstetud
        self.assertIn('title: "Uuendused 2099-01-02"', ee)
        self.assertIn("## Uuendused", ee)
        self.assertNotIn("Väljalase", ee)
        self.assertNotIn("Esiletõstetud", ee)

        # ee bullet: colon lead-in keeps the title nominative; "Vaata [" banned
        self.assertIn("- Loe lähemalt: [Ajajälgimine](/help-center/core/time-tracking).", ee)
        self.assertNotIn("Vaata [", ee)

        # both drafts + the console output point at the mandatory codex polish pass
        for text in (en, ee, out):
            self.assertIn("codex exec -m gpt-5.5", text)
            self.assertIn("/help-center", text)

    def test_existing_entries_left_as_is(self):
        rc1, _ = self._run()
        self.assertEqual(rc1, 0)
        before = self.en_path.read_text(encoding="utf-8")
        rc2, out2 = self._run()
        self.assertEqual(rc2, 0)
        self.assertIn("[exists]", out2)
        self.assertEqual(before, self.en_path.read_text(encoding="utf-8"))

    def test_console_output_is_ascii(self):
        # CLAUDE.md console rule: ASCII-only prints (markdown file CONTENT may
        # be Estonian; the terminal lines may not).
        rc, out = self._run()
        self.assertEqual(rc, 0)
        for line in out.splitlines():
            self.assertTrue(line.isascii(), f"non-ASCII console line: {line!r}")


if __name__ == "__main__":
    unittest.main()
