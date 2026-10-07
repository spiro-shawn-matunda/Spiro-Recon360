"""Local Spiro dashboard. Run in PyCharm, then open the printed URL."""
import argparse
import csv
import io
import json
import secrets
import socket
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, unquote

import psycopg
from .db_config import read_database_config
from .reconcile import EXPORT_FIELDS
from .reconciliation_backend import ReconciliationBackend, ReconciliationFilter
from .import_service import ImportJobs, MAX_UPLOAD_BYTES, source_inventory
from . import counterpart_tracking, due_tracking, offer_tracking
from .dashboard_cache import cache_ready, ensure_cache

from . import PROJECT_ROOT

WEB = PROJECT_ROOT / "web"
MAX_EXPORT_RECORDS = 10000
STATIC = {"/": "index.html", "/styles.css": "styles.css", "/app.js": "app.js", "/favicon.svg": "favicon.svg"}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, database_settings):
        self.database_settings = database_settings
        self.csrf_token = secrets.token_urlsafe(32)
        self.imports = ImportJobs(database_settings)
        super().__init__(address, DashboardHandler)

    def server_close(self):
        super().server_close()
        self.imports.close()


@contextmanager
def snapshot(settings):
    ensure_cache(settings)
    with psycopg.connect(**settings, connect_timeout=15, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '120s'")
            yield ReconciliationBackend(conn, cached=cache_ready(conn))


def query_options(query, allowed):
    values = parse_qs(query, keep_blank_values=True, max_num_fields=10)
    if set(values) - allowed or any(len(items) != 1 for items in values.values()):
        raise ValueError("Unknown or repeated query parameter.")
    values = {key: items[0] for key, items in values.items()}
    filters = ReconciliationFilter(country=values.get("country") or None,
                                   start_date=values.get("start") or None,
                                   end_date=values.get("end") or None,
                                   status=values.get("status") or None)
    review = values.get("review_only", "true")
    if review not in ("true", "false"):
        raise ValueError("review_only must be true or false.")
    try:
        limit = int(values.get("limit", "50"))
    except ValueError:
        raise ValueError("Page size must be an integer.") from None
    if not 1 <= limit <= 100:
        raise ValueError("Dashboard page size must be between 1 and 100.")
    return filters, review == "true", limit, values.get("cursor") or None, values


def filter_dates(conn, *, view='overview', country=None, group=None):
    """Dates on the module's source records, independent of review outcomes.

    Use exported creation dates, just like result filtering. In particular,
    Dues/Offers constrain swap dates, not repayment or consumption dates.
    """
    ReconciliationFilter(country=country)
    if view not in ('overview', 'counterparts', 'due', 'offer'):
        raise ValueError('Choose a known module for date filters.')
    wallet = view == 'overview'
    if view == 'counterparts':
        source, _ = counterpart_tracking.selection(group or 'wallet_without_swap')
        wallet = source == 'wallet_without_swap'
    elif group is not None:
        raise ValueError('Tracking groups apply only to Missing Counterparts.')
    table = 'wallet_transactions' if wallet else 'swap_transactions'
    conditions = ["country IN ('Kenya', 'Rwanda')", 'created_on IS NOT NULL']
    values = []
    if wallet:
        conditions += ["transaction_type='Debit'", "status='Committed'", "settled_against='Swap'"]
    if view in ('due', 'offer'):
        conditions.append('payment_method=%s')
        values.append('DUE_CREATED' if view == 'due' else 'OFFER_APPLIED')
    if country:
        conditions.append('country=%s')
        values.append(country)
    rows = conn.execute('SELECT DISTINCT created_on::date FROM reconciliation.' + table
                        + ' WHERE ' + ' AND '.join(conditions) + ' ORDER BY 1', values).fetchall()
    dates = [row[0].isoformat() for row in rows]
    return {'view': view, 'country': country, 'group': group, 'dates': dates,
            'first_date': dates[0] if dates else None,
            'last_date': dates[-1] if dates else None,
            'date_basis': 'source_created_on'}


def export_candidates(backend, filters):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    cursor, count = None, 0
    while True:
        page = backend.list_records(filters, review_only=True, limit=1000, after_record_id=cursor)
        for record in page["records"]:
            count += 1
            if count > MAX_EXPORT_RECORDS:
                raise ValueError("More than 10,000 candidates. Narrow the country or date filters before exporting.")
            row = {field: record.get(field) for field in EXPORT_FIELDS}
            for field, value in row.items():
                if isinstance(value, list):
                    row[field] = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, str) and field not in ("wallet_amount", "successful_swap_amount") and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                    row[field] = "'" + value
            writer.writerow(row)
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    return ("\ufeff" + stream.getvalue()).encode("utf-8"), count


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"Dashboard {self.command} {urlsplit(self.path).path}: {args[1] if len(args)>1 else ''}", flush=True)

    def respond(self, status, body, content_type, *, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, value):
        self.respond(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def allowed_request(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            self.json(403, {"error": "This dashboard accepts local requests only."})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + host for host in hosts}:
            self.json(403, {"error": "Cross-origin access is not allowed."})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.json(403, {"error": "Cross-site access is not allowed."})
            return False
        return True

    def do_GET(self):
        if not self.allowed_request():
            return
        parsed = urlsplit(self.path)
        if parsed.path in STATIC:
            path = WEB / STATIC[parsed.path]
            self.respond(200, path.read_bytes(), MIME[path.suffix])
            return
        common = {"country", "start", "end", "status"}
        try:
            if parsed.path == '/api/session':
                self.json(200, {'csrf_token': self.server.csrf_token, 'max_upload_bytes': MAX_UPLOAD_BYTES})
            elif parsed.path == '/api/sources':
                self.json(200, source_inventory(self.server.database_settings))
            elif parsed.path == '/api/filter-dates':
                filters, _, _, _, values = query_options(parsed.query, {'view', 'country', 'group'})
                with snapshot(self.server.database_settings) as backend:
                    result = filter_dates(backend.conn, view=values.get('view') or 'overview',
                                          country=filters.country, group=values.get('group') or None)
                self.json(200, result)
            elif parsed.path.startswith('/api/import-jobs/'):
                key = parsed.path.removeprefix('/api/import-jobs/')
                self.json(200, self.server.imports.status(key))
            elif parsed.path in ('/api/offer-swaps', '/api/offer-swaps/export'):
                allowed = {'country', 'start', 'end', 'group'}
                if not parsed.path.endswith('/export'):
                    allowed |= {'limit', 'cursor'}
                filters, _, limit, cursor, values = query_options(parsed.query, allowed)
                group = values.get('group', 'needs_review')
                offer_tracking.selection(group)
                with snapshot(self.server.database_settings) as backend:
                    if parsed.path.endswith('/export'):
                        body = offer_tracking.export(backend.conn, filters, group=group)
                    else:
                        result = {'summary': offer_tracking.summary(backend.conn, filters),
                                  'page': offer_tracking.page(backend.conn, filters, group=group, limit=limit, cursor=cursor),
                                  'group': group, 'generated_at_utc': datetime.now(timezone.utc).isoformat()}
                if parsed.path.endswith('/export'):
                    self.respond(200, body, 'text/csv; charset=utf-8', filename='spiro_offer_swaps.csv')
                else:
                    self.json(200, result)
            elif parsed.path in ('/api/due-swaps', '/api/due-swaps/export'):
                allowed = {'country', 'start', 'end', 'group'}
                if parsed.path == '/api/due-swaps':
                    allowed |= {'limit', 'cursor'}
                filters, _, limit, cursor, values = query_options(parsed.query, allowed)
                group = values.get('group', 'needs_review')
                due_tracking.selection(group)
                with snapshot(self.server.database_settings) as backend:
                    if parsed.path.endswith('/export'):
                        body = due_tracking.export(backend.conn, filters, group=group)
                    else:
                        result = {'summary': due_tracking.summary(backend.conn, filters),
                                  'page': due_tracking.page(backend.conn, filters, limit=limit, cursor=cursor, group=group),
                                  'group': group,
                                  'generated_at_utc': datetime.now(timezone.utc).isoformat()}
                if parsed.path.endswith('/export'):
                    self.respond(200, body, 'text/csv; charset=utf-8', filename='spiro_due_created_swaps.csv')
                else:
                    self.json(200, result)
            elif parsed.path in ('/api/counterparts', '/api/counterparts/export'):
                allowed = {'country', 'start', 'end', 'group'}
                if parsed.path == '/api/counterparts':
                    allowed |= {'limit', 'cursor'}
                filters, _, limit, cursor, values = query_options(parsed.query, allowed)
                group = values.get('group', 'wallet_without_swap')
                counterpart_tracking.selection(group)
                with snapshot(self.server.database_settings) as backend:
                    if parsed.path == '/api/counterparts/export':
                        body = counterpart_tracking.export(backend.conn, group, filters, cached=backend.cached)
                    else:
                        result = {'summary': counterpart_tracking.summary(backend.conn, filters, cached=backend.cached),
                                  'page': counterpart_tracking.page(backend.conn, group, filters, limit=limit, cursor=cursor, cached=backend.cached),
                                  'group': group, 'generated_at_utc': datetime.now(timezone.utc).isoformat()}
                if parsed.path == '/api/counterparts/export':
                    self.respond(200, body, 'text/csv; charset=utf-8', filename=group+'_tracking.csv')
                else:
                    self.json(200, result)
            elif parsed.path in ("/api/dashboard", "/api/records"):
                filters, review, limit, cursor, _ = query_options(parsed.query, common | {"review_only", "limit", "cursor"})
                with snapshot(self.server.database_settings) as backend:
                    page = backend.list_records(filters, review_only=review, limit=limit, after_record_id=cursor)
                    result = {"page": page, "generated_at_utc": datetime.now(timezone.utc).isoformat()}
                    if parsed.path == "/api/dashboard":
                        result["summary"] = backend.summary(filters)
                        swap_filters = ReconciliationFilter(country=filters.country,
                            start_date=filters.start_date, end_date=filters.end_date)
                        result['swap_review'] = counterpart_tracking.swap_review_summary(
                            backend.conn, swap_filters, cached=backend.cached)
                self.json(200, result)
            elif parsed.path == "/api/detail":
                filters, _, _, _, values = query_options(parsed.query, {"country", "record_id"})
                record_id = values.get("record_id", "")
                with snapshot(self.server.database_settings) as backend:
                    record = backend.get_record(record_id, country=filters.country)
                self.json(200, {"record": record}) if record else self.json(404, {"error": "Transaction was not found."})
            elif parsed.path == "/api/export":
                filters, _, _, _, _ = query_options(parsed.query, common)
                with snapshot(self.server.database_settings) as backend:
                    body, _ = export_candidates(backend, filters)
                self.respond(200, body, "text/csv; charset=utf-8", filename="spiro_review_candidates.csv")
            else:
                self.json(404, {"error": "Page was not found."})
        except ValueError as exc:
            self.json(400, {"error": str(exc)})
        except KeyError:
            self.json(404, {'error': 'File session expired. Choose the file again.'})
        except psycopg.Error:
            self.json(503, {"error": "The database could not be queried. Check your project connection and restart main.py if needed."})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if not self.allowed_request():
            return
        path = urlsplit(self.path).path
        if path != '/api/uploads' and not path.startswith('/api/import-jobs/'):
            self.json(405, {'error': 'Only CSV upload and import actions accept POST requests.'})
            return
        if not secrets.compare_digest(self.headers.get('X-Spiro-Token', '').encode('utf-8'), self.server.csrf_token.encode('utf-8')):
            self.json(403, {'error': 'Refresh the dashboard before importing files.'})
            return
        key = None
        try:
            if path == '/api/uploads':
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type') != 'application/octet-stream':
                    raise ValueError('Send a CSV file with its exact size.')
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise ValueError('Upload size is invalid.') from None
                if not 0 < size <= MAX_UPLOAD_BYTES:
                    self.close_connection = True
                    self.json(413, {'error': 'Choose a nonempty CSV smaller than 512 MB. Split larger exports by date.'})
                    return
                key, target = self.server.imports.reserve(unquote(self.headers.get('X-Filename', '')))
                original_timeout = self.connection.gettimeout()
                self.connection.settimeout(60)
                try:
                    with target.open('wb') as output:
                        remaining = size
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError('Upload was interrupted. Choose the file again.')
                            output.write(chunk)
                            remaining -= len(chunk)
                finally:
                    self.connection.settimeout(original_timeout)
                self.server.imports.validate(key)
                self.json(202, self.server.imports.status(key))
            else:
                parts = path.removeprefix('/api/import-jobs/').split('/')
                if len(parts) != 2 or parts[1] not in ('commit', 'discard'):
                    self.json(404, {'error': 'Import action was not found.'})
                    return
                if parts[1] == 'commit':
                    self.json(202, self.server.imports.commit(parts[0]))
                else:
                    self.server.imports.discard(parts[0])
                    self.json(200, {'discarded': True})
        except ValueError as exc:
            if key:
                self.server.imports.abort(key)
            self.close_connection = True
            self.json(400, {'error': str(exc)})
        except KeyError:
            self.json(404, {'error': 'File session expired. Choose the file again.'})
        except (OSError, TimeoutError):
            if key:
                self.server.imports.abort(key)
            self.close_connection = True
            try:
                self.json(400, {'error': 'The file could not be uploaded. Choose it again.'})
            except OSError:
                pass


def serve(settings, port=8765):
    with DashboardServer(("127.0.0.1", port), settings) as server:
        print(f"Spiro reconciliation dashboard: http://127.0.0.1:{port}", flush=True)
        print("Add Zoho CSV files from the dashboard. Stop the PyCharm run to stop the server.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise ValueError("Port must be between 1024 and 65535.")
    from .import_service import prepare_database
    settings = read_database_config()
    prepare_database(settings)
    serve(settings, args.port)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Dashboard could not start: {exc}", file=sys.stderr)
        sys.exit(1)
