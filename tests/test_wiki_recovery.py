"""Tests for tark_cli wiki body recovery.

Mirror of the server-side recovery in tark-platform's `_recover_wiki_body`.
The CLI catches the same JSON-double-encode and naked-escape mistakes locally
so the caller sees a warning before the request leaves their machine.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

import tark_cli


_SHARED_FIXTURE = Path(
    os.environ.get(
        'WIKI_RECOVERY_FIXTURE',
        str(_AUTOMATION_DIR.parent / 'shared' / 'wiki_recovery_cases.json'),
    )
)


class WikiRecoveryTests(unittest.TestCase):
    def test_unwrap_json_quoted_string(self):
        original = "## Brief\n\nLine one.\n\n- bullet\n"
        wrapped = json.dumps(original)
        recovered, reason, params = tark_cli._recover_wiki_body(wrapped)
        self.assertEqual(recovered, original)
        self.assertEqual(reason, tark_cli._REASON_JSON_QUOTED)
        self.assertEqual(params, {})

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
        recovered, reason, params = tark_cli._recover_wiki_body(corrupted)
        self.assertNotIn('\\n', recovered)
        self.assertTrue(recovered.startswith('## Plan\n'))
        self.assertIn('### Backend', recovered)
        self.assertEqual(reason, tark_cli._REASON_NAKED_ESCAPE)
        self.assertGreater(params['max_line'], 500)
        self.assertGreaterEqual(params['literal_n'], 5)

    def test_already_correct_markdown_untouched(self):
        original = (
            "## Brief\n\nThis is fine.\n\n"
            'Some Python code: `print("hello\\nworld")` — the `\\n` here is '
            "a literal escape in a code span and must NOT be expanded.\n\n"
            "More content. " + ("Padding text. " * 20) + "\n"
        )
        recovered, reason, params = tark_cli._recover_wiki_body(original)
        self.assertEqual(recovered, original)
        self.assertIsNone(reason)
        self.assertEqual(params, {})

    def test_short_corrupted_line_is_not_touched(self):
        body = "Use `\\n` in regex.\nThis is a normal short paragraph.\n"
        recovered, reason, params = tark_cli._recover_wiki_body(body)
        self.assertEqual(recovered, body)
        self.assertIsNone(reason)
        self.assertEqual(params, {})

    def test_empty_and_none(self):
        self.assertEqual(tark_cli._recover_wiki_body(''), ('', None, {}))
        self.assertEqual(tark_cli._recover_wiki_body(None), (None, None, {}))

    def test_recovers_json_with_escaped_quotes(self):
        original = '## Brief\n\nHe said "hello".\n'
        wrapped = json.dumps(original)
        recovered, reason, params = tark_cli._recover_wiki_body(wrapped)
        self.assertEqual(recovered, original)
        self.assertEqual(reason, tark_cli._REASON_JSON_QUOTED)
        self.assertEqual(params, {})

    def test_long_table_row_with_real_backslash_n_is_untouched(self):
        """False-positive guard: parity with the server-side test of the same
        name. Documents current (still-eats) behaviour so future tightening
        flips a single assertion.
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
        recovered, reason, params = tark_cli._recover_wiki_body(body)
        if reason is not None:
            self.assertEqual(reason, tark_cli._REASON_NAKED_ESCAPE)
            self.assertGreater(params['max_line'], 500)
        else:
            self.assertEqual(recovered, body)

    def test_format_recovery_notice_renders_both_reasons(self):
        self.assertIn(
            'JSON-quoted',
            tark_cli._format_recovery_notice(tark_cli._REASON_JSON_QUOTED, {}),
        )
        rendered = tark_cli._format_recovery_notice(
            tark_cli._REASON_NAKED_ESCAPE, {'max_line': 812, 'literal_n': 12}
        )
        self.assertIn('812', rendered)
        self.assertIn('12', rendered)
        # unknown reason falls back to raw, never crashes
        self.assertIn('whatever', tark_cli._format_recovery_notice('whatever', {}))

    @unittest.skipUnless(
        _SHARED_FIXTURE.exists(),
        f'shared fixture not at {_SHARED_FIXTURE}',
    )
    def test_shared_fixture_contract(self):
        """Run every case from _tark/shared/wiki_recovery_cases.json.

        Cross-repo contract: the server-side test in tark-platform runs the
        same fixture against its own _recover_wiki_body. Drift in either
        implementation fails locally before the umbrella repo merges.
        """
        data = json.loads(_SHARED_FIXTURE.read_text())
        for case in data['cases']:
            with self.subTest(case=case['name']):
                body, reason, params = tark_cli._recover_wiki_body(case['input'])
                self.assertEqual(body, case['expected_body'], 'body mismatch')
                self.assertEqual(reason, case['expected_reason'], 'reason mismatch')
                self.assertEqual(params, case['expected_params'], 'params mismatch')


class WikiResolveBodyTests(unittest.TestCase):
    """Cover the body-source precedence wrapper. Recovery is tested above;
    this suite is about argument handling + I/O."""

    def _ns(self, **kw):
        defaults = {'body': None, 'from_file': None, 'from_stdin': False}
        defaults.update(kw)
        return Namespace(**defaults)

    def test_returns_none_when_no_source_given(self):
        self.assertIsNone(tark_cli._resolve_body(self._ns()))

    def test_body_arg_takes_precedence_over_file(self):
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False) as fh:
            fh.write('## From file\n')
            path = fh.name
        try:
            ns = self._ns(body='## From arg\n', from_file=path)
            self.assertEqual(tark_cli._resolve_body(ns), '## From arg\n')
        finally:
            os.unlink(path)

    def test_body_arg_takes_precedence_over_stdin(self):
        ns = self._ns(body='## From arg\n', from_stdin=True)
        with mock.patch.object(sys, 'stdin', io.StringIO('## From stdin\n')):
            self.assertEqual(tark_cli._resolve_body(ns), '## From arg\n')

    def test_from_file_reads_utf8(self):
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as fh:
            fh.write('## Plaan\n\nKõik kombes.\n')
            path = fh.name
        try:
            self.assertEqual(
                tark_cli._resolve_body(self._ns(from_file=path)),
                '## Plaan\n\nKõik kombes.\n',
            )
        finally:
            os.unlink(path)

    def test_from_file_missing_returns_none_and_exits(self):
        ns = self._ns(from_file='/nonexistent/path/to/wiki.md')
        with self.assertRaises(SystemExit):
            tark_cli._resolve_body(ns)

    def test_from_stdin_reads(self):
        ns = self._ns(from_stdin=True)
        with mock.patch.object(sys, 'stdin', io.StringIO('## From stdin\n')):
            self.assertEqual(tark_cli._resolve_body(ns), '## From stdin\n')

    def test_recovery_warning_fires_for_json_quoted(self):
        wrapped = json.dumps('## Brief\n\nBody.\n')
        ns = self._ns(body=wrapped)
        with mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            result = tark_cli._resolve_body(ns)
        self.assertEqual(result, '## Brief\n\nBody.\n')
        stderr = err.getvalue()
        self.assertIn('JSON-quoted', stderr)
        self.assertIn(f'reason={tark_cli._REASON_JSON_QUOTED}', stderr)

    def test_no_warning_on_clean_body(self):
        ns = self._ns(body='## Brief\n\nBody.\n')
        with mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            tark_cli._resolve_body(ns)
        self.assertEqual(err.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
