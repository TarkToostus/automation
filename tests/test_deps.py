"""Tests for `tark_cli deps` — the PM task-dependency (blocker) command.

No network: `tark_cli._get` and `tark_cli._request` are mocked, and every call's
path / params / body is captured.

Context: PM `TaskDependency` had a model, serializer, viewset and UI but was never
registered on the PAT surface, so the two blockers on C2 #5617 had to be created
by ssh'ing to prod and driving the web endpoint through a Django shell
(2026-07-21). This command ships the CLI half of the `backend/project_management/
api/pat_urls.py` INVARIANT ("every PAT registration must have a matching CLI
command") — it fully closes only once tark-platform registers the PAT endpoint
(tracked as C2 #5643 Scope row 5, FOLLOWUP). Until then `deps` 404s by design;
see DepsNotYetDeployedTests below for the clean-failure contract that covers it.
"""
import io
import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

import tark_cli


def _ns(task_id, action='list', **kw):
    defaults = {'task_id': task_id, 'action': action, 'blocker': None,
                'type': 'finish_to_start', 'json': False}
    defaults.update(kw)
    return Namespace(**defaults)


def _dep(i, blocking, blocked, dtype='finish_to_start'):
    return {'id': i, 'blocking_task': blocking, 'blocked_task': blocked, 'dependency_type': dtype}


class _Harness(unittest.TestCase):
    """Swaps the two network helpers for capturing fakes."""

    def _run(self, ns, get_map=None, request_result=None):
        """get_map: {filter_field: rows}. Returns (gets, requests, stdout)."""
        gets, requests = [], []

        def fake_get(path, **params):
            gets.append((path, params))
            key = next(iter(params), None)
            return {'results': (get_map or {}).get(key, [])}

        def fake_request(method, path, body=None, params=None):
            requests.append({'method': method, 'path': path, 'body': body})
            return request_result if request_result is not None else {'id': 99}

        orig_get, orig_req = tark_cli._get, tark_cli._request
        tark_cli._get, tark_cli._request = fake_get, fake_request
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                tark_cli.cmd_deps(ns)
        finally:
            tark_cli._get, tark_cli._request = orig_get, orig_req
        return gets, requests, out.getvalue()


class DepsListTests(_Harness):
    def test_lists_both_directions(self):
        gets, _, out = self._run(
            _ns(5617),
            get_map={'blocked_task': [_dep(2, 5616, 5617), _dep(3, 5634, 5617)],
                     'blocking_task': [_dep(9, 5617, 5700)]},
        )
        # Both directions are queried — a blocker list alone hides what this task holds up.
        self.assertEqual([g[1] for g in gets], [{'blocked_task': '5617'}, {'blocking_task': '5617'}])
        self.assertIn('#5616', out)
        self.assertIn('#5634', out)
        self.assertIn('blocked by', out)
        self.assertIn('blocks', out)
        self.assertIn('#5700', out)

    def test_no_dependencies_reports_none(self):
        """Negative control: an empty board must read as 'none', not as a stale table."""
        _, _, out = self._run(_ns(5617))
        self.assertIn('none', out)
        self.assertNotIn('blocked by', out)

    def test_list_never_writes(self):
        _, requests, _ = self._run(_ns(5617), get_map={'blocked_task': [_dep(2, 5616, 5617)]})
        self.assertEqual(requests, [])


class DepsAddTests(_Harness):
    def test_add_posts_the_pair(self):
        _, requests, out = self._run(_ns(5617, 'add', blocker=5616))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['method'], 'POST')
        self.assertEqual(requests[0]['path'], '/api/v1/pat/pm/task-dependencies/')
        self.assertEqual(requests[0]['body'], {
            'blocking_task': 5616, 'blocked_task': 5617, 'dependency_type': 'finish_to_start',
        })

    def test_add_honours_dependency_type(self):
        _, requests, _ = self._run(_ns(5617, 'add', blocker=5616, type='finish_to_finish'))
        self.assertEqual(requests[0]['body']['dependency_type'], 'finish_to_finish')

    def test_add_warns_that_the_daemon_gates_on_the_wiki(self):
        """The row alone does NOT stop the daemon — it reads `Blocked by #N` from
        the wiki. Silently letting someone believe otherwise is the failure this
        command is most likely to cause."""
        _, _, out = self._run(_ns(5617, 'add', blocker=5616))
        self.assertIn('wiki', out.lower())
        self.assertIn('Blocked by #5616', out)

    def test_add_without_blocker_exits(self):
        with self.assertRaises(SystemExit):
            self._run(_ns(5617, 'add'))

    def test_add_rejects_self_block(self):
        """A self-dependency would be permanently unsatisfiable, and the daemon's
        own dep scanner explicitly skips self-references."""
        with self.assertRaises(SystemExit):
            self._run(_ns(5617, 'add', blocker=5617))


class DepsRemoveTests(_Harness):
    def test_remove_resolves_row_id_from_the_pair(self):
        """A human knows the two task ids, never the dependency row id."""
        gets, requests, out = self._run(
            _ns(5617, 'remove', blocker=5616),
            get_map={'blocked_task': [_dep(2, 5616, 5617), _dep(3, 5634, 5617)]},
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['method'], 'DELETE')
        # Row 2, not row 3 — the OTHER blocker must survive.
        self.assertEqual(requests[0]['path'], '/api/v1/pat/pm/task-dependencies/2/')

    def test_remove_unknown_pair_exits_without_deleting(self):
        with self.assertRaises(SystemExit):
            self._run(_ns(5617, 'remove', blocker=1234),
                      get_map={'blocked_task': [_dep(2, 5616, 5617)]})

    def test_remove_without_blocker_exits(self):
        with self.assertRaises(SystemExit):
            self._run(_ns(5617, 'remove'))

    def test_remove_ignores_a_row_for_a_different_blocked_task(self):
        """A server that silently drops the `blocked_task` filter param (the exact
        DjangoFilterBackend failure mode already hit twice on this codebase - #467,
        #5478) must NOT cause `remove` to delete some other task's dependency just
        because it shares the same `blocking_task`. The client re-checks both
        sides of the pair, not just `blocking_task`."""
        gets, requests, out = self._run(
            _ns(5617, 'remove', blocker=5616),
            # blocked_task=5617 was requested, but the server returned a row for
            # a DIFFERENT task too (as if the filter param were ignored).
            get_map={'blocked_task': [_dep(2, 5616, 5617), _dep(7, 5616, 9999)]},
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['path'], '/api/v1/pat/pm/task-dependencies/2/')

    def test_remove_json_emits_valid_json_not_text(self):
        """`--json` must never fall through to the human-readable print — a
        script parsing this output would get a ValueError on plain text."""
        import json
        gets, requests, out = self._run(
            _ns(5617, 'remove', blocker=5616, json=True),
            get_map={'blocked_task': [_dep(2, 5616, 5617)]},
        )
        payload = json.loads(out)
        self.assertEqual(payload, {'task': 5617, 'blocker': 5616, 'removed': [2]})


class DepsNotYetDeployedTests(unittest.TestCase):
    """The PAT `task-dependencies` endpoint does not exist on any deployed
    tark-platform yet (C2 #5643 Scope row 5, FOLLOWUP — separate repo/PR). Every
    `deps` call must 404 as a CLEAN CLI error: no traceback, and `--json` stdout
    must stay empty (never a corrupted mix of a warning line + partial JSON).
    Exercises the REAL `_request` HTTP-error path (urllib mocked at the transport
    boundary), not the harness fakes the other test classes use.
    """

    def _run_against_404(self, ns):
        import urllib.error

        def raise_404(req, timeout=15):
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, io.BytesIO(b'{"detail": "Not found."}'))

        orig_urlopen = tark_cli.urllib.request.urlopen
        tark_cli.urllib.request.urlopen = raise_404
        out, err = io.StringIO(), io.StringIO()
        try:
            from contextlib import redirect_stderr
            with redirect_stdout(out), redirect_stderr(err):
                with self.assertRaises(SystemExit) as ctx:
                    tark_cli.cmd_deps(ns)
        finally:
            tark_cli.urllib.request.urlopen = orig_urlopen
        return ctx.exception.code, out.getvalue(), err.getvalue()

    def test_list_404s_cleanly(self):
        code, out, err = self._run_against_404(_ns(5617))
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('404', err)

    def test_json_list_404_leaves_stdout_empty(self):
        """The most important guarantee: --json callers must never see a
        warning/error line mixed into what should be pure JSON on stdout."""
        code, out, err = self._run_against_404(_ns(5617, json=True))
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('404', err)

    def test_add_404s_cleanly(self):
        code, out, err = self._run_against_404(_ns(5617, 'add', blocker=5616))
        self.assertEqual(code, 1)
        self.assertEqual(out, '')


class DepsWiringTests(unittest.TestCase):
    def test_command_is_registered(self):
        self.assertIs(tark_cli.COMMANDS['deps'], tark_cli.cmd_deps)

    def test_parser_accepts_the_documented_forms(self):
        parser = tark_cli.build_parser()
        a = parser.parse_args(['deps', '5617'])
        self.assertEqual((a.task_id, a.action), (5617, 'list'))
        a = parser.parse_args(['deps', '5617', 'add', '--blocker', '5616'])
        self.assertEqual((a.action, a.blocker, a.type), ('add', 5616, 'finish_to_start'))
        a = parser.parse_args(['deps', '5617', 'remove', '--blocker', '5616'])
        self.assertEqual((a.action, a.blocker), ('remove', 5616))


if __name__ == '__main__':
    unittest.main()
