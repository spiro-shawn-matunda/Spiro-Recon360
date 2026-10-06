# TEC-189: reconciliation dashboard

The dashboard runs in your existing PyCharm project and reads the TEC-188 backend. It uses Python's standard library, your existing PostgreSQL driver and plain HTML/CSS/JavaScript; no new packages are required.

## Start

Open **main.py** in PyCharm and click Run, or select **Spiro - Main**. Startup prepares the reconciliation schema. Use **Add files & manage data** to upload, validate and import new CSVs. See **SELF_SERVICE.md** for the daily workflow. Open **http://127.0.0.1:8765** in your browser. Keep the Python run active while using the dashboard. Stop the run to close the server.

```powershell
.\.venv\Scripts\python.exe main.py
# Use another port if 8765 is occupied:
.\.venv\Scripts\python.exe main.py --port 8766
```

The startup process reads your project's `.env`. Restart it after changing database settings. Setup scripts are not needed for normal startup. New source CSV files are loaded from the dashboard after validation and an explicit Import action. Existing configured CSVs are not reloaded on startup.

## Features

- Country filters for Kenya, Rwanda or both, inclusive deduction date filters and reconciliation result filters.
- Counts for filtered deductions, matched successful swaps, records needing review and deductions with no loaded swap.
- Country overview cards show match rates and country totals without combining currencies.
- Review candidates are shown by default. Clear **Review candidates only** to include all filtered deductions.
- Fifty records per page, with Previous and Next controls. Pages use the backend's exact source Record Id cursor.
- **View** opens a transaction panel with customer/wallet information, exact amounts, references, swap counts/payment methods, review reasons and observed source date bounds.
- **Export candidates** downloads all review candidates matching the applied country/date/result filters, regardless of the table's review toggle or current page. Export is limited to 10,000 candidates; narrow filters for larger populations. Text fields that could become spreadsheet formulas are protected in this dashboard export.
- Empty results, invalid filters and database errors have visible messages. Database credentials and raw source JSON are never sent to the browser.

The filter form's **Apply filters** button refreshes results. Dates refer to deduction creation time and are inclusive at both ends. Source times remain literal; their timezone is unconfirmed. Currency and customer mappings come from the current Wallets export. Additional review flags can appear on otherwise matched transactions, so review counts include more than unmatched deductions.

## Local API

The server provides read-only GET routes for reconciliation and explicitly requested POST actions for source CSV imports:

- `/api/dashboard`: filtered summary and first/current page in one repeatable-read database snapshot.
- `/api/records`: another page using `cursor`.
- `/api/detail`: one transaction using `record_id` and optional `country`.
- `/api/export`: CSV of review candidates using country, date and result filters.

Common filters are `country`, `start`, `end` and `status`. Page routes accept `review_only=true|false`, `limit` from 1 to 100, and `cursor`. UI amounts are formatted from decimal strings, not converted into floating point numbers.

Pagination calls are live reads; imports between pages can change counts. Apply filters again after an import to refresh the overview. A single dashboard response and each CSV export use consistent database snapshots.

## Access and ticket scope

The server binds only to **127.0.0.1**. It does not open an inbound firewall rule or provide access to teammates on other computers. It rejects other Host/Origin values and serves only the four public UI assets, not `.env`, source data or other project files. Reconciliation queries use read-only transactions. CSV imports use atomic write transactions, with a local session token and Host/Origin checks. This local server is intended for development and review; shared hosting still needs an authenticated production deployment.

Source coverage remains unverified. Review flags are investigation candidates and do not authorize a refund. The dashboard provides no balance changes or refund actions. TEC-189 currently has a title but no description or acceptance criteria; the UI should be reviewed with the team before marking the ticket complete. Jira status and comments were not changed.

## Validation

All 22 integration tests passed against isolated fixtures: eight backend tests, six dashboard HTTP tests, and eight self-service validation/import tests. They cover country filtering and paging, details and empty results, invalid parameters, local-origin restrictions, private-file isolation, rejection of write requests and filtered CSV export. Live browser verification confirmed the populated Spiro counts, Rwanda filtering, next-page navigation, transaction details, empty date ranges, Reset, mobile layout, and a complete 1,718-row review CSV download. The full export takes about a minute on this local database.
