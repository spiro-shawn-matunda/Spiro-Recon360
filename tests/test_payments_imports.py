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
    rows = [dict(row, type=row.get('type', 'Credit')) if 'reference' in row and 'transaction_id' in row else row for row in rows]
    rows = [dict(row, remarks='Wallet Recharge') if ('atlas_transaction_id' in row or 'Atlas Transaction ID' in row) and 'remarks' not in row and 'Remarks' not in row else row for row in rows]
    rows = [dict(row, payment_status='Paid') if ('atlas_transaction_id' in row or 'Atlas Transaction ID' in row) and 'payment_status' not in row and 'Payment Status' not in row else row for row in rows]
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
            with self.assertRaisesRegex(ValueError, 'Remarks'): service.inspect_csv(path)
            path = Path(folder)/'empty.csv'
            path.write_text('Record Id,Atlas Transaction ID\n',encoding='utf-8')
            with self.assertRaises(ValueError): service.inspect_csv(path)

    def test_atlas_accepts_remarks_without_settled_against(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'atlas.csv'
            path.write_text('Record Id,Atlas Transaction ID,Remarks,Payment Status\np1,001,Wallet Recharge,Paid\n',encoding='utf-8')
            self.assertEqual(service.inspect_csv(path)['dataset'],'atlas')
            self.assertEqual(next(service.read_rows(path))['remarks'],'Wallet Recharge')

    def test_payment_status_header_is_required(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'atlas.csv'
            path.write_text('Record Id,Atlas Transaction ID,Remarks\np1,001,Wallet Recharge\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Payment Status'):
                service.inspect_csv(path)

    def test_wallet_requires_type_header_and_accepts_credit_and_debit(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'wallet.csv'
            path.write_text('Record Id,Reference,Transaction ID,Settled Against\nw1,001,t1,Wallet Recharge\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Type'):
                service.inspect_csv(path)
            path.write_text('Record Id,Reference,Transaction ID,Settled Against,Type\nw1,001,t1,Wallet Recharge,Credit\nw2,002,t2,Wallet Recharge,Debit\n',encoding='utf-8')
            self.assertEqual(service.inspect_csv(path)['rows'],2)
            self.assertEqual([r['type'] for r in service.read_rows(path)],['Credit','Debit'])


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

    def test_payment_remarks_and_wallet_settlement_scope_in_summary_and_details(self):
        atlas = write_csv(self.folder.name, [
            {'record_id':'match','atlas_transaction_id':'001','settled_against':'Wallet Recharge','remarks':'Wallet Recharge'},
            {'record_id':'wrong_wallet_category','atlas_transaction_id':'002','settled_against':'Wallet Recharge','remarks':'Wallet Recharge'},
            {'record_id':'no_wallet_id','atlas_transaction_id':'003','settled_against':'Wallet Recharge','remarks':'Wallet Recharge'},
            {'record_id':'swap_excluded','atlas_transaction_id':'004','settled_against':'Swap','remarks':'Swap'},
            {'record_id':'null_excluded','atlas_transaction_id':'005','settled_against':'','remarks':None},
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

    def test_payment_remarks_is_exact_and_independent_of_settled_against(self):
        rows=[
            {'record_id':'exact','atlas_transaction_id':'001','remarks':'Wallet Recharge','settled_against':'Swap'},
            {'record_id':'exact-null-settlement','atlas_transaction_id':'002','remarks':'Wallet Recharge','settled_against':None},
            {'record_id':'variant','atlas_transaction_id':'003','remarks':'Wallet Recharge(Spiro Atlas Customer)','settled_against':'Wallet Recharge'},
            {'record_id':'other','atlas_transaction_id':'004','remarks':'USSD Africastalking Payment','settled_against':'Wallet Recharge'},
            {'record_id':'empty','atlas_transaction_id':'005','remarks':None,'settled_against':'Wallet Recharge'},
        ]
        service.import_file(self.conn,write_csv(self.folder.name,rows),'atlas')
        self.assertEqual(self.conn.execute(SUMMARY_SQL.replace('payments_reconciliation.','pg_temp.')).fetchone(),(2,0,0))
        missing=self.conn.execute(MISSING_SQL.replace('payments_reconciliation.','pg_temp.'),(50,0)).fetchall()
        self.assertEqual({row[1] for row in missing},{'001','002'})

    def test_only_paid_payments_are_in_summary_and_missing_table(self):
        rows=[
            {'record_id':'paid-matched','atlas_transaction_id':'001','payment_status':'Paid'},
            {'record_id':'paid-missing','atlas_transaction_id':'002','payment_status':'Paid'},
            {'record_id':'failed','atlas_transaction_id':'003','payment_status':'Failed'},
            {'record_id':'pending','atlas_transaction_id':'004','payment_status':'Pending'},
            {'record_id':'blank','atlas_transaction_id':'005','payment_status':None},
        ]
        service.import_file(self.conn,write_csv(self.folder.name,rows),'atlas')
        wallet=write_csv(self.folder.name,[
            {'record_id':'w1','reference':'001','transaction_id':'t1'},
            {'record_id':'w2','reference':'003','transaction_id':'t2'},
            {'record_id':'w3','reference':'004','transaction_id':'t3'},
        ],'wallet.csv')
        service.import_file(self.conn,wallet,'wallet')
        self.assertEqual(self.conn.execute(SUMMARY_SQL.replace('payments_reconciliation.','pg_temp.')).fetchone(),(2,1,3))
        missing=self.conn.execute(MISSING_SQL.replace('payments_reconciliation.','pg_temp.'),(50,0)).fetchall()
        self.assertEqual([row[1] for row in missing],['002'])
        self.assertEqual({row[5] for row in missing},{'Paid'})

    def test_remarks_migration_backfills_existing_uploads_without_overwrite(self):
        self.conn.execute("INSERT INTO pg_temp.atlas_transactions(record_id,raw_record,remarks) VALUES ('title',%s::jsonb,NULL),('snake',%s::jsonb,NULL),('preserved',%s::jsonb,'Other'),('absent','{}'::jsonb,NULL)",
                          ('{"Remarks":"Wallet Recharge"}','{"remarks":"USSD Africastalking Payment"}','{"Remarks":"Wallet Recharge"}'))
        migration=(Path(__file__).resolve().parents[1]/'sql/11_payments_wallet_remarks.sql').read_text(encoding='utf-8').replace('payments_reconciliation.','pg_temp.')
        self.conn.execute(migration);self.conn.execute(migration)
        rows=dict(self.conn.execute('SELECT record_id,remarks FROM pg_temp.atlas_transactions').fetchall())
        self.assertEqual(rows,{'title':'Wallet Recharge','snake':'USSD Africastalking Payment','preserved':'Other','absent':None})

    def test_only_credit_wallet_entries_match_and_count(self):
        ids=['matched','debit-only','missing-type','blank-type','no-transaction-id','wrong-category','no-wallet','00123']
        atlas=write_csv(self.folder.name,[{'record_id':key,'atlas_transaction_id':key} for key in ids])
        wallet=write_csv(self.folder.name,[
            {'record_id':'c1','reference':'matched','transaction_id':'t1','type':'Credit','settled_against':'Wallet Recharge'},
            {'record_id':'c2','reference':'matched','transaction_id':'t2','type':'Credit','settled_against':'Wallet Recharge'},
            {'record_id':'d1','reference':'matched','transaction_id':'t3','type':'Debit','settled_against':'Wallet Recharge'},
            {'record_id':'d2','reference':'debit-only','transaction_id':'t4','type':'Debit','settled_against':'Wallet Recharge'},
            {'record_id':'u1','reference':'missing-type','transaction_id':'t5','type':None,'settled_against':'Wallet Recharge'},
            {'record_id':'u2','reference':'blank-type','transaction_id':'t6','type':'  ','settled_against':'Wallet Recharge'},
            {'record_id':'c3','reference':'no-transaction-id','transaction_id':'','type':'Credit','settled_against':'Wallet Recharge'},
            {'record_id':'c4','reference':'wrong-category','transaction_id':'t7','type':'Credit','settled_against':'Swap'},
            {'record_id':'c5','reference':'00123','transaction_id':'t8','type':'Credit','settled_against':'Wallet Recharge'},
            {'record_id':'c6','reference':'','transaction_id':'t9','type':'Credit','settled_against':'Wallet Recharge'},
        ],'wallet.csv')
        service.import_file(self.conn,atlas,'atlas')
        service.import_file(self.conn,wallet,'wallet')
        self.assertEqual(self.conn.execute(SUMMARY_SQL.replace('payments_reconciliation.','pg_temp.')).fetchone(),(8,2,5))
        query=MISSING_SQL.replace('payments_reconciliation.','pg_temp.')
        missing=self.conn.execute(query,(50,0)).fetchall()
        self.assertEqual({row[1] for row in missing},set(ids)-{'matched','00123'})
        paged=[]
        for offset in range(0,len(missing),2):
            paged.extend(self.conn.execute(query,(2,offset)).fetchall())
        self.assertEqual([row[1] for row in paged],[row[1] for row in missing])

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
