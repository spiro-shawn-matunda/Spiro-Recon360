"""Regression checks for payment features merged into the app/web layout."""
import csv
import http.client
import io
import json
import threading
import unittest
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

from app import dashboard, import_service


class AssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ('src', 'href') and value.startswith('/'):
                self.paths.append(value)


class PaymentMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = dashboard.DashboardServer(('127.0.0.1', 0), {})
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=5)
        cls.server.server_close()

    def request(self, path, method='GET', body=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {}
        if body is not None:
            headers = {'Content-Type': 'application/octet-stream', 'X-Filename': 'payment.csv',
                       'X-Spiro-Token': self.server.csrf_token}
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_home_and_payment_pages_link_to_served_assets(self):
        pages = ('/', '/dashboard_ui/payments-wallet.html',
                 '/dashboard_ui/payments-swap.html', '/dashboard_ui/payments-due.html')
        for path in pages:
            with self.subTest(path=path):
                status, body = self.request(path)
                self.assertEqual(status, 200)
                parser = AssetLinks()
                parser.feed(body.decode('utf-8'))
                for link in parser.paths:
                    self.assertEqual(self.request(link)[0], 200, link)
        home = self.request('/')[1].decode('utf-8')
        for view in ('view-home', 'view-overview', 'view-counterparts', 'view-due', 'view-offer'):
            self.assertIn('id="' + view + '"', home)

    def test_each_payment_upload_validates_in_its_own_manager(self):
        cases = (
            ('payments', self.server.payment_imports, 'atlas',
             {'Record Id': '00001', 'Atlas Transaction ID': '000123',
              'Remarks': 'Wallet Recharge', 'Payment Status': 'Paid', 'Country': 'Kenya'}),
            ('swap', self.server.swap_imports, 'swap_payments',
             {'Record Id': '00001', 'Atlas Transaction ID': '000123', 'Payment Status': 'Paid'}),
            ('due', self.server.due_imports, 'due_payments',
             {'Record Id': '00001', 'Country': 'Uganda', 'Remarks': 'Settlement of Rent',
              'Union Due ID (New)': ''}),
        )
        for scope, manager, dataset, row in cases:
            with self.subTest(scope=scope):
                stream = io.StringIO(newline='')
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
                status, body = self.request('/api/' + scope + '-uploads', 'POST',
                                            stream.getvalue().encode('utf-8'))
                self.assertEqual(status, 202, body)
                key = json.loads(body)['id']
                # Waiting on the worker queue ensures validation and its callback have finished.
                manager.pool.submit(lambda: None).result(timeout=5)
                status, body = self.request('/api/' + scope + '-import-jobs/' + key)
                job = json.loads(body)
                self.assertEqual(status, 200)
                self.assertEqual(job['state'], 'ready', job)
                self.assertEqual(job['preview']['dataset'], dataset)
                self.assertGreater(manager.jobs[key]['received_bytes'], 0)
                self.assertEqual(self.request('/api/import-jobs/' + key)[0], 404)

                importer = MagicMock(return_value={'rows_read': 1, 'rows_applied': 1})
                with patch.object(manager, 'importer', importer), \
                        patch('app.import_service.psycopg.connect') as connect, \
                        patch('app.import_service.refresh_cache') as refresh:
                    conn = connect.return_value.__enter__.return_value
                    status, body = self.request('/api/' + scope + '-import-jobs/' + key + '/commit', 'POST')
                    # Commit also requires CSRF, including requests without a CSV body.
                    self.assertEqual(status, 403)
                    status, body = self.request('/api/' + scope + '-import-jobs/' + key + '/commit', 'POST', b'')
                    self.assertEqual(status, 202, body)
                    manager.pool.submit(lambda: None).result(timeout=5)
                    self.assertEqual(manager.status(key)['state'], 'complete')
                    importer.assert_called_once()
                    self.assertEqual(importer.call_args.args[2], dataset)
                    statements = [call.args[0] for call in conn.execute.call_args_list]
                    analyze = [s for s in statements if not isinstance(s, str)][0]
                    self.assertIn('"' + manager.schema + '"', analyze.as_string())
                    refresh.assert_not_called()
                manager.discard(key)

    def test_startup_prepares_both_existing_and_payment_schemas(self):
        with patch('app.import_service.psycopg.connect') as connect, \
                patch('app.import_service.install_cache') as install, \
                patch('app.import_service.refresh_cache') as refresh:
            import_service.prepare_database({})
            conn = connect.return_value.__enter__.return_value
            statements = '\n'.join(str(call.args[0]) for call in conn.execute.call_args_list)
            for schema in ('reconciliation', 'payments_reconciliation', 'swap_reconciliation', 'due_reconciliation'):
                self.assertIn('CREATE SCHEMA IF NOT EXISTS ' + schema, statements)
            install.assert_called_once_with(conn)
            refresh.assert_called_once_with(conn)
