-- Independent CSV storage for Due payments. Existing payment tables are untouched.
CREATE SCHEMA IF NOT EXISTS due_reconciliation;
CREATE TABLE IF NOT EXISTS due_reconciliation.due_payments (
    record_id text PRIMARY KEY,
    country text NOT NULL,
    remarks text,
    union_due_id_new text,
    atlas_transaction_id text,
    payment_transaction_no text,
    customer_name text,
    currency text,
    amount numeric,
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
CREATE TABLE IF NOT EXISTS due_reconciliation.import_batches (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset text NOT NULL CHECK (dataset = 'due_payments'),
    source_file text NOT NULL,
    sha256 text NOT NULL,
    rows_read bigint NOT NULL,
    rows_applied bigint NOT NULL,
    min_created_on timestamp without time zone,
    max_created_on timestamp without time zone,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS due_payment_scope_idx
    ON due_reconciliation.due_payments (country, remarks);
COMMENT ON SCHEMA due_reconciliation IS
    'Due payment uploads. Uganda No due: Remarks = Settlement of Rent with null, empty or whitespace-only Union Due ID (New). ID presence is checked without requiring a due-table match.';
