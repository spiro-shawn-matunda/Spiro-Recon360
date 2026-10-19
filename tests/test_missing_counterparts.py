"""Missing-counterpart exports use explicit isolated fixtures, never project .env."""
import csv
import json
import os
import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import psycopg
from app.missing_counterparts import write_missing_reports
from app.reconciliation_backend import ReconciliationFilter


class MainExportTests(unittest.TestCase):
    def test_main_dispatches_report_without_starting_dashboard(self):
        import main
        with patch('app.missing_counterparts.main') as export, patch('main.read_database_config') as config:
            main.main(['--export-unmatched', '--country', 'Rwanda'])
            export.assert_called_once_with(['--country', 'Rwanda'])
            config.assert_not_called()

    def test_main_dispatches_review_export_without_starting_dashboard(self):
        import main
        with patch('app.reconcile.main') as export, patch('main.read_database_config') as config:
            main.main(['--export-review', '--country', 'Rwanda'])
            export.assert_called_once_with(['--country', 'Rwanda'])
            config.assert_not_called()


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class MissingCounterpartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'], autocommit=True)
        with cls.conn.transaction():
            for record, tx, country, kind, created, amount in [
                ('wallet-ke', 'mc-shared', 'Kenya', 'Debit', '2026-09-23', '120.10'),
                ('dupe-a', 'mc-dupe', 'Kenya', 'Debit', '2026-09-23', '120.10'),
                ('dupe-b', 'mc-dupe', 'Kenya', 'Debit', '2026-09-23', '120.10'),
                ('failed-match', 'mc-failed', 'Kenya', 'Debit', '2026-09-23', '120.10'),
                ('credit', 'mc-credit', 'Kenya', 'Credit', '2026-09-23', '120.10'),
                ('null', None, 'Kenya', 'Debit', '2026-09-23', '120.10'),
                ('date-wallet', 'mc-date-wallet', 'Kenya', 'Debit', '2026-09-23', '120.10'),
                ('date-swap', 'mc-date-swap', 'Kenya', 'Debit', '2026-09-22', '120.10'),
                ('missing-amount', 'mc-missing-amount', 'Kenya', 'Debit', '2026-09-23', None),
                ('country-none', 'mc-unknown-country', None, 'Debit', '2026-09-23', '120.10'),
            ]:
                cls.conn.execute('''INSERT INTO reconciliation.wallet_transactions
                    (zoho_record_id, transaction_id, country, wallet_id, transaction_type,status,settled_against,
                     amount,created_on,source_file,source_row_number,raw_record)
                    VALUES (%s,%s,%s,'wallet_ke',%s,'Committed','Swap',%s,%s,'missing-report-fixture.csv',1,'{}')''',
                    ('missing-report-'+record, tx, country, kind, amount, created))
            for record, tx, country, status, method, created in [
                ('swap-rw', 'mc-shared', 'Rwanda', 'SUCCESS', 'WALLET', '2026-09-23'),
                ('failed-match', 'mc-failed', 'Kenya', 'FAILED', 'WALLET', '2026-09-23'),
                ('credit', 'mc-credit', 'Kenya', 'SUCCESS', 'WALLET', '2026-09-23'),
                ('null', None, 'Kenya', 'SUCCESS', 'WALLET', '2026-09-23'),
                ('date-wallet', 'mc-date-wallet', 'Kenya', 'SUCCESS', 'WALLET', '2026-09-22'),
                ('date-swap', 'mc-date-swap', 'Kenya', 'SUCCESS', 'WALLET', '2026-09-23'),
                ('offer', 'mc-offer', 'Kenya', 'SUCCESS', 'OFFER_APPLIED', '2026-09-23'),
                ('country-none', 'mc-unknown-country', None, 'SUCCESS', 'WALLET', '2026-09-23'),
            ]:
                cls.conn.execute('''INSERT INTO reconciliation.swap_transactions
                    (zoho_record_id,transaction_id,country,customer_id,status,payment_method,swap_amount,
                     created_on,source_file,source_row_number,raw_record)
                    VALUES (%s,%s,%s,%s,%s,%s,120.10,%s,'missing-report-fixture.csv',1,'{}')''',
                    ('missing-report-'+record, tx, country, 'customer_rw' if country=='Rwanda' else 'customer_ke', status, method, created))

    @classmethod
    def tearDownClass(cls):
        with cls.conn.transaction():
            for table in ('wallet_transactions','swap_transactions'):
                cls.conn.execute(psycopg.sql.SQL('DELETE FROM {} WHERE zoho_record_id LIKE %s').format(psycopg.sql.Identifier('reconciliation',table)), ('missing-report-%',))
        cls.conn.close()

    def export(self, filters=None):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        folder = Path(self.folder.name)
        summary = write_missing_reports(self.conn, folder, filters)
        groups = {}
        for group in ('wallet_without_swap','swap_without_wallet'):
            with (folder/(group+'.csv')).open(encoding='utf-8-sig',newline='') as stream:
                groups[group] = list(csv.DictReader(stream))
        return summary, groups

    def test_country_match_duplicate_rows_and_any_swap_status(self):
        _, groups = self.export()
        wallets = {r['wallet_record_id']:r for r in groups['wallet_without_swap']}
        swaps = {r['swap_record_id']:r for r in groups['swap_without_wallet']}
        self.assertIn('missing-report-wallet-ke', wallets)
        self.assertIn('missing-report-swap-rw', swaps)
        self.assertIn('missing-report-dupe-a', wallets)
        self.assertIn('missing-report-dupe-b', wallets)
        self.assertNotIn('missing-report-failed-match', wallets)
        self.assertNotIn('missing-report-failed-match', swaps)
        self.assertNotIn('missing-report-credit', wallets)
        self.assertIn('missing-report-credit', swaps)
        self.assertEqual(wallets['missing-report-wallet-ke']['wallet_amount'], '120.10')
        self.assertEqual(swaps['missing-report-swap-rw']['swap_amount'], '120.10')
        self.assertEqual(swaps['missing-report-swap-rw']['currency'], 'RWF')
        self.assertEqual(swaps['missing-report-offer']['wallet_payment_expected'], 'False')

    def test_missing_identifiers_are_labelled_and_summary_agrees(self):
        summary, groups = self.export()
        for group, rows in groups.items():
            key = 'wallet_record_id' if group=='wallet_without_swap' else 'swap_record_id'
            owned = {row[key]:row for row in rows}
            self.assertEqual(owned['missing-report-null']['missing_counterpart_reason'],'missing_transaction_id')
            self.assertEqual(owned['missing-report-country-none']['missing_counterpart_reason'],'missing_country')
            self.assertEqual(len(rows), summary['groups'][group]['rows'])
            self.assertEqual(dict(Counter(row['country'] or 'Unknown' for row in rows)), summary['groups'][group]['by_country'])
            unmatched = sum(row['missing_counterpart_reason'] in ('missing_transaction_id','missing_country') for row in rows)
            self.assertEqual(unmatched, sum(summary['groups'][group]['unmatchable_records_by_country'].values()))
            self.assertEqual(len(rows)-unmatched, sum(summary['groups'][group]['valid_identifier_no_counterpart_records_by_country'].values()))
            self.assertTrue(all(row['source_coverage_status']=='not_verified' for row in rows))

    def test_date_filter_does_not_hide_counterpart_outside_range(self):
        _, groups = self.export(ReconciliationFilter(country='Kenya',start_date='2026-09-23',end_date='2026-09-23'))
        for group, rows in groups.items():
            key = 'wallet_record_id' if group=='wallet_without_swap' else 'swap_record_id'
            self.assertTrue(all(row['country']=='Kenya' for row in rows))
            self.assertNotIn('missing-report-date-wallet', {row[key] for row in rows})
            self.assertNotIn('missing-report-date-swap', {row[key] for row in rows})

    def test_empty_results_have_headers_and_export_is_read_only(self):
        before = self.conn.execute('SELECT count(*) FROM reconciliation.import_batches').fetchone()[0]
        summary, groups = self.export(ReconciliationFilter(start_date='2030-01-01'))
        self.assertTrue(all(not rows for rows in groups.values()))
        self.assertTrue(all(group['rows']==0 for group in summary['groups'].values()))
        after = self.conn.execute('SELECT count(*) FROM reconciliation.import_batches').fetchone()[0]
        self.assertEqual(before, after)


if __name__=='__main__': unittest.main()
