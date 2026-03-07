-- 003_pokemon_scoring.sql

-- Unified Pokémon Scoring Table (PvE & PvP) + Profiles & Movesets Recommendation

BEGIN;

-- =========================================================
-- 1. Profile for Pokemon Scoring
CREATE TABLE IF NOT EXISTS pogo_scoring_pokemon_profile (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    context TEXT NOT NULL, -- e.g., 'pve_raid', 'pve_gym_def', 'pvp_great', 'pvp_ultra', 'pvp_master'
    version TEXT NOT NULL DEFAULT 'v1',
    weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalizer JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_pogo_score_pkmn_prof UNIQUE (name, context, version)
);

CREATE INDEX IF NOT EXISTS ix_pogo_score_pkmn_prof_active ON pogo_scoring_pokemon_profile(is_active, context);

-- =========================================================
-- 2. Pokemon Scoring (Profile-based)
-- Replacing the old pogo_scoring_pokemon table
DROP TABLE IF EXISTS pogo_scoring_pokemon CASCADE;

CREATE TABLE IF NOT EXISTS pogo_scoring_pokemon (
    profile_id BIGINT NOT NULL REFERENCES pogo_scoring_pokemon_profile(id) ON DELETE CASCADE,
    pokemon_id BIGINT NOT NULL REFERENCES pogo_pokemon(id) ON DELETE CASCADE,
    raw_score NUMERIC(15, 6) NOT NULL,
    score_0_100 NUMERIC(5, 2) NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    PRIMARY KEY (profile_id, pokemon_id)
);

CREATE INDEX IF NOT EXISTS idx_pogo_score_pkmn_idx ON pogo_scoring_pokemon(profile_id, score_0_100 DESC);
CREATE INDEX IF NOT EXISTS idx_pogo_score_pkmn_pkm ON pogo_scoring_pokemon(pokemon_id);

-- =========================================================
-- 3. Moveset Recommendation
CREATE TABLE IF NOT EXISTS pogo_scoring_moveset (
    profile_id BIGINT NOT NULL REFERENCES pogo_scoring_pokemon_profile(id) ON DELETE CASCADE,
    pokemon_id BIGINT NOT NULL REFERENCES pogo_pokemon(id) ON DELETE CASCADE,
    fast_move_id TEXT NOT NULL REFERENCES pogo_move(move_id) ON DELETE RESTRICT,
    charged_move_id_1 TEXT NOT NULL REFERENCES pogo_move(move_id) ON DELETE RESTRICT,
    charged_move_id_2 TEXT NULL REFERENCES pogo_move(move_id) ON DELETE RESTRICT,
    rank INTEGER NOT NULL,
    raw_score NUMERIC(15, 6) NOT NULL,
    score_0_100 NUMERIC(5, 2) NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_elite BOOLEAN NOT NULL DEFAULT FALSE,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (profile_id, pokemon_id, rank)
);

CREATE INDEX IF NOT EXISTS ix_pogo_scoring_moveset_pkmn ON pogo_scoring_moveset(pokemon_id);

-- =========================================================
-- 4. PvP IV Rank
CREATE TABLE IF NOT EXISTS pogo_pvp_iv_rank (
    pokemon_id BIGINT NOT NULL REFERENCES pogo_pokemon(id) ON DELETE CASCADE,
    league TEXT NOT NULL, -- 'great', 'ultra'
    cap_cp INTEGER NOT NULL, -- 1500, 2500
    rank INTEGER NOT NULL,
    level NUMERIC(4,1) NOT NULL,
    iv_attack SMALLINT NOT NULL,
    iv_defense SMALLINT NOT NULL,
    iv_stamina SMALLINT NOT NULL,
    cp INTEGER NOT NULL,
    atk_eff NUMERIC NOT NULL,
    def_eff NUMERIC NOT NULL,
    sta_eff NUMERIC NOT NULL,
    stat_product NUMERIC NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (pokemon_id, league, rank)
);

COMMIT;
