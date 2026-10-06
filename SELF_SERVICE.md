# Spiro self-service dashboard

## Daily use

1. Open SpiroData in PyCharm. Run **main.py** or select **Spiro - Main**.
2. Open **http://127.0.0.1:8765/**. Keep the Python run active while using the dashboard.
3. Open **Add files & manage data** and choose one or more Zoho CSV exports.
4. Click **Validate files**. Check the detected module, countries, row counts and date range.
5. Click **Import validated files**. The dashboard displays progress and how many records were added or updated, then refreshes its data sources and reconciliation results.
6. Click **Clear** to add another group of files. Clear removes the temporary upload list, not imported database records.

No new packages are needed. Connection settings continue to come from the existing `.env`. There is no need to edit `config.json` for dashboard uploads, run setup scripts separately, or reimport the existing CSVs on every startup.

## Files and batches

Upload original Zoho exports for **Wallets**, **Wallet Transactions**, or **Swapping Transactions** for **Kenya** and **Rwanda**. The module is detected from the complete column headers. Files can include either country or both. Missing headers, invalid amounts/dates, missing Record Ids, unknown countries and empty exports are rejected before database writes.

Choose up to ten files at a time, each no larger than 512 MB. File validation completes before the Import button becomes available. Wallets/customer mappings are imported first, then wallet transactions and swaps. Wallet PINs are removed from temporary master exports and excluded from database raw JSON. Other identifiers and decimal amounts retain their source values.

Zoho's 200,000-row export cap still applies. Use date batches that each fit within the cap and keep original Record Ids. The preview warns for files with at least 200,000 rows. Coverage is not automatically confirmed from the number of uploaded files or observed timestamps.

## Repeat uploads and failures

Records merge by exact Zoho Record Id. Re-uploading identical rows produces zero changes; older source versions do not overwrite newer ones. Each file commits independently and is recorded in **Recent imports**. Files already imported in a group remain saved if a later file fails. A database failure rolls back that entire file, including its import audit entry. Correct the problem and choose the failed file again.

Imports do not create refunds or post new CRM wallet debits/credits. They update the local copies of exported source records for analysis. The results keep country/currency distinctions and show investigation candidates.

Keep the page open while uploading or importing. A page refresh clears its temporary file list; **Recent imports** remains in PostgreSQL and can be used to check completed work. Validated file sessions expire after one hour of inactivity, and uploaded files are removed after import, failure, Clear or server shutdown. Start a new validation after restarting the program.

## Other run options

```powershell
.\.venv\Scripts\python.exe main.py
# Another local port:
.\.venv\Scripts\python.exe main.py --port 8766
# Older configured batch import, without starting the dashboard:
.\.venv\Scripts\python.exe main.py --import-config
# Validate configured files, without a database connection:
.\.venv\Scripts\python.exe main.py --dry-run
```

The existing Wallets/Rwanda import and validation shortcuts continue to work. `dashboard.py` remains a compatible alternate entry point. Stop the PyCharm run before starting a second copy on the same port. PostgreSQL must already be running and the database named in `.env` must exist; startup prepares its reconciliation tables and views using the configured account.

## Local access

The server accepts only local connections on 127.0.0.1. No firewall change is required. Upload and import requests require the current dashboard session token and pass local Host/Origin checks. The server never serves `.env`, source CSVs or other project files as public assets. This version is for local use; teammate access still requires a separate authenticated shared deployment.

## Verification

22 tests passed against an isolated PostgreSQL database, covering the reconciliation backend, dashboard queries, CSV validation, PIN removal, all three module imports, repeat uploads, import history, request restrictions and rollback after a database failure. Browser testing imported three synthetic exports, confirmed an automatic count refresh and rejected an incomplete CSV. The real Spiro data was used for startup and read-only dashboard checks; synthetic test rows were never imported into Spiro.
