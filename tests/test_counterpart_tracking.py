"""Tracking integration uses an explicit disposable database, never project .env."""
import csv
import io
import json
import os
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import psycopg
from psycopg.conninfo import conninfo_to_dict
from app.dashboard import DashboardServer


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class CounterpartTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'], autocommit=True)
        with cls.conn.transaction():
            for record, tx, country, kind, created in [
                ('a', 'ct-shared', 'Kenya', 'Debit', '2031-01-01'),
                ('b', 'ct-dupe', 'Kenya', 'Debit', '2031-01-01'),
                ('c', 'ct-dupe', 'Kenya', 'Debit', '2031-01-01'),
                ('null', None, 'Kenya', 'Debit', '2031-01-01'),
                ('blank', '  ', 'Kenya', 'Debit', '2031-01-01'),
                ('country', 'ct-country', None, 'Debit', '2031-01-01'),
                ('failed', 'ct-failed', 'Kenya', 'Debit', '2031-01-01'),
                ('date-wallet', 'ct-date-wallet', 'Kenya', 'Debit', '2031-01-01'),
                ('date-swap', 'ct-date-swap', 'Kenya', 'Debit', '2030-12-31'),
                ('credit', 'ct-credit', 'Kenya', 'Credit', '2031-01-01'),
            ]:
                cls.conn.execute('''INSERT INTO reconciliation.wallet_transactions
                    (zoho_record_id,transaction_id,country,wallet_id,transaction_type,status,
                     settled_against,amount,created_on,source_file,source_row_number,raw_record)
                    VALUES (%s,%s,%s,'wallet_ke',%s,'Committed','Swap',120.10,%s,'=unsafe.csv',2,'{}')''',
                    ('ct-'+record, tx, country, kind, created))
            for record, tx, country, status, method, created in [
                ('rw', 'ct-shared', 'Rwanda', 'SUCCESS', 'WALLET', '2031-01-01'),
                ('credit', 'ct-credit', 'Kenya', 'SUCCESS', 'WALLET', '2031-01-01'),
                ('offer', 'ct-offer', 'Kenya', 'SUCCESS', 'OFFER_APPLIED', '2031-01-01'),
                ('null', None, 'Kenya', 'SUCCESS', 'DUE_CREATED', '2031-01-01'),
                ('blank', '  ', 'Kenya', 'SUCCESS', None, '2031-01-01'),
                ('country', 'ct-country', None, 'SUCCESS', 'WALLET', '2031-01-01'),
                ('failed', 'ct-failed', 'Kenya', 'FAILED', 'WALLET', '2031-01-01'),
                ('date-wallet', 'ct-date-wallet', 'Kenya', 'SUCCESS', 'WALLET', '2030-12-31'),
                ('date-swap', 'ct-date-swap', 'Kenya', 'SUCCESS', 'WALLET', '2031-01-01'),
            ]:
                cls.conn.execute('''INSERT INTO reconciliation.swap_transactions
                    (zoho_record_id,transaction_id,country,customer_id,status,payment_method,
                     swap_amount,created_on,source_file,source_row_number,raw_record)
                    VALUES (%s,%s,%s,'customer_ke',%s,%s,120.10,%s,'=unsafe.csv',3,'{}')''',
                    ('ct-'+record, tx, country, status, method, created))
        cls.server=DashboardServer(('127.0.0.1',0),conninfo_to_dict(os.environ['SPIRO_TEST_DSN']))
        cls.port=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
        with cls.conn.transaction():
            for table in ('wallet_transactions','swap_transactions'):
                cls.conn.execute(psycopg.sql.SQL('DELETE FROM {} WHERE zoho_record_id LIKE %s').format(
                    psycopg.sql.Identifier('reconciliation',table)), ('ct-%',))
        cls.conn.close()

    def request(self, params=None, *, export=False):
        query={'start':'2031-01-01','end':'2031-01-01',**(params or {})}
        path='/api/counterparts'+('/export' if export else '')+'?'+urlencode(query)
        try:
            with urlopen(f'http://127.0.0.1:{self.port}'+path,timeout=20) as response:
                return response.status,response.headers,response.read()
        except HTTPError as response:
            return response.code,response.headers,response.read()

    def data(self, **params):
        status,_,body=self.request(params)
        self.assertEqual(status,200,body)
        return json.loads(body)

    def test_four_groups_separate_missing_and_unmatchable(self):
        data=self.data()
        groups=data['summary']['groups']
        self.assertEqual({key:value['count'] for key,value in groups.items()},
                         dict(open_swaps=6,wallet_without_swap=3,swap_without_wallet=3,unmatchable_swaps=3,unmatchable_wallets=3))
        self.assertEqual(groups['swap_without_wallet']['by_country'],{'Kenya':2,'Rwanda':1})
        self.assertEqual(groups['unmatchable_swaps']['by_country'],{'Kenya':2,'Unknown':1})
        self.assertEqual({r['record_id'] for r in data['page']['records']},{'ct-a','ct-b','ct-c'})

    def test_country_and_own_dates_keep_counterparts_outside_range(self):
        data=self.data(country='Kenya',group='swap_without_wallet')
        self.assertTrue(all(r['country']=='Kenya' for r in data['page']['records']))
        self.assertEqual(data['summary']['groups']['swap_without_wallet']['count'],2)
        self.assertEqual({r['record_id'] for r in data['page']['records']},{'ct-credit','ct-offer'})
        self.assertEqual({r['record_id'] for r in self.data(country='Kenya')['page']['records']},{'ct-a','ct-b','ct-c'})

    def test_cursor_pagination_keeps_duplicate_references_as_source_rows(self):
        first=self.data(limit=2)['page']
        self.assertTrue(first['has_more'])
        second=self.data(limit=2,cursor=first['next_cursor'])['page']
        ids=[r['record_id'] for r in first['records']+second['records']]
        self.assertEqual(ids,['ct-a','ct-b','ct-c'])
        self.assertFalse(second['has_more'])
        self.assertEqual(first['records'][0]['wallet_amount'],'120.10')

    def test_exports_obey_group_and_filters_and_preserve_decimal_money(self):
        for group in ('wallet_without_swap','swap_without_wallet','unmatchable_swaps','unmatchable_wallets'):
            with self.subTest(group=group):
                status,headers,body=self.request({'group':group,'country':'Kenya'},export=True)
                self.assertEqual(status,200)
                self.assertIn(group+'_tracking.csv',headers['Content-Disposition'])
                rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
                self.assertEqual(len(rows),self.data(group=group,country='Kenya')['summary']['groups'][group]['count'])
                self.assertTrue(all(r['country']=='Kenya' for r in rows))
                for r in rows:
                    self.assertEqual(r.get('wallet_amount',r.get('swap_amount')),'120.10')
                    self.assertEqual(r['source_file'],"'=unsafe.csv")
                    self.assertEqual(r['source_coverage_status'],'not_verified')
                    self.assertEqual(r['missing_counterpart_reason']=='missing_transaction_id',group.startswith('unmatchable'))
                    self.assertNotIn('raw_record',r)
                    self.assertNotIn('pin',r)

    def test_fresh_query_removes_a_record_after_counterpart_is_added(self):
        before=self.data()['summary']['groups']['wallet_without_swap']['count']
        self.conn.execute('''INSERT INTO reconciliation.swap_transactions
            (zoho_record_id,transaction_id,country,status,payment_method,swap_amount,created_on,
             source_file,source_row_number,raw_record)
            VALUES ('ct-new-counterpart','ct-shared','Kenya','SUCCESS','WALLET',120.10,
                    '2030-01-01','fixture.csv',1,'{}')''')
        try:
            after=self.data()
            self.assertEqual(after['summary']['groups']['wallet_without_swap']['count'],before-1)
            self.assertNotIn('ct-a',{r['record_id'] for r in after['page']['records']})
        finally:
            self.conn.execute("DELETE FROM reconciliation.swap_transactions WHERE zoho_record_id='ct-new-counterpart'")

    def test_empty_dates_have_zero_counts_and_csv_headers(self):
        params={'start':'2040-01-01','end':'2040-01-02'}
        before=self.conn.execute('SELECT count(*) FROM reconciliation.import_batches').fetchone()[0]
        data=self.data(**params)
        self.assertTrue(all(group['count']==0 for group in data['summary']['groups'].values()))
        self.assertEqual(data['page']['records'],[])
        status,_,body=self.request(params,export=True)
        self.assertEqual(status,200)
        reader=csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
        self.assertIn('wallet_record_id',reader.fieldnames)
        self.assertEqual(list(reader),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM reconciliation.import_batches').fetchone()[0],before)

    def test_rejects_unknown_groups_status_and_invalid_filters(self):
        for params in ({'group':'unknown'},{'status':'matched'},{'country':'Uganda'},
                       {'limit':'101'},{'start':'bad-date'},{'start':'2032-01-01'},
                       {'password':'x'}):
            with self.subTest(params=params):self.assertEqual(self.request(params)[0],400)

    def test_export_limit_returns_an_actionable_error(self):
        with patch('app.counterpart_tracking.MAX_EXPORT',1):
            status,_,body=self.request(export=True)
        self.assertEqual(status,400)
        self.assertIn('Narrow',json.loads(body)['error'])


if __name__=='__main__':unittest.main()
