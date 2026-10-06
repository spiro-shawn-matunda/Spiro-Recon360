"""Derived results must stay equivalent to live queries and invalidate safely."""
import os
import unittest

import psycopg
from app.dashboard_cache import cache_ready, refresh_cache
from app.reconciliation_backend import ReconciliationBackend, ReconciliationFilter
from app import counterpart_tracking


class RollbackCheck(Exception):
    pass


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class DashboardCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'], autocommit=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        refresh_cache(self.conn)

    def test_cached_rows_summaries_details_and_pages_equal_live_results(self):
        live = ReconciliationBackend(self.conn)
        cached = ReconciliationBackend(self.conn, cached=True)
        filters = [ReconciliationFilter(), ReconciliationFilter(country='Kenya'),
                   ReconciliationFilter(country='Rwanda'), ReconciliationFilter(status='matched'),
                   ReconciliationFilter(start_date='2026-09-23', end_date='2026-09-23'),
                   ReconciliationFilter(start_date='2040-01-01')]
        for selection in filters:
            with self.subTest(filters=selection):
                self.assertEqual(cached.summary(selection), live.summary(selection))
                for review in (True, False):
                    first = cached.list_records(selection, review_only=review, limit=3)
                    self.assertEqual(first, live.list_records(selection, review_only=review, limit=3))
                    if first['has_more']:
                        self.assertEqual(
                            cached.list_records(selection, review_only=review, limit=3, after_record_id=first['next_cursor']),
                            live.list_records(selection, review_only=review, limit=3, after_record_id=first['next_cursor']))
        for record in ('matched', 'duplicate_wallet_a', 'missing_tx', 'missing_amount', 'shared_rw', 'credit_excluded', 'nonexistent'):
            self.assertEqual(cached.get_record(record), live.get_record(record))
        self.assertIsNone(cached.get_record('matched', country='Rwanda'))

    def test_cached_tracking_and_exports_equal_live_results(self):
        for filters in (ReconciliationFilter(), ReconciliationFilter(country='Kenya'),
                        ReconciliationFilter(start_date='2026-09-23', end_date='2026-09-23')):
            self.assertEqual(counterpart_tracking.summary(self.conn, filters, cached=True),
                             counterpart_tracking.summary(self.conn, filters))
            for group in counterpart_tracking.GROUPS:
                self.assertEqual(counterpart_tracking.page(self.conn, group, filters, limit=2, cached=True),
                                 counterpart_tracking.page(self.conn, group, filters, limit=2))
                self.assertEqual(counterpart_tracking.export(self.conn, group, filters, cached=True),
                                 counterpart_tracking.export(self.conn, group, filters))

    def test_source_edits_deletes_and_truncates_invalidate_and_rollbacks_restore(self):
        statements = (
            "UPDATE reconciliation.wallets SET customer_name='Changed rider' WHERE zoho_record_id='wallet_ke'",
            "UPDATE reconciliation.wallet_transactions SET amount=1.01 WHERE zoho_record_id='matched'",
            "DELETE FROM reconciliation.swap_transactions WHERE zoho_record_id='swap_matched'",
            "TRUNCATE reconciliation.swap_transactions",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(RollbackCheck):
                    with self.conn.transaction():
                        self.conn.execute(statement)
                        self.assertFalse(cache_ready(self.conn))
                        self.assertTrue(refresh_cache(self.conn))
                        self.assertTrue(cache_ready(self.conn))
                        self.assertEqual(ReconciliationBackend(self.conn, cached=True).get_record('matched'),
                                         ReconciliationBackend(self.conn).get_record('matched'))
                        raise RollbackCheck()
                self.assertTrue(cache_ready(self.conn))
        self.assertEqual(ReconciliationBackend(self.conn, cached=True).get_record('matched')['wallet_amount'], '120.10')

    def test_unchanged_sources_reuse_cache_without_rebuilding_or_mutating_sources(self):
        before = self.conn.execute('SELECT refreshed_at FROM reconciliation.dashboard_cache_state').fetchone()[0]
        source = self.conn.execute('SELECT count(*),sum(amount) FROM reconciliation.wallet_transactions').fetchone()
        self.assertFalse(refresh_cache(self.conn))
        self.assertEqual(before, self.conn.execute('SELECT refreshed_at FROM reconciliation.dashboard_cache_state').fetchone()[0])
        self.assertEqual(source, self.conn.execute('SELECT count(*),sum(amount) FROM reconciliation.wallet_transactions').fetchone())
