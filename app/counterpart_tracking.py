"""Live read-only counterpart tracking shares the report's matching rules."""
import csv
import io

from .missing_counterparts import report_query, csv_value
from .reconciliation_backend import ReconciliationFilter, serialize

GROUPS = {
    'open_swaps': ('swap_without_wallet', None),
    'wallet_without_swap': ('wallet_without_swap', 'valid'),
    'swap_without_wallet': ('swap_without_wallet', 'valid'),
    'unmatchable_swaps': ('swap_without_wallet', 'unmatchable'),
    'unmatchable_wallets': ('wallet_without_swap', 'unmatchable'),
}
MAX_EXPORT = 10000


def resolution_query():
    from .due_tracking import paid_resolution_query
    from .offer_tracking import resolution_query as offer_resolution_query
    return ("SELECT record_id,country,'paid_due'::text AS reason FROM (" + paid_resolution_query()
            + ") p UNION ALL SELECT record_id,country,'offer'::text AS reason FROM ("
            + offer_resolution_query() + ') o')


def swap_review_summary(conn, filters=None, *, cached=False):
    """Shared live resolution rules keep both counts and exports consistent."""
    filters = filters or ReconciliationFilter()
    statement, values = report_query('swap_without_wallet', filters, order=None,
                                    cached=cached, include_resolved=True)
    rows = conn.execute('WITH resolutions AS MATERIALIZED (' + resolution_query()
                        + '), gaps AS MATERIALIZED (' + statement + ''')
        SELECT g.country, count(*) AS total_count,
            count(*) FILTER (WHERE p.record_id IS NULL) AS open_count,
            count(*) FILTER (WHERE p.reason='paid_due') AS resolved_paid_due_count,
            count(*) FILTER (WHERE p.reason='offer') AS resolved_offer_count
        FROM gaps g LEFT JOIN resolutions p ON p.country=g.country AND p.record_id=g.swap_record_id
        GROUP BY g.country ORDER BY g.country''', values).fetchall()
    by_country = [{'country': country or 'Unknown', 'total_count': total,
                   'open_count': opened, 'resolved_paid_due_count': paid, 'resolved_offer_count': offered}
                  for country, total, opened, paid, offered in rows]
    return {'by_country': by_country, **{field: sum(row[field] for row in by_country)
        for field in ('total_count', 'open_count', 'resolved_paid_due_count', 'resolved_offer_count')}}


def selection(group):
    if group not in GROUPS:
        raise ValueError('Choose a known counterpart tracking group.')
    return GROUPS[group]


def summary(conn, filters=None, *, cached=False):
    filters = filters or ReconciliationFilter()
    groups = {key: {'count': 0, 'by_country': {}} for key in GROUPS}
    for group, (source, classification) in GROUPS.items():
        statement, values = report_query(source, filters, classification=classification, order=None, detail=False, cached=cached)
        rows = conn.execute('SELECT r.country, count(*) FROM (' + statement + ') r GROUP BY 1', values)
        for country, count in rows:
            target = groups[group]
            target['count'] += count
            country = country or 'Unknown'
            target['by_country'][country] = target['by_country'].get(country, 0) + count
    return {'filters': filters.as_dict(), 'groups': groups, 'source_coverage_status': 'not_verified',
            'swap_review': swap_review_summary(conn, filters, cached=cached)}


def page(conn, group, filters=None, *, limit=50, cursor=None, cached=False):
    source, classification = selection(group)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Page size must be between 1 and 100.')
    filters = filters or ReconciliationFilter()
    statement, values = report_query(source, filters, classification=classification, cursor=cursor, order=None, detail=False, cached=cached)
    ids = [row[0] for row in conn.execute('WITH missing_ids AS MATERIALIZED (' + statement + ') '
           'SELECT zoho_record_id FROM missing_ids ORDER BY zoho_record_id LIMIT %s', [*values, limit + 1])]
    if not ids:
        return {'records': [], 'has_more': False, 'next_cursor': None}
    statement, values = report_query(source, filters, classification=classification, record_ids=ids, order='id', cached=cached)
    with conn.cursor() as cur:
        cur.execute(statement, values)
        fields = [column.name for column in cur.description]
        rows = [serialize(dict(zip(fields, items))) for items in cur.fetchall()]
    more = len(rows) > limit
    records = rows[:limit]
    for row in records:
        row['record_id'] = row['wallet_record_id'] if source == 'wallet_without_swap' else row['swap_record_id']
        row['record_type'] = 'Wallet deduction' if source == 'wallet_without_swap' else 'Swap'
    return {'records': records, 'has_more': more,
            'next_cursor': records[-1]['record_id'] if more else None}


def export(conn, group, filters=None, *, cached=False):
    source, classification = selection(group)
    filters = filters or ReconciliationFilter()
    statement, values = report_query(source, filters, classification=classification, order=None, detail=False, cached=cached)
    ids = [row[0] for row in conn.execute('WITH missing_ids AS MATERIALIZED (' + statement + ') '
           'SELECT zoho_record_id FROM missing_ids LIMIT %s', [*values, MAX_EXPORT + 1])]
    if len(ids) > MAX_EXPORT:
        raise ValueError('More than 10,000 records. Narrow the country or date range, or use the project report command.')
    statement, values = report_query(source, filters, classification=classification, record_ids=ids, cached=cached)
    with conn.cursor() as cur:
        cur.execute(statement, values)
        fields = [column.name for column in cur.description]
        records = cur.fetchall()
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for items in records:
        writer.writerow({field: csv_value(field, value) for field, value in zip(fields, items)})
    return ('\ufeff' + stream.getvalue()).encode('utf-8')
