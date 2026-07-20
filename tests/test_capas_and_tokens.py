"""Tests for the "other capas" commands + JWT token management.

No network: tark_cli's HTTP helpers (`_request`/`_get`/`_jwt_login`/`_jwt_request`)
are mocked. Each test asserts the request the CLI would make (method, path, body),
the destructive-guard FAILURE path (guard blocks without --yes), client-side scope
validation, and that a password is never persisted to config.
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


def _ns(**kw):
    kw.setdefault('json', False)
    return Namespace(**kw)


def _capture_request():
    """Patch tark_cli._request, capturing (method, path, body). Returns (patcher, cap)."""
    cap = {}

    def fake(method, path, body=None, params=None):
        cap['method'] = method
        cap['path'] = path
        cap['body'] = body
        cap['params'] = params
        return {'id': 99}

    return mock.patch.object(tark_cli, '_request', fake), cap


# ---------------------------------------------------------------------------
# Feature A — request shaping for the new capas
# ---------------------------------------------------------------------------

class NewCapasRequestShapeTests(unittest.TestCase):
    def test_comment_create_body(self):
        patcher, cap = _capture_request()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_comment(_ns(task_id=42, body=['hello', 'world']))
        self.assertEqual(cap['method'], 'POST')
        self.assertEqual(cap['path'], '/api/v1/pat/pm/task-comments/')
        self.assertEqual(cap['body'], {'task': 42, 'text': 'hello world'})

    def test_boards_create_body(self):
        patcher, cap = _capture_request()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_boards_create(_ns(project=7, name='Sprint'))
        self.assertEqual(cap['method'], 'POST')
        self.assertEqual(cap['path'], '/api/v1/pat/pm/boards/')
        self.assertEqual(cap['body'], {'project': 7, 'name': 'Sprint'})

    def test_sites_active_requires_domains(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_sites_active(_ns(domains=None, window=None))

    def test_sites_active_get_params(self):
        cap = {}

        def fake_get(path, **params):
            cap['path'] = path
            cap['params'] = params
            return {'active_users': 0, 'per_domain': {}, 'window_minutes': 15}

        with mock.patch.object(tark_cli, '_get', fake_get), \
                mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_sites_active(_ns(domains='a.tt.ee,b.tt.ee', window='30m'))
        self.assertEqual(cap['path'], '/api/v1/pat/c2/sites/active-now/')
        self.assertEqual(cap['params'], {'domains': 'a.tt.ee,b.tt.ee', 'window': '30m'})

    def test_contract_blocks_list_path(self):
        cap = {}

        def fake_get(path, **params):
            cap['path'] = path
            return []

        with mock.patch.object(tark_cli, '_get', fake_get), \
                mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_contract_blocks(_ns())
        self.assertEqual(cap['path'], '/api/v1/pat/system/contract-blocks/')

    def test_detail_retrieve_paths(self):
        """Every detail command GETs /<prefix>/<id>/."""
        for name, (prefix, label) in tark_cli._DETAIL_RESOURCES.items():
            handler = tark_cli.COMMANDS[name]
            cap = {}

            def fake_get(path, **params):
                cap['path'] = path
                return {'id': 5}

            with mock.patch.object(tark_cli, '_get', fake_get), \
                    mock.patch.object(sys, 'stdout', io.StringIO()):
                handler(_ns(id='5'))
            self.assertEqual(cap['path'], f'/api/v1/pat/{prefix}/5/', f'{name} detail path')


# ---------------------------------------------------------------------------
# Destructive-guard FAILURE path (negative control) + happy path
# ---------------------------------------------------------------------------

class DestructiveGuardTests(unittest.TestCase):
    _DELETES = [
        (tark_cli.cmd_task_delete, {'id': 3}, 'DELETE', '/api/v1/pat/pm/tasks/3/'),
        (tark_cli.cmd_time_delete, {'id': 4}, 'DELETE', '/api/v1/pat/pm/time-entries/4/'),
        (tark_cli.cmd_offer_line_delete, {'id': 5}, 'DELETE', '/api/v1/pat/sales/offer-lines/5/'),
    ]

    def test_guard_blocks_without_yes_and_does_NOT_delete(self):
        """Negative control: a non-'yes' reply must abort BEFORE any request."""
        for handler, kw, _method, _path in self._DELETES:
            patcher, cap = _capture_request()
            with patcher, \
                    mock.patch.object(sys, 'stdin', io.StringIO('no\n')), \
                    mock.patch.object(sys, 'stderr', io.StringIO()), \
                    self.assertRaises(SystemExit):
                handler(_ns(yes=False, **kw))
            self.assertEqual(cap, {}, f'{handler.__name__} issued a request despite abort')

    def test_guard_blocks_on_empty_stdin(self):
        """EOF / closed stdin (piped, no input) must also abort, not proceed."""
        patcher, cap = _capture_request()
        with patcher, \
                mock.patch.object(sys, 'stdin', io.StringIO('')), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli.cmd_task_delete(_ns(yes=False, id=3))
        self.assertEqual(cap, {})

    def test_yes_flag_bypasses_and_deletes(self):
        for handler, kw, method, path in self._DELETES:
            patcher, cap = _capture_request()
            with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
                handler(_ns(yes=True, **kw))
            self.assertEqual((cap.get('method'), cap.get('path')), (method, path))

    def test_typed_yes_confirms_and_deletes(self):
        patcher, cap = _capture_request()
        with patcher, \
                mock.patch.object(sys, 'stdin', io.StringIO('yes\n')), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_task_delete(_ns(yes=False, id=3))
        self.assertEqual(cap['path'], '/api/v1/pat/pm/tasks/3/')


# ---------------------------------------------------------------------------
# Feature B — token management (JWT)
# ---------------------------------------------------------------------------

class TokensScopesTests(unittest.TestCase):
    def test_static_map_offline_no_login(self):
        """`tokens scopes` with no creds must not attempt a login and must print
        every known scope."""
        def boom(*a, **k):
            raise AssertionError('must not log in for offline scopes')

        out = io.StringIO()
        with mock.patch.dict('os.environ', {'TARK_PASSWORD': ''}, clear=False), \
                mock.patch.object(tark_cli, '_load_config', lambda: {}), \
                mock.patch.object(tark_cli, '_jwt_login', boom), \
                mock.patch.object(sys, 'stdout', out):
            tark_cli._tokens_scopes(_ns(user=None))
        printed = out.getvalue()
        for scope in tark_cli._SCOPE_CAPABILITIES:
            self.assertIn(scope, printed)


class TokensCreateTests(unittest.TestCase):
    def _run_create(self, **kw):
        cap = {}

        def fake_jwt_request(method, path, access, body=None):
            cap['method'] = method
            cap['path'] = path
            cap['body'] = body
            return {'id': 1, 'name': body['name'], 'scopes': body['scopes'], 'token': 'tark_pat_SECRET'}

        with mock.patch.object(tark_cli, '_resolve_login', lambda a: ('u', 'p')), \
                mock.patch.object(tark_cli, '_jwt_login', lambda u, p: 'ACCESS'), \
                mock.patch.object(tark_cli, '_jwt_request', fake_jwt_request), \
                mock.patch.object(sys, 'stdout', io.StringIO()) as out:
            tark_cli._tokens_create(_ns(**kw))
        return cap, out.getvalue()

    def test_create_body_and_expiry(self):
        cap, _ = self._run_create(name='ci', scopes=['pm:write', 'sales:read'], expires='2026-12-31')
        self.assertEqual(cap['method'], 'POST')
        self.assertEqual(cap['path'], '/api/v1/pat/tokens/')
        self.assertEqual(cap['body']['name'], 'ci')
        self.assertEqual(cap['body']['scopes'], ['pm:write', 'sales:read'])
        self.assertEqual(cap['body']['expires_at'], '2026-12-31T23:59:59')

    def test_no_expiry_omits_field(self):
        cap, _ = self._run_create(name='ci', scopes=['pm:read'], expires=None)
        self.assertNotIn('expires_at', cap['body'])

    def test_token_printed_once_with_warning(self):
        _, out = self._run_create(name='ci', scopes=['pm:read'], expires=None)
        self.assertIn('tark_pat_SECRET', out)
        self.assertIn('ONLY ONCE', out)

    def test_unknown_scope_rejected_before_login(self):
        def boom(*a, **k):
            raise AssertionError('must validate scopes before logging in')

        with mock.patch.object(tark_cli, '_jwt_login', boom), \
                mock.patch.object(tark_cli, '_resolve_login', boom), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli._tokens_create(_ns(name='x', scopes=['pm:write', 'bogus:scope'], expires=None))

    def test_missing_name_rejected(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli._tokens_create(_ns(name=None, scopes=['pm:read'], expires=None))

    def test_no_scope_rejected(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli._tokens_create(_ns(name='x', scopes=[], expires=None))

    def test_bad_expiry_format_rejected(self):
        with mock.patch.object(tark_cli, '_resolve_login', lambda a: ('u', 'p')), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli._tokens_create(_ns(name='x', scopes=['pm:read'], expires='31-12-2026'))


class TokensRevokeTests(unittest.TestCase):
    def test_revoke_guard_blocks_without_yes(self):
        def boom(*a, **k):
            raise AssertionError('must not reach JWT request when aborting')

        with mock.patch.object(tark_cli, '_jwt_request', boom), \
                mock.patch.object(tark_cli, '_jwt_login', boom), \
                mock.patch.object(tark_cli, '_resolve_login', boom), \
                mock.patch.object(sys, 'stdin', io.StringIO('no\n')), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli._tokens_revoke(_ns(token_id='5', user=None, yes=False))

    def test_revoke_with_yes_issues_delete(self):
        cap = {}

        def fake_jwt_request(method, path, access, body=None):
            cap['method'] = method
            cap['path'] = path
            return {}

        with mock.patch.object(tark_cli, '_resolve_login', lambda a: ('u', 'p')), \
                mock.patch.object(tark_cli, '_jwt_login', lambda u, p: 'ACCESS'), \
                mock.patch.object(tark_cli, '_jwt_request', fake_jwt_request), \
                mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli._tokens_revoke(_ns(token_id='5', user=None, yes=True))
        self.assertEqual((cap['method'], cap['path']), ('DELETE', '/api/v1/pat/tokens/5/'))

    def test_revoke_missing_id_rejected(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli._tokens_revoke(_ns(token_id=None, user=None, yes=True))


class LoginCredentialTests(unittest.TestCase):
    def test_password_from_env_never_persisted(self):
        """$TARK_PASSWORD is used but NEVER written to config (no _save_config)."""
        def boom_save(cfg):
            raise AssertionError('password/login must never write config')

        with mock.patch.dict('os.environ', {'TARK_PASSWORD': 's3cret'}, clear=False), \
                mock.patch.object(tark_cli, '_load_config', lambda: {'user': 'martin'}), \
                mock.patch.object(tark_cli, '_save_config', boom_save):
            user, pw = tark_cli._resolve_login(_ns(user=None))
        self.assertEqual((user, pw), ('martin', 's3cret'))

    def test_user_flag_beats_config(self):
        with mock.patch.dict('os.environ', {'TARK_PASSWORD': 'x'}, clear=False), \
                mock.patch.object(tark_cli, '_load_config', lambda: {'user': 'cfguser'}):
            user, _ = tark_cli._resolve_login(_ns(user='flaguser'))
        self.assertEqual(user, 'flaguser')

    def test_non_tty_without_password_errors(self):
        fake_stdin = io.StringIO('')
        fake_stdin.isatty = lambda: False  # type: ignore[assignment]
        with mock.patch.dict('os.environ', {'TARK_PASSWORD': ''}, clear=False), \
                mock.patch.object(tark_cli, '_load_config', lambda: {'user': 'martin'}), \
                mock.patch.object(sys, 'stdin', fake_stdin), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli._resolve_login(_ns(user=None))


if __name__ == '__main__':
    unittest.main()
