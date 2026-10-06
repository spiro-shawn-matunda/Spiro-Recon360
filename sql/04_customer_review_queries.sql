SELECT country, count(*) AS wallets FROM reconciliation.wallets GROUP BY 1 ORDER BY 1;

SELECT country, customer_mapping_status, count(*) AS wallet_deductions
FROM reconciliation.wallet_swap_customer_review GROUP BY 1,2 ORDER BY 1,2;

SELECT country, reconciliation_status, customer_mapping_status, count(*) AS wallet_deductions
FROM reconciliation.wallet_swap_customer_review GROUP BY 1,2,3 ORDER BY 1,2,3;

-- Customer details are available even when there is no swap in the loaded data.
-- Observed bounds do not certify completeness; these are investigation candidates.
WITH bounds AS (
    SELECT country, max(created_on) AS last_swap
    FROM reconciliation.swap_transactions GROUP BY country
)
SELECT r.country, r.wallet_record_id, r.transaction_id, r.wallet_reference, r.wallet_code,
       r.wallet_customer_id, r.wallet_customer_reference, r.wallet_customer_name,
       r.wallet_amount, r.wallet_currency, r.wallet_created_on, r.reconciliation_status
FROM reconciliation.wallet_swap_customer_review r JOIN bounds b ON b.country=r.country
WHERE r.reconciliation_status = 'no_swap_in_loaded_data'
  AND r.wallet_created_on <= b.last_swap
ORDER BY r.country, r.wallet_created_on;

SELECT country, swap_customer_lookup_check, count(*) AS matched_wallet_deductions
FROM reconciliation.wallet_swap_customer_review
WHERE reconciliation_status = 'matched' GROUP BY 1,2 ORDER BY 1,2;
