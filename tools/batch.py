"""Optional config.json batch imports; main.py remains the entry point."""
import argparse
import json
from pathlib import Path

from app import PROJECT_ROOT
from app.import_csv import dry_run, import_file
from app.db_config import read_database_config

PROJECT=PROJECT_ROOT

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
            conn.execute((PROJECT / "sql" / "schema.sql").read_text(encoding="utf-8"))
            conn.execute((PROJECT / "sql" / "wallets.sql").read_text(encoding="utf-8"))
            conn.execute((PROJECT / "sql" / "reconciliation.sql").read_text(encoding="utf-8"))
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

