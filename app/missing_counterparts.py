"""Export swaps without wallet deductions and wallet deductions without swaps."""
import argparse
import csv
import json
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path

import psycopg
from .db_config import read_database_config
from .reconciliation_backend import COUNTRIES, ReconciliationFilter

from . import PROJECT_ROOT

PROJECT = PROJECT_ROOT
ELIGIBLE = """SELECT * FROM reconciliation.wallet_transactions
    WHERE transaction_type='Debit' AND status='Committed' AND settled_against='Swap'"""

QUERIES = {
    'wallet_without_swap': f"""
        WITH eligible_wallets AS NOT MATERIALIZED ({ELIGIBLE}), bounds AS (
            SELECT c.country, 1 AS records,
                   (SELECT min(created_on) FROM reconciliation.swap_transactions WHERE country=c.country) AS first_seen,
                   (SELECT max(created_on) FROM reconciliation.swap_transactions WHERE country=c.country) AS last_seen
            FROM (SELECT DISTINCT country FROM reconciliation.swap_transactions) c
        )
        SELECT w.country, w.zoho_record_id AS wallet_record_id, w.transaction_id,
               w.reference AS wallet_reference, w.wallet_id, m.wallet_code,
               m.customer_crm_id AS customer_id, m.customer_business_id AS customer_reference,
               m.customer_name, w.amount AS wallet_amount, m.currency AS currency,
               w.created_on, w.status AS wallet_status, w.transaction_type, w.settled_against,
               CASE WHEN nullif(btrim(w.transaction_id),'') IS NULL THEN 'missing_transaction_id'
                    WHEN nullif(btrim(w.country),'') IS NULL THEN 'missing_country'
                    ELSE 'no_swap_in_loaded_data' END AS missing_counterpart_reason,
               b.first_seen AS observed_first_swap, b.last_seen AS observed_last_swap,
               CASE WHEN b.records IS NULL THEN 'no_counterpart_data_loaded'
                    WHEN w.created_on IS NULL OR b.first_seen IS NULL THEN 'timestamp_unavailable'
                    WHEN w.created_on < b.first_seen THEN 'before_observed_counterpart_start'
                    WHEN w.created_on > b.last_seen THEN 'after_observed_counterpart_end'
                    ELSE 'within_observed_counterpart_span' END AS date_context,
               w.source_file, w.source_row_number, 'not_verified' AS source_coverage_status
        FROM eligible_wallets w
        LEFT JOIN reconciliation.wallets m ON m.country=w.country AND m.zoho_record_id=w.wallet_id
        LEFT JOIN bounds b ON b.country=w.country
        WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation.swap_transactions s
            WHERE s.country=w.country AND s.transaction_id=w.transaction_id
              AND nullif(btrim(w.transaction_id),'') IS NOT NULL
              AND nullif(btrim(w.country),'') IS NOT NULL
        ) {{filters}}
    """,
    'swap_without_wallet': f"""
        WITH eligible_wallets AS NOT MATERIALIZED ({ELIGIBLE}), bounds AS (
            SELECT c.country, 1 AS records,
                   (SELECT min(created_on) FROM eligible_wallets WHERE country=c.country) AS first_seen,
                   (SELECT max(created_on) FROM eligible_wallets WHERE country=c.country) AS last_seen
            FROM (SELECT DISTINCT country FROM eligible_wallets) c
        ), customers AS (
            SELECT country, customer_crm_id,
                   array_agg(DISTINCT customer_name) FILTER (WHERE customer_name IS NOT NULL) AS names,
                   array_agg(DISTINCT customer_business_id) FILTER (WHERE customer_business_id IS NOT NULL) AS customer_references,
                   array_agg(DISTINCT zoho_record_id) AS wallet_record_ids,
                   array_agg(DISTINCT currency) FILTER (WHERE currency IS NOT NULL) AS currencies
            FROM reconciliation.wallets WHERE customer_crm_id IS NOT NULL
            GROUP BY country, customer_crm_id
        )
        SELECT s.country, s.zoho_record_id AS swap_record_id, s.transaction_id,
               s.swap_reference, s.customer_id,
               coalesce(nullif(s.customer_name,''),nullif(s.customer_label,''),
                        CASE WHEN cardinality(c.names)=1 THEN c.names[1] END) AS customer_name,
               c.customer_references AS mapped_customer_references,
               c.wallet_record_ids AS mapped_wallet_record_ids,
               s.swap_amount, CASE WHEN cardinality(c.currencies)=1 THEN c.currencies[1] END AS currency,
               c.currencies AS currency_candidates,
               s.created_on, s.swap_date, s.status AS swap_status, s.payment_method,
               coalesce(s.status='SUCCESS' AND s.payment_method='WALLET',false) AS wallet_payment_expected,
               s.vehicle_registration, s.station_label, s.station_code, s.city,
               CASE WHEN nullif(btrim(s.transaction_id),'') IS NULL THEN 'missing_transaction_id'
                    WHEN nullif(btrim(s.country),'') IS NULL THEN 'missing_country'
                    ELSE 'no_committed_swap_wallet_debit_in_loaded_data' END AS missing_counterpart_reason,
               b.first_seen AS observed_first_wallet_deduction,
               b.last_seen AS observed_last_wallet_deduction,
               CASE WHEN b.records IS NULL THEN 'no_counterpart_data_loaded'
                    WHEN s.created_on IS NULL OR b.first_seen IS NULL THEN 'timestamp_unavailable'
                    WHEN s.created_on < b.first_seen THEN 'before_observed_counterpart_start'
                    WHEN s.created_on > b.last_seen THEN 'after_observed_counterpart_end'
                    ELSE 'within_observed_counterpart_span' END AS date_context,
               s.source_file, s.source_row_number, 'not_verified' AS source_coverage_status
        FROM reconciliation.swap_transactions s
        LEFT JOIN customers c ON c.country=s.country AND c.customer_crm_id=s.customer_id
        LEFT JOIN bounds b ON b.country=s.country
        WHERE NOT EXISTS (
            SELECT 1 FROM eligible_wallets w
            WHERE w.country=s.country AND w.transaction_id=s.transaction_id
              AND nullif(btrim(s.transaction_id),'') IS NOT NULL
              AND nullif(btrim(s.country),'') IS NOT NULL
        ) {{filters}}
    """,
}

# Count and page the small set of missing source IDs before fetching customer
# details. Keeping this scan unordered avoids a LIMIT-driven random index scan
# through hundreds of thousands of otherwise matched source records.
TRACKING_QUERIES = {
    'wallet_without_swap': f"""SELECT w.country, w.zoho_record_id
        FROM ({ELIGIBLE}) w WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation.swap_transactions s
            WHERE s.country=w.country AND s.transaction_id=w.transaction_id
        ) {{filters}}""",
    'swap_without_wallet': f"""SELECT s.country, s.zoho_record_id
        FROM reconciliation.swap_transactions s WHERE NOT EXISTS (
            SELECT 1 FROM ({ELIGIBLE}) w
            WHERE w.country=s.country AND w.transaction_id=s.transaction_id
        ) {{filters}}""",
}
UNMATCHABLE_QUERIES = {
    'wallet_without_swap': f'SELECT w.country, w.zoho_record_id FROM ({ELIGIBLE}) w WHERE true {{filters}}',
    'swap_without_wallet': 'SELECT s.country, s.zoho_record_id FROM reconciliation.swap_transactions s WHERE true {filters}',
}


def report_query(group, filters, *, classification=None, cursor=None, order='country', detail=True, record_ids=None, cached=False):
    alias = 'w' if group == 'wallet_without_swap' else 's'
    record = ('wallet_record_id' if group == 'wallet_without_swap' else 'swap_record_id') if cached else 'zoho_record_id'
    clauses, values = [], []
    if filters.country:
        clauses.append(f'{alias}.country=%s'); values.append(filters.country)
    if filters.start_date:
        clauses.append(f'{alias}.created_on >= %s')
        values.append(datetime.combine(filters.start_date, time.min))
    if filters.end_date:
        clauses.append(f'{alias}.created_on <= %s')
        values.append(datetime.combine(filters.end_date, time.max))
    if filters.status:
        raise ValueError('Use country and date filters for missing-counterpart reports.')
    identifiers = f"nullif(btrim({alias}.transaction_id),'') IS NOT NULL AND nullif(btrim({alias}.country),'') IS NOT NULL"
    if classification == 'valid':
        clauses.append('(' + identifiers + ')')
    elif classification == 'unmatchable':
        clauses.append('NOT (' + identifiers + ')')
    elif classification is not None:
        raise ValueError('Unknown identifier classification.')
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.strip():
            raise ValueError('Provide a nonblank source Record Id cursor.')
        clauses.append(f'{alias}.{record} > %s'); values.append(cursor)
    if record_ids is not None:
        clauses.append(f'{alias}.{record} = ANY(%s)'); values.append(record_ids)
    suffix = ' AND ' + ' AND '.join(clauses) if clauses else ''
    if cached:
        view = 'dashboard_wallet_gaps' if group == 'wallet_without_swap' else 'dashboard_swap_gaps'
        fields = f'{alias}.*' if detail else f'{alias}.country, {alias}.{record} AS zoho_record_id'
        statement = f'SELECT {fields} FROM reconciliation.{view} {alias} WHERE true{suffix}'
    elif detail:
        statement = QUERIES[group].format(filters=suffix)
    elif classification == 'valid':
        statement = TRACKING_QUERIES[group].format(filters=suffix)
    elif classification == 'unmatchable':
        statement = UNMATCHABLE_QUERIES[group].format(filters=suffix)
    else:
        raise ValueError('Core tracking queries require an identifier classification.')
    if order == 'country':
        statement += f' ORDER BY {alias}.country NULLS LAST, {alias}.{record}'
    elif order == 'id':
        statement += f' ORDER BY {alias}.{record}'
    elif order is not None:
        raise ValueError('Unknown report ordering.')
    return statement, values


def csv_value(field, value):
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    # Protect formula-like source text when a report is opened in a spreadsheet.
    # Decimal amounts remain exact numeric strings; identifiers never become floats.
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def write_missing_reports(conn, output=None, filters=None):
    filters = filters or ReconciliationFilter()
    output = Path(output or PROJECT / 'reports' / 'missing_counterparts')
    output.mkdir(parents=True, exist_ok=True)
    report = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'filters': filters.as_dict(), 'source_coverage_status': 'not_verified',
              'matching_rule': 'Exact country and nonblank Transaction ID; one row per source Record Id.',
              'wallet_scope': 'Committed Debit wallet transactions settled against Swap.',
              'swap_scope': 'All loaded swaps, including unsuccessful and non-wallet payment methods.',
              'date_filter_rule': 'Filters select each group by its own creation time; counterparts are searched across all loaded dates.',
              'note': 'These are missing from loaded exports, not proof of absence in Zoho. Missing identifiers are separately labelled; non-wallet swaps may legitimately have no wallet debit.',
              'groups': {}}
    paths = []
    try:
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='120s'")
            for group in QUERIES:
                target = output / (group + '.csv')
                temporary = target.with_suffix('.csv.tmp')
                paths.append((temporary, target))
                countries, reasons, expected = Counter(), Counter(), Counter()
                unmatchable, missing = Counter(), Counter()
                statement, values = report_query(group, filters)
                with conn.cursor(name=group) as cursor, temporary.open('w', encoding='utf-8-sig', newline='') as stream:
                    cursor.execute(statement, values)
                    fields = [column.name for column in cursor.description]
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    for items in cursor:
                        row = dict(zip(fields, items))
                        countries[row['country'] or 'Unknown'] += 1
                        reasons[row['missing_counterpart_reason']] += 1
                        if row['missing_counterpart_reason'] in ('missing_transaction_id', 'missing_country'):
                            unmatchable[row['country'] or 'Unknown'] += 1
                        else:
                            missing[row['country'] or 'Unknown'] += 1
                        if row.get('wallet_payment_expected'):
                            expected[row['country'] or 'Unknown'] += 1
                        writer.writerow({field: csv_value(field, value) for field, value in row.items()})
                report['groups'][group] = {'file': target.name, 'rows': sum(countries.values()),
                                          'by_country': dict(countries), 'by_reason': dict(reasons),
                                          'unmatchable_records_by_country': dict(unmatchable),
                                          'valid_identifier_no_counterpart_records_by_country': dict(missing)}
                if group == 'swap_without_wallet':
                    report['groups'][group]['successful_wallet_payment_rows_by_country'] = dict(expected)
        summary = output / 'summary.json'
        summary_tmp = summary.with_suffix('.json.tmp')
        paths.append((summary_tmp, summary))
        summary_tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        for temporary, target in paths:
            temporary.replace(target)
    finally:
        for temporary, _ in paths:
            temporary.unlink(missing_ok=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--country', choices=COUNTRIES)
    parser.add_argument('--start', help='First included source creation date, YYYY-MM-DD')
    parser.add_argument('--end', help='Last included source creation date, YYYY-MM-DD')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    filters = ReconciliationFilter(country=args.country, start_date=args.start, end_date=args.end)
    with psycopg.connect(**read_database_config(), connect_timeout=15, autocommit=True) as conn:
        report = write_missing_reports(conn, args.output, filters)
    for group, result in report['groups'].items():
        print(f'{group}: {result["rows"]:,} records; {result["by_country"]}', flush=True)
        print(f'  Valid identifiers, no loaded counterpart: {sum(result["valid_identifier_no_counterpart_records_by_country"].values()):,}; '
              f'unmatchable identifiers: {sum(result["unmatchable_records_by_country"].values()):,}', flush=True)
    print(f'Reports saved in {args.output or PROJECT / "reports" / "missing_counterparts"}', flush=True)
    return report


if __name__ == '__main__':
    main()
