"""Due swaps include blank IDs without claiming debt or repayment verification."""
import csv
import io
import os
import unittest
from unittest.mock import patch

import psycopg
from app import due_tracking, counterpart_tracking
from app.dashboard_cache import cache_ready, refresh_cache
from app.reconciliation_backend import ReconciliationFilter


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class DueTrackingTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'])
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        # Same lookup across countries, duplicate wallet snapshots in Kenya,
        # blank Transaction IDs, a failed swap, and the inclusive date boundary.
        self.conn.execute("""INSERT INTO reconciliation.wallets
            (zoho_record_id,country,currency,customer_crm_id,customer_business_id,source_file,source_row_number,raw_record)
            VALUES ('due_wallet_1','Kenya','KES','due_customer','KE-DUE','fixture',1,'{}'),
                   ('due_wallet_2','Kenya','KES','due_customer','KE-DUE','fixture',2,'{}'),
                   ('due_wallet_3','Rwanda','RWF','due_customer','RW-DUE','fixture',3,'{}')""")
        self.conn.execute("""INSERT INTO reconciliation.swap_transactions
            (zoho_record_id,country,customer_id,customer_name,swap_reference,transaction_id,payment_method,status,
             swap_amount,created_on,source_file,source_row_number,raw_record)
            VALUES ('due_a','Kenya','due_customer','=unsafe','KE-SWAP',NULL,'DUE_CREATED','SUCCESS',120.10,'2032-01-01 00:00:00','fixture',1,'{}'),
                   ('due_b','Kenya','due_customer','Customer','KE-SWAP-2','','DUE_CREATED','FAILED',5.20,'2032-01-01 23:59:59.999999','fixture',2,'{}'),
                   ('due_c','Rwanda','due_customer','Customer','RW-SWAP',NULL,'DUE_CREATED','SUCCESS',1200.01,'2032-01-02','fixture',3,'{}'),
                   ('due_d','Kenya','unmapped','Customer','KE-SWAP-3',NULL,'DUE_CREATED','SUCCESS',NULL,'2032-01-02','fixture',4,'{}'),
                   ('due_not_wallet','Kenya','due_customer','Customer','KE-WALLET',NULL,'WALLET','SUCCESS',100,'2032-01-01','fixture',5,'{}'),
                   ('due_not_offer','Kenya','due_customer','Customer','KE-OFFER',NULL,'OFFER_APPLIED','SUCCESS',0,'2032-01-01','fixture',6,'{}')""")
        self.filters = ReconciliationFilter(start_date='2032-01-01')

    def test_blank_ids_and_duplicate_wallets_do_not_exclude_or_multiply_swaps(self):
        result = due_tracking.summary(self.conn, self.filters)
        self.assertEqual(result['swap_count'], 4)
        self.assertEqual(result['successful_count'], 3)
        self.assertEqual(result['awaiting_due_export_count'], 4)
        self.assertEqual(result['needs_review_count'], 4)
        self.assertEqual(result['matched_count'], 0)
        groups = {(g['country'], g['currency']): g for g in result['groups']}
        self.assertEqual(groups[('Kenya', 'KES')]['swap_amount'], '125.30')
        self.assertEqual(groups[('Kenya', 'KES')]['swap_count'], 2)
        self.assertEqual(groups[('Rwanda', 'RWF')]['swap_amount'], '1200.01')
        self.assertEqual(groups[('Kenya', None)]['missing_amount_count'], 1)
        self.assertIsNone(groups[('Kenya', None)]['swap_amount'])

    def test_country_date_filters_and_cursor_keep_exact_records(self):
        filters = ReconciliationFilter(country='Kenya', start_date='2032-01-01', end_date='2032-01-01')
        first = due_tracking.page(self.conn, filters, limit=1)
        self.assertEqual([r['record_id'] for r in first['records']], ['due_a'])
        self.assertTrue(first['has_more'])
        second = due_tracking.page(self.conn, filters, limit=1, cursor=first['next_cursor'])
        self.assertEqual([r['record_id'] for r in second['records']], ['due_b'])
        self.assertFalse(second['has_more'])
        self.assertNotIn('raw_record', second['records'][0])
        self.assertEqual(due_tracking.summary(self.conn, filters)['swap_count'], 2)
        self.assertEqual(due_tracking.page(self.conn, ReconciliationFilter(start_date='2033-01-01'))['records'], [])

    def test_ambiguous_wallet_currency_is_not_assigned_or_double_counted(self):
        self.conn.execute("UPDATE reconciliation.wallets SET currency='USD' WHERE zoho_record_id='due_wallet_2'")
        row = due_tracking.page(self.conn, self.filters)['records'][0]
        self.assertIsNone(row['currency'])
        self.assertEqual(row['currency_source'], 'ambiguous_wallet_snapshot')
        self.assertEqual(due_tracking.summary(self.conn, self.filters)['swap_count'], 4)

    def test_export_keeps_scope_amount_precision_and_unknown_repayment(self):
        body = due_tracking.export(self.conn, ReconciliationFilter(country='Kenya', start_date='2032-01-01', end_date='2032-01-01'))
        rows = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual([r['record_id'] for r in rows], ['due_a', 'due_b'])
        self.assertEqual(rows[0]['swap_amount'], '120.10')
        self.assertEqual(rows[0]['customer_name'], "'=unsafe")
        self.assertTrue(all(r['repayment_status']=='not_verified' for r in rows))
        self.conn.execute("UPDATE reconciliation.swap_transactions SET swap_amount=-5.20 WHERE zoho_record_id='due_b'")
        signed = list(csv.DictReader(io.StringIO(due_tracking.export(self.conn, self.filters).decode('utf-8-sig'))))
        self.assertEqual(signed[1]['swap_amount'], '-5.20')
        with patch.object(due_tracking, 'MAX_EXPORT', 1):
            with self.assertRaises(ValueError):
                due_tracking.export(self.conn, self.filters)

    def test_invalid_page_or_wallet_result_filter_is_rejected(self):
        for options in ({'limit':0}, {'limit':True}, {'cursor':' '}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                due_tracking.page(self.conn, self.filters, **options)
        with self.assertRaises(ValueError):
            due_tracking.summary(self.conn, ReconciliationFilter(status='matched'))
        with self.assertRaises(ValueError):
            due_tracking.page(self.conn, self.filters, group='unknown')

    def due(self, record, *, swap='due_a', country='Kenya', customer='due_customer',
            amount='120.10', currency='KES', status='Paid', transaction_id='settlement-id'):
        self.conn.execute('''INSERT INTO reconciliation.dues
            (zoho_record_id,due_reference,swap_record_id,country,customer_id,due_amount,currency,status,
             transaction_id,due_creation_date,due_settlement_date,created_on,source_file,source_row_number,raw_record)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'2032-01-01','2032-01-03','2035-01-01','fixture',1,'{}')''',
            (record,'DUE-'+record,swap,country,customer,amount,currency,status,transaction_id))

    def test_exact_lookup_matches_across_due_dates_and_preserves_paid_amount(self):
        self.due('linked')
        self.due('settlement_is_not_swap_key', swap=None, transaction_id='due_b', amount='5.20')
        self.due('other_country', swap='due_b', country='Rwanda', amount='5.20', currency='RWF')
        result = due_tracking.summary(self.conn, self.filters)
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['paid_count'], 1)
        self.assertEqual(result['needs_review_count'], 3)
        records = {r['record_id']:r for r in due_tracking.page(self.conn, self.filters)['records']}
        self.assertEqual(records['due_a']['due_record_status'], 'matched')
        self.assertEqual(records['due_a']['due_amount'], '120.10')
        self.assertEqual(records['due_a']['repayment_status'], 'Paid')
        self.assertEqual(records['due_a']['resolution_status'], 'resolved_paid_due')
        self.assertEqual(result['resolved_count'], 1)
        self.assertEqual(records['due_b']['due_record_status'], 'no_due_in_loaded_data')
        self.assertEqual(records['due_b']['dues'], [])
        # Loading another country's unrelated Due must not create a cross-country match.
        self.assertEqual(records['due_c']['due_record_status'], 'no_due_in_loaded_data')

    def test_duplicate_dues_are_reviewed_without_picking_a_paid_record(self):
        self.due('one', status='Paid')
        self.due('two', status='Pending')
        row = due_tracking.page(self.conn, self.filters)['records'][0]
        self.assertEqual(row['due_count'], 2)
        self.assertEqual(row['due_record_status'], 'multiple_due_records')
        self.assertIsNone(row['due_status'])
        self.assertIsNone(row['due_amount'])
        self.assertEqual(row['repayment_status'], 'ambiguous')
        self.assertEqual(len(row['dues']), 2)
        result = due_tracking.summary(self.conn, self.filters)
        self.assertEqual(result['swap_count'], 4)
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['paid_count'], 0)
        self.assertEqual(result['pending_count'], 0)
        self.assertEqual(result['resolved_count'], 0)

    def test_discrepancies_and_missing_amount_do_not_clear_review(self):
        cases = [('customer_mismatch', {'customer':'different'}),
                 ('amount_mismatch', {'amount':'120.11'}),
                 ('missing_amount', {'amount':None}),
                 ('currency_mismatch', {'currency':'USD'}),
                 ('missing_due_currency', {'currency':None})]
        for expected, options in cases:
            with self.subTest(expected=expected):
                self.conn.execute("DELETE FROM reconciliation.dues WHERE zoho_record_id='case'")
                self.due('case', **options)
                row = due_tracking.page(self.conn, self.filters)['records'][0]
                self.assertEqual(row['due_record_status'], expected)
                self.assertEqual(due_tracking.summary(self.conn, self.filters)['matched_count'], 0)
                self.assertEqual(due_tracking.summary(self.conn, self.filters)['resolved_count'], 0)

    def test_country_without_export_stays_pending_and_group_export_obeys_selection(self):
        self.due('paid')
        self.due('pending', swap='due_b', amount='5.20', status='Pending')
        result = due_tracking.summary(self.conn, self.filters)
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['needs_review_count'], 3)
        self.assertEqual(result['awaiting_due_export_count'], 1)
        self.assertEqual(result['pending_count'], 1)
        rows = due_tracking.page(self.conn, self.filters, group='awaiting_due_export')['records']
        self.assertEqual([r['record_id'] for r in rows], ['due_c'])
        export = list(csv.DictReader(io.StringIO(due_tracking.export(self.conn, self.filters, group='pending').decode('utf-8-sig'))))
        self.assertEqual([r['record_id'] for r in export], ['due_b'])
        self.assertEqual(export[0]['due_amount'], '5.20')
        # A fresh read reflects a later source status update without stale Due caches.
        self.conn.execute("UPDATE reconciliation.dues SET status='Closed' WHERE zoho_record_id='pending'")
        result = due_tracking.summary(self.conn, self.filters)
        self.assertEqual(result['pending_count'], 0)
        self.assertEqual(result['closed_count'], 1)
        self.assertEqual(result['paid_count'], 1)

    def test_only_paid_clears_a_matching_due_and_unpaid_status_reopens_review(self):
        self.due('status_cycle', status='Pending')
        filters = ReconciliationFilter(country='Kenya', start_date='2032-01-01', end_date='2032-01-02')
        for status, cleared, expected_resolution in (
            ('Pending', 0, 'pending_due'), ('Paid', 1, 'resolved_paid_due'),
            ('Closed', 0, 'closed_due_not_paid'), ('Pending', 0, 'pending_due'),
        ):
            with self.subTest(status=status):
                self.conn.execute("UPDATE reconciliation.dues SET status=%s WHERE zoho_record_id='status_cycle'", (status,))
                result = due_tracking.summary(self.conn, filters)
                self.assertEqual(result['matched_count'], 1)
                self.assertEqual(result['resolved_count'], cleared)
                self.assertEqual(result['needs_review_count'], 3-cleared)
                row = due_tracking.page(self.conn, filters)['records'][0]
                self.assertEqual(row['resolution_status'], expected_resolution)
                resolved = due_tracking.page(self.conn, filters, group='resolved')['records']
                self.assertEqual([r['record_id'] for r in resolved], ['due_a'] if cleared else [])

    def test_paid_resolution_changes_cached_and_live_counterpart_lists_and_exports(self):
        self.due('cache_cycle', status='Pending')
        filters = ReconciliationFilter(country='Kenya', start_date='2032-01-01', end_date='2032-01-02')
        refresh_cache(self.conn)
        self.assertTrue(cache_ready(self.conn))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM reconciliation.dashboard_swap_gaps WHERE swap_record_id='due_a'").fetchone()[0], 1)
        for status, cleared in (('Pending', 0), ('Paid', 1), ('Closed', 0)):
            self.conn.execute("UPDATE reconciliation.dues SET status=%s WHERE zoho_record_id='cache_cycle'", (status,))
            self.assertTrue(cache_ready(self.conn))
            for cached in (False, True):
                with self.subTest(status=status, cached=cached):
                    result = counterpart_tracking.summary(self.conn, filters, cached=cached)
                    self.assertEqual(result['groups']['open_swaps']['count'], 5-cleared)
                    self.assertEqual(result['groups']['unmatchable_swaps']['count'], 5-cleared)
                    self.assertEqual(result['swap_review']['resolved_paid_due_count'], cleared)
                    self.assertEqual(result['swap_review']['open_count'], 5-cleared)
                    first = counterpart_tracking.page(self.conn, 'open_swaps', filters, limit=2, cached=cached)
                    ids = [r['record_id'] for r in first['records']]
                    while first['has_more']:
                        first = counterpart_tracking.page(self.conn, 'open_swaps', filters, limit=2,
                            cursor=first['next_cursor'], cached=cached)
                        ids.extend(r['record_id'] for r in first['records'])
                    self.assertEqual(len(ids), 5-cleared)
                    self.assertEqual(len(set(ids)), len(ids))
                    self.assertEqual('due_a' in ids, not cleared)
                    exported = list(csv.DictReader(io.StringIO(counterpart_tracking.export(
                        self.conn, 'open_swaps', filters, cached=cached).decode('utf-8-sig'))))
                    self.assertEqual([r['swap_record_id'] for r in exported], ids)

    def test_paid_resolution_never_double_counts_a_swap_with_an_attached_wallet_debit(self):
        self.due('already_wallet_paid')
        self.conn.execute("UPDATE reconciliation.swap_transactions SET transaction_id='due_wallet_tx' WHERE zoho_record_id='due_a'")
        self.conn.execute('''INSERT INTO reconciliation.wallet_transactions
            (zoho_record_id,country,transaction_id,transaction_type,status,settled_against,amount,
             created_on,source_file,source_row_number,raw_record)
            VALUES ('due_attached_debit','Kenya','due_wallet_tx','Debit','Committed','Swap',120.10,
                    '2035-01-01','fixture',1,'{}')''')
        result = due_tracking.summary(self.conn, self.filters)
        self.assertEqual(result['wallet_attached_count'], 1)
        self.assertEqual(result['resolved_count'], 0)
        row = due_tracking.page(self.conn, self.filters)['records'][0]
        self.assertEqual(row['resolution_status'], 'wallet_attached')
        self.assertEqual(row['wallet_debit_count'], 1)

    def test_ambiguous_currency_and_failed_swap_do_not_clear_paid_dues(self):
        self.due('failed_paid', swap='due_b', amount='5.20')
        self.due('ambiguous_paid')
        self.conn.execute('''INSERT INTO reconciliation.wallets
            (zoho_record_id,country,currency,customer_crm_id,source_file,source_row_number,raw_record)
            VALUES ('due_usd_wallet','Kenya','USD','due_customer','fixture',1,'{}')''')
        result = due_tracking.summary(self.conn, self.filters)
        self.assertEqual(result['resolved_count'], 0)
        self.assertEqual(result['needs_review_count'], 4)
        records = {r['record_id']:r for r in due_tracking.page(self.conn, self.filters)['records']}
        self.assertEqual(records['due_a']['due_record_status'], 'ambiguous_swap_currency')
        self.assertEqual(records['due_b']['due_record_status'], 'swap_not_successful')

    def test_unlinked_source_statuses_are_visible_without_claiming_swap_matches(self):
        self.due('rw_unlinked_paid', swap=None, country='Rwanda', currency='RWF', status='Paid')
        self.due('rw_unlinked_pending', swap=None, country='Rwanda', currency='RWF', status='Pending')
        self.due('ke_linked_paid')
        self.conn.execute('''INSERT INTO reconciliation.import_batches
            (dataset,source_file,sha256,rows_read,rows_applied)
            VALUES ('due','fixture','test',200000,2), ('due','fixture','test',200000,0)''')
        result = due_tracking.summary(self.conn, ReconciliationFilter(country='Rwanda', start_date='2032-01-01', end_date='2032-01-02'))
        self.assertEqual(result['matched_count'], 0)
        self.assertEqual(result['pending_count'], 0)
        self.assertEqual(result['awaiting_due_export_count'], 0)
        self.assertEqual(result['needs_review_count'], 1)
        self.assertEqual(result['due_sources'], [{'country':'Rwanda', 'record_count':2,
            'swap_lookup_count':0, 'missing_swap_lookup_count':2, 'paid_count':1,
            'pending_count':1, 'closed_count':0, 'export_limit_reached':True}])

    def test_source_coverage_counts_keep_country_and_all_due_dates(self):
        self.due('ke_one')
        self.due('ke_two', swap=None, status='Closed')
        self.due('rw_one', swap=None, country='Rwanda', currency='RWF', status='Pending')
        result = due_tracking.summary(self.conn, ReconciliationFilter(country='Kenya', start_date='2032-01-01', end_date='2032-01-01'))
        self.assertEqual(result['due_sources'], [{'country':'Kenya', 'record_count':2,
            'swap_lookup_count':1, 'missing_swap_lookup_count':1, 'paid_count':1,
            'pending_count':0, 'closed_count':1, 'export_limit_reached':False}])
