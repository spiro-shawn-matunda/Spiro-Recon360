"""Stream Zoho CSV exports into existing Spiro tables using the project's .env.

No API credentials are needed. Run --dry-run first. Source fields are preserved
in raw_record except Wallet Pin. Primary keys are exact exported Zoho Record Ids.
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

COMMON = [('zoho_record_id', 'Record Id', 'required'), ('transaction_id', 'Transaction ID', 'text'),
          ('source_record_id', 'RecordID', 'text'), ('country', 'Country', 'text'),
          ('status', 'Status', 'text'), ('created_on', 'Created On', 'timestamp'),
          ('source_change_at', 'Change Log Time', 'timestamp')]
MAPS = {
    'wallet': COMMON + [('reference', 'Reference', 'text'), ('wallet_id', 'Wallet.id', 'text'),
        ('wallet_label', 'Wallet', 'text'), ('transaction_type', 'Type', 'text'),
        ('settled_against', 'Settled Against', 'text'), ('narration', 'Narration', 'text'),
        ('amount', 'Amount', 'decimal'), ('opening_balance', 'Opening Balance', 'decimal'),
        ('available_balance', 'Available Balance', 'decimal'), ('hold_amount', 'Hold Amount', 'decimal'),
        ('crm_created_at', 'Created Time', 'timestamp'), ('source_modified_at', 'Modified Time', 'timestamp')],
    'swap': COMMON + [('swap_reference', 'Battery Swapping ID', 'text'), ('customer_id', 'Customer.id', 'text'),
        ('customer_label', 'Customer', 'text'), ('customer_name', 'Customer Name', 'text'),
        ('vehicle_id', 'Vehicle ID', 'text'), ('vehicle_registration', 'Vehicle Reg No', 'text'),
        ('station_id', 'Swap Station.id', 'text'), ('station_label', 'Swap Station', 'text'),
        ('station_code', 'Station', 'text'), ('in_battery_oem', 'In Battery OEM No', 'text'),
        ('out_battery_oem', 'Out Battery OEM No', 'text'), ('in_battery_serial', 'In Battery Serial No', 'text'),
        ('out_battery_serial', 'Out Battery Serial No', 'text'), ('city', 'City', 'text'),
        ('payment_method', 'Pay Method', 'text'), ('swap_amount', 'Swap Amount', 'decimal'),
        ('swap_date', 'Swap Date', 'date'), ('source_modified_at', 'Last Modified On', 'timestamp')],
    'wallet_master': [('zoho_record_id', 'Record Id', 'required'),
        ('wallet_code', 'Wallet ID', 'text'), ('source_record_id', 'RecordID', 'text'),
        ('customer_crm_id', 'Customer.id', 'text'), ('customer_label', 'Customer', 'text'),
        ('customer_business_id', 'Customer ID', 'text'), ('customer_name', 'Customer Name', 'text'),
        ('country', 'Country', 'text'), ('country_code', 'Country Code', 'text'),
        ('currency', 'Currency', 'text'), ('category', 'Category', 'text'),
        ('opening_balance', 'Opening Balance', 'decimal'), ('hold_amount', 'Hold Amount', 'decimal'),
        ('available_balance', 'Available Balance', 'decimal'), ('created_on', 'Created On', 'timestamp'),
        ('source_modified_at', 'Modified Time', 'timestamp'), ('source_change_at', 'Change Log Time', 'timestamp')],
    'due': [('zoho_record_id', 'Record Id', 'required'), ('due_reference', 'Due ID', 'text'),
        ('transaction_id', 'Transaction ID', 'text'), ('country', 'Country', 'text'),
        ('currency', 'Currency', 'text'), ('status', 'Status', 'text'),
        ('customer_id', 'Customer.id', 'text'), ('customer_label', 'Customer', 'text'),
        ('customer_name', 'Customer Name', 'text'), ('vehicle_registration', 'Vehicle Reg No', 'text'),
        ('due_type', 'Due Type', 'text'), ('payment_mode', 'Payment Mode', 'text'),
        ('due_amount', 'Due Amount', 'decimal'), ('original_due_amount', 'Original Due Amount', 'decimal'),
        ('advance_applied_amount', 'Advance Applied Amount', 'decimal'),
        ('swap_record_id', 'Swapping Transaction.id', 'text'), ('swap_reference', 'Swapping Transaction', 'text'),
        ('due_source_id', 'Due Source ID', 'text'), ('payment_transaction_id', 'Payment Transaction.id', 'text'),
        ('due_creation_date', 'Due Creation Date', 'date'), ('due_settlement_date', 'Due Settlement Date', 'date'),
        ('created_on', 'Created On', 'timestamp'), ('source_modified_at', 'Last Modified On', 'timestamp'),
        ('source_change_at', 'Change Log Time', 'timestamp')]
}
MAPS.update({
    'offer_allocation': [
        ('zoho_record_id', 'Record Id', 'required'),
        ('allocation_reference', 'Offer Allocation ID', 'text'),
        ('country', 'Country', 'text'),
        ('customer_id', 'Customer Name.id', 'text'),
        ('offer_id', 'Assigned Offer.id', 'text'),
        ('offer_reference', 'Assigned Offer', 'text'),
        ('offer_type', 'Offer Type', 'text'),
        ('status', 'Assigned Offer Status', 'text'),
        ('frequency', 'Frequency', 'text'),
        ('allocation_date', 'Allocation Date', 'date'),
        ('expiry_date', 'Expiry Date', 'date'),
        ('valid_from', 'Allocated Offer Valid From', 'date'),
        ('valid_till', 'Allocated Offer Valid Till', 'date'),
        ('total_soc', 'Total Offer SOC', 'decimal'),
        ('consumed_soc', 'Consumed Offer SOC', 'decimal'),
        ('total_discount', 'Total Discount Price', 'decimal'),
        ('consumed_discount', 'Consumed Discount Price', 'decimal'),
        ('total_price', 'Total Offer Price', 'decimal'),
        ('consumed_price', 'Consumed Offer Price', 'decimal'),
        ('created_on', 'Created On', 'timestamp'),
        ('source_modified_at', 'Last Modified On', 'timestamp'),
        ('source_change_at', 'Change Log Time', 'timestamp'),
    ],
    'offer_consumption': [
        ('zoho_record_id', 'Record Id', 'required'),
        ('consumption_reference', 'Offer Consumption_ID', 'text'),
        ('country', 'Country', 'text'),
        ('currency', 'Currency', 'text'),
        ('customer_business_id', 'Customer ID', 'text'),
        ('allocation_reference', 'Offer Allocation ID', 'text'),
        ('offer_name', 'Offer Name', 'text'),
        ('transaction_type', 'Transaction Type', 'text'),
        ('consumption_date', 'Offer Consumption Date', 'date'),
        ('swap_record_id', 'Swap Transaction ID.id', 'text'),
        ('swap_reference', 'Swap Transaction ID', 'text'),
        ('allocated_soc', 'Allocated SOC', 'decimal'),
        ('remaining_soc', 'Remaining SOC', 'decimal'),
        ('consumed_soc', 'Consumed SOC', 'decimal'),
        ('allocated_discount', 'Allocated Discount Price', 'decimal'),
        ('remaining_discount', 'Remaining Discount Price', 'decimal'),
        ('consumed_discount', 'Consumed Discount Price', 'decimal'),
        ('created_on', 'Created On', 'timestamp'),
        ('source_modified_at', 'Modified Time', 'timestamp'),
        ('source_change_at', 'Change Log Time', 'timestamp'),
    ],
})
TABLES = {'wallet': 'wallet_transactions', 'swap': 'swap_transactions', 'wallet_master': 'wallets', 'due': 'dues',
          'offer_allocation': 'offer_allocations', 'offer_consumption': 'offer_consumptions'}

def parse_value(value, kind, field):
    # Preserve nonblank identifiers exactly; never pass through a spreadsheet float.
    if not value or not value.strip():
        if kind == 'required': raise ValueError(f'Missing required {field}')
        return None
    if kind in ('text', 'required'): return value
    if kind == 'decimal':
        result = Decimal(value)
        if not result.is_finite(): raise ValueError(f'Non-finite decimal in {field}')
        return result
    if kind == 'date': return date.fromisoformat(value)
    if kind == 'timestamp':
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            raise ValueError(f'{field} has a timezone offset; these tables are for naive CSV timestamps')
        return result
    raise ValueError(f'Unknown field type {kind}')

def read_rows(path, dataset):
    mapping = MAPS[dataset]
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        required = {field for _, field, _ in mapping}
        missing = required - set(reader.fieldnames or [])
        if missing: raise ValueError('CSV is missing expected headers: ' + ', '.join(sorted(missing)))
        for n, row in enumerate(reader, start=1):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'{path.name}: record {n} has the wrong number of fields')
            try:
                values = {col: parse_value(row[field], kind, field) for col, field, kind in mapping}
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f'{path.name}: record {n}: {exc}') from None
            safe_raw = {key: value for key, value in row.items() if key != 'Wallet Pin'}
            values.update(source_file=path.name, source_row_number=n, raw_record=json.dumps(safe_raw, ensure_ascii=False))
            yield values

def dry_run(path, dataset):
    n = 0; ids = set(); txids = set(); duplicates = 0; duplicate_txids = 0
    missing_txids = 0; statuses = Counter(); minimum = maximum = None
    amount_total = Decimal(0)
    for row in read_rows(path, dataset):
        n += 1
        key = row['zoho_record_id']
        if key in ids: duplicates += 1
        ids.add(key)
        tx = row.get('transaction_id')
        if tx is None and dataset in ('wallet', 'swap', 'due'): missing_txids += 1
        elif tx in txids: duplicate_txids += 1
        if tx is not None: txids.add(tx)
        statuses[row.get('status', row.get('category'))] += 1
        dt = row['created_on']
        if dt is not None:
            minimum = dt if minimum is None else min(minimum, dt)
            maximum = dt if maximum is None else max(maximum, dt)
        amount_field = {'wallet': 'amount', 'swap': 'swap_amount', 'wallet_master': 'available_balance', 'due': 'due_amount', 'offer_allocation': 'total_soc', 'offer_consumption': 'consumed_soc'}[dataset]
        amount = row[amount_field]
        if amount is not None: amount_total += amount
    print(json.dumps({'dataset': dataset, 'file': path.name, 'rows': n,
        'distinct_record_ids': len(ids), 'duplicate_record_id_rows': duplicates,
        'missing_transaction_ids': missing_txids, 'duplicate_transaction_id_rows': duplicate_txids,
        'statuses': dict(statuses), 'min_created_on': str(minimum), 'max_created_on': str(maximum),
        'amount_field': amount_field,
        'amount_total_in_source_units': str(amount_total)}, indent=2))

def import_file(conn, path, dataset, sql, *, progress=None):
    columns = [col for col, _, _ in MAPS[dataset]] + ['source_file', 'source_row_number', 'raw_record']
    identifiers = sql.SQL(', ').join(map(sql.Identifier, columns))
    table = sql.Identifier('reconciliation', TABLES[dataset])
    checksum = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): checksum.update(block)
    # A file is an atomic unit. A failed parse/COPY/merge leaves this dataset unchanged.
    with conn.transaction():
        with conn.cursor() as cur:
            # Serialize imports of the same dataset; concurrent wallet/swap imports are allowed.
            cur.execute('SELECT pg_advisory_xact_lock(%s)', ({'wallet':18701, 'swap':18702, 'wallet_master':18703, 'due':18704, 'offer_allocation':18705, 'offer_consumption':18706}[dataset],))
            cur.execute(sql.SQL('CREATE TEMP TABLE import_stage (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP').format(table))
            n = 0; minimum = maximum = None
            with cur.copy(sql.SQL('COPY import_stage ({}) FROM STDIN').format(identifiers)) as copy:
                for row in read_rows(path, dataset):
                    copy.write_row([row[c] for c in columns])
                    n += 1
                    dt = row['created_on']
                    if dt is not None:
                        minimum = dt if minimum is None else min(minimum, dt)
                        maximum = dt if maximum is None else max(maximum, dt)
                    if n % 10000 == 0 and progress: progress(n, 'staging')
                    if n % 50000 == 0: print(f'{dataset}: staged {n:,} rows', flush=True)
            if progress: progress(n, 'merging')
            # Select the latest source version if an export repeats a Record Id.
            assignments = sql.SQL(', ').join(sql.SQL('{} = EXCLUDED.{}').format(sql.Identifier(c), sql.Identifier(c))
                for c in columns if c != 'zoho_record_id')
            statement = sql.SQL('''
                INSERT INTO {} AS existing ({})
                SELECT {} FROM (
                    SELECT DISTINCT ON (zoho_record_id) * FROM import_stage
                    ORDER BY zoho_record_id,
                             coalesce(greatest(source_modified_at, source_change_at), '-infinity'::timestamp) DESC,
                             source_row_number DESC
                ) newest WHERE true
                ON CONFLICT (zoho_record_id) DO UPDATE SET {}, imported_at = now()
                WHERE coalesce(greatest(EXCLUDED.source_modified_at, EXCLUDED.source_change_at), '-infinity'::timestamp)
                   >= coalesce(greatest(existing.source_modified_at, existing.source_change_at), '-infinity'::timestamp)
                  AND existing.raw_record IS DISTINCT FROM EXCLUDED.raw_record
            ''').format(table, identifiers, identifiers, assignments)
            cur.execute(statement)
            applied = cur.rowcount
            cur.execute('''INSERT INTO reconciliation.import_batches
                (dataset, source_file, sha256, rows_read, rows_applied, min_created_on, max_created_on)
                VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                (dataset, path.name, checksum.hexdigest(), n, applied, minimum, maximum))
    print(f'{dataset}: {n:,} rows read; {applied:,} records inserted/updated; committed.', flush=True)
    return {'rows_read': n, 'rows_applied': applied,
            'min_created_on': minimum.isoformat() if minimum else None,
            'max_created_on': maximum.isoformat() if maximum else None}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wallet', type=Path, action='append', default=[], help='Wallet CSV; repeat for more batches')
    parser.add_argument('--swap', type=Path, action='append', default=[], help='Swap CSV; repeat for more batches')
    parser.add_argument('--wallet-master', type=Path, action='append', default=[], help='Wallets module CSV with customer mappings')
    parser.add_argument('--due', type=Path, action='append', default=[], help='Dues module CSV with swap lookups and settlement status')
    parser.add_argument('--offer-allocation', type=Path, action='append', default=[], help='Offer Allocation CSV')
    parser.add_argument('--offer-consumption', type=Path, action='append', default=[], help='Offer Consumptions CSV')
    parser.add_argument('--dry-run', action='store_true', help='Validate files without connecting or writing to PostgreSQL')
    args = parser.parse_args()
    files = [('wallet_master', p) for p in args.wallet_master] + [('wallet', p) for p in args.wallet] + [('swap', p) for p in args.swap] + [('due', p) for p in args.due] + [('offer_allocation', p) for p in args.offer_allocation] + [('offer_consumption', p) for p in args.offer_consumption]
    if not files: parser.error('Provide wallet, swap, due or offer CSV files')
    for _, p in files:
        if not p.is_file(): parser.error(f'File not found: {p}')
    if args.dry_run:
        for dataset, p in files: dry_run(p, dataset)
        return
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise SystemExit('Install the driver first: python -m pip install -r requirements.txt')
    from .db_config import read_database_config
    with psycopg.connect(**read_database_config(), connect_timeout=15, autocommit=True) as conn:
        for dataset, p in files: import_file(conn, p, dataset, sql)
        for dataset in {dataset for dataset, _ in files}:
            conn.execute(sql.SQL('ANALYZE {}').format(sql.Identifier('reconciliation', TABLES[dataset])))
        with conn.cursor() as cur:
            cur.execute('''SELECT country, reconciliation_status, count(*) FROM reconciliation.wallet_swap_review
                           GROUP BY country, reconciliation_status ORDER BY country, reconciliation_status''')
            for country, status, count in cur.fetchall(): print(f'{country} / {status}: {count:,}')

if __name__ == '__main__':
    try: main()
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr); sys.exit(1)
