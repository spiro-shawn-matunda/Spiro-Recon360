-- Run this file in a SQL editor connected to the existing Spiro database.
-- Source timestamps have no offset: retain their literal values until the
-- Zoho export timezone is confirmed. Do not assume UTC or Africa/Nairobi.
CREATE SCHEMA IF NOT EXISTS reconciliation;

CREATE TABLE IF NOT EXISTS reconciliation.wallet_transactions (
    zoho_record_id text PRIMARY KEY,
    transaction_id text,
    reference text,
    source_record_id text,
    wallet_id text,
    wallet_label text,
    country text,
    transaction_type text,
    status text,
    settled_against text,
    narration text,
    amount numeric,
    opening_balance numeric,
    available_balance numeric,
    hold_amount numeric,
    created_on timestamp without time zone,
    crm_created_at timestamp without time zone,
    source_modified_at timestamp without time zone,
    source_change_at timestamp without time zone,
    source_file text NOT NULL,
    source_row_number bigint NOT NULL,
    raw_record jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reconciliation.swap_transactions (
    zoho_record_id text PRIMARY KEY,
    swap_reference text,
    transaction_id text,
    source_record_id text,
    customer_id text,
    customer_label text,
    customer_name text,
    vehicle_id text,
    vehicle_registration text,
    station_id text,
    station_label text,
    station_code text,
    in_battery_oem text,
    out_battery_oem text,
    in_battery_serial text,
    out_battery_serial text,
    country text,
    city text,
    payment_method text,
    status text,
    swap_amount numeric,
    swap_date date,
    created_on timestamp without time zone,
    source_modified_at timestamp without time zone,
    source_change_at timestamp without time zone,
    source_file text NOT NULL,
    source_row_number bigint NOT NULL,
    raw_record jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reconciliation.import_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset text NOT NULL CHECK (dataset IN ('wallet', 'swap')),
    source_file text NOT NULL,
    sha256 text NOT NULL,
    rows_read bigint NOT NULL,
    rows_applied bigint NOT NULL,
    min_created_on timestamp without time zone,
    max_created_on timestamp without time zone,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wallet_transaction_id_idx
    ON reconciliation.wallet_transactions (transaction_id);
CREATE INDEX IF NOT EXISTS wallet_created_on_idx
    ON reconciliation.wallet_transactions (created_on);
CREATE INDEX IF NOT EXISTS swap_transaction_id_idx
    ON reconciliation.swap_transactions (transaction_id);
CREATE INDEX IF NOT EXISTS swap_created_on_idx
    ON reconciliation.swap_transactions (created_on);

COMMENT ON COLUMN reconciliation.wallet_transactions.zoho_record_id IS
    'Exact CSV Record Id, including the zcrm_ prefix. API ID normalization is a separate step.';
COMMENT ON COLUMN reconciliation.wallet_transactions.wallet_id IS
    'Wallet.id is a wallet lookup, not a customer ID. Join reconciliation.wallets by Zoho Record Id and country for customer mapping.';
COMMENT ON COLUMN reconciliation.wallet_transactions.raw_record IS
    'All original CSV fields preserved as strings, including blank values.';

-- One output row per committed swap-related wallet debit. No automatic refunds.
-- Absence in these tables is not proof of absence in Zoho.
CREATE OR REPLACE VIEW reconciliation.wallet_swap_review AS
WITH swap_groups AS (
    SELECT country, transaction_id,
           count(*) AS swap_count,
           count(*) FILTER (WHERE status = 'SUCCESS') AS successful_swap_count,
           min(swap_amount) FILTER (WHERE status = 'SUCCESS') AS successful_swap_amount,
           min(created_on) FILTER (WHERE status = 'SUCCESS') AS successful_swap_created_on,
           array_agg(DISTINCT swap_reference) AS swap_references,
           array_agg(DISTINCT customer_id) FILTER (WHERE customer_id IS NOT NULL) AS customer_ids,
           array_agg(DISTINCT payment_method) FILTER (WHERE status = 'SUCCESS') AS successful_payment_methods
    FROM reconciliation.swap_transactions
    WHERE transaction_id IS NOT NULL
    GROUP BY country, transaction_id
), wallet_debits AS (
    SELECT w.*, count(*) OVER (PARTITION BY country, transaction_id) AS wallet_count
    FROM reconciliation.wallet_transactions w
    WHERE transaction_type = 'Debit' AND status = 'Committed' AND settled_against = 'Swap'
)
SELECT w.zoho_record_id AS wallet_record_id, w.transaction_id, w.reference AS wallet_reference,
       w.wallet_id, w.wallet_label, w.country, w.amount AS wallet_amount,
       w.created_on AS wallet_created_on,
       w.wallet_count, coalesce(s.swap_count, 0) AS swap_count,
       coalesce(s.successful_swap_count, 0) AS successful_swap_count,
       s.successful_swap_amount, s.successful_swap_created_on,
       s.swap_references, s.customer_ids, s.successful_payment_methods,
       CASE
           WHEN w.transaction_id IS NULL THEN 'missing_wallet_transaction_id'
           WHEN w.wallet_count > 1 OR s.successful_swap_count > 1 THEN 'ambiguous_transaction_id'
           WHEN s.transaction_id IS NULL THEN 'no_swap_in_loaded_data'
           WHEN s.successful_swap_count = 0 THEN 'no_successful_swap_in_loaded_data'
           WHEN w.amount IS NULL OR s.successful_swap_amount IS NULL THEN 'missing_amount'
           WHEN w.amount <> s.successful_swap_amount THEN 'amount_mismatch'
           ELSE 'matched'
       END AS reconciliation_status,
       CASE WHEN s.successful_swap_count = 1
            THEN s.successful_payment_methods IS DISTINCT FROM ARRAY['WALLET']::text[]
            ELSE false END AS payment_method_needs_review,
       w.source_file AS wallet_source_file, w.imported_at AS wallet_imported_at
FROM wallet_debits w
LEFT JOIN swap_groups s ON s.country = w.country AND s.transaction_id = w.transaction_id;
