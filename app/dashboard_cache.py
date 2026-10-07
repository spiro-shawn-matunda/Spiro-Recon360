"""Store derived results once per source revision, never cache CRM credentials."""
import psycopg
from psycopg import sql

from . import PROJECT_ROOT
from .missing_counterparts import report_query
from .reconciliation_backend import ReconciliationFilter

VIEWS = ('dashboard_reconciliation', 'dashboard_wallet_gaps', 'dashboard_swap_gaps')
CACHE_LOCK = 18801


def install_cache(conn):
    """Called with schema setup; gap views reuse the existing report queries."""
    conn.execute((PROJECT_ROOT / 'sql/dashboard_cache.sql').read_text(encoding='utf-8'))
    for group, view, record in (
        ('wallet_without_swap', 'dashboard_wallet_gaps', 'wallet_record_id'),
        ('swap_without_wallet', 'dashboard_swap_gaps', 'swap_record_id'),
    ):
        # Cache the full source gap. Paid Due resolutions are checked live so
        # status edits reopen cases without rebuilding the large wallet cache.
        statement, _ = report_query(group, ReconciliationFilter(), order=None, include_resolved=True)
        conn.execute(sql.SQL('CREATE MATERIALIZED VIEW IF NOT EXISTS {} AS {} WITH NO DATA').format(
            sql.Identifier('reconciliation', view), sql.SQL(statement)))
        conn.execute(sql.SQL('CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} ({})').format(
            sql.Identifier(view + '_record_idx'), sql.Identifier('reconciliation', view), sql.Identifier(record)))
        conn.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {} (country, created_on)').format(
            sql.Identifier(view + '_date_idx'), sql.Identifier('reconciliation', view)))


def cache_ready(conn):
    return conn.execute('''SELECT cached_revision IS NOT NULL AND cached_revision=source_revision
        FROM reconciliation.dashboard_cache_state WHERE singleton''').fetchone()[0]


def refresh_cache(conn):
    """Publish all three views atomically. A rollback leaves the cache dirty.

    Lock source tables before the revision row so imports can finish without a
    lock-order deadlock. Readers use one complete revision or the live queries.
    """
    if cache_ready(conn):
        return False
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (CACHE_LOCK,))
        conn.execute("SET LOCAL lock_timeout = '120s'")
        conn.execute("SET LOCAL statement_timeout = '180s'")
        conn.execute('''LOCK TABLE reconciliation.wallets,
            reconciliation.wallet_transactions, reconciliation.swap_transactions IN SHARE MODE''')
        if cache_ready(conn):
            return False
        conn.execute("SET LOCAL work_mem = '64MB'")
        for view in VIEWS:
            conn.execute(sql.SQL('REFRESH MATERIALIZED VIEW {}').format(sql.Identifier('reconciliation', view)))
            conn.execute(sql.SQL('ANALYZE {}').format(sql.Identifier('reconciliation', view)))
        conn.execute('''UPDATE reconciliation.dashboard_cache_state
            SET cached_revision=source_revision, refreshed_at=clock_timestamp() WHERE singleton''')
    return True


def ensure_cache(settings):
    with psycopg.connect(**settings, connect_timeout=15, autocommit=True) as conn:
        refresh_cache(conn)
