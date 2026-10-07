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
import psycopg
from app.dashboard import DashboardServer, filter_dates


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
        self.assertTrue(all(g['country']=='Kenya' for g in data['swap_review']['by_country']))
        self.assertEqual(data['swap_review']['open_count'] + data['swap_review']['resolved_paid_due_count']
                         + data['swap_review']['resolved_offer_count'],
                         data['swap_review']['total_count'])
        first=data['page'];self.assertEqual(len(first['records']),2);self.assertTrue(first['has_more'])
        status,_,body=self.request('/api/records?country=Kenya&review_only=false&limit=2&cursor='+first['next_cursor'])
        self.assertEqual(status,200)
        second=json.loads(body)['page']
        self.assertFalse({r['wallet_record_id'] for r in first['records']}&{r['wallet_record_id'] for r in second['records']})

    def test_invalid_filters_and_unknown_parameters(self):
        for query in ('country=Uganda','limit=1001','review_only=1','start=2026-99-99','country=Kenya&country=Rwanda','password=x'):
            with self.subTest(query=query):self.assertEqual(self.request('/api/dashboard?'+query)[0],400)

    def test_loaded_date_route_matches_the_selected_source(self):
        status, _, body = self.request('/api/filter-dates?view=overview&country=Kenya')
        self.assertEqual(status, 200)
        dates = json.loads(body)
        self.assertEqual(dates['dates'], ['2026-09-23', '2026-09-24', '2026-09-25'])
        self.assertEqual(dates['first_date'], '2026-09-23')
        self.assertEqual(dates['last_date'], '2026-09-25')
        status, _, body = self.request('/api/filter-dates?view=counterparts&country=Kenya&group=open_swaps')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['dates'], ['2026-09-23', '2026-09-24'])
        self.assertEqual(json.loads(self.request('/api/filter-dates?view=due')[2])['dates'], [])
        for query in ('view=home', 'country=Uganda', 'group=open_swaps', 'view=due&start=2026-09-23',
                      'view=counterparts&group=unknown', 'country=Kenya&country=Rwanda'):
            with self.subTest(query=query):
                self.assertEqual(self.request('/api/filter-dates?' + query)[0], 400)

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

    def test_due_swap_routes_and_invalid_filters(self):
        status, _, body = self.request('/api/due-swaps?country=Kenya&limit=2')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['summary']['matching_key'], 'country + Swapping Transaction.id')
        self.assertEqual(data['summary']['repayment_status_source'], 'exported_due_status')
        self.assertIn('records', data['page'])
        status, headers, body = self.request('/api/due-swaps/export?country=Rwanda')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertIn('repayment_status', body.decode('utf-8-sig'))
        for query in ('country=Uganda', 'status=matched', 'start=bad', 'limit=101', 'country=Kenya&country=Rwanda', 'group=unknown'):
            with self.subTest(query=query):
                self.assertEqual(self.request('/api/due-swaps?' + query)[0], 400)

    def test_offer_swap_routes_export_and_invalid_filters(self):
        status, _, body = self.request('/api/offer-swaps?country=Kenya&group=wallet_attached&limit=1')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['summary']['wallet_attached_count'], 1)
        self.assertEqual(data['summary']['resolved_count'], 0)
        self.assertEqual(data['page']['records'][0]['resolution_status'], 'wallet_attached')
        status, headers, body = self.request('/api/offer-swaps/export?country=Kenya&group=all')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        rows = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['offer_result'], 'no_consumption')
        for query in ('country=Uganda','status=matched','start=bad','limit=101','group=unknown','group=all&group=resolved'):
            with self.subTest(query=query):
                self.assertEqual(self.request('/api/offer-swaps?' + query)[0], 400)


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class LoadedDateTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'])
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.conn.execute('DELETE FROM reconciliation.wallet_transactions')
        self.conn.execute('DELETE FROM reconciliation.swap_transactions')

    def wallet(self, record_id, created, country='Kenya', kind='Debit', status='Committed', settled='Swap'):
        self.conn.execute('''INSERT INTO reconciliation.wallet_transactions
            (zoho_record_id,country,created_on,transaction_type,status,settled_against,
             source_file,source_row_number,raw_record)
            VALUES (%s,%s,%s,%s,%s,%s,'date-fixture',1,'{}')''',
            (record_id,country,created,kind,status,settled))

    def swap(self, record_id, created, method, country='Kenya'):
        self.conn.execute('''INSERT INTO reconciliation.swap_transactions
            (zoho_record_id,country,created_on,payment_method,source_file,source_row_number,raw_record)
            VALUES (%s,%s,%s,%s,'date-fixture',1,'{}')''', (record_id,country,created,method))

    def test_country_gaps_duplicates_nulls_and_non_swap_debits(self):
        self.wallet('first', '2033-01-01 00:00:00')
        self.wallet('last', '2033-01-03 23:59:59.999999')
        self.wallet('same-day', '2033-01-03 00:00:00')
        self.wallet('no-date', None)
        self.wallet('credit', '2033-01-02', kind='Credit')
        self.wallet('pending', '2033-01-02', status='Pending')
        self.wallet('rental', '2033-01-02', settled='Rental')
        self.wallet('rwanda', '2033-01-04', country='Rwanda')
        self.assertEqual(filter_dates(self.conn, country='Kenya')['dates'], ['2033-01-01','2033-01-03'])
        self.assertEqual(filter_dates(self.conn, country='Rwanda')['dates'], ['2033-01-04'])
        self.assertEqual(filter_dates(self.conn)['dates'], ['2033-01-01','2033-01-03','2033-01-04'])
        self.conn.execute("UPDATE reconciliation.wallet_transactions SET created_on='2033-01-05' WHERE zoho_record_id='last'")
        self.assertEqual(filter_dates(self.conn, country='Kenya')['dates'], ['2033-01-01','2033-01-03','2033-01-05'])

    def test_module_and_counterpart_source_dates_and_empty_scope(self):
        self.wallet('wallet-day', '2033-02-01')
        self.swap('due-day', '2033-02-03', 'DUE_CREATED')
        self.swap('offer-day', '2033-02-05', 'OFFER_APPLIED')
        self.swap('wallet-swap-day', '2033-02-07', 'WALLET')
        self.swap('rwanda-due', '2033-02-09', 'DUE_CREATED', country='Rwanda')
        self.swap('undated-offer', None, 'OFFER_APPLIED')
        self.assertEqual(filter_dates(self.conn, view='due', country='Kenya')['dates'], ['2033-02-03'])
        self.assertEqual(filter_dates(self.conn, view='offer', country='Kenya')['dates'], ['2033-02-05'])
        self.assertEqual(filter_dates(self.conn, view='due')['dates'], ['2033-02-03','2033-02-09'])
        for group in ('wallet_without_swap', 'unmatchable_wallets'):
            self.assertEqual(filter_dates(self.conn, view='counterparts', group=group)['dates'], ['2033-02-01'])
        for group in ('open_swaps', 'swap_without_wallet', 'unmatchable_swaps'):
            self.assertEqual(filter_dates(self.conn, view='counterparts', country='Kenya', group=group)['dates'],
                             ['2033-02-03','2033-02-05','2033-02-07'])
        empty = filter_dates(self.conn, view='offer', country='Rwanda')
        self.assertEqual(empty['dates'], [])
        self.assertIsNone(empty['first_date'])
        self.assertIsNone(empty['last_date'])


if __name__=='__main__':unittest.main()
