# Spiro dashboard

Reconcile Zoho wallet deductions and swaps for Kenya and Rwanda. Upload exports, investigate transactions, and download filtered results.

## Run

In PyCharm, use the project's interpreter and run **main.py** or **Spiro - Main**. Open **http://127.0.0.1:8765/** and keep the run active. Startup prepares database tables/views/indexes and preserves existing source records. Connection settings always come from the project's root `.env`.

For a fresh clone, copy `.env.example` to `.env`, enter PostgreSQL connection details, and install `requirements.txt` in your project environment.

Choose **Add files & manage data → Validate files → Import validated files** to load Wallets, Wallet Transactions and Swapping Transactions CSVs. Files merge by Record Id, and Wallet PINs are excluded. Completed imports refresh the active view. Use date batches for Zoho exports that exceed its export cap.

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
| PostgreSQL settings | `.env` and `app/db_config.py` |

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
