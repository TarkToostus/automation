"""Tests for tark_cli wiki body recovery.

Mirror of the server-side recovery in tark-platform's `_recover_wiki_body`.
The CLI catches the same JSON-double-encode and naked-escape mistakes locally
so the caller sees a warning before the request leaves their machine.
"""
import json
import sys
import unittest
from pathlib import Path

_AUTOMATION_DIR = str(Path(__file__).resolve().parent.parent)
if _AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, _AUTOMATION_DIR)

import tark_cli


class WikiRecoveryTests(unittest.TestCase):
    def test_unwrap_json_quoted_string(self):
        original = "## Brief\n\nLine one.\n\n- bullet\n"
        wrapped = json.dumps(original)
        recovered, notice = tark_cli._recover_wiki_body(wrapped)
        self.assertEqual(recovered, original)
        self.assertIsNotNone(notice)
        self.assertIn('unwrapped', notice)

    def test_unwrap_naked_escape(self):
        corrupted = (
            "## Plan\\n\\nApproach\\n\\nThe data flow gap is set at import "
            "but is not propagated to traveller. The model is already correct "
            "(both exist; has a FK to). The fix touches the serializer import "
            "logic, a backfill migration, and one frontend view.\\n\\n"
            "### Backend\\n\\n1. WorksOrderImport (line 441): After resolving, "
            "if value is not in dest (or is null) and source is set, inject and "
            "update.\\n\\n2. Serializer (line 181): In the update branch, "
            "cascade to linked objects.\\n\\n### Frontend\\n\\nExtend TS type "
            "(line 69). Show formatted date when present.\\n"
        )
        recovered, notice = tark_cli._recover_wiki_body(corrupted)
        self.assertNotIn('\\n', recovered)
        self.assertTrue(recovered.startswith('## Plan\n'))
        self.assertIn('### Backend', recovered)
        self.assertIsNotNone(notice)
        self.assertIn('un-escaped', notice)

    def test_already_correct_markdown_untouched(self):
        original = (
            "## Brief\n\nThis is fine.\n\n"
            'Some Python code: `print("hello\\nworld")` — the `\\n` here is '
            "a literal escape in a code span and must NOT be expanded.\n\n"
            "More content. " + ("Padding text. " * 20) + "\n"
        )
        recovered, notice = tark_cli._recover_wiki_body(original)
        self.assertEqual(recovered, original)
        self.assertIsNone(notice)

    def test_short_corrupted_line_is_not_touched(self):
        body = "Use `\\n` in regex.\nThis is a normal short paragraph.\n"
        recovered, notice = tark_cli._recover_wiki_body(body)
        self.assertEqual(recovered, body)
        self.assertIsNone(notice)

    def test_empty_and_none(self):
        self.assertEqual(tark_cli._recover_wiki_body(''), ('', None))
        self.assertEqual(tark_cli._recover_wiki_body(None), (None, None))

    def test_recovers_json_with_escaped_quotes(self):
        original = '## Brief\n\nHe said "hello".\n'
        wrapped = json.dumps(original)
        recovered, notice = tark_cli._recover_wiki_body(wrapped)
        self.assertEqual(recovered, original)
        self.assertIsNotNone(notice)


if __name__ == '__main__':
    unittest.main()
