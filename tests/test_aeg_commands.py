"""Tests for `tark_cli aeg ...` (workforce schedules) and named profiles.

No network: `tark_cli._request` (and `_get`, which wraps it) is replaced by a tiny
in-memory fake of /api/v1/pat/workforce/ that serves one month of grid and applies
schedule-instances/save/ bodies to it. Each test asserts what the CLI would send
and what it refuses to send:

  * the 11 h rest rule catches a night -> next-morning day shift (a planted
    violation) and a violating plan is NOT written without --force;
  * --dry-run prints the diff and writes nothing;
  * apply batches a whole month into ONE save request (one notification batch);
  * replace/delete address the existing row by id (seeded rows have no client_id);
  * delete needs a selector or --all-in-scope, and --yes to proceed;
  * a selected profile never falls back to the default PAT/URL (token leak);
  * seat login (no PAT API on the server): falls back on the PAT 404, picks the
    editor family the seat may use, and renews an expired JWT once.
"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from argparse import Namespace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

_AUTOMATION_DIR = Path(__file__).resolve().parent.parent
if str(_AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(_AUTOMATION_DIR))

import tark_cli  # noqa: E402

LOC = {'id': 7, 'name': 'Vastuvõtt'}
SPA = {'id': 9, 'name': 'Spa'}
LOC_NAMES = {7: 'Vastuvõtt', 9: 'Spa'}
SHIFTS = [
    {'id': 11, 'name': 'Vastuvõtt päev', 'acronym': 'VP', 'start_time': '07:00', 'end_time': '19:00',
     'location_id': 7, 'location_name': 'Vastuvõtt', 'locations': [7]},
    {'id': 12, 'name': 'Vastuvõtt öö', 'acronym': 'VÖ', 'start_time': '19:00', 'end_time': '07:00',
     'location_id': 7, 'location_name': 'Vastuvõtt', 'locations': [7]},
    {'id': 13, 'name': 'Spa õhtu', 'acronym': 'SÕ', 'start_time': '10:00', 'end_time': '18:00',
     'location_id': 9, 'location_name': 'Spa', 'locations': [9]},
]
PEOPLE = [(101, 'Mari Maasikas'), (102, 'Jaan Tamm'), (103, 'Liis Lepp')]


class FakeServer:
    """Month grid for 2026-10 + an instances save that mutates it."""

    def __init__(self, planned=None):
        self.calls = []
        self.bearers = []
        self.next_id = 500
        self.planned = {}  # (uid, day) -> [instance]
        for uid, day, shift_id, row_id, *loc in planned or []:  # optional 5th: location id / None
            self._add(uid, day, shift_id, row_id, client_id=None, loc=loc[0] if loc else 7)

    def _add(self, uid, day, shift_id, row_id, client_id, loc=7):
        sh = next(s for s in SHIFTS if s['id'] == shift_id)
        s_local = datetime(2026, 10, day, int(sh['start_time'][:2]))
        e_local = datetime(2026, 10, day, int(sh['end_time'][:2]))
        if e_local <= s_local:
            e_local += timedelta(days=1)
        # Server times are UTC; Tallinn is UTC+3 in October.
        utc = lambda dt: (dt - timedelta(hours=3)).isoformat() + '+00:00'
        self.planned.setdefault((uid, day), []).append({
            'id': row_id, 'client_id': client_id, 'shift_hour_id': shift_id, 'shift_name': sh['name'],
            'location_id': loc, 'location_name': LOC_NAMES.get(loc, ''), 'hours': 12.0,
            'starts_at': utc(s_local), 'ends_at': utc(e_local),
        })

    def grid(self):
        rows = []
        for uid, name in PEOPLE:
            days = {str(d): {'planned': list(self.planned.get((uid, d), [])), 'absence': None} for d in range(1, 32)}
            rows.append({'user_id': uid, 'display_name': name, 'username': name.split()[0].lower(),
                         'group_name': 'Vastuvõtu administraator', 'location_id': 7, 'location_name': 'Vastuvõtt',
                         'location_roles': {}, 'contract_type': 'full_time', 'is_active': True, 'days': days})
        return {'year': 2026, 'month': 10, 'rows': rows, 'shift_hours': SHIFTS,
                'manager_locations': [LOC, SPA], 'scope': 'location'}

    pat_api = True          # False: server predates /api/v1/pat/workforce/ (404)
    web_modes = frozenset({'all'})  # editor families the seat may use on /api/v1/workforce/
    expire_first = False    # first JWT call -> 401 (expired access token)

    def _soft(self, code, soft_errors):
        if code not in soft_errors:
            raise AssertionError(f'unexpected hard HTTP {code}')
        raise tark_cli._SoftHTTPError(code, '{}')

    def request(self, method, path, body=None, params=None, soft_errors=(), bearer=None):
        self.calls.append((method, path, body, params))
        if path.startswith('/api/v1/pat/') and not self.pat_api:
            self._soft(404, soft_errors)
        if path.startswith('/api/v1/workforce/'):
            self.bearers.append(bearer)
            if self.expire_first and bearer == 'jwt-1':
                self._soft(401, soft_errors)
            leaf = path[len('/api/v1/workforce/'):]
            mode = leaf.split('-schedule')[0] if leaf.startswith(('location-', 'team-')) else 'all'
            if mode not in self.web_modes:
                self._soft(403, soft_errors)
        if path.endswith('schedule-grid/'):
            if params and int(params['month']) != 10:
                g = self.grid()
                g.update(month=int(params['month']), rows=[dict(r, days={}) for r in g['rows']])
                return g
            return self.grid()
        if path.endswith('schedule-instances/save/'):
            saved, deleted = [], []
            for inst in body['instances']:
                day = int(inst['date'][8:10])
                cell = self.planned.setdefault((inst['user_id'], day), [])
                existing = next((r for r in cell if r['id'] == inst.get('id')), None)
                if inst.get('deleted'):
                    if existing:
                        cell.remove(existing)
                        deleted.append(inst['client_id'])
                    continue
                if existing:
                    cell.remove(existing)
                row_id = inst.get('id') or self._next()
                self._add(inst['user_id'], day, inst['shift_hour_id'], row_id, inst['client_id'],
                          loc=inst.get('location_id'))
                saved.append({'client_id': inst['client_id'], 'id': row_id})
            return {'saved': saved, 'deleted': deleted, 'dropped': [], 'blocked': 0, 'scope': 'location'}
        raise AssertionError(f'unexpected {method} {path}')

    def _next(self):
        self.next_id += 1
        return self.next_id

    def saves(self):
        return [c for c in self.calls if c[1].endswith('/save/')]


def _ns(**kw):
    base = dict(json=False, dry_run=False, yes=False, force=False, no_check=False, prune=False,
                date=None, date_from=None, date_to=None, month=None, week=None, employee=None,
                department=None, location=None, role=None, shift=None, min_rest=None,
                max_consecutive=None, max_nights=None, max_week_hours=None, require=None, senior=None,
                all_in_scope=False, plan=None, weekdays=None, format=None, search=None, legacy_save=False)
    base.update(kw)
    return Namespace(**base)


def _plan_file(assignments, **extra):
    f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
    json.dump(dict(extra, assignments=assignments), f, ensure_ascii=False)
    f.close()
    return f.name


class _Wire:
    """urlopen stand-in: the REAL tark_cli._request runs (headers and all) and its
    requests are answered by FakeServer. Records (host, Authorization) per request."""

    def __init__(self, server):
        self.server, self.auth = server, []

    def __call__(self, req, timeout=None):
        parts = urllib.parse.urlsplit(req.full_url)
        header = req.get_header('Authorization') or ''
        self.auth.append((parts.netloc, header))
        try:
            out = self.server.request(req.get_method(), parts.path, json.loads(req.data) if req.data else None,
                                      dict(urllib.parse.parse_qsl(parts.query)) or None,
                                      soft_errors=(401, 403, 404), bearer=header[len('Bearer '):])
        except tark_cli._SoftHTTPError as e:
            raise urllib.error.HTTPError(req.full_url, e.code, 'fake', {}, io.BytesIO(b'{}')) from None
        return io.BytesIO(json.dumps(out).encode())


class _Base(unittest.TestCase):
    def run_cmd(self, fn, server, args, stdin='', session=None, wire=None):
        tark_cli._AEG_GRID_CACHE.clear()
        tark_cli._AEG_SESSION.clear()
        if session is None:  # default: the PAT API, already resolved
            tark_cli._AEG_SESSION.update(auth='pat', prefix=tark_cli.AEG_PREFIX, paths=tark_cli._AEG_PATHS['pat'])
        tark_cli._AEG_ARGS = args
        out, err = io.StringIO(), io.StringIO()
        code = 0
        transport = (mock.patch.object(urllib.request, 'urlopen', wire) if wire
                     else mock.patch.object(tark_cli, '_request', server.request))
        with transport, \
                mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err), \
                mock.patch.object(sys, 'stdin', io.StringIO(stdin)):
            try:
                fn(args)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()


VIOLATION = [
    {'employee': 'Jaan Tamm', 'date': '2026-10-05', 'shift': 'Vastuvõtt öö'},
    {'employee': 'Jaan Tamm', 'date': '2026-10-06', 'shift': 'Vastuvõtt päev'},
]


class RestRuleTests(_Base):
    def test_night_then_next_day_is_a_rest_violation_and_is_not_written(self):
        server = FakeServer()
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=_plan_file(VIOLATION)))
        self.assertEqual(code, 3)  # same exit code as dry-run / check, not a generic error
        self.assertIn('--force', err)
        self.assertIn('rest', out)
        self.assertIn('0.0 h rest', out)
        self.assertEqual(server.saves(), [])

    def test_check_with_plan_exits_3(self):
        server = FakeServer()
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_check, server,
                                    _ns(plan=_plan_file(VIOLATION), date_from='2026-10-05', date_to='2026-10-11'))
        self.assertEqual(code, 3)
        self.assertIn('Jaan Tamm', out)

    def test_rest_against_an_existing_server_shift(self):
        # Night already planned on the server; a plan adding next morning's day shift must fail.
        server = FakeServer(planned=[(102, 5, 12, 900)])
        code, out, _ = self.run_cmd(
            tark_cli.cmd_aeg_schedule_apply, server,
            _ns(file=_plan_file([{'employee': 'Jaan', 'date': '2026-10-06', 'shift': 'päev'}])))
        self.assertNotEqual(code, 0)
        self.assertIn('0.0 h rest', out)
        self.assertEqual(server.saves(), [])

    def test_headcount_and_senior_rules(self):
        server = FakeServer()
        plan = _plan_file([{'employee': 'Liis Lepp', 'date': '2026-10-05', 'shift': 'Vastuvõtt päev'}],
                          rules={'require': ['Vastuvõtt päev=2'], 'senior': ['Vastuvõtt päev=Mari Maasikas,Jaan Tamm']})
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=plan, dry_run=True))
        self.assertEqual(code, 3)
        self.assertIn('1 of 2 planned', out)
        self.assertIn('no senior', out)


class ApplyTests(_Base):
    GOOD = [
        {'employee': 'Mari Maasikas', 'date': '2026-10-05', 'shift': 'Vastuvõtt päev'},
        {'employee': 'Jaan Tamm', 'date': '2026-10-05', 'shift': 'Vastuvõtt öö'},
        {'employee': 'Liis Lepp', 'date': '2026-10-31', 'shift': 'VP'},
    ]

    def test_dry_run_writes_nothing(self):
        server = FakeServer()
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                    _ns(file=_plan_file(self.GOOD), dry_run=True))
        self.assertEqual(code, 0)
        self.assertIn('3 to create', out)
        self.assertIn('DRY RUN', out)
        self.assertEqual(server.saves(), [])

    def test_apply_is_one_request_per_month_and_verifies(self):
        server = FakeServer()
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=_plan_file(self.GOOD)))
        self.assertEqual(code, 0, out)
        saves = server.saves()
        self.assertEqual(len(saves), 1)
        body = saves[0][2]
        self.assertEqual((body['year'], body['month'], len(body['instances'])), (2026, 10, 3))
        self.assertTrue(all(i['location_id'] == 7 and i['client_id'].startswith('cli-') for i in body['instances']))
        self.assertIn('Verified on server: 3/3', out)

    def test_replace_addresses_existing_row_by_id_and_needs_yes(self):
        server = FakeServer(planned=[(101, 5, 12, 900)])  # seeded row: no client_id
        plan = _plan_file([{'employee': 'Mari Maasikas', 'date': '2026-10-05', 'shift': 'Vastuvõtt päev'}])
        code, _, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=plan))
        self.assertNotEqual(code, 0)  # no --yes, empty stdin -> aborted
        self.assertEqual(server.saves(), [])
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=plan, yes=True))
        self.assertEqual(code, 0, out)
        inst = server.saves()[0][2]['instances'][0]
        self.assertEqual((inst['id'], inst['shift_hour_id']), (900, 11))
        self.assertEqual(inst['client_id'], 'cli-adopt-900')

    def test_csv_plan(self):
        f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8')
        f.write('employee,date,shift\nMari Maasikas,2026-10-07,Vastuvõtt päev\n')
        f.close()
        server = FakeServer()
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=f.name))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(server.saves()), 1)


class DeleteTests(_Base):
    def test_delete_requires_a_selector(self):
        server = FakeServer(planned=[(101, 31, 11, 900)])
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_delete, server, _ns(date=['2026-10-31'], yes=True))
        self.assertNotEqual(code, 0)
        self.assertIn('--all-in-scope', err)
        self.assertEqual(server.saves(), [])

    def test_delete_dry_run_then_yes(self):
        server = FakeServer(planned=[(101, 31, 11, 900), (102, 31, 11, 901)])
        args = _ns(date=['2026-10-31'], employee=['Mari Maasikas'], dry_run=True)
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_delete, server, args)
        self.assertEqual(code, 0)
        self.assertIn('1 to delete', out)
        self.assertEqual(server.saves(), [])
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_delete, server,
                                    _ns(date=['2026-10-31'], employee=['Mari Maasikas'], yes=True))
        self.assertEqual(code, 0, out)
        inst = server.saves()[0][2]['instances'][0]
        self.assertEqual((inst['id'], inst['deleted']), (900, True))
        self.assertNotIn((101, 31), {k for k, v in server.planned.items() if v})
        self.assertIn((102, 31), {k for k, v in server.planned.items() if v})


class ReadTests(_Base):
    def test_employees_filter_by_department(self):
        server = FakeServer()
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_employees, server, _ns(json=True, department='vastuvõtt'))
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 3)

    def test_schedule_get_json_has_local_times(self):
        server = FakeServer(planned=[(102, 5, 12, 900)])
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_get, server,
                                    _ns(json=True, date_from='2026-10-05', date_to='2026-10-06'))
        self.assertEqual(code, 0)
        entries = [e for e in json.loads(out) if e.get('shift_id')]
        self.assertEqual([(e['employee'], e['shift'], e['id']) for e in entries],
                         [('Jaan Tamm', 'Vastuvõtt öö', 900)])

    def test_ambiguous_employee_is_refused(self):
        server = FakeServer()
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_get, server,
                                    _ns(employee=['a'], date_from='2026-10-05', date_to='2026-10-05'))
        self.assertNotEqual(code, 0)
        self.assertIn('ambiguous', err)


class SeatLoginTests(_Base):
    """Server without the PAT schedule API: `aeg` logs in as the seat and uses the
    web editor endpoints. The login itself (urllib) is faked at _jwt_login_payload."""

    def setUp(self):
        self.logins = []

        def login(user, password):
            self.logins.append((user, password))
            return {'access': f'jwt-{len(self.logins)}', 'refresh': 'r',
                    'user': {'is_superuser': False, 'permissions': self.perms}}
        self.perms = []
        tmp = Path(tempfile.mkdtemp())
        self.sessions_file = tmp / 'aeg-sessions.json'
        self.patches = [mock.patch.object(tark_cli, 'CONFIG_DIR', tmp),
                        mock.patch.object(tark_cli, 'AEG_SESSIONS_FILE', self.sessions_file),
                        mock.patch.object(tark_cli, '_jwt_login_payload', login),
                        mock.patch.object(tark_cli, '_aeg_renew', lambda sess: tark_cli._aeg_login(sess)),
                        mock.patch.object(tark_cli, '_peek_pat', lambda: 'tark_pat_x'),
                        mock.patch.dict('os.environ', {'TARK_AEG_PASSWORD': 'pw'}, clear=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        tark_cli._AEG_SESSION.clear()

    def test_pat_404_falls_back_to_location_editor(self):
        server = FakeServer()
        server.pat_api, server.web_modes = False, {'location'}
        plan = _plan_file([{'employee': 'Jaan Tamm', 'date': '2026-10-07', 'shift': 'Vastuvõtt päev'}])
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                      _ns(file=plan, user='demo.manager', auth=None), session='login')
        self.assertEqual(code, 0, err)
        self.assertIn('seat login', err)
        self.assertEqual(self.logins, [('demo.manager', 'pw')])
        self.assertEqual([c[1] for c in server.saves()], ['/api/v1/workforce/location-schedule-instances/save/'])
        self.assertTrue(all(b and b.startswith('jwt-') for b in server.bearers))

    def test_manage_all_seat_goes_straight_to_the_tenant_editor(self):
        server = FakeServer()
        server.pat_api = False
        self.perms = ['core.can_manage_all_schedules', 'core.can_view_plan_actual_grid']
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_employees, server,
                                    _ns(json=True, user='demo.manager', auth='login'), session='login')
        self.assertEqual(code, 0, err)
        self.assertFalse(any(c[1].startswith('/api/v1/pat/') for c in server.calls))  # --auth login: no probe
        self.assertFalse(any('location-' in c[1] or 'team-' in c[1] for c in server.calls))

    def test_expired_jwt_is_renewed_once(self):
        server = FakeServer()
        server.pat_api, server.web_modes, server.expire_first = False, {'all'}, True
        self.perms = ['core.can_manage_all_schedules']
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_employees, server,
                                    _ns(json=True, user='demo.manager', auth=None), session='login')
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.logins), 2)

    def test_second_run_reuses_the_cached_jwt_not_the_password(self):
        server = FakeServer()
        server.pat_api, server.web_modes = False, {'location'}
        for _ in range(2):
            code, _, err = self.run_cmd(tark_cli.cmd_aeg_employees, server,
                                        _ns(json=True, user='demo.manager', auth='login'), session='login')
            self.assertEqual(code, 0, err)
        self.assertEqual(len(self.logins), 1)  # login is throttled server-side; refresh is not
        cached = json.loads(self.sessions_file.read_text())
        self.assertEqual([v['mode'] for v in cached.values()], ['location'])
        self.assertNotIn('pw', self.sessions_file.read_text())  # never the password

    def test_no_schedule_capability_is_refused(self):
        server = FakeServer()
        server.pat_api, server.web_modes = False, set()
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_employees, server,
                                    _ns(user='demo.manager', auth=None), session='login')
        self.assertNotEqual(code, 0)
        self.assertIn('no schedule capability', err)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / 'config.json'
        self.tmp.write_text(json.dumps({
            'url': 'https://c2.example', 'pat': 'tark_pat_c2default',
            'profiles': {'demo': {'url': 'https://demo.example', 'pat_env': 'TARK_DEMO_PAT'}},
        }))
        self.patches = [mock.patch.object(tark_cli, 'CONFIG_FILE', self.tmp),
                        mock.patch.dict('os.environ', {'TARK_PAT': 'tark_pat_envdefault'}, clear=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        tark_cli._PROFILE = ''
        tark_cli._URL_OVERRIDE = ''

    def test_default_profile_unchanged(self):
        tark_cli._PROFILE = ''
        self.assertEqual(tark_cli._get_url(), 'https://c2.example')
        self.assertEqual(tark_cli._get_pat(), 'tark_pat_envdefault')

    def test_profile_uses_its_own_url_and_pat_env(self):
        tark_cli._PROFILE = 'demo'
        with mock.patch.dict('os.environ', {'TARK_DEMO_PAT': 'tark_pat_demo'}):
            self.assertEqual(tark_cli._get_url(), 'https://demo.example')
            self.assertEqual(tark_cli._get_pat(), 'tark_pat_demo')

    def test_profile_never_falls_back_to_the_default_pat(self):
        tark_cli._PROFILE = 'demo'
        with mock.patch.dict('os.environ', {'TARK_DEMO_PAT': ''}), mock.patch.object(sys, 'stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                tark_cli._get_pat()

    def test_unknown_profile_errors(self):
        tark_cli._PROFILE = 'nope'
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli._get_url()

    def test_url_flag_wins(self):
        tark_cli._PROFILE = 'demo'
        tark_cli._URL_OVERRIDE = 'https://other.example'
        self.assertEqual(tark_cli._get_url(), 'https://other.example')

    def test_config_set_writes_into_the_profile(self):
        tark_cli._PROFILE = 'demo'
        with mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_config(Namespace(action='set', key='user', value='demo.manager', json=False))
        cfg = json.loads(self.tmp.read_text())
        self.assertEqual(cfg['profiles']['demo']['user'], 'demo.manager')
        self.assertNotIn('user', cfg)

    def test_config_set_refuses_a_password_and_writes_nothing(self):
        before = self.tmp.read_text()
        for profile in ('', 'demo'):
            tark_cli._PROFILE = profile
            for key in ('password', 'aeg_password', 'PASSWORD'):
                err = io.StringIO()
                with mock.patch.object(sys, 'stderr', err), self.assertRaises(SystemExit):
                    tark_cli.cmd_config(Namespace(action='set', key=key, value='s3cret-example', json=False))
                self.assertIn('password_env', err.getvalue())
        self.assertEqual(self.tmp.read_text(), before)
        with mock.patch.object(sys, 'stdout', io.StringIO()):  # naming the env var is still allowed
            tark_cli.cmd_config(Namespace(action='set', key='password_env', value='TARK_DEMO_PASSWORD', json=False))
        self.assertEqual(json.loads(self.tmp.read_text())['profiles']['demo']['password_env'], 'TARK_DEMO_PASSWORD')


class DateListTests(_Base):
    """A repeated --date means exactly those days - never the span first..last."""

    def test_delete_two_dates_does_not_wipe_the_days_between(self):
        server = FakeServer(planned=[(101, 1, 11, 900), (101, 15, 11, 901), (101, 31, 11, 902)])
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_delete, server,
                                    _ns(date=['2026-10-01', '2026-10-31'], employee=['Mari'], yes=True))
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(i['id'] for i in server.saves()[0][2]['instances']), [900, 902])
        self.assertTrue(server.planned[(101, 15)])  # mid-month shift survives

    def test_get_two_dates_shows_only_those_days(self):
        server = FakeServer(planned=[(101, 1, 11, 900), (101, 15, 11, 901), (101, 31, 11, 902)])
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_get, server,
                                    _ns(json=True, date=['2026-10-31', '2026-10-01']))
        self.assertEqual(code, 0)
        self.assertEqual(sorted(e['id'] for e in json.loads(out) if e.get('shift_id')), [900, 902])

    def test_date_list_respects_the_92_day_cap(self):
        server = FakeServer()
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_get, server, _ns(date=['2026-01-01', '2026-10-01']))
        self.assertEqual(code, 1)
        self.assertIn('3 months', err)
        self.assertEqual(server.calls, [])


class LocationScopeTests(_Base):
    """Mari (home: Vastuvõtt) has a Vastuvõtt shift on the 6th, a Spa shift on the 7th
    and a location-less (Graafik fallback) row on the 8th."""

    PLANNED = ((101, 6, 11, 900, 7), (101, 7, 13, 901, 9), (101, 8, 11, 902, None))

    def _deleted(self, server):
        return sorted(i['id'] for c in server.saves() for i in c[2]['instances'] if i.get('deleted'))

    def test_prune_only_deletes_rows_at_the_plan_location(self):
        server = FakeServer(planned=self.PLANNED)
        plan = _plan_file([{'employee': 'Mari', 'date': '2026-10-05', 'shift': 'VP'},
                           {'employee': 'Mari', 'date': '2026-10-09', 'shift': 'VP'}], location='Vastuvõtt')
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                      _ns(file=plan, prune=True, yes=True))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self._deleted(server), [900, 902])  # the Spa shift (901) is kept
        self.assertTrue(server.planned[(101, 7)])

    def test_delete_department_only_deletes_rows_at_its_locations(self):
        server = FakeServer(planned=self.PLANNED)
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_delete, server,
                                    _ns(date_from='2026-10-06', date_to='2026-10-08', department='Vastuvõtt',
                                        yes=True))
        self.assertEqual(code, 0, out)
        self.assertEqual(self._deleted(server), [900, 902])  # location-less row of its person: deletable

    def test_delete_location_only_deletes_rows_at_that_location(self):
        server = FakeServer(planned=self.PLANNED)
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_delete, server,
                                    _ns(date_from='2026-10-06', date_to='2026-10-08', location='Spa', yes=True))
        self.assertEqual(code, 0, out)
        self.assertEqual(self._deleted(server), [901])  # location-less row = home (Vastuvõtt): kept

    def test_plan_at_another_location_never_touches_that_days_rows(self):
        cat = {'employees': {101: {'name': 'Mari Maasikas', 'location_id': 7}}, 'locations': LOC_NAMES}
        existing = [{'user_id': 101, 'date': '2026-10-06', 'shift_id': 11, 'location_id': 7, 'id': 900},
                    {'user_id': 101, 'date': '2026-10-06', 'shift_id': 12, 'location_id': None, 'id': 902}]
        targets = {(101, '2026-10-06'): [{'shift': SHIFTS[2], 'location_id': 9}]}  # Spa evening
        for prune_locations in (None, {9}):
            ops = tark_cli._aeg_diff(cat, targets, existing, prune_locations=prune_locations)
            self.assertEqual([(o['op'], o['location_id'], o['row']) for o in ops], [('create', 9, None)])

    def test_apply_at_another_location_creates_beside_the_existing_shift(self):
        server = FakeServer(planned=self.PLANNED)
        plan = _plan_file([{'employee': 'Mari', 'date': '2026-10-06', 'shift': 'SÕ'}], location='Spa')
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=plan, yes=True, no_check=True))
        self.assertEqual(code, 0, out + err)
        sent = [i for c in server.saves() for i in c[2]['instances']]
        self.assertEqual([(i.get('id'), i.get('location_id'), i.get('deleted')) for i in sent], [(None, 9, None)])
        self.assertEqual(sorted(r['location_id'] for r in server.planned[(101, 6)]), [7, 9])  # 900 kept


class ClearEntryTests(_Base):
    """`shift: null` clears the day at the entry's / plan's / home location only.
    Mari (home: Vastuvõtt=7) has a Vastuvõtt row (910) and a Spa row (911) on the 7th."""

    PLANNED = ((101, 7, 11, 910, 7), (101, 7, 13, 911, 9))
    EXISTING = ({'user_id': 101, 'date': '2026-10-07', 'shift_id': 11, 'location_id': 7, 'id': 910},
                {'user_id': 101, 'date': '2026-10-07', 'shift_id': 13, 'location_id': 9, 'id': 911})

    def _build(self, plan):
        box = {}

        def fn(_args):
            box['cat'] = tark_cli._aeg_catalog(date(2026, 10, 7), date(2026, 10, 7))
            box['clears'] = {}
            box['targets'] = tark_cli._aeg_build_targets(box['cat'], plan, box['clears'])
        code, out, err = self.run_cmd(fn, FakeServer(), _ns())
        self.assertEqual(code, 0, out + err)
        return box

    def _ops(self, plan):
        b = self._build(plan)
        ops = tark_cli._aeg_diff(b['cat'], b['targets'], list(self.EXISTING), clears=b['clears'])
        return [(o['op'], (o['row'] or {}).get('id')) for o in ops if o['user_id'] == 101]  # Mari's cell

    def _apply(self, plan_kwargs, assignments):
        server = FakeServer(planned=self.PLANNED)
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                      _ns(file=_plan_file(assignments, **plan_kwargs), yes=True, no_check=True))
        self.assertEqual(code, 0, out + err)
        deleted = sorted(i['id'] for c in server.saves() for i in c[2]['instances'] if i.get('deleted'))
        return deleted, out, server

    def test_build_targets_records_the_clear_location(self):
        b = self._build({'location': 'Vastuvõtt', 'assignments': [
            {'employee': 'Mari', 'date': '2026-10-07', 'shift': None, 'location': 'Spa'},
            {'employee': 'Jaan', 'date': '2026-10-07', 'shift': None},
            {'employee': 'Liis', 'date': '2026-10-07', 'shift': ''}]})
        self.assertEqual(b['targets'], {(101, '2026-10-07'): [], (102, '2026-10-07'): [], (103, '2026-10-07'): []})
        self.assertEqual(b['clears'], {(101, '2026-10-07'): {9}, (102, '2026-10-07'): {7}, (103, '2026-10-07'): {7}})

    def test_build_targets_clear_without_any_location_uses_the_home_location(self):
        b = self._build({'assignments': [{'employee': 'Mari', 'date': '2026-10-07', 'shift': None}]})
        self.assertEqual(b['clears'], {(101, '2026-10-07'): {7}})

    def test_diff_clear_with_entry_location_deletes_only_that_locations_row(self):
        self.assertEqual(self._ops({'assignments': [
            {'employee': 'Mari', 'date': '2026-10-07', 'shift': None, 'location': 'Spa'}]}), [('delete', 911)])

    def test_diff_clear_with_plan_level_location_deletes_only_that_locations_row(self):
        self.assertEqual(self._ops({'location': 'Vastuvõtt', 'assignments': [
            {'employee': 'Mari', 'date': '2026-10-07', 'shift': None}]}), [('delete', 910)])

    def test_diff_clear_never_touches_another_locations_row(self):
        # Another entry names Spa, but Mari's clear is at Vastuvõtt: her Spa row stays.
        self.assertEqual(self._ops({'assignments': [
            {'employee': 'Mari', 'date': '2026-10-07', 'shift': None, 'location': 'Vastuvõtt'},
            {'employee': 'Jaan', 'date': '2026-10-07', 'shift': 'SÕ', 'location': 'Spa'}]}),
            [('delete', 910)])

    def test_apply_clear_with_entry_location(self):
        deleted, out, server = self._apply({}, [
            {'employee': 'Mari', 'date': '2026-10-07', 'shift': None, 'location': 'Spa'}])
        self.assertEqual(deleted, [911])
        self.assertEqual([r['id'] for r in server.planned[(101, 7)]], [910])
        self.assertIn('Verified on server: 1/1', out)

    def test_apply_clear_with_plan_level_location(self):
        deleted, _, server = self._apply({'location': 'Vastuvõtt'}, [
            {'employee': 'Mari', 'date': '2026-10-07', 'shift': None}])
        self.assertEqual(deleted, [910])
        self.assertEqual([r['id'] for r in server.planned[(101, 7)]], [911])

    def test_apply_clear_does_not_touch_another_locations_row(self):
        server = FakeServer(planned=((101, 7, 13, 911, 9),))  # only a Spa row
        plan = _plan_file([{'employee': 'Mari', 'date': '2026-10-07', 'shift': None}], location='Vastuvõtt')
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                      _ns(file=plan, yes=True, no_check=True))
        self.assertEqual(code, 0, out + err)
        self.assertIn('Nothing to write', out)
        self.assertEqual(server.saves(), [])
        self.assertEqual([r['id'] for r in server.planned[(101, 7)]], [911])


class UrlOverrideTests(_Base):
    """--url points at another host: the stored / env PAT must never be sent there."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = tmp / 'config.json'
        cfg.write_text(json.dumps({'url': 'https://c2.example', 'pat': 'tark_pat_cfgdefault'}))
        self.perms = ['core.can_manage_all_schedules']
        self.patches = [
            mock.patch.object(tark_cli, 'CONFIG_DIR', tmp), mock.patch.object(tark_cli, 'CONFIG_FILE', cfg),
            mock.patch.object(tark_cli, 'AEG_SESSIONS_FILE', tmp / 'aeg-sessions.json'),
            mock.patch.object(tark_cli, '_jwt_login_payload', lambda u, p: {
                'access': 'jwt-1', 'refresh': 'r', 'user': {'is_superuser': False, 'permissions': self.perms}}),
            mock.patch.dict('os.environ', {'TARK_PAT': 'tark_pat_envdefault', 'C2_PAT': 'tark_pat_legacy',
                                           'TARK_AEG_PASSWORD': 'pw'}, clear=False),
            mock.patch.object(tark_cli, '_URL_OVERRIDE', 'https://other.example'),
            mock.patch.object(tark_cli, '_PAT_OVERRIDE', ''),
            mock.patch.object(tark_cli, '_PROFILE', ''),
            # a foreign --url host gets an env password only when named explicitly (--password-env)
            mock.patch.object(tark_cli, '_PASSWORD_ENV_OVERRIDE', 'TARK_AEG_PASSWORD'),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        tark_cli._AEG_SESSION.clear()

    def test_stored_pat_never_reaches_the_overridden_host(self):
        server = FakeServer()  # has the PAT API - auto mode must still not probe it with a stored PAT
        wire = _Wire(server)
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_employees, server,
                                    _ns(json=True, user='demo.manager', auth=None), session='login', wire=wire)
        self.assertEqual(code, 0, err)
        self.assertTrue(wire.auth)
        self.assertFalse(any(c[1].startswith('/api/v1/pat/') for c in server.calls))
        for host, header in wire.auth:
            self.assertEqual(host, 'other.example')
            self.assertEqual(header, 'Bearer jwt-1')  # the seat JWT, never a tark_pat_* token

    def test_foreign_url_gets_no_env_password_without_password_env(self):
        server = FakeServer()
        wire = _Wire(server)
        logins = []
        with mock.patch.object(tark_cli, '_PASSWORD_ENV_OVERRIDE', ''), \
                mock.patch.object(tark_cli, '_jwt_login_payload', lambda u, p: logins.append(p)):
            code, _, err = self.run_cmd(tark_cli.cmd_aeg_employees, server,
                                        _ns(json=True, user='demo.manager', auth=None), session='login', wire=wire)
        self.assertEqual(code, 1)
        self.assertIn('--password-env', err)
        self.assertEqual(logins, [])  # $TARK_AEG_PASSWORD was set, and never sent
        self.assertEqual(wire.auth, [])

    def test_get_pat_refuses_a_stored_pat_under_url(self):
        self.assertEqual(tark_cli._peek_pat(), '')
        with mock.patch.object(sys, 'stderr', io.StringIO()) as err, self.assertRaises(SystemExit):
            tark_cli._get_pat()
        self.assertIn('--pat', err.getvalue())

    def test_explicit_pat_is_used_under_url(self):
        with mock.patch.object(tark_cli, '_PAT_OVERRIDE', 'tark_pat_explicit'):
            self.assertEqual(tark_cli._get_pat(), 'tark_pat_explicit')
            self.assertEqual(tark_cli._peek_pat(), 'tark_pat_explicit')


PW_ENV = {'TARK_PASSWORD': 'c2-pw', 'TARK_AEG_PASSWORD': 'aeg-pw', 'TARK_SEALED_PASSWORD': 'sealed-pw'}


class AegPasswordTests(unittest.TestCase):
    """$TARK_PASSWORD is the default (C2) password: never used for a profile / --url host."""

    def setUp(self):
        self.cfg = Path(tempfile.mkdtemp()) / 'config.json'
        self.cfg.write_text(json.dumps({'url': 'https://c2.example', 'password_env': 'TARK_TOP_PASSWORD', 'profiles': {
            'sealed': {'url': 'https://a.example', 'password_env': 'TARK_SEALED_PASSWORD'},
            'plain': {'url': 'https://b.example'},
            'samehost': {'url': 'https://C2.example/'}}}))  # the default target's host, no password_env
        self.patches = [mock.patch.object(tark_cli, 'CONFIG_FILE', self.cfg),
                        mock.patch.object(tark_cli, '_URL_OVERRIDE', ''),
                        mock.patch.object(sys, 'stdin', io.StringIO())]  # not a tty
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        tark_cli._PROFILE = ''

    def _pw(self, profile, env, url='', pw_env=''):
        tark_cli._PROFILE = profile
        with mock.patch.dict('os.environ', env, clear=True), mock.patch.object(tark_cli, '_URL_OVERRIDE', url), \
                mock.patch.object(tark_cli, '_PASSWORD_ENV_OVERRIDE', pw_env), \
                mock.patch.object(sys, 'stderr', io.StringIO()):
            try:
                return tark_cli._aeg_password()
            except SystemExit:
                return None

    def test_profile_password_env_wins_and_is_sealed(self):
        self.assertEqual(self._pw('sealed', PW_ENV), 'sealed-pw')
        self.assertIsNone(self._pw('sealed', {k: v for k, v in PW_ENV.items() if k != 'TARK_SEALED_PASSWORD'}))

    def test_profile_without_password_env_on_the_default_host_uses_aeg_password_never_the_c2_one(self):
        self.assertEqual(self._pw('samehost', PW_ENV), 'aeg-pw')
        self.assertIsNone(self._pw('samehost', {'TARK_PASSWORD': 'c2-pw'}))

    def test_profile_without_password_env_on_another_host_gets_no_env_password(self):
        self.assertIsNone(self._pw('plain', PW_ENV))  # $TARK_AEG_PASSWORD is the default host's
        self.assertEqual(self._pw('plain', dict(PW_ENV, TARK_B_PASSWORD='b-pw'), pw_env='TARK_B_PASSWORD'), 'b-pw')

    def test_profile_prompts_instead_of_using_the_c2_password(self):
        tark_cli._PROFILE = 'plain'
        with mock.patch.dict('os.environ', {'TARK_PASSWORD': 'c2-pw'}, clear=True), \
                mock.patch.object(sys.stdin, 'isatty', lambda: True), \
                mock.patch.object(tark_cli.getpass, 'getpass', lambda prompt: 'typed'):
            self.assertEqual(tark_cli._aeg_password(), 'typed')

    def test_default_target_keeps_the_c2_fallback_but_url_does_not(self):
        self.assertEqual(self._pw('', {'TARK_PASSWORD': 'c2-pw'}), 'c2-pw')
        self.assertEqual(self._pw('', PW_ENV), 'aeg-pw')
        self.assertIsNone(self._pw('', {'TARK_PASSWORD': 'c2-pw'}, url='https://other.example'))
        self.assertIsNone(self._pw('', PW_ENV, url='https://other.example'))

    def test_foreign_url_host_gets_no_env_password(self):
        env = dict(PW_ENV, TARK_TOP_PASSWORD='top-pw')
        url = 'https://other.example'
        self.assertIsNone(self._pw('', env, url=url))  # not $TARK_AEG_PASSWORD, top-level, nor $TARK_PASSWORD
        self.assertIsNone(self._pw('sealed', env, url=url))  # nor a profile's password_env
        self.assertIsNone(self._pw('samehost', env, url=url))
        self.assertEqual(self._pw('', dict(env, TARK_OTHER_PASSWORD='o-pw'), url=url, pw_env='TARK_OTHER_PASSWORD'),
                         'o-pw')  # --password-env: the explicit opt-in

    def test_url_on_the_targets_own_host_keeps_that_targets_password(self):
        env = {'TARK_PASSWORD': 'c2-pw', 'TARK_TOP_PASSWORD': 'top-pw', 'TARK_SEALED_PASSWORD': 'sealed-pw'}
        self.assertEqual(self._pw('sealed', env, url='https://a.example:443/x'), 'sealed-pw')
        self.assertEqual(self._pw('', env, url='https://c2.example/api'), 'top-pw')
        self.assertIsNone(self._pw('sealed', env, url='http://a.example'))  # other port/scheme = other host

    # -- the tokens commands share the resolver (_resolve_login / _tokens_scopes) --

    def _login(self, profile, env, url='', tty=False):
        tark_cli._PROFILE = profile
        with mock.patch.dict('os.environ', env, clear=True), mock.patch.object(tark_cli, '_URL_OVERRIDE', url), \
                mock.patch.object(tark_cli, '_PASSWORD_ENV_OVERRIDE', ''), \
                mock.patch.object(sys.stdin, 'isatty', lambda: tty), \
                mock.patch.object(tark_cli.getpass, 'getpass', lambda prompt: 'typed'), \
                mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            try:
                return tark_cli._resolve_login(Namespace(user='demo.manager'))
            except SystemExit:
                return err.getvalue()

    def test_resolve_login_under_a_profile_never_returns_the_c2_password(self):
        c2_only = {'TARK_PASSWORD': 'c2-pw'}
        self.assertIn('password_env', self._login('plain', c2_only))  # non-tty: refused, not c2-pw
        self.assertEqual(self._login('plain', c2_only, tty=True), ('demo.manager', 'typed'))
        self.assertIn('TARK_SEALED_PASSWORD', self._login('sealed', c2_only))
        self.assertIn('TARK_AEG_PASSWORD', self._login('samehost', c2_only))
        self.assertIn('password_env', self._login('plain', PW_ENV))  # foreign host: not $TARK_AEG_PASSWORD
        self.assertEqual(self._login('samehost', PW_ENV), ('demo.manager', 'aeg-pw'))
        self.assertEqual(self._login('sealed', PW_ENV), ('demo.manager', 'sealed-pw'))

    def test_resolve_login_under_url_never_returns_the_c2_password(self):
        url = 'https://other.example'
        env = {'TARK_PASSWORD': 'c2-pw', 'TARK_TOP_PASSWORD': 'top-pw'}
        self.assertIn('--password-env', self._login('', env, url=url))
        self.assertIn('--password-env', self._login('', dict(env, TARK_AEG_PASSWORD='aeg-pw'), url=url))
        self.assertEqual(self._login('', dict(env, TARK_AEG_PASSWORD='aeg-pw'), url=url, tty=True),
                         ('demo.manager', 'typed'))

    def test_resolve_login_default_target_uses_the_c2_password(self):
        self.assertEqual(self._login('', {'TARK_PASSWORD': 'c2-pw'}), ('demo.manager', 'c2-pw'))

    def _scopes(self, profile, env, url=''):
        tark_cli._PROFILE = profile
        jwt_login = mock.Mock(return_value='jwt')
        jwt_request = mock.Mock(return_value=['tasks:read'])
        no_prompt = mock.Mock(side_effect=AssertionError('tokens scopes must never prompt'))
        with mock.patch.dict('os.environ', env, clear=True), mock.patch.object(tark_cli, '_URL_OVERRIDE', url), \
                mock.patch.object(tark_cli, '_PASSWORD_ENV_OVERRIDE', ''), \
                mock.patch.object(tark_cli, '_jwt_login', jwt_login), \
                mock.patch.object(tark_cli, '_jwt_request', jwt_request), \
                mock.patch.object(sys.stdin, 'isatty', lambda: True), \
                mock.patch.object(tark_cli.getpass, 'getpass', no_prompt), \
                mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli._tokens_scopes(Namespace(user='demo.manager', json=True))
        no_prompt.assert_not_called()
        return jwt_login

    def test_tokens_scopes_under_a_profile_does_no_login_with_the_c2_password(self):
        self._scopes('plain', {'TARK_PASSWORD': 'c2-pw'}).assert_not_called()
        self._scopes('', {'TARK_PASSWORD': 'c2-pw'}, url='https://other.example').assert_not_called()
        self._scopes('plain', PW_ENV).assert_not_called()  # foreign host without password_env
        self._scopes('', PW_ENV, url='https://other.example').assert_not_called()
        self._scopes('samehost', PW_ENV).assert_called_once_with('demo.manager', 'aeg-pw')

    def test_tokens_scopes_default_target_logs_in_with_the_c2_password(self):
        self._scopes('', {'TARK_PASSWORD': 'c2-pw'}).assert_called_once_with('demo.manager', 'c2-pw')


class WorkforceScopeLabelTests(unittest.TestCase):
    """F4: the scope list marks workforce:* as not yet available on servers (PAT API parked)."""

    def _render(self, as_json):
        out = io.StringIO()
        with mock.patch.object(tark_cli, '_cfg_get', lambda key, default='': ''), \
                mock.patch.object(sys, 'stdout', out):
            tark_cli._tokens_scopes(Namespace(user=None, json=as_json))
        return out.getvalue()

    def test_table_marks_workforce_scopes_and_only_them(self):
        rows = [line for line in self._render(False).splitlines() if ':' in line and line.strip()[:1].isalpha()]
        wf = [r for r in rows if r.strip().startswith('workforce:')]
        self.assertEqual(len(wf), 2)
        for row in wf:
            self.assertIn('[not yet available on servers]', row)
        for row in rows:
            if not row.strip().startswith('workforce:'):
                self.assertNotIn('not yet available', row)

    def test_json_capabilities_mark_workforce_scopes(self):
        caps = json.loads(self._render(True))['capabilities']
        for scope in ('workforce:read', 'workforce:write'):
            self.assertTrue(caps[scope].startswith('[not yet available on servers]'), scope)
        self.assertFalse(any('not yet available' in v for k, v in caps.items() if not k.startswith('workforce:')))


class PasswordPromptNamesHostTests(unittest.TestCase):
    """F10: the getpass prompt names the user and the host the password is sent to."""

    setUp = AegPasswordTests.setUp  # same config fixture, without re-running AegPasswordTests' tests
    tearDown = AegPasswordTests.tearDown

    def _prompt(self, call, profile='', url='', user='demo.manager'):
        tark_cli._PROFILE = profile
        seen = []
        with mock.patch.dict('os.environ', {}, clear=True), mock.patch.object(tark_cli, '_URL_OVERRIDE', url), \
                mock.patch.object(tark_cli, '_PASSWORD_ENV_OVERRIDE', ''), \
                mock.patch.object(sys.stdin, 'isatty', lambda: True), \
                mock.patch.object(tark_cli.getpass, 'getpass', lambda prompt: seen.append(prompt) or 'typed'):
            call(user)
        self.assertEqual(len(seen), 1)
        return seen[0]

    def test_aeg_prompt_names_user_and_profile_host(self):
        self.assertEqual(self._prompt(tark_cli._aeg_password, profile='plain'),
                         'Password for demo.manager@b.example: ')

    def test_aeg_login_passes_the_seat_user_to_the_prompt(self):
        with mock.patch.object(tark_cli, '_jwt_login_payload', return_value={'access': 'a'}):
            prompt = self._prompt(lambda u: tark_cli._aeg_login({'user': u}), profile='plain', user='seat.one')
        self.assertEqual(prompt, 'Password for seat.one@b.example: ')

    def test_tokens_prompt_names_the_url_override_host_and_port(self):
        prompt = self._prompt(lambda u: tark_cli._resolve_login(Namespace(user=u)), url='https://other.example:8443')
        self.assertEqual(prompt, 'Password for demo.manager@other.example:8443: ')


class PrivateFileTests(unittest.TestCase):
    """config.json / aeg-sessions.json are written atomically and are never world-readable."""

    def setUp(self):
        self.old_umask = os.umask(0)  # most permissive umask: the mode must still come out 0600

    def tearDown(self):
        os.umask(self.old_umask)

    def test_save_config_creates_0600_file_in_a_0700_dir(self):
        d = Path(tempfile.mkdtemp()) / 'tark'
        with mock.patch.object(tark_cli, 'CONFIG_DIR', d), mock.patch.object(tark_cli, 'CONFIG_FILE', d / 'config.json'):
            tark_cli._save_config({'url': 'https://c2.example'})
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((d / 'config.json').stat().st_mode), 0o600)
        self.assertEqual(json.loads((d / 'config.json').read_text()), {'url': 'https://c2.example'})
        self.assertEqual(os.listdir(d), ['config.json'])  # no temp file left behind

    def test_existing_world_readable_file_is_replaced_0600(self):
        d = Path(tempfile.mkdtemp())
        f = d / 'aeg-sessions.json'
        f.write_text('{}')
        os.chmod(f, 0o644)
        with mock.patch.object(tark_cli, 'CONFIG_DIR', d), mock.patch.object(tark_cli, 'AEG_SESSIONS_FILE', f), \
                mock.patch.object(tark_cli, '_URL_OVERRIDE', 'https://c2.example'):
            tark_cli._aeg_store_session({'access': 'a', 'refresh': 'r', 'mode': 'all'}, 'demo.manager')
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(list(json.loads(f.read_text()).values()), [{'access': 'a', 'refresh': 'r', 'mode': 'all'}])

    def test_new_sessions_file_is_created_0600_without_the_password(self):
        d = Path(tempfile.mkdtemp()) / 'tark'
        f = d / 'aeg-sessions.json'
        with mock.patch.object(tark_cli, 'CONFIG_DIR', d), mock.patch.object(tark_cli, 'AEG_SESSIONS_FILE', f), \
                mock.patch.object(tark_cli, '_URL_OVERRIDE', 'https://c2.example'):
            tark_cli._aeg_store_session({'access': 'a', 'refresh': 'r', 'mode': 'all', 'password': 'pw-secret',
                                         'user': 'demo.manager'}, 'demo.manager')
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            self.assertNotIn('pw-secret', f.read_text())
            tark_cli._aeg_store_session(None, 'demo.manager')  # --relogin rewrites it: still 0600
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            self.assertEqual(json.loads(f.read_text()), {})
        self.assertEqual(os.listdir(d), ['aeg-sessions.json'])  # no temp file left behind


class NoPlanActualServer(FakeServer):
    """A seat without the Plan/Actual capability: schedule-instances/save/ 403s; the
    Graafik schedule-grid/save/ writes one location-less row per cell."""

    def request(self, method, path, body=None, params=None, soft_errors=(), bearer=None):
        if path.endswith('schedule-instances/save/'):
            self.calls.append((method, path, body, params))
            self._soft(403, soft_errors)
        if path.endswith('schedule-grid/save/'):
            self.calls.append((method, path, body, params))
            for a in body['assignments']:
                cell = self.planned.setdefault((a['user_id'], a['day']), [])
                cell[:] = [r for r in cell if r['location_id']]
                if a['shift_hour_id']:
                    self._add(a['user_id'], a['day'], a['shift_hour_id'], self._next(), None, loc=None)
            return {'created': len(body['assignments']), 'updated': 0, 'deleted': 0, 'blocked': 0}
        return super().request(method, path, body, params, soft_errors, bearer)


ONE_SHIFT = [{'employee': 'Mari Maasikas', 'date': '2026-10-05', 'shift': 'Vastuvõtt päev'}]


class LegacySaveTests(_Base):
    """F1: a 403 on the Plan/Actual save never silently becomes a Graafik save."""

    def grid_saves(self, server):
        return [c for c in server.calls if c[1].endswith('schedule-grid/save/')]

    def test_403_aborts_without_legacy_save(self):
        server = NoPlanActualServer()
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=_plan_file(ONE_SHIFT)))
        self.assertEqual(code, 1)
        self.assertIn('Plan/Actual save refused (403)', err)
        self.assertIn('--legacy-save', err)
        self.assertIn('Nothing was written', err)
        self.assertEqual(self.grid_saves(server), [])
        self.assertNotIn('Verified on server', out)

    def test_legacy_save_is_always_confirmed_then_uses_the_graafik_save(self):
        server = NoPlanActualServer()
        plan = _plan_file(ONE_SHIFT)
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=plan, legacy_save=True))
        self.assertEqual(code, 1)  # create-only plan, yet --legacy-save asks; empty stdin -> aborted
        self.assertIn('--legacy-save', err)
        self.assertEqual(server.saves(), [])
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                      _ns(file=plan, legacy_save=True, yes=True))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.grid_saves(server)), 1)
        self.assertIn('grid (--legacy-save)', out)

    def test_dry_run_shows_legacy_save(self):
        server = NoPlanActualServer()
        plan = _plan_file(ONE_SHIFT)
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                    _ns(file=plan, legacy_save=True, dry_run=True))
        self.assertEqual(code, 0)
        self.assertIn('--legacy-save', out)
        self.assertIn('location-less', out)
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                    _ns(file=plan, legacy_save=True, dry_run=True, json=True))
        doc = json.loads(out)
        self.assertTrue(doc['legacy_save'])
        self.assertIn('location-less', doc['legacy_save_note'])
        self.assertEqual(server.saves(), [])


class JsonWriteTests(_Base):
    """F7: --json on a real write prints exactly one JSON document on stdout."""

    def test_json_write_emits_json_only(self):
        server = FakeServer()
        code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                      _ns(file=_plan_file(ApplyTests.GOOD), json=True))
        self.assertEqual(code, 0, err)
        doc = json.loads(out)  # raises if a table leaked onto stdout
        self.assertEqual(len(doc['ops']), 3)
        self.assertEqual(doc['written']['created'], 3)
        self.assertEqual((doc['verified'], doc['expected']), (3, 3))
        self.assertIn('Verified on server: 3/3', err)

    def test_json_write_with_violations_reports_issues_and_exits_3(self):
        server = FakeServer()
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                    _ns(file=_plan_file(VIOLATION), json=True))
        self.assertEqual(code, 3)
        doc = json.loads(out)
        self.assertTrue(doc['issues'])
        self.assertIsNone(doc['written'])
        self.assertEqual(server.saves(), [])


class JsonPlanHeaderTests(_Base):
    """F9: `apply --json` sends the PLAN header to stderr - stdout is one JSON document."""

    def test_apply_json_plan_header_on_stderr_dry_run_and_write(self):
        for dry_run in (True, False):
            server = FakeServer()
            code, out, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server,
                                          _ns(file=_plan_file(ApplyTests.GOOD), json=True, dry_run=dry_run))
            self.assertEqual(code, 0, err)
            self.assertEqual(len(json.loads(out)['ops']), 3)  # raises if the header leaked onto stdout
            self.assertNotIn('PLAN ', out)
            self.assertIn('PLAN ', err)

    def test_apply_without_json_keeps_the_plan_header_on_stdout(self):
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, FakeServer(),
                                    _ns(file=_plan_file(ApplyTests.GOOD), dry_run=True))
        self.assertEqual(code, 0)
        self.assertIn('PLAN ', out)


class ConfirmNamesHostTests(_Base):
    """F8: the destructive confirm prompt names the host it writes to."""

    def test_replace_confirm_prompt_names_the_target_host(self):
        server = FakeServer(planned=[(101, 5, 12, 900)])
        plan = _plan_file([{'employee': 'Mari Maasikas', 'date': '2026-10-05', 'shift': 'Vastuvõtt päev'}])
        with mock.patch.object(tark_cli, '_URL_OVERRIDE', 'https://live-tenant.example'):
            code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=plan))
        self.assertEqual(code, 1)  # empty stdin -> aborted
        self.assertIn('About to delete/replace 1 existing planned shift(s) on https://live-tenant.example', err)
        self.assertEqual(server.saves(), [])

    def test_delete_confirm_prompt_names_the_target_host(self):
        server = FakeServer(planned=[(101, 31, 11, 900)])
        with mock.patch.object(tark_cli, '_URL_OVERRIDE', 'https://live-tenant.example'):
            code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_delete, server,
                                        _ns(date=['2026-10-31'], all_in_scope=True))
        self.assertEqual(code, 1)
        self.assertIn('live-tenant.example', err)
        self.assertEqual(server.saves(), [])


class RequireDateTests(_Base):
    """F11: an impossible --require date is a clean exit-2 error, never a traceback."""

    def test_cli_require_with_impossible_date_is_an_argparse_error(self):
        err = io.StringIO()
        with mock.patch.object(sys, 'stderr', err), self.assertRaises(SystemExit) as cm:
            tark_cli.build_parser().parse_args(['aeg', 'schedule', 'check', '--week', '2026-W41',
                                                '--require', 'Vastuvõtt päev@2026-02-30=1'])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn('not a calendar date', err.getvalue())
        self.assertNotIn('Traceback', err.getvalue())

    def test_cli_require_valid_forms_parse(self):
        args = tark_cli.build_parser().parse_args(['aeg', 'schedule', 'check', '--week', '2026-W41',
                                                   '--require', 'VP=2', '--require', 'VP@2026-10-05=1'])
        self.assertEqual(args.require, ['VP=2', 'VP@2026-10-05=1'])

    def test_plan_rule_require_with_impossible_date_exits_2(self):
        plan = _plan_file([{'employee': 'Liis Lepp', 'date': '2026-10-05', 'shift': 'Vastuvõtt päev'}],
                          rules={'require': ['Vastuvõtt päev@2026-02-30=2']})
        server = FakeServer()
        code, _, err = self.run_cmd(tark_cli.cmd_aeg_schedule_apply, server, _ns(file=plan, dry_run=True))
        self.assertEqual(code, 2)
        self.assertIn('not a calendar date', err)
        self.assertEqual(server.saves(), [])


class ArgValidationTests(_Base):
    """F6: bad --weekdays / --week / --month are argparse errors (exit 2), not tracebacks."""

    def parse(self, *argv):
        err = io.StringIO()
        with mock.patch.object(sys, 'stderr', err):
            try:
                return tark_cli.build_parser().parse_args(list(argv)), ''
            except SystemExit as e:
                self.assertEqual(e.code, 2)
                return None, err.getvalue()

    SET = ('aeg', 'schedule', 'set', '-e', 'Mari', '--shift', 'VP', '--from', '2026-10-05', '--to', '2026-10-11')

    def test_weekdays(self):
        args, _ = self.parse(*self.SET, '--weekdays', '1,3')
        self.assertEqual(args.weekdays, frozenset({1, 3}))
        for bad in ('1,x', '8', '0', ''):
            args, err = self.parse(*self.SET, '--weekdays', bad)
            self.assertIsNone(args, bad)
            self.assertIn('ISO weekdays', err)

    def test_week_and_month(self):
        self.assertEqual(self.parse('aeg', 'schedule', 'check', '--week', '2026-W41')[0].week, '2026-W41')
        for bad in ('2026-W54', '2026-W00', 'W41'):
            args, err = self.parse('aeg', 'schedule', 'check', '--week', bad)
            self.assertIsNone(args, bad)
            self.assertIn('ISO week', err)
        args, err = self.parse('aeg', 'schedule', 'delete', '--month', '2026-13')
        self.assertIsNone(args)
        self.assertIn('YYYY-MM', err)
        self.assertIsNone(self.parse('aeg', 'employees', '--month', '2026-0')[0])

    def test_set_with_parsed_weekdays(self):
        server = FakeServer()
        code, out, _ = self.run_cmd(tark_cli.cmd_aeg_schedule_set, server,
                                    _ns(employee=['Mari Maasikas'], shift='Vastuvõtt päev', date_from='2026-10-05',
                                        date_to='2026-10-11', weekdays=frozenset({1, 3}), dry_run=True))
        self.assertEqual(code, 0, out)
        self.assertIn('2 to create', out)


class ResolveIpTests(unittest.TestCase):
    """F5: a profile's resolve_ip pins only the profile's own host, never a --url override."""

    def setUp(self):
        self.cfg = Path(tempfile.mkdtemp()) / 'config.json'
        self.cfg.write_text(json.dumps({'url': 'https://c2.example', 'profiles': {
            'demo': {'url': 'https://demo.example', 'resolve_ip': '192.0.2.10'}}}))
        self.patches = [mock.patch.object(tark_cli, 'CONFIG_FILE', self.cfg),
                        mock.patch.object(tark_cli, '_PROFILE', 'demo'),
                        mock.patch.object(tark_cli, '_URL_OVERRIDE', '')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def ip(self, url='', resolve_ip=None, command='aeg'):
        with mock.patch.object(tark_cli, '_URL_OVERRIDE', url), mock.patch.object(sys, 'stderr', io.StringIO()) as err:
            return tark_cli._effective_resolve_ip(Namespace(resolve_ip=resolve_ip, command=command)), err.getvalue()

    def test_profile_host(self):
        self.assertEqual(self.ip()[0], '192.0.2.10')
        self.assertEqual(self.ip(url='https://demo.example/')[0], '192.0.2.10')

    def test_url_on_another_host_is_not_pinned(self):
        ip, err = self.ip(url='https://other.example')
        self.assertEqual(ip, '')
        self.assertIn('resolve_ip not applied', err)

    def test_explicit_flag_wins_and_config_skips(self):
        self.assertEqual(self.ip(url='https://other.example', resolve_ip='192.0.2.99')[0], '192.0.2.99')
        self.assertEqual(self.ip(command='config')[0], '')


class PublicRepoHygieneTests(unittest.TestCase):
    """The repo is PUBLIC: no real deployment host may appear in any tracked text file."""

    def test_no_tracked_file_names_a_real_deployment_domain(self):
        if not shutil.which('git'):
            self.skipTest('git not available')
        res = subprocess.run(['git', 'ls-files', '-z'], cwd=_AUTOMATION_DIR, capture_output=True, check=False)
        if res.returncode != 0:
            self.skipTest('not a git checkout')
        needle = ('on' + 'tark').encode()  # split so this file does not match itself
        hits = []
        for name in filter(None, res.stdout.decode().split('\0')):
            try:
                data = (_AUTOMATION_DIR / name).read_bytes()
            except OSError:  # tracked but deleted in the working tree
                continue
            if b'\0' not in data and needle in data.lower():
                hits.append(name)
        self.assertEqual(hits, [])


if __name__ == '__main__':
    unittest.main()
