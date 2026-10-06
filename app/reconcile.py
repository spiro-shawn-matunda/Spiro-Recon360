"""Run in PyCharm to build reconciliation summaries and review-candidate reports."""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from .db_config import read_database_config
from .reconciliation_backend import COUNTRIES, STATUSES, ReconciliationBackend, ReconciliationFilter

from . import PROJECT_ROOT

PROJECT = PROJECT_ROOT
EXPORT_FIELDS = (
    "country", "wallet_record_id", "transaction_id", "wallet_reference",
    "wallet_id", "wallet_code", "wallet_customer_id", "wallet_customer_reference",
    "wallet_customer_name", "wallet_amount", "wallet_currency", "wallet_created_on",
    "reconciliation_status", "review_reasons", "coverage_assessment",
    "swap_count", "successful_swap_count", "successful_swap_amount",
    "successful_swap_created_on", "swap_references", "successful_payment_methods",
    "customer_mapping_status", "swap_customer_lookup_check", "source_coverage_status",
)


def write_report(conn, filters, output_directory):
    """Use one repeatable-read transaction for a consistent report and pagination."""
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    # Build temporary files, replacing prior reports only after a complete export.
    summary_path = output_directory / "reconciliation_summary.json"
    candidates_path = output_directory / "review_candidates.csv"
    summary_tmp = summary_path.with_suffix(".json.tmp")
    candidates_tmp = candidates_path.with_suffix(".csv.tmp")
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '120s'")
            backend = ReconciliationBackend(conn)
            report = backend.summary(filters)
            report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
            report["candidate_count"] = 0
            report["note"] = "Review candidates require investigation. Source coverage and refund eligibility are not verified."
            with candidates_tmp.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS)
                writer.writeheader()
                cursor = None
                while True:
                    page = backend.list_records(filters, review_only=True, limit=1000, after_record_id=cursor)
                    for record in page["records"]:
                        row = {field: record.get(field) for field in EXPORT_FIELDS}
                        for field, value in row.items():
                            if isinstance(value, list):
                                row[field] = json.dumps(value, ensure_ascii=False)
                        writer.writerow(row)
                        report["candidate_count"] += 1
                    if not page["has_more"]:
                        break
                    cursor = page["next_cursor"]
            if report["candidate_count"] != report["needs_review_count"]:
                raise RuntimeError("Candidate export count does not agree with the summary.")
            summary_tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        candidates_tmp.replace(candidates_path)
        summary_tmp.replace(summary_path)
    finally:
        summary_tmp.unlink(missing_ok=True)
        candidates_tmp.unlink(missing_ok=True)
    return report, summary_path, candidates_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", choices=COUNTRIES, help="Omit to report both countries separately")
    parser.add_argument("--start", help="First included deduction date, YYYY-MM-DD")
    parser.add_argument("--end", help="Last included deduction date, YYYY-MM-DD")
    parser.add_argument("--status", choices=STATUSES)
    parser.add_argument("--output", type=Path, help="Report folder; default reports/reconciliation/<country or all>")
    args = parser.parse_args(argv)
    filters = ReconciliationFilter(country=args.country, start_date=args.start, end_date=args.end, status=args.status)
    output = args.output or PROJECT / "reports" / "reconciliation" / (args.country or "all")
    with psycopg.connect(**read_database_config(), connect_timeout=15, autocommit=True) as conn:
        report, summary_path, candidates_path = write_report(conn, filters, output)
    for group in report["groups"]:
        print(f"{group['country']} / {group['currency']} / {group['status']}: "
              f"{group['deduction_count']:,}; needing review: {group['needs_review_count']:,}", flush=True)
    print(f"Review candidates exported: {report['candidate_count']:,}")
    print(f"Summary: {summary_path}")
    print(f"Candidates: {candidates_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Reconciliation failed: {exc}", file=sys.stderr)
        sys.exit(1)
