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


if __name__=='__main__': unittest.main()
