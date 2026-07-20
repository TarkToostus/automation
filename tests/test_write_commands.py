"""Tests for the create/update write commands (offers, offer-lines, contracts,
leads-update, time-update, email-tasks-create, leads-ingest).

No network: `tark_cli._request` is mocked. Asserts method/URL/body, sparse-PATCH
(only provided flags sent), required-flag validation, and JSON-field parsing.
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


def _ns_for(spec, **overrides):
    """Namespace with every flag in `spec` defaulting to None, plus overrides."""
    d = {flag: None for flag, _f, _k in spec}
    d.update(json=False)
    d.update(overrides)
    return Namespace(**d)


def _capture():
    cap = {}

    def fake(method, path, body=None, params=None):
        cap['method'] = method
        cap['path'] = path
        cap['body'] = body
        return {'id': 7, 'title': (body or {}).get('title', ''), 'status': (body or {}).get('status', 'DRAFT')}

    return mock.patch.object(tark_cli, '_request', fake), cap


class OffersTests(unittest.TestCase):
    def test_create_requires_title(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_offers_create(_ns_for(tark_cli._OFFER_FIELDS))

    def test_create_body_sparse_and_typed(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_offers_create(_ns_for(
                tark_cli._OFFER_FIELDS, title='Retrofit', client=5, amount=1500.0))
        self.assertEqual(cap['method'], 'POST')
        self.assertEqual(cap['path'], '/api/v1/pat/sales/offers/')
        self.assertEqual(cap['body'], {'title': 'Retrofit', 'client': 5, 'amount': 1500.0})

    def test_crm_meta_json_parsed(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_offers_create(_ns_for(
                tark_cli._OFFER_FIELDS, title='X', crm_meta='{"utm":"ads"}'))
        self.assertEqual(cap['body']['crm_meta'], {'utm': 'ads'})

    def test_bad_json_rejected(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_offers_create(_ns_for(
                tark_cli._OFFER_FIELDS, title='X', crm_meta='{not json'))

    def test_update_is_sparse_patch(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_offers_update(_ns_for(
                tark_cli._OFFER_FIELDS, id=9, probability=80.0))
        self.assertEqual(cap['method'], 'PATCH')
        self.assertEqual(cap['path'], '/api/v1/pat/sales/offers/9/')
        self.assertEqual(cap['body'], {'probability': 80.0})  # ONLY the provided flag

    def test_update_no_flags_errors(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_offers_update(_ns_for(tark_cli._OFFER_FIELDS, id=9))


class OfferLinesTests(unittest.TestCase):
    def test_create_requires_offer_and_description(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_offer_lines_create(_ns_for(tark_cli._OFFERLINE_FIELDS, offer=3))

    def test_create_body(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_offer_lines_create(_ns_for(
                tark_cli._OFFERLINE_FIELDS, offer=3, description='Widget', quantity=2.0, unit_price=99.0))
        self.assertEqual((cap['method'], cap['path']), ('POST', '/api/v1/pat/sales/offer-lines/'))
        self.assertEqual(cap['body'], {'offer': 3, 'description': 'Widget', 'quantity': 2.0, 'unit_price': 99.0})

    def test_update_sparse(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_offer_lines_update(_ns_for(tark_cli._OFFERLINE_FIELDS, id=4, discount=10.0))
        self.assertEqual((cap['method'], cap['path'], cap['body']),
                         ('PATCH', '/api/v1/pat/sales/offer-lines/4/', {'discount': 10.0}))


class ContractsTests(unittest.TestCase):
    def test_create_body_no_required(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_contracts_create(_ns_for(tark_cli._CONTRACT_FIELDS, title='MSA', client=2, template=1))
        self.assertEqual((cap['method'], cap['path']), ('POST', '/api/v1/pat/sales/contracts/'))
        self.assertEqual(cap['body'], {'title': 'MSA', 'client': 2, 'template': 1})

    def test_update_sparse(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_contracts_update(_ns_for(tark_cli._CONTRACT_FIELDS, id=8, status='signed'))
        self.assertEqual(cap['body'], {'status': 'signed'})


class LeadsUpdateTests(unittest.TestCase):
    def test_sparse_patch(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_leads_update(_ns_for(tark_cli._LEAD_FIELDS, id=11, status='QUALIFIED', pipeline_stage=4))
        self.assertEqual((cap['method'], cap['path']), ('PATCH', '/api/v1/pat/sales/leads/11/'))
        self.assertEqual(cap['body'], {'status': 'QUALIFIED', 'pipeline_stage': 4})

    def test_no_flags_errors(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_leads_update(_ns_for(tark_cli._LEAD_FIELDS, id=11))


class TimeUpdateTests(unittest.TestCase):
    def test_sparse_patch(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_time_update(_ns_for(tark_cli._TIMEENTRY_FIELDS, id=6, hours=2.5, description='rework'))
        self.assertEqual((cap['method'], cap['path']), ('PATCH', '/api/v1/pat/pm/time-entries/6/'))
        self.assertEqual(cap['body'], {'hours': 2.5, 'description': 'rework'})


class EmailTasksCreateTests(unittest.TestCase):
    def test_requires_lead(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_email_tasks_create(_ns_for(tark_cli._EMAILTASK_FIELDS, subject='Hi'))

    def test_create_body_no_confirm_field(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_email_tasks_create(_ns_for(
                tark_cli._EMAILTASK_FIELDS, lead=12, subject='Follow-up', body='text', status='REVIEW'))
        self.assertEqual((cap['method'], cap['path']), ('POST', '/api/v1/pat/sales/email-tasks/'))
        self.assertEqual(cap['body'], {'lead': 12, 'subject': 'Follow-up', 'body': 'text', 'status': 'REVIEW'})
        # No send/confirm surface: the CLI has no flag that could set CONFIRMED/SENT/FAILED.
        self.assertFalse(any(f in ('confirmed_at', 'sent_at', 'sent_via') for _fl, f, _k in tark_cli._EMAILTASK_FIELDS))


class LeadsIngestTests(unittest.TestCase):
    def test_requires_pipeline(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_leads_ingest(Namespace(json=False, pipeline=None, leads='[]', leads_file=None, source_loop=None))

    def test_requires_leads_payload(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_leads_ingest(Namespace(json=False, pipeline='Imports', leads=None, leads_file=None, source_loop=None))

    def test_body(self):
        patcher, cap = _capture()
        with patcher, mock.patch.object(sys, 'stdout', io.StringIO()):
            tark_cli.cmd_leads_ingest(Namespace(
                json=False, pipeline='Imports', leads='[{"title":"Acme"}]',
                leads_file=None, source_loop='weekly'))
        self.assertEqual((cap['method'], cap['path']), ('POST', '/api/v1/pat/sales/leads/ingest/'))
        self.assertEqual(cap['body'], {'pipeline': 'Imports', 'leads': [{'title': 'Acme'}], 'source_loop': 'weekly'})

    def test_bad_leads_json_errors(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            tark_cli.cmd_leads_ingest(Namespace(
                json=False, pipeline='Imports', leads='{bad', leads_file=None, source_loop=None))


if __name__ == '__main__':
    unittest.main()
