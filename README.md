# Spiro dashboard

Reconcile Zoho wallet deductions and swaps for Kenya and Rwanda. Upload exports, investigate transactions, and download filtered results.

## Run

In PyCharm, use the project's interpreter and run **main.py** or **Spiro - Main**. Open **http://127.0.0.1:8765/** and keep the run active. Startup prepares database tables/views/indexes and preserves existing source records. Connection settings always come from the project's root `.env`.

**Home** is the shared landing page. The left sidebar has an expandable **Swap** category containing **Wallet & Swap**, **Missing Counterparts**, **Swap to Due** and **Swap to Offer**. Home also provides shortcuts to each module. Wallet & Swap preserves the deduction, matched, review and missing-swap cards plus country cards. Uploads remain available under Add files & manage data.

**Swap to Due** reconciles swaps with Pay Method `DUE_CREATED` to Dues using exact country + `Swapping Transaction.id` → swap `Record Id`. A unique link with matching customer, amount and available currency is matched. Only an exact matching Due marked Paid clears a swap without a wallet debit. Pending, Closed, missing links, duplicate Dues and discrepancies remain open for review. Cleared swaps stay available under Cleared by Paid Dues and leave missing-counterpart review exports. Countries without any loaded Due records are shown as awaiting a Due export. Use the result selector to inspect/export review candidates, matched records, Paid, Pending, Closed or all swaps. Date filters select swaps and check Dues across all loaded dates. Due payment status comes from the export; Closed does not mean Paid. Due Amount can remain populated on Paid records and is not a verified outstanding balance. Blank swap Transaction IDs do not prevent Due matching. Original sources remain intact.

Dashboard results are stored in indexed PostgreSQL materialized views. The first run builds these results once; normal loads reuse them. Imports refresh them before reporting completion. Wallet and swap source edits invalidate the stored results, including changes made through DBeaver. Due and offer resolution evidence is checked live on every request. The next read rebuilds a dirty cache. An unchanged restart does not recalculate matching. Original tables and report rules remain the source of truth; Python report commands can still query them directly.

For a fresh clone, copy `.env.example` to `.env`, enter PostgreSQL connection details, and install `requirements.txt` in your project environment.

Choose **Add files & manage data → Validate files → Import validated files** to load Wallets, Wallet Transactions, Swapping Transactions, Dues, Offer Allocation and Offer Consumptions CSVs. Files merge by Record Id, and Wallet PINs are excluded. Completed Due imports open Swap to Due; offer imports open Swap to Offer; other imports refresh the active view. Use date batches for Zoho exports that exceed its export cap.

Swap to Due shows all loaded Due records and their exported statuses separately from the statuses of Dues linked to the selected swaps. Source counts use all Due dates for the selected country. Upload validation reports blank swap lookups, and the country cards show missing lookups and files that reached the 200,000-row cap. A country can have Pending Dues even when no Due-created swap can be matched. The overview shows swap review and Paid Due resolution counts alongside the wallet deduction cards. Adding Dues can lower swap review without changing wallet deduction counts. Paid Due decisions are checked live against the full cached gap list: a Paid → Pending edit reopens the case without rebuilding the wallet cache.

**Swap to Offer** tracks `OFFER_APPLIED` swaps. Exact country + `Swap Transaction ID.id` joins consumptions to swaps; `Offer Allocation ID` joins the allocation business reference. A unique one-time SOC voucher clears only a successful zero-charge swap with full non-billable SOC coverage, matching CRM and business customer IDs, matching wallet currency, valid dates and consistent voucher balances. All loaded consumption totals are checked against the allocation. Expiry conflicts, date differences, duplicate links, partial coverage, discounts and repeated-frequency offers remain open. Historical `Expired` status alone does not invalidate a valid earlier consumption. Offer Created terms are not loaded or inferred from offer names. Source records remain unchanged, and later edits can reopen cases immediately. The overview and open-swap CSV exports use the same resolution rules.

## Project directory

```text
main.py       Dashboard entry point
app/          Python API, imports, matching and report logic
web/          Dashboard HTML, CSS and JavaScript
sql/          Database schema, views and indexes
tests/        Unit/integration tests and synthetic fixtures
tools/        Optional batch imports, validation and setup utilities
data/         Local CSVs, ignored by Git
reports/      Generated reports, ignored by Git
.env          Local database credentials, ignored by Git
```

| Change | Start here |
|---|---|
| Dashboard appearance and interactions | `web/index.html`, `web/styles.css`, `web/app.js` |
| HTTP routes | `app/dashboard.py` |
| Upload validation/imports | `app/import_service.py`, `app/import_csv.py` |
| Reconciliation rules | `app/reconciliation_backend.py`, `sql/` |
| Missing-counterpart groups | `app/counterpart_tracking.py`, `app/missing_counterparts.py` |
| Due-created swap tracking | `app/due_tracking.py` |
| Offer coverage and evidence | `app/offer_tracking.py` |
| PostgreSQL settings | `.env` and `app/db_config.py` |
| Dashboard performance and refresh | `app/dashboard_cache.py`, `sql/dashboard_cache.sql` |

Keep dashboard code in `app/` and `web/`. Optional helpers and their old PyCharm launch configurations are in `tools/`. The sole active launch configuration is **Spiro - Main**. SQL examples are under `sql/examples/`; existing reports are under `reports/reconciliation/` or `reports/missing_counterparts/`.

## Reports

**Reconciliation overview** compares committed swap debits with swaps and shows amount/status review flags. **Missing counterparts** separates usable references with no loaded counterpart from records with missing identifiers. Match using exact country + nonblank Transaction ID. Date filters select each source record while checking counterparts across all loaded dates. Blank IDs and non-wallet methods do not establish a missing charge. Counts reflect loaded exports; source coverage still needs verification.

Download filtered CSVs from the dashboard, or use the same entry point for project exports:

```powershell
python main.py --export-unmatched
python main.py --export-review --country Rwanda
```

Report options: `--country`, `--start`, `--end`, `--output`; review reports also accept `--status`. The existing `--import-config` and `--dry-run` options remain available through `main.py` for configured local files. The server currently accepts local connections only.

## Tests

Run `python -m unittest discover -s tests -v` for unit tests; integration tests require an explicit `SPIRO_TEST_DSN` and never read `.env`. For the full suite, create a **new empty disposable PostgreSQL database**, set `SPIRO_TEST_DSN` to its connection string, and run `python tests/run.py`. The runner creates synthetic fixtures and refuses a database that already has user tables.
