# TEC-188: wallet deduction and swap reconciliation

The Python backend is implemented locally in `SpiroData`, using the existing PostgreSQL data and dependencies. The Jira ticket currently has a title but no description or acceptance criteria. This implementation covers read-only matching, review reasons, dashboard queries and report exports.

## Run in PyCharm

Open **reconcile.py** and Run it. It reads database settings from the project's `.env`, queries the existing data, and writes `reports/TEC188/all/reconciliation_summary.json` and `review_candidates.csv`. It does not import the source CSVs again. Run configurations are also available for **Reconcile All Countries**, **Reconcile Kenya**, and **Reconcile Rwanda**.

For a database that has not yet received the backend view, run **setup_reconciliation.py** first. Normal `main.py` imports also install the view automatically.

```powershell
.\.venv\Scripts\python.exe reconcile.py
.\.venv\Scripts\python.exe reconcile.py --country Rwanda
.\.venv\Scripts\python.exe reconcile.py --country Kenya --start 2026-09-23 --end 2026-09-25
```

Both date endpoints are inclusive. Date filters apply to the deduction's source `Created On`. Source timestamps retain their original literal values; their timezone remains unconfirmed. A date-filtered report omits deductions with unknown creation dates. A report without date filters retains them and flags unavailable timestamps.

`--status` filters one reconciliation status. `--output` selects a report folder. The same selected output folder is refreshed on a successful run; use a new folder to retain previous reports. Summaries and candidate pages use one repeatable-read, read-only database transaction so an import during a report cannot cause inconsistent pagination.

## Matching and review rules

The backend queries `reconciliation.backend_reconciliation`, built on the existing views:

- Include committed Debit wallet transactions settled against Swap.
- Join wallet deductions to swaps using country and exact Transaction ID.
- A single successful swap with equal amounts is matched. Amounts remain exact PostgreSQL numeric values and are returned as decimal strings in Python/JSON.
- Repeated wallet references or more than one successful swap produce `ambiguous_transaction_id`. Date filters do not hide duplicates outside the selected dates.
- Distinguish missing references, no loaded swap, no successful swap, missing amounts and amount differences.
- Flag payment methods other than WALLET, unavailable/differing customer lookups, missing wallet/customer mappings, unknown currency and unavailable timestamps for review.
- Calculate observed swap date bounds separately for each country. Bounds provide context, not a source-completeness certificate.

The export includes **all records needing review**, including matched records with additional flags. Its count can therefore exceed the count of deductions without a loaded swap. The `review_reasons` CSV column is a JSON array. Amount totals are grouped by country, wallet currency and reconciliation status, and represent deductions rather than confirmed losses or refund amounts.

Customer details and currency come from the current wallet snapshot. Customer comparisons include the lookup IDs present in loaded swaps sharing the reference; differing values require investigation. The report does not infer a historical wallet balance. Source coverage remains `not_verified`.

No wallet balances, source transactions or refunds are changed. Credits/refunds are not represented by the supplied deduction filter. Business rules for delayed swaps, reversals, refund eligibility and OFFER_APPLIED still need agreement before adding any financial action.

## Use from a Python dashboard

```python
import psycopg
from db_config import read_database_config
from reconciliation_backend import ReconciliationBackend, ReconciliationFilter

with psycopg.connect(**read_database_config(), autocommit=True) as conn:
    backend = ReconciliationBackend(conn)
    selection = ReconciliationFilter(country="Rwanda", start_date="2026-09-23", end_date="2026-09-25")
    summary = backend.summary(selection)
    page = backend.list_records(selection, review_only=True, limit=100)
    # Pass next_cursor as after_record_id to get the following page.
    # backend.get_record(wallet_record_id, country="Rwanda") retrieves one case.
```

`list_records` has a maximum page size of 1,000. The cursor is the last source wallet transaction Record Id, ordered as text. Parameters are passed separately from SQL. The library returns JSON-compatible dictionaries and never returns raw source JSON or Wallet Pin. Keep database credentials on the Python backend. This is a Python module and CLI; a network API/dashboard deployment is a separate integration step.

The caller owns dashboard transaction boundaries. Use a repeatable-read transaction if multiple summary/detail calls must represent the same snapshot; otherwise live results may change between calls as imports run. A database role with SELECT on the reconciliation views is sufficient for querying; view setup requires the existing schema-owner account.

## Validation

Eight integration tests passed against an isolated PostgreSQL 17.5 database with controlled cases for every matching status, customer/payment flags, country isolation, exact decimal amounts, duplicate detection across date boundaries, cursor pagination, empty reports and excluded credits/rentals/uncommitted transactions. Tests require an explicitly supplied disposable test DSN and never read the project's `.env`.

The backend is ready for dashboard integration and business review. A ticket with no acceptance criteria cannot yet establish approval of delayed-swap or refund policies. No Jira status or comments were changed.
