"""HTTP integration tests against an explicitly supplied isolated test database."""
import csv
import io
import json
import os
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from psycopg.conninfo import conninfo_to_dict
from app.dashboard import DashboardServer


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=DashboardServer(('127.0.0.1',0),conninfo_to_dict(os.environ['SPIRO_TEST_DSN']))
        cls.port=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def request(self,path,headers=None,method='GET'):
        request=Request(f'http://127.0.0.1:{self.port}'+path,headers=headers or {},method=method)
        try:
            with urlopen(request,timeout=20) as response:
                return response.status,response.headers,response.read()
        except HTTPError as response:
            return response.code,response.headers,response.read()

    def test_static_assets_and_private_files(self):
        for path in ('/','/app.js','/styles.css','/favicon.svg'):
            status,headers,body=self.request(path)
            self.assertEqual(status,200)
            self.assertTrue(body)
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        for path in ('/.env','/../.env','/%2e%2e/.env','/config.json','/data/Wallet_2026_10_05.csv'):
            with self.subTest(path=path):self.assertEqual(self.request(path)[0],404)

    def test_duplicate_dashboard_cannot_share_an_active_port(self):
        with self.assertRaises(OSError):
            DashboardServer(('127.0.0.1', self.port), self.server.database_settings)
        self.assertEqual(self.request('/api/session')[0], 200)

    def test_rejects_untrusted_hosts_origins_and_writes(self):
        for headers in ({'Host':'other.example'},{'Origin':'https://other.example'},{'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/dashboard',headers=headers)[0],403)
        self.assertEqual(self.request('/api/dashboard',method='POST')[0],405)

    def test_country_filters_and_pagination(self):
        status,_,body=self.request('/api/dashboard?country=Kenya&review_only=false&limit=2')
        self.assertEqual(status,200)
        data=json.loads(body)
        self.assertTrue(all(g['country']=='Kenya' for g in data['summary']['groups']))
        first=data['page'];self.assertEqual(len(first['records']),2);self.assertTrue(first['has_more'])
        status,_,body=self.request('/api/records?country=Kenya&review_only=false&limit=2&cursor='+first['next_cursor'])
        self.assertEqual(status,200)
        second=json.loads(body)['page']
        self.assertFalse({r['wallet_record_id'] for r in first['records']}&{r['wallet_record_id'] for r in second['records']})

    def test_invalid_filters_and_unknown_parameters(self):
        for query in ('country=Uganda','limit=1001','review_only=1','start=2026-99-99','country=Kenya&country=Rwanda','password=x'):
            with self.subTest(query=query):self.assertEqual(self.request('/api/dashboard?'+query)[0],400)

    def test_details_and_empty_dates(self):
        status,_,body=self.request('/api/detail?country=Kenya&record_id=matched')
        self.assertEqual(status,200)
        record=json.loads(body)['record']
        self.assertEqual(record['wallet_amount'],'120.10')
        self.assertNotIn('raw_record',record)
        self.assertEqual(self.request('/api/detail?country=Rwanda&record_id=matched')[0],404)
        status,_,body=self.request('/api/dashboard?start=2030-01-01')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['summary']['deduction_count'],0)
        self.assertEqual(json.loads(body)['page']['records'],[])

    def test_review_export_obeys_country_and_status(self):
        status,headers,body=self.request('/api/export?country=Kenya&status=matched')
        self.assertEqual(status,200)
        self.assertIn('attachment',headers['Content-Disposition'])
        rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertTrue(rows)
        self.assertTrue(all(r['country']=='Kenya' and r['reconciliation_status']=='matched' for r in rows))
        self.assertNotIn('matched',{r['wallet_record_id'] for r in rows})
        self.assertTrue(all(json.loads(row['review_reasons']) for row in rows))


if __name__=='__main__':unittest.main()
