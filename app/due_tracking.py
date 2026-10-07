"""Reconcile DUE_CREATED swaps to exact Due swap lookups within each country."""
import csv
import io
from datetime import datetime, time

from .missing_counterparts import csv_value
from .reconciliation_backend import ReconciliationFilter, _rows

MAX_EXPORT = 10000
GROUPS = {
    'all': 'true',
    'needs_review': "r.wallet_debit_count=0 AND r.resolution_status<>'resolved_paid_due'",
    'resolved': "r.resolution_status='resolved_paid_due'",
    'no_due': "r.due_record_status='no_due_in_loaded_data'",
    'matched': "r.due_record_status='matched'",
    'paid': "r.due_count=1 AND r.due_status='Paid'",
    'pending': "r.due_count=1 AND r.due_status='Pending'",
    'closed': "r.due_count=1 AND r.due_status='Closed'",
    'awaiting_due_export': "r.due_record_status='due_data_not_loaded'",
}
FIELDS = (
    'record_id', 'swap_reference', 'transaction_id', 'country', 'customer_id',
    'customer_name', 'customer_references', 'swap_amount', 'currency',
    'currency_source', 'swap_status', 'payment_method', 'created_on', 'swap_date',
    'station', 'vehicle_registration', 'due_count', 'due_record_id', 'due_reference',
    'due_amount', 'due_currency', 'due_status', 'due_creation_date', 'due_settlement_date',
    'due_record_status', 'repayment_status', 'wallet_debit_count', 'resolution_status',
    'source_file', 'source_row_number',
)


def selection(group):
    if group not in GROUPS:
        raise ValueError('Choose a known Due reconciliation group.')
    return GROUPS[group]


def _scope(filters=None, cursor=None):
    filters = filters or ReconciliationFilter()
    if not isinstance(filters, ReconciliationFilter) or filters.status is not None:
        raise ValueError('Use country and creation-date filters for due-created swaps.')
    conditions = ["s.payment_method='DUE_CREATED'", "s.country IN ('Kenya', 'Rwanda')"]
    values = []
    if filters.country:
        conditions.append('s.country=%s')
        values.append(filters.country)
    if filters.start_date:
        conditions.append('s.created_on >= %s')
        values.append(datetime.combine(filters.start_date, time.min))
    if filters.end_date:
        conditions.append('s.created_on <= %s')
        values.append(datetime.combine(filters.end_date, time.max))
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.strip():
            raise ValueError('The cursor must be a nonblank swap Record Id.')
        conditions.append('s.zoho_record_id > %s')
        values.append(cursor)
    return filters, ' AND '.join(conditions), values


def _query(conditions, *, group='all', limit=False):
    # Aggregate both lookups to keep one row per swap. Duplicate Dues need
    # review, and duplicates must never multiply totals or pick a paid record.
    group_condition = selection(group)
    return f"""WITH selected AS MATERIALIZED (
        SELECT s.zoho_record_id, s.swap_reference, s.transaction_id, s.country,
            s.customer_id, s.customer_name, s.customer_label, s.swap_amount, s.status,
            s.payment_method, s.created_on, s.swap_date, s.station_label, s.station_code,
            s.vehicle_registration, s.source_file, s.source_row_number
        FROM reconciliation.swap_transactions s WHERE {conditions}
    ), due_countries AS MATERIALIZED (
        SELECT DISTINCT country FROM reconciliation.dues
    ), enriched AS (
        SELECT s.zoho_record_id AS record_id, s.swap_reference, s.transaction_id,
        s.country, s.customer_id, coalesce(s.customer_name, s.customer_label) AS customer_name,
        m.customer_references, s.swap_amount,
        CASE WHEN cardinality(m.currencies)=1 THEN m.currencies[1] END AS currency,
        CASE WHEN cardinality(m.currencies)=1 THEN 'wallet_snapshot'
             WHEN cardinality(m.currencies)>1 THEN 'ambiguous_wallet_snapshot'
             ELSE 'unavailable' END AS currency_source,
        s.status AS swap_status, s.payment_method, s.created_on, s.swap_date,
        coalesce(s.station_label, s.station_code) AS station, s.vehicle_registration,
        w.wallet_debit_count,
        d.due_count, CASE WHEN d.due_count=1 THEN d.due_record_id END AS due_record_id,
        CASE WHEN d.due_count=1 THEN d.due_reference END AS due_reference,
        CASE WHEN d.due_count=1 THEN d.due_amount END AS due_amount,
        CASE WHEN d.due_count=1 THEN d.due_currency END AS due_currency,
        CASE WHEN d.due_count=1 THEN d.due_status END AS due_status,
        CASE WHEN d.due_count=1 THEN d.due_creation_date END AS due_creation_date,
        CASE WHEN d.due_count=1 THEN d.due_settlement_date END AS due_settlement_date,
        CASE WHEN d.due_count=1 THEN d.due_customer_id END AS due_customer_id,
        coalesce(d.dues, '[]'::jsonb) AS dues,
        dc.country IS NOT NULL AS due_data_loaded,
        s.source_file, s.source_row_number
        FROM selected s LEFT JOIN LATERAL (
            SELECT array_agg(DISTINCT nullif(btrim(w.currency), ''))
                       FILTER (WHERE nullif(btrim(w.currency), '') IS NOT NULL) AS currencies,
                   array_agg(DISTINCT w.customer_business_id)
                       FILTER (WHERE nullif(btrim(w.customer_business_id), '') IS NOT NULL) AS customer_references
            FROM reconciliation.wallets w
            WHERE w.country=s.country AND w.customer_crm_id=s.customer_id
              AND nullif(btrim(s.customer_id), '') IS NOT NULL
        ) m ON true LEFT JOIN LATERAL (
            SELECT count(*) AS due_count, min(d.zoho_record_id) AS due_record_id,
                min(d.due_reference) AS due_reference, min(d.due_amount) AS due_amount,
                min(nullif(btrim(d.currency), '')) AS due_currency,
                min(d.status) AS due_status, min(d.customer_id) AS due_customer_id,
                min(d.due_creation_date) AS due_creation_date,
                min(d.due_settlement_date) AS due_settlement_date,
                jsonb_agg(jsonb_build_object('record_id', d.zoho_record_id,
                    'reference', d.due_reference, 'amount', d.due_amount::text,
                    'original_amount', d.original_due_amount::text, 'currency', d.currency,
                    'status', d.status, 'type', d.due_type, 'customer_id', d.customer_id,
                    'payment_mode', d.payment_mode, 'transaction_id', d.transaction_id,
                    'creation_date', d.due_creation_date, 'settlement_date', d.due_settlement_date,
                    'source_modified_at', d.source_modified_at, 'source_file', d.source_file)
                    ORDER BY d.zoho_record_id) AS dues
            FROM reconciliation.dues d WHERE d.country=s.country AND d.swap_record_id=s.zoho_record_id
        ) d ON true LEFT JOIN LATERAL (
            SELECT count(*) AS wallet_debit_count
            FROM reconciliation.wallet_transactions w
            WHERE w.country=s.country AND w.transaction_id=s.transaction_id
              AND nullif(btrim(s.transaction_id), '') IS NOT NULL
              AND w.transaction_type='Debit' AND w.status='Committed' AND w.settled_against='Swap'
        ) w ON true LEFT JOIN due_countries dc ON dc.country=s.country
    ), reviewed AS (
        SELECT e.*,
            CASE WHEN due_count=0 AND NOT due_data_loaded THEN 'due_data_not_loaded'
                 WHEN due_count=0 THEN 'no_due_in_loaded_data'
                 WHEN due_count>1 THEN 'multiple_due_records'
                 WHEN swap_status IS DISTINCT FROM 'SUCCESS' THEN 'swap_not_successful'
                 WHEN nullif(btrim(customer_id), '') IS NULL OR nullif(btrim(due_customer_id), '') IS NULL THEN 'missing_customer_id'
                 WHEN customer_id<>due_customer_id THEN 'customer_mismatch'
                 WHEN swap_amount IS NULL OR due_amount IS NULL THEN 'missing_amount'
                 WHEN swap_amount<>due_amount THEN 'amount_mismatch'
                 WHEN due_currency IS NULL THEN 'missing_due_currency'
                 WHEN currency_source='ambiguous_wallet_snapshot' THEN 'ambiguous_swap_currency'
                 WHEN currency IS NOT NULL AND currency<>due_currency THEN 'currency_mismatch'
                 ELSE 'matched' END AS due_record_status,
            CASE WHEN due_count=1 THEN coalesce(due_status, 'status_not_supplied')
                 WHEN due_count>1 THEN 'ambiguous' ELSE 'not_verified' END AS repayment_status
        FROM enriched e
    ), resolved AS (
        SELECT r.*, CASE
            WHEN wallet_debit_count>0 THEN 'wallet_attached'
            WHEN due_record_status='matched' AND due_status='Paid' THEN 'resolved_paid_due'
            WHEN due_record_status='matched' AND due_status='Pending' THEN 'pending_due'
            WHEN due_record_status='matched' AND due_status='Closed' THEN 'closed_due_not_paid'
            ELSE 'needs_review' END AS resolution_status
        FROM reviewed r
    ) SELECT * FROM resolved r WHERE {group_condition}
      ORDER BY r.record_id {'LIMIT %s' if limit else ''}"""


def summary(conn, filters=None):
    filters, conditions, values = _scope(filters)
    groups = _rows(conn, f"""SELECT r.country, r.currency, r.currency_source,
        count(*) AS swap_count, sum(r.swap_amount) AS swap_amount,
        count(*) FILTER (WHERE r.swap_status='SUCCESS') AS successful_count,
        count(*) FILTER (WHERE r.swap_amount IS NULL) AS missing_amount_count,
        count(*) FILTER (WHERE r.due_count>0) AS linked_count,
        count(*) FILTER (WHERE r.due_record_status='matched') AS matched_count,
        count(*) FILTER (WHERE {GROUPS['needs_review']}) AS needs_review_count,
        count(*) FILTER (WHERE {GROUPS['resolved']}) AS resolved_count,
        count(*) FILTER (WHERE r.wallet_debit_count>0) AS wallet_attached_count,
        count(*) FILTER (WHERE {GROUPS['no_due']}) AS no_due_count,
        count(*) FILTER (WHERE {GROUPS['awaiting_due_export']}) AS awaiting_due_export_count,
        count(*) FILTER (WHERE {GROUPS['paid']}) AS paid_count,
        count(*) FILTER (WHERE {GROUPS['pending']}) AS pending_count,
        count(*) FILTER (WHERE {GROUPS['closed']}) AS closed_count,
        min(r.created_on) AS first_created_on, max(r.created_on) AS last_created_on
        FROM ({_query(conditions)}) r GROUP BY 1,2,3 ORDER BY 1,2,3""", values)
    count_fields = ('swap_count', 'successful_count', 'linked_count', 'matched_count',
                    'needs_review_count', 'resolved_count', 'wallet_attached_count', 'no_due_count', 'awaiting_due_export_count',
                    'paid_count', 'pending_count', 'closed_count')
    result = {'filters': filters.as_dict(), 'groups': groups,
              'source_coverage_status': 'not_verified', 'matching_key': 'country + Swapping Transaction.id',
              'repayment_status_source': 'exported_due_status'}
    source_conditions = "d.country IN ('Kenya', 'Rwanda')"
    source_values = []
    if filters.country:
        source_conditions += ' AND d.country=%s'
        source_values.append(filters.country)
    # A Due may be created outside the selected swap dates. Show source coverage
    # for all loaded Due dates, and do not confuse unlinked Pending Dues with zero debt.
    result['due_sources'] = _rows(conn, f"""WITH capped_files AS MATERIALIZED (
        SELECT DISTINCT source_file FROM reconciliation.import_batches
        WHERE dataset='due' AND rows_read>=200000
    ) SELECT d.country, count(*) AS record_count,
        count(*) FILTER (WHERE nullif(btrim(d.swap_record_id), '') IS NOT NULL) AS swap_lookup_count,
        count(*) FILTER (WHERE nullif(btrim(d.swap_record_id), '') IS NULL) AS missing_swap_lookup_count,
        count(*) FILTER (WHERE d.status='Paid') AS paid_count,
        count(*) FILTER (WHERE d.status='Pending') AS pending_count,
        count(*) FILTER (WHERE d.status='Closed') AS closed_count,
        bool_or(c.source_file IS NOT NULL) AS export_limit_reached
        FROM reconciliation.dues d LEFT JOIN capped_files c ON c.source_file=d.source_file
        WHERE {source_conditions} GROUP BY d.country ORDER BY d.country""", source_values)
    result.update({field: sum(g[field] for g in groups) for field in count_fields})
    result['by_group'] = {'all': result['swap_count'], **{
        group: result[field] for group, field in (
            ('needs_review', 'needs_review_count'), ('no_due', 'no_due_count'),
            ('matched', 'matched_count'), ('resolved', 'resolved_count'), ('awaiting_due_export', 'awaiting_due_export_count'),
            ('paid', 'paid_count'), ('pending', 'pending_count'), ('closed', 'closed_count'))}}
    return result


def paid_resolution_query():
    """Shared exact-match rule used to clear swaps from counterpart review.

    Keep this live: a later Paid -> Pending edit must reopen a cached swap gap.
    """
    _, conditions, _ = _scope()
    return 'SELECT record_id, country FROM (' + _query(conditions, group='resolved') + ') paid'


def page(conn, filters=None, *, limit=50, cursor=None, group='all'):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Page size must be between 1 and 100.')
    _, conditions, values = _scope(filters, cursor)
    rows = _rows(conn, _query(conditions, group=group, limit=True), [*values, limit + 1])
    more = len(rows) > limit
    records = rows[:limit]
    return {'records': records, 'has_more': more,
            'next_cursor': records[-1]['record_id'] if more else None}


def export(conn, filters=None, *, group='all'):
    _, conditions, values = _scope(filters)
    records = _rows(conn, _query(conditions, group=group, limit=True), [*values, MAX_EXPORT + 1])
    if len(records) > MAX_EXPORT:
        raise ValueError('More than 10,000 swaps. Narrow the country or date range before exporting.')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for record in records:
        writer.writerow({field: record.get(field) if field in ('swap_amount', 'due_amount')
                         else csv_value(field, record.get(field)) for field in FIELDS})
    return ('\ufeff' + stream.getvalue()).encode('utf-8')
