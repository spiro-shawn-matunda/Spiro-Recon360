-- Adds the Wallets module to an existing populated reconciliation database.
CREATE TABLE IF NOT EXISTS reconciliation.wallets (
    zoho_record_id text PRIMARY KEY,
    wallet_code text,
    source_record_id text,
    customer_crm_id text,
    customer_label text,
    customer_business_id text,
    customer_name text,
    country text,
    country_code text,
    currency text,
    category text,
    opening_balance numeric,
    hold_amount numeric,
    available_balance numeric,
    created_on timestamp without time zone,
    source_modified_at timestamp without time zone,
    source_change_at timestamp without time zone,
    source_file text NOT NULL,
    source_row_number bigint NOT NULL,
    raw_record jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE reconciliation.import_batches
    DROP CONSTRAINT IF EXISTS import_batches_dataset_check;
ALTER TABLE reconciliation.import_batches
    ADD CONSTRAINT import_batches_dataset_check
    CHECK (dataset IN ('wallet', 'swap', 'wallet_master', 'due', 'offer_allocation', 'offer_consumption'));

CREATE INDEX IF NOT EXISTS wallets_customer_crm_id_idx
    ON reconciliation.wallets (customer_crm_id);

COMMENT ON COLUMN reconciliation.wallets.raw_record IS
    'CSV fields preserved except Wallet Pin, which is excluded before import.';
COMMENT ON COLUMN reconciliation.wallets.available_balance IS
    'Balance from the current wallet export; not the historical balance at a deduction.';
COMMENT ON COLUMN reconciliation.wallets.customer_crm_id IS
    'Customer.id is the Zoho lookup ID. Customer ID is a separate business reference.';

COMMENT ON COLUMN reconciliation.wallet_transactions.wallet_id IS
    'Wallet.id joins to reconciliation.wallets.zoho_record_id for customer mapping.';

-- Keep the original review view available and add an enriched backend view.
CREATE OR REPLACE VIEW reconciliation.wallet_swap_customer_review AS
SELECT r.*,
       m.wallet_code,
       m.customer_crm_id AS wallet_customer_id,
       m.customer_business_id AS wallet_customer_reference,
       m.customer_name AS wallet_customer_name,
       m.currency AS wallet_currency,
       m.source_modified_at AS wallet_mapping_modified_at,
       CASE WHEN m.zoho_record_id IS NULL THEN 'wallet_not_loaded'
            WHEN m.customer_crm_id IS NULL AND m.customer_business_id IS NULL THEN 'customer_not_available'
            ELSE 'mapped' END AS customer_mapping_status,
       CASE WHEN m.customer_crm_id IS NULL THEN 'wallet_customer_lookup_unavailable'
            WHEN r.customer_ids IS NULL THEN 'swap_customer_lookup_unavailable'
            WHEN r.customer_ids <@ ARRAY[m.customer_crm_id]::text[] THEN 'agrees'
            ELSE 'differs' END AS swap_customer_lookup_check
FROM reconciliation.wallet_swap_review r
LEFT JOIN reconciliation.wallets m ON m.zoho_record_id = r.wallet_id AND m.country = r.country;
