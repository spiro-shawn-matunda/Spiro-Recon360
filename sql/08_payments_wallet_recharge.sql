-- Upgrade existing payment uploads without requiring another export.
ALTER TABLE payments_reconciliation.atlas_transactions
    ADD COLUMN IF NOT EXISTS settled_against text;
UPDATE payments_reconciliation.atlas_transactions AS a
SET settled_against = (
    SELECT value FROM jsonb_each_text(a.raw_record)
    WHERE trim(both '_' from regexp_replace(lower(key), '[^a-z0-9]+', '_', 'g')) = 'settled_against'
    LIMIT 1
)
WHERE a.settled_against IS NULL
  AND EXISTS (
    SELECT 1 FROM jsonb_each_text(a.raw_record)
    WHERE trim(both '_' from regexp_replace(lower(key), '[^a-z0-9]+', '_', 'g')) = 'settled_against'
      AND value IS NOT NULL
  );
