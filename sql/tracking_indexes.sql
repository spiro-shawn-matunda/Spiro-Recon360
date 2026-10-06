-- Cover country/date/identifier scans without reading the large raw CRM JSON.
-- These indexes also support the first/last observed counterpart lookups.
CREATE INDEX IF NOT EXISTS wallet_tracking_scope_idx
    ON reconciliation.wallet_transactions (country, created_on)
    INCLUDE (transaction_id, zoho_record_id)
    WHERE transaction_type='Debit' AND status='Committed' AND settled_against='Swap';
CREATE INDEX IF NOT EXISTS swap_tracking_scope_idx
    ON reconciliation.swap_transactions (country, created_on)
    INCLUDE (transaction_id, zoho_record_id);
