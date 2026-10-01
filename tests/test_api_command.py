"""Tests for `tark_cli api <path>` URL construction.

No network: `tark_cli._request` is mocked and the path it receives is captured.

**The bug these pin (2026-07-23).** `cmd_api` built its URL as
`f'/api/v1/pat/{path}/{qs}'` after only `args.path.strip('/')`. `strip('/')` trims
the ends of the WHOLE string, so a path carrying an inline query
(`pm/tasks/?board=48&page=2` - the documented escape-hatch form) kept its `?...`
inside `path` and the canonical trailing slash landed AFTER the query, glued onto
the LAST parameter's value:

    /api/v1/pat/pm/tasks/?board=48&page=2/          <- 404 "Invalid page."
    /api/v1/pat/pm/tasks/?board=48&page_size=1000/  <- silently falls back to 50

The second form is the dangerous one: the request succeeds, so a page-walk
truncates while looking healthy and the caller concludes the data set is smaller
than it is (board 48 returned 50 of 745 rows). The corruption was visible in the
server's echoed `next` URL as `&page_size=1000%2F`. Appending a throwaway last
param (`&_=x`) made the slash land on the dummy - the workaround that proved it.

Encoding is asserted too: parse_qsl decodes the inline query and urlencode
re-encodes it, so a value must round-trip percent-encoded EXACTLY once - a
double-encode (`%252F`) is as wrong as no encode.
"""
import sys
import unittest
from argparse import Namespace
from pathlib import Path

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

import tark_cli  # noqa: E402


def _ns(path, **kw):
    defaults = {'path': path, 'filter': None, 'post': None, 'patch': None, 'json': True}
    defaults.update(kw)
    return Namespace(**defaults)


def _run(path, **kw):
    """Run cmd_api against a mocked transport; return (method, path, body)."""
    calls = []

    def fake_request(method, req_path, body=None):
        calls.append((method, req_path, body))
        return {'ok': True}

    orig_request, orig_out = tark_cli._request, tark_cli._json_out
    tark_cli._request = fake_request
    tark_cli._json_out = lambda _data: None
    try:
        tark_cli.cmd_api(_ns(path, **kw))
    finally:
        tark_cli._request, tark_cli._json_out = orig_request, orig_out
    return calls[0]


class ApiPathTests(unittest.TestCase):
    def test_inline_query_only(self):
        """The reported repro: query survives, no slash after it."""
        _, path, _ = _run('pm/tasks/?board=48&page=2')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?board=48&page=2')

    def test_inline_query_order_is_preserved(self):
        """Reversed order hit a different symptom (HTTP 500 on `board=48/`), so the
        pair order the caller wrote must reach the server unchanged."""
        _, path, _ = _run('pm/tasks/?page=2&board=48')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?page=2&board=48')

    def test_filter_only_unchanged(self):
        """Today's `--filter` behaviour is untouched by the fix."""
        _, path, _ = _run('sales/leads', filter=['pipeline=Imports', 'source=COLD'])
        self.assertEqual(path, '/api/v1/pat/sales/leads/?pipeline=Imports&source=COLD')

    def test_inline_query_plus_filter_compose(self):
        """Both sets of pairs land in ONE query string - neither is dropped."""
        _, path, _ = _run('pm/tasks/?board=48&page=2', filter=['priority=high'])
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?board=48&page=2&priority=high')

    def test_filter_wins_over_inline_pair_of_same_key(self):
        """Documented precedence: the explicit flag beats the path-embedded pair,
        and the key appears exactly once (last-wins, matching `--filter` today)."""
        _, path, _ = _run('pm/tasks/?page=1', filter=['page=7'])
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?page=7')

    def test_repeated_inline_keys_are_preserved(self):
        """The pre-fix code passed the inline query through verbatim, so BOTH pairs
        reached the server; django_filters' `in`/multiple-choice filters read them.
        Collapsing to a dict here would have been a silent data-loss regression
        smuggled in behind a bug fix."""
        _, path, _ = _run('pm/tasks/?tag=a&tag=b')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?tag=a&tag=b')

    def test_repeated_filter_key_stays_last_wins(self):
        """`--filter` has always been last-wins. The fix must not change it -
        that contract is out of scope, and silently widening it is still a change."""
        _, path, _ = _run('pm/tasks/', filter=['tag=a', 'tag=b'])
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?tag=b')

    def test_filter_replaces_every_inline_pair_of_that_key(self):
        """Precedence must be total, not partial: a surviving inline `tag=a`
        alongside the override would send a filter the caller tried to replace."""
        _, path, _ = _run('pm/tasks/?tag=a&tag=b&page=1', filter=['tag=z'])
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?page=1&tag=z')

    def test_no_query_keeps_trailing_slash(self):
        """Django APPEND_SLASH resolves on the path - the slash must stay."""
        _, path, _ = _run('pm/tasks/')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/')

    def test_surrounding_slashes_are_still_stripped(self):
        _, path, _ = _run('/pm/tasks/')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/')

    def test_bare_path_without_slash_gains_one(self):
        _, path, _ = _run('sales/leads')
        self.assertEqual(path, '/api/v1/pat/sales/leads/')

    def test_values_are_encoded_exactly_once(self):
        """A space, an `&` and a `/` inside one value. Encoded once - never raw,
        never double (`%252F` would mean parse_qsl/urlencode ran out of step)."""
        _, path, _ = _run('pm/tasks/?q=a%20b%26c%2Fd')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?q=a+b%26c%2Fd')
        self.assertNotIn('%25', path)

    def test_filter_value_is_encoded_exactly_once(self):
        _, path, _ = _run('pm/tasks/', filter=['q=a b&c/d'])
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?q=a+b%26c%2Fd')
        self.assertNotIn('%25', path)

    def test_blank_inline_value_is_kept(self):
        """`?archived=` is a real DRF filter form - dropping it changes the query."""
        _, path, _ = _run('pm/tasks/?archived=')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/?archived=')

    def test_post_and_patch_paths_get_the_same_treatment(self):
        method, path, body = _run('pm/tasks/123/?notify=false', patch='{"name": "x"}')
        self.assertEqual(method, 'PATCH')
        self.assertEqual(path, '/api/v1/pat/pm/tasks/123/?notify=false')
        self.assertEqual(body, {'name': 'x'})


class RejectedInputTests(unittest.TestCase):
    """Every shape that would silently build a URL the caller did not ask for.

    Each of these used to sail through `strip('/')` and produce a request to a
    wrong (or nonexistent) endpoint with a live PAT attached - the failure mode
    the original bug is an instance of, so the fix closes the class, not the case.
    """

    def _assert_rejected(self, path, needle, **kw):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            _run(path, **kw)
        self.assertIn(needle, err.getvalue().lower())

    def test_fragment_is_rejected_not_silently_dropped(self):
        """urllib strips a `#fragment` before sending, so accepting one would mean
        the caller's input never reaches the server without saying so."""
        self._assert_rejected('pm/tasks/?board=48#frag', 'fragment')

    def test_full_url_is_rejected_not_double_prefixed(self):
        """Pasting the server's own `next` URL is the natural next move for anyone
        page-walking - and it silently built /api/v1/pat/api/v1/pat/... . Say so."""
        self._assert_rejected('https://host/api/v1/pat/pm/tasks/?page=2', 'full url')

    def test_dot_segments_are_rejected(self):
        """The escape hatch must not escape its own prefix: a proxy resolves
        /api/v1/pat/../../admin/ to /admin/."""
        self._assert_rejected('../../admin', '..')

    def test_empty_path_is_rejected(self):
        """`api "?board=48"` built /api/v1/pat//?board=48 - a double slash aimed
        at no endpoint at all."""
        self._assert_rejected('?board=48', 'empty')

    def test_filter_without_equals_is_rejected(self):
        """A bare `--filter archived` was silently discarded, so the caller got an
        UNFILTERED result set that looked filtered - the same silent-wrong-answer
        shape as the page_size truncation this branch fixes."""
        self._assert_rejected('pm/tasks/', 'k=v', filter=['archived'])


class TrailingSlashRegressionTests(unittest.TestCase):
    """Named for the bug: a query string must NEVER end in a slash.

    This is the invariant, independent of any single repro above - it fails for
    any future refactor that reintroduces `f'{path}/{qs}'` ordering.
    """

    CASES = [
        'pm/tasks/?board=48&page=2',
        'pm/tasks/?page=2&board=48',
        'pm/tasks/?board=48&page_size=1000',
        'pm/tasks/?a=1',
        'pm/tasks/?a=1&b=2&c=3',
        'pm/tasks/',
        'pm/tasks',
    ]

    def test_query_string_never_ends_in_a_slash(self):
        import re
        for raw in self.CASES:
            with self.subTest(raw=raw):
                _, path, _ = _run(raw)
                self.assertIsNone(re.search(r'\?.*/$', path),
                                  f'trailing slash landed inside the query: {path}')

    def test_query_string_never_ends_in_an_encoded_slash(self):
        """The server echoed `page_size=1000%2F` - an encoded slash is the same
        defect one layer down, and would survive the raw-slash assertion above."""
        for raw in self.CASES:
            with self.subTest(raw=raw):
                _, path, _ = _run(raw, filter=['x=1'])
                self.assertFalse(path.endswith('%2F'), f'encoded slash at query end: {path}')

    def test_exactly_one_question_mark(self):
        """Two query strings would mean the inline one was appended, not merged."""
        _, path, _ = _run('pm/tasks/?board=48', filter=['page=2'])
        self.assertEqual(path.count('?'), 1)

    def test_slash_sits_before_the_query(self):
        """The positive half: the endpoint keeps its canonical trailing slash."""
        _, path, _ = _run('pm/tasks/?board=48')
        self.assertTrue(path.split('?')[0].endswith('/'))


if __name__ == '__main__':
    unittest.main()
