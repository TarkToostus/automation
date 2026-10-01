"""Tests for `tark_cli tasks` filtering + pagination.

No network: `tark_cli._get` is mocked and every call's query params are captured.

Two things are covered, and only one of them is new code.

**The filter lookups (regression coverage for a fix that shipped untested).**
DRF's DjangoFilterBackend SILENTLY IGNORES a query param that is not in
`filterset_fields`, so a stale lookup name returns EVERY task in the tenant while
looking perfectly filtered. tark-platform #467 (2026-06-22) moved the task → board
link behind `board_card`, silently invalidating the lookups this command sent;
`0c2cdc9` (#5478) renamed them to the registered names but added no test, so
nothing stops the next rename from re-breaking it just as quietly. These
assertions pin each server-side lookup name so a future divergence fails loudly
instead of degrading to "return everything".

**The pagination (new behaviour in this change).** See TasksPaginationTests.
"""
import sys
import unittest
from argparse import Namespace
from pathlib import Path

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

import tark_cli


def _ns(**kw):
    defaults = {'json': True, 'project': None, 'board': None, 'status': None, 'all': True}
    defaults.update(kw)
    return Namespace(**defaults)


def _task(i, column='WORK'):
    return {'id': i, 'name': f'task {i}', 'column_name': column, 'project_name': 'Autopilot',
            'priority': 'medium', 'total_hours': None}


class TasksFilterTests(unittest.TestCase):
    """The params `tasks` sends must match TaskViewSet.filterset_fields verbatim."""

    def _run(self, pages=None, **kw):
        """Run cmd_tasks against a canned page sequence; return (calls, rows)."""
        pages = pages if pages is not None else [{'results': [_task(1)], 'next': None}]
        calls = []

        def fake_get(path, **params):
            calls.append(params)
            return pages[len(calls) - 1]

        printed = []
        orig_get, orig_out = tark_cli._get, tark_cli._json_out
        tark_cli._get = fake_get
        tark_cli._json_out = printed.append
        try:
            tark_cli.cmd_tasks(_ns(**kw))
        finally:
            tark_cli._get, tark_cli._json_out = orig_get, orig_out
        return calls, (printed[0] if printed else [])

    def test_board_filter_uses_board_card_lookup(self):
        calls, _ = self._run(board=48)
        self.assertEqual(calls[0]['board_card__board'], '48')
        # The pre-#467 name must be gone — the server ignores it, returning everything.
        self.assertNotIn('board', calls[0])

    def test_daemon_call_shape_survives_a_page_walk(self):
        """The filters must be re-sent on EVERY page, not just the first — dropping
        them on page 2 would silently splice unfiltered rows onto a filtered list."""
        pages = [
            {'results': [_task(1)], 'next': 'http://x/?page=2'},
            {'results': [_task(2)], 'next': None},
        ]
        calls, _ = self._run(pages=pages, board=48, status='WORK')
        for call in calls:
            self.assertEqual(call['board_card__board'], '48')
            self.assertEqual(call['board_card__column__name'], 'WORK')

    def test_status_filter_uses_board_card_column_lookup(self):
        calls, _ = self._run(status='WORK')
        self.assertEqual(calls[0]['board_card__column__name'], 'WORK')
        self.assertNotIn('column__name', calls[0])

    def test_numeric_project_filter_uses_board_card_lookup(self):
        calls, _ = self._run(project='28')
        self.assertEqual(calls[0]['board_card__board__project'], '28')
        self.assertNotIn('board__project', calls[0])

    def test_board_and_status_combine(self):
        """The daemon's exact call — board-scoped AND column-scoped in one request."""
        calls, _ = self._run(board=48, status='WORK')
        self.assertEqual(calls[0]['board_card__board'], '48')
        self.assertEqual(calls[0]['board_card__column__name'], 'WORK')

    def test_no_filters_sends_no_scope_params(self):
        """Negative control: without flags, none of the scope lookups are sent —
        so a passing filter test above cannot be an artefact of a static default."""
        calls, _ = self._run()
        for key in ('board_card__board', 'board_card__column__name', 'board_card__board__project'):
            self.assertNotIn(key, calls[0])


class TasksPaginationTests(unittest.TestCase):
    """The server caps a page at 50 rows whatever `limit` says, so `tasks` must
    follow `next` — a single call truncates any column past 50, and it truncates
    the OLDEST rows (server order is -updated_at) that the daemon's FIFO pick
    order exists to reach."""

    def _run(self, pages, **kw):
        calls = []

        def fake_get(path, **params):
            calls.append(params)
            return pages[len(calls) - 1]

        printed = []
        orig_get, orig_out = tark_cli._get, tark_cli._json_out
        tark_cli._get = fake_get
        tark_cli._json_out = printed.append
        try:
            tark_cli.cmd_tasks(_ns(**kw))
        finally:
            tark_cli._get, tark_cli._json_out = orig_get, orig_out
        return calls, (printed[0] if printed else [])

    def test_follows_next_and_concatenates(self):
        pages = [
            {'results': [_task(i) for i in range(50)], 'next': 'http://x/?page=2'},
            {'results': [_task(i) for i in range(50, 52)], 'next': None},
        ]
        calls, rows = self._run(pages, board=48, status='WORK')
        self.assertEqual(len(calls), 2)
        self.assertEqual([c['page'] for c in calls], ['1', '2'])
        self.assertEqual(len(rows), 52)
        self.assertEqual(len({r['id'] for r in rows}), 52)

    def test_stops_on_last_page(self):
        pages = [{'results': [_task(1)], 'next': None}]
        calls, rows = self._run(pages, board=48)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 1)

    def test_page_cap_is_bounded_and_warns(self):
        """A `next` that never clears must stop at the cap, not loop forever."""
        pages = [{'results': [_task(i)], 'next': 'http://x/?page=next'} for i in range(tark_cli.TASKS_MAX_PAGES)]
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            calls, rows = self._run(pages, board=48)
        self.assertEqual(len(calls), tark_cli.TASKS_MAX_PAGES)
        self.assertIn('truncated', err.getvalue())

    def test_non_dict_payload_is_passed_through(self):
        """An unpaginated (bare list) response must not be dropped."""
        calls, rows = self._run([[_task(1), _task(2)]], board=48)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 2)


if __name__ == '__main__':
    unittest.main()
