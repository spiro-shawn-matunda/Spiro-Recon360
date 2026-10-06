-- Derived dashboard results only. Source records and matching rules stay intact.
CREATE TABLE IF NOT EXISTS reconciliation.dashboard_cache_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    source_revision bigint NOT NULL DEFAULT 0,
    cached_revision bigint,
    refreshed_at timestamptz
);
INSERT INTO reconciliation.dashboard_cache_state (singleton)
VALUES (true) ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION reconciliation.mark_dashboard_changed()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE reconciliation.dashboard_cache_state
    SET source_revision = source_revision + 1 WHERE singleton;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER dashboard_source_changed
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reconciliation.wallets
FOR EACH STATEMENT EXECUTE FUNCTION reconciliation.mark_dashboard_changed();
CREATE OR REPLACE TRIGGER dashboard_source_changed
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reconciliation.wallet_transactions
FOR EACH STATEMENT EXECUTE FUNCTION reconciliation.mark_dashboard_changed();
CREATE OR REPLACE TRIGGER dashboard_source_changed
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reconciliation.swap_transactions
FOR EACH STATEMENT EXECUTE FUNCTION reconciliation.mark_dashboard_changed();

CREATE INDEX IF NOT EXISTS wallet_counterpart_key_idx
ON reconciliation.wallet_transactions (country, transaction_id)
WHERE transaction_type='Debit' AND status='Committed' AND settled_against='Swap';
CREATE INDEX IF NOT EXISTS swap_counterpart_key_idx
ON reconciliation.swap_transactions (country, transaction_id);
CREATE INDEX IF NOT EXISTS wallets_source_scope_idx
ON reconciliation.wallets (country, created_on);
CREATE INDEX IF NOT EXISTS wallet_source_scope_idx
ON reconciliation.wallet_transactions (country, created_on);

CREATE MATERIALIZED VIEW IF NOT EXISTS reconciliation.dashboard_reconciliation AS
SELECT * FROM reconciliation.backend_reconciliation WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS dashboard_wallet_record_idx
ON reconciliation.dashboard_reconciliation (wallet_record_id);
CREATE INDEX IF NOT EXISTS dashboard_review_record_idx
ON reconciliation.dashboard_reconciliation (wallet_record_id) WHERE needs_review;
CREATE INDEX IF NOT EXISTS dashboard_country_date_idx
ON reconciliation.dashboard_reconciliation (country, wallet_created_on);
CREATE INDEX IF NOT EXISTS dashboard_status_idx
ON reconciliation.dashboard_reconciliation (reconciliation_status);
