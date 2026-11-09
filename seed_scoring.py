import sys
import logging
from decimal import Decimal
from psycopg2.extras import Json, execute_batch

from lib.db import safe_transaction
from lib.log import setup_logger

log = setup_logger("move-scoring", "seed_scoring.log")

# ── Profiles ──────────────────────────────────────────────────

DEFAULT_PROFILES = [
    {
        "name": "moves_v1",
        "context": "pve_fast",
        "version": "v1",
        "weights": {"w_dps": 0.65, "w_eps": 0.35},
        "notes": "Base PVE Fast move scoring"
    },
    {
        "name": "moves_v1",
        "context": "pve_charged",
        "version": "v1",
        "weights": {"w_dpe": 0.75, "w_dps": 0.25},
        "notes": "Base PVE Charged move scoring"
    },
    {
        "name": "moves_v1",
        "context": "pvp_fast",
        "version": "v1",
        "weights": {"w_ept": 0.60, "w_dpt": 0.40},
        "notes": "Base PVP Fast move scoring"
    },
    {
        "name": "moves_v1",
        "context": "pvp_charged",
        "version": "v1",
        "weights": {"w_dpe": 0.80, "w_cost": 0.20},
        "notes": "Base PVP Charged move scoring"
    }
]

def init_profiles(cur):
    """Upsert default profiles and return full profile data from DB."""
    log.info("🛠 Initializing scoring profiles...")

    # Try to load formula defaults from pogo_formula (if table exists)
    formula_defaults = {}
    try:
        cur.execute("""
            SELECT name, defaults FROM pogo_formula
            WHERE kind = 'scoring' AND scope = 'move' AND is_active = TRUE
        """)
        for fname, defaults in cur.fetchall():
            if defaults and isinstance(defaults, dict):
                formula_defaults[fname] = defaults
                log.info(f"  📐 Loaded formula '{fname}': {defaults}")
    except Exception:
        log.info("  ℹ pogo_formula table not available — using hardcoded defaults")

    # Map formula name → profile context for weight merging
    formula_to_context = {
        "move_pve_fast_v1": "pve_fast",
        "move_pve_charged_v1": "pve_charged",
        "move_pvp_fast_v1": "pvp_fast",
        "move_pvp_charged_v1": "pvp_charged",
    }

    for p in DEFAULT_PROFILES:
        # Override weights from formula if available
        weights = dict(p["weights"])
        for fname, ctx in formula_to_context.items():
            if ctx == p["context"] and fname in formula_defaults:
                weights.update(formula_defaults[fname])

        cur.execute("""
            INSERT INTO pogo_scoring_profile (name, context, version, weights, notes, is_active)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (name, context, version) DO UPDATE SET
                weights = EXCLUDED.weights,
                is_active = EXCLUDED.is_active,
                updated_at = now()
        """, (p["name"], p["context"], p["version"], Json(weights), p["notes"], True))
        
    # Always fetch back from DB to be the source of truth
    cur.execute("SELECT id, context, weights FROM pogo_scoring_profile WHERE is_active = true")
    return {row[1]: {"id": row[0], "weights": row[2]} for row in cur.fetchall()}

# ── Calculations ──────────────────────────────────────────────

def calculate_pve_fast(move, weights):
    # Safety guards
    power = move.get('power') or 0
    energy = move.get('energy') or 0
    duration_ms = max(500, move.get('duration_ms') or 1000) # Min 500ms
    
    duration_sec = duration_ms / 1000.0
    dps = power / duration_sec
    eps = energy / duration_sec
    
    raw = float(weights['w_dps']) * dps + float(weights['w_eps']) * eps
    return raw, {"dps": round(dps, 2), "eps": round(eps, 2)}

def calculate_pve_charged(move, weights):
    power = move.get('power') or 0
    energy = move.get('energy') or 0
    
    if not energy:
        return 0.0, {"dpe": 0, "dps": 0}
        
    energy_cost = abs(energy)
    duration_ms = max(500, move.get('duration_ms') or 1000)
    
    duration_sec = duration_ms / 1000.0
    dpe = power / energy_cost
    dps = power / duration_sec
    
    raw = float(weights['w_dpe']) * dpe + float(weights['w_dps']) * dps
    return raw, {"dpe": round(dpe, 2), "dps": round(dps, 2)}

def calculate_pvp_fast(move, weights):
    combat = move.get('combat') or {}
    power = combat.get('power') or 0
    energy = combat.get('energy') or 0
    turns = max(1, combat.get('turns') or 1)
    
    dpt = power / turns
    ept = energy / turns
    
    raw = float(weights['w_ept']) * ept + float(weights['w_dpt']) * dpt
    return raw, {"dpt": round(dpt, 2), "ept": round(ept, 2)}

def calculate_pvp_charged(move, weights):
    combat = move.get('combat') or {}
    power = combat.get('power') or 0
    energy = combat.get('energy') or 0
    
    if not energy:
        return 0.0, {"dpe": 0, "cost_factor": 0}
        
    energy_cost = abs(energy)
    cost_factor = 1.0 / energy_cost
    
    raw = (float(weights['w_dpe']) * (power / energy_cost) + 
           float(weights['w_cost']) * cost_factor)
    
    return raw, {"dpe": round(power/energy_cost, 2), "cost_factor": round(cost_factor, 4)}

# ── Main Seeder ───────────────────────────────────────────────

def run_scoring():
    with safe_transaction() as (conn, cur):
        # 1. Init profiles and get source of truth from DB
        profiles = init_profiles(cur)

        # 2. Get move classifications and usage from pivot table
        log.info("🔍 Classifying moves and fetching usage data...")
        cur.execute("""
            SELECT 
                move_id, 
                ARRAY_AGG(DISTINCT move_kind) as kinds, 
                COUNT(*) as usage_count 
            FROM pogo_pokemon_move 
            GROUP BY move_id
        """)
        usage_data = {row[0]: {"kinds": row[1], "usage_count": row[2]} for row in cur.fetchall()}
        
        # Helper to identify fast/charged
        fast_move_ids = {mid for mid, data in usage_data.items() 
                         if 'quick' in data['kinds'] or 'elite_quick' in data['kinds']}
        charged_move_ids = {mid for mid, data in usage_data.items() 
                            if 'cinematic' in data['kinds'] or 'elite_cinematic' in data['kinds']}

        # 3. Get all move data
        cur.execute("SELECT move_id, power, energy, duration_ms, combat FROM pogo_move")
        columns = [desc[0] for desc in cur.description]
        all_moves = [dict(zip(columns, row)) for row in cur.fetchall()]
        log.info(f"📊 Calculating scores for {len(all_moves)} moves...")

        # 4. Calculate Raw Scores
        context_results = {ctx: [] for ctx in profiles.keys()}

        for move in all_moves:
            mid = move['move_id']
            u = usage_data.get(mid, {"kinds": [], "usage_count": 0})
            kinds = u['kinds']
            usage_count = u['usage_count']
            
            is_fast = mid in fast_move_ids
            is_charged = mid in charged_move_ids
            
            # Common availability metrics
            availability = {
                "kinds": kinds,
                "usage_count": usage_count,
            }

            if is_fast:
                raw_pve, m_pve = calculate_pve_fast(move, profiles['pve_fast']['weights'])
                raw_pvp, m_pvp = calculate_pvp_fast(move, profiles['pvp_fast']['weights'])
                
                # elite_only = has elite_quick but NO quick
                elite_only = 'elite_quick' in kinds and 'quick' not in kinds
                
                m_pve.update(availability); m_pve["elite_only"] = elite_only
                m_pvp.update(availability); m_pvp["elite_only"] = elite_only
                
                context_results['pve_fast'].append({"mid": mid, "raw": raw_pve, "metrics": m_pve})
                context_results['pvp_fast'].append({"mid": mid, "raw": raw_pvp, "metrics": m_pvp})
            
            if is_charged:
                raw_pve, m_pve = calculate_pve_charged(move, profiles['pve_charged']['weights'])
                raw_pvp, m_pvp = calculate_pvp_charged(move, profiles['pvp_charged']['weights'])

                # elite_only = has elite_cinematic but NO cinematic
                elite_only = 'elite_cinematic' in kinds and 'cinematic' not in kinds
                
                m_pve.update(availability); m_pve["elite_only"] = elite_only
                m_pvp.update(availability); m_pvp["elite_only"] = elite_only

                context_results['pve_charged'].append({"mid": mid, "raw": raw_pve, "metrics": m_pve})
                context_results['pvp_charged'].append({"mid": mid, "raw": raw_pvp, "metrics": m_pvp})

        # 5. Normalize and Batch Upsert
        for ctx, results in context_results.items():
            if not results: continue
            
            pid = profiles[ctx]['id']
            raw_vals = [r['raw'] for r in results]
            min_raw = min(raw_vals)
            max_raw = max(raw_vals)
            
            log.info(f"✨ Normalizing {ctx}: min={min_raw:.4f}, max={max_raw:.4f}")
            
            # Update normalizer
            cur.execute("UPDATE pogo_scoring_profile SET normalizer = %s WHERE id = %s",
                       (Json({"min_raw": min_raw, "max_raw": max_raw}), pid))

            # Prepare batch data
            batch_data = []
            for r in results:
                score = 0.0
                if max_raw > min_raw:
                    score = 100.0 * (r['raw'] - min_raw) / (max_raw - min_raw)
                
                # Clamp and Round
                score = round(max(0.0, min(100.0, score)), 2)
                
                batch_data.append((pid, r['mid'], r['raw'], score, Json(r['metrics'])))

            # Execute batch upsert
            execute_batch(cur, """
                INSERT INTO pogo_scoring_move (profile_id, move_id, raw_score, score_0_100, metrics)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (profile_id, move_id) DO UPDATE SET
                    raw_score = EXCLUDED.raw_score,
                    score_0_100 = EXCLUDED.score_0_100,
                    metrics = EXCLUDED.metrics,
                    computed_at = now()
            """, batch_data)

    log.info("🏁 Refined move scoring complete!")

if __name__ == "__main__":
    run_scoring()
