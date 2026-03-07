-- =========================================================
-- db_pogo_manager — Full schema migration
-- Run: psql -U <user> -d db_pogo_manager -f migrations/001_init.sql
-- =========================================================

BEGIN;

-- =========================================================
-- 1. Users & User Pokemon
-- =========================================================

CREATE TABLE IF NOT EXISTS users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email           TEXT UNIQUE,
  username        TEXT UNIQUE,
  display_name    TEXT,
  password_hash   TEXT,
  role            TEXT NOT NULL DEFAULT 'user',
  is_active       BOOLEAN NOT NULL DEFAULT true,
  last_login_at   TIMESTAMPTZ,
  trainer_name    TEXT,
  trainer_level   INT4,
  team            TEXT,
  timezone        TEXT NOT NULL DEFAULT 'Asia/Jakarta',
  language        TEXT NOT NULL DEFAULT 'id',
  rules_version   TEXT NOT NULL DEFAULT 'v1',
  settings_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- =========================================================
-- 2. PokéAPI Layer (main series)
-- =========================================================

-- 2a. Species
CREATE TABLE IF NOT EXISTS pokedex_species (
  id                      BIGSERIAL PRIMARY KEY,
  pokeapi_species_id      INT4 NOT NULL UNIQUE,
  name                    TEXT NOT NULL,
  generation              TEXT,
  color                   TEXT,
  shape                   TEXT,
  habitat                 TEXT,
  growth_rate             TEXT,
  gender_rate             INT2,
  capture_rate            INT2,
  base_happiness          INT2,
  hatch_counter           INT2,
  is_baby                 BOOLEAN NOT NULL DEFAULT false,
  is_legendary            BOOLEAN NOT NULL DEFAULT false,
  is_mythical             BOOLEAN NOT NULL DEFAULT false,
  has_gender_differences  BOOLEAN NOT NULL DEFAULT false,
  forms_switchable        BOOLEAN NOT NULL DEFAULT false,
  names                   JSONB NOT NULL DEFAULT '{}'::jsonb,
  genera                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  flavor_texts            JSONB NOT NULL DEFAULT '[]'::jsonb,
  egg_groups              TEXT[] DEFAULT '{}'::text[],
  dex_numbers             JSONB NOT NULL DEFAULT '{}'::jsonb,
  evolution_chain         JSONB,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2b. Pokemon
CREATE TABLE IF NOT EXISTS pokedex_pokemon (
  id                  BIGSERIAL PRIMARY KEY,
  pokeapi_pokemon_id  INT4 NOT NULL UNIQUE,
  species_id          BIGINT NOT NULL REFERENCES pokedex_species(id) ON DELETE RESTRICT,
  name                TEXT NOT NULL,
  form_name           TEXT,
  is_default          BOOLEAN NOT NULL DEFAULT true,
  height_dm           INT4,
  weight_hg           INT4,
  base_experience     INT4,
  pokemon_order       INT4,
  types               JSONB NOT NULL DEFAULT '[]'::jsonb,
  stats               JSONB NOT NULL DEFAULT '[]'::jsonb,
  abilities           JSONB NOT NULL DEFAULT '[]'::jsonb,
  sprites             JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2c. Move
CREATE TABLE IF NOT EXISTS pokedex_move (
  pokeapi_move_id  INT4 PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  type             TEXT,
  damage_class     TEXT,
  power            INT4,
  accuracy         INT4,
  pp               INT4,
  priority         INT4,
  data             JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2d. Pokemon <-> Move
CREATE TABLE IF NOT EXISTS pokedex_pokemon_move (
  pokedex_pokemon_id  BIGINT NOT NULL REFERENCES pokedex_pokemon(id) ON DELETE CASCADE,
  pokeapi_move_id     INT4 NOT NULL REFERENCES pokedex_move(pokeapi_move_id) ON DELETE CASCADE,
  learnset            JSONB NOT NULL DEFAULT '[]'::jsonb,
  PRIMARY KEY (pokedex_pokemon_id, pokeapi_move_id)
);


-- =========================================================
-- 3. Pokemon GO Layer (pokemon-go-api)
-- =========================================================

-- 3a. API Hashes (sync hemat: skip kalau sha sama)
CREATE TABLE IF NOT EXISTS pogo_api_hashes (
  endpoint    TEXT PRIMARY KEY,
  sha512      TEXT NOT NULL,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3b. GO Pokemon
CREATE TABLE IF NOT EXISTS pogo_pokemon (
  id              BIGSERIAL PRIMARY KEY,
  go_id           TEXT NOT NULL,
  form_id         TEXT NOT NULL,
  dex_nr          INT4 NOT NULL,
  generation      INT2,
  primary_type    TEXT,
  secondary_type  TEXT,
  pokemon_class   TEXT,
  stats           JSONB NOT NULL DEFAULT '{}'::jsonb,
  names           JSONB NOT NULL DEFAULT '{}'::jsonb,
  assets          JSONB NOT NULL DEFAULT '{}'::jsonb,
  data            JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_pogo_pokemon_goid_form UNIQUE (go_id, form_id),
  CONSTRAINT uq_pogo_pokemon_dex_form  UNIQUE (dex_nr, form_id)
);

CREATE INDEX IF NOT EXISTS ix_pogo_pokemon_dex   ON pogo_pokemon(dex_nr);
CREATE INDEX IF NOT EXISTS ix_pogo_pokemon_type1 ON pogo_pokemon(primary_type);
CREATE INDEX IF NOT EXISTS ix_pogo_pokemon_type2 ON pogo_pokemon(secondary_type);
CREATE INDEX IF NOT EXISTS ix_pogo_pokemon_goid  ON pogo_pokemon(go_id);

-- 3c. GO Moves
CREATE TABLE IF NOT EXISTS pogo_move (
  move_id      TEXT PRIMARY KEY,
  type         TEXT,
  power        INT4,
  energy       INT4,
  duration_ms  INT4,
  names        JSONB NOT NULL DEFAULT '{}'::jsonb,
  combat       JSONB NOT NULL DEFAULT '{}'::jsonb,
  data         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_pogo_move_type ON pogo_move(type);

-- 3d. GO Pokemon <-> Move (quick/cinematic/elite)
CREATE TABLE IF NOT EXISTS pogo_pokemon_move (
  pogo_pokemon_id  BIGINT NOT NULL REFERENCES pogo_pokemon(id) ON DELETE CASCADE,
  move_id          TEXT NOT NULL REFERENCES pogo_move(move_id) ON DELETE CASCADE,
  move_kind        TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT pk_pogo_pokemon_move PRIMARY KEY (pogo_pokemon_id, move_id, move_kind),
  CONSTRAINT ck_pogo_move_kind CHECK (
    move_kind IN ('quick','cinematic','elite_quick','elite_cinematic')
  )
);

CREATE INDEX IF NOT EXISTS ix_pogo_pkmv_pokemon ON pogo_pokemon_move(pogo_pokemon_id);
CREATE INDEX IF NOT EXISTS ix_pogo_pkmv_move    ON pogo_pokemon_move(move_id);

-- 3e. GO Types
CREATE TABLE IF NOT EXISTS pogo_type (
  type               TEXT PRIMARY KEY,
  names              JSONB NOT NULL DEFAULT '{}'::jsonb,
  double_damage_from TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  half_damage_from   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  no_damage_from     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  weather_boost      JSONB NOT NULL DEFAULT '{}'::jsonb,
  data               JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3f. Dynamic Snapshots (historis)
CREATE TABLE IF NOT EXISTS pogo_raidboss_snapshot (
  id          BIGSERIAL PRIMARY KEY,
  sha512      TEXT NOT NULL,
  data        JSONB NOT NULL,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_pogo_raidboss_sha UNIQUE (sha512)
);

CREATE TABLE IF NOT EXISTS pogo_maxbattle_snapshot (
  id          BIGSERIAL PRIMARY KEY,
  sha512      TEXT NOT NULL,
  data        JSONB NOT NULL,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_pogo_maxbattle_sha UNIQUE (sha512)
);

CREATE TABLE IF NOT EXISTS pogo_quest_snapshot (
  id          BIGSERIAL PRIMARY KEY,
  sha512      TEXT NOT NULL,
  data        JSONB NOT NULL,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_pogo_quest_sha UNIQUE (sha512)
);

CREATE INDEX IF NOT EXISTS ix_pogo_raidboss_fetched  ON pogo_raidboss_snapshot(fetched_at DESC);
CREATE INDEX IF NOT EXISTS ix_pogo_maxbattle_fetched ON pogo_maxbattle_snapshot(fetched_at DESC);
CREATE INDEX IF NOT EXISTS ix_pogo_quest_fetched     ON pogo_quest_snapshot(fetched_at DESC);


-- =========================================================
-- 4. Mapping & User Pokemon
-- =========================================================

-- 4a. PokéAPI <-> GO Mapping
CREATE TABLE IF NOT EXISTS pokedex_pokemon_go_map (
  id                  BIGSERIAL PRIMARY KEY,
  pokedex_pokemon_id  BIGINT NOT NULL REFERENCES pokedex_pokemon(id) ON DELETE CASCADE,
  pogo_pokemon_id     BIGINT NOT NULL REFERENCES pogo_pokemon(id) ON DELETE CASCADE,
  match_rule          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_map_pokedex_pokemon ON pokedex_pokemon_go_map(pokedex_pokemon_id);
CREATE INDEX IF NOT EXISTS ix_map_pogo_pokemon    ON pokedex_pokemon_go_map(pogo_pokemon_id);

-- 4b. CP Multiplier
CREATE TABLE IF NOT EXISTS pogo_cpm (
  level       NUMERIC(4,1) PRIMARY KEY,
  cpm         NUMERIC(18,12) NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4c. User Pokemon
CREATE TABLE IF NOT EXISTS user_pokemon (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pokedex_pokemon_id  BIGINT NOT NULL REFERENCES pokedex_pokemon(id) ON DELETE RESTRICT,
  pogo_pokemon_id     BIGINT NULL REFERENCES pogo_pokemon(id) ON DELETE RESTRICT,
  nickname            TEXT,
  shiny               BOOLEAN NOT NULL DEFAULT false,
  cp                  INT4 CHECK (cp >= 0),
  level               NUMERIC CHECK (level >= 0),
  iv_attack           INT2 CHECK (iv_attack >= 0 AND iv_attack <= 15),
  iv_defense          INT2 CHECK (iv_defense >= 0 AND iv_defense <= 15),
  iv_stamina          INT2 CHECK (iv_stamina >= 0 AND iv_stamina <= 15),
  caught_at           TIMESTAMPTZ,
  extra               JSONB,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_user_pokemon_pogo_pokemon_id ON user_pokemon(pogo_pokemon_id);

COMMIT;
