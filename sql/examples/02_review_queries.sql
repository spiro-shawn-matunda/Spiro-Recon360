-- Diagnostic results for the loaded data; keep countries separate.
SELECT country, 'wallet' AS dataset, count(*) AS records
FROM reconciliation.wallet_transactions GROUP BY country
UNION ALL
SELECT country, 'swap', count(*) FROM reconciliation.swap_transactions GROUP BY country;

SELECT country, reconciliation_status, count(*) AS wallet_records
FROM reconciliation.wallet_swap_review GROUP BY 1,2 ORDER BY 1,2;

-- Observed time bounds do not certify export completeness.
WITH bounds AS (
    SELECT country, min(created_on) AS first_swap, max(created_on) AS last_swap
    FROM reconciliation.swap_transactions GROUP BY country
)
SELECT r.country,
       CASE WHEN r.wallet_created_on > b.last_swap THEN 'after_swap_export_max_created_on'
            WHEN r.wallet_created_on < b.first_swap THEN 'before_swap_export_min_created_on'
            WHEN b.first_swap IS NULL THEN 'no_swap_data_loaded'
            ELSE 'within_observed_swap_time_span_not_proven_complete' END AS coverage_note,
       count(*) AS wallet_records
FROM reconciliation.wallet_swap_review r LEFT JOIN bounds b ON b.country=r.country
WHERE r.reconciliation_status = 'no_swap_in_loaded_data'
GROUP BY 1,2 ORDER BY 1,2;

-- Example: Kenya candidates on 24 September 2026.
SELECT country, wallet_record_id, transaction_id, wallet_reference, wallet_id,
       wallet_amount, wallet_created_on, reconciliation_status
FROM reconciliation.wallet_swap_review
WHERE country='Kenya' AND reconciliation_status = 'no_swap_in_loaded_data'
  AND wallet_created_on >= timestamp '2026-09-24 00:00:00'
  AND wallet_created_on < timestamp '2026-09-25 00:00:00'
ORDER BY wallet_created_on;

SELECT * FROM reconciliation.wallet_swap_review
WHERE payment_method_needs_review ORDER BY country, wallet_created_on;

SELECT country, payment_method, count(*) AS missing_transaction_id
FROM reconciliation.swap_transactions
WHERE transaction_id IS NULL GROUP BY 1,2 ORDER BY 1,2;

-- Business reference duplicates are evaluated within each country.
SELECT country, transaction_id, count(*) FROM reconciliation.wallet_transactions
WHERE transaction_id IS NOT NULL GROUP BY 1,2 HAVING count(*) > 1;
SELECT country, transaction_id, count(*) FROM reconciliation.swap_transactions
WHERE transaction_id IS NOT NULL GROUP BY 1,2 HAVING count(*) > 1;

SELECT dataset, source_file, rows_read, rows_applied, min_created_on,
       max_created_on, imported_at FROM reconciliation.import_batches
ORDER BY batch_id DESC;
