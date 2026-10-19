"""Offer evidence can close and reopen only the affected swap cases."""
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql
from app import offer_tracking, counterpart_tracking
from app.dashboard_cache import cache_ready, refresh_cache
from app.import_csv import MAPS, import_file, dry_run
from app.import_service import inspect_csv
from app.reconciliation_backend import ReconciliationFilter


@unittest.skipUnless(os.environ.get('SPIRO_TEST_DSN'), 'Explicit isolated test database required')
class OfferTrackingTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ['SPIRO_TEST_DSN'])
        self.addCleanup(self.conn.close); self.addCleanup(self.conn.rollback)
        self.filters = ReconciliationFilter(country='Kenya', start_date='2032-02-01', end_date='2032-02-01')
        self.insert('wallets', zoho_record_id='offer_wallet', customer_crm_id='offer_customer',
                    customer_business_id='KE-OFFER', currency='KES')
        self.swap('offer_swap')
        self.allocation()
        self.consumption()

    def insert(self, table, **values):
        defaults = dict(country='Kenya', source_file='fixture', source_row_number=1, raw_record='{}')
        defaults.update(values)
        values = defaults
        self.conn.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
            sql.Identifier('reconciliation',table), sql.SQL(',').join(map(sql.Identifier,values)),
            sql.SQL(',').join(sql.Placeholder() for _ in values)), list(values.values()))

    def swap(self, record, **values):
        defaults = dict(zoho_record_id=record,customer_id='offer_customer',customer_name='=unsafe',
                        swap_reference='SW-'+record,swap_amount='0',status='SUCCESS',payment_method='OFFER_APPLIED',
                        created_on='2032-02-01 12:00:00')
        defaults.update(values)
        raw=json.dumps({'Net SOC':'80','Billable SOC':'0','Non-Billable SOC':'80'})
        self.insert('swap_transactions',raw_record=raw,**defaults)

    def allocation(self, record='offer_allocation', **values):
        defaults=dict(zoho_record_id=record,allocation_reference='OA-BUSINESS',customer_id='offer_customer',
            offer_id='offer_master_lookup',offer_type='SOC Voucher',frequency='One-Time',status='Expired',
            allocation_date='2032-01-01',expiry_date='2032-03-01',valid_from='2032-01-01',valid_till='2032-03-01',
            total_soc='500',consumed_soc='80')
        defaults.update(values);self.insert('offer_allocations',**defaults)

    def consumption(self, record='offer_consumption', **values):
        defaults=dict(zoho_record_id=record,consumption_reference='OC-'+record,allocation_reference='OA-BUSINESS',
            customer_business_id='KE-OFFER',currency='KES',transaction_type='Battery Swap',
            consumption_date='2032-02-01',swap_record_id='offer_swap',allocated_soc='500',consumed_soc='80',
            remaining_soc='420',allocated_discount='0',remaining_discount='0',consumed_discount='0')
        defaults.update(values);self.insert('offer_consumptions',**defaults)

    def row(self):
        return offer_tracking.page(self.conn,self.filters,group='all')['records'][0]

    def test_exact_links_close_swap_with_historical_expired_status(self):
        row=self.row()
        self.assertEqual(row['offer_result'],'covered')
        self.assertEqual(row['resolution_status'],'resolved_offer')
        self.assertEqual(row['allocations'][0]['allocation_reference'],'OA-BUSINESS')
        self.assertNotIn('raw_record',row['allocations'][0])
        result=counterpart_tracking.swap_review_summary(self.conn,self.filters)
        self.assertEqual((result['open_count'],result['resolved_offer_count']),(0,1))
        self.assertEqual(counterpart_tracking.page(self.conn,'open_swaps',self.filters)['records'],[])

    def test_missing_links_and_cross_country_refs_never_use_names_or_amounts(self):
        for column,value,reason in [('swap_record_id',None,'no_consumption'),
                                    ('country','Rwanda','no_consumption'),
                                    ('allocation_reference','offer_allocation','no_allocation')]:
            with self.subTest(column=column),self.conn.transaction(force_rollback=True):
                self.conn.execute(sql.SQL('UPDATE reconciliation.offer_consumptions SET {}=%s').format(sql.Identifier(column)),[value])
                self.assertEqual(self.row()['offer_result'],reason)

    def test_duplicate_consumptions_and_allocation_business_references_stay_open(self):
        with self.conn.transaction(force_rollback=True):
            self.consumption('extra')
            self.assertEqual(self.row()['offer_result'],'multiple_consumptions')
        with self.conn.transaction(force_rollback=True):
            self.allocation('extra_allocation')
            self.assertEqual(self.row()['offer_result'],'multiple_allocations')
        self.consumption('duplicate_business',swap_record_id=None,consumption_reference='OC-offer_consumption')
        self.assertEqual(self.row()['offer_result'],'duplicate_consumption_reference')

    def test_customer_and_currency_conflicts_stay_open(self):
        for table,column,value,reason in [
            ('offer_allocations','customer_id','other','allocation_customer_mismatch'),
            ('offer_consumptions','customer_business_id','other','consumption_customer_mismatch'),
            ('offer_consumptions','currency','USD','currency_mismatch'),
            ('wallets','currency',None,'currency_mismatch')]:
            with self.subTest(column=column),self.conn.transaction(force_rollback=True):
                self.conn.execute(sql.SQL('UPDATE reconciliation.{} SET {}=%s').format(sql.Identifier(table),sql.Identifier(column)),[value])
                self.assertEqual(self.row()['offer_result'],reason)
        self.insert('wallets',zoho_record_id='ambiguous_offer_wallet',customer_crm_id='offer_customer',customer_business_id='KE-OFFER',currency='USD')
        self.assertEqual(self.row()['offer_result'],'currency_mismatch')

    def test_expiry_window_and_consumption_dates_are_not_inferred(self):
        for table,column,value,reason in [
            ('offer_allocations','expiry_date','2032-01-31','conflicting_expiry_date'),
            ('offer_allocations','valid_till','2032-01-31','outside_validity_window'),
            ('offer_allocations','valid_from',None,'missing_validity_dates'),
            ('offer_consumptions','consumption_date','2032-01-31','consumption_date_mismatch')]:
            with self.subTest(column=column),self.conn.transaction(force_rollback=True):
                self.conn.execute(sql.SQL('UPDATE reconciliation.{} SET {}=%s').format(sql.Identifier(table),sql.Identifier(column)),[value])
                self.assertEqual(self.row()['offer_result'],reason)

    def test_partial_coverage_and_unpaid_charge_stay_open(self):
        for column,value,reason in [('swap_amount','10','uncovered_swap_charge'),('status','FAILED','swap_not_successful')]:
            with self.subTest(column=column),self.conn.transaction(force_rollback=True):
                self.conn.execute(sql.SQL('UPDATE reconciliation.swap_transactions SET {}=%s WHERE zoho_record_id=%s').format(sql.Identifier(column)),[value,'offer_swap'])
                self.assertEqual(self.row()['offer_result'],reason)
        for raw,reason in [({'Net SOC':'80','Billable SOC':'10','Non-Billable SOC':'70'},'uncovered_swap_charge'),
                           ({'Net SOC':'bad','Billable SOC':'0','Non-Billable SOC':'80'},'soc_coverage_mismatch')]:
            with self.subTest(raw=raw),self.conn.transaction(force_rollback=True):
                self.conn.execute('UPDATE reconciliation.swap_transactions SET raw_record=%s WHERE zoho_record_id=%s',[json.dumps(raw),'offer_swap'])
                self.assertEqual(self.row()['offer_result'],reason)

    def test_budget_includes_unlinked_consumptions_across_all_dates(self):
        self.consumption('old_unlinked',swap_record_id=None,consumption_date='2020-01-01',consumed_soc='450')
        self.assertEqual(self.row()['offer_result'],'soc_budget_mismatch')
        self.assertEqual(offer_tracking.summary(self.conn,self.filters)['needs_review_count'],1)

    def test_discount_repeated_frequency_and_bad_status_are_not_auto_cleared(self):
        for table,column,value,reason in [
            ('offer_consumptions','consumed_discount','1','unsupported_discount_terms'),
            ('offer_allocations','frequency','Daily','unsupported_offer_terms'),
            ('offer_allocations','status','Cancelled','allocation_status_needs_review'),
            ('offer_allocations','offer_id',None,'missing_offer_reference')]:
            with self.subTest(column=column),self.conn.transaction(force_rollback=True):
                self.conn.execute(sql.SQL('UPDATE reconciliation.{} SET {}=%s').format(sql.Identifier(table),sql.Identifier(column)),[value])
                self.assertEqual(self.row()['offer_result'],reason)

    def test_wallet_attached_swaps_are_not_counted_as_offer_resolutions(self):
        self.conn.execute("UPDATE reconciliation.swap_transactions SET transaction_id='offer_tx' WHERE zoho_record_id='offer_swap'")
        self.insert('wallet_transactions',zoho_record_id='offer_debit',transaction_id='offer_tx',transaction_type='Debit',status='Committed',settled_against='Swap',amount='1')
        self.assertEqual(self.row()['resolution_status'],'wallet_attached')
        self.assertEqual(offer_tracking.summary(self.conn,self.filters)['resolved_count'],0)

    def test_edit_reopens_cached_review_without_rebuilding_wallet_cache(self):
        refresh_cache(self.conn)
        before=counterpart_tracking.swap_review_summary(self.conn,self.filters,cached=True)
        self.assertEqual(before['resolved_offer_count'],1)
        self.conn.execute("UPDATE reconciliation.offer_allocations SET expiry_date='2032-01-31' WHERE zoho_record_id='offer_allocation'")
        self.assertTrue(cache_ready(self.conn))
        cached=counterpart_tracking.swap_review_summary(self.conn,self.filters,cached=True)
        self.assertEqual(cached,counterpart_tracking.swap_review_summary(self.conn,self.filters))
        self.assertEqual(cached['open_count'],1)
        body=counterpart_tracking.export(self.conn,'open_swaps',self.filters,cached=True)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))),1)

    def test_page_cursor_filters_and_evidence_export(self):
        self.swap('offer_swap_b')
        first=offer_tracking.page(self.conn,self.filters,group='all',limit=1)
        second=offer_tracking.page(self.conn,self.filters,group='all',limit=1,cursor=first['next_cursor'])
        self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
        self.assertEqual(second['records'][0]['offer_result'],'no_consumption')
        self.assertEqual(offer_tracking.page(self.conn,ReconciliationFilter(country='Rwanda'))['records'],[])
        body=offer_tracking.export(self.conn,self.filters,group='resolved')
        rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['customer_name'],"'=unsafe")
        self.assertEqual(json.loads(rows[0]['consumptions'])[0]['swap_record_id'],'offer_swap')
        for options in ({'limit':False},{'cursor':' '},{'group':'unknown'}):
            with self.assertRaises(ValueError):offer_tracking.page(self.conn,self.filters,**options)
        with self.assertRaises(ValueError):offer_tracking.summary(self.conn,ReconciliationFilter(status='matched'))
        with patch.object(offer_tracking,'MAX_EXPORT',1),self.assertRaises(ValueError):
            offer_tracking.export(self.conn,self.filters,group='all')

    def test_offer_csv_detection_upsert_versions_and_failed_file_rollback(self):
        for dataset,table in [('offer_allocation','offer_allocations'),('offer_consumption','offer_consumptions')]:
            mapping=MAPS[dataset]
            row={header:'' for _,header,_ in mapping}
            row.update({'Record Id':'csv_'+dataset,'Country':'Kenya','Created On':'2032-02-01T12:00:00',
                        'Change Log Time':'2032-02-01T13:00:00'})
            row['Last Modified On' if dataset=='offer_allocation' else 'Modified Time']='2032-02-01T13:00:00'
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'offer.csv'
                def write(rows):
                    with path.open('w',encoding='utf-8',newline='') as f:
                        writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
                write([row]);preview=inspect_csv(path)
                self.assertEqual(preview['dataset'],dataset)
                if dataset=='offer_consumption':self.assertEqual(preview['missing_swap_lookup_rows'],1)
                def load():
                    result=import_file(self.conn,path,dataset,sql)
                    # The test's rollback-only outer transaction keeps ON COMMIT
                    # temporary tables alive; production imports own their commit.
                    self.conn.execute('DROP TABLE pg_temp.import_stage')
                    return result
                with patch('sys.stdout',new=io.StringIO()):
                    dry_run(path,dataset)
                    self.assertEqual(load()['rows_applied'],1)
                    self.assertEqual(load()['rows_applied'],0)
                    older=dict(row,Country='Rwanda');older['Change Log Time']='2030-01-01T12:00:00'
                    older['Last Modified On' if dataset=='offer_allocation' else 'Modified Time']='2030-01-01T12:00:00'
                    write([older]);self.assertEqual(load()['rows_applied'],0)
                    invalid=dict(row);invalid['Record Id']=''
                    write([dict(row,Country='Rwanda'),invalid])
                    with self.assertRaises(ValueError):import_file(self.conn,path,dataset,sql)
                self.assertEqual(self.conn.execute(sql.SQL('SELECT country FROM reconciliation.{} WHERE zoho_record_id=%s').format(sql.Identifier(table)),['csv_'+dataset]).fetchone()[0],'Kenya')
