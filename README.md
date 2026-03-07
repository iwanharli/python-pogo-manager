# 🔴 Python Pogo Seeder

Seeder untuk menarik data Pokédex dan Pokémon GO ke database PostgreSQL (`db_pogo_manager`), menghitung skor performa Move/Moveset/Pokémon, dan menghasilkan ranking PvP/PvE yang siap dikonsumsi oleh frontend.

| Sumber                                                            | Script                    | Deskripsi                                         |
| ----------------------------------------------------------------- | ------------------------- | ------------------------------------------------- |
| [PokéAPI](https://pokeapi.co/)                                    | `seed_pokedex.py`         | Data main series (species, pokemon, moves)        |
| [Pokemon GO API](https://pokemon-go-api.github.io/pokemon-go-api) | `seed_pogo.py`            | Data GO (stats, moves, raids, quests, CPM)        |
| Internal Logic                                                    | `seed_scoring.py`         | Skor performa move individu (0-100)               |
| Internal Logic                                                    | `seed_pokemon_scoring.py` | Moveset recommendations + Tempo + Pokemon scoring |
| Dummy / Internal                                                  | `seeder/seed_user.py`     | Create admin/default users                        |

## Quick Start

```bash
# 1. Setup environment
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # sesuaikan credentials

# 2. One-shot: create tables + seed semua data
python setup.py

# Atau reset total:
python setup.py --reset
```

## Environment Variables

```env
# ── Database ──────────────────────────────────────
DB_HOST=localhost
DB_PORT=5432
DB_NAME=db_pogo_manager
DB_USER=iwanharli
DB_PASS=

# ── API Sources ───────────────────────────────────
POKEAPI_BASE_URL=https://pokeapi.co/api/v2
POGO_API_BASE_URL=https://pokemon-go-api.github.io/pokemon-go-api
GAMEMASTER_URL=https://raw.githubusercontent.com/.../latest.json

# ── Options ───────────────────────────────────────
SEED_LIMIT=0           # 0 = auto-detect semua species
CACHE_DIR=./cache
LOG_DIR=./logs
```

---

## Scripts

### 1. `seed_pokedex.py` — PokéAPI (Main Series)

Menarik data dari PokéAPI (species, pokemon, moves, learnset).

```bash
python seed_pokedex.py               # update — skip yang sudah ada
python seed_pokedex.py --reset       # truncate + seed ulang
SEED_LIMIT=151 python seed_pokedex.py  # Gen 1 saja
```

**Tabel:** `pokedex_species`, `pokedex_pokemon`, `pokedex_move`, `pokedex_pokemon_move`

---

### 2. `seed_pogo.py` — Pokemon GO API

Menarik data spesifik Pokémon GO dengan **hash-based sync** — skip kalau data belum berubah. Juga menarik CPM (CP Multiplier) dari GameMaster.

```bash
python seed_pogo.py                  # sync (skip jika hash sama)
python seed_pogo.py --reset          # truncate + seed ulang
python seed_pogo.py --force          # force re-fetch meski hash sama
```

**Sync strategy:**

- **Static** (upsert): `pogo_pokemon`, `pogo_move`, `pogo_pokemon_move`, `pogo_type`
- **Dynamic** (snapshot): `pogo_raidboss_snapshot`, `pogo_maxbattle_snapshot`, `pogo_quest_snapshot`
- **Mapping**: `pokedex_pokemon_go_map` (match by `dex_nr`)
- **CPM**: `pogo_cpm` — CP Multiplier per level (1.0–51.0), auto-detect obfuscation Niantic

---

### 3. `setup.py` — One-Shot Setup

Jalankan migration + semua seeder dalam satu command.

```bash
python setup.py                      # create tables + seed semua
python setup.py --reset              # reset + re-seed semua
```

---

### 4. `seeder/seed_user.py` — User Seeder

Menambah data user dummy (admin/test) ke tabel `users`.

```bash
python seeder/seed_user.py           # upsert default users
```

---

### 5. `seed_scoring.py` — Move Scoring (Fase 1)

Menghitung skor performa per move individu (skala 0–100) di 4 konteks evaluasi:

| Konteks         | Rumus                                     | Bias           |
| --------------- | ----------------------------------------- | -------------- |
| **PVE Fast**    | `(0.65 × DPS) + (0.35 × EPS)`             | DPS-heavy      |
| **PVE Charged** | `(0.75 × DPE) + (0.25 × DPS)`             | DPE-heavy      |
| **PVP Fast**    | `(0.60 × EPT) + (0.40 × DPT)`             | Pressure-heavy |
| **PVP Charged** | `(0.80 × DPE) + (0.20 × (1/Energy Cost))` | Efficiency     |

```bash
python seed_scoring.py
```

**Tabel:** `pogo_scoring_profile`, `pogo_scoring_move`

---

### 6. `seed_pokemon_scoring.py` — Pokemon & Moveset Scoring (Fase 2)

Mengevaluasi keseluruhan performa Pokémon berdasarkan 5 profil utama.

#### Profil Evaluasi

| Profil      | Konteks       | Stat Bias | CP Cap | Keterangan                  |
| ----------- | ------------- | --------- | ------ | --------------------------- |
| PVE Raid    | `pve_raid`    | Attack    | –      | Raid DPS output             |
| PVE Gym Def | `pve_gym_def` | Bulk      | –      | Bertahan di Gym             |
| PVP Great   | `pvp_great`   | Bulk      | 1500   | Great League (stat product) |
| PVP Ultra   | `pvp_ultra`   | Bulk      | 2500   | Ultra League (stat product) |
| PVP Master  | `pvp_master`  | Bulk      | –      | Master League (max stats)   |

#### Pipeline

1. **Rekomendasi Moveset (`pogo_scoring_moveset`)**
   - PVE: evaluasi kombinasi 1 Fast + 1 Charged Move
   - PVP: evaluasi kombinasi 1 Fast + 2 Charged Move
   - Simpan 3 moveset teratas per Pokémon per profil

2. **Tempo Score (PvP Only)** — _Seberapa cepat tekanan charged move tercapai?_

   4 komponen dihitung per moveset:

   | Komponen           | Rumus                                                | Arti                                |
   | ------------------ | ---------------------------------------------------- | ----------------------------------- |
   | **Spam Pressure**  | `1 / (min(TTC₁, TTC₂) + 1)`                          | Makin cepat spam → makin tinggi     |
   | **Bait Potential** | `I(cheap≤40 ∧ expensive≥55) × nuke_dpe / (cheap/10)` | Cheap + Nuke combo = bait shield    |
   | **Buff/Debuff**    | `Σ(chance × stage_delta_weighted)`                   | Buff diri / debuff lawan = pressure |
   | **Nuke**           | `dpe × log1p(cost)`                                  | DPE tinggi + cost tinggi = finisher |

   **Turns-to-Charged (TTC):**

   ```
   fast_needed = ceil(charged_cost / fast_energy_per_use)
   turns_to_charged = fast_needed × fast_turns
   ```

   **Blending:** `raw_total = 0.70 × core_raw + 0.30 × tempo_raw` (PvP only, PvE = 0)

   **Normalisasi:** p05/p95 percentile → `tempo_score_0_100`

3. **Skor Final Pokemon (`pogo_scoring_pokemon`)**
   - `65% × Moveset Score (Rank #1) + 35% × Stat Score`
   - PVE: Stat Score bias Attack
   - PVP: Stat Score bias Stat Product / Bulk (Defence × Stamina)

4. **PVP IV Rank (`pogo_pvp_iv_rank`)**
   - Brute-force semua 4096 kombinasi IV (0–15) × Level
   - Cari best Stat Product di bawah CP cap per species per league
   - Digunakan untuk kalkulasi akurat skor personal di My Roster

```bash
python seed_pokemon_scoring.py
```

**Tabel:** `pogo_scoring_pokemon_profile`, `pogo_scoring_moveset`, `pogo_scoring_pokemon`, `pogo_pvp_iv_rank`

---

## Database Tables

### PokéAPI Layer

| Tabel                  | Deskripsi                                          |
| ---------------------- | -------------------------------------------------- |
| `pokedex_species`      | Species (nama, generasi, habitat, evolution chain) |
| `pokedex_pokemon`      | Pokemon (types, stats, abilities, sprites)         |
| `pokedex_move`         | Move (type, power, accuracy, pp)                   |
| `pokedex_pokemon_move` | Pokemon ↔ move + learnset                          |

### Pokemon GO Layer

| Tabel                     | Deskripsi                              |
| ------------------------- | -------------------------------------- |
| `pogo_api_hashes`         | SHA512 hash per endpoint (sync hemat)  |
| `pogo_pokemon`            | GO stats, names, assets                |
| `pogo_move`               | GO moves (PvE + PvP combat data)       |
| `pogo_pokemon_move`       | quick / cinematic / elite moves        |
| `pogo_type`               | 18 types + damage multiplier + weather |
| `pogo_raidboss_snapshot`  | Raid boss snapshot (historis)          |
| `pogo_maxbattle_snapshot` | Max battle snapshot (historis)         |
| `pogo_quest_snapshot`     | Quest snapshot (historis)              |
| `pogo_cpm`                | CP Multiplier per level (1.0–51.0)     |

### Unified Scoring Layer

| Tabel                          | Deskripsi                                                           |
| ------------------------------ | ------------------------------------------------------------------- |
| `pogo_scoring_profile`         | Definisi rumus & normalizer untuk Move individu                     |
| `pogo_scoring_move`            | Skor 0-100 per Move untuk setiap profil evaluasi                    |
| `pogo_scoring_pokemon_profile` | Definisi profil bermain (PVE / PVP League) Pokémon                  |
| `pogo_scoring_moveset`         | Ranking moveset + `tempo_raw`, `tempo_score_0_100`, breakdown JSONB |
| `pogo_scoring_pokemon`         | Skor akhir performa 0-100 per Pokémon (profile-based)               |
| `pogo_pvp_iv_rank`             | Best IV/Level/Stat Product per species per CP cap league            |

### Mapping & User

| Tabel                    | Deskripsi                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------- |
| `pokedex_pokemon_go_map` | PokéAPI ↔ GO (Mapping 1:Many)                                                         |
| `users`                  | Data akun user (seeder dummy tersedia)                                                |
| `user_pokemon`           | Koleksi pokemon user + `fast_move`, `charged_move_1/2` (moveset aktual untuk scoring) |

### Migrations

| File                         | Deskripsi                                                 |
| ---------------------------- | --------------------------------------------------------- |
| `001_init.sql`               | Full schema — users, pokedex, pogo, mapping tables        |
| `002_scoring.sql`            | Scoring profiles + move scoring tables                    |
| `003_pokemon_scoring.sql`    | Pokemon scoring + moveset + PVP IV rank tables            |
| `004_user_pokemon_moves.sql` | Kolom `fast_move`, `charged_move_1/2` di `user_pokemon`   |
| `005_moveset_tempo.sql`      | Kolom `tempo_raw`, `tempo_score_0_100` di scoring moveset |

---

## Deployment & Cron Jobs

### 1. Initial Setup Server (One-Shot Deployment)

Saat pertama kali mengunduh seeder ke server kosong:

```bash
# 1. Jalankan migrasi tabel & seed data master (Pokedex, GameMaster, CPM)
python3 setup.py --reset

# 2. Kalkulasi Skor Move dan Moveset Pokemon (PVE & PVP)
python3 seed_scoring.py
python3 seed_pokemon_scoring.py

# 3. (Opsional) Tambah profil dummy admin untuk mengetes UI
python3 seeder/seed_user.py
```

### 2. Sinkronisasi Data Berkala

Pokemon GO adalah game dinamis (rotasi raid, misi, hingga perubahan balance move PvP).
Gunakan _Crontab_ untuk menjalankan _scheduler_ pembaruan ini. `seed_pokedex.py` dan `setup.py` **TIDAK BOLEH** dijalankan melalui cron.

| Script           | Frekuensi    | Alasan                                                                                                |
| ---------------- | ------------ | ----------------------------------------------------------------------------------------------------- |
| `seed_pogo.py`   | **2x/hari**  | Raid boss & quest berputar berkala. Hemat bandwith karena _hash-based_                                |
| `seed_scoring`\* | **1x/Bulan** | Opsional: Mengkalkulasi ulang Moveset/Skor Pokemon jika Niantic melakukan peluncuran Move/Season baru |

_\*Script scoring meliputi `seed_scoring.py` dan `seed_pokemon_scoring.py`._

#### Contoh Crontab

```cron
# Pokemon GO sync — 2x sehari (06:00 & 18:00)
0 6,18 * * *  cd /path/to/python-pogo-seeder && venv/bin/python3 seed_pogo.py >> logs/cron.log 2>&1

# Re-calculate Scoring — Setiap tanggal 1 jam 05:00 pagi
0 5 1 * *     cd /path/to/python-pogo-seeder && venv/bin/python3 seed_scoring.py && venv/bin/python3 seed_pokemon_scoring.py >> logs/cron_score.log 2>&1
```

---

## Scoring Architecture

```
                                  seed_scoring.py
                                  ┌───────────────────────────┐
                                  │  Move Evaluation (Fase 1) │
                                  │  ─────────────────────────│
                                  │  PVE Fast  → DPS + EPS    │
                    pogo_move     │  PVE Charged → DPE + DPS  │
                 ┌──────────────→ │  PVP Fast  → EPT + DPT    │ → pogo_scoring_move
                 │                │  PVP Charged → DPE + Cost  │
                 │                └───────────────────────────┘
                 │
                 │                seed_pokemon_scoring.py
                 │                ┌───────────────────────────────────────────┐
                 │                │  Pokemon Evaluation (Fase 2)             │
                 │                │  ─────────────────────────────────────── │
  pogo_pokemon ──┤                │  1. Moveset Combos (Fast+Charged×2)     │
  pogo_cpm ──────┤                │     → core_raw (move synergy score)     │
  pogo_scoring   │                │                                         │
   _move ────────┤──────────────→ │  2. Tempo Score (PvP Only)              │
                 │                │     Spam + Bait + Buff + Nuke           │
                 │                │     → tempo_raw (blended 30%)           │ → pogo_scoring_moveset
                 │                │                                         │
                 │                │  3. Pokemon Score                       │
                 │                │     65% Moveset + 35% Stats             │ → pogo_scoring_pokemon
                 │                │     (PVP: Stat Product / Bulk)          │
                 │                │     (PVE: Attack-biased)                │
                 │                │                                         │
                 │                │  4. PVP IV Rank                         │
                 │                │     Best IV+Level under CP cap          │ → pogo_pvp_iv_rank
                 │                └───────────────────────────────────────────┘
                 │
                 │                pogo-manager (Nuxt API)
                 │                ┌───────────────────────────────────────────┐
                 │                │  my-top.get.ts — Personal Scoring        │
                 │                │  ──────────────────────────────────────── │
  user_pokemon ──┘                │  • Moveset score → dari equipped moves   │
                                  │  • Stat Product → dari user IV + Level   │
                                  │  • SP Ratio = user_sp / best_sp          │
                                  │  • Fallback global jika tanpa moveset    │
                                  └───────────────────────────────────────────┘
```

---

## Fitur Umum

- **Disk cache** — response API di-cache, re-run hemat bandwidth
- **Idempotent** — `ON CONFLICT` upsert, aman dijalankan berulang
- **Polite fetching** — User-Agent, persistent session, delay antar request
- **Retry & backoff** — 3x retry dengan exponential backoff
- **Dual logging** — console (emoji) + file log (timestamp)
- **JSONB adapter** — `psycopg2.extras.Json()` untuk casting aman
- **Tempo scoring** — PvP moveset pressure analysis (spam, bait, buff, nuke)
- **Personal scoring** — My Roster uses actual user IV/moves for ranking

## Logging

| Script                    | Log File                        |
| ------------------------- | ------------------------------- |
| `seed_pokedex.py`         | `logs/seed.log`                 |
| `seed_pogo.py`            | `logs/seed_pogo.log`            |
| `seed_scoring.py`         | `logs/seed_scoring.log`         |
| `seed_pokemon_scoring.py` | `logs/seed_pokemon_scoring.log` |

---

## Struktur Folder

```
python-pogo-seeder/
├── setup.py                  # One-shot: migration + all seeders
├── seed_pokedex.py           # Seeder PokéAPI
├── seed_pogo.py              # Seeder Pokemon GO API + CPM
├── seed_scoring.py           # Move scoring (Fase 1)
├── seed_pokemon_scoring.py   # Pokemon/moveset scoring + tempo (Fase 2)
├── seeder/                   # Custom/Internal seeders
│   └── seed_user.py          # Create dummy user
├── lib/                      # Shared utilities
│   ├── db.py                 # Database connection + safe_transaction
│   └── log.py                # Logger setup
├── requirements.txt
├── .env
├── migrations/
│   ├── 001_init.sql          # Core schema (users, pokedex, pogo, maps)
│   ├── 002_scoring.sql       # Move scoring profiles + tables
│   ├── 003_pokemon_scoring.sql # Pokemon scoring + moveset + IV rank
│   ├── 004_user_pokemon_moves.sql # User moveset columns
│   └── 005_moveset_tempo.sql # Tempo scoring columns
├── cache/                    # (git-ignored)
│   ├── *.json                # PokéAPI cache
│   ├── pogo/                 # GO API cache
│   └── gm/                   # GameMaster cache
└── logs/                     # (git-ignored)
    ├── seed.log
    ├── seed_pogo.log
    ├── seed_scoring.log
    └── seed_pokemon_scoring.log
```
# python-pogo-manager
