-- 004_user_pokemon_moves.sql
-- Add moveset columns to user_pokemon for personal scoring

BEGIN;

ALTER TABLE user_pokemon
  ADD COLUMN IF NOT EXISTS fast_move TEXT REFERENCES pogo_move(move_id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS charged_move_1 TEXT REFERENCES pogo_move(move_id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS charged_move_2 TEXT REFERENCES pogo_move(move_id) ON DELETE SET NULL;

COMMIT;
