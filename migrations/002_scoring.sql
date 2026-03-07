BEGIN;

-- =========================================================
-- 1) Profile: definisi rumus & normalisasi
-- =========================================================
CREATE TABLE IF NOT EXISTS pogo_scoring_profile (
  id          BIGSERIAL PRIMARY KEY,

  -- Nama profile bebas: contoh 'moves_v1'
  name        TEXT NOT NULL,

  -- Konteks scoring biar jelas (fast/charged + pve/pvp)
  context     TEXT NOT NULL,

  -- Versi rumus
  version     TEXT NOT NULL DEFAULT 'v1',

  -- Parameter rumus (bobot dsb) & normalizer untuk skala 0..100
  -- contoh weights: {"w_dps":0.65,"w_eps":0.35}
  -- contoh normalizer: {"min_raw":0.123,"max_raw":4.567}
  weights     JSONB NOT NULL DEFAULT '{}'::jsonb,
  normalizer  JSONB NOT NULL DEFAULT '{}'::jsonb,

  notes       TEXT NULL,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,

  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT ck_pogo_scoring_context CHECK (
    context IN ('pve_fast','pve_charged','pvp_fast','pvp_charged')
  ),
  CONSTRAINT uq_pogo_scoring_profile UNIQUE (name, context, version)
);

CREATE INDEX IF NOT EXISTS ix_pogo_scoring_profile_active
  ON pogo_scoring_profile(is_active, context);

-- =========================================================
-- 2) Score hasil per move per profile
-- =========================================================
CREATE TABLE IF NOT EXISTS pogo_scoring_move (
  profile_id  BIGINT NOT NULL REFERENCES pogo_scoring_profile(id) ON DELETE CASCADE,
  move_id     TEXT   NOT NULL REFERENCES pogo_move(move_id) ON DELETE CASCADE,

  raw_score   NUMERIC(12,6) NOT NULL,   -- nilai mentah sebelum normalisasi
  score_0_100 NUMERIC(5,2)  NOT NULL,   -- skor final 0..100

  -- simpan breakdown biar UI enak (dps/eps/dpe/dpt/ept/cost/buff_bonus, dll)
  metrics     JSONB NOT NULL DEFAULT '{}'::jsonb,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (profile_id, move_id),
  CONSTRAINT ck_pogo_scoring_move_range CHECK (score_0_100 >= 0 AND score_0_100 <= 100)
);

CREATE INDEX IF NOT EXISTS ix_pogo_scoring_move_move
  ON pogo_scoring_move(move_id);

CREATE INDEX IF NOT EXISTS ix_pogo_scoring_move_profile
  ON pogo_scoring_move(profile_id);

CREATE INDEX IF NOT EXISTS ix_pogo_scoring_move_sort
  ON pogo_scoring_move(profile_id, score_0_100 DESC);

COMMIT;
