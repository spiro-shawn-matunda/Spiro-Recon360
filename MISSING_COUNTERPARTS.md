# The two missing-counterpart groups

## Current exported data — 6 October 2026

Each CSV contains 1,690 rows: Kenya 1,534 and Rwanda 156. **The wallet file has 1,690 valid Transaction IDs with no loaded swap. All 1,690 rows in the swap file have missing Transaction IDs and are unmatchable, rather than confirmed missing wallet transactions.** There are currently zero swaps with a valid Transaction ID but no matching committed swap wallet debit.

The unmatchable swaps are all SUCCESS records. Payment methods are DUE_CREATED (1,517), OFFER_APPLIED (163), or blank (10); none has WALLET as its payment method. Investigate source linkage or the relevant business payment flow before treating these as missing wallet charges.

The current data files are in **reports/missing_counterparts/**:

- **wallet_without_swap.csv**: one row per committed Debit wallet transaction settled against Swap, for which no corresponding loaded swap can be matched.
- **swap_without_wallet.csv**: one row per loaded swap for which no corresponding committed Debit wallet transaction settled against Swap can be matched.
- **summary.json**: generation time, filter settings, counts by country, matching rules and source coverage notes.

Both groups match using **country + exact Transaction ID**. A counterpart counts as present regardless of amount, date or swap status. Amount mismatches and failed swaps with a corresponding wallet deduction remain separate reconciliation review cases. Repeated source Record Ids are already handled by the importer; different wallet records sharing one missing reference remain separate rows in these files.

The reverse group includes all swap statuses and payment methods. Use **wallet_payment_expected=True** to focus on successful WALLET payments; offer or other non-wallet swaps may legitimately lack a wallet debit. Currency is derived from the customer's current wallet mapping only when unambiguous; **currency_candidates** retains all available source currencies. Customer mapping arrays avoid duplicating a swap when a customer has several wallets.

Each CSV includes source references, exact amounts, creation times, customer information, the source filename/row, a reason, and observed counterpart date bounds. Missing Transaction IDs or countries are labelled explicitly instead of being presented as a confirmed missing counterpart. Formula-like source text receives an apostrophe prefix for spreadsheet safety. No Wallet PINs, raw JSON or database credentials are included.

The exports show **absence from the loaded data**. They do not prove that a record is absent from Zoho or establish a refund decision. Source exports were capped at 200,000 rows; upload remaining date batches before concluding that a transaction is missing from the CRM. Observed start/end times are context, not a completeness check.

## Refresh after importing more data

Select **Export Missing Counterparts** in PyCharm, or run:

```powershell
.\.venv\Scripts\python.exe main.py --export-unmatched
# One country:
.\.venv\Scripts\python.exe main.py --export-unmatched --country Rwanda
# One source date range, saved in a separate folder:
.\.venv\Scripts\python.exe main.py --export-unmatched --start 2026-09-23 --end 2026-09-24 --output reports/missing_counterparts/selected_dates
```

The command reads the existing `.env` and uses one repeatable-read, read-only PostgreSQL transaction. It does not import or change database records. A date filter selects wallet rows by wallet creation time and swap rows by swap creation time; matching counterparts are still searched across every loaded date. The default output folder is replaced with the latest successfully generated reports. Normal `main.py` startup continues to start the dashboard without regenerating these files.

Five dedicated tests cover country separation, exact amounts, duplicate missing references, missing identifiers, non-wallet payment methods, counterpart status/date handling, empty exports, read-only reporting and main-command dispatch. All 27 application tests passed against an isolated PostgreSQL database.
