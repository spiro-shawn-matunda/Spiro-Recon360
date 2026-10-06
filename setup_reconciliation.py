"""Run once in PyCharm to add TEC-188's backend view to the loaded database."""
import sys
from pathlib import Path
import psycopg
from db_config import read_database_config


def main():
    sql_path = Path(__file__).resolve().parent / "sql/06_backend_reconciliation.sql"
    with psycopg.connect(**read_database_config(), connect_timeout=15) as conn:
        conn.execute(sql_path.read_text(encoding="utf-8"))
    print("TEC-188 backend view is ready. Run reconcile.py to generate review reports.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
