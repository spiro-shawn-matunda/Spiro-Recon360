"""Read-only reconciliation of Atlas payments against wallet references."""

from decimal import Decimal

import psycopg


# A wallet reference identifies the Atlas payment; transaction_id identifies
# the wallet entry and must be present. Count payment rows, never joined rows.
ATLAS_SCOPE = "a.settled_against = 'Wallet Recharge'"

MATCH_EXISTS = """
    EXISTS (
        SELECT 1 FROM payments_reconciliation.wallet_transactions w
        WHERE w.reference = a.atlas_transaction_id
          AND w.settled_against = 'Wallet Recharge'
          AND NULLIF(BTRIM(a.atlas_transaction_id), '') IS NOT NULL
          AND NULLIF(BTRIM(w.transaction_id), '') IS NOT NULL
    )
"""

SUMMARY_SQL = f"""
    SELECT COUNT(*) AS atlas_total,
           COUNT(*) FILTER (WHERE {MATCH_EXISTS}) AS matched,
           (SELECT COUNT(*) FROM payments_reconciliation.wallet_transactions
            WHERE settled_against = 'Wallet Recharge') AS wallet_total
    FROM payments_reconciliation.atlas_transactions a
    WHERE {ATLAS_SCOPE}
"""

MISSING_SQL = f"""
    SELECT a.payment_transaction_no AS "Payment Transaction No",
           a.atlas_transaction_id AS "Atlas Transaction ID",
           a.customer_name AS "Customer Name",
           a.amount AS "Amount",
           a.currency AS "Currency",
           a.payment_status AS "Payment Status",
           a.payment_operator AS "Payment Operator",
           a.transaction_date AS "Transaction Date"
    FROM payments_reconciliation.atlas_transactions a
    WHERE {ATLAS_SCOPE} AND NOT {MATCH_EXISTS}
    ORDER BY a.transaction_date DESC NULLS LAST, a.record_id
    LIMIT %s OFFSET %s
"""


def get_summary(settings):
    """Count Wallet Recharge payments once, including those without an Atlas ID."""
    with psycopg.connect(**settings) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        atlas_total, matched, wallet_total = conn.execute(SUMMARY_SQL).fetchone()
    return {
        'atlas_total': atlas_total,
        'wallet_total': wallet_total,
        'matched': matched,
        'missing_in_wallet': atlas_total - matched,
        'match_percentage': round(matched / atlas_total * 100, 2) if atlas_total else 0,
    }


def get_missing_transactions(settings, limit=50, offset=0):
    """List Wallet Recharge payments without a qualifying Wallet Recharge entry."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('limit must be a positive integer')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('offset must be a non-negative integer')
    with psycopg.connect(**settings) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        with conn.cursor() as cur:
            cur.execute(MISSING_SQL, (limit, offset))
            columns = [desc[0] for desc in cur.description]
            records = [
                {col: val.isoformat() if hasattr(val, 'isoformat') else str(val) if isinstance(val, Decimal) else val
                 for col, val in zip(columns, row)}
                for row in cur.fetchall()
            ]
            cur.execute(f'SELECT COUNT(*) FROM payments_reconciliation.atlas_transactions a WHERE {ATLAS_SCOPE} AND NOT {MATCH_EXISTS}')
            total = cur.fetchone()[0]
    return {
        'records': records,
        'total': total,
        'limit': limit,
        'offset': offset,
        'has_more': offset + limit < total,
    }
