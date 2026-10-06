"""Self-service validation and HTTP imports against explicit isolated fixtures."""
import csv
import io
import json
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict
from dashboard import DashboardServer
from import_csv import MAPS
from import_service import MAX_UPLOAD_BYTES, inspect_csv


def csv_bytes(dataset='wallet_master', records=None, extra_headers=()):
    output = io.StringIO(newline='')
    fields = [field for _, field, _ in MAPS[dataset]] + list(extra_headers)
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for record in records or [{'Record Id': 'selfservice-test-wallet', 'Country': 'Kenya', 'Currency': 'KES', 'Customer.id': 'test-customer', 'Wallet Pin': '123456'}]:
        writer.writerow({field: record.get(field, '') for field in fields})
    return output.getvalue().encode('utf-8-sig')


class ValidationTests(unittest.TestCase):
    def test_master_detection_and_pin_removed_from_storage(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'master.csv'
            path.write_bytes(csv_bytes(extra_headers=('Wallet Pin',)))
            summary = inspect_csv(path)
            self.assertEqual(summary['dataset'], 'wallet_master')
            self.assertEqual(summary['countries'], {'Kenya': 1})
            self.assertNotIn('Wallet Pin', path.read_text(encoding='utf-8'))
            self.assertNotIn('123456', path.read_text(encoding='utf-8'))

    def test_bad_headers_empty_country_and_bad_decimal_rejected(self):
        records = [{'Record Id': 'a', 'Country': 'Uganda'}, {'Record Id': 'a', 'Country': ''},
                   {'Record Id': 'a', 'Country': 'Kenya', 'Available Balance': 'NaN'}]
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'invalid.csv'
            for record in records:
                path.write_bytes(csv_bytes(records=[record]))
                with self.assertRaises(ValueError): inspect_csv(path)
            path.write_text('Record Id,Country\na,Kenya\n', encoding='utf-8')
            with self.assertRaises(ValueError): inspect_csv(path)

    def test_main_defaults_to_setup_and_dashboard(self):
        import main
        settings = {'example': 'test'}
        with patch('main.read_database_config', return_value=settings), patch('import_service.prepare_database') as prepare, patch('dashboard.serve') as serve, patch('main.read_config') as config:
            main.main(['--port', '8767'])
            prepare.assert_called_once_with(settings)
            serve.assert_called_once_with(settings, 8767)
            config.assert_not_called()


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class UploadIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = conninfo_to_dict(os.environ['SPIRO_TEST_DSN'])
        cls.server = DashboardServer(('127.0.0.1', 0), cls.settings)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]
        cls.token = cls.server.csrf_token

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        with psycopg.connect(**cls.settings) as conn:
            for table in ('wallets', 'wallet_transactions', 'swap_transactions'):
                conn.execute(psycopg.sql.SQL('DELETE FROM {} WHERE zoho_record_id LIKE %s').format(psycopg.sql.Identifier('reconciliation', table)), ('selfservice-test-%',))
            conn.execute("DELETE FROM reconciliation.import_batches WHERE source_file LIKE 'selfservice-test-%'")

    def request(self, path, body=None, headers=None, method=None):
        request = Request(f'http://127.0.0.1:{self.port}'+path, data=body, headers=headers or {}, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except HTTPError as response:
            return response.code, json.loads(response.read())

    def upload(self, data, name='selfservice-test-master.csv'):
        status, job = self.request('/api/uploads', data, {'Content-Type': 'application/octet-stream', 'X-Filename': name, 'X-Spiro-Token': self.token})
        self.assertEqual(status, 202)
        return self.wait(job['id'])

    def wait(self, key):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, job = self.request('/api/import-jobs/'+key)
            self.assertEqual(status, 200)
            if job['state'] not in ('receiving', 'validating', 'importing'): return job
            time.sleep(.05)
        self.fail('Import job did not finish')

    def commit(self, key):
        return self.request('/api/import-jobs/'+key+'/commit', b'', {'X-Spiro-Token': self.token})

    def test_rejects_csrf_cross_origin_path_and_oversized_upload(self):
        payload = csv_bytes()
        self.assertEqual(self.request('/api/uploads', payload, {'Content-Type': 'application/octet-stream', 'X-Filename': 'a.csv'})[0], 403)
        headers = {'Content-Type': 'application/octet-stream', 'X-Spiro-Token': self.token, 'X-Filename': '../a.csv'}
        self.assertEqual(self.request('/api/uploads', payload, headers)[0], 400)
        headers['X-Filename'] = 'a.csv'; headers['Origin'] = 'https://other.example'
        self.assertEqual(self.request('/api/uploads', payload, headers)[0], 403)
        headers.pop('Origin'); headers['Content-Length'] = str(MAX_UPLOAD_BYTES+1)
        self.assertEqual(self.request('/api/uploads', b'x', headers)[0], 413)

    def test_invalid_file_cannot_commit_and_changes_no_data(self):
        job = self.upload(csv_bytes(records=[{'Record Id': 'selfservice-test-invalid', 'Country': 'Kenya'}, {'Record Id': 'selfservice-test-invalid2', 'Country': 'Rwanda', 'Available Balance': 'not-a-number'}]))
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(self.commit(job['id'])[0], 400)
        with psycopg.connect(**self.settings) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM reconciliation.wallets WHERE zoho_record_id LIKE 'selfservice-test-invalid%'").fetchone()[0], 0)

    def test_import_repeat_and_source_history(self):
        data = csv_bytes(extra_headers=('Wallet Pin',))
        first = self.upload(data)
        self.assertEqual(first['state'], 'ready')
        self.assertEqual(self.commit(first['id'])[0], 202)
        first = self.wait(first['id'])
        self.assertEqual(first['state'], 'complete')
        self.assertEqual(first['result']['rows_applied'], 1)
        self.assertEqual(self.commit(first['id'])[0], 400)
        second = self.upload(data)
        self.assertEqual(self.commit(second['id'])[0], 202)
        self.assertEqual(self.wait(second['id'])['result']['rows_applied'], 0)
        with psycopg.connect(**self.settings) as conn:
            record = conn.execute("SELECT raw_record FROM reconciliation.wallets WHERE zoho_record_id='selfservice-test-wallet'").fetchone()[0]
            self.assertNotIn('Wallet Pin', record)
        status, inventory = self.request('/api/sources')
        self.assertEqual(status, 200)
        self.assertIn('selfservice-test-master.csv', {batch['filename'] for batch in inventory['recent_imports']})
        self.assertTrue(inventory['sources'])
        self.assertEqual(self.request('/api/import-jobs/'+second['id']+'/discard', b'', {'X-Spiro-Token': self.token})[0], 200)
        self.assertEqual(self.request('/api/import-jobs/'+second['id'])[0], 404)

    def test_three_module_import_and_reconciliation_refresh(self):
        common = {'Country':'Rwanda','Created On':'2026-09-23T13:00:00'}
        records = {
            'wallet_master': {**common, 'Record Id':'selfservice-test-rw-master','Currency':'RWF','Customer.id':'selfservice-test-customer','Customer Name':'Test rider'},
            'wallet': {**common,'Record Id':'selfservice-test-rw-wallet','Transaction ID':'selfservice-test-tx','Wallet.id':'selfservice-test-rw-master','Type':'Debit','Status':'Committed','Settled Against':'Swap','Amount':'2377.10'},
            'swap': {**common,'Record Id':'selfservice-test-rw-swap','Transaction ID':'selfservice-test-tx','Status':'SUCCESS','Swap Amount':'2377.10','Pay Method':'WALLET','Customer.id':'selfservice-test-customer'},
        }
        for dataset, record in records.items():
            job = self.upload(csv_bytes(dataset, [record]), 'selfservice-test-'+dataset+'.csv')
            self.assertEqual(job['state'], 'ready')
            self.assertEqual(job['preview']['dataset'], dataset)
            self.assertEqual(self.commit(job['id'])[0], 202)
            self.assertEqual(self.wait(job['id'])['state'], 'complete')
        status, data = self.request('/api/detail?record_id=selfservice-test-rw-wallet&country=Rwanda')
        self.assertEqual(status, 200)
        self.assertEqual(data['record']['reconciliation_status'], 'matched')
        self.assertEqual(data['record']['wallet_amount'], '2377.10')
        self.assertEqual(data['record']['wallet_currency'], 'RWF')

    def test_database_failure_rolls_back_whole_file_and_audit(self):
        with psycopg.connect(**self.settings, autocommit=True) as conn:
            conn.execute("ALTER TABLE reconciliation.wallets ADD CONSTRAINT selfservice_test_reject CHECK (zoho_record_id <> 'selfservice-test-blocked') NOT VALID")
            try:
                job = self.upload(csv_bytes(records=[{'Record Id':'selfservice-test-before-failure','Country':'Kenya'}, {'Record Id':'selfservice-test-blocked','Country':'Kenya'}]), 'selfservice-test-rollback.csv')
                self.assertEqual(job['state'], 'ready')
                self.assertEqual(self.commit(job['id'])[0], 202)
                job = self.wait(job['id'])
                self.assertEqual(job['state'], 'failed')
                self.assertIn('rolled back', job['error'])
                self.assertEqual(conn.execute("SELECT count(*) FROM reconciliation.wallets WHERE zoho_record_id IN ('selfservice-test-before-failure','selfservice-test-blocked')").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT count(*) FROM reconciliation.import_batches WHERE source_file='selfservice-test-rollback.csv'").fetchone()[0], 0)
            finally:
                conn.execute('ALTER TABLE reconciliation.wallets DROP CONSTRAINT selfservice_test_reject')


if __name__ == '__main__': unittest.main()
