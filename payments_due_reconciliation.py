"""Uganda Due payment classification, dashboard records and CSV export."""
import csv
import io
from contextlib import contextmanager
from decimal import Decimal
import psycopg
from psycopg.rows import dict_row

RENT_REMARKS = 'Settlement of Rent'
UGANDA_SCOPE_SQL = "country = 'Uganda' AND BTRIM(remarks) = 'Settlement of Rent'"
MISSING_DUE_ID_SQL = "COALESCE(union_due_id_new, '') ~ '^[[:space:]]*$'"
NO_DUE_SQL = f"({UGANDA_SCOPE_SQL}) AND ({MISSING_DUE_ID_SQL})"


def classify_payment(country, remarks, union_due_id_new):
    """ID presence only; with_due_id does not prove a matching due exists."""
    if country != 'Uganda' or (remarks or '').strip(' ') != RENT_REMARKS:
        return 'out_of_scope'
    return 'no_due' if not (union_due_id_new or '').strip() else 'with_due_id'


SUMMARY_SQL = f"""SELECT COUNT(*) AS payment_total,
    COUNT(*) FILTER (WHERE NOT ({MISSING_DUE_ID_SQL})) AS with_due_id,
    COUNT(*) FILTER (WHERE {MISSING_DUE_ID_SQL}) AS no_due
    FROM due_reconciliation.due_payments WHERE {UGANDA_SCOPE_SQL}"""
DETAIL_SQL = f"""SELECT record_id, atlas_transaction_id, payment_transaction_no,
    customer_name, amount, currency, payment_status, union_due_id_new,
    payment_operator, transaction_date, country, remarks
    FROM due_reconciliation.due_payments WHERE {NO_DUE_SQL}
    ORDER BY record_id"""
EXPORT_FIELDS = ['Record Id','Atlas Transaction ID','Payment Transaction No',
    'Customer Name','Amount','Currency','Payment Status','Union Due ID (New)',
    'Payment Operator','Transaction Date','Country','Remarks']


def validate_options(country='', page=0):
    if country not in ('', 'Uganda', 'Kenya', 'Rwanda'):
        raise ValueError('Country must be Uganda, Kenya or Rwanda.')
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise ValueError('Page must be a non-negative integer.')


@contextmanager
def read_snapshot(settings):
    with psycopg.connect(**settings, connect_timeout=15) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '60s'")
        yield conn


def get_dashboard(settings, country='', page=0):
    validate_options(country,page)
    limit=50
    configured=country in ('','Uganda')
    with read_snapshot(settings) as conn:
        loaded=conn.execute('SELECT COUNT(*) FROM due_reconciliation.due_payments').fetchone()[0]
        summary={'payment_total':None,'with_due_id':None,'no_due':None,'dues_total':None}
        records=[]
        if configured:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SUMMARY_SQL)
                summary.update(cur.fetchone())
                # Count payments carrying a Due ID within the Uganda rent scope.
                summary['dues_total'] = summary['with_due_id']
                cur.execute(DETAIL_SQL + ' LIMIT %s OFFSET %s', (limit,page*limit))
                records=[{key: value.isoformat() if hasattr(value,'isoformat') else str(value) if isinstance(value,Decimal) else value
                          for key,value in row.items()} for row in cur.fetchall()]
    total=summary['no_due'] or 0
    return {'summary':summary,'records':records,'total':total,'loaded_total':loaded,
            'country':country,'rule_configured':configured,'limit':limit,'offset':page*limit,
            'has_more':page*limit+limit<total}


def csv_cell(value):
    if value is None:
        return ''
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    value=value.isoformat() if hasattr(value,'isoformat') else str(value)
    if value.lstrip(' ').startswith(('=','+','-','@','\t','\r','\n')):
        return "'"+value
    return value


def export_missing(settings, country=''):
    validate_options(country)
    if country not in ('','Uganda'):
        raise ValueError('The No due rule is currently configured for Uganda only.')
    stream=io.StringIO(newline='')
    writer=csv.writer(stream)
    writer.writerow(EXPORT_FIELDS)
    with read_snapshot(settings) as conn:
        # Export all cases, not just the currently displayed page.
        with conn.cursor(name='due_missing_export') as cur:
            cur.execute(DETAIL_SQL)
            for row in cur:
                writer.writerow([csv_cell(value) for value in row])
    return ('\ufeff'+stream.getvalue()).encode('utf-8')
