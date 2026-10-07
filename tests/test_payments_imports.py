"""Payment CSV validation and imports; integration uses only temporary tables."""
import csv
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import psycopg
import payments_import_service as service
from payments_wallet_reconciliation import SUMMARY_SQL, MATCH_EXISTS, ATLAS_SCOPE, MISSING_SQL


def write_csv(folder, rows, name='payments.csv'):
    path = Path(folder) / name
    rows = [dict(row, settled_against=row.get('settled_against', 'Wallet Recharge')) for row in rows]
    fields = list(rows[0])
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


class PaymentValidationTests(unittest.TestCase):
    def test_zoho_and_database_headers_preserve_identifiers(self):
        with TemporaryDirectory() as folder:
            path = write_csv(folder, [{'Record Id':'00001','Atlas Transaction ID':'000123','Amount':'1.20','Country':'Kenya'}])
            self.assertEqual(service.inspect_csv(path)['dataset'], 'atlas')
            self.assertEqual(next(service.read_rows(path))['atlas_transaction_id'], '000123')
            path = write_csv(folder, [{'record_id':'w1','reference':'000123','transaction_id':'000234'}])
            self.assertEqual(service.inspect_csv(path)['dataset'], 'wallet')

    def test_invalid_csvs_are_rejected(self):
        cases = [
            [{'Record Id':'','Atlas Transaction ID':'a'}],
            [{'Record Id':'p','Atlas Transaction ID':'a','Amount':'NaN'}],
            [{'Record Id':'p','Atlas Transaction ID':'a','Transaction Date':'bad'}],
            [{'Record Id':'p','Atlas Transaction ID':'a','Transaction Date':'2026-10-07T10:00:00+03:00'}],
            [{'Record Id':'p','Unknown':'a'}],
        ]
        with TemporaryDirectory() as folder:
            for rows in cases:
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    service.inspect_csv(write_csv(folder, rows))
            path = Path(folder)/'missing-settlement.csv'
            path.write_text('Record Id,Atlas Transaction ID\na,001\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Settled Against'): service.inspect_csv(path)
            path = Path(folder)/'empty.csv'
            path.write_text('Record Id,Atlas Transaction ID\n',encoding='utf-8')
            with self.assertRaises(ValueError): service.inspect_csv(path)


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit database connection required; writes only temporary tables')
class PaymentDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'], autocommit=True)
        self.conn.execute('CREATE TEMP TABLE payments_test_anchor (id integer)')
        schema = (Path(__file__).resolve().parents[1]/'sql/07_payments_reconciliation.sql').read_text(encoding='utf-8')
        schema = schema[:schema.index('COMMENT ON SCHEMA')]
        schema = schema.replace('CREATE SCHEMA IF NOT EXISTS payments_reconciliation;', '')
        schema = schema.replace('payments_reconciliation.', 'pg_temp.')
        schema = schema.replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMP TABLE IF NOT EXISTS')
        self.conn.execute(schema)
        self.override = patch.object(service, 'SCHEMA', 'pg_temp')
        self.override.start()
        self.folder = TemporaryDirectory()

    def tearDown(self):
        self.override.stop()
        self.conn.close()
        self.folder.cleanup()

    def test_import_repeat_latest_version_and_matching(self):
        path = write_csv(self.folder.name, [
            {'record_id':'a','atlas_transaction_id':'001','amount':'12.30','source_modified_at':'2026-10-01T10:00:00'},
            {'record_id':'a','atlas_transaction_id':'001','amount':'13.30','source_modified_at':'2026-10-02T10:00:00'},
            {'record_id':'b','atlas_transaction_id':'002','amount':'1','source_modified_at':'2026-10-02T10:00:00'},
        ])
        self.assertEqual(service.import_file(self.conn,path,'atlas')['rows_applied'],2)
        self.assertEqual(service.import_file(self.conn,path,'atlas')['rows_applied'],0)
        self.assertEqual(str(self.conn.execute("SELECT amount FROM pg_temp.atlas_transactions WHERE record_id='a'").fetchone()[0]),'13.30')
        older = write_csv(self.folder.name,[{'record_id':'a','atlas_transaction_id':'001','amount':'9','source_modified_at':'2026-09-01T10:00:00'}])
        self.assertEqual(service.import_file(self.conn,older,'atlas')['rows_applied'],0)
        wallet = write_csv(self.folder.name,[
            {'record_id':'w1','reference':'001','transaction_id':'t1'},
            {'record_id':'w2','reference':'001','transaction_id':'t2'},
            {'record_id':'w3','reference':'002','transaction_id':''},
        ],'wallet.csv')
        service.import_file(self.conn,wallet,'wallet')
        totals=self.conn.execute(SUMMARY_SQL.replace('payments_reconciliation.','pg_temp.')).fetchone()
        self.assertEqual(totals,(2,1,3))
        missing=self.conn.execute('SELECT count(*) FROM pg_temp.atlas_transactions a WHERE '+ATLAS_SCOPE+' AND NOT '+MATCH_EXISTS.replace('payments_reconciliation.','pg_temp.')).fetchone()[0]
        self.assertEqual(missing,1)

    def test_only_wallet_recharge_on_both_sides_in_summary_and_details(self):
        atlas = write_csv(self.folder.name, [
            {'record_id':'match','atlas_transaction_id':'001','settled_against':'Wallet Recharge'},
            {'record_id':'wrong_wallet_category','atlas_transaction_id':'002','settled_against':'Wallet Recharge'},
            {'record_id':'no_wallet_id','atlas_transaction_id':'003','settled_against':'Wallet Recharge'},
            {'record_id':'swap_excluded','atlas_transaction_id':'004','settled_against':'Swap'},
            {'record_id':'null_excluded','atlas_transaction_id':'005','settled_against':''},
        ])
        wallet = write_csv(self.folder.name, [
            {'record_id':'w1','reference':'001','transaction_id':'t1','settled_against':'Wallet Recharge'},
            {'record_id':'w2','reference':'001','transaction_id':'t2','settled_against':'Wallet Recharge'},
            {'record_id':'w3','reference':'002','transaction_id':'t3','settled_against':'Swap'},
            {'record_id':'w4','reference':'003','transaction_id':'','settled_against':'Wallet Recharge'},
            {'record_id':'w5','reference':'004','transaction_id':'t5','settled_against':'Wallet Recharge'},
        ],'wallet.csv')
        service.import_file(self.conn,atlas,'atlas')
        service.import_file(self.conn,wallet,'wallet')
        self.assertEqual(self.conn.execute(SUMMARY_SQL.replace('payments_reconciliation.','pg_temp.')).fetchone(),(3,1,4))
        missing=self.conn.execute(MISSING_SQL.replace('payments_reconciliation.','pg_temp.'),(50,0)).fetchall()
        self.assertEqual({row[1] for row in missing},{'002','003'})
        page=self.conn.execute(MISSING_SQL.replace('payments_reconciliation.','pg_temp.'),(1,1)).fetchall()
        self.assertEqual(len(page),1)

    def test_migration_recovers_settlement_and_is_repeatable(self):
        self.conn.execute("INSERT INTO pg_temp.atlas_transactions(record_id,raw_record) VALUES ('legacy', %s::jsonb),('unclassified', '{}'::jsonb)", ('{"Settled Against":"Wallet Recharge"}',))
        migration=(Path(__file__).resolve().parents[1]/'sql/08_payments_wallet_recharge.sql').read_text(encoding='utf-8').replace('payments_reconciliation.','pg_temp.')
        self.conn.execute(migration)
        self.conn.execute(migration)
        self.assertEqual(self.conn.execute("SELECT settled_against FROM pg_temp.atlas_transactions WHERE record_id='legacy'").fetchone()[0],'Wallet Recharge')
        self.assertIsNone(self.conn.execute("SELECT settled_against FROM pg_temp.atlas_transactions WHERE record_id='unclassified'").fetchone()[0])

    def test_failed_file_rolls_back_rows_and_history(self):
        self.conn.execute("ALTER TABLE pg_temp.atlas_transactions ADD CHECK (record_id <> 'blocked')")
        path=write_csv(self.folder.name,[{'record_id':'ok','atlas_transaction_id':'001'}, {'record_id':'blocked','atlas_transaction_id':'002'}])
        with self.assertRaises(psycopg.Error): service.import_file(self.conn,path,'atlas')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM pg_temp.atlas_transactions').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM pg_temp.import_batches').fetchone()[0],0)


if __name__ == '__main__': unittest.main()
