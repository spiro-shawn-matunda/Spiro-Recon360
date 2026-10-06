"""Run in PyCharm to prepare PostgreSQL and start the self-service dashboard.

Connection details come from .env. Add and validate Zoho CSVs in the browser.
Use --import-config for the older config.json batch import workflow.
"""
import argparse
import json
import sys
from pathlib import Path

from import_csv import dry_run, import_file
from db_config import read_database_config

PROJECT = Path(__file__).resolve().parent


def read_config():
    config = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
    files = []
    for dataset, key in (("wallet_master", "wallet_master_files"), ("wallet", "wallet_files"), ("swap", "swap_files")):
        for item in config.get(key, []):
            path = Path(item)
            if not path.is_absolute():
                path = PROJECT / path
            if not path.is_file():
                raise ValueError(f"CSV not found: {path}. Update config.json or copy the file into data/.")
            files.append((dataset, path))
    if not files:
        raise ValueError("Add wallet_master_files, wallet_files, or swap_files to config.json.")
    return config, files


def run_import(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate CSVs without accessing PostgreSQL")
    parser.add_argument("--wallet-master-only", action="store_true", help="Load only Wallets/customer mappings")
    parser.add_argument("--file-prefix", help="Import only configured files whose names start with this prefix")
    args = parser.parse_args(argv)
    config, files = read_config()
    if args.wallet_master_only:
        files = [(dataset, path) for dataset, path in files if dataset == 'wallet_master']
        if not files:
            raise ValueError("Add wallet_master_files to config.json first.")
    if args.file_prefix:
        files = [(dataset, path) for dataset, path in files if path.name.startswith(args.file_prefix)]
        if not files:
            raise ValueError(f"No configured files start with {args.file_prefix!r}.")
    if args.dry_run:
        for dataset, path in files:
            dry_run(path, dataset)
        print("Validation finished. Run main.py --import-config to import these configured files into Spiro.")
        return

    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise ValueError("Missing driver. In PyCharm Terminal run: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt") from None

    db = read_database_config()
    print(f"Connecting to {db['host']}:{db['port']}/{db['dbname']}...", flush=True)
    with psycopg.connect(**db, connect_timeout=15, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            actual_database, actual_user = cur.fetchone()
            print(f"Connected to database {actual_database} as {actual_user}.", flush=True)
        with conn.transaction():
            conn.execute((PROJECT / "sql" / "01_create_tables.sql").read_text(encoding="utf-8"))
            conn.execute((PROJECT / "sql" / "03_wallet_customer_mapping.sql").read_text(encoding="utf-8"))
            conn.execute((PROJECT / "sql" / "06_backend_reconciliation.sql").read_text(encoding="utf-8"))
        print("Reconciliation tables are ready.", flush=True)
        for dataset, path in files:
            import_file(conn, path, dataset, sql)
        for table in ("wallets", "wallet_transactions", "swap_transactions"):
            conn.execute(sql.SQL("ANALYZE {}").format(sql.Identifier("reconciliation", table)))
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM reconciliation.wallets")
            print(f"Wallet master records in database: {cur.fetchone()[0]:,}")
            cur.execute("SELECT count(*) FROM reconciliation.wallet_transactions")
            print(f"Wallet records in database: {cur.fetchone()[0]:,}")
            cur.execute("SELECT count(*) FROM reconciliation.swap_transactions")
            print(f"Swap records in database: {cur.fetchone()[0]:,}")
            cur.execute("SELECT country, reconciliation_status, count(*) FROM reconciliation.wallet_swap_review GROUP BY 1,2 ORDER BY 1,2")
            for country, status, count in cur.fetchall():
                print(f"{country} / {status}: {count:,}")
            cur.execute("SELECT country, customer_mapping_status, count(*) FROM reconciliation.wallet_swap_customer_review GROUP BY 1,2 ORDER BY 1,2")
            for country, status, count in cur.fetchall():
                print(f"{country} / Customer mapping {status}: {count:,}")
    print("Import finished. Refresh the reconciliation schema in DBeaver.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--export-unmatched' in argv:
        from missing_counterparts import main as export_missing
        return export_missing([option for option in argv if option != '--export-unmatched'])
    # Keep existing PyCharm validation/import shortcuts compatible.
    if any(option.split('=', 1)[0] in ('--import-config', '--dry-run', '--wallet-master-only', '--file-prefix') for option in argv):
        return run_import([option for option in argv if option != '--import-config'])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise ValueError('Port must be between 1024 and 65535.')
    from dashboard import serve
    from import_service import prepare_database
    settings = read_database_config()
    print('Preparing the reconciliation database...', flush=True)
    prepare_database(settings)
    print('Database ready.', flush=True)
    serve(settings, args.port)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # PostgreSQL reports useful connection/permission errors without secrets.
        print("Could not start Spiro. Check that PostgreSQL is running, .env is complete, and the dashboard port is available.", file=sys.stderr)
        sys.exit(1)
