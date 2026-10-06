-- TEC-188: a read-only backend view over the existing reconciliation rules.
-- Observed export bounds do not establish complete source coverage.
CREATE OR REPLACE VIEW reconciliation.backend_reconciliation AS
WITH bounds AS (
    SELECT country, min(created_on) AS first_swap, max(created_on) AS last_swap
    FROM reconciliation.swap_transactions GROUP BY country
), flagged AS (
    SELECT r.*, b.first_swap AS observed_first_swap, b.last_swap AS observed_last_swap,
           array_remove(ARRAY[
               CASE WHEN r.reconciliation_status <> 'matched' THEN r.reconciliation_status END,
               CASE WHEN r.payment_method_needs_review THEN 'payment_method_needs_review' END,
               CASE WHEN r.customer_mapping_status <> 'mapped' THEN r.customer_mapping_status END,
               CASE WHEN r.successful_swap_count > 0 AND r.swap_customer_lookup_check = 'differs'
                    THEN 'customer_lookup_differs' END,
               CASE WHEN r.successful_swap_count > 0 AND r.swap_customer_lookup_check IN
                    ('swap_customer_lookup_unavailable', 'wallet_customer_lookup_unavailable')
                    THEN 'customer_lookup_unavailable' END,
               CASE WHEN r.wallet_currency IS NULL THEN 'currency_unavailable' END,
               CASE WHEN r.country IS NULL THEN 'country_unavailable' END,
               CASE WHEN r.wallet_created_on IS NULL OR
                    (r.successful_swap_count = 1 AND r.successful_swap_created_on IS NULL)
                    THEN 'timestamp_unavailable' END
           ]::text[], NULL) AS review_reasons,
           CASE WHEN b.first_swap IS NULL THEN 'no_swap_data_loaded'
                WHEN r.wallet_created_on IS NULL THEN 'timestamp_unavailable'
                WHEN r.wallet_created_on < b.first_swap THEN 'before_observed_swap_start'
                WHEN r.wallet_created_on > b.last_swap THEN 'after_observed_swap_cutoff'
                ELSE 'within_observed_swap_span' END AS coverage_assessment
    FROM reconciliation.wallet_swap_customer_review r
    LEFT JOIN bounds b ON b.country = r.country
)
SELECT flagged.*, cardinality(review_reasons) > 0 AS needs_review,
       'not_verified'::text AS source_coverage_status
FROM flagged;

COMMENT ON VIEW reconciliation.backend_reconciliation IS
    'TEC-188 read-only reconciliation results. Review flags are investigation candidates, not refund authorization. Currency/customer mapping comes from the current wallet snapshot.';
