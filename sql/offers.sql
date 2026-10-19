-- Source exports are retained independently. Missing links never block ingestion.
CREATE TABLE IF NOT EXISTS reconciliation.offer_allocations (
    zoho_record_id text PRIMARY KEY, allocation_reference text, country text,
    customer_id text, offer_id text, offer_reference text, offer_type text,
    status text, frequency text, allocation_date date, expiry_date date,
    valid_from date, valid_till date, total_soc numeric, consumed_soc numeric,
    total_discount numeric, consumed_discount numeric, total_price numeric,
    consumed_price numeric, created_on timestamp, source_modified_at timestamp,
    source_change_at timestamp, source_file text NOT NULL,
    source_row_number bigint NOT NULL, raw_record jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS reconciliation.offer_consumptions (
    zoho_record_id text PRIMARY KEY, consumption_reference text, country text,
    currency text, customer_business_id text, allocation_reference text,
    offer_name text, transaction_type text, consumption_date date,
    swap_record_id text, swap_reference text, allocated_soc numeric,
    remaining_soc numeric, consumed_soc numeric, allocated_discount numeric,
    remaining_discount numeric, consumed_discount numeric,
    created_on timestamp, source_modified_at timestamp, source_change_at timestamp,
    source_file text NOT NULL, source_row_number bigint NOT NULL,
    raw_record jsonb NOT NULL, imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS offer_allocation_reference_idx
    ON reconciliation.offer_allocations(country, allocation_reference);
CREATE INDEX IF NOT EXISTS offer_consumption_swap_idx
    ON reconciliation.offer_consumptions(country, swap_record_id);
CREATE INDEX IF NOT EXISTS offer_consumption_allocation_idx
    ON reconciliation.offer_consumptions(country, allocation_reference);
CREATE INDEX IF NOT EXISTS offer_consumption_reference_idx
    ON reconciliation.offer_consumptions(country, consumption_reference);
CREATE INDEX IF NOT EXISTS offer_swap_scope_idx
    ON reconciliation.swap_transactions(country, created_on, zoho_record_id)
    WHERE payment_method='OFFER_APPLIED';
COMMENT ON COLUMN reconciliation.offer_consumptions.allocation_reference IS
    'Exact Offer Allocation ID business reference, not a Zoho Record Id.';
COMMENT ON COLUMN reconciliation.offer_consumptions.swap_record_id IS
    'Exact Swap Transaction ID.id lookup within the same country.';
