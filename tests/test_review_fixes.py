"""Regression coverage for the GitHub review, independent of project data."""
import contextlib
import csv
import io
import http.client
import json
import threading
import time
import unittest
from concurrent.futures import Future
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
import main
from app import dashboard, due_tracking, offer_tracking
from app.csv_safety import csv_value
from app.import_csv import numeric_warnings, parse_value
from app.import_service import ACTIVE_JOB_TTL, JOB_TTL, UPLOAD_TTL, ImportJobs, inspect_csv
from app.missing_counterparts import report_query
from app.reconciliation_backend import ReconciliationBackend, ReconciliationFilter, validate_record_id
from test_import_service import csv_bytes


class ReviewQueryTests(unittest.TestCase):
    def test_templates_format_once_for_every_counterpart_group(self):
        for group in ('wallet_without_swap', 'swap_without_wallet'):
            for classification in (None, 'valid', 'unmatchable'):
                for detail in (True, False):
                    with self.subTest(group=group, classification=classification, detail=detail):
                        statement, values = report_query(group, ReconciliationFilter(country='Kenya'),
                            classification=classification, detail=detail, include_resolved=True)
                        self.assertNotIn('{filters}', statement)
                        self.assertIn('country=%s', statement)
                        self.assertEqual(values, ['Kenya'])

    def test_query_field_limit_rejects_entire_request(self):
        query = '&'.join('p'+str(i)+'=x' for i in range(11))
        with self.assertRaisesRegex(ValueError, 'at most 10'):
            dashboard.query_options(query, {'p'+str(i) for i in range(11)})

    def test_invalid_cursors_never_reach_the_database(self):
        conn = MagicMock()
        for cursor in ('', ' ', 'a'*181, 'a\n', 'a b', '=formula', '\x00', "a' OR true", 1):
            calls = (
                lambda: dashboard.query_options('cursor='+str(cursor), {'cursor'}),
                lambda: ReconciliationBackend(conn).list_records(after_record_id=cursor),
                lambda: report_query('wallet_without_swap', ReconciliationFilter(), cursor=cursor),
                lambda: due_tracking._scope(cursor=cursor),
                lambda: offer_tracking.query(cursor=cursor),
            )
            # The HTTP parser sees numeric cursors as legitimate identifier strings.
            for call in calls[1:] if cursor == 1 else calls:
                with self.subTest(cursor=repr(cursor)), self.assertRaises(ValueError):
                    call()
        conn.cursor.assert_not_called()
        conn.execute.assert_not_called()

    def test_supported_source_ids_and_detail_lookup_use_one_query(self):
        for value in ('zcrm_1234567890123456789', '12345', 'fixture-a', 'KE:1.2'):
            self.assertEqual(validate_record_id(value), value)
        conn = MagicMock()
        with patch('app.reconciliation_backend._rows', return_value=[{'wallet_record_id':'fixture-a'}]) as rows:
            record = ReconciliationBackend(conn).get_record('fixture-a', country='Kenya')
        self.assertEqual(record['wallet_record_id'], 'fixture-a')
        self.assertEqual(rows.call_count, 1)
        statement, params = rows.call_args.args[1:]
        self.assertIn('WITH target AS MATERIALIZED', statement)
        self.assertIn('r.transaction_id = (SELECT transaction_id FROM target)', statement)
        self.assertIn('UNION ALL', statement)
        self.assertEqual(params, ['fixture-a', 'Kenya', 'fixture-a', 'Kenya', 'fixture-a'])
        conn.execute.assert_not_called()


class ReviewCsvTests(unittest.TestCase):
    def test_all_source_strings_are_protected_and_real_numbers_remain_exact(self):
        for value in ('=SUM(1,2)', '+formula', '-formula', '@SUM(1)', '\tformula', '\rformula', '\nformula', '  =formula'):
            for field in ('wallet_amount', 'reconciliation_status', 'customer_name'):
                self.assertEqual(csv_value(field, value, numeric=field=='wallet_amount'), "'"+value)
        self.assertEqual(csv_value('wallet_amount', '-120.10', numeric=True), '-120.10')
        self.assertEqual(csv_value('wallet_amount', Decimal('-120.10')), Decimal('-120.10'))
        self.assertEqual(csv_value('record_id', '-123'), "'-123")

    def test_dashboard_export_checks_amount_contents_and_status(self):
        backend = MagicMock()
        backend.list_records.return_value = {'records':[{'wallet_record_id':'fixture-a',
            'wallet_amount':'=SUM(1,2)', 'successful_swap_amount':'-12.10',
            'reconciliation_status':'@formula'}], 'has_more':False}
        body, count = dashboard.export_candidates(backend, ReconciliationFilter())
        row = next(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual(count, 1)
        self.assertEqual(row['wallet_amount'], "'=SUM(1,2)")
        self.assertEqual(row['successful_swap_amount'], '-12.10')
        self.assertEqual(row['reconciliation_status'], "'@formula")

    def test_cli_review_export_uses_the_same_safety_rules(self):
        from app.reconcile import write_report
        backend = MagicMock()
        backend.summary.return_value = {'needs_review_count':1}
        backend.list_records.return_value = {'records':[{'wallet_record_id':'fixture-a',
            'wallet_amount':'-12.10', 'wallet_customer_name':'=formula'}], 'has_more':False}
        with TemporaryDirectory() as folder, patch('app.reconcile.ReconciliationBackend', return_value=backend):
            _, _, output = write_report(MagicMock(), ReconciliationFilter(), folder)
            row = next(csv.DictReader(io.StringIO(output.read_text(encoding='utf-8-sig'))))
        self.assertEqual(row['wallet_customer_name'], "'=formula")
        self.assertEqual(row['wallet_amount'], '-12.10')

    def test_invalid_decimals_rejected_and_suspicious_values_preserved_with_warnings(self):
        for value in ('NaN', 'Infinity', '1e131072', '1e-16384'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_value(value, 'decimal', 'Amount')
        with TemporaryDirectory() as folder:
            path = Path(folder)/'wallet.csv'
            path.write_bytes(csv_bytes('wallet', [{'Record Id':'test-a', 'Country':'Kenya',
                'Amount':'-1000000000.123', 'Opening Balance':'0.000'}]))
            preview = inspect_csv(path)
            self.assertEqual(preview['numeric_warnings'], {'negative_amount':1,
                'amount_at_least_one_billion_source_units':1, 'amount_with_more_than_two_decimal_places':1})
            self.assertIn('-1000000000.123', path.read_text(encoding='utf-8-sig'))
        self.assertEqual(numeric_warnings({'swap_amount':Decimal('0')}, 'swap'), {'zero_transaction_amount':1})


class ReviewArgumentTests(unittest.TestCase):
    def test_conflicting_unknown_and_missing_arguments_fail_before_side_effects(self):
        invalid = (
            ['--export-review', '--export-unmatched'], ['--export-review', '--import-config'],
            ['--export-review', '--dry-run'], ['--export-review', '--port', '9000'],
            ['--export-unmatched', '--status', 'matched'], ['--country','Kenya'],
            ['--import-config','--output','out'], ['--file-prefix'], ['--port'],
            ['--export-review','extra_arg'], ['some_random_flag'],
        )
        with patch('main.read_database_config') as config, patch('tools.batch.run_import') as importing, patch('app.reconcile.main') as report:
            for args in invalid:
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main.main(args)
                self.assertEqual(exc.exception.code, 2)
            config.assert_not_called()
            importing.assert_not_called()
            report.assert_not_called()

    def test_existing_import_options_and_export_arguments_still_dispatch(self):
        with patch('tools.batch.run_import') as importing:
            main.main(['--import-config','--dry-run','--file-prefix=KE'])
            importing.assert_called_once_with(['--dry-run','--file-prefix=KE'])
        with patch('app.reconcile.main') as report:
            main.main(['--export-review','--country','Kenya','--start','2026-09-23'])
            report.assert_called_once_with(['--country','Kenya','--start','2026-09-23'])


class ReviewJobTests(unittest.TestCase):
    def setUp(self):
        self.jobs = ImportJobs({})
        self.addCleanup(self.jobs.close)

    def new_job(self, state='ready'):
        key, path = self.jobs.reserve('source.csv')
        path.write_text('source', encoding='utf-8')
        self.jobs.update(key, state=state, preview={'dataset':'wallet'})
        return key, path

    def test_abandoned_uploads_and_expired_completed_jobs_release_files_and_capacity(self):
        receipt, path1 = self.new_job('receiving')
        ready, path2 = self.new_job()
        with self.jobs.lock:
            self.jobs.jobs[receipt]['_created'] = time.monotonic()-UPLOAD_TTL-1
            self.jobs.jobs[ready]['_updated'] = time.monotonic()-JOB_TTL-1
        self.jobs.cleanup()
        self.assertFalse(path1.exists())
        self.assertFalse(path2.exists())
        self.assertFalse(self.jobs.jobs)

    def test_expired_queued_work_is_cancelled_without_running(self):
        key, path = self.new_job('importing')
        future = Future()
        future.add_done_callback(lambda done:self.jobs._finished(key, done))
        self.jobs.jobs[key].update(_future=future, _started=time.monotonic()-ACTIVE_JOB_TTL-1)
        self.jobs.cleanup()
        self.assertTrue(future.cancelled())
        self.assertEqual(self.jobs.status(key)['state'], 'failed')
        self.assertFalse(path.exists())

    def test_expired_running_work_cancels_connection_but_keeps_owned_file(self):
        key, path = self.new_job('importing')
        future = Future()
        future.set_running_or_notify_cancel()
        conn = MagicMock()
        self.jobs.jobs[key].update(_future=future, _connection=conn,
                                  _started=time.monotonic()-ACTIVE_JOB_TTL-1)
        self.jobs.cleanup()
        conn.cancel_safe.assert_called_once_with(timeout=5)
        self.assertTrue(path.exists())
        with self.assertRaisesRegex(ValueError, 'timed out'):
            self.jobs._progress(key, 10000, 'staging')
        for action in (self.jobs.abort, self.jobs.discard):
            with self.assertRaises(ValueError):
                action(key)

    def test_concurrent_updates_are_serialized_under_the_job_lock(self):
        key, _ = self.new_job()
        barrier = threading.Barrier(12)
        def update(index):
            barrier.wait()
            self.jobs.update(key, **{'worker_'+str(index):index})
        threads = [threading.Thread(target=update,args=(index,)) for index in range(12)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        for index in range(12): self.assertEqual(self.jobs.status(key)['worker_'+str(index)], index)

    def test_concurrent_commits_start_only_one_worker_and_discard_cannot_delete_its_file(self):
        key, path = self.new_job()
        barrier = threading.Barrier(2)
        entered, finish = threading.Event(), threading.Event()
        outcomes = []
        def worker(job_id):
            entered.set()
            finish.wait(timeout=5)
        def commit():
            barrier.wait()
            try:
                outcomes.append(self.jobs.commit(key)['state'])
            except ValueError:
                outcomes.append('rejected')
        with patch.object(self.jobs, '_import', side_effect=worker) as imported:
            threads = [threading.Thread(target=commit) for _ in range(2)]
            for thread in threads: thread.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                for thread in threads: thread.join(timeout=5)
                self.assertCountEqual(outcomes, ['importing','rejected'])
                with self.assertRaises(ValueError): self.jobs.discard(key)
                self.assertTrue(path.exists())
                imported.assert_called_once_with(key)
            finally:
                finish.set()
                self.jobs.pool.submit(lambda:None).result(timeout=5)

    def test_validation_releases_future_and_cannot_be_started_twice(self):
        key, path = self.jobs.reserve('wallets.csv')
        path.write_bytes(csv_bytes())
        self.jobs.validate(key)
        self.jobs.pool.submit(lambda:None).result(timeout=5)
        self.assertEqual(self.jobs.status(key)['state'], 'ready')
        self.assertNotIn('_future', self.jobs.jobs[key])
        with self.assertRaises(ValueError): self.jobs.validate(key)
        self.jobs.discard(key)
        self.assertFalse(path.exists())

    def test_worker_crash_is_logged_and_does_not_leave_an_active_job_or_callback(self):
        key, path = self.new_job('validating')
        future = Future()
        future.set_exception(OSError('worker stopped'))
        self.jobs.jobs[key]['_future'] = future
        with self.assertLogs('app.import_service', level='ERROR'):
            self.jobs._finished(key, future)
        self.assertEqual(self.jobs.status(key)['state'], 'failed')
        self.assertNotIn('_future', self.jobs.jobs[key])
        self.assertFalse(path.exists())

    def test_background_cleanup_runs_without_a_status_request(self):
        removed = threading.Event()
        with patch('app.import_service.CLEANUP_INTERVAL', 0.01):
            jobs = ImportJobs({})
            self.addCleanup(jobs.close)
            key, path = jobs.reserve('orphan.csv')
            jobs.jobs[key]['_created'] = time.monotonic()-UPLOAD_TTL-1
            original = jobs._remove
            def remove(job_id, job):
                original(job_id, job)
                removed.set()
            with patch.object(jobs, '_remove', side_effect=remove):
                self.assertTrue(removed.wait(timeout=5))
            self.assertFalse(path.parent.exists())
            self.assertNotIn(key, jobs.jobs)

    def test_statistics_and_cache_failures_are_visible_after_committed_import(self):
        key, path = self.new_job('importing')
        conn = MagicMock()
        def execute(statement):
            if 'ANALYZE' in str(statement): raise psycopg.DatabaseError('statistics failure')
        conn.execute.side_effect = execute
        result = {'rows_read':1,'rows_applied':1}
        with patch('app.import_service.psycopg.connect') as connect, patch('app.import_service.import_file', return_value=result), patch('app.import_service.refresh_cache', side_effect=RuntimeError('refresh failed')), self.assertLogs('app.import_service', level='ERROR') as logs:
            connect.return_value.__enter__.return_value = conn
            self.jobs._import(key)
        status = self.jobs.status(key)
        self.assertEqual(status['state'], 'complete_with_warnings')
        self.assertEqual(status['result'], result)
        self.assertIn('statistics', status['warning'])
        self.assertIn('recalculation', status['warning'])
        self.assertEqual(len(logs.records), 2)
        self.assertFalse(path.exists())

    def test_failed_atomic_import_reports_rollback_and_logs_exception(self):
        key, path = self.new_job('importing')
        with patch('app.import_service.psycopg.connect'), patch('app.import_service.import_file', side_effect=psycopg.DatabaseError('copy failed')), self.assertLogs('app.import_service', level='ERROR'):
            self.jobs._import(key)
        status = self.jobs.status(key)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('rolled back', status['error'])
        self.assertNotIn('result', status)
        self.assertFalse(path.exists())

    def test_error_after_commit_never_claims_rollback(self):
        key, _ = self.new_job('importing')
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.side_effect = psycopg.OperationalError('connection closed')
        with patch('app.import_service.psycopg.connect', return_value=conn), patch('app.import_service.import_file', return_value={'rows_read':1,'rows_applied':1}), patch('app.import_service.refresh_cache'), self.assertLogs('app.import_service', level='ERROR'):
            self.jobs._import(key)
        status = self.jobs.status(key)
        self.assertEqual(status['state'], 'complete_with_warnings')
        self.assertNotIn('error', status)


class ReviewHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = dashboard.DashboardServer(('127.0.0.1',0), {})
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:'+str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, **kwargs):
        request = Request(self.url+path, **kwargs)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as response:
            return response.code, response.read()

    def test_excess_query_parameters_and_bad_cursor_return_400_before_connecting(self):
        for path in ('/api/records?'+'&'.join('p'+str(i)+'=x' for i in range(11)), '/api/records?cursor='+('a'*181)):
            with patch('app.dashboard.snapshot') as snapshot:
                self.assertEqual(self.request(path)[0], 400)
                snapshot.assert_not_called()

    def test_missing_assets_return_controlled_error_and_fail_startup(self):
        with TemporaryDirectory() as folder, patch('app.dashboard.WEB', Path(folder)):
            with self.assertLogs('app.dashboard', level='ERROR'):
                status, body = self.request('/styles.css')
            self.assertEqual(status, 503)
            self.assertIn('Dashboard files', json.loads(body)['error'])
            with self.assertRaisesRegex(ValueError, 'assets are missing'):
                dashboard.DashboardServer(('127.0.0.1',0), {})

    def test_database_errors_log_context_without_returning_raw_exception(self):
        failures = ((psycopg.errors.QueryCanceled('private query'), 'timed out'),
                    (psycopg.errors.InsufficientPrivilege('private schema'), 'permissions'),
                    (psycopg.OperationalError('private connection'), 'connection'))
        for exc, expected in failures:
            with patch('app.dashboard.source_inventory', side_effect=exc), self.assertLogs('app.dashboard', level='ERROR') as logs:
                status, body = self.request('/api/sources')
            self.assertEqual(status, 503)
            self.assertIn(expected, json.loads(body)['error'])
            self.assertNotIn('private', body.decode())
            self.assertIn('route=/api/sources', logs.output[0])

    def test_csrf_rejection_precedes_upload_reservation(self):
        with patch.object(self.server.imports, 'reserve') as reserve:
            status, _ = self.request('/api/uploads', data=b'CSV', headers={
                'Content-Type':'application/octet-stream', 'X-Filename':'file.csv'})
        self.assertEqual(status, 403)
        reserve.assert_not_called()

    def test_continuing_upload_resets_idle_timeout_instead_of_timing_out_whole_file(self):
        body = csv_bytes()
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        self.addCleanup(conn.close)
        with patch('app.dashboard.UPLOAD_IDLE_TIMEOUT', 0.2):
            conn.putrequest('POST','/api/uploads')
            conn.putheader('Content-Type','application/octet-stream')
            conn.putheader('Content-Length',str(len(body)))
            conn.putheader('X-Filename','slow.csv')
            conn.putheader('X-Spiro-Token',self.server.csrf_token)
            conn.endheaders()
            chunk_size = (len(body)+5)//6
            for start in range(0,len(body),chunk_size):
                conn.send(body[start:start+chunk_size])
                time.sleep(0.07)
            response = conn.getresponse()
            self.assertEqual(response.status, 202)
            response.read()
