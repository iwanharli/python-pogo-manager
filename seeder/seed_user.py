import sys
import os
from pathlib import Path
from psycopg2.extras import Json

# Add the parent directory to sys.path so we can import from lib
sys.path.append(str(Path(__file__).parent.parent))

from lib.db import safe_transaction
from lib.log import setup_logger

log = setup_logger("user-seeder", "seed_user.log")

def seed_user():
    # User data from request
    email = "harlicuan@gmail.com"
    password_hash = "Kecilkec1l" # Simplified for dummy seeder (should be hashed in prod)
    username = "harlibud"
    display_name = "Harli Cuan"

    log.info(f"👤 Seeding dummy user: {username} ({email})...")

    with safe_transaction() as (conn, cur):
        cur.execute("""
            INSERT INTO users (email, username, display_name, password_hash, role, is_active)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET
                username = EXCLUDED.username,
                display_name = EXCLUDED.display_name,
                password_hash = EXCLUDED.password_hash,
                updated_at = now()
            RETURNING id
        """, (email, username, display_name, password_hash, 'admin', True))
        
        user_id = cur.fetchone()[0]
        log.info(f"  ✅ User {username} seeded successfully with ID: {user_id}")
        
        # Adding 5 Pokemon Dummies specific to this user
        log.info(f"🦇 Giving 5 dummy pokemons to {username}...")
        
        dummy_pokemons = [
            # Venusaur (Bulbasaur evolution, pokedex: 3), pogo_id can be left null or linked explicitly
            {"pokedex_id": 3, "nickname": "Rasta", "shiny": False, "cp": 2490, "level": 30.5, "iv": (15, 14, 15), "extra": {}}, 
            # Charizard (pokedex: 6) - Dynamax
            {"pokedex_id": 6, "nickname": "Ignis", "shiny": True, "cp": 2840, "level": 35.0, "iv": (15, 15, 14), "extra": {"is_dynamax": True}},
            # Blastoise (pokedex: 9) - Shadow
            {"pokedex_id": 9, "nickname": "Tsunami", "shiny": False, "cp": 1495, "level": 19.5, "iv": (0, 14, 15), "extra": {"is_shadow": True}},
            # Pikachu (pokedex: 25)
            {"pokedex_id": 25, "nickname": "Pika", "shiny": True, "cp": 850, "level": 20.0, "iv": (15, 15, 15), "extra": {}},
            # Mewtwo (pokedex: 150) - Shadow + Dynamax
            {"pokedex_id": 150, "nickname": "Psycho", "shiny": False, "cp": 4120, "level": 40.0, "iv": (15, 15, 15), "extra": {"is_shadow": True, "is_dynamax": True}}
        ]
        
        # Ensure the pokemons actually exist in the Pokedex table using their pokedex IDs.
        # Also join with pokedex_pokemon_go_map to get the base pogo_id
        cur.execute("""
            SELECT pd.pokeapi_pokemon_id, pd.id, pg_map.pogo_pokemon_id 
            FROM pokedex_pokemon pd
            LEFT JOIN pokedex_pokemon_go_map pg_map ON pd.id = pg_map.pokedex_pokemon_id
            WHERE pd.pokeapi_pokemon_id = ANY(%s)
        """, ([p["pokedex_id"] for p in dummy_pokemons],))
        db_id_map = {row[0]: {"pokedex_id": row[1], "pogo_id": row[2]} for row in cur.fetchall()}
        
        # Wipe old dummies to prevent duplication since we are seeding
        cur.execute("DELETE FROM user_pokemon WHERE user_id = %s", (user_id,))
        
        for p in dummy_pokemons:
            internal_ids = db_id_map.get(p["pokedex_id"])
            if not internal_ids:
                log.warning(f"     ⚠️ Pokemon {p['pokedex_id']} not found in pokedex_pokemon table. Skipping.")
                continue
                
            cur.execute("""
                INSERT INTO user_pokemon (
                    user_id, pokedex_pokemon_id, pogo_pokemon_id, nickname, shiny, cp, level, 
                    iv_attack, iv_defense, iv_stamina, extra
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, internal_ids["pokedex_id"], internal_ids["pogo_id"], p["nickname"], p["shiny"], p["cp"], 
                p["level"], p["iv"][0], p["iv"][1], p["iv"][2], Json(p.get("extra", {}))
            ))
            
        log.info(f"  ✅ 5 Dummy Pokemons assigned successfully.")

if __name__ == "__main__":
    try:
        seed_user()
    except Exception as e:
        log.error(f"  ❌ Failed to seed user: {e}")
        sys.exit(1)
