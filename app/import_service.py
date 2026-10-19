"""Local CSV validation and imports. Uploaded files are temporary, not public assets."""
import csv
import logging
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
from .import_csv import MAPS, TABLES, import_file, read_rows, numeric_warnings
from .dashboard_cache import install_cache, refresh_cache

from . import PROJECT_ROOT

PROJECT = PROJECT_ROOT
DATASETS = {'wallet_master': 'Wallets', 'wallet': 'Wallet transactions', 'swap': 'Swapping transactions', 'due': 'Dues',
            'offer_allocation': 'Offer allocations', 'offer_consumption': 'Offer consumptions'}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_JOBS = 30
JOB_TTL = 60 * 60
UPLOAD_TTL = 15 * 60
ACTIVE_JOB_TTL = 4 * 60 * 60
CLEANUP_INTERVAL = 30
LOGGER = logging.getLogger(__name__)
SESSION_EXPIRED_ERROR = 'File session expired. Choose the file again.'
JOB_BUSY_ERROR = 'Wait for this file to finish before clearing it.'


def prepare_database(settings):
    """Install the existing schema and views; never reload configured CSVs on startup."""
    with psycopg.connect(**settings, connect_timeout=15, autocommit=True) as conn:
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(18700)')
            for name in ('schema.sql', 'wallets.sql', 'dues.sql', 'offers.sql', 'reconciliation.sql', 'tracking_indexes.sql'):
                conn.execute((PROJECT / 'sql' / name).read_text(encoding='utf-8'))
            install_cache(conn)
        refresh_cache(conn)


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
        raise ValueError('This is not a complete Wallets, Wallet Transactions, Swapping Transactions, Dues, Offer Allocation or Offer Consumptions export. Keep the original Zoho column headers.')
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
    count = duplicates = swap_lookups = 0
    ids = set()
    countries = Counter()
    statuses = Counter()
    warnings = Counter()
    minimum = maximum = None
    for row in read_rows(path, dataset):
        count += 1
        warnings.update(numeric_warnings(row, dataset))
        country = row['country']
        if country not in ('Kenya', 'Rwanda'):
            raise ValueError(f'Record {count}: Country must be Kenya or Rwanda. Check the export filter.')
        countries[country] += 1
        duplicates += row['zoho_record_id'] in ids
        ids.add(row['zoho_record_id'])
        statuses[row.get('status') or row.get('category') or 'Unspecified'] += 1
        if dataset in ('due', 'offer_consumption') and row.get('swap_record_id'):
            swap_lookups += 1
        created = row['created_on']
        if created is not None:
            minimum = min(minimum, created) if minimum else created
            maximum = max(maximum, created) if maximum else created
        if progress and count % 10000 == 0:
            progress(count, 'validating')
    if not count:
        raise ValueError('This CSV has headers but no data records.')
    result = {'dataset': dataset, 'module': DATASETS[dataset], 'rows': count,
            'distinct_record_ids': len(ids), 'duplicate_record_id_rows': duplicates,
            'countries': dict(countries), 'statuses': dict(statuses),
            'min_created_on': minimum.isoformat() if minimum else None,
            'max_created_on': maximum.isoformat() if maximum else None,
            'export_cap_warning': count >= 200000, 'numeric_warnings': dict(warnings)}
    if dataset in ('due', 'offer_consumption'):
        result.update(swap_lookup_rows=swap_lookups, missing_swap_lookup_rows=count-swap_lookups)
    return result


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
        self.closed = False
        self.stop_cleanup = threading.Event()
        self.cleaner = threading.Thread(target=self._reap, name='spiro-upload-cleanup', daemon=True)
        self.cleaner.start()

    def _reap(self):
        while not self.stop_cleanup.wait(CLEANUP_INTERVAL):
            try:
                self.cleanup()
            except Exception:
                LOGGER.exception('Upload cleanup failed')

    def _remove(self, key, job):
        try:
            shutil.rmtree(job['_folder'])
        except FileNotFoundError:
            pass
        except OSError:
            LOGGER.exception('Could not remove upload job=%s', key)
            return
        self.jobs.pop(key, None)

    def cleanup(self):
        cancellations = []
        with self.lock:
            now = time.monotonic()
            for key, job in list(self.jobs.items()):
                future = job.get('_future')
                if future is not None and not future.done():
                    if now - job['_started'] > ACTIVE_JOB_TTL and not job.get('_cancelled'):
                        job['_cancelled'] = True
                        job['phase'] = 'Stopping an expired file session'
                        if future.cancel():
                            job.update(state='failed', error='File processing timed out. Choose the file again.', _updated=now)
                        elif job.get('_connection') is not None:
                            cancellations.append((key, job['_connection']))
                    continue
                ttl = UPLOAD_TTL if job['state'] == 'receiving' else JOB_TTL
                clock = job['_created'] if job['state'] == 'receiving' else job['_updated']
                if now - clock > ttl:
                    self._remove(key, job)
        # libpq cancellation may block. Never hold the job lock during network I/O.
        for key, conn in cancellations:
            try:
                conn.cancel_safe(timeout=5)
            except psycopg.Error:
                LOGGER.exception('Could not cancel expired import job=%s', key)

    def reserve(self, filename):
        filename = safe_filename(filename)
        self.cleanup()
        with self.lock:
            if self.closed:
                raise ValueError('The dashboard is stopping. Restart it before uploading.')
            if len(self.jobs) >= MAX_JOBS:
                raise ValueError('Too many pending files. Clear the current uploads before adding more.')
            key = secrets.token_urlsafe(24)
            folder = Path(self.root.name) / key
            folder.mkdir()
            self.jobs[key] = {'id': key, 'filename': filename, 'state': 'receiving', 'phase': 'uploading',
                              'processed_rows': 0, '_folder': folder, '_path': folder / filename,
                              '_created': time.monotonic(), '_updated': time.monotonic()}
            return key, folder / filename

    def update(self, key, **values):
        with self.lock:
            self.jobs[key].update(values, _updated=time.monotonic())

    def _progress(self, key, rows, phase):
        with self.lock:
            if self.jobs[key].get('_cancelled'):
                raise ValueError('File processing timed out. Choose the file again.')
            self.update(key, processed_rows=rows, phase=phase)

    def _finished(self, key, future):
        with self.lock:
            job = self.jobs.get(key)
            if job is None:
                return
            job.pop('_future', None)
            job.pop('_connection', None)
            failure = None if future.cancelled() else future.exception()
            if failure is not None:
                LOGGER.error('Background file worker failed job=%s', key,
                             exc_info=(type(failure), failure, failure.__traceback__))
                if job.get('result') is not None:
                    self.update(key, state='complete_with_warnings', phase='imported with warnings',
                                warning='The file was imported, but final processing failed. Check the application logs.')
                else:
                    self.update(key, state='failed', phase='file processing failed',
                                error='File processing stopped unexpectedly. Check the application logs and choose the file again.')
            if job.pop('_validated', False) and not future.cancelled() and failure is None:
                self.update(key, state='ready', phase='validated')
            if future.cancelled() or failure is not None:
                try:
                    job['_path'].unlink(missing_ok=True)
                except OSError:
                    LOGGER.exception('Could not remove stopped file job=%s', key)

    def _submit(self, key, worker):
        job = self.jobs[key]
        job['_started'] = time.monotonic()
        job['_cancelled'] = False
        future = self.pool.submit(worker, key)
        job['_future'] = future
        future.add_done_callback(lambda completed: self._finished(key, completed))

    def status(self, key):
        self.cleanup()
        with self.lock:
            if key not in self.jobs:
                raise KeyError(SESSION_EXPIRED_ERROR)
            return {name: value for name, value in self.jobs[key].items() if not name.startswith('_')}

    def abort(self, key):
        self.cleanup()
        with self.lock:
            job = self.jobs.get(key)
            if job:
                if job.get('_future') is not None and not job['_future'].done():
                    raise ValueError(JOB_BUSY_ERROR)
                self._remove(key, job)

    def validate(self, key):
        self.cleanup()
        with self.lock:
            if self.closed or self.jobs[key]['state'] != 'receiving':
                raise ValueError('Only a new upload can be validated once.')
            self.update(key, state='validating', phase='validating')
            self._submit(key, self._validate)

    def _validate(self, key):
        with self.lock:
            path = self.jobs[key]['_path']
        try:
            self._progress(key, 0, 'validating')
            preview = inspect_csv(path, lambda rows, phase: self._progress(key, rows, phase))
            self._progress(key, preview['rows'], 'validated')
            self.update(key, _validated=True, preview=preview, processed_rows=preview['rows'])
        except (ValueError, UnicodeError, csv.Error) as exc:
            self.update(key, state='failed', error=str(exc), phase='validation failed')
            path.unlink(missing_ok=True)
        except Exception:
            LOGGER.exception('CSV validation failed job=%s', key)
            self.update(key, state='failed', error='The file could not be read. Choose it again or check the CSV export.', phase='validation failed')
            path.unlink(missing_ok=True)

    def commit(self, key):
        self.cleanup()
        with self.lock:
            if key not in self.jobs:
                raise KeyError(SESSION_EXPIRED_ERROR)
            if self.closed or self.jobs[key]['state'] != 'ready' or self.jobs[key].get('_future') is not None:
                raise ValueError('Only a validated file can be imported. Each file session can be imported once.')
            self.update(key, state='importing', phase='queued', processed_rows=0)
            self._submit(key, self._import)
        return self.status(key)

    def _import(self, key):
        self.cleanup()
        with self.lock:
            job = self.jobs[key]
        result = None
        warnings = []
        try:
            self._progress(key, 0, 'connecting')
            with psycopg.connect(**self.settings, connect_timeout=15, autocommit=True) as conn:
                with self.lock:
                    job['_connection'] = conn
                conn.execute("SET statement_timeout = '4h'")
                conn.execute("SET lock_timeout = '120s'")
                result = import_file(conn, job['_path'], job['preview']['dataset'], sql,
                                     progress=lambda rows, phase: self._progress(key, rows, phase))
                self.update(key, phase='Refreshing dashboard results', result=result, processed_rows=result['rows_read'])
                # The data is already committed; statistics failure must not report an import failure.
                try:
                    conn.execute(sql.SQL('ANALYZE {}').format(sql.Identifier('reconciliation', TABLES[job['preview']['dataset']])))
                except psycopg.Error:
                    LOGGER.exception('Import statistics refresh failed job=%s dataset=%s', key, job['preview']['dataset'])
                    warnings.append('The file was imported, but database statistics could not be refreshed. Check the application logs.')
                # Import is committed. Never claim rollback if a derived refresh fails.
                try:
                    refresh_cache(conn)
                except Exception:
                    LOGGER.exception('Import cache refresh failed job=%s dataset=%s', key, job['preview']['dataset'])
                    warnings.append('The file was imported. Dashboard results will retry recalculation when refreshed.')
                self.update(key, state='complete_with_warnings' if warnings else 'complete',
                            phase='imported with warnings' if warnings else 'imported', warning=' '.join(warnings))
        except psycopg.Error:
            LOGGER.exception('Database import failed job=%s dataset=%s', key, job['preview']['dataset'])
            if result is not None:
                self.update(key, state='complete_with_warnings', phase='imported with warnings', result=result,
                            warning='The file was imported, but final database processing failed. Check the application logs.')
            else:
                self.update(key, state='failed', phase='import failed', error='The database import failed and this file was rolled back. Check the database connection and permissions, then choose the file again.')
        except Exception:
            LOGGER.exception('Import failed job=%s dataset=%s', key, job['preview']['dataset'])
            if result is not None:
                self.update(key, state='complete_with_warnings', phase='imported with warnings', result=result,
                            warning='The file was imported, but final processing failed. Check the application logs before refreshing results.')
            else:
                self.update(key, state='failed', phase='import failed', error='The import could not complete. This file was rolled back. Choose the file again.')
        finally:
            with self.lock:
                job.pop('_connection', None)
            job['_path'].unlink(missing_ok=True)

    def discard(self, key):
        self.cleanup()
        with self.lock:
            if key not in self.jobs:
                raise KeyError(SESSION_EXPIRED_ERROR)
            if self.jobs[key]['state'] in ('receiving', 'validating', 'importing') or self.jobs[key].get('_future') is not None:
                raise ValueError(JOB_BUSY_ERROR)
            self._remove(key, self.jobs[key])

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.stop_cleanup.set()
        self.cleaner.join()
        self.pool.shutdown(wait=True)
        self.root.cleanup()
        with self.lock:
            self.jobs.clear()
