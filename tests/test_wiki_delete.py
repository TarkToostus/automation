"""Tests for `wiki <task-id> delete --section <h>`.

No network: `tark_cli._get` and `tark_cli._post` are mocked.

Why the exact-match preflight is the load-bearing part: the server runs TWO
header matchers that disagree. Stage gates use a PREFIX match (`_wiki_has_section`
-- "Verify" hits "## Verify: Phase 1"), while `_wiki_delete_section` compares the
title VERBATIM. The CLI already had a mirror of the prefix matcher
(`_wiki_section_exists`), and reusing it here would have green-lit a delete the
server then 404s on. `_wiki_exact_sections` mirrors the delete-side matcher.
"""
import io
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

import tark_cli  # noqa: E402

WIKI = """## Brief

first brief body

## Verify: Phase 1

verify body

## Brief

second brief body
"""


def _ns(task_id=42, section='Brief', **overrides):
    d = dict(task_id=task_id, action='delete', section=section, body=None,
             from_file=None, from_stdin=False, force=False, yes=False, json=False)
    d.update(overrides)
    return Namespace(**d)


def _run(args, wiki=WIKI, post_result=None):
    """Drive cmd_wiki end-to-end; return (posted_body_or_None, stdout, stderr)."""
    posted = {}

    def fake_get(path, **params):
        return {'id': args.task_id, 'wiki': wiki}

    def fake_post(path, body):
        posted['path'] = path
        posted['body'] = body
        return post_result if post_result is not None else {'id': args.task_id, 'wiki': ''}

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(tark_cli, '_get', fake_get), \
         mock.patch.object(tark_cli, '_post', fake_post), \
         mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err):
        try:
            tark_cli.cmd_wiki(args)
        except SystemExit:
            pass
    return (posted or None), out.getvalue(), err.getvalue()


class ExactSectionMatching(unittest.TestCase):
    def test_exact_match_finds_every_duplicate(self):
        self.assertEqual(len(tark_cli._wiki_exact_sections(WIKI, 'Brief')), 2)

    def test_prefix_only_header_is_not_an_exact_match(self):
        """"Verify" prefix-matches "## Verify: Phase 1" for the stage gate, but the
        delete op would 404 on it -- the preflight must agree with the server."""
        self.assertTrue(tark_cli._wiki_section_exists(WIKI, 'Verify'))
        self.assertEqual(tark_cli._wiki_exact_sections(WIKI, 'Verify'), [])

    def test_span_ends_at_the_next_header_not_the_end_of_the_wiki(self):
        (start, end), _second = tark_cli._wiki_exact_sections(WIKI, 'Brief')
        self.assertEqual(WIKI[start:end].strip().splitlines()[0], '## Brief')
        self.assertNotIn('Verify', WIKI[start:end])

    def test_near_miss_titles_are_reported_for_a_failed_match(self):
        self.assertEqual(tark_cli._wiki_prefix_titles(WIKI, 'Verify'), ['Verify: Phase 1'])


class DeleteGuards(unittest.TestCase):
    def test_without_yes_it_is_a_dry_run_and_posts_nothing(self):
        posted, out, err = _run(_ns())
        self.assertIsNone(posted)
        self.assertIn('would remove "## Brief"', out)
        self.assertIn('--yes', err)

    def test_missing_section_flag_errors_before_any_request(self):
        posted, _out, err = _run(_ns(section=None))
        self.assertIsNone(posted)
        self.assertIn('--section', err)

    def test_unknown_header_fails_locally_with_near_miss_hint(self):
        posted, _out, err = _run(_ns(section='Verify', yes=True))
        self.assertIsNone(posted)
        self.assertIn('Verify: Phase 1', err)

    def test_hash_prefixed_section_is_normalised(self):
        posted, _out, _err = _run(_ns(section='## Brief', yes=True))
        self.assertEqual(posted['body']['section'], 'Brief')

    def test_bare_hash_section_is_rejected(self):
        posted, _out, err = _run(_ns(section='##', yes=True))
        self.assertIsNone(posted)
        self.assertIn('header', err)


class DeleteRequest(unittest.TestCase):
    def test_posts_the_delete_action_with_no_body_field(self):
        posted, _out, _err = _run(_ns(yes=True))
        self.assertEqual(posted['path'], '/api/v1/pat/pm/tasks/42/wiki/')
        self.assertEqual(posted['body'], {'action': 'delete', 'section': 'Brief'})

    def test_duplicate_header_warns_that_only_one_copy_goes(self):
        _posted, out, err = _run(_ns(yes=True))
        self.assertIn('first of 2 copies', out)
        self.assertIn('appears 2x', err)

    def test_remaining_duplicate_count_is_reported_from_the_response(self):
        remaining = '## Brief\n\nsecond brief body\n'
        _posted, out, _err = _run(_ns(yes=True), post_result={'id': 42, 'wiki': remaining})
        self.assertIn('1 copy of that header remains', out)

    def test_remaining_plural_when_more_than_one_copy_is_left(self):
        remaining = '## Brief\n\nb\n\n## Brief\n\nc\n'
        _posted, out, _err = _run(_ns(yes=True), post_result={'id': 42, 'wiki': remaining})
        self.assertIn('2 copies of that header remain', out)

    def test_single_section_delete_reports_no_remainder(self):
        wiki = '## Brief\n\nonly body\n'
        _posted, out, err = _run(_ns(yes=True), wiki=wiki)
        self.assertIn('wiki delete OK', out)
        self.assertNotIn('copies', out)
        self.assertNotIn('appears', err)

    def test_json_mode_emits_the_server_payload(self):
        import json
        _posted, out, _err = _run(_ns(yes=True, json=True), post_result={'id': 42, 'wiki': 'x'})
        self.assertEqual(json.loads(out), {'id': 42, 'wiki': 'x'})

    def test_json_dry_run_leaves_stdout_empty(self):
        """House rule (tests/test_deps.py): a `--json` caller must never find
        non-JSON on stdout. The dry run prints its preview, so under --json that
        preview has to go to stderr and stdout has to stay parseable-or-empty."""
        posted, out, err = _run(_ns(json=True))
        self.assertIsNone(posted)
        self.assertEqual(out, '')
        self.assertIn('would remove "## Brief"', err)

    def test_json_unknown_header_leaves_stdout_empty(self):
        posted, out, err = _run(_ns(section='Verify', yes=True, json=True))
        self.assertIsNone(posted)
        self.assertEqual(out, '')
        self.assertIn('Verify: Phase 1', err)

    def test_section_body_is_never_echoed_to_stdout(self):
        """The preflight GET is deliberately not routed through the safety screen,
        so it must report the section's SIZE and never its content."""
        _posted, out, err = _run(_ns(yes=True))
        self.assertNotIn('first brief body', out + err)

    def test_untrusted_header_escape_bytes_are_stripped(self):
        """Section titles are other people's text. A near-miss hint echoes them,
        so an ESC byte in a title must not reach the terminal raw."""
        wiki = '## Brief\x1b[2J\x07 evil\n\nbody\n'
        _posted, out, err = _run(_ns(section='Brief', yes=True), wiki=wiki)
        self.assertIn('Brief[2J evil', err)
        self.assertNotIn('\x1b', out + err)
        self.assertNotIn('\x07', out + err)


class ScopeErrorSurfacing(unittest.TestCase):
    """A 403 on the delete op means pm:delete is missing. The path-derived hint
    says "add pm:write" -- which the caller already holds -- so the server's own
    `detail` has to win."""

    def _403(self, payload):
        import urllib.error
        err = urllib.error.HTTPError(
            'http://x/api/v1/pat/pm/tasks/42/wiki/', 403, 'Forbidden', {}, io.BytesIO(payload))
        with mock.patch.object(tark_cli.urllib.request, 'urlopen', side_effect=err), \
             mock.patch.object(tark_cli, '_get_url', lambda: 'http://x'), \
             mock.patch.object(tark_cli, '_get_pat', lambda: 't'), \
             mock.patch.object(sys, 'stderr', io.StringIO()) as cap:
            with self.assertRaises(SystemExit):
                tark_cli._request('POST', '/api/v1/pat/pm/tasks/42/wiki/', body={'action': 'delete'})
            return cap.getvalue()

    def test_server_detail_names_the_real_missing_scope(self):
        msg = self._403(b'{"detail": "pm:delete scope required"}')
        self.assertIn('pm:delete scope required', msg)
        self.assertNotIn('pm:write', msg)

    def test_detailless_403_still_falls_back_to_the_path_hint(self):
        msg = self._403(b'')
        self.assertIn('pm:write', msg)

    def test_generic_drf_detail_does_not_swallow_the_scope_hint(self):
        """REGRESSION GUARD. `PATScope.has_permission` sets no custom message, so
        EVERY missing-scope denial in the fleet returns DRF's boilerplate. If a
        bare `detail` were allowed to win, all ~30 other commands would trade an
        actionable "add pm:write" for "you do not have permission" -- and the
        detail-less fallback below would be dead code in production."""
        msg = self._403(b'{"detail": "You do not have permission to perform this action."}')
        self.assertIn('You do not have permission', msg)
        self.assertIn('Add pm:write scope', msg)

    def test_generic_drf_detail_keeps_the_c2_hint_on_c2_paths(self):
        import urllib.error
        err = urllib.error.HTTPError(
            'http://x/api/v1/pat/c2/deployments/', 403, 'Forbidden', {},
            io.BytesIO(b'{"detail": "You do not have permission to perform this action."}'))
        with mock.patch.object(tark_cli.urllib.request, 'urlopen', side_effect=err), \
             mock.patch.object(tark_cli, '_get_url', lambda: 'http://x'), \
             mock.patch.object(tark_cli, '_get_pat', lambda: 't'), \
             mock.patch.object(sys, 'stderr', io.StringIO()) as cap:
            with self.assertRaises(SystemExit):
                tark_cli._request('GET', '/api/v1/pat/c2/deployments/')
        self.assertIn('Add c2:read scope', cap.getvalue())

    def test_403_detail_escape_bytes_are_stripped(self):
        msg = self._403(b'{"detail": "pm:delete scope required\\u001b[2J"}')
        self.assertIn('pm:delete scope required', msg)
        self.assertNotIn('\x1b', msg)


if __name__ == '__main__':
    unittest.main()


class UpsertUsesTheExactMatcher(unittest.TestCase):
    """`set` must predict what the server's `replace` will do. `_wiki_replace_section`
    compares titles verbatim; the prefix matcher the stage gates use said "Verify"
    was present in a wiki holding only "## Verify: Phase 1", so `set` chose
    `replace` and the server 404'd on a section the CLI had just reported present.
    Hit live against c2-prelive on 2026-07-23 while writing this branch's own
    evidence section."""

    def _run_set(self, action, section, wiki, force=False):
        posted = {}

        def fake_get(path, **params):
            return {'id': 9, 'wiki': wiki}

        def fake_post(path, body):
            posted['body'] = body
            return {'id': 9, 'wiki': wiki}

        args = Namespace(task_id=9, action=action, section=section, body='x', from_file=None,
                         from_stdin=False, force=force, yes=False, json=False)
        with mock.patch.object(tark_cli, '_get', fake_get), \
             mock.patch.object(tark_cli, '_post', fake_post), \
             mock.patch.object(sys, 'stdout', io.StringIO()), \
             mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            try:
                tark_cli.cmd_wiki(args)
            except SystemExit:
                pass
        return posted.get('body'), err.getvalue()

    def test_set_appends_when_only_a_prefix_sibling_exists(self):
        body, _err = self._run_set('set', 'Verify', '## Verify: Phase 1\n\nevidence\n')
        self.assertEqual(body['action'], 'append')

    def test_set_replaces_on_a_real_exact_match(self):
        body, _err = self._run_set('set', 'Verify', '## Verify\n\nevidence\n')
        self.assertEqual(body['action'], 'replace')

    def test_append_does_not_refuse_over_a_prefix_sibling(self):
        body, _err = self._run_set('append', 'Verify', '## Verify: Phase 1\n\nevidence\n')
        self.assertEqual(body['action'], 'append')

    def test_append_still_refuses_a_real_duplicate(self):
        body, err = self._run_set('append', 'Verify', '## Verify\n\nevidence\n')
        self.assertIsNone(body)
        self.assertIn('already exists', err)
