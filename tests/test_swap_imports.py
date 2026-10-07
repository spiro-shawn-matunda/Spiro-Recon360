"""Swap CSV uploads; database integration writes only session-temporary tables."""
import csv
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import psycopg
import swap_import_service as service
from payments_swap_reconciliation import SUMMARY_SQL


def write_csv(folder,rows,name='payments.csv'):
    path=Path(folder)/name
    with path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    return path


class SwapCsvValidationTests(unittest.TestCase):
    def test_detection_identifiers_and_invalid_values(self):
        with TemporaryDirectory() as folder:
            path=write_csv(folder,[{'Record Id':'0001','Atlas Transaction ID':'000123','Amount':'12.50','Locked':'false'}])
            self.assertEqual(service.inspect_csv(path)['dataset'],'swap_payments')
            self.assertEqual(next(service.read_rows(path))[1]['Atlas Transaction ID'],'000123')
            path=write_csv(folder,[{'Record Id':'s1','Transaction ID':'000123','Pay Method':'PAYMENT_OPERATOR'}])
            self.assertEqual(service.inspect_csv(path)['dataset'],'swap')
            for row in [
                {'Record Id':'','Atlas Transaction ID':'a'},
                {'Record Id':'a','Atlas Transaction ID':'a','Amount':'NaN'},
                {'Record Id':'a','Atlas Transaction ID':'a','Locked':'maybe'},
            ]:
                with self.subTest(row=row),self.assertRaises(ValueError):service.inspect_csv(write_csv(folder,[row]))
            with self.assertRaises(ValueError):service.layout(['Record Id','Atlas Transaction ID','Transaction ID','Pay Method'])
            with self.assertRaises(ValueError):service.layout(['Record Id','Reference','Transaction ID'])

    def test_ambiguous_column_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            service.column_mapping(['record_id','customer_id'],{'Record Id':'text','Customer.id':'text','Customer ID':'text'})


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'),'Explicit connection required; tests write only temporary tables')
class SwapCsvDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn=psycopg.connect(os.environ['SPIRO_TEST_DSN'],autocommit=True)
        self.conn.execute('CREATE TEMP TABLE swap_test_anchor (id integer)')
        schema=(Path(__file__).resolve().parents[1]/'sql/09_swap_reconciliation.sql').read_text(encoding='utf-8')
        schema=schema[:schema.index('COMMENT ON SCHEMA')]
        schema=schema.replace('CREATE SCHEMA IF NOT EXISTS swap_reconciliation;','').replace('swap_reconciliation.','pg_temp.')
        schema=schema.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS')
        self.conn.execute(schema)
        self.schema=patch.object(service,'SCHEMA','pg_temp');self.schema.start()
        self.audit=patch.object(service,'AUDIT_SCHEMA','pg_temp');self.audit.start()
        self.folder=TemporaryDirectory()

    def tearDown(self):
        self.schema.stop();self.audit.stop();self.conn.close();self.folder.cleanup()

    def test_repeat_duplicates_old_exports_and_id_reconciliation(self):
        path=write_csv(self.folder.name,[
            {'Record Id':'p1','Atlas Transaction ID':'000123','Amount':'10.50','Modified Time':'2026-10-01T10:00:00','Locked':'false'},
            {'Record Id':'p1','Atlas Transaction ID':'000123','Amount':'12.50','Modified Time':'2026-10-02T10:00:00','Locked':'true'},
            {'Record Id':'p2','Atlas Transaction ID':'B','Amount':'20','Modified Time':'2026-10-02T10:00:00','Locked':'false'},
        ])
        self.assertEqual(service.import_file(self.conn,path,'swap_payments')['rows_applied'],2)
        self.assertEqual(service.import_file(self.conn,path,'swap_payments')['rows_applied'],0)
        self.assertEqual(self.conn.execute("""SELECT \"Amount\",\"Locked\" FROM pg_temp.swap_payments WHERE \"Record Id\"='p1'""").fetchone(),('12.50',True))
        older=write_csv(self.folder.name,[{'Record Id':'p1','Atlas Transaction ID':'000123','Amount':'1','Modified Time':'2026-09-01T10:00:00'}])
        self.assertEqual(service.import_file(self.conn,older,'swap_payments')['rows_applied'],0)
        swaps=write_csv(self.folder.name,[
            {'Record Id':'s1','Transaction ID':'000123','Pay Method':'PAYMENT_OPERATOR','Swap Amount':'12.50','Last Modified On':'2026-10-02T10:00:00'},
            {'Record Id':'s2','Transaction ID':'C','Pay Method':'PAYMENT_OPERATOR','Swap Amount':'20','Last Modified On':'2026-10-02T10:00:00'},
            {'Record Id':'s3','Transaction ID':'B','Pay Method':'WALLET','Swap Amount':'20','Last Modified On':'2026-10-02T10:00:00'},
        ],'swaps.csv')
        self.assertEqual(service.import_file(self.conn,swaps,'swap')['rows_applied'],3)
        self.assertEqual(self.conn.execute(SUMMARY_SQL.replace('swap_reconciliation.','pg_temp.')).fetchone(),(2,2,1,1))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM pg_temp.swap_import_batches').fetchone()[0],4)

    def test_file_failure_rolls_back_data_and_audit(self):
        self.conn.execute("""ALTER TABLE pg_temp.swap_payments ADD CHECK (\"Atlas Transaction ID\" <> 'blocked')""")
        path=write_csv(self.folder.name,[{'Record Id':'p1','Atlas Transaction ID':'ok'},{'Record Id':'p2','Atlas Transaction ID':'blocked'}])
        with self.assertRaises(psycopg.Error):service.import_file(self.conn,path,'swap_payments')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM pg_temp.swap_payments').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM pg_temp.swap_import_batches').fetchone()[0],0)


if __name__=='__main__':unittest.main()
