import sys
import logging
import itertools
import math
from decimal import Decimal
from psycopg2.extras import Json, execute_batch

from lib.db import safe_transaction
from lib.log import setup_logger

log = setup_logger("pokemon-scoring", "seed_pokemon_scoring.log")

# ── Fallback defaults (used if pogo_formula table is empty) ───
_FALLBACK_WEIGHTS = {
    "w_moveset": 0.65,
    "w_stats": 0.35,
    "w_core": 0.70,
    "w_tempo": 0.30,
}

# League Caps for IV Rank approximations
CAP_GREAT = 1500
CAP_ULTRA = 2500

# Shadow Pokémon multipliers
SHADOW_ATK_MULT = 1.2
SHADOW_DEF_MULT = 0.8


def load_formula_weights(cur):
    """Load scoring weights from pogo_formula table, fallback to hardcoded defaults."""
    weights = dict(_FALLBACK_WEIGHTS)

    cur.execute("""
        SELECT name, defaults FROM pogo_formula
        WHERE kind = 'scoring' AND scope = 'pokemon' AND is_active = TRUE
        ORDER BY name
    """)
    for name, defaults in cur.fetchall():
        if defaults and isinstance(defaults, dict):
            weights.update(defaults)
            log.info(f"  📐 Loaded formula '{name}': {defaults}")

    log.info(f"  📐 Final weights: w_moveset={weights['w_moveset']}, w_stats={weights['w_stats']}, "
             f"w_core={weights['w_core']}, w_tempo={weights['w_tempo']}")
    return weights

def build_default_profiles(weights):
    """Build profile list using weights from formula table."""
    WEIGHT_MOVESET = weights["w_moveset"]
    WEIGHT_STATS = weights["w_stats"]
    return [
    {
        "name": "pokemon_v1",
        "context": "pve_raid",
        "version": "v1",
        "weights": {"w_moveset": WEIGHT_MOVESET, "w_stats": WEIGHT_STATS, "stat_bias": "attack"},
        "notes": "PVE Raid performance, biased towards attack and cycle DPS."
    },
    {
        "name": "pokemon_v1",
        "context": "pve_gym_def",
        "version": "v1",
        "weights": {"w_moveset": WEIGHT_MOVESET, "w_stats": WEIGHT_STATS, "stat_bias": "bulk"},
        "notes": "PVE Gym Defense performance, biased towards bulk (Defense/Stamina) and overall moveset coverage."
    },
    {
        "name": "pokemon_v1",
        "context": "pvp_great",
        "version": "v1",
        "weights": {"w_moveset": WEIGHT_MOVESET, "w_stats": WEIGHT_STATS, "stat_bias": "bulk", "cap": CAP_GREAT},
        "notes": "Great League PVP performance, focusing on bulk and move pressure."
    },
    {
        "name": "pokemon_v1",
        "context": "pvp_ultra",
        "version": "v1",
        "weights": {"w_moveset": WEIGHT_MOVESET, "w_stats": WEIGHT_STATS, "stat_bias": "bulk", "cap": CAP_ULTRA},
        "notes": "Ultra League PVP performance, focusing on bulk and move pressure."
    },
    {
        "name": "pokemon_v1",
        "context": "pvp_master",
        "version": "v1",
        "weights": {"w_moveset": WEIGHT_MOVESET, "w_stats": WEIGHT_STATS, "stat_bias": "bulk", "cap": None},
        "notes": "Master League PVP performance, max stats and move pressure."
    },
    {
        "name": "pokemon_v1",
        "context": "pve_raid_shadow",
        "version": "v1",
        "weights": {"w_moveset": WEIGHT_MOVESET, "w_stats": WEIGHT_STATS, "stat_bias": "attack", "shadow": True},
        "notes": "PVE Raid Shadow performance. Same moveset as pve_raid but with Shadow ATK×1.2 / DEF×0.8."
    }
]

def init_pokemon_profiles(cur, formula_weights):
    log.info("🛠 Initializing pokemon scoring profiles...")
    profiles = build_default_profiles(formula_weights)
    for p in profiles:
        cur.execute("""
            INSERT INTO pogo_scoring_pokemon_profile (name, context, version, weights, notes, is_active)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (name, context, version) DO UPDATE SET
                weights = EXCLUDED.weights,
                is_active = EXCLUDED.is_active,
                updated_at = now()
        """, (p["name"], p["context"], p["version"], Json(p["weights"]), p["notes"], True))
    
    cur.execute("SELECT id, context, weights FROM pogo_scoring_pokemon_profile WHERE is_active = true")
    return {row[1]: {"id": row[0], "weights": row[2]} for row in cur.fetchall()}

def get_max_stats(cur):
    cur.execute("""
        SELECT 
            MAX((stats->>'attack')::numeric),
            MAX((stats->>'defense')::numeric * (stats->>'stamina')::numeric)
        FROM pogo_pokemon
    """)
    row = cur.fetchone()
    return {"max_attack": float(row[0] or 1), "max_bulk": float(row[1] or 1)}

def get_cpm_map(cur):
    log.info("📈 Fetching CPM Map...")
    cur.execute("SELECT level, cpm FROM pogo_cpm")
    return {float(r[0]): float(r[1]) for r in cur.fetchall()}

def find_best_pvp_stat_product(base_atk, base_def, base_sta, cpm_map, cap_cp=None):
    best_sp = 0.0
    best_cp = 0
    best_stats = {}
    
    levels = sorted(cpm_map.keys(), reverse=True)
    floor = math.floor
    sqrt = math.sqrt
    
    if cap_cp is None:
        cpm = cpm_map.get(50.0, 1.0)
        atk_eff = (base_atk + 15) * cpm
        def_eff = (base_def + 15) * cpm
        sta_eff = floor((base_sta + 15) * cpm)
        cp = max(10, floor(atk_eff * sqrt(def_eff) * sqrt(sta_eff) / 10.0))
        sp = atk_eff * def_eff * sta_eff
        return sp, cp, {"level": 50.0, "iv_a": 15, "iv_d": 15, "iv_s": 15, "atk_eff": atk_eff, "def_eff": def_eff, "sta_eff": sta_eff}
    
    # Great / Ultra League (Capped)
    for iv_a in range(0, 16):
        ba = base_atk + iv_a
        for iv_d in range(0, 16):
            bd = base_def + iv_d
            for iv_s in range(0, 16):
                bs = base_sta + iv_s
                
                for lvl in levels:
                    cpm = cpm_map[lvl]
                    atk_eff = ba * cpm
                    def_eff = bd * cpm
                    sta_eff = floor(bs * cpm)
                    cp = max(10, floor(atk_eff * sqrt(def_eff) * sqrt(sta_eff) / 10.0))
                    
                    if cp <= cap_cp:
                        sp = atk_eff * def_eff * sta_eff
                        if sp > best_sp:
                            best_sp = sp
                            best_cp = cp
                            best_stats = {
                                "level": lvl, "iv_a": iv_a, "iv_d": iv_d, "iv_s": iv_s,
                                "atk_eff": round(atk_eff, 2), "def_eff": round(def_eff, 2), "sta_eff": sta_eff
                            }
                        break
                        
    return best_sp, best_cp, best_stats

def compute_tempo(fast_combat, c1_combat, c2_combat, c1_metrics, c2_metrics):
    """
    Calculate tempo score for a PvP moveset (Fast + Charged1 + Charged2).
    Returns (tempo_raw, tempo_breakdown_dict).
    
    Components:
    A) Spam pressure — how fast can we reach the cheapest charged move?
    B) Bait potential — cheap + nuke combo?
    C) Buff/Debuff — does any charged move have buffs?
    D) Nuke capability — high DPE finisher?
    """
    fast_energy = fast_combat.get('energy', 0)
    fast_turns = fast_combat.get('turns', 1) or 1
    
    # Guard: invalid energy
    if fast_energy <= 0:
        return 0, {"error": "fast_energy <= 0"}
    
    fast_ept = fast_energy / fast_turns
    
    c1_cost = abs(c1_combat.get('energy', 0))
    c2_cost = abs(c2_combat.get('energy', 0)) if c2_combat else 9999
    c1_power = c1_combat.get('power', 0)
    c2_power = c2_combat.get('power', 0) if c2_combat else 0
    c1_dpe = (c1_metrics or {}).get('dpe', 0) if c1_metrics else (c1_power / c1_cost if c1_cost > 0 else 0)
    c2_dpe = (c2_metrics or {}).get('dpe', 0) if c2_metrics else (c2_power / c2_cost if c2_cost > 0 else 0)
    
    # Guard: invalid costs
    if c1_cost <= 0:
        return 0, {"error": "c1_cost <= 0"}
    
    # Turns-to-charged (per the user's corrected formula)
    def ttc(cost):
        if cost <= 0 or fast_energy <= 0:
            return 9999
        fast_needed = math.ceil(cost / fast_energy)
        return fast_needed * fast_turns
    
    ttc1 = ttc(c1_cost)
    ttc2 = ttc(c2_cost) if c2_combat else 9999
    
    # A) Spam pressure: faster = better
    t_fast = min(ttc1, ttc2)
    spam_raw = 1.0 / (t_fast + 1)
    
    # B) Bait potential: cheap + nuke combo
    cheap = min(c1_cost, c2_cost) if c2_combat else c1_cost
    expensive = max(c1_cost, c2_cost) if c2_combat else c1_cost
    nuke_dpe = c1_dpe if c1_cost >= c2_cost else c2_dpe
    if c2_combat is None:
        nuke_dpe = c1_dpe
    
    bait_raw = 0.0
    if c2_combat and cheap <= 40 and expensive >= 55 and nuke_dpe > 0:
        bait_raw = nuke_dpe / max(cheap / 10.0, 0.1)
    
    # C) Buff/Debuff pressure
    buff_raw = 0.0
    for cm in [c1_combat, c2_combat]:
        if cm is None:
            continue
        buffs = cm.get('buffs')
        if buffs and isinstance(buffs, dict):
            chance = buffs.get('chance', 0) or 0
            # Attacker buffs (positive = good)
            atk_stage = buffs.get('attacker_attack_stage', 0) or 0
            def_stage = buffs.get('attacker_defense_stage', 0) or 0
            # Target debuffs (negative on target = good for attacker)
            t_atk_stage = buffs.get('target_attack_stage', 0) or 0
            t_def_stage = buffs.get('target_defense_stage', 0) or 0
            
            # Positive contributions: own atk/def buff, target def debuff
            # Negative: own atk debuff (risky)
            weighted = (
                max(atk_stage, 0) * 1.0 +     # Self atk buff
                max(def_stage, 0) * 0.8 +     # Self def buff
                abs(min(t_def_stage, 0)) * 1.0 + # Target def debuff
                abs(min(t_atk_stage, 0)) * 0.5 + # Target atk debuff
                min(atk_stage, 0) * 0.3       # Self atk debuff (penalty, negative)
            )
            buff_raw += chance * weighted
    
    # D) Nuke capability (finisher)
    best_nuke_dpe = max(c1_dpe, c2_dpe) if c2_combat else c1_dpe
    best_nuke_cost = c1_cost if c1_dpe >= c2_dpe else c2_cost
    nuke_raw = best_nuke_dpe * math.log1p(best_nuke_cost) if best_nuke_cost > 0 else 0
    
    # Combine components (weighted sum)
    # Spam is the most important, then bait, then nuke, then buff
    tempo_raw = (
        spam_raw * 40.0 +
        bait_raw * 3.0 +
        buff_raw * 8.0 +
        nuke_raw * 0.5
    )
    
    breakdown = {
        "fast_ept": round(fast_ept, 2),
        "fast_turns": fast_turns,
        "turns_to_c1": ttc1,
        "turns_to_c2": ttc2 if c2_combat else None,
        "seconds_to_c1": round(ttc1 * 0.5, 1),
        "seconds_to_c2": round(ttc2 * 0.5, 1) if c2_combat else None,
        "cheap_cost": cheap,
        "expensive_cost": expensive if c2_combat else None,
        "spam_raw": round(spam_raw, 4),
        "bait_raw": round(bait_raw, 4),
        "buff_raw": round(buff_raw, 4),
        "nuke_raw": round(nuke_raw, 4),
    }
    
    return tempo_raw, breakdown


def build_moveset_recommendations(cur, profiles_map, w_core=0.70, w_tempo=0.30):
    """
    Evaluate all Fast + Charged move combos for each Pokemon.
    Save top moveset into `pogo_scoring_moveset`.
    Includes Tempo Score for PvP profiles.
    """
    log.info("⚔️ Building Moveset Recommendations for all profiles...")
    
    # 1. Fetch move scores indexed by context -> move_id
    cur.execute("""
        SELECT sp.context, sm.move_id, sm.score_0_100, sm.metrics
        FROM pogo_scoring_move sm
        JOIN pogo_scoring_profile sp ON sm.profile_id = sp.id
        WHERE sp.is_active = true
    """)
    move_scores = {'pve_fast': {}, 'pve_charged': {}, 'pvp_fast': {}, 'pvp_charged': {}}
    for ctx, move_id, score, metrics in cur.fetchall():
        if ctx in move_scores:
            move_scores[ctx][move_id] = {"score": float(score), "metrics": metrics}

    # 2. Fetch Pokemon Move Pools
    cur.execute("""
        SELECT pogo_pokemon_id, move_id, move_kind 
        FROM pogo_pokemon_move
    """)
    pools = {}
    for pid, mid, kind in cur.fetchall():
        if pid not in pools:
            pools[pid] = {'fast': [], 'charged': []}
        if 'quick' in kind:
            pools[pid]['fast'].append((mid, 'elite' in kind))
        elif 'cinematic' in kind:
            pools[pid]['charged'].append((mid, 'elite' in kind))

    # 3. Fetch raw move combat data (energy, turns, buffs, power) for tempo calculation
    cur.execute("SELECT move_id, combat FROM pogo_move WHERE combat IS NOT NULL")
    move_combat = {}
    for mid, combat in cur.fetchall():
        move_combat[mid] = combat or {}

    # Helper function to compute raw core scores
    def compute_moveset(ctx, f_mid, c1_mid, c2_mid=None):
        if 'pve' in ctx:
            fs = move_scores['pve_fast'].get(f_mid, {"score": 0})["score"]
            cs = move_scores['pve_charged'].get(c1_mid, {"score": 0})["score"]
            return (fs + cs) / 2.0
        else: # pvp
            fs = move_scores['pvp_fast'].get(f_mid, {"score": 0})["score"]
            cs1 = move_scores['pvp_charged'].get(c1_mid, {"score": 0})["score"]
            cs2 = move_scores['pvp_charged'].get(c2_mid, {"score": 0})["score"] if c2_mid else 0
            # A simple average proxy for PVP moveset synergy
            return (fs + max(cs1, cs2)*0.7 + min(cs1, cs2)*0.3) / 2.0

    batch_data = []
    best_movesets = {pid: {} for pid in pools.keys()} # Maps pokemon_id -> context -> best score
    
    for pid, pool in pools.items():
        fasts = pool['fast']
        chargeds = pool['charged']
        if not fasts or not chargeds:
            continue
            
        for ctx_p, prof in profiles_map.items():
            is_pvp = 'pvp' in ctx_p
            results = []
            if not is_pvp:
                for (f_id, f_elite), (c_id, c_elite) in itertools.product(fasts, chargeds):
                    core_raw = compute_moveset(ctx_p, f_id, c_id)
                    results.append({
                        "f": f_id, "c1": c_id, "c2": None,
                        "core_raw": core_raw, "raw": core_raw,
                        "tempo_raw": None, "tempo_breakdown": None,
                        "elite": f_elite or c_elite,
                        "metrics": {"fast_score": move_scores['pve_fast'].get(f_id,{}).get("score",0),
                                    "charged1_score": move_scores['pve_charged'].get(c_id,{}).get("score",0)}
                    })
            else: # PVP uses 2 charged moves ideally
                for f_id, f_elite in fasts:
                    fc = move_combat.get(f_id, {})
                    if len(chargeds) == 1:
                        c_id, c_elite = chargeds[0]
                        core_raw = compute_moveset(ctx_p, f_id, c_id)
                        cc1 = move_combat.get(c_id, {})
                        c1m = move_scores['pvp_charged'].get(c_id, {}).get("metrics")
                        t_raw, t_bd = compute_tempo(fc, cc1, None, c1m, None)
                        blended_raw = w_core * core_raw + w_tempo * t_raw
                        metrics = {
                            "fast_score": move_scores['pvp_fast'].get(f_id,{}).get("score",0),
                            "charged1_score": move_scores['pvp_charged'].get(c_id,{}).get("score",0),
                            "tempo": t_bd
                        }
                        results.append({"f": f_id, "c1": c_id, "c2": None,
                                        "core_raw": core_raw, "tempo_raw": t_raw, "raw": blended_raw,
                                        "tempo_breakdown": t_bd, "elite": f_elite or c_elite,
                                        "metrics": metrics})
                    else:
                        for (c1_id, c1_elite), (c2_id, c2_elite) in itertools.combinations(chargeds, 2):
                            core_raw = compute_moveset(ctx_p, f_id, c1_id, c2_id)
                            cc1 = move_combat.get(c1_id, {})
                            cc2 = move_combat.get(c2_id, {})
                            c1m = move_scores['pvp_charged'].get(c1_id, {}).get("metrics")
                            c2m = move_scores['pvp_charged'].get(c2_id, {}).get("metrics")
                            t_raw, t_bd = compute_tempo(fc, cc1, cc2, c1m, c2m)
                            blended_raw = w_core * core_raw + w_tempo * t_raw
                            metrics = {
                                "fast_score": move_scores['pvp_fast'].get(f_id,{}).get("score",0),
                                "charged1_score": move_scores['pvp_charged'].get(c1_id,{}).get("score",0),
                                "charged2_score": move_scores['pvp_charged'].get(c2_id,{}).get("score",0),
                                "tempo": t_bd
                            }
                            results.append({"f": f_id, "c1": c1_id, "c2": c2_id,
                                            "core_raw": core_raw, "tempo_raw": t_raw, "raw": blended_raw,
                                            "tempo_breakdown": t_bd, "elite": f_elite or c1_elite or c2_elite,
                                            "metrics": metrics})

            if results:
                # Sort descending by blended raw
                results.sort(key=lambda x: x["raw"], reverse=True)
                top_results = results[:3] # Keep top 3 recommendations
                
                # Assign 0-100 scores purely referencing the best combo locally
                max_raw = top_results[0]["raw"]
                best_movesets[pid][ctx_p] = max_raw

                for i, r in enumerate(top_results):
                    score_0100 = 100 * (r["raw"]/max_raw) if max_raw > 0 else 0
                    batch_data.append((
                        prof["id"], pid, r["f"], r["c1"], r["c2"],
                        i+1, r["raw"], score_0100, r["elite"],
                        Json(r["metrics"]),
                        r["tempo_raw"]
                    ))

    # Normalize tempo scores with p05/p95 percentile
    tempo_vals = sorted([b[10] for b in batch_data if b[10] is not None])
    if tempo_vals:
        def percentile(vals, pct):
            k = (len(vals) - 1) * (pct / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return vals[int(k)]
            return vals[f] * (c - k) + vals[c] * (k - f)
        p05 = percentile(tempo_vals, 5)
        p95 = percentile(tempo_vals, 95)
        log.info(f"📊 Tempo normalization: p05={p05:.4f}, p95={p95:.4f}")
    else:
        p05, p95 = 0, 1

    final_batch = []
    for row in batch_data:
        t_raw = row[10]
        if t_raw is not None and p95 > p05:
            t_score = 100.0 * (t_raw - p05) / (p95 - p05)
            t_score = max(0, min(100, t_score))
        else:
            t_score = None
        # (profile_id, pokemon_id, fast, c1, c2, rank, raw_score, score_0_100, is_elite, metrics, tempo_raw, tempo_score)
        final_batch.append(row[:10] + (t_raw, round(t_score, 2) if t_score is not None else None))

    cur.execute("TRUNCATE TABLE pogo_scoring_moveset CASCADE;")
    execute_batch(cur, """
        INSERT INTO pogo_scoring_moveset
            (profile_id, pokemon_id, fast_move_id, charged_move_id_1, charged_move_id_2,
             rank, raw_score, score_0_100, is_elite, metrics, tempo_raw, tempo_score_0_100)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, final_batch)
    
    return best_movesets

def calculate_pokemon_scores(pokemon, max_stats, best_moves, prof_ctx, prof_weights, is_pvp, precomputed_pvp, max_sp):
    pid = pokemon['id']
    base_atk = float(pokemon['stats'].get('attack', 1))
    base_def = float(pokemon['stats'].get('defense', 1))
    base_sta = float(pokemon['stats'].get('stamina', 1))

    # Apply Shadow multipliers if this is a shadow profile
    is_shadow = prof_weights.get('shadow', False)
    if is_shadow:
        base_atk *= SHADOW_ATK_MULT
        base_def *= SHADOW_DEF_MULT

    bulk = base_def * base_sta
    
    # Shadow profiles reuse pve_raid moveset scores
    moveset_ctx = 'pve_raid' if prof_ctx == 'pve_raid_shadow' else prof_ctx
    moveset_score = best_moves.get(pid, {}).get(moveset_ctx, 0)
    
    pvp_ivs = None
    if is_pvp and precomputed_pvp:
        sp = precomputed_pvp["sp"]
        stat_score = 100.0 * (sp / max_sp) if max_sp > 0 else 0
        pvp_ivs = precomputed_pvp["pvp_stats"]
        pvp_ivs["sp"] = sp
        pvp_ivs["cp"] = precomputed_pvp["cp"]
        pvp_ivs["cap_cp"] = prof_weights.get("cap") or 0
    else:
        if prof_weights.get("stat_bias") == "attack":
            stat_score = 100.0 * (base_atk / max_stats['max_attack'])
        else:
            stat_score = 100.0 * (bulk / max_stats['max_bulk'])

    raw_score = (prof_weights['w_moveset'] * moveset_score) + (prof_weights['w_stats'] * stat_score)
    metrics = {
        "moveset_contribution": round(moveset_score, 2),
        "stat_contribution": round(stat_score, 2),
        "base_atk": round(base_atk, 1), "base_bulk": round(bulk, 1)
    }
    if is_shadow:
        metrics["shadow"] = True
    
    if is_pvp and precomputed_pvp:
        metrics["pvp_sp"] = int(precomputed_pvp["sp"])
        metrics["pvp_cp"] = int(precomputed_pvp["cp"])
        metrics["pvp_level"] = precomputed_pvp["pvp_stats"]["level"]
    
    return raw_score, metrics, pvp_ivs

def run_scoring():
    with safe_transaction() as (conn, cur):
        # Load weights from pogo_formula (or fallback to hardcoded defaults)
        formula_weights = load_formula_weights(cur)
        w_core = formula_weights["w_core"]
        w_tempo = formula_weights["w_tempo"]

        profiles_map = init_pokemon_profiles(cur, formula_weights)
        
        log.info("📊 Fetching Max Bounds for Stat Normalization...")
        max_stats = get_max_stats(cur)
        # Compute shadow max stats for shadow profiles
        shadow_max_stats = {
            'max_attack': max_stats['max_attack'] * SHADOW_ATK_MULT,
            'max_bulk': max_stats['max_bulk'] * SHADOW_DEF_MULT  # DEF portion of bulk is reduced
        }
        cpm_map = get_cpm_map(cur)
        
        best_moves = build_moveset_recommendations(cur, profiles_map, w_core, w_tempo)

        log.info("🧬 Fetching Pokémon Base Stats...")
        cur.execute("SELECT id, stats FROM pogo_pokemon")
        all_pokemon = [{"id": r[0], "stats": r[1]} for r in cur.fetchall()]
        
        batch_data = []
        iv_rank_batch = []
        
        for ctx, prof in profiles_map.items():
            results = []
            
            cap_cp = prof["weights"].get("cap")
            is_pvp = 'cap' in prof["weights"]
            
            max_sp = 1.0
            p_metrics = {}
            if is_pvp:
                log.info(f"🔄 Calculating Stat Products for {ctx}...")
                for p in all_pokemon:
                    base_a = float(p["stats"].get("attack", 1))
                    base_d = float(p["stats"].get("defense", 1))
                    base_s = float(p["stats"].get("stamina", 1))
                    sp, cp, metrics = find_best_pvp_stat_product(base_a, base_d, base_s, cpm_map, cap_cp)
                    p_metrics[p["id"]] = {"sp": sp, "cp": cp, "pvp_stats": metrics}
                    if sp > max_sp: max_sp = sp

            for p in all_pokemon:
                # Use shadow max stats for shadow profiles
                effective_max_stats = shadow_max_stats if prof["weights"].get("shadow") else max_stats
                raw, met, pvp_ivs = calculate_pokemon_scores(p, effective_max_stats, best_moves, ctx, prof["weights"], is_pvp, p_metrics.get(p["id"]), max_sp)
                results.append({"pid": p["id"], "raw": raw, "met": met})
                if pvp_ivs:
                    iv_rank_batch.append((p["id"], ctx, pvp_ivs["cap_cp"], 1, pvp_ivs["level"], pvp_ivs["iv_a"], pvp_ivs["iv_d"], pvp_ivs["iv_s"], pvp_ivs["cp"], pvp_ivs["atk_eff"], pvp_ivs["def_eff"], pvp_ivs["sta_eff"], pvp_ivs["sp"]))
            
            # Normalize for this profile
            if not results: continue
            raw_vals = [r["raw"] for r in results]
            min_raw, max_raw = min(raw_vals), max(raw_vals)
            
            cur.execute("UPDATE pogo_scoring_pokemon_profile SET normalizer = %s WHERE id = %s",
                       (Json({"min_raw": min_raw, "max_raw": max_raw}), prof["id"]))
            
            log.info(f"✨ Normalizing {ctx}: min={min_raw:.4f}, max={max_raw:.4f}")
            for r in results:
                score = 0
                if max_raw > min_raw:
                    score = 100.0 * (r["raw"] - min_raw) / (max_raw - min_raw)
                score = round(max(0.0, min(100.0, score)), 2)
                r["met"]["normalizer"] = {"min_raw": min_raw, "max_raw": max_raw}
                batch_data.append((prof["id"], r["pid"], r["raw"], score, Json(r["met"])))

        execute_batch(cur, """
            INSERT INTO pogo_scoring_pokemon (profile_id, pokemon_id, raw_score, score_0_100, metrics)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (profile_id, pokemon_id) DO UPDATE SET
                raw_score = EXCLUDED.raw_score,
                score_0_100 = EXCLUDED.score_0_100,
                metrics = EXCLUDED.metrics,
                updated_at = now()
        """, batch_data)
        
        # Insert to pogo_pvp_iv_rank if applicable
        if iv_rank_batch:
            execute_batch(cur, """
                INSERT INTO pogo_pvp_iv_rank (
                    pokemon_id, league, cap_cp, rank, level, 
                    iv_attack, iv_defense, iv_stamina, cp, 
                    atk_eff, def_eff, sta_eff, stat_product
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (pokemon_id, league, rank) DO UPDATE SET
                    cap_cp = EXCLUDED.cap_cp, level = EXCLUDED.level,
                    iv_attack = EXCLUDED.iv_attack, iv_defense = EXCLUDED.iv_defense, iv_stamina = EXCLUDED.iv_stamina,
                    cp = EXCLUDED.cp, atk_eff = EXCLUDED.atk_eff, def_eff = EXCLUDED.def_eff, sta_eff = EXCLUDED.sta_eff,
                    stat_product = EXCLUDED.stat_product, computed_at = now()
            """, iv_rank_batch)
        
        log.info("✅ Unified Pokémon Scoring Complete!")

if __name__ == "__main__":
    run_scoring()
