"""
seed_tags.py — Seed formula definitions and evaluate auto-tagging rules.

This script manages the pogo_formula table (both scoring and tagging formulas)
and evaluates tagging rules to auto-label user pokémon for roster management.

Usage:
    python seed_tags.py               # seed formulas + evaluate tags
    python seed_tags.py --formulas    # only seed/update formula definitions
    python seed_tags.py --evaluate    # only evaluate tags (skip formula upsert)
    python seed_tags.py --reset       # truncate tags + re-evaluate
"""

import sys
import math
import argparse
import logging
from collections import defaultdict
from psycopg2.extras import Json, execute_batch

from lib.db import safe_transaction
from lib.log import setup_logger

log = setup_logger("tagging", "seed_tags.log")

# ── Default Formulas ──────────────────────────────────────────

DEFAULT_SCORING_FORMULAS = [
    # Move scoring formulas
    {
        "name": "move_pve_fast_v1",
        "kind": "scoring",
        "scope": "move",
        "expression": "w_dps * DPS + w_eps * EPS",
        "variables": {"DPS": "power / duration_s", "EPS": "energy / duration_s"},
        "defaults": {"w_dps": 0.65, "w_eps": 0.35},
        "description": "PVE Fast move scoring — DPS-heavy"
    },
    {
        "name": "move_pve_charged_v1",
        "kind": "scoring",
        "scope": "move",
        "expression": "w_dpe * DPE + w_dps * DPS",
        "variables": {"DPE": "power / abs(energy)", "DPS": "power / duration_s"},
        "defaults": {"w_dpe": 0.75, "w_dps": 0.25},
        "description": "PVE Charged move scoring — DPE-heavy"
    },
    {
        "name": "move_pvp_fast_v1",
        "kind": "scoring",
        "scope": "move",
        "expression": "w_ept * EPT + w_dpt * DPT",
        "variables": {"EPT": "energy / turns", "DPT": "power / turns"},
        "defaults": {"w_ept": 0.60, "w_dpt": 0.40},
        "description": "PVP Fast move scoring — Pressure-heavy"
    },
    {
        "name": "move_pvp_charged_v1",
        "kind": "scoring",
        "scope": "move",
        "expression": "w_dpe * DPE + w_cost * COST_FACTOR",
        "variables": {"DPE": "power / abs(energy)", "COST_FACTOR": "1 / abs(energy)"},
        "defaults": {"w_dpe": 0.80, "w_cost": 0.20},
        "description": "PVP Charged move scoring — Efficiency"
    },
    # Pokemon scoring formulas
    {
        "name": "pokemon_composite_v1",
        "kind": "scoring",
        "scope": "pokemon",
        "expression": "w_moveset * MOVESET + w_stats * STATS",
        "variables": {},
        "defaults": {"w_moveset": 0.65, "w_stats": 0.35},
        "description": "Pokemon composite score — 65% moveset + 35% stats"
    },
    {
        "name": "pokemon_tempo_blend_v1",
        "kind": "scoring",
        "scope": "pokemon",
        "expression": "w_core * core_raw + w_tempo * tempo_raw",
        "variables": {},
        "defaults": {"w_core": 0.70, "w_tempo": 0.30},
        "description": "PvP tempo blending — 70% core + 30% tempo (spam/bait/buff/nuke)"
    },
]

DEFAULT_TAGGING_FORMULAS = [
    # ── Size tags ─────────────────────────────────
    {
        "name": "tag_xxl",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "size == 'xxl'",
        "defaults": {},
        "tag_config": {"tag": "XXL", "color": "#D32F2F", "icon": "⬆️", "priority": 10},
        "description": "XXL size pokémon"
    },
    {
        "name": "tag_xl",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "size == 'xl'",
        "defaults": {},
        "tag_config": {"tag": "XL", "color": "#E64A19", "icon": "🔼", "priority": 11},
        "description": "XL size pokémon"
    },
    {
        "name": "tag_xs",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "size == 'xs'",
        "defaults": {},
        "tag_config": {"tag": "XS", "color": "#1976D2", "icon": "🔽", "priority": 12},
        "description": "XS size pokémon"
    },
    {
        "name": "tag_xxs",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "size == 'xxs'",
        "defaults": {},
        "tag_config": {"tag": "XXS", "color": "#0D47A1", "icon": "⬇️", "priority": 13},
        "description": "XXS size pokémon"
    },
    {
        "name": "tag_contest",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "has_size_tag",
        "defaults": {},
        "tag_config": {"tag": "CONTEST", "color": "#AB47BC", "icon": "🎀", "priority": 9},
        "description": "Pokémon with a special size (XL/XS/XXL/XXS) — contest eligible"
    },

    # ── IV tier tags ──────────────────────────────
    {
        "name": "tag_hundo",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "iv_percent == 100",
        "defaults": {},
        "tag_config": {"tag": "HUNDO", "color": "#FFD700", "icon": "💯", "priority": 1},
        "description": "Perfect IV (15/15/15)"
    },
    {
        "name": "tag_90plus",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "iv_percent >= 90 and iv_percent < 100",
        "defaults": {},
        "tag_config": {"tag": "90+", "color": "#4CAF50", "icon": "🌟", "priority": 2},
        "description": "High IV — 90% to 99%"
    },
    {
        "name": "tag_60_89",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "iv_percent >= 60 and iv_percent < 90 and not is_pvp_iv",
        "defaults": {},
        "tag_config": {"tag": "60-89", "color": "#FF9800", "icon": "📊", "priority": 3},
        "description": "Mid IV — 60% to 89% (non-PvP IVs)"
    },
    {
        "name": "tag_purify_hundo",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "is_shadow and iv_attack >= 13 and iv_defense >= 13 and iv_stamina >= 13",
        "defaults": {},
        "tag_config": {"tag": "PURIFY 💯", "color": "#E91E63", "icon": "✨", "priority": 0},
        "description": "Shadow Pokémon that will become 100% IV if purified"
    },
    {
        "name": "tag_pvp",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "is_pvp_iv",
        "defaults": {},
        "tag_config": {"tag": "PVP", "color": "#9C27B0", "icon": "⚔️", "priority": 4},
        "description": "Good PvP IVs (stat product ≥ 95% of best for Great or Ultra League)"
    },

    # ── Roster management tags ────────────────────
    {
        "name": "tag_dex",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "((iv_percent < 60 and species_count == 1 and not is_pvp_iv) or is_shiny or (is_shadow and is_best_shadow and iv_percent < 45))",
        "defaults": {},
        "tag_config": {"tag": "DEX", "color": "#78909C", "icon": "📖", "priority": 5},
        "description": "Low IV unique, Shiny, or best Shadow representative — keep for dex completion"
    },
    {
        "name": "tag_transfer",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "(not is_shadow and iv_percent <= 80 and species_count > 1 and not is_pvp_iv and not is_iv_pvp and not is_best_of_species and not is_best_dynamax and not has_size_tag and not is_shiny) or (not is_shadow and iv_percent >= 81 and iv_percent <= 89 and range_81_89_rank > 2 and not has_size_tag and not is_shiny) or (is_shadow and iv_percent < 45 and not is_best_shadow and not is_shiny)",
        "defaults": {},
        "tag_config": {"tag": "TRANSFER", "color": "#F44336", "icon": "♻️", "priority": 6},
        "description": "Duplicates safe to transfer: Low IV (<80%), excess mid-high (81-89% rank > 2), or weak Shadows (<45%)"
    },

    # ── Ranking tags ──────────────────────────────
    {
        "name": "tag_top10_pvp",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "pvp_roster_rank <= 10",
        "defaults": {},
        "tag_config": {"tag": "TOP 10 PVP", "color": "#7B1FA2", "icon": "🏆", "priority": 7},
        "description": "Top 10 pokémon in user's roster by PvP score"
    },
    {
        "name": "tag_top10_pve",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "pve_roster_rank <= 10",
        "defaults": {},
        "tag_config": {"tag": "TOP 10 PVE", "color": "#C62828", "icon": "⚔️", "priority": 8},
        "description": "Top 10 pokémon in user's roster by PvE score"
    },
    {
        "name": "tag_iv_pvp",
        "kind": "tagging", "scope": "user_pokemon",
        "expression": "iv_attack < 5 and iv_defense > 10 and iv_stamina > 10",
        "defaults": {},
        "tag_config": {"tag": "IV PVP", "color": "#2196F3", "icon": "💎", "priority": 4},
        "description": "PVP oriented IVs (Low Atk, High Def, High Sta)"
    },
]


# ── Safe Expression Evaluator ─────────────────────────────────

SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "round": round,
    "True": True, "False": False, "None": None,
    "math": math,
}

def safe_eval(expression: str, variables: dict) -> bool:
    """Evaluate a Python expression in a restricted namespace."""
    try:
        namespace = {"__builtins__": {}}
        namespace.update(SAFE_BUILTINS)
        namespace.update(variables)
        result = eval(expression, namespace)
        return bool(result)
    except Exception as e:
        log.debug(f"  Expression eval failed: {expression} → {e}")
        return False


# ── Formula Seeder ────────────────────────────────────────────

def seed_formulas(cur):
    """Upsert all default formulas into pogo_formula."""
    log.info("🧪 Seeding formula definitions...")

    all_formulas = DEFAULT_SCORING_FORMULAS + DEFAULT_TAGGING_FORMULAS
    count = 0
    for f in all_formulas:
        cur.execute("""
            INSERT INTO pogo_formula (name, kind, scope, expression, variables, defaults, tag_config, description, is_active, version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
            ON CONFLICT (name, version) DO UPDATE SET
                kind = EXCLUDED.kind,
                scope = EXCLUDED.scope,
                expression = EXCLUDED.expression,
                variables = EXCLUDED.variables,
                defaults = EXCLUDED.defaults,
                tag_config = EXCLUDED.tag_config,
                description = EXCLUDED.description,
                is_active = EXCLUDED.is_active,
                updated_at = now()
        """, (
            f["name"], f["kind"], f["scope"], f["expression"],
            Json(f.get("variables", {})), Json(f.get("defaults", {})),
            Json(f.get("tag_config")) if f.get("tag_config") else None,
            f.get("description"),
            f.get("version", "v1"),
        ))
        count += 1

    log.info(f"  ✔ {count} formulas upserted")
    return count


# ── User Pokemon Tagging (Roster Management) ─────────────────

def evaluate_user_pokemon_tags(cur):
    """
    Evaluate tagging formulas against user pokemon.
    
    Computes rich context per pokemon:
    - IV tier (hundo/90+/60-89)
    - PvP IV goodness (stat product ratio vs species best)
    - Species count in roster (for DEX/TRANSFER logic)
    - Size from extra JSONB (for XL/XS/XXL/XXS/CONTEST)
    - PvP/PvE roster ranking (for TOP 10)
    - Elite move detection
    """
    log.info("🏷️  Evaluating user pokemon tags...")

    # Fetch active tagging formulas for user_pokemon scope
    cur.execute("""
        SELECT id, name, expression, defaults, tag_config
        FROM pogo_formula
        WHERE kind = 'tagging' AND scope = 'user_pokemon' AND is_active = TRUE
        ORDER BY (tag_config->>'priority')::int ASC NULLS LAST
    """)
    formulas = cur.fetchall()
    if not formulas:
        log.info("  ⚠ No active user_pokemon tagging formulas found")
        return 0

    # ── 1. Fetch all user pokemon with species mapping ──
    cur.execute("""
        SELECT 
            up.id, up.pokedex_pokemon_id, up.pogo_pokemon_id,
            up.iv_attack, up.iv_defense, up.iv_stamina,
            up.cp, up.level, up.shiny, up.extra,
            up.fast_move, up.charged_move_1, up.charged_move_2,
            COALESCE(up.pogo_pokemon_id, m.pogo_pokemon_id) as effective_pogo_id,
            gp.stats as base_stats
        FROM user_pokemon up
        JOIN pokedex_pokemon_go_map m ON m.pokedex_pokemon_id = up.pokedex_pokemon_id
        JOIN pogo_pokemon gp ON gp.id = COALESCE(up.pogo_pokemon_id, m.pogo_pokemon_id)
    """)
    columns = [desc[0] for desc in cur.description]
    all_user_pokemon = [dict(zip(columns, row)) for row in cur.fetchall()]

    if not all_user_pokemon:
        log.info("  ⚠ No user pokemon found")
        return 0

    log.info(f"  📋 {len(all_user_pokemon)} user pokémon to evaluate")

    # ── 2. Species count (how many of each species does user have?) ──
    species_count = defaultdict(int)
    species_pokemon = defaultdict(list)  # species -> list of user pokemon
    for up in all_user_pokemon:
        sid = up["effective_pogo_id"]
        species_count[sid] += 1
        species_pokemon[sid].append(up)
    
    # ── 3. Best IV per species (to know which is the "best" of duplicates) ──
    species_best_iv = {}
    species_best_shadow_iv = {}
    for sid, pokes in species_pokemon.items():
        best_iv = -1
        best_id = None
        best_shadow_iv = -1
        best_shadow_id = None
        for p in pokes:
            iv_sum = (p.get("iv_attack") or 0) + (p.get("iv_defense") or 0) + (p.get("iv_stamina") or 0)
            if iv_sum > best_iv:
                best_iv = iv_sum
                best_id = p["id"]
            
            # Check for best shadow
            extra = p.get("extra") or {}
            if extra.get("is_shadow"):
                if iv_sum > best_shadow_iv:
                    best_shadow_iv = iv_sum
                    best_shadow_id = p["id"]

        species_best_iv[sid] = best_id
        species_best_shadow_iv[sid] = best_shadow_id

    # ── 3b. Best Dynamax per species ──
    species_best_dynamax_iv = {}
    for sid, pokes in species_pokemon.items():
        best_dynamax_id = None
        best_dynamax_iv = -1
        for p in pokes:
            extra = p.get("extra") or {}
            if extra.get("is_dynamax"):
                iv_sum = (p.get("iv_attack") or 0) + (p.get("iv_defense") or 0) + (p.get("iv_stamina") or 0)
                if iv_sum > best_dynamax_iv:
                    best_dynamax_iv = iv_sum
                    best_dynamax_id = p["id"]
        species_best_dynamax_iv[sid] = best_dynamax_id

    # ── 3b. Rank Normal Pokemon in 81-89% IV range ──
    range_81_89_ranks = {} # pokemon_id -> rank
    for sid, pokes in species_pokemon.items():
        # filter normal pokes in 81-89 range
        mid_high_pokes = []
        for p in pokes:
            extra = p.get("extra") or {}
            iv_total = (p.get("iv_attack") or 0) + (p.get("iv_defense") or 0) + (p.get("iv_stamina") or 0)
            iv_pct = round(iv_total / 45 * 100, 2)
            if not extra.get("is_shadow") and 81 <= iv_pct <= 89:
                mid_high_pokes.append(p)
        
        # Sort by IV desc, then id (stable)
        mid_high_pokes.sort(key=lambda x: ((x.get("iv_attack") or 0) + (x.get("iv_defense") or 0) + (x.get("iv_stamina") or 0)), reverse=True)
        for i, p in enumerate(mid_high_pokes):
            range_81_89_ranks[p["id"]] = i + 1

    # ── 4. PvP IV check (stat product ratio) ──
    cur.execute("SELECT level, cpm FROM pogo_cpm")
    cpm_map = {float(r[0]): float(r[1]) for r in cur.fetchall()}

    # Fetch best stat products per pokemon (Great + Ultra)
    cur.execute("""
        SELECT pokemon_id, league, stat_product
        FROM pogo_pvp_iv_rank
        WHERE rank = 1 AND league IN ('pvp_great', 'pvp_ultra')
    """)
    best_sp_map = {}  # {pokemon_id: {league: sp}}
    for pid, league, sp in cur.fetchall():
        if pid not in best_sp_map:
            best_sp_map[pid] = {}
        best_sp_map[pid][league] = float(sp)

    # ── 5. Scoring data for roster ranking ──
    # PvP: best score across GL/UL/ML
    cur.execute("""
        SELECT s.pokemon_id, MAX(s.score_0_100) as best_score
        FROM pogo_scoring_pokemon s
        JOIN pogo_scoring_pokemon_profile sp ON sp.id = s.profile_id
        WHERE sp.context IN ('pvp_great', 'pvp_ultra', 'pvp_master') AND sp.is_active = TRUE
        GROUP BY s.pokemon_id
    """)
    pvp_scores = {pid: float(score) for pid, score in cur.fetchall()}

    # PvE: pve_raid score
    cur.execute("""
        SELECT s.pokemon_id, s.score_0_100
        FROM pogo_scoring_pokemon s
        JOIN pogo_scoring_pokemon_profile sp ON sp.id = s.profile_id
        WHERE sp.context = 'pve_raid' AND sp.is_active = TRUE
    """)
    pve_scores = {pid: float(score) for pid, score in cur.fetchall()}

    # Rank user pokemon by PvP and PvE scores
    # CRITICAL: We only want unique species in the Top 10.
    # So we take only the best-of-species individuals for the ranking pool.
    best_representatives = []
    for sid, pokes in species_pokemon.items():
        if not pokes:
            continue
        # Pick the one with highest IV sum as the species representative
        best_rep = max(pokes, key=lambda x: (x.get("iv_attack") or 0) + (x.get("iv_defense") or 0) + (x.get("iv_stamina") or 0))
        best_representatives.append(best_rep)

    pvp_ranked_pool = sorted(best_representatives, key=lambda p: pvp_scores.get(p["effective_pogo_id"], 0), reverse=True)
    pve_ranked_pool = sorted(best_representatives, key=lambda p: pve_scores.get(p["effective_pogo_id"], 0), reverse=True)

    pvp_rank_map = {p["id"]: rank + 1 for rank, p in enumerate(pvp_ranked_pool)}
    pve_rank_map = {p["id"]: rank + 1 for rank, p in enumerate(pve_ranked_pool)}

    # ── 6. Elite move IDs ──
    cur.execute("""
        SELECT DISTINCT move_id FROM pogo_pokemon_move
        WHERE move_kind IN ('elite_quick', 'elite_cinematic')
    """)
    elite_move_ids = {row[0] for row in cur.fetchall()}

    # ── 7. Evaluate each pokemon ──
    PVP_SP_THRESHOLD = 0.98  # ≥ 98% stat product = elite PvP IVs

    batch_data = []
    tag_counts = {}

    for up in all_user_pokemon:
        iv_a = up.get("iv_attack") or 0
        iv_d = up.get("iv_defense") or 0
        iv_s = up.get("iv_stamina") or 0
        iv_total = iv_a + iv_d + iv_s
        iv_percent = round(iv_total / 45 * 100, 2)
        extra = up.get("extra") or {}
        sid = up["effective_pogo_id"]

        # Size from extra JSONB
        size = (extra.get("size") or "").lower()
        has_size_tag = size in ("xl", "xs", "xxl", "xxs")

        # PvP IV check: is this a good PvP IV spread?
        is_pvp_iv = False
        level = float(up["level"]) if up.get("level") else None
        base_stats = up.get("base_stats") or {}

        if base_stats and sid in best_sp_map:
            base_atk = float(base_stats.get("attack", 0))
            base_def = float(base_stats.get("defense", 0))
            base_sta = float(base_stats.get("stamina", 0))

            for league, best_sp in best_sp_map.get(sid, {}).items():
                if best_sp <= 0:
                    continue
                # Check all possible levels to find if these IVs reach ≥95% SP
                cap = 1500 if league == "pvp_great" else 2500
                for lvl in sorted(cpm_map.keys(), reverse=True):
                    cpm = cpm_map[lvl]
                    atk_eff = (base_atk + iv_a) * cpm
                    def_eff = (base_def + iv_d) * cpm
                    sta_eff = math.floor((base_sta + iv_s) * cpm)
                    cp = max(10, math.floor(atk_eff * math.sqrt(def_eff) * math.sqrt(sta_eff) / 10.0))
                    if cp <= cap:
                        user_sp = atk_eff * def_eff * sta_eff
                        sp_ratio = user_sp / best_sp
                        if sp_ratio >= PVP_SP_THRESHOLD:
                            is_pvp_iv = True
                        break
                if is_pvp_iv:
                    break

        # IV PVP check (Diamond tag)
        is_iv_pvp = (iv_a < 5 and iv_d > 10 and iv_s > 10)

        # Elite move check
        has_elite_move = (
            up.get("fast_move") in elite_move_ids or
            up.get("charged_move_1") in elite_move_ids or
            up.get("charged_move_2") in elite_move_ids
        )

        # Is this the best of its species (highest IV)?
        is_best_of_species = (species_best_iv.get(sid) == up["id"])
        is_best_shadow = (species_best_shadow_iv.get(sid) == up["id"])

        # Build context variables for expression eval
        ctx_vars = {
            "iv_attack": iv_a,
            "iv_defense": iv_d,
            "iv_stamina": iv_s,
            "iv_total": iv_total,
            "iv_percent": iv_percent,
            "cp": up.get("cp") or 0,
            "level": level or 0,
            "size": size,
            "has_size_tag": has_size_tag,
            "is_shiny": up.get("shiny", False),
            "is_shadow": extra.get("is_shadow", False),
            "is_purified": extra.get("is_purified", False),
            "is_dynamax": extra.get("is_dynamax", False),
            "has_elite_move": has_elite_move,
            "is_pvp_iv": is_pvp_iv,
            "is_iv_pvp": is_iv_pvp,
            "species_count": species_count[sid],
            "is_best_of_species": is_best_of_species,
            "is_best_shadow": is_best_shadow,
            "is_best_dynamax": up["id"] == species_best_dynamax_iv.get(sid),
            "range_81_89_rank": range_81_89_ranks.get(up["id"], 9999),
            "pvp_roster_rank": pvp_rank_map.get(up["id"], 9999),
            "pve_roster_rank": pve_rank_map.get(up["id"], 9999),
            "pvp_score": pvp_scores.get(sid, 0),
            "pve_score": pve_scores.get(sid, 0),
        }

        for fid, fname, expr, defaults, tag_config in formulas:
            eval_vars = {**ctx_vars, **(defaults or {})}
            tag_name = (tag_config or {}).get("tag", fname)

            if safe_eval(expr, eval_vars):
                # Confidence based on tag type
                confidence = None
                if "iv_percent" in expr:
                    confidence = iv_percent
                elif "pvp_roster_rank" in expr or "pve_roster_rank" in expr:
                    rank = ctx_vars.get("pvp_roster_rank", 0) if "pvp" in fname else ctx_vars.get("pve_roster_rank", 0)
                    confidence = round(max(0, 100 - (rank - 1) * 10), 2)

                batch_data.append((
                    fid, up["id"], tag_name,
                    None, confidence,
                    Json({"expression": expr, "matched": True}),
                ))
                tag_counts[tag_name] = tag_counts.get(tag_name, 0) + 1

    # Clear old tags and insert new
    cur.execute("TRUNCATE TABLE pogo_user_pokemon_tag CASCADE")
    if batch_data:
        execute_batch(cur, """
            INSERT INTO pogo_user_pokemon_tag (formula_id, user_pokemon_id, tag, context, confidence, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (formula_id, user_pokemon_id, tag) DO UPDATE SET
                context = EXCLUDED.context,
                confidence = EXCLUDED.confidence,
                metadata = EXCLUDED.metadata,
                computed_at = now()
        """, batch_data)

    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
        log.info(f"  {tag}: {count}")

    log.info(f"  ✔ {len(batch_data)} user pokemon tags assigned")
    return len(batch_data)


# ── Main ──────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Seed formula definitions and evaluate auto-tags.")
    parser.add_argument("--formulas", action="store_true", help="Only seed/update formula definitions")
    parser.add_argument("--evaluate", action="store_true", help="Only evaluate tags (skip formula upsert)")
    parser.add_argument("--reset", action="store_true", help="Truncate tags and re-evaluate")
    return parser.parse_args()


def main():
    args = parse_args()
    do_formulas = not args.evaluate  # seed formulas unless --evaluate only
    do_evaluate = not args.formulas  # evaluate tags unless --formulas only

    with safe_transaction() as (conn, cur):
        if args.reset:
            log.info("🔄 Resetting tags...")
            cur.execute("TRUNCATE TABLE pogo_pokemon_tag CASCADE")
            cur.execute("TRUNCATE TABLE pogo_user_pokemon_tag CASCADE")

        if do_formulas:
            seed_formulas(cur)

        if do_evaluate:
            evaluate_user_pokemon_tags(cur)

    log.info("🏁 Tagging complete!")


if __name__ == "__main__":
    main()
