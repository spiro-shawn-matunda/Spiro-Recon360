"""Integration tests use an explicit isolated PostgreSQL connection, never .env.

Run through the workspace validation harness or set SPIRO_TEST_DSN to a disposable
test database with the project's SQL schemas and fixtures supplied by the harness.
"""
import csv
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from app.reconciliation_backend import ReconciliationBackend, ReconciliationFilter
from app.reconcile import write_report


class FilterValidationTests(unittest.TestCase):
    def test_rejects_invalid_filters(self):
        for args in ({'country': "Kenya' OR true --"}, {'status':'unknown'},
                     {'start_date':'2026-02-30'}, {'start_date':'2026-09-26', 'end_date':'2026-09-25'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ReconciliationFilter(**args)


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class BackendIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'], autocommit=True)
        cls.backend = ReconciliationBackend(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_statuses_and_review_reasons(self):
        cases={
            'matched':('matched', False, None),
            'mismatch':('amount_mismatch', True, 'amount_mismatch'),
            'failed':('no_successful_swap_in_loaded_data', True, 'no_successful_swap_in_loaded_data'),
            'missing_tx':('missing_wallet_transaction_id', True, 'missing_wallet_transaction_id'),
            'duplicate_wallet_a':('ambiguous_transaction_id', True, 'ambiguous_transaction_id'),
            'duplicate_success':('ambiguous_transaction_id', True, 'ambiguous_transaction_id'),
            'missing_amount':('missing_amount', True, 'missing_amount'),
            'offer':('matched', True, 'payment_method_needs_review'),
            'customer_diff':('matched', True, 'customer_lookup_differs'),
            'unmapped':('matched', True, 'wallet_not_loaded'),
            'missing_customer':('matched', True, 'customer_lookup_unavailable'),
            'missing_timestamp':('matched', True, 'timestamp_unavailable'),
            'after_cutoff':('no_swap_in_loaded_data', True, 'no_swap_in_loaded_data'),
        }
        for record_id,(status,needs_review,reason) in cases.items():
            with self.subTest(record_id=record_id):
                record=self.backend.get_record(record_id)
                self.assertEqual(record['reconciliation_status'],status)
                self.assertEqual(record['needs_review'],needs_review)
                if reason:self.assertIn(reason,record['review_reasons'])
                self.assertEqual(record['source_coverage_status'],'not_verified')

    def test_country_separation_and_decimal_money(self):
        for record_id,country,currency in [('shared_ke','Kenya','KES'),('shared_rw','Rwanda','RWF')]:
            row=self.backend.get_record(record_id,country=country)
            self.assertEqual(row['reconciliation_status'],'matched')
            self.assertEqual(row['wallet_currency'],currency)
            self.assertEqual(row['wallet_amount'],'120.10')
        self.assertIsNone(self.backend.get_record('shared_ke',country='Rwanda'))
        self.assertIsNone(self.backend.get_record("x' OR true --"))

    def test_date_filter_does_not_hide_duplicate_outside_selected_day(self):
        selected=self.backend.list_records(ReconciliationFilter(country='Kenya',start_date='2026-09-23',end_date='2026-09-23'),limit=100)
        records={r['wallet_record_id']:r for r in selected['records']}
        self.assertIn('duplicate_wallet_a',records)
        self.assertNotIn('duplicate_wallet_b',records)
        self.assertEqual(records['duplicate_wallet_a']['reconciliation_status'],'ambiguous_transaction_id')
        self.assertIn('end_of_day',records)
        self.assertNotIn('next_day',records)

    def test_country_specific_observed_bounds(self):
        self.assertEqual(self.backend.get_record('after_cutoff')['coverage_assessment'],'after_observed_swap_cutoff')
        self.assertEqual(self.backend.get_record('no_swap_country',country='Rwanda')['coverage_assessment'],'no_swap_data_loaded')

    def test_non_swap_debits_are_excluded(self):
        for record_id in ('credit_excluded','rental_excluded','uncommitted_excluded'):
            with self.subTest(record_id=record_id):
                self.assertIsNone(self.backend.get_record(record_id))

    def test_cursor_pagination_has_no_missing_or_repeated_records(self):
        cursor=None;ids=[]
        while True:
            page=self.backend.list_records(limit=3,after_record_id=cursor)
            ids.extend(row['wallet_record_id'] for row in page['records'])
            if not page['has_more']:break
            self.assertIsNotNone(page['next_cursor'])
            cursor=page['next_cursor']
        expected=[r['wallet_record_id'] for r in self.backend.list_records(limit=100)['records']]
        self.assertEqual(ids,expected)
        self.assertEqual(len(ids),len(set(ids)))
        with self.assertRaises(ValueError):self.backend.list_records(limit=0)
        with self.assertRaises(ValueError):self.backend.list_records(limit=True)

    def test_export_counts_reasons_and_empty_filters(self):
        with TemporaryDirectory() as directory:
            summary,summary_path,csv_path=write_report(self.conn,ReconciliationFilter(),directory)
            saved=json.loads(summary_path.read_text(encoding='utf-8'))
            with csv_path.open(encoding='utf-8-sig',newline='') as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),summary['needs_review_count'])
            self.assertEqual(saved['candidate_count'],len(rows))
            self.assertTrue(all(json.loads(row['review_reasons']) for row in rows))
            self.assertNotIn('matched',{row['wallet_record_id'] for row in rows})
            before=self.conn.execute('SELECT count(*),sum(amount) FROM reconciliation.wallet_transactions').fetchone()
            empty,_,path=write_report(self.conn,ReconciliationFilter(start_date='2030-01-01'),Path(directory)/'empty')
            self.assertEqual(empty['deduction_count'],0)
            self.assertEqual(empty['candidate_count'],0)
            with path.open(encoding='utf-8-sig',newline='') as f:self.assertEqual(list(csv.DictReader(f)),[])
            self.assertEqual(before,self.conn.execute('SELECT count(*),sum(amount) FROM reconciliation.wallet_transactions').fetchone())


if __name__=='__main__':unittest.main()
