"""Swap/payment ID-presence checks; SQL fixtures are read-only CTEs."""
import os
import unittest
import psycopg
from payments_swap_reconciliation import (SUMMARY_SQL, PAYMENT_MISSING_SQL, SWAP_MISSING_SQL,
    PAYMENT_MISSING_COUNT_SQL, SWAP_MISSING_COUNT_SQL, get_missing_transactions)

FIXTURE = """
WITH swap_payments AS (
 SELECT * FROM (VALUES
 ('p1','001','pay1','Rider','10.50','KES','Paid','MPESA','2026-10-07'),
 ('p2','B','pay2','Rider','20','KES','Paid','MPESA','2026-10-07'),
 ('p3','C','pay3','Rider','30','KES','Paid','MPESA','2026-10-07'),
 ('p4',NULL,'pay4','Rider','40','KES','Paid','MPESA','2026-10-07'),
 ('p5','  ','pay5','Rider','50','KES','Paid','MPESA','2026-10-07'),
 ('p6','D','pay6','Rider','60','KES','Paid','MPESA','2026-10-07'),
 ('p7','D','pay7','Rider','70','KES','Pending','MPESA','2026-10-07'),
 ('p8','E','pay8','Rider','80','KES','Failed','MPESA','2026-10-07'),
 ('p9','F','pay9','Rider','90','KES','Pending','MPESA','2026-10-07'),
 ('p10','G','pay10','Rider','100','KES',NULL,'MPESA','2026-10-07'),
 ('p11','H','pay11','Rider','110','KES','','MPESA','2026-10-07')
 ) p("Record Id","Atlas Transaction ID","Payment Transaction No","Customer Name","Amount","Currency","Payment Status","Payment Operator","Transaction Date")
), swap_transactions AS (
 SELECT * FROM (VALUES
 ('s1','001','swap1','Rider','10.50','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s2','001','swap2','Rider','10.50','FAILED','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s3','D','swap3','Rider','60','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s4','E','swap4','Rider','80','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s5',NULL,'swap5','Rider','90','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s6','  ','swap6','Rider','100','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s7','B','swap7','Rider','20','SUCCESS','WALLET','Kenya','2026-10-07'),
 ('s8','C','swap8','Rider','30','SUCCESS',NULL,'Kenya','2026-10-07'),
 ('s9','F','swap9','Rider','90','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s10','G','swap10','Rider','100','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07'),
 ('s11','H','swap11','Rider','110','SUCCESS','PAYMENT_OPERATOR','Kenya','2026-10-07')
 ) s("Record Id","Transaction ID","Battery Swapping ID","Customer Name","Swap Amount","Status","Pay Method","Country","Created On")
)
"""


class PaginationValidationTests(unittest.TestCase):
    def test_invalid_arguments_are_rejected_before_connecting(self):
        for kwargs in ({'limit':0},{'limit':True},{'limit':1001},{'offset':-1},
                       {'offset':False},{'direction':'unknown'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                get_missing_transactions({},**kwargs)


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit test connection required; fixtures use read-only CTEs')
class SwapPaymentSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn=psycopg.connect(os.environ['SPIRO_TEST_DSN'])
        cls.conn.execute('SET TRANSACTION READ ONLY')

    @classmethod
    def tearDownClass(cls): cls.conn.close()

    def query(self,statement,params=None):
        return self.conn.execute(FIXTURE+statement.replace('swap_reconciliation.',''),params)

    def test_summary_counts_records_without_join_multiplication(self):
        self.assertEqual(self.query(SUMMARY_SQL).fetchone(),(6,9,2,6))

    def test_operator_swaps_without_payment_include_blank_ids(self):
        rows=self.query(SWAP_MISSING_SQL,(50,0)).fetchall()
        self.assertEqual({r[0] for r in rows},{'s4','s5','s6','s9','s10','s11'})
        self.assertEqual({r[0]:r[-1] for r in rows},
                         {'s4':'no_matching_payment','s5':'missing_transaction_id','s6':'missing_transaction_id',
                          's9':'no_matching_payment','s10':'no_matching_payment','s11':'no_matching_payment'})
        self.assertEqual(self.query(SWAP_MISSING_COUNT_SQL).fetchone()[0],len(rows))

    def test_payment_does_not_match_wallet_or_unclassified_swaps(self):
        rows=self.query(PAYMENT_MISSING_SQL,(50,0)).fetchall()
        self.assertEqual({r[0] for r in rows},{'p2','p3','p4','p5'})
        self.assertEqual(self.query(PAYMENT_MISSING_COUNT_SQL).fetchone()[0],len(rows))

    def test_failed_pending_and_blank_status_do_not_match_swaps(self):
        rows=self.query(SWAP_MISSING_SQL,(50,0)).fetchall()
        ids={row[1] for row in rows}
        self.assertTrue({'E','F','G','H'} <= ids)
        self.assertNotIn('D',ids)  # D has a Paid payment alongside a Pending one.
        missing_payments=self.query(PAYMENT_MISSING_SQL,(50,0)).fetchall()
        self.assertTrue(all(row[6]=='Paid' for row in missing_payments))
        self.assertTrue({row[0] for row in missing_payments}.isdisjoint({'p7','p8','p9','p10','p11'}))

    def test_pagination_matches_complete_results(self):
        for query in (PAYMENT_MISSING_SQL,SWAP_MISSING_SQL):
            expected=[r[0] for r in self.query(query,(50,0)).fetchall()]
            actual=[]
            for offset in range(0,len(expected),2):
                actual.extend(r[0] for r in self.query(query,(2,offset)).fetchall())
            self.assertEqual(actual,expected)


if __name__=='__main__':unittest.main()
