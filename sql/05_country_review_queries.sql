-- Counts and amounts remain separate by country and source currency.
SELECT country, count(*) AS wallets
FROM reconciliation.wallets GROUP BY country ORDER BY country;

SELECT country, wallet_currency, reconciliation_status, count(*) AS deductions,
       sum(wallet_amount) AS amount_in_source_currency
FROM reconciliation.wallet_swap_customer_review
GROUP BY country, wallet_currency, reconciliation_status ORDER BY 1,2,3;

SELECT country, customer_mapping_status, count(*) AS deductions
FROM reconciliation.wallet_swap_customer_review GROUP BY 1,2 ORDER BY 1,2;

-- Investigation candidates within each country's observed swap time span.
WITH bounds AS (
    SELECT country, max(created_on) AS last_swap
    FROM reconciliation.swap_transactions GROUP BY country
)
SELECT r.country, r.wallet_record_id, r.transaction_id, r.wallet_reference,
       r.wallet_customer_id, r.wallet_customer_reference, r.wallet_customer_name,
       r.wallet_amount, r.wallet_currency, r.wallet_created_on
FROM reconciliation.wallet_swap_customer_review r JOIN bounds b ON b.country=r.country
WHERE r.reconciliation_status = 'no_swap_in_loaded_data'
  AND r.wallet_created_on <= b.last_swap
ORDER BY r.country, r.wallet_created_on;

-- Review source wallet currencies without substituting a country default.
SELECT country, currency, count(*) AS wallets
FROM reconciliation.wallets GROUP BY 1,2 ORDER BY 1,2;
