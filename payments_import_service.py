"""CSV validation/imports for the independent payments_reconciliation schema."""
import csv
import hashlib
import json
import re
from collections import Counter
from decimal import InvalidOperation

import psycopg
from psycopg import sql
from import_csv import parse_value

SCHEMA = 'payments_reconciliation'
TABLES = {'atlas': 'atlas_transactions', 'wallet': 'wallet_transactions'}
DATASETS = {'atlas': 'Atlas payments', 'wallet': 'Wallet transactions'}
COMMON = {'record_id': 'required', 'country': 'text', 'amount': 'decimal',
          'created_on': 'timestamp', 'source_modified_at': 'timestamp'}
FIELDS = {
    'atlas': {**COMMON, 'atlas_transaction_id': 'text', 'payment_transaction_no': 'text',
              'customer_name': 'text', 'currency': 'text', 'payment_status': 'text',
              'payment_operator': 'text', 'transaction_date': 'timestamp', 'settled_against': 'text'},
    'wallet': {**COMMON, 'reference': 'text', 'transaction_id': 'text', 'wallet_id': 'text',
               'type': 'text', 'status': 'text', 'settled_against': 'text'},
}


def header_name(value):
    name = re.sub(r'[^a-z0-9]+', '_', value.strip().lower()).strip('_')
    return {'modified_time': 'source_modified_at', 'last_modified_on': 'source_modified_at',
            'recordid': 'record_id_2'}.get(name, name)


def layout(headers):
    if not headers or len(headers) != len(set(headers)):
        raise ValueError('CSV needs unique column headers.')
    normalized = [header_name(h) for h in headers]
    datasets = [d for d, key in [('atlas', 'atlas_transaction_id'), ('wallet', 'reference')]
                if key in normalized and (d != 'wallet' or 'transaction_id' in normalized)]
    if len(datasets) != 1 or 'record_id' not in normalized:
        raise ValueError('Choose an Atlas Payments export with Record Id and Atlas Transaction ID, or a Wallet Transactions export with Record Id, Reference and Transaction ID. Database-style headers are also accepted.')
    dataset = datasets[0]
    if 'settled_against' not in normalized:
        raise ValueError('Include the Settled Against column in the CSV export. This page compares only Wallet Recharge transactions.')
    mapping = {}
    for header, name in zip(headers, normalized):
        if name not in FIELDS[dataset]:
            continue
        if name in mapping:
            # Full Zoho exports can have both Modified Time and Last Modified On.
            if name == 'source_modified_at':
                continue
            raise ValueError('Multiple CSV headers map to ' + name)
        mapping[name] = header
    return dataset, mapping


def read_rows(path, dataset=None):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        detected, mapping = layout(reader.fieldnames)
        if dataset is not None and dataset != detected:
            raise ValueError('The CSV module changed after validation.')
        for number, raw in enumerate(reader, 1):
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError(f'Record {number} has the wrong number of fields.')
            try:
                row = {col: parse_value(raw[header], FIELDS[detected][col], header)
                       for col, header in mapping.items()}
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f'Record {number}: {exc}') from None
            # Keep exported identifiers exact, including leading zeros.
            row.update(source_file=path.name, source_row_number=number,
                       raw_record=json.dumps({k: v for k, v in raw.items()
                                              if header_name(k) != 'wallet_pin'}, ensure_ascii=False))
            yield row


def inspect_csv(path, progress=None):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        dataset, _ = layout(next(csv.reader(stream), []))
    countries = Counter()
    ids = set()
    count = duplicates = 0
    minimum = maximum = None
    for row in read_rows(path, dataset):
        count += 1
        duplicates += row['record_id'] in ids
        ids.add(row['record_id'])
        countries[row.get('country') or 'Unknown country'] += 1
        created = row.get('created_on') or row.get('transaction_date')
        if created is not None:
            minimum = min(minimum, created) if minimum else created
            maximum = max(maximum, created) if maximum else created
        if progress and count % 10000 == 0:
            progress(count, 'validating')
    if not count:
        raise ValueError('This CSV has headers but no data records.')
    return {'dataset': dataset, 'module': DATASETS[dataset], 'rows': count,
            'distinct_record_ids': len(ids), 'duplicate_record_id_rows': duplicates,
            'countries': dict(countries), 'min_created_on': minimum.isoformat() if minimum else None,
            'max_created_on': maximum.isoformat() if maximum else None,
            'export_cap_warning': count >= 200000}


def import_file(conn, path, dataset, sql_module=None, *, progress=None):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        detected, mapping = layout(next(csv.reader(stream), []))
    if detected != dataset:
        raise ValueError('The CSV module changed after validation.')
    columns = list(mapping) + ['source_file', 'source_row_number', 'raw_record']
    names = sql.SQL(', ').join(map(sql.Identifier, columns))
    table = sql.Identifier(SCHEMA, TABLES[dataset])
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)', (18801 if dataset == 'atlas' else 18802,))
            cur.execute(sql.SQL('CREATE TEMP TABLE payments_import_stage (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP').format(table))
            count = 0
            minimum = maximum = None
            with cur.copy(sql.SQL('COPY payments_import_stage ({}) FROM STDIN').format(names)) as copy:
                for row in read_rows(path, dataset):
                    copy.write_row([row[col] for col in columns])
                    count += 1
                    created = row.get('created_on') or row.get('transaction_date')
                    if created is not None:
                        minimum = min(minimum, created) if minimum else created
                        maximum = max(maximum, created) if maximum else created
                    if progress and count % 10000 == 0:
                        progress(count, 'staging')
            assignments = sql.SQL(', ').join(sql.SQL('{} = EXCLUDED.{}').format(sql.Identifier(c), sql.Identifier(c))
                                           for c in columns if c != 'record_id')
            if progress:
                progress(count, 'merging')
            cur.execute(sql.SQL("""
                INSERT INTO {} AS existing ({})
                SELECT {} FROM (
                    SELECT DISTINCT ON (record_id) * FROM payments_import_stage
                    ORDER BY record_id, coalesce(source_modified_at, '-infinity'::timestamp) DESC,
                             source_row_number DESC
                ) newest WHERE true
                ON CONFLICT (record_id) DO UPDATE SET {}, imported_at = now()
                WHERE coalesce(EXCLUDED.source_modified_at, '-infinity'::timestamp)
                   >= coalesce(existing.source_modified_at, '-infinity'::timestamp)
                  AND existing.raw_record IS DISTINCT FROM EXCLUDED.raw_record
            """).format(table, names, names, assignments))
            applied = cur.rowcount
            cur.execute(sql.SQL("""INSERT INTO {}.import_batches
                (dataset,source_file,sha256,rows_read,rows_applied,min_created_on,max_created_on)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""").format(sql.Identifier(SCHEMA)),
                (dataset, path.name, checksum.hexdigest(), count, applied, minimum, maximum))
    return {'rows_read': count, 'rows_applied': applied,
            'min_created_on': minimum.isoformat() if minimum else None,
            'max_created_on': maximum.isoformat() if maximum else None}


def source_inventory(settings):
    with psycopg.connect(**settings, connect_timeout=15) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        sources = []
        for dataset, table in TABLES.items():
            for country, count, first, last in conn.execute(sql.SQL(
                    'SELECT country,count(*),min(created_on),max(created_on) FROM {} GROUP BY country ORDER BY country'
                    ).format(sql.Identifier(SCHEMA, table))):
                sources.append({'dataset': dataset, 'module': DATASETS[dataset], 'country': country,
                                'rows': count, 'first': first.isoformat() if first else None,
                                'last': last.isoformat() if last else None})
        batches = [{'id': key, 'module': DATASETS[dataset], 'filename': filename,
                    'rows_read': read, 'rows_applied': applied, 'imported_at': when.isoformat()}
                   for key, dataset, filename, read, applied, when in conn.execute(sql.SQL(
                       'SELECT batch_id,dataset,source_file,rows_read,rows_applied,imported_at FROM {}.import_batches ORDER BY batch_id DESC LIMIT 10'
                       ).format(sql.Identifier(SCHEMA)))]
    return {'sources': sources, 'recent_imports': batches}
