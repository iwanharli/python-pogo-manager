"""
seed_pogo.py — Seed Pokemon GO data from pokemon-go-api into PostgreSQL.

Sync strategy:
  - Fetch /api/hashes.json first, compare with pogo_api_hashes table.
  - Static (upsert when hash changes): pogo_pokemon, pogo_move, pogo_pokemon_move, pogo_type
  - Dynamic (insert snapshot when hash changes): pogo_raidboss_snapshot, pogo_maxbattle_snapshot, pogo_quest_snapshot
  - Mapping: pokedex_pokemon_go_map (dex_nr match)

Usage:
    python seed_pogo.py               # sync (skip if hashes unchanged, default)
    python seed_pogo.py --update      # same as above
    python seed_pogo.py --reset       # truncate all pogo_* tables, re-seed from scratch
    python seed_pogo.py --force       # force re-fetch even if hashes match
    python seed_pogo.py --dry-run     # preview tanpa eksekusi
"""

import time
import argparse

from tqdm import tqdm

from psycopg2.extras import Json

from lib.config import POGO_API_BASE_URL, GAMEMASTER_URL, CACHE_DIR, DB_NAME, DB_HOST, DB_PORT, LOG_DIR
from lib.log import setup_logger
from lib.http import fetch
from lib.db import safe_transaction

log = setup_logger("pogo-seeder", "seed_pogo.log")

CACHE = CACHE_DIR / "pogo"
CACHE_GM = CACHE_DIR / "gm"


# ── Helpers ──────────────────────────────────────────────────

def get_stored_hashes(cur) -> dict:
    """Return {endpoint: sha512} from pogo_api_hashes."""
    cur.execute("SELECT endpoint, sha512 FROM pogo_api_hashes")
    return {row[0]: row[1] for row in cur.fetchall()}


def update_hash(cur, endpoint: str, sha512: str):
    cur.execute("""
        INSERT INTO pogo_api_hashes (endpoint, sha512, fetched_at)
        VALUES (%s, %s, now())
        ON CONFLICT (endpoint) DO UPDATE SET
            sha512 = EXCLUDED.sha512,
            fetched_at = now(),
            updated_at = now()
    """, (endpoint, sha512))


# ── Truncate ─────────────────────────────────────────────────

def truncate_pogo(cur):
    """Truncate all pogo tables and clear cache."""
    tables = [
        "pokedex_pokemon_go_map",
        "pogo_pokemon_move",
        "pogo_move",
        "pogo_pokemon",
        "pogo_type",
        "pogo_raidboss_snapshot",
        "pogo_maxbattle_snapshot",
        "pogo_quest_snapshot",
        "pogo_api_hashes",
        "pogo_cpm",
    ]
    for t in tables:
        cur.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")
        log.info("  🗑 Truncated %s", t)

    count = 0
    for f in CACHE.glob("*.json"):
        f.unlink()
        count += 1
    log.info("  🗑 Cleared %d cached files", count)
    log.info("  ✅ All pogo tables & cache cleared")


# ── Seed: Types ──────────────────────────────────────────────

def seed_types(cur):
    """Seed pogo_type from /api/types.json."""
    log.info("\n🟣 Seeding types...")
    data = fetch(f"{POGO_API_BASE_URL}/api/types.json", CACHE, use_cache=False)

    for t in data:
        cur.execute("""
            INSERT INTO pogo_type (type, names, double_damage_from, half_damage_from,
                                   no_damage_from, weather_boost, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (type) DO UPDATE SET
                names = EXCLUDED.names,
                double_damage_from = EXCLUDED.double_damage_from,
                half_damage_from = EXCLUDED.half_damage_from,
                no_damage_from = EXCLUDED.no_damage_from,
                weather_boost = EXCLUDED.weather_boost,
                data = EXCLUDED.data,
                updated_at = now()
        """, (
            t["type"],
            Json(t.get("names", {})),
            t.get("doubleDamageFrom", []),
            t.get("halfDamageFrom", []),
            t.get("noDamageFrom", []),
            Json(t.get("weatherBoost", {})),
            Json(t),
        ))
        log.debug("UPSERT type %s", t["type"])

    log.info("  ✔ %d types seeded", len(data))


# ── Seed: Pokedex (pokemon + moves + pivot) ──────────────────

def seed_pokedex(cur):
    """Seed pogo_pokemon, pogo_move, pogo_pokemon_move from /api/pokedex.json."""
    log.info("\n🔵 Seeding GO pokedex...")
    data = fetch(f"{POGO_API_BASE_URL}/api/pokedex.json", CACHE, use_cache=False)

    seen_moves = set()
    total_pokemon = 0
    total_links = 0

    for pkmn in tqdm(data, desc="GO Pokemon", unit="pk", disable=None):
        go_id = pkmn["id"]
        form_id = pkmn["formId"]

        # Upsert pokemon
        cur.execute("""
            INSERT INTO pogo_pokemon (
                go_id, form_id, dex_nr, generation,
                primary_type, secondary_type, pokemon_class,
                stats, names, assets, data
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (go_id, form_id) DO UPDATE SET
                dex_nr = EXCLUDED.dex_nr,
                generation = EXCLUDED.generation,
                primary_type = EXCLUDED.primary_type,
                secondary_type = EXCLUDED.secondary_type,
                pokemon_class = EXCLUDED.pokemon_class,
                stats = EXCLUDED.stats,
                names = EXCLUDED.names,
                assets = EXCLUDED.assets,
                data = EXCLUDED.data,
                updated_at = now()
            RETURNING id
        """, (
            go_id,
            form_id,
            pkmn["dexNr"],
            pkmn.get("generation"),
            pkmn.get("primaryType", {}).get("type") if pkmn.get("primaryType") else None,
            pkmn.get("secondaryType", {}).get("type") if pkmn.get("secondaryType") else None,
            pkmn.get("pokemonClass"),
            Json(pkmn.get("stats") or {}),
            Json(pkmn.get("names", {})),
            Json(pkmn.get("assets") or {}),
            Json(pkmn),
        ))
        pogo_pokemon_id = cur.fetchone()[0]
        total_pokemon += 1

        # Collect and upsert moves, then link
        move_kinds = {
            "quick": pkmn.get("quickMoves", {}),
            "cinematic": pkmn.get("cinematicMoves", {}),
            "elite_quick": pkmn.get("eliteQuickMoves", {}),
            "elite_cinematic": pkmn.get("eliteCinematicMoves", {}),
        }

        for kind, moves_raw in move_kinds.items():
            # API returns {} (dict) for moves, but [] (empty list) when none
            if not isinstance(moves_raw, dict):
                continue
            for move_id, move_data in moves_raw.items():
                # Upsert move (once)
                if move_id not in seen_moves:
                    cur.execute("""
                        INSERT INTO pogo_move (
                            move_id, type, power, energy, duration_ms,
                            names, combat, data
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (move_id) DO UPDATE SET
                            type = EXCLUDED.type,
                            power = EXCLUDED.power,
                            energy = EXCLUDED.energy,
                            duration_ms = EXCLUDED.duration_ms,
                            names = EXCLUDED.names,
                            combat = EXCLUDED.combat,
                            data = EXCLUDED.data,
                            updated_at = now()
                    """, (
                        move_id,
                        move_data.get("type", {}).get("type") if move_data.get("type") else None,
                        move_data.get("power"),
                        move_data.get("energy"),
                        move_data.get("durationMs"),
                        Json(move_data.get("names", {})),
                        Json(move_data.get("combat") or {}),
                        Json(move_data),
                    ))
                    seen_moves.add(move_id)
                    log.debug("UPSERT move %s", move_id)

                # Link pokemon <-> move
                cur.execute("""
                    INSERT INTO pogo_pokemon_move (pogo_pokemon_id, move_id, move_kind)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (pogo_pokemon_id, move_id, move_kind) DO NOTHING
                """, (pogo_pokemon_id, move_id, kind))
                total_links += 1

        if total_pokemon % 100 == 0:
            log.info("  ... %d pokemon processed", total_pokemon)

    log.info("  ✔ %d pokemon, %d moves, %d links seeded",
             total_pokemon, len(seen_moves), total_links)


# ── Seed: Shadow / Purified special moves ────────────────────

def seed_shadow_purified_moves(cur):
    """Insert Frustration (Shadow) and Return (Purified) moves.

    These moves are not part of any species' regular moveset in the API,
    but all Shadow Pokémon have Frustration and all Purified have Return.
    """
    log.info("\n🌑 Seeding Shadow/Purified special moves...")

    special_moves = [
        {
            "move_id": "FRUSTRATION",
            "type": "POKEMON_TYPE_NORMAL",
            "power": 10,
            "energy": -33,
            "duration_ms": 2000,
            "names": {"English": "Frustration"},
            "combat": {"power": 10, "energy": 33, "turns": 1},
        },
        {
            "move_id": "RETURN",
            "type": "POKEMON_TYPE_NORMAL",
            "power": 35,
            "energy": -33,
            "duration_ms": 700,
            "names": {"English": "Return"},
            "combat": {"power": 130, "energy": 33, "turns": 1},
        },
    ]

    for m in special_moves:
        cur.execute("""
            INSERT INTO pogo_move (
                move_id, type, power, energy, duration_ms,
                names, combat, data
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (move_id) DO UPDATE SET
                type = EXCLUDED.type,
                power = EXCLUDED.power,
                energy = EXCLUDED.energy,
                duration_ms = EXCLUDED.duration_ms,
                names = EXCLUDED.names,
                combat = EXCLUDED.combat,
                data = EXCLUDED.data,
                updated_at = now()
        """, (
            m["move_id"],
            m["type"],
            m["power"],
            m["energy"],
            m["duration_ms"],
            Json(m["names"]),
            Json(m["combat"]),
            Json({"shadow_purified_special": True}),
        ))
        log.debug("UPSERT special move %s", m["move_id"])

    log.info("  ✔ %d special moves seeded (Frustration, Return)", len(special_moves))


# ── Seed: Snapshots (dynamic) ────────────────────────────────

def seed_snapshot(cur, endpoint: str, table: str, remote_hash: str, stored_hash: str | None):
    """Insert a new snapshot if hash changed."""
    if remote_hash == stored_hash:
        log.info("  ⏭ %s hash unchanged, skipping", endpoint)
        return False

    log.info("  ↓ Fetching %s (hash changed)...", endpoint)
    data = fetch(f"{POGO_API_BASE_URL}/api/{endpoint}", CACHE, use_cache=False)

    cur.execute(f"""
        INSERT INTO {table} (sha512, data, fetched_at)
        VALUES (%s, %s, now())
        ON CONFLICT (sha512) DO NOTHING
    """, (remote_hash, Json(data)))

    log.info("  ✔ Snapshot inserted into %s", table)
    return True


# ── Seed: Mapping ────────────────────────────────────────────

def seed_mapping(cur):
    """Build pokedex_pokemon_go_map by matching dex_nr."""
    log.info("\n🔗 Building pokedex ↔ pogo mapping...")

    cur.execute("""
        SELECT pp.id, ps.pokeapi_species_id
        FROM pokedex_pokemon pp
        JOIN pokedex_species ps ON pp.species_id = ps.id
        WHERE pp.is_default = true
    """)
    pokedex_map = {row[1]: row[0] for row in cur.fetchall()}

    cur.execute("""
        SELECT DISTINCT ON (dex_nr) id, dex_nr, go_id, form_id
        FROM pogo_pokemon
        ORDER BY dex_nr, (go_id = form_id) DESC, id
    """)
    pogo_rows = cur.fetchall()

    matched = 0
    for pogo_id, dex_nr, go_id, form_id in pogo_rows:
        pokedex_pokemon_id = pokedex_map.get(dex_nr)
        if not pokedex_pokemon_id:
            continue

        cur.execute("""
            INSERT INTO pokedex_pokemon_go_map (pokedex_pokemon_id, pogo_pokemon_id, match_rule)
            VALUES (%s, %s, %s)
            ON CONFLICT (pokedex_pokemon_id) DO UPDATE SET
                pogo_pokemon_id = EXCLUDED.pogo_pokemon_id,
                match_rule = EXCLUDED.match_rule,
                updated_at = now()
        """, (
            pokedex_pokemon_id,
            pogo_id,
            Json({"method": "dex_nr_match", "dex_nr": dex_nr, "go_id": go_id}),
        ))
        matched += 1

    log.info("  ✔ %d mappings created (of %d pogo base forms)", matched, len(pogo_rows))


# ── Main ─────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed Pokemon GO data from pokemon-go-api into PostgreSQL.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--reset", action="store_true",
        help="Truncate semua tabel pogo_*, seed ulang dari awal.",
    )
    group.add_argument(
        "--update", action="store_true", default=True,
        help="Sync hanya jika hash berubah (default).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-fetch semua data meskipun hash sama.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview berapa data yang akan di-fetch/skip tanpa eksekusi.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    mode = "reset" if args.reset else "update"

    log.info("=" * 60)
    log.info("  Pokemon GO API → db_pogo_manager Seeder")
    log.info("  Mode: %s%s | DB: %s@%s:%s",
             mode, "+force" if args.force else "",
             DB_NAME, DB_HOST, DB_PORT)
    log.info("  Log file: %s", LOG_DIR / "seed_pogo.log")
    log.info("=" * 60)

    # 1. Fetch remote hashes
    log.info("\n📋 Fetching remote hashes...")
    remote_hashes = fetch(f"{POGO_API_BASE_URL}/api/hashes.json", CACHE, use_cache=False)
    remote_sha = remote_hashes.get("sha512", {})
    log.info("  ✔ Got hashes for: %s", ", ".join(remote_sha.keys()))

    if args.dry_run:
        from lib.db import get_connection
        conn = get_connection()
        with conn.cursor() as cur:
            stored = get_stored_hashes(cur)
            cur.execute("SELECT count(*) FROM pogo_pokemon")
            pk = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pogo_move")
            mv = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pogo_type")
            tp = cur.fetchone()[0]
        conn.close()

        log.info("\n📋 Dry-run preview:")
        log.info("  Pokemon: %d in DB", pk)
        log.info("  Moves:   %d in DB", mv)
        log.info("  Types:   %d in DB", tp)
        for ep, sha in remote_sha.items():
            status = "✔ same" if stored.get(ep) == sha else "→ changed"
            log.info("  %s: %s", ep, status)
        log.info("\n  (no data will be written)")
        return

    t0 = time.time()

    with safe_transaction() as (conn, cur):
        if args.reset:
            log.info("\n🔴 Resetting all pogo tables...")
            truncate_pogo(cur)
            stored_hashes = {}
        else:
            stored_hashes = get_stored_hashes(cur)

        force = args.force or args.reset

        # 2. Static: types
        types_changed = remote_sha.get("pokedex.json") != stored_hashes.get("pokedex.json")
        if force or types_changed:
            seed_types(cur)
        else:
            log.info("\n⏭ Types: hash unchanged, skipping")

        # 3. Static: pokedex (pokemon + moves)
        pokedex_changed = remote_sha.get("pokedex.json") != stored_hashes.get("pokedex.json")
        if force or pokedex_changed:
            seed_pokedex(cur)
            seed_shadow_purified_moves(cur)
            update_hash(cur, "pokedex.json", remote_sha.get("pokedex.json", ""))
        else:
            log.info("\n⏭ Pokedex: hash unchanged, skipping")

        # 4. Dynamic snapshots
        log.info("\n📸 Checking dynamic snapshots...")
        snapshot_map = {
            "raidboss.json": "pogo_raidboss_snapshot",
            "maxbattles.json": "pogo_maxbattle_snapshot",
            "quests.json": "pogo_quest_snapshot",
        }
        for endpoint, table in snapshot_map.items():
            r_hash = remote_sha.get(endpoint, "")
            s_hash = None if force else stored_hashes.get(endpoint)
            if seed_snapshot(cur, endpoint, table, r_hash, s_hash):
                update_hash(cur, endpoint, r_hash)

        # 5. Mapping
        seed_mapping(cur)

        # 6. CPM from GameMaster
        from lib.cpm import extract_cpm_from_gm, update_gm_hash
        log.info("\n🔢 Seeding CPM from GameMaster...")
        gm_data = fetch(GAMEMASTER_URL, CACHE_GM, use_cache=False, timeout=60)
        cpm_list = extract_cpm_from_gm(gm_data)
        max_level = max(r["level"] for r in cpm_list)
        if args.reset:
            cur.execute("TRUNCATE pogo_cpm RESTART IDENTITY")
        for row in cpm_list:
            cur.execute("""
                INSERT INTO pogo_cpm (level, cpm)
                VALUES (%s, %s)
                ON CONFLICT (level) DO UPDATE SET
                    cpm = EXCLUDED.cpm, updated_at = now()
            """, (row["level"], row["cpm"]))
        update_gm_hash(cur, gm_data)
        log.info("  ✔ %d CPM values upserted (level 1.0–%.1f)", len(cpm_list), max_level)

    # Summary
    elapsed = time.time() - t0
    log.info("\n" + "=" * 60)
    log.info("  📊 Summary Report")
    log.info("=" * 60)
    log.info("  Types:     %d", 18)
    log.info("  Pokemon:   seeded/updated")
    log.info("  Moves:     seeded/updated")
    log.info("  Snapshots: checked")
    log.info("  Mapping:   synced")
    log.info("  CPM:       %d values", len(cpm_list))
    log.info("  ⏱ Elapsed:  %.1f seconds", elapsed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
