-- 006_formula_tagging.sql
-- Formula registry for scoring/tagging + auto-tag result tables

BEGIN;

-- =========================================================
-- 1. Formula Registry
-- =========================================================
CREATE TABLE IF NOT EXISTS pogo_formula (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    scope       TEXT NOT NULL,

    -- Expression (Python-safe eval string)
    expression  TEXT NOT NULL,

    -- Derived variable definitions
    variables   JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Default weights / thresholds
    defaults    JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Tagging display config (only for kind='tagging')
    tag_config  JSONB,

    description TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    version     TEXT NOT NULL DEFAULT 'v1',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_pogo_formula UNIQUE (name, version),
    CONSTRAINT ck_pogo_formula_kind CHECK (kind IN ('scoring', 'tagging')),
    CONSTRAINT ck_pogo_formula_scope CHECK (scope IN ('move', 'pokemon', 'user_pokemon'))
);

CREATE INDEX IF NOT EXISTS ix_pogo_formula_active ON pogo_formula(is_active, kind);

-- =========================================================
-- 2. Link formulas to existing profile tables (optional FK)
-- =========================================================
ALTER TABLE pogo_scoring_profile
    ADD COLUMN IF NOT EXISTS formula_id BIGINT REFERENCES pogo_formula(id) ON DELETE SET NULL;

ALTER TABLE pogo_scoring_pokemon_profile
    ADD COLUMN IF NOT EXISTS formula_id BIGINT REFERENCES pogo_formula(id) ON DELETE SET NULL;

-- =========================================================
-- 3. Auto-tag results for global pokemon
-- =========================================================
CREATE TABLE IF NOT EXISTS pogo_pokemon_tag (
    id          BIGSERIAL PRIMARY KEY,
    formula_id  BIGINT NOT NULL REFERENCES pogo_formula(id) ON DELETE CASCADE,
    pokemon_id  BIGINT NOT NULL REFERENCES pogo_pokemon(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    context     TEXT,
    confidence  NUMERIC(5,2),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_pokemon_tag UNIQUE (formula_id, pokemon_id, tag)
);

CREATE INDEX IF NOT EXISTS ix_pokemon_tag_pokemon ON pogo_pokemon_tag(pokemon_id);
CREATE INDEX IF NOT EXISTS ix_pokemon_tag_tag ON pogo_pokemon_tag(tag);
CREATE INDEX IF NOT EXISTS ix_pokemon_tag_context ON pogo_pokemon_tag(context);

-- =========================================================
-- 4. Auto-tag results for user roster pokemon
-- =========================================================
CREATE TABLE IF NOT EXISTS pogo_user_pokemon_tag (
    id              BIGSERIAL PRIMARY KEY,
    formula_id      BIGINT NOT NULL REFERENCES pogo_formula(id) ON DELETE CASCADE,
    user_pokemon_id UUID NOT NULL REFERENCES user_pokemon(id) ON DELETE CASCADE,
    tag             TEXT NOT NULL,
    context         TEXT,
    confidence      NUMERIC(5,2),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_user_pokemon_tag UNIQUE (formula_id, user_pokemon_id, tag)
);

CREATE INDEX IF NOT EXISTS ix_upt_user_pokemon ON pogo_user_pokemon_tag(user_pokemon_id);
CREATE INDEX IF NOT EXISTS ix_upt_tag ON pogo_user_pokemon_tag(tag);

COMMIT;
