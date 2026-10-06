# SpiroData in PyCharm

For the two missing-counterpart groups, see **MISSING_COUNTERPARTS.md** and **reports/missing_counterparts/**. Run `main.py --export-unmatched` to regenerate both CSVs from the latest loaded data.

This project imports Kenya and Rwanda wallet deductions, swap transactions, and Wallets/customer mappings into the existing PostgreSQL database `Spiro`. Run `main.py` to prepare the database and start the dashboard. Add new Zoho CSV exports under **Add files & manage data**. Connection settings come from the project's `.env`; the older CLI batch importer still uses `config.json`. See **SELF_SERVICE.md** for the daily workflow.

## Kenya wallet mapping

You reported that `main.py` already loaded the two transaction exports. Open **load_wallets.py** in PyCharm and Run it to add the Wallets module without reimporting the transactions. Alternatively select **Load Wallet Customers** in the run configurations.

The new `data/Wallet_2026_10_05.csv` contains 39,437 Kenya wallets. The project copy excludes the Wallet Pin column, and the importer also strips that field before saving raw JSON. The original attachment is unchanged.

Refresh **Schemas > reconciliation** in DBeaver. The new table is `reconciliation.wallets`; the new customer-enriched view is `reconciliation.wallet_swap_customer_review`. The original transaction tables and review view remain available. Use `sql/04_customer_review_queries.sql` to review customer mappings and investigation candidates.

## Run all imports

1. Open this folder as the PyCharm project.
2. Select `.venv/Scripts/python.exe` under Settings > Python Interpreter if necessary.
3. Set the PostgreSQL details in `.env`, using the same account and exact database name as DBeaver.
4. Run `validate_data.py` to validate the configured CSVs without accessing PostgreSQL.
5. Run `main.py --import-config` to create/update the reconciliation schema and import all configured files. The database account needs permission to create or alter the reconciliation tables and views.

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=Spiro
DB_USER=admin
DB_PASSWORD='your_actual_password'
```

The application reads `.env` beside `main.py` every time, even when launched from another working directory. It does not prompt for database credentials. Single quotes preserve spaces and `#`; escape an apostrophe within a single-quoted password as `\'`. `${...}` remains literal. `.env` is excluded from Git; `.env.example` is the shareable blank template. Missing settings stop the script with an error naming the fields.

The PostgreSQL driver and python-dotenv are installed in the existing virtual environment. From the PyCharm terminal:

```powershell
.\.venv\Scripts\python.exe validate_data.py
.\.venv\Scripts\python.exe load_wallets.py
# Or import all configured files:
.\.venv\Scripts\python.exe main.py --import-config
```

`config.json` lists `wallet_master_files`, `wallet_files`, and `swap_files`. Add subsequent exports to these lists. Each file commits independently. Imports deduplicate by exact Zoho Record Id, and older source versions do not replace newer ones. Reimporting identical data leaves existing records unchanged. Raw JSON retains the exported fields except Wallet Pin. A future Zoho API adapter needs explicit field mappings and consistent ID normalization.

## Results for the supplied Kenya exports

- Wallets: 39,437 records, all Kenya and currency KES.
- Wallet deductions: 200,000 records; swaps: 200,000 records.
- Every supplied wallet deduction has a matching wallet master record with customer lookup, business reference, and name.
- 198,466 transaction IDs match a successful swap with the same amount.
- 1,534 deductions have no swap in the supplied data. Of these, 1,494 occur after the latest observed swap timestamp; 40 fall within the observed time span. All 40 now have customer details.
- Of the matched pairs, 198,458 have agreeing customer lookup IDs; eight lack a swap-side customer lookup. None have differing nonblank customer lookup IDs.

Wallet deductions join `Wallet.id` to the Wallets module's `Record Id`. `Wallet ID` is a display/business code and has a duplicate in this export, so it is not used as the primary key. `Customer.id` is the Zoho lookup ID; `Customer ID` is a separate business reference.

The new view adds `wallet_customer_id`, `wallet_customer_reference`, `wallet_customer_name`, `wallet_currency`, `customer_mapping_status`, and `swap_customer_lookup_check`. Customer mappings and currency come from the current Wallets snapshot; balances do not establish a customer's balance at a historical deduction.

## Work still needed for TEC-187

The transaction exports were downloaded on 5 October but cover 23-28 September. Both reached the 200,000-row cap. Export complete date batches and compare source counts before treating missing swaps as confirmed failures. Include relevant swap statuses and allow for delayed swap creation at batch boundaries. The 40 candidates are investigation records, not confirmed refund liability.

Nine matched swaps use OFFER_APPLIED and need a business-rule check. Credits/refunds are outside the supplied wallet filter. Timestamps have no timezone offset and remain literal until the export timezone is confirmed. Complete coverage and acceptance checks are still needed before closing the ticket.

The supplied files and schema migration were validated against a separate PostgreSQL 17.5 test instance, including customer joins, PIN exclusion, and repeated imports.

## Rwanda exports added on 5 October 2026

The three Rwanda exports are stored with `Rwanda_` prefixes in `data/` and are included in `config.json`. Run **load_rwanda.py** or select **Load Rwanda CSV** to load only these files. Run **main.py --import-config** to import all configured Kenya and Rwanda exports; unchanged source rows are not duplicated.

The reconciliation joins successful swaps by both **country and Transaction ID**, and wallet/customer mappings by **country and wallet Record Id**. Query `sql/05_country_review_queries.sql` for country and currency summaries. Transaction amounts are not combined across currencies. Investigation cutoffs use each country's observed swap end date.

Rwanda source results:

- 53,356 wallet master records; 200,000 deductions; 200,000 swaps.
- All 200,000 deductions link to wallet/customer records.
- 199,844 matched successful swaps with equal amounts; 156 deductions have no swap in the supplied data.
- 90 unmatched deductions are within Rwanda's observed swap time span; 66 are after its latest swap timestamp. These are investigation candidates rather than confirmed failed swaps.
- Deductions cover 23 September 2026 03:00:02 through 25 September 18:59:07. Swaps cover 23 September 03:00:04 through 25 September 18:57:32.
- Wallet currencies are 53,355 RWF and one USD. Preserve the USD value as supplied and review that source wallet. Wallet Pin is excluded from the project copy and imported raw JSON.

Both Rwanda transaction exports contain exactly 200,000 rows; observed date bounds alone do not establish full source coverage. Rwanda Record Ids do not overlap the supplied Kenya Record Ids. The Kenya results remain 198,466 matches and 1,534 deductions without a loaded swap.

## TEC-188 backend

Run **reconcile.py** to generate country-specific summaries and review candidate reports from the loaded database. See **TEC188_README.md** for the Python dashboard interface, date filters, review rules and validation. This reads the project's `.env` and uses existing dependencies.

## TEC-189 dashboard

Run **main.py** in PyCharm (or select **Spiro - Main**) and open **http://127.0.0.1:8765**. Use **Add files & manage data** to validate and import new CSV exports without editing config.json. The local dashboard provides country/date filters, live counts, review records, transaction details and candidate CSV export. See **TEC189_README.md**. No new dependencies are required.
