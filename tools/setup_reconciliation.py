"""Run once in PyCharm to add reconciliation's backend view to the loaded database."""
import sys
from pathlib import Path
import psycopg
from app.db_config import read_database_config


def main():
    sql_path = Path(__file__).resolve().parents[1] / "sql/reconciliation.sql"
    with psycopg.connect(**read_database_config(), connect_timeout=15) as conn:
        conn.execute(sql_path.read_text(encoding="utf-8"))
    print("reconciliation backend view is ready. Run main.py --export-review to generate review reports.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
