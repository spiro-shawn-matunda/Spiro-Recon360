-- Separate storage for Payments vs Wallet uploads. Existing schemas are untouched.
CREATE SCHEMA IF NOT EXISTS payments_reconciliation;
CREATE TABLE IF NOT EXISTS payments_reconciliation.atlas_transactions (
    record_id text PRIMARY KEY,
    atlas_transaction_id text,
    payment_transaction_no text,
    customer_name text,
    country text,
    currency text,
    amount numeric,
    settled_against text,
    payment_status text,
    payment_operator text,
    transaction_date timestamp without time zone,
    created_on timestamp without time zone,
    source_modified_at timestamp without time zone,
    source_file text,
    source_row_number bigint,
    raw_record jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS payments_reconciliation.wallet_transactions (
    record_id text PRIMARY KEY,
    reference text,
    transaction_id text,
    country text,
    wallet_id text,
    type text,
    status text,
    settled_against text,
    amount numeric,
    created_on timestamp without time zone,
    source_modified_at timestamp without time zone,
    source_file text,
    source_row_number bigint,
    raw_record jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS payments_reconciliation.import_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset text NOT NULL CHECK (dataset IN ('atlas', 'wallet')),
    source_file text NOT NULL,
    sha256 text NOT NULL,
    rows_read bigint NOT NULL,
    rows_applied bigint NOT NULL,
    min_created_on timestamp without time zone,
    max_created_on timestamp without time zone,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS atlas_transaction_id_idx
    ON payments_reconciliation.atlas_transactions (atlas_transaction_id);
CREATE INDEX IF NOT EXISTS wallet_reference_idx
    ON payments_reconciliation.wallet_transactions (reference);
CREATE INDEX IF NOT EXISTS wallet_transaction_id_idx
    ON payments_reconciliation.wallet_transactions (transaction_id);
COMMENT ON SCHEMA payments_reconciliation IS
    'Independent CSV imports for Payments vs Wallet. Only Wallet Recharge records are compared. Matching requires wallet.reference = atlas.atlas_transaction_id and a populated wallet.transaction_id.';
