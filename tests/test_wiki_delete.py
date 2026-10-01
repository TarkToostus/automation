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
        EVERY missing-scope denial on the server returns DRF's boilerplate. If a
        bare `detail` were allowed to win, all ~30 other commands would trade an
        actionable "add pm:write" for "you do not have permission" -- and the
        detail-less fallback below would be dead code in production."""
        msg = self._403(b'{"detail": "You do not have permission to perform this action."}')
        self.assertIn('You do not have permission', msg)
        self.assertIn('Add pm:write scope', msg)

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
    Hit live against a staging server while writing a wiki evidence section."""

    def _run_set(self, action, section, wiki, force=False):
        """Drive the verb end-to-end against the stateful `_FakeWikiServer`.

        This used to mock `_get` with a closure returning the PRE-write wiki, so the
        read-back could never see the write: every case below exited 2 after TWO
        posts and the assertion landed on the RETRY's payload, not the decision under
        test. Green by construction, discriminating nothing. Returns the FIRST post's
        body, the exit code and stderr.
        """
        srv = _FakeWikiServer(wiki)
        args = Namespace(task_id=9, action=action, section=section, body='x',
                         from_file=None, from_stdin=False, force=force, yes=False,
                         json=False)
        code = 0
        with mock.patch.object(tark_cli, '_get', srv.get), \
             mock.patch.object(tark_cli, '_post', srv.post), \
             mock.patch.object(sys, 'stdout', io.StringIO()), \
             mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            try:
                tark_cli.cmd_wiki(args)
            except SystemExit as exc:
                code = exc.code
        first = srv.posts[0] if srv.posts else None
        return first, code, err.getvalue()

    def test_set_appends_when_only_a_prefix_sibling_exists(self):
        body, code, _err = self._run_set('set', 'Verify', '## Verify: Phase 1\n\nevidence\n')
        self.assertEqual(body['action'], 'append')
        self.assertEqual(code, 0, _err)

    def test_set_replaces_on_a_real_exact_match(self):
        body, code, _err = self._run_set('set', 'Verify', '## Verify\n\nevidence\n')
        self.assertEqual(body['action'], 'replace')
        self.assertEqual(code, 0, _err)

    def test_append_does_not_refuse_over_a_prefix_sibling(self):
        body, code, _err = self._run_set('append', 'Verify', '## Verify: Phase 1\n\nevidence\n')
        self.assertEqual(body['action'], 'append')
        self.assertEqual(code, 0, _err)

    def test_append_merges_onto_a_real_exact_match(self):
        """Contract: `append` onto an existing exact-match
        section no longer refuses -- it merges (GET -> merge -> write) by
        sending the server a `replace` with the old body plus the new text."""
        body, code, _err = self._run_set('append', 'Verify', '## Verify\n\nevidence\n')
        self.assertEqual(body['action'], 'replace')
        self.assertIn('evidence', body['body'])
        self.assertIn('x', body['body'])
        self.assertEqual(code, 0, _err)


class _FakeWikiServer:
    """In-memory model of the task wiki endpoint, so the read-back contract can be
    tested at all. The other harnesses in this file return the PRE-write wiki from
    every `_get`, which means their read-back can never see the write -- fine for
    asserting the POST payload, vacuous about whether the verification is right.

    Section grain mirrors the server's observed behaviour:
    `append` adds a `## <section>` block at the end; `replace` swaps the body of the
    FIRST exact-title `## <section>` block, where a block ends at the next `## ` line
    -- including a `## ` line that arrived inside a body.
    """

    def __init__(self, wiki='', mangle=None, deaf=False):
        self.wiki = wiki
        self.posts = []
        self.mangle = mangle   # callable(body) -> what actually gets stored
        self.deaf = deaf       # True: accept the POST, store nothing (the silent no-op)

    def get(self, path, **params):
        return {'id': 9, 'wiki': self.wiki}

    def put(self, path, payload):
        self.posts.append(payload)
        if not self.deaf:
            self.wiki = self.mangle(payload['wiki']) if self.mangle else payload['wiki']
        return {'id': 9, 'wiki': self.wiki}

    def post(self, path, body):
        self.posts.append(body)
        if self.deaf:
            return {'id': 9, 'wiki': self.wiki}
        text = body['body']
        if self.mangle:
            text = self.mangle(text)
        section = body['section'].strip().lstrip('#').strip()
        if body['action'] == 'append':
            head = self.wiki.rstrip('\n')
            self.wiki = (head + '\n\n' if head else '') + f'## {section}\n\n{text}'
        else:
            spans = tark_cli._wiki_exact_sections(self.wiki, section)
            start, end = spans[0]
            nl = self.wiki.find('\n', start)
            self.wiki = self.wiki[:nl + 1] + '\n' + text + self.wiki[end:]
        return {'id': 9, 'wiki': self.wiki}


class ReadBackVerification(unittest.TestCase):
    """Every wiki write re-GETs and diffs. These pin what that diff may conclude."""

    def _drive(self, server, action, section, body, force=False):
        args = Namespace(task_id=9, action=action, section=section, body=body,
                         from_file=None, from_stdin=False, force=force, yes=False,
                         json=False)
        code = 0
        with mock.patch.object(tark_cli, '_get', server.get), \
             mock.patch.object(tark_cli, '_post', server.post), \
             mock.patch.object(sys, 'stdout', io.StringIO()), \
             mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            try:
                tark_cli.cmd_wiki(args)
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue()

    def test_a_body_carrying_its_own_heading_verifies_ok(self):
        """Callers commonly write bodies like `## Verify: Phase 1\n\nevidence`. `## ` is
        exactly what the section scanner splits on, so a span-based read-back
        compares the sent body against the EMPTY string and fails a write that
        landed perfectly. Observed live: exit 2 with
        `section body does not equal what was sent`, content on the card intact."""
        srv = _FakeWikiServer('## Seed\n\nseed body\n')
        code, err = self._drive(srv, 'set', 'Hdr', '## Hdr: Phase 1\n\nevidence line\n')
        self.assertEqual(code, 0, err)
        self.assertEqual(len(srv.posts), 1)
        self.assertIn('## Hdr\n', srv.wiki)
        self.assertIn('evidence line', srv.wiki)

    def test_a_landed_but_wrong_write_is_caught_and_not_retried(self):
        """Two things at once, both regressions the read-back exists to stop.

        (a) The section must hold the sent body and NOTHING ELSE -- `wiki set` has
        left a stale tail behind the new body before, and a bare prefix match would call that
        a pass. (b) Once the wiki HAS changed, retrying is forbidden: a blind retry
        re-derives `exists` from the changed wiki, flips `set` from append to
        replace, and writes the body a SECOND time -- observed live: the task
        wiki ended up with it twice AND the command still exited 2.
        Retry is legal only when the read-back proves nothing landed.
        """
        srv = _FakeWikiServer('## Seed\n\nseed body\n', mangle=lambda b: b + '\nSERVER NOISE\n')
        code, err = self._drive(srv, 'set', 'Alpha', 'alpha body\n')
        self.assertEqual(code, 2)
        self.assertEqual(len(srv.posts), 1, 'a landed write must not be written twice')
        self.assertIn('the wiki DID change', err)

    def test_a_silent_noop_write_is_retried_once_then_fails(self):
        """The original defect: the server answers OK and stores nothing. That is the
        one case a retry is safe in -- the wiki came back byte-identical."""
        srv = _FakeWikiServer('## Seed\n\nseed body\n', deaf=True)
        code, err = self._drive(srv, 'append', 'Alpha', 'alpha body\n')
        self.assertEqual(code, 2)
        self.assertEqual(len(srv.posts), 2)
        self.assertIn('[FAIL] wiki append #9', err)

    def test_append_merges_onto_an_existing_section_end_to_end(self):
        srv = _FakeWikiServer('## Alpha\n\nfirst line\n')
        code, err = self._drive(srv, 'append', 'Alpha', 'second line\n')
        self.assertEqual(code, 0, err)
        self.assertEqual(len(srv.posts), 1)
        self.assertEqual(len(tark_cli._wiki_exact_sections(srv.wiki, 'Alpha')), 1)
        self.assertIn('first line', srv.wiki)
        self.assertIn('second line', srv.wiki)

    def test_append_force_still_makes_a_real_duplicate(self):
        srv = _FakeWikiServer('## Alpha\n\nfirst line\n')
        code, err = self._drive(srv, 'append', 'Alpha', 'second line\n', force=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(tark_cli._wiki_exact_sections(srv.wiki, 'Alpha')), 2)

    def test_a_body_whose_heading_EXACTLY_equals_the_section_verifies_ok(self):
        """`## Verify: Phase 1` inside a body is a PREFIX sibling and never inflated
        the exact-span count. `## Plan` inside a `--section Plan` body is an EXACT
        one, and does: the read-back counted 2 spans where the arithmetic expected 1
        and reported `expected a new section, found 2 (had 0)` -- exit 2 on a write
        that landed, and a caller that reacts by rewriting duplicates the block."""
        srv = _FakeWikiServer('## Seed\n\nseed body\n')
        code, err = self._drive(srv, 'set', 'Plan', '## Plan\n\nstep one\n')
        self.assertEqual(code, 0, err)
        self.assertEqual(len(srv.posts), 1)
        self.assertEqual(len(tark_cli._wiki_exact_sections(srv.wiki, 'Plan')), 2)

    def test_force_duplicate_with_an_exact_heading_in_the_body_verifies_ok(self):
        srv = _FakeWikiServer('## Alpha\n\nfirst\n')
        code, err = self._drive(srv, 'append', 'Alpha', '## Alpha\n\nsecond\n', force=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(srv.posts), 1)

    def test_a_body_opening_with_an_indented_code_block_verifies_ok(self):
        """`sent.strip()` ate the leading spaces the stored tail keeps, so an indented
        first line failed a write that landed. Both sides strip NEWLINES only."""
        srv = _FakeWikiServer('## Seed\n\nseed body\n')
        code, err = self._drive(srv, 'set', 'Code', '    indented line\n    still indented\n')
        self.assertEqual(code, 0, err)
        self.assertEqual(len(srv.posts), 1)
        self.assertIn('    indented line', srv.wiki)

    def test_put_round_trips_and_tolerates_a_trailing_newline(self):
        srv = _FakeWikiServer('## Old\n\nold body\n', mangle=lambda b: b.rstrip('\n'))
        code, err = self._drive_put(srv, '## New\n\nnew body\n')
        self.assertEqual(code, 0, err)
        self.assertEqual(srv.wiki, '## New\n\nnew body')

    def test_put_fails_when_the_body_does_not_round_trip(self):
        srv = _FakeWikiServer('## Old\n\nold body\n', deaf=True)
        code, err = self._drive_put(srv, '## New\n\nnew body\n')
        self.assertEqual(code, 2)
        self.assertIn('[FAIL] wiki put #9', err)

    def _drive_put(self, server, body):
        args = Namespace(task_id=9, action='put', section=None, body=body,
                         from_file=None, from_stdin=False, force=False, yes=True,
                         json=False)
        code = 0
        with mock.patch.object(tark_cli, '_get', server.get), \
             mock.patch.object(tark_cli, '_put', server.put), \
             mock.patch.object(sys, 'stdout', io.StringIO()), \
             mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            try:
                tark_cli.cmd_wiki(args)
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue()

    def test_merging_into_an_empty_section_body_adds_no_blank_padding(self):
        """A section whose span body is empty (its sub-headings start right after the
        header) gained two leading blank lines on every append."""
        srv = _FakeWikiServer('## Plan\n\n## Plan: Phase 1\n\nphase one\n')
        code, err = self._drive(srv, 'append', 'Plan', 'phase two\n')
        self.assertEqual(code, 0, err)
        self.assertNotIn('\n\n\n', srv.wiki)
        self.assertIn('phase two', srv.wiki)

    def test_a_stale_duplicate_of_the_same_section_is_not_a_boundary(self):
        """Accepting ANY `## ` line as the section boundary cannot tell "the next
        section" from "a stale duplicate of the one I just wrote": three successive
        `set --section Plan` calls all exited 0 while leaving FOUR `## Plan` blocks
        on the card -- the false FAIL traded for a silent corrupt PASS."""
        srv = _FakeWikiServer('## Seed\n\nseed\n')
        code, err = self._drive(srv, 'set', 'Plan', '## Plan\n\nstep one\n')
        self.assertEqual(code, 0, err)          # landed exactly as sent (double header and all)
        code, err = self._drive(srv, 'set', 'Plan', '## Plan\n\nstep two\n')
        self.assertEqual(code, 2)
        self.assertIn('appears 3 times on this card', err)
        self.assertEqual(len(tark_cli._wiki_exact_sections(srv.wiki, 'Plan')), 3)

    def test_a_clean_body_over_an_already_doubled_section_still_fails(self):
        """The `tark-cli-wiki-set-replaces-section` scar itself: `set` upserts the
        FIRST exact match and leaves the second dangling inside it."""
        srv = _FakeWikiServer('## Plan\n\nstep one\n\n## Plan\n\nstep two\n')
        code, err = self._drive(srv, 'set', 'Plan', 'clean body\n')
        self.assertEqual(code, 2)

    def test_the_next_section_is_still_a_valid_boundary(self):
        srv = _FakeWikiServer('## Plan\n\nold\n\n## Verify\n\nkeep me\n')
        code, err = self._drive(srv, 'set', 'Plan', 'new body\n')
        self.assertEqual(code, 0, err)
        self.assertIn('keep me', srv.wiki)

    def test_an_indented_residue_is_not_a_section_boundary(self):
        """A 4-space-indented `##` is a CODE BLOCK, not a heading -- `_WIKI_HEADER_RE`
        refuses to treat it as a boundary, so a whole stale old body could hide
        behind four spaces while verification passed. Driven end-to-end so this reds
        on the OUTCOME, not on a changed signature."""
        srv = _FakeWikiServer('## Seed\n\nseed\n',
                              mangle=lambda b: b + '\n\n    ## Old\n\nSTALE OLD BODY\n')
        code, err = self._drive(srv, 'set', 'X', 'new body\n')
        self.assertEqual(code, 2)
        self.assertIn('STALE OLD BODY', srv.wiki)

    def test_a_body_that_restates_its_own_header_warns_the_caller(self):
        srv = _FakeWikiServer('## Seed\n\nseed\n')
        code, err = self._drive(srv, 'set', 'Plan', '## Plan\n\nstep one\n')
        self.assertEqual(code, 0, err)
        self.assertIn('DOUBLE header', err)

    def test_a_pre_existing_duplicate_gets_an_honest_reason(self):
        """`append --force` is a supported way to duplicate a block, so a card can
        legitimately hold two `## Alpha`. A later `set` still refuses -- the read-back
        is genuinely ambiguous -- but `section body does not equal what was sent` was
        a false claim: it does equal it."""
        srv = _FakeWikiServer('## Alpha\n\nfirst\n\n## Alpha\n\nsecond\n')
        code, err = self._drive(srv, 'set', 'Alpha', 'third\n')
        self.assertEqual(code, 2)
        self.assertIn('appears 2 times on this card', err)
        self.assertNotIn('does not equal what was sent', err)

    def test_the_double_header_warning_reads_the_first_heading_not_any(self):
        srv = _FakeWikiServer('## Seed\n\nseed\n')
        code, err = self._drive(srv, 'set', 'Plan', '## Notes\n\nx\n\n## Plan\n\ny\n')
        self.assertNotIn('DOUBLE header', err)
