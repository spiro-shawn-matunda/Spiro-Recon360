"""Local CSV validation and imports. Uploaded files are temporary, not public assets."""
import csv
import secrets
import shutil
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
from psycopg import sql
from .import_csv import MAPS, TABLES, import_file, read_rows

from . import PROJECT_ROOT

PROJECT = PROJECT_ROOT
DATASETS = {'wallet_master': 'Wallets', 'wallet': 'Wallet transactions', 'swap': 'Swapping transactions'}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_JOBS = 30
JOB_TTL = 60 * 60


def prepare_database(settings):
    """Install the existing schema and views; never reload configured CSVs on startup."""
    with psycopg.connect(**settings, connect_timeout=15, autocommit=True) as conn:
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(18700)')
            for name in ('schema.sql', 'wallets.sql', 'reconciliation.sql', 'tracking_indexes.sql'):
                conn.execute((PROJECT / 'sql' / name).read_text(encoding='utf-8'))


def safe_filename(value):
    if (not value or len(value) > 180 or '/' in value or '\\' in value or ':' in value
            or any(ord(char) < 32 for char in value) or not value.lower().endswith('.csv')):
        raise ValueError('Choose a CSV file with a simple filename, without folders.')
    return value


def inspect_csv(path, progress=None):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        headers = next(csv.reader(stream), [])
    if len(headers) != len(set(headers)):
        raise ValueError('CSV has duplicate column headers. Export it again from Zoho.')
    matches = [dataset for dataset, mapping in MAPS.items()
               if {field for _, field, _ in mapping} <= set(headers)]
    if len(matches) != 1:
        raise ValueError('This is not a complete Wallets, Wallet Transactions or Swapping Transactions export. Keep the original Zoho column headers.')
    dataset = matches[0]
    # A master export can contain Wallet Pin. Remove it from temporary storage too.
    if 'Wallet Pin' in headers:
        clean = path.with_suffix('.clean')
        try:
            with path.open(encoding='utf-8-sig', newline='') as source, clean.open('w', encoding='utf-8', newline='') as target:
                reader = csv.DictReader(source)
                writer = csv.DictWriter(target, fieldnames=[header for header in headers if header != 'Wallet Pin'])
                writer.writeheader()
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError('CSV contains a row with the wrong number of fields.')
                    row.pop('Wallet Pin', None)
                    writer.writerow(row)
            clean.replace(path)
        finally:
            clean.unlink(missing_ok=True)
    count = duplicates = 0
    ids = set()
    countries = Counter()
    statuses = Counter()
    minimum = maximum = None
    for row in read_rows(path, dataset):
        count += 1
        country = row['country']
        if country not in ('Kenya', 'Rwanda'):
            raise ValueError(f'Record {count}: Country must be Kenya or Rwanda. Check the export filter.')
        countries[country] += 1
        duplicates += row['zoho_record_id'] in ids
        ids.add(row['zoho_record_id'])
        statuses[row.get('status') or row.get('category') or 'Unspecified'] += 1
        created = row['created_on']
        if created is not None:
            minimum = min(minimum, created) if minimum else created
            maximum = max(maximum, created) if maximum else created
        if progress and count % 10000 == 0:
            progress(count, 'validating')
    if not count:
        raise ValueError('This CSV has headers but no data records.')
    return {'dataset': dataset, 'module': DATASETS[dataset], 'rows': count,
            'distinct_record_ids': len(ids), 'duplicate_record_id_rows': duplicates,
            'countries': dict(countries), 'statuses': dict(statuses),
            'min_created_on': minimum.isoformat() if minimum else None,
            'max_created_on': maximum.isoformat() if maximum else None,
            'export_cap_warning': count >= 200000}


def source_inventory(settings):
    with psycopg.connect(**settings, connect_timeout=15, autocommit=True) as conn:
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            sources = []
            for dataset, table in TABLES.items():
                for country, count, first, last in conn.execute(sql.SQL('SELECT country, count(*), min(created_on), max(created_on) FROM {} GROUP BY country ORDER BY country').format(sql.Identifier('reconciliation', table))):
                    sources.append({'dataset': dataset, 'module': DATASETS[dataset], 'country': country,
                                    'rows': count, 'first': first.isoformat() if first else None,
                                    'last': last.isoformat() if last else None})
            batches = [{'id': batch, 'module': DATASETS[dataset], 'filename': name, 'rows_read': read,
                        'rows_applied': applied, 'imported_at': when.isoformat()}
                       for batch, dataset, name, read, applied, when in conn.execute('SELECT batch_id, dataset, source_file, rows_read, rows_applied, imported_at FROM reconciliation.import_batches ORDER BY batch_id DESC LIMIT 10')]
    return {'sources': sources, 'recent_imports': batches}


class ImportJobs:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='spiro-import')
        self.root = tempfile.TemporaryDirectory(prefix='spiro-uploads-')

    def _cleanup(self):
        for key, job in list(self.jobs.items()):
            if job['state'] not in ('receiving', 'validating', 'importing') and time.monotonic() - job['_updated'] > JOB_TTL:
                shutil.rmtree(job['_folder'], ignore_errors=True)
                del self.jobs[key]

    def reserve(self, filename):
        filename = safe_filename(filename)
        with self.lock:
            self._cleanup()
            if len(self.jobs) >= MAX_JOBS:
                raise ValueError('Too many pending files. Clear the current uploads before adding more.')
            key = secrets.token_urlsafe(24)
            folder = Path(self.root.name) / key
            folder.mkdir()
            self.jobs[key] = {'id': key, 'filename': filename, 'state': 'receiving', 'phase': 'uploading',
                              'processed_rows': 0, '_folder': folder, '_path': folder / filename,
                              '_updated': time.monotonic()}
            return key, folder / filename

    def update(self, key, **values):
        with self.lock:
            self.jobs[key].update(values, _updated=time.monotonic())

    def status(self, key):
        with self.lock:
            self._cleanup()
            if key not in self.jobs:
                raise KeyError('File session expired. Choose the file again.')
            return {name: value for name, value in self.jobs[key].items() if not name.startswith('_')}

    def abort(self, key):
        with self.lock:
            job = self.jobs.pop(key, None)
            if job:
                shutil.rmtree(job['_folder'], ignore_errors=True)

    def validate(self, key):
        self.update(key, state='validating', phase='validating')
        self.pool.submit(self._validate, key)

    def _validate(self, key):
        path = self.jobs[key]['_path']
        try:
            preview = inspect_csv(path, lambda rows, phase: self.update(key, processed_rows=rows, phase=phase))
            self.update(key, state='ready', phase='validated', preview=preview, processed_rows=preview['rows'])
        except (ValueError, UnicodeError, csv.Error) as exc:
            self.update(key, state='failed', error=str(exc), phase='validation failed')
            path.unlink(missing_ok=True)
        except Exception:
            self.update(key, state='failed', error='The file could not be read. Choose it again or check the CSV export.', phase='validation failed')
            path.unlink(missing_ok=True)

    def commit(self, key):
        with self.lock:
            self.status(key)
            if self.jobs[key]['state'] != 'ready':
                raise ValueError('Only a validated file can be imported. Each file session can be imported once.')
            self.update(key, state='importing', phase='queued', processed_rows=0)
            self.pool.submit(self._import, key)
        return self.status(key)

    def _import(self, key):
        job = self.jobs[key]
        try:
            with psycopg.connect(**self.settings, connect_timeout=15, autocommit=True) as conn:
                result = import_file(conn, job['_path'], job['preview']['dataset'], sql,
                                     progress=lambda rows, phase: self.update(key, processed_rows=rows, phase=phase))
                self.update(key, state='complete', phase='imported', result=result, processed_rows=result['rows_read'])
                # The data is already committed; statistics failure must not report an import failure.
                try:
                    conn.execute(sql.SQL('ANALYZE {}').format(sql.Identifier('reconciliation', TABLES[job['preview']['dataset']])))
                except psycopg.Error:
                    pass
        except psycopg.Error:
            self.update(key, state='failed', phase='import failed', error='The database import failed and this file was rolled back. Check the database connection and permissions, then choose the file again.')
        except Exception:
            self.update(key, state='failed', phase='import failed', error='The import could not complete. This file was rolled back. Choose the file again.')
        finally:
            job['_path'].unlink(missing_ok=True)

    def discard(self, key):
        with self.lock:
            self.status(key)
            if self.jobs[key]['state'] in ('receiving', 'validating', 'importing'):
                raise ValueError('Wait for this file to finish before clearing it.')
            self.abort(key)

    def close(self):
        self.pool.shutdown(wait=True)
        self.root.cleanup()
