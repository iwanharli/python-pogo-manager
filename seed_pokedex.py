"""
seed_pokedex.py — Seed pokedex_species, pokedex_pokemon, pokedex_move, pokedex_pokemon_move
from PokéAPI into PostgreSQL (db_pogo_manager).

Uses async HTTP prefetch for speed (20 concurrent requests).

Usage:
    python seed_pokedex.py --reset      # truncate semua tabel pokedex, seed ulang dari awal
    python seed_pokedex.py --update     # hanya fetch pokemon yang belum ada (default)
    python seed_pokedex.py              # sama dengan --update
    python seed_pokedex.py --dry-run    # preview tanpa eksekusi
    SEED_LIMIT=151 python seed_pokedex.py  # batasi hanya Gen 1
"""

import time
import argparse

from tqdm import tqdm
from psycopg2.extras import Json

from lib.config import (POKEAPI_BASE_URL, SEED_LIMIT, CACHE_DIR,
                         DB_NAME, DB_HOST, DB_PORT, LOG_DIR)
from lib.log import setup_logger
from lib.http import fetch, session
from lib.http_async import prefetch_all
from lib.db import safe_transaction

log = setup_logger("pogo-seeder", "seed.log")

CACHE = CACHE_DIR  # PokéAPI cache at root cache dir


# ── Helpers ──────────────────────────────────────────────────

def api_fetch(url: str) -> dict:
    """Fetch from PokéAPI with cache always enabled."""
    return fetch(url, CACHE, use_cache=True)


def resolve_seed_limit() -> int:
    """Return SEED_LIMIT, or auto-detect total species from PokéAPI if 0."""
    if SEED_LIMIT > 0:
        return SEED_LIMIT
    log.info("  ℹ SEED_LIMIT=0 → auto-detecting total species from PokéAPI...")
    resp = session.get(f"{POKEAPI_BASE_URL}/pokemon-species/?limit=1", timeout=30)
    resp.raise_for_status()
    total = resp.json()["count"]
    log.info("  ℹ Found %d species in PokéAPI", total)
    return total


def prefetch_urls(url_list: list[str], label: str):
    """Async bulk prefetch URLs not yet cached."""
    uncached = [u for u in url_list if not (CACHE / __import__('hashlib').md5(u.encode()).hexdigest()).with_suffix('.json').exists()]
    if not uncached:
        log.info("  ✔ %s: all %d already cached", label, len(url_list))
        return
    log.info("  ↓ %s: fetching %d/%d uncached URLs (async, 20 concurrent)...",
             label, len(uncached), len(url_list))
    prefetch_all(uncached, CACHE, concurrency=20)
    log.info("  ✔ %s: prefetch done", label)


# ── Extract helpers ──────────────────────────────────────────

def extract_names(names_list: list) -> dict:
    out = {}
    for n in names_list:
        lang = n.get("language", {}).get("name", "")
        out[lang] = n.get("name", "")
    return out


def extract_genera(genera_list: list) -> dict:
    out = {}
    for g in genera_list:
        lang = g.get("language", {}).get("name", "")
        out[lang] = g.get("genus", "")
    return out


def extract_flavor_texts(ft_list: list) -> list:
    out = []
    for ft in ft_list:
        out.append({
            "lang": ft.get("language", {}).get("name", ""),
            "version": ft.get("version", {}).get("name", "") if ft.get("version") else None,
            "text": ft.get("flavor_text", "").replace("\n", " ").replace("\f", " "),
        })
    return out


def extract_dex_numbers(pdn_list: list) -> dict:
    out = {}
    for pdn in pdn_list:
        dex_name = pdn.get("pokedex", {}).get("name", "")
        out[dex_name] = pdn.get("entry_number")
    return out


def extract_egg_groups(eg_list: list) -> list:
    return [eg.get("name", "") for eg in eg_list]


def extract_evolution_chain(chain_url: str | None) -> dict | None:
    if not chain_url:
        return None
    chain_data = api_fetch(chain_url)
    chain_id = chain_data.get("id")
    edges = []

    def walk(node, parent_name=None):
        species_name = node.get("species", {}).get("name", "")
        if parent_name:
            for detail in node.get("evolution_details", []):
                edges.append({
                    "from": parent_name,
                    "to": species_name,
                    "trigger": detail.get("trigger", {}).get("name"),
                    "min_level": detail.get("min_level"),
                    "item": detail.get("item", {}).get("name") if detail.get("item") else None,
                })
        for child in node.get("evolves_to", []):
            walk(child, species_name)

    walk(chain_data.get("chain", {}))
    return {"chain_id": chain_id, "edges": edges}


def extract_types(types_list: list) -> list:
    return [{"slot": t["slot"], "name": t["type"]["name"]} for t in types_list]


def extract_stats(stats_list: list) -> list:
    return [
        {"name": s["stat"]["name"], "base_stat": s["base_stat"], "effort": s["effort"]}
        for s in stats_list
    ]


def extract_abilities(abilities_list: list) -> list:
    return [
        {"name": a["ability"]["name"], "slot": a["slot"], "is_hidden": a["is_hidden"]}
        for a in abilities_list
    ]


def extract_sprites(sprites_dict: dict) -> dict:
    out = {}
    for key in ("front_default", "front_shiny", "front_female", "back_default", "back_shiny"):
        if sprites_dict.get(key):
            out[key] = sprites_dict[key]

    other = sprites_dict.get("other", {})
    oa = other.get("official-artwork", {})
    if oa.get("front_default"):
        out["official_artwork"] = oa["front_default"]
    if oa.get("front_shiny"):
        out["official_artwork_shiny"] = oa["front_shiny"]

    home = other.get("home", {})
    if home.get("front_default"):
        out["home"] = home["front_default"]

    dw = other.get("dream_world", {})
    if dw.get("front_default"):
        out["dream_world"] = dw["front_default"]

    return out


# ── Truncate ─────────────────────────────────────────────────

def truncate_pokedex(cur):
    tables = [
        "pokedex_pokemon_move",
        "pokedex_move",
        "pokedex_pokemon",
        "pokedex_species",
    ]
    for t in tables:
        cur.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")
        log.info("  🗑 Truncated %s", t)

    count = 0
    for f in CACHE.glob("*.json"):
        f.unlink()
        count += 1
    log.info("  🗑 Cleared %d cached files from %s", count, CACHE)
    log.info("  ✅ All pokedex tables & cache cleared")


# ── Seed functions ───────────────────────────────────────────

def seed_species(cur, limit: int, skip_existing: bool = False) -> dict:
    """Seed pokedex_species. Returns summary stats."""
    existing = set()
    if skip_existing:
        cur.execute("SELECT pokeapi_species_id FROM pokedex_species")
        existing = {row[0] for row in cur.fetchall()}

    ids_to_seed = [i for i in range(1, limit + 1) if i not in existing]

    # Async prefetch
    if ids_to_seed:
        species_urls = [f"{POKEAPI_BASE_URL}/pokemon-species/{i}" for i in ids_to_seed]
        prefetch_urls(species_urls, "Species")

    log.info("\n🔵 Seeding species (1..%d)...", limit)
    seeded = 0
    for i in tqdm(range(1, limit + 1), desc="Species", unit="sp", disable=None):
        if i in existing:
            continue
        data = api_fetch(f"{POKEAPI_BASE_URL}/pokemon-species/{i}")

        chain_url = data.get("evolution_chain", {}).get("url") if data.get("evolution_chain") else None

        row = {
            "pokeapi_species_id": data["id"],
            "name": data["name"],
            "generation": data.get("generation", {}).get("name") if data.get("generation") else None,
            "color": data.get("color", {}).get("name") if data.get("color") else None,
            "shape": data.get("shape", {}).get("name") if data.get("shape") else None,
            "habitat": data.get("habitat", {}).get("name") if data.get("habitat") else None,
            "growth_rate": data.get("growth_rate", {}).get("name") if data.get("growth_rate") else None,
            "gender_rate": data.get("gender_rate"),
            "capture_rate": data.get("capture_rate"),
            "base_happiness": data.get("base_happiness"),
            "hatch_counter": data.get("hatch_counter"),
            "is_baby": data.get("is_baby", False),
            "is_legendary": data.get("is_legendary", False),
            "is_mythical": data.get("is_mythical", False),
            "has_gender_differences": data.get("has_gender_differences", False),
            "forms_switchable": data.get("forms_switchable", False),
            "names": Json(extract_names(data.get("names", []))),
            "genera": Json(extract_genera(data.get("genera", []))),
            "flavor_texts": Json(extract_flavor_texts(data.get("flavor_text_entries", []))),
            "egg_groups": extract_egg_groups(data.get("egg_groups", [])),
            "dex_numbers": Json(extract_dex_numbers(data.get("pokedex_numbers", []))),
            "evolution_chain": Json(extract_evolution_chain(chain_url)) if chain_url else None,
        }

        cur.execute("""
            INSERT INTO pokedex_species (
                pokeapi_species_id, name, generation, color, shape, habitat, growth_rate,
                gender_rate, capture_rate, base_happiness, hatch_counter,
                is_baby, is_legendary, is_mythical, has_gender_differences, forms_switchable,
                names, genera, flavor_texts, egg_groups, dex_numbers, evolution_chain
            ) VALUES (
                %(pokeapi_species_id)s, %(name)s, %(generation)s, %(color)s, %(shape)s,
                %(habitat)s, %(growth_rate)s,
                %(gender_rate)s, %(capture_rate)s, %(base_happiness)s, %(hatch_counter)s,
                %(is_baby)s, %(is_legendary)s, %(is_mythical)s, %(has_gender_differences)s,
                %(forms_switchable)s,
                %(names)s, %(genera)s, %(flavor_texts)s, %(egg_groups)s,
                %(dex_numbers)s, %(evolution_chain)s
            )
            ON CONFLICT (pokeapi_species_id) DO UPDATE SET
                name = EXCLUDED.name,
                names = EXCLUDED.names,
                genera = EXCLUDED.genera,
                flavor_texts = EXCLUDED.flavor_texts,
                dex_numbers = EXCLUDED.dex_numbers,
                evolution_chain = EXCLUDED.evolution_chain,
                updated_at = now()
        """, row)
        seeded += 1

    return {"seeded": seeded, "skipped": len(existing), "total": limit}


def seed_pokemon(cur, limit: int, skip_existing: bool = False) -> dict:
    """Seed pokedex_pokemon. Returns summary stats."""
    existing = set()
    if skip_existing:
        cur.execute("SELECT pokeapi_pokemon_id FROM pokedex_pokemon")
        existing = {row[0] for row in cur.fetchall()}

    ids_to_seed = [i for i in range(1, limit + 1) if i not in existing]

    # Async prefetch
    if ids_to_seed:
        pokemon_urls = [f"{POKEAPI_BASE_URL}/pokemon/{i}" for i in ids_to_seed]
        prefetch_urls(pokemon_urls, "Pokemon")

    log.info("\n🟢 Seeding pokemon (1..%d)...", limit)
    seeded = 0
    for i in tqdm(range(1, limit + 1), desc="Pokemon", unit="pk", disable=None):
        if i in existing:
            continue
        data = api_fetch(f"{POKEAPI_BASE_URL}/pokemon/{i}")

        cur.execute(
            "SELECT id FROM pokedex_species WHERE name = %s",
            (data["species"]["name"],)
        )
        species_row = cur.fetchone()
        if not species_row:
            log.warning("  ⚠ species not found for %s, skipping", data["name"])
            continue

        row = {
            "pokeapi_pokemon_id": data["id"],
            "species_id": species_row[0],
            "name": data["name"],
            "form_name": None,
            "is_default": data.get("is_default", True),
            "height_dm": data.get("height"),
            "weight_hg": data.get("weight"),
            "base_experience": data.get("base_experience"),
            "pokemon_order": data.get("order"),
            "types": Json(extract_types(data.get("types", []))),
            "stats": Json(extract_stats(data.get("stats", []))),
            "abilities": Json(extract_abilities(data.get("abilities", []))),
            "sprites": Json(extract_sprites(data.get("sprites", {}))),
        }

        cur.execute("""
            INSERT INTO pokedex_pokemon (
                pokeapi_pokemon_id, species_id, name, form_name, is_default,
                height_dm, weight_hg, base_experience, pokemon_order,
                types, stats, abilities, sprites
            ) VALUES (
                %(pokeapi_pokemon_id)s, %(species_id)s, %(name)s, %(form_name)s,
                %(is_default)s, %(height_dm)s, %(weight_hg)s, %(base_experience)s,
                %(pokemon_order)s, %(types)s, %(stats)s, %(abilities)s, %(sprites)s
            )
            ON CONFLICT (pokeapi_pokemon_id) DO UPDATE SET
                types = EXCLUDED.types,
                stats = EXCLUDED.stats,
                abilities = EXCLUDED.abilities,
                sprites = EXCLUDED.sprites,
                updated_at = now()
        """, row)
        seeded += 1

    return {"seeded": seeded, "skipped": len(existing), "total": limit}


def seed_moves(cur, limit: int, skip_existing: bool = False) -> dict:
    """Seed pokedex_move and pokedex_pokemon_move. Returns summary stats."""
    existing_links = set()
    if skip_existing:
        cur.execute("SELECT DISTINCT pokedex_pokemon_id FROM pokedex_pokemon_move")
        existing_pkmn_ids = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT pokeapi_pokemon_id, id FROM pokedex_pokemon")
        for api_id, db_id in cur.fetchall():
            if db_id in existing_pkmn_ids:
                existing_links.add(api_id)

    ids_to_seed = [i for i in range(1, limit + 1) if i not in existing_links]

    # Async prefetch pokemon data (moves loaded from pokemon endpoint)
    if ids_to_seed:
        pokemon_urls = [f"{POKEAPI_BASE_URL}/pokemon/{i}" for i in ids_to_seed]
        prefetch_urls(pokemon_urls, "Pokemon (moves)")

    log.info("\n🟡 Seeding moves for pokemon 1..%d...", limit)

    seen_moves = set()
    move_urls_to_fetch = []

    # First pass: collect all move URLs we need
    for i in ids_to_seed:
        data = api_fetch(f"{POKEAPI_BASE_URL}/pokemon/{i}")
        for move_entry in data.get("moves", []):
            move_url = move_entry["move"]["url"]
            move_id = int(move_url.rstrip("/").split("/")[-1])
            if move_id not in seen_moves:
                seen_moves.add(move_id)
                move_urls_to_fetch.append(move_url)

    # Async prefetch moves
    if move_urls_to_fetch:
        prefetch_urls(move_urls_to_fetch, "Moves")

    # Second pass: insert
    seen_moves.clear()
    total_links = 0
    seeded_pokemon = 0

    for i in tqdm(range(1, limit + 1), desc="Moves", unit="pk", disable=None):
        if i in existing_links:
            continue
        data = api_fetch(f"{POKEAPI_BASE_URL}/pokemon/{i}")
        pokemon_name = data["name"]

        cur.execute(
            "SELECT id FROM pokedex_pokemon WHERE pokeapi_pokemon_id = %s",
            (data["id"],)
        )
        pkmn_row = cur.fetchone()
        if not pkmn_row:
            continue
        pkmn_id = pkmn_row[0]

        for move_entry in data.get("moves", []):
            move_name = move_entry["move"]["name"]
            move_url = move_entry["move"]["url"]
            pokeapi_move_id = int(move_url.rstrip("/").split("/")[-1])

            if pokeapi_move_id not in seen_moves:
                move_data = api_fetch(move_url)
                cur.execute("""
                    INSERT INTO pokedex_move (
                        pokeapi_move_id, name, type, damage_class,
                        power, accuracy, pp, priority, data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pokeapi_move_id) DO NOTHING
                """, (
                    pokeapi_move_id,
                    move_data["name"],
                    move_data.get("type", {}).get("name") if move_data.get("type") else None,
                    move_data.get("damage_class", {}).get("name") if move_data.get("damage_class") else None,
                    move_data.get("power"),
                    move_data.get("accuracy"),
                    move_data.get("pp"),
                    move_data.get("priority"),
                    Json(move_data),
                ))
                seen_moves.add(pokeapi_move_id)

            learnset = []
            for vgd in move_entry.get("version_group_details", []):
                learnset.append({
                    "version_group": vgd.get("version_group", {}).get("name", ""),
                    "learn_method": vgd.get("move_learn_method", {}).get("name", ""),
                    "level": vgd.get("level_learned_at", 0),
                })

            cur.execute("""
                INSERT INTO pokedex_pokemon_move (
                    pokedex_pokemon_id, pokeapi_move_id, learnset
                ) VALUES (%s, %s, %s)
                ON CONFLICT (pokedex_pokemon_id, pokeapi_move_id) DO UPDATE SET
                    learnset = EXCLUDED.learnset
            """, (pkmn_id, pokeapi_move_id, Json(learnset)))
            total_links += 1

        seeded_pokemon += 1

    return {
        "seeded_pokemon": seeded_pokemon,
        "skipped": len(existing_links),
        "moves": len(seen_moves),
        "links": total_links,
        "total": limit,
    }


# ── Summary report ───────────────────────────────────────────

def print_summary(species: dict, pokemon: dict, moves: dict, elapsed: float):
    log.info("\n" + "=" * 60)
    log.info("  📊 Summary Report")
    log.info("=" * 60)
    log.info("  %-20s %8s %8s %8s", "", "Seeded", "Skipped", "Total")
    log.info("  " + "-" * 48)
    log.info("  %-20s %8d %8d %8d", "Species",
             species["seeded"], species["skipped"], species["total"])
    log.info("  %-20s %8d %8d %8d", "Pokemon",
             pokemon["seeded"], pokemon["skipped"], pokemon["total"])
    log.info("  %-20s %8d %8d %8d", "Pokemon (moves)",
             moves["seeded_pokemon"], moves["skipped"], moves["total"])
    log.info("  %-20s %8d %8s %8s", "Unique moves",
             moves["moves"], "", "")
    log.info("  %-20s %8d %8s %8s", "Move links",
             moves["links"], "", "")
    log.info("  " + "-" * 48)
    log.info("  ⏱ Elapsed: %.1f seconds", elapsed)
    log.info("=" * 60)


# ── Main ─────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed Pokédex data from PokéAPI into PostgreSQL.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--reset", action="store_true",
        help="Truncate semua tabel pokedex lalu seed ulang dari awal.",
    )
    group.add_argument(
        "--update", action="store_true", default=True,
        help="Hanya fetch pokemon yang belum ada di DB (default).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview berapa data yang akan di-fetch/skip tanpa eksekusi.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    limit = resolve_seed_limit()
    mode = "dry-run" if args.dry_run else ("reset" if args.reset else "update")

    log.info("=" * 60)
    log.info("  PokéAPI → db_pogo_manager Seeder")
    log.info("  Mode: %s | Limit: %d | DB: %s@%s:%s", mode, limit, DB_NAME, DB_HOST, DB_PORT)
    log.info("  Log file: %s", LOG_DIR / "seed.log")
    log.info("=" * 60)

    if args.dry_run:
        from lib.db import get_connection
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pokedex_species")
            sp = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pokedex_pokemon")
            pk = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pokedex_move")
            mv = cur.fetchone()[0]
        conn.close()
        log.info("\n📋 Dry-run preview:")
        log.info("  Species: %d in DB, %d to seed → %d to fetch", sp, limit, max(0, limit - sp))
        log.info("  Pokemon: %d in DB, %d to seed → %d to fetch", pk, limit, max(0, limit - pk))
        log.info("  Moves:   %d in DB", mv)
        log.info("\n  (no data will be written)")
        return

    skip = not args.reset
    t0 = time.time()

    with safe_transaction() as (conn, cur):
        if args.reset:
            log.info("\n🔴 Resetting all pokedex tables...")
            truncate_pokedex(cur)

        sp = seed_species(cur, limit, skip_existing=skip)
        pk = seed_pokemon(cur, limit, skip_existing=skip)
        mv = seed_moves(cur, limit, skip_existing=skip)

    print_summary(sp, pk, mv, time.time() - t0)


if __name__ == "__main__":
    main()
