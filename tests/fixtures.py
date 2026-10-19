"""Synthetic integration fixtures; never read project .env."""
from datetime import datetime
from decimal import Decimal
import psycopg


def load_fixtures(conn):
    conn.execute("""INSERT INTO reconciliation.wallets
        (zoho_record_id,country,currency,customer_crm_id,customer_business_id,customer_name,source_file,source_row_number,raw_record)
        VALUES ('wallet_ke','Kenya','KES','customer_ke','KE-1','Test Kenya rider','fixture',1,'{}'),
               ('wallet_rw','Rwanda','RWF','customer_rw','RW-1','Test Rwanda rider','fixture',2,'{}')""")

    def wallet(record_id, *, tx=None, amount='120.10', country='Kenya', wallet_id='wallet_ke', created='2026-09-23 12:00:00', status='Committed',kind='Debit',settled='Swap'):
        conn.execute('''INSERT INTO reconciliation.wallet_transactions
            (zoho_record_id,transaction_id,wallet_id,country,transaction_type,status,settled_against,amount,created_on,source_file,source_row_number,raw_record)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'fixture',1,'{}')''',
            (record_id,tx if tx is not None else record_id,wallet_id,country,kind,status,settled,Decimal(amount) if amount is not None else None,datetime.fromisoformat(created) if created else None))

    def swap(record_id, *, tx=None, country='Kenya',status='SUCCESS',amount='120.10',customer='customer_ke',method='WALLET',created='2026-09-23 12:00:02'):
        conn.execute('''INSERT INTO reconciliation.swap_transactions
            (zoho_record_id,transaction_id,country,status,swap_amount,customer_id,payment_method,created_on,source_file,source_row_number,raw_record)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'fixture',1,'{}')''',
            ('swap_'+record_id,tx or record_id,country,status,Decimal(amount) if amount is not None else None,customer,method,datetime.fromisoformat(created) if created else None))

    wallet('matched');swap('matched')
    wallet('mismatch');swap('mismatch',amount='120.11')
    wallet('failed');swap('failed',status='FAILED')
    wallet('missing_tx',tx='');conn.execute("UPDATE reconciliation.wallet_transactions SET transaction_id=NULL WHERE zoho_record_id='missing_tx'")
    wallet('duplicate_wallet_a',tx='duplicate_wallet')
    wallet('duplicate_wallet_b',tx='duplicate_wallet',created='2026-09-24 12:00:00');swap('duplicate_wallet')
    wallet('duplicate_success');swap('duplicate_success');swap('duplicate_success_2',tx='duplicate_success')
    wallet('missing_amount',amount=None);swap('missing_amount')
    wallet('offer');swap('offer',method='OFFER_APPLIED')
    wallet('customer_diff');swap('customer_diff',customer='different_customer')
    wallet('unmapped',wallet_id='missing_wallet');swap('unmapped')
    wallet('missing_customer');swap('missing_customer',customer=None)
    wallet('missing_timestamp',created=None);swap('missing_timestamp')
    wallet('after_cutoff',created='2026-09-25 12:00:00')
    wallet('shared_ke',tx='shared');swap('shared_ke',tx='shared')
    wallet('shared_rw',tx='shared',country='Rwanda',wallet_id='wallet_rw');swap('shared_rw',tx='shared',country='Rwanda',customer='customer_rw',created=None)
    wallet('no_swap_country',country='Rwanda',wallet_id='wallet_rw')
    wallet('end_of_day',created='2026-09-23 23:59:59.999999');swap('end_of_day')
    wallet('next_day',created='2026-09-24 00:00:00');swap('next_day',created='2026-09-24 00:00:02')
    wallet('credit_excluded',kind='Credit')
    wallet('rental_excluded',settled='Rental')
    wallet('uncommitted_excluded',status='Pending')
    for table in ['wallets','wallet_transactions','swap_transactions']:
        conn.execute(psycopg.sql.SQL('ANALYZE {}').format(psycopg.sql.Identifier('reconciliation',table)))
