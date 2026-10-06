"""Initialize a new disposable test database and run the integration suite."""
import os
import sys
import unittest
from pathlib import Path

import psycopg

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(1, str(PROJECT / 'tests'))
from fixtures import load_fixtures
from app.dashboard_cache import install_cache


def main():
    dsn = os.environ.get('SPIRO_TEST_DSN')
    if not dsn:
        raise SystemExit('Set SPIRO_TEST_DSN to a new empty disposable PostgreSQL database. Tests never use .env.')
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.transaction():
            occupied = conn.execute("""SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
                  AND c.relkind IN ('r','p','v','m','f')
            )""").fetchone()[0]
            if occupied:
                raise SystemExit('Refusing to initialize a database with user tables/views. Create a new empty test database.')
            for name in ('schema.sql', 'wallets.sql', 'reconciliation.sql', 'tracking_indexes.sql'):
                conn.execute((PROJECT / 'sql' / name).read_text(encoding='utf-8'))
            load_fixtures(conn)
            install_cache(conn)
    suite = unittest.defaultTestLoader.discover(str(PROJECT / 'tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
