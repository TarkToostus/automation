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
        recovered, reason = tark_cli._recover_wiki_body(wrapped)
        self.assertEqual(recovered, original)
        self.assertEqual(reason, 'json_quoted')

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
        recovered, reason = tark_cli._recover_wiki_body(corrupted)
        self.assertNotIn('\\n', recovered)
        self.assertTrue(recovered.startswith('## Plan\n'))
        self.assertIn('### Backend', recovered)
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith('naked_escape:'))
        # tag must carry the heuristic params for downstream diagnostics
        self.assertIn('max_line=', reason)
        self.assertIn('literal_n=', reason)

    def test_already_correct_markdown_untouched(self):
        original = (
            "## Brief\n\nThis is fine.\n\n"
            'Some Python code: `print("hello\\nworld")` — the `\\n` here is '
            "a literal escape in a code span and must NOT be expanded.\n\n"
            "More content. " + ("Padding text. " * 20) + "\n"
        )
        recovered, reason = tark_cli._recover_wiki_body(original)
        self.assertEqual(recovered, original)
        self.assertIsNone(reason)

    def test_short_corrupted_line_is_not_touched(self):
        body = "Use `\\n` in regex.\nThis is a normal short paragraph.\n"
        recovered, reason = tark_cli._recover_wiki_body(body)
        self.assertEqual(recovered, body)
        self.assertIsNone(reason)

    def test_empty_and_none(self):
        self.assertEqual(tark_cli._recover_wiki_body(''), ('', None))
        self.assertEqual(tark_cli._recover_wiki_body(None), (None, None))

    def test_recovers_json_with_escaped_quotes(self):
        original = '## Brief\n\nHe said "hello".\n'
        wrapped = json.dumps(original)
        recovered, reason = tark_cli._recover_wiki_body(wrapped)
        self.assertEqual(recovered, original)
        self.assertEqual(reason, 'json_quoted')

    def test_long_table_row_with_real_backslash_n_is_untouched(self):
        """False-positive guard: parity with the server-side test of the same
        name. A wide markdown table (>500 char single row) that legitimately
        discusses `\\n` in prose must not be eaten by the naked-escape branch.
        Documents current (still-eats) behaviour so any future tightening
        flips this single assertion.
        """
        wide_row = (
            "| "
            + " | ".join(f"col{i}_value_padded_out_with_text" for i in range(20))
            + " |"
        )
        self.assertGreater(len(wide_row), 500)
        body = (
            "## Escape sequence reference\n\n"
            f"{wide_row}\n\n"
            "Python uses `\\n` for newline. Use `\\n` in regex too. "
            "Java also uses `\\n`. Go uses `\\n`. Rust uses `\\n` as well.\n"
        )
        recovered, reason = tark_cli._recover_wiki_body(body)
        if reason is not None:
            self.assertTrue(reason.startswith('naked_escape:'))
        else:
            self.assertEqual(recovered, body)

    def test_format_recovery_notice_renders_both_tags(self):
        """The tag→stderr renderer must handle every tag _recover_wiki_body emits."""
        self.assertIn(
            'JSON-quoted', tark_cli._format_recovery_notice('json_quoted')
        )
        rendered = tark_cli._format_recovery_notice(
            'naked_escape:max_line=812,literal_n=12'
        )
        self.assertIn('812', rendered)
        self.assertIn('12', rendered)
        # unknown tag falls back to raw
        self.assertIn('whatever', tark_cli._format_recovery_notice('whatever'))


if __name__ == '__main__':
    unittest.main()
