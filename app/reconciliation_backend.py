"""Read-only reconciliation query interface for Python dashboards.

Amounts are serialized as decimal strings; timestamps retain the source's
unconfirmed timezone. Matching is owned by the PostgreSQL views, not duplicated
in Python. No balances, ledger records, or refunds are changed by this module.
"""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

COUNTRIES = ("Kenya", "Rwanda")
STATUSES = (
    "matched", "no_swap_in_loaded_data", "no_successful_swap_in_loaded_data",
    "missing_wallet_transaction_id", "ambiguous_transaction_id",
    "missing_amount", "amount_mismatch",
)
VIEW = "reconciliation.backend_reconciliation"
DETAIL_FIELDS = """r.wallet_record_id, r.transaction_id, r.wallet_reference,
    r.country, r.wallet_id, r.wallet_code, r.wallet_amount, r.wallet_currency,
    r.wallet_created_on, r.wallet_count, r.swap_count, r.successful_swap_count,
    r.successful_swap_amount, r.successful_swap_created_on, r.swap_references,
    r.successful_payment_methods, r.wallet_customer_id,
    r.wallet_customer_reference, r.wallet_customer_name,
    r.customer_mapping_status, r.swap_customer_lookup_check,
    r.reconciliation_status, r.review_reasons, r.needs_review,
    r.coverage_assessment, r.source_coverage_status,
    r.observed_first_swap, r.observed_last_swap,
    r.wallet_mapping_modified_at, r.wallet_source_file"""


def serialize(value):
    """Produce JSON-compatible results without floating point money conversion."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value


def parse_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("Use a date, not a datetime, for date filters.")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("Dates must use YYYY-MM-DD.")


@dataclass(frozen=True)
class ReconciliationFilter:
    country: str | None = None
    start_date: date | str | None = None
    end_date: date | str | None = None
    status: str | None = None

    def __post_init__(self):
        if self.country is not None and self.country not in COUNTRIES:
            raise ValueError("Country must be Kenya or Rwanda.")
        if self.status is not None and self.status not in STATUSES:
            raise ValueError("Unknown reconciliation status.")
        object.__setattr__(self, "start_date", parse_date(self.start_date))
        object.__setattr__(self, "end_date", parse_date(self.end_date))
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Start date must not be after end date.")

    def as_dict(self):
        return serialize({"country": self.country, "start_date": self.start_date,
                          "end_date": self.end_date, "status": self.status,
                          "end_date_inclusive": True})


def _conditions(filters):
    if not isinstance(filters, ReconciliationFilter):
        raise TypeError("Use ReconciliationFilter for query filters.")
    conditions, params = [], []
    if filters.country:
        conditions.append("r.country = %s")
        params.append(filters.country)
    if filters.start_date:
        conditions.append("r.wallet_created_on >= %s")
        params.append(datetime.combine(filters.start_date, time.min))
    if filters.end_date:
        conditions.append("r.wallet_created_on <= %s")
        params.append(datetime.combine(filters.end_date, time.max))
    if filters.status:
        conditions.append("r.reconciliation_status = %s")
        params.append(filters.status)
    return conditions, params


def _where(conditions):
    return " WHERE " + " AND ".join(conditions) if conditions else ""


def _rows(conn, statement, params):
    with conn.cursor() as cur:
        cur.execute(statement, params)
        columns = [column.name for column in cur.description]
        return [serialize(dict(zip(columns, row))) for row in cur.fetchall()]


class ReconciliationBackend:
    """Queries only; the caller owns the connection and transaction lifetime."""

    def __init__(self, conn):
        self.conn = conn

    def summary(self, filters=None):
        filters = filters or ReconciliationFilter()
        conditions, params = _conditions(filters)
        groups = _rows(self.conn, f"""SELECT r.country, r.wallet_currency AS currency,
            r.reconciliation_status AS status, count(*) AS deduction_count,
            sum(r.wallet_amount) AS deduction_amount,
            count(*) FILTER (WHERE r.needs_review) AS needs_review_count,
            count(*) FILTER (WHERE r.coverage_assessment='within_observed_swap_span') AS within_observed_span_count,
            count(*) FILTER (WHERE r.coverage_assessment='after_observed_swap_cutoff') AS after_cutoff_count
            FROM {VIEW} r{_where(conditions)}
            GROUP BY r.country, r.wallet_currency, r.reconciliation_status
            ORDER BY r.country, r.wallet_currency, r.reconciliation_status""", params)
        return {"filters": filters.as_dict(), "source_coverage_status": "not_verified",
                "deduction_count": sum(group["deduction_count"] for group in groups),
                "needs_review_count": sum(group["needs_review_count"] for group in groups),
                "groups": groups}

    def list_records(self, filters=None, *, review_only=False, limit=100, after_record_id=None):
        filters = filters or ReconciliationFilter()
        conditions, params = _conditions(filters)
        if type(review_only) is not bool:
            raise ValueError("review_only must be True or False.")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Page size must be an integer between 1 and 1000.")
        if review_only:
            conditions.append("r.needs_review")
        if after_record_id is not None:
            if not isinstance(after_record_id, str) or not after_record_id.strip():
                raise ValueError("The cursor must be a nonblank source wallet Record Id.")
            conditions.append("r.wallet_record_id > %s")
            params.append(after_record_id)
        params.append(limit + 1)
        rows = _rows(self.conn, f"SELECT {DETAIL_FIELDS} FROM {VIEW} r{_where(conditions)} "
                     "ORDER BY r.wallet_record_id LIMIT %s", params)
        has_more = len(rows) > limit
        records = rows[:limit]
        return {"records": records, "has_more": has_more,
                "next_cursor": records[-1]["wallet_record_id"] if has_more else None}

    def get_record(self, wallet_record_id, *, country=None):
        if not isinstance(wallet_record_id, str) or not wallet_record_id.strip():
            raise ValueError("Provide a source wallet transaction Record Id.")
        conditions, params = _conditions(ReconciliationFilter(country=country))
        # Restrict the complete reference partition, preserving duplicate checks
        # while avoiding a whole-country window scan for one detail request.
        target = self.conn.execute('''SELECT country, transaction_id
            FROM reconciliation.wallet_transactions WHERE zoho_record_id = %s
              AND transaction_type='Debit' AND status='Committed' AND settled_against='Swap' ''',
            (wallet_record_id,)).fetchone()
        if target is None or (country is not None and target[0] != country):
            return None
        for column, value in (("country", target[0]), ("transaction_id", target[1])):
            if value is None:
                conditions.append(f"r.{column} IS NULL")
            else:
                conditions.append(f"r.{column} = %s")
                params.append(value)
        conditions.append("r.wallet_record_id = %s")
        params.append(wallet_record_id)
        rows = _rows(self.conn, f"SELECT {DETAIL_FIELDS} FROM {VIEW} r{_where(conditions)}", params)
        return rows[0] if rows else None
