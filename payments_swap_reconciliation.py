"""Read-only reconciliation of swap payments and PAYMENT_OPERATOR swaps."""
from decimal import Decimal
import psycopg

PAYMENT_MATCH = """
    EXISTS (SELECT 1 FROM swap_reconciliation.swap_transactions s
            WHERE s."Pay Method" = 'PAYMENT_OPERATOR'
              AND s."Transaction ID" = p."Atlas Transaction ID"
              AND NULLIF(BTRIM(p."Atlas Transaction ID"), '') IS NOT NULL)
"""
SWAP_MATCH = """
    EXISTS (SELECT 1 FROM swap_reconciliation.swap_payments p
            WHERE p."Atlas Transaction ID" = s."Transaction ID"
              AND NULLIF(BTRIM(s."Transaction ID"), '') IS NOT NULL)
"""
SWAP_SCOPE = "s.\"Pay Method\" = 'PAYMENT_OPERATOR'"
SUMMARY_SQL = f"""
    SELECT (SELECT COUNT(*) FROM swap_reconciliation.swap_payments) AS payment_total,
           (SELECT COUNT(*) FROM swap_reconciliation.swap_transactions s WHERE {SWAP_SCOPE}) AS swap_total,
           (SELECT COUNT(*) FROM swap_reconciliation.swap_payments p WHERE {PAYMENT_MATCH}) AS matched,
           (SELECT COUNT(*) FROM swap_reconciliation.swap_transactions s WHERE {SWAP_SCOPE} AND NOT {SWAP_MATCH}) AS missing_in_payments
"""
PAYMENT_MISSING_SQL = f"""
    SELECT p."Record Id" AS record_id, p."Atlas Transaction ID" AS transaction_id,
           p."Payment Transaction No" AS reference, p."Customer Name" AS customer_name,
           p."Amount" AS amount, p."Currency" AS currency, p."Payment Status" AS status,
           p."Payment Operator" AS payment_operator, p."Transaction Date" AS transaction_date,
           CASE WHEN NULLIF(BTRIM(p."Atlas Transaction ID"), '') IS NULL
                THEN 'missing_transaction_id' ELSE 'no_matching_operator_swap' END AS reason
    FROM swap_reconciliation.swap_payments p WHERE NOT {PAYMENT_MATCH}
    ORDER BY p."Record Id" NULLS LAST, p."Atlas Transaction ID" NULLS LAST,
             p."Payment Transaction No" NULLS LAST
    LIMIT %s OFFSET %s
"""
SWAP_MISSING_SQL = f"""
    SELECT s."Record Id" AS record_id, s."Transaction ID" AS transaction_id,
           s."Battery Swapping ID" AS reference, s."Customer Name" AS customer_name,
           s."Swap Amount" AS amount, s."Status" AS status, s."Pay Method" AS pay_method,
           s."Country" AS country, s."Created On" AS transaction_date,
           CASE WHEN NULLIF(BTRIM(s."Transaction ID"), '') IS NULL
                THEN 'missing_transaction_id' ELSE 'no_matching_payment' END AS reason
    FROM swap_reconciliation.swap_transactions s WHERE {SWAP_SCOPE} AND NOT {SWAP_MATCH}
    ORDER BY s."Record Id" NULLS LAST, s."Transaction ID" NULLS LAST,
             s."Battery Swapping ID" NULLS LAST
    LIMIT %s OFFSET %s
"""
PAYMENT_MISSING_COUNT_SQL = f'SELECT COUNT(*) FROM swap_reconciliation.swap_payments p WHERE NOT {PAYMENT_MATCH}'
SWAP_MISSING_COUNT_SQL = f'SELECT COUNT(*) FROM swap_reconciliation.swap_transactions s WHERE {SWAP_SCOPE} AND NOT {SWAP_MATCH}'


def get_summary(settings):
    with psycopg.connect(**settings, connect_timeout=15) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '60s'")
        payments, swaps, matched, missing_payments = conn.execute(SUMMARY_SQL).fetchone()
    return {'payment_total': payments, 'atlas_total': payments, 'swap_total': swaps,
            'matched': matched, 'missing_in_swap': payments - matched,
            'missing_in_payments': missing_payments,
            'match_percentage': round(matched / payments * 100, 2) if payments else 0}


def get_missing_transactions(settings, limit=50, offset=0, direction='payments'):
    """Payments without an operator swap, or operator swaps without a payment."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('limit must be an integer between 1 and 1000')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('offset must be a non-negative integer')
    if direction not in ('payments', 'swaps'):
        raise ValueError('direction must be payments or swaps')
    query, count = (PAYMENT_MISSING_SQL, PAYMENT_MISSING_COUNT_SQL) if direction == 'payments' else (SWAP_MISSING_SQL, SWAP_MISSING_COUNT_SQL)
    with psycopg.connect(**settings, connect_timeout=15) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '60s'")
        with conn.cursor() as cur:
            cur.execute(query, (limit, offset))
            columns = [desc[0] for desc in cur.description]
            records = [{col: value.isoformat() if hasattr(value, 'isoformat') else str(value) if isinstance(value, Decimal) else value
                        for col, value in zip(columns, row)} for row in cur.fetchall()]
            cur.execute(count)
            total = cur.fetchone()[0]
    return {'records': records, 'total': total, 'limit': limit, 'offset': offset,
            'has_more': offset + limit < total, 'direction': direction}
