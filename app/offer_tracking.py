"""Conservative SOC voucher evidence. Never infer coverage from an offer name."""
import csv
import io
import json
from datetime import datetime, time

from .missing_counterparts import csv_value
from .reconciliation_backend import ReconciliationFilter, serialize

GROUPS = {'all': 'true', 'resolved': "r.resolution_status='resolved_offer'",
          'needs_review': "r.resolution_status='needs_review'",
          'wallet_attached': "r.resolution_status='wallet_attached'"}
MAX_EXPORT = 10000


def selection(group):
    if group not in GROUPS:
        raise ValueError('Choose a known offer result group.')
    return GROUPS[group]


def _decimal(field):
    # Raw swap SOC fields remain strings; malformed values must stay in review.
    value = "s.raw_record->>'" + field + "'"
    return f"CASE WHEN {value} ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN ({value})::numeric END"


def query(filters=None, *, group='all', cursor=None):
    filters = filters or ReconciliationFilter()
    if filters.status:
        raise ValueError('Use country and swap dates for offer results.')
    clauses, values = ["s.payment_method='OFFER_APPLIED'"], []
    for column, value in [('country', filters.country),
                          ('created_on >=', datetime.combine(filters.start_date, time.min) if filters.start_date else None),
                          ('created_on <=', datetime.combine(filters.end_date, time.max) if filters.end_date else None)]:
        if value is not None:
            clauses.append('s.' + column + ('=%s' if column == 'country' else ' %s'))
            values.append(value)
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.strip():
            raise ValueError('Provide a nonblank source Record Id cursor.')
        clauses.append('s.zoho_record_id>%s'); values.append(cursor)
    predicate = selection(group)
    statement = '''WITH budgets AS MATERIALIZED (
        SELECT country, allocation_reference, sum(consumed_soc) AS loaded_consumed_soc,
            bool_and(consumed_soc IS NOT NULL AND consumed_soc>=0) AS valid_consumptions
        FROM reconciliation.offer_consumptions GROUP BY country, allocation_reference
    ), candidates AS (
        SELECT s.zoho_record_id AS record_id, s.country, s.customer_id, s.customer_name,
            s.swap_reference, s.status AS swap_status, s.swap_amount, s.created_on,
            s.source_file, s.source_row_number, s.transaction_id,
            ''' + _decimal('Net SOC') + ''' AS net_soc,
            ''' + _decimal('Billable SOC') + ''' AS billable_soc,
            ''' + _decimal('Non-Billable SOC') + ''' AS non_billable_soc,
            cm.currency, cm.currency_count, cm.business_id, cm.business_count,
            c.*, a.*, b.loaded_consumed_soc, b.valid_consumptions,
            (SELECT count(*) FROM reconciliation.wallet_transactions w
             WHERE w.country=s.country AND w.transaction_id=s.transaction_id
             AND nullif(btrim(s.transaction_id),'') IS NOT NULL
             AND w.transaction_type='Debit' AND w.status='Committed' AND w.settled_against='Swap') AS wallet_debit_count
        FROM reconciliation.swap_transactions s
        LEFT JOIN LATERAL (
            SELECT min(currency) AS currency, count(DISTINCT nullif(btrim(currency),'')) AS currency_count,
                min(customer_business_id) AS business_id,
                count(DISTINCT nullif(btrim(customer_business_id),'')) AS business_count
            FROM reconciliation.wallets w WHERE w.country=s.country AND w.customer_crm_id=s.customer_id
        ) cm ON true
        LEFT JOIN LATERAL (
            SELECT count(*) AS consumption_count, min(consumption_reference) AS consumption_reference,
                min(allocation_reference) AS allocation_reference,
                min(customer_business_id) AS consumption_customer_id,
                min(currency) AS consumption_currency, min(transaction_type) AS consumption_type,
                min(consumption_date) AS consumption_date, min(consumed_soc) AS consumed_soc,
                min(allocated_soc) AS allocated_soc, min(remaining_soc) AS remaining_soc,
                min(allocated_discount) AS allocated_discount, min(consumed_discount) AS consumed_discount,
                min(remaining_discount) AS remaining_discount,
                coalesce(jsonb_agg(to_jsonb(c)-'raw_record' ORDER BY c.zoho_record_id),'[]') AS consumptions
            FROM reconciliation.offer_consumptions c WHERE c.country=s.country AND c.swap_record_id=s.zoho_record_id
        ) c ON true
        LEFT JOIN LATERAL (
            SELECT count(*) AS allocation_count, min(customer_id) AS allocation_customer_id,
                min(offer_id) AS offer_id, min(offer_type) AS offer_type,
                min(status) AS allocation_status, min(frequency) AS frequency,
                min(valid_from) AS valid_from, min(valid_till) AS valid_till,
                min(allocation_date) AS allocation_date, min(expiry_date) AS expiry_date,
                min(total_soc) AS total_soc, min(consumed_soc) AS allocation_consumed_soc,
                coalesce(jsonb_agg(to_jsonb(a)-'raw_record' ORDER BY a.zoho_record_id),'[]') AS allocations
            FROM reconciliation.offer_allocations a WHERE a.country=s.country
                AND nullif(btrim(c.allocation_reference),'') IS NOT NULL
                AND a.allocation_reference=c.allocation_reference
        ) a ON true
        LEFT JOIN budgets b ON b.country=s.country AND b.allocation_reference=c.allocation_reference
        WHERE ''' + ' AND '.join(clauses) + '''
    ), reviewed AS (
        SELECT *, CASE
            WHEN consumption_count=0 THEN 'no_consumption'
            WHEN consumption_count<>1 THEN 'multiple_consumptions'
            WHEN nullif(btrim(consumption_reference),'') IS NULL OR
                (SELECT count(*) FROM reconciliation.offer_consumptions c
                 WHERE c.country=r.country AND c.consumption_reference=r.consumption_reference)<>1
                THEN 'duplicate_consumption_reference'
            WHEN allocation_count=0 THEN 'no_allocation'
            WHEN allocation_count<>1 THEN 'multiple_allocations'
            WHEN swap_status IS DISTINCT FROM 'SUCCESS' THEN 'swap_not_successful'
            WHEN nullif(btrim(customer_id),'') IS NULL OR allocation_customer_id IS DISTINCT FROM customer_id
                THEN 'allocation_customer_mismatch'
            WHEN business_count<>1 OR consumption_customer_id IS DISTINCT FROM business_id
                THEN 'consumption_customer_mismatch'
            WHEN currency_count<>1 OR consumption_currency IS DISTINCT FROM currency THEN 'currency_mismatch'
            WHEN consumption_type IS DISTINCT FROM 'Battery Swap' THEN 'wrong_consumption_type'
            WHEN offer_type IS DISTINCT FROM 'SOC Voucher' OR frequency IS DISTINCT FROM 'One-Time'
                THEN 'unsupported_offer_terms'
            WHEN nullif(btrim(offer_id),'') IS NULL THEN 'missing_offer_reference'
            WHEN allocation_status IS NULL OR allocation_status NOT IN ('Active','Running','Expired')
                THEN 'allocation_status_needs_review'
            WHEN valid_from IS NULL OR valid_till IS NULL OR allocation_date IS NULL OR created_on IS NULL
                OR consumption_date IS NULL THEN 'missing_validity_dates'
            WHEN consumption_date<>created_on::date THEN 'consumption_date_mismatch'
            WHEN created_on::date<valid_from OR created_on::date>valid_till OR created_on::date<allocation_date
                THEN 'outside_validity_window'
            WHEN expiry_date IS NOT NULL AND created_on::date>expiry_date THEN 'conflicting_expiry_date'
            WHEN swap_amount IS DISTINCT FROM 0::numeric OR billable_soc IS DISTINCT FROM 0::numeric
                THEN 'uncovered_swap_charge'
            WHEN net_soc IS NULL OR net_soc<=0 OR non_billable_soc IS DISTINCT FROM net_soc
                OR consumed_soc IS DISTINCT FROM net_soc THEN 'soc_coverage_mismatch'
            WHEN allocated_soc IS NULL OR allocated_soc IS DISTINCT FROM total_soc OR consumed_soc>allocated_soc
                OR remaining_soc IS NULL OR remaining_soc<0 OR remaining_soc>allocated_soc-consumed_soc
                OR allocation_consumed_soc IS NULL OR allocation_consumed_soc<consumed_soc
                OR allocation_consumed_soc>total_soc OR valid_consumptions IS DISTINCT FROM true
                OR loaded_consumed_soc>total_soc OR loaded_consumed_soc>allocation_consumed_soc
                THEN 'soc_budget_mismatch'
            WHEN allocated_discount IS DISTINCT FROM 0::numeric OR consumed_discount IS DISTINCT FROM 0::numeric
                OR remaining_discount IS DISTINCT FROM 0::numeric THEN 'unsupported_discount_terms'
            ELSE 'covered' END AS offer_result
        FROM candidates r
    ), resolved AS (
        SELECT *, CASE WHEN wallet_debit_count>0 THEN 'wallet_attached'
            WHEN offer_result='covered' THEN 'resolved_offer' ELSE 'needs_review' END AS resolution_status
        FROM reviewed
    ) SELECT * FROM resolved r WHERE ''' + predicate
    return statement, values


def resolution_query():
    statement, _ = query(group='resolved')
    return 'SELECT record_id,country FROM (' + statement + ') covered_offers'


def summary(conn, filters=None):
    statement, values = query(filters)
    rows = conn.execute('SELECT country,resolution_status,offer_result,count(*) FROM (' + statement
                        + ') r GROUP BY 1,2,3 ORDER BY 1,2,3', values).fetchall()
    result = {'swap_count': 0, 'resolved_count': 0, 'needs_review_count': 0,
              'wallet_attached_count': 0, 'by_group': {key: 0 for key in GROUPS},
              'by_country': [], 'review_reasons': {}, 'source_coverage_status': 'not_verified'}
    countries = {}
    for country, status, reason, count in rows:
        key = {'resolved_offer': 'resolved', 'needs_review': 'needs_review', 'wallet_attached': 'wallet_attached'}[status]
        field = {'resolved': 'resolved_count', 'needs_review': 'needs_review_count', 'wallet_attached': 'wallet_attached_count'}[key]
        target = countries.setdefault(country, {'country': country, 'swap_count': 0,
            'resolved_count': 0, 'needs_review_count': 0, 'wallet_attached_count': 0})
        target['swap_count'] += count; target[field] += count
        result['swap_count'] += count; result[field] += count
        result['by_group'][key] += count; result['by_group']['all'] += count
        if status == 'needs_review':
            result['review_reasons'][reason] = result['review_reasons'].get(reason, 0) + count
    result['by_country'] = list(countries.values())
    sources = []
    for table, label in [('offer_allocations', 'Offer allocations'), ('offer_consumptions', 'Offer consumptions')]:
        for country, count, missing in conn.execute('SELECT country,count(*),count(*) FILTER (WHERE '
                + ("nullif(btrim(swap_record_id),'') IS NULL" if table == 'offer_consumptions' else "nullif(btrim(offer_id),'') IS NULL")
                + ') FROM reconciliation.' + table + (' WHERE country=%s' if filters and filters.country else '')
                + ' GROUP BY country ORDER BY country', [filters.country] if filters and filters.country else []):
            sources.append({'module': label, 'country': country, 'rows': count, 'missing_lookup_rows': missing})
    result['sources'] = sources
    return result


def page(conn, filters=None, *, group='needs_review', limit=50, cursor=None):
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError('Page size must be between 1 and 100.')
    statement, values = query(filters, group=group, cursor=cursor)
    with conn.cursor() as cur:
        cur.execute(statement + ' ORDER BY record_id LIMIT %s', [*values, limit+1])
        fields = [c.name for c in cur.description]
        records = [serialize(dict(zip(fields, row))) for row in cur.fetchall()]
    more = len(records)>limit
    records = records[:limit]
    return {'records': records, 'has_more': more, 'next_cursor': records[-1]['record_id'] if more else None}


def export(conn, filters=None, *, group='needs_review'):
    statement, values = query(filters, group=group)
    with conn.cursor() as cur:
        cur.execute(statement + ' ORDER BY record_id LIMIT %s', [*values, MAX_EXPORT+1])
        fields = [c.name for c in cur.description]
        rows = cur.fetchall()
    if len(rows)>MAX_EXPORT:
        raise ValueError('More than 10,000 records. Narrow the country or date range.')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
    for row in rows:
        writer.writerow({k: csv_value(k, json.dumps(serialize(v), ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                         for k,v in zip(fields,row)})
    return ('\ufeff'+stream.getvalue()).encode('utf-8')
