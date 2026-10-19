"""Uganda rent-settlement rule; fixtures never modify user data."""
import os
import unittest
import psycopg
from payments_due_reconciliation import classify_payment, NO_DUE_SQL

FIXTURES=[('Uganda','Settlement of Rent',value) for value in (None,'','  ','\t\r\n','DUE-1','0')]
FIXTURES += [('Kenya','Settlement of Rent',None),('Rwanda','Settlement of Rent',None),
             ('Uganda','Wallet Recharge',None),('Uganda',None,None),
             ('Uganda','Settlement of Rent adjustment',None)]


class UgandaDueTests(unittest.TestCase):
    def test_blank_ids_are_no_due(self):
        for value in (None,'','  ','\t\r\n'):
            with self.subTest(value=value):
                self.assertEqual(classify_payment('Uganda','Settlement of Rent',value),'no_due')

    def test_populated_ids_are_not_no_due(self):
        for value in ('DUE-1',' DUE-1 ','0'):
            with self.subTest(value=value):
                self.assertEqual(classify_payment('Uganda','Settlement of Rent',value),'with_due_id')

    def test_other_countries_and_remarks_are_out_of_scope(self):
        for country,remarks,value in FIXTURES[6:]:
            with self.subTest(country=country,remarks=remarks):
                self.assertEqual(classify_payment(country,remarks,value),'out_of_scope')


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Read-only SQL fixture needs a connection')
class UgandaDueSqlTests(unittest.TestCase):
    def test_sql_matches_classifier(self):
        with psycopg.connect(os.environ['SPIRO_TEST_DSN']) as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            for country,remarks,value in FIXTURES:
                with self.subTest(country=country,remarks=remarks,value=value):
                    result=conn.execute('SELECT COALESCE('+NO_DUE_SQL+',false) FROM '
                        '(VALUES (%s::text,%s::text,%s::text)) p(country,remarks,union_due_id_new)',
                        (country,remarks,value)).fetchone()[0]
                    self.assertEqual(result,classify_payment(country,remarks,value)=='no_due')





import csv
import io
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import due_import_service as importer
import payments_due_reconciliation as backend


def write_export(folder, rows):
    path=Path(folder)/'due-payments.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def payment(record_id, **changes):
    return {'Record Id':record_id,'Country':'Uganda','Remarks':'Settlement of Rent',
            'Union Due ID (New)':'','Atlas Transaction ID':'000123',
            'Payment Transaction No':'UG-P-'+record_id,'Amount':'12.30',
            'Payment Status':'Paid',**changes}


class DueCsvValidationTests(unittest.TestCase):
    def test_blank_due_id_is_accepted_and_ids_preserved(self):
        with TemporaryDirectory() as folder:
            path=write_export(folder,[payment('000001')])
            self.assertEqual(importer.inspect_csv(path)['dataset'],'due_payments')
            row=next(importer.read_rows(path))
            self.assertIsNone(row['union_due_id_new'])
            self.assertEqual(row['atlas_transaction_id'],'000123')
            self.assertEqual(row['record_id'],'000001')

    def test_missing_due_column_is_rejected_not_classified_as_blank(self):
        with TemporaryDirectory() as folder:
            row=payment('p1');del row['Union Due ID (New)']
            with self.assertRaisesRegex(ValueError,'Union Due ID'):
                importer.inspect_csv(write_export(folder,[row]))

    def test_csv_safety_preserves_numeric_amounts(self):
        self.assertEqual(backend.csv_cell(Decimal('-12.30')),'-12.30')
        self.assertEqual(backend.csv_cell('=1+2'),"'=1+2")
        self.assertEqual(backend.csv_cell('000123'),'000123')

    def test_invalid_requests_fail_before_connecting(self):
        for country,page in [('invalid',0),('Uganda',-1),('Uganda',True)]:
            with self.subTest(country=country,page=page),self.assertRaises(ValueError):
                backend.get_dashboard({},country,page)
        with self.assertRaises(ValueError): backend.export_missing({},'Kenya')


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Writes only temporary test tables')
class DueImportAndResultsTests(unittest.TestCase):
    def setUp(self):
        self.conn=psycopg.connect(os.environ['SPIRO_TEST_DSN'],autocommit=True)
        ddl=(Path(__file__).resolve().parents[1]/'sql/10_due_reconciliation.sql').read_text(encoding='utf-8')
        ddl=ddl[:ddl.index('COMMENT ON SCHEMA')]
        ddl=ddl.replace('CREATE SCHEMA IF NOT EXISTS due_reconciliation;','')
        ddl=ddl.replace('due_reconciliation.','pg_temp.').replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS')
        self.conn.execute(ddl)
        self.folder=TemporaryDirectory()
        self.schema_patch=patch.object(importer,'SCHEMA','pg_temp');self.schema_patch.start()
        self.summary_patch=patch.object(backend,'SUMMARY_SQL',backend.SUMMARY_SQL.replace('due_reconciliation.','pg_temp.'));self.summary_patch.start()
        self.details_patch=patch.object(backend,'DETAIL_SQL',backend.DETAIL_SQL.replace('due_reconciliation.','pg_temp.'));self.details_patch.start()
        self.snapshot_patch=patch.object(backend,'read_snapshot',lambda _: self.existing_snapshot());self.snapshot_patch.start()

    @backend.contextmanager
    def existing_snapshot(self):
        # Map the loaded count too; all other statements already use pg_temp.
        class Proxy:
            def __init__(self,conn):self.conn=conn
            def execute(self,query,*args):return self.conn.execute(query.replace('due_reconciliation.','pg_temp.'),*args)
            def cursor(self,*args,**kwargs):return self.conn.cursor(*args,**kwargs)
        with self.conn.transaction():
            yield Proxy(self.conn)

    def tearDown(self):
        self.snapshot_patch.stop();self.details_patch.stop();self.summary_patch.stop();self.schema_patch.stop()
        self.conn.close();self.folder.cleanup()

    def load(self,rows):
        return importer.import_file(self.conn,write_export(self.folder.name,rows),'due_payments')

    def test_cards_records_and_export_have_the_same_scope(self):
        rows=[payment('blank'),payment('spaces',**{'Union Due ID (New)':' \t '}),
              payment('failed',**{'Payment Status':'Failed'}),
              payment('with-id',**{'Union Due ID (New)':'DUE-NOT-IN-A-TABLE'}),
              payment('kenya',Country='Kenya'),payment('rwanda',Country='Rwanda'),
              payment('other',Remarks='Wallet Recharge')]
        self.load(rows)
        result=backend.get_dashboard({},'Uganda')
        self.assertEqual(result['summary'],{'payment_total':4,'with_due_id':1,'no_due':3,'dues_total':1})
        self.assertEqual({r['record_id'] for r in result['records']},{'blank','spaces','failed'})
        exported=list(csv.DictReader(io.StringIO(backend.export_missing({}).decode('utf-8-sig'))))
        self.assertEqual({r['Record Id'] for r in exported},{'blank','spaces','failed'})
        self.assertTrue(all(r['Remarks']=='Settlement of Rent' and r['Country']=='Uganda' for r in exported))
        self.assertTrue(all(r['Atlas Transaction ID']=='000123' for r in exported))
        other=backend.get_dashboard({},'Kenya')
        self.assertFalse(other['rule_configured']);self.assertIsNone(other['summary']['no_due'])

    def test_dues_total_counts_populated_uganda_rent_payment_ids(self):
        self.assertEqual(backend.get_dashboard({},'Uganda')['summary']['dues_total'],0)
        self.load([
            payment('with-id-1',**{'Union Due ID (New)':'DUE-001'}),
            payment('with-id-2',**{'Union Due ID (New)':'DUE-001'}),
            payment('blank',**{'Union Due ID (New)':''}),
            payment('spaces',**{'Union Due ID (New)':' \t '}),
            payment('kenya',Country='Kenya',**{'Union Due ID (New)':'DUE-002'}),
            payment('rwanda',Country='Rwanda',**{'Union Due ID (New)':'DUE-003'}),
            payment('other-remarks',Remarks='Wallet Recharge',**{'Union Due ID (New)':'DUE-004'}),
        ])
        summary=backend.get_dashboard({},'Uganda')['summary']
        self.assertEqual(summary['dues_total'],2)
        self.assertEqual(summary['no_due'],2)
        self.assertEqual(summary['payment_total'],4)
        self.assertEqual(backend.get_dashboard({})['summary']['dues_total'],2)
        self.assertIsNone(backend.get_dashboard({},'Kenya')['summary']['dues_total'])

    def test_export_includes_all_pages(self):
        self.load([payment(str(i).zfill(4)) for i in range(123)])
        first=backend.get_dashboard({});last=backend.get_dashboard({},'Uganda',2)
        self.assertEqual((len(first['records']),first['total'],first['has_more']),(50,123,True))
        self.assertEqual((len(last['records']),last['offset'],last['has_more']),(23,100,False))
        rows=list(csv.DictReader(io.StringIO(backend.export_missing({}).decode('utf-8-sig'))))
        self.assertEqual(len(rows),123);self.assertEqual(len({r['Record Id'] for r in rows}),123)

    def test_repeat_import_and_due_id_update(self):
        rows=[payment('p1',**{'Last Modified On':'2026-10-01T00:00:00'})]
        self.assertEqual(self.load(rows)['rows_applied'],1)
        self.assertEqual(self.load(rows)['rows_applied'],0)
        self.load([payment('p1',**{'Last Modified On':'2026-10-02T00:00:00','Union Due ID (New)':'DUE-1'})])
        self.assertEqual(backend.get_dashboard({})['summary']['no_due'],0)
        self.assertEqual(self.load(rows)['rows_applied'],0)
        self.assertEqual(backend.get_dashboard({})['summary']['with_due_id'],1)

    def test_failed_import_rolls_back_data_and_history(self):
        self.conn.execute("ALTER TABLE pg_temp.due_payments ADD CHECK (record_id <> 'blocked')")
        with self.assertRaises(psycopg.Error): self.load([payment('ok'),payment('blocked')])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM pg_temp.due_payments').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM pg_temp.import_batches').fetchone()[0],0)


if __name__=='__main__': unittest.main()
