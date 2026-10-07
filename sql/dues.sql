-- Exact source IDs preserve the Due -> Swapping Transaction relationship.
CREATE TABLE IF NOT EXISTS reconciliation.dues (
    zoho_record_id text PRIMARY KEY,
    due_reference text,
    transaction_id text,
    country text,
    currency text,
    status text,
    customer_id text,
    customer_label text,
    customer_name text,
    vehicle_registration text,
    due_type text,
    payment_mode text,
    due_amount numeric,
    original_due_amount numeric,
    advance_applied_amount numeric,
    swap_record_id text,
    swap_reference text,
    due_source_id text,
    payment_transaction_id text,
    due_creation_date date,
    due_settlement_date date,
    created_on timestamp without time zone,
    source_modified_at timestamp without time zone,
    source_change_at timestamp without time zone,
    source_file text NOT NULL,
    source_row_number bigint NOT NULL,
    raw_record jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS due_swap_lookup_idx
    ON reconciliation.dues (country, swap_record_id) WHERE swap_record_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS dues_country_created_idx
    ON reconciliation.dues (country, created_on);
COMMENT ON COLUMN reconciliation.dues.swap_record_id IS
    'Swapping Transaction.id joins the exact swap Zoho Record Id within the same country. Settlement Transaction ID is not the swap key.';
COMMENT ON COLUMN reconciliation.dues.due_amount IS
    'Exported Due Amount. Paid records can retain this amount; it is not a verified outstanding balance.';
