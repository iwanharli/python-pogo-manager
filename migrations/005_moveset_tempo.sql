-- 005_moveset_tempo.sql
-- Add tempo scoring columns to pogo_scoring_moveset

BEGIN;

ALTER TABLE pogo_scoring_moveset
  ADD COLUMN IF NOT EXISTS tempo_raw NUMERIC,
  ADD COLUMN IF NOT EXISTS tempo_score_0_100 NUMERIC(6,2);

COMMIT;
