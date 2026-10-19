-- Recover Remarks from previously uploaded payment records; no CSV reload needed.
ALTER TABLE payments_reconciliation.atlas_transactions
    ADD COLUMN IF NOT EXISTS remarks text;
UPDATE payments_reconciliation.atlas_transactions AS a
SET remarks = (
    SELECT value FROM jsonb_each_text(a.raw_record)
    WHERE trim(both '_' from regexp_replace(lower(key), '[^a-z0-9]+', '_', 'g')) = 'remarks'
    LIMIT 1
)
WHERE a.remarks IS NULL
  AND EXISTS (
    SELECT 1 FROM jsonb_each_text(a.raw_record)
    WHERE trim(both '_' from regexp_replace(lower(key), '[^a-z0-9]+', '_', 'g')) = 'remarks'
      AND value IS NOT NULL
  );
