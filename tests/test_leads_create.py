"""Tests for `tark_cli leads create` body composition + pipeline resolution.

No network: `tark_cli._post` and `tark_cli._get` are mocked. Each test asserts
the request body the CLI would send, mirroring the server-side LeadSerializer
contract (only `title` is required; `source`/`status` upper-cased; `--pipeline`
resolves a `sales_lead` pipeline by name or numeric ID).
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

import tark_cli


def _ns(**kw):
    defaults = {
        'action': 'create', 'json': False, 'title': None, 'company': None,
        'person': None, 'email': None, 'phone': None, 'source': None,
        'status': None, 'notes': None, 'pipeline': None,
    }
    defaults.update(kw)
    return Namespace(**defaults)


_PIPELINES = [
    {'id': 7, 'name': 'Imports', 'pipeline_type': 'sales_lead'},
    {'id': 9, 'name': 'Hiring', 'pipeline_type': 'sales_lead'},
    {'id': 3, 'name': 'Imports', 'pipeline_type': 'sales_deal'},  # name collision, wrong type
]


class LeadsCreateBodyTests(unittest.TestCase):
    def _create(self, **kw):
        """Run cmd_leads in create mode, return the body passed to _post."""
        captured = {}

        def fake_post(path, body=None):
            captured['path'] = path
            captured['body'] = body
            return {'id': 42, 'title': body.get('title'), 'company_name': body.get('company_name', '')}

        with mock.patch.object(tark_cli, '_post', fake_post), \
                mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_leads(_ns(**kw))
        return captured

    def test_minimal_body_only_title(self):
        cap = self._create(title='Acme retrofit')
        self.assertEqual(cap['path'], '/api/v1/pat/sales/leads/')
        self.assertEqual(cap['body'], {'title': 'Acme retrofit'})

    def test_optional_fields_included_when_set(self):
        cap = self._create(
            title='Acme retrofit', company='Acme OÜ', person='Mari Maasikas',
            email='mari@acme.ee', phone='+372 5000 0000', notes='warm intro',
        )
        b = cap['body']
        self.assertEqual(b['company_name'], 'Acme OÜ')
        self.assertEqual(b['person_name'], 'Mari Maasikas')
        self.assertEqual(b['email'], 'mari@acme.ee')
        self.assertEqual(b['phone'], '+372 5000 0000')
        self.assertEqual(b['notes'], 'warm intro')

    def test_source_and_status_upcased(self):
        cap = self._create(title='X', source='cold', status='qualified')
        self.assertEqual(cap['body']['source'], 'COLD')
        self.assertEqual(cap['body']['status'], 'QUALIFIED')

    def test_unset_optionals_absent_not_null(self):
        cap = self._create(title='X')
        for k in ('company_name', 'person_name', 'email', 'phone', 'source', 'status', 'notes', 'pipeline'):
            self.assertNotIn(k, cap['body'])

    def test_pipeline_name_resolved_to_id(self):
        with mock.patch.object(tark_cli, '_get', lambda path: {'results': _PIPELINES}):
            cap = self._create(title='X', pipeline='Imports')
        self.assertEqual(cap['body']['pipeline'], 7)  # sales_lead Imports, not the deal one (id 3)

    def test_pipeline_numeric_passthrough_no_network(self):
        def boom(path):
            raise AssertionError('numeric pipeline must not hit the network')

        with mock.patch.object(tark_cli, '_get', boom):
            cap = self._create(title='X', pipeline='7')
        self.assertEqual(cap['body']['pipeline'], 7)

    def test_missing_title_exits(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli.cmd_leads(_ns(title=None))


class ResolveLeadPipelineTests(unittest.TestCase):
    def test_numeric_returns_int_without_get(self):
        with mock.patch.object(tark_cli, '_get', lambda p: self.fail('no network')):
            self.assertEqual(tark_cli._resolve_lead_pipeline('12'), 12)

    def test_name_filters_to_sales_lead_type(self):
        with mock.patch.object(tark_cli, '_get', lambda p: {'results': _PIPELINES}):
            self.assertEqual(tark_cli._resolve_lead_pipeline('Imports'), 7)

    def test_substring_match(self):
        with mock.patch.object(tark_cli, '_get', lambda p: {'results': _PIPELINES}):
            self.assertEqual(tark_cli._resolve_lead_pipeline('hir'), 9)

    def test_no_match_exits(self):
        with mock.patch.object(tark_cli, '_get', lambda p: {'results': _PIPELINES}), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli._resolve_lead_pipeline('Nonexistent')

    def test_ambiguous_exits(self):
        dupes = [
            {'id': 1, 'name': 'Inbound EU', 'pipeline_type': 'sales_lead'},
            {'id': 2, 'name': 'Inbound US', 'pipeline_type': 'sales_lead'},
        ]
        with mock.patch.object(tark_cli, '_get', lambda p: {'results': dupes}), \
                mock.patch.object(sys, 'stderr', io.StringIO()), \
                self.assertRaises(SystemExit):
            tark_cli._resolve_lead_pipeline('Inbound')


if __name__ == '__main__':
    unittest.main()
