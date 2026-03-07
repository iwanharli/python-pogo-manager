"""
setup.py — One-shot setup: create tables + seed PokéAPI + seed Pokemon GO + seed CPM.

Usage:
    python setup.py                # create tables + update all seeders
    python setup.py --reset        # truncate + re-seed from scratch
"""

import sys
import argparse
import subprocess

from lib.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS, MIGRATION_DIR, ROOT_DIR
from lib.db import get_connection


def banner(msg: str):
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}\n")


def run_migration():
    """Execute the migration SQL to create all tables."""
    banner("📦 Step 1/3: Running migration (create tables)")

    if not MIGRATION_DIR.exists():
        print(f"  ❌ Migration directory not found: {MIGRATION_DIR}")
        sys.exit(1)

    sql_files = sorted(MIGRATION_DIR.glob("*.sql"))
    if not sql_files:
        print(f"  ❌ No .sql files found in {MIGRATION_DIR}")
        sys.exit(1)

    conn = get_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for sql_file in sql_files:
                print(f"  Running {sql_file.name}...")
                sql = sql_file.read_text()
                cur.execute(sql)
        print("  ✅ Migration complete — all tables ready")
    except Exception as e:
        print(f"  ❌ Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


def run_seeder(script: str, args: list[str], label: str):
    """Run a seeder script as a subprocess."""
    banner(label)
    cmd = [sys.executable, str(ROOT_DIR / script)] + args
    print(f"  → {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if result.returncode != 0:
        print(f"\n  ❌ {script} failed (exit code {result.returncode})")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="One-shot setup: create tables + seed all data.",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Reset semua tabel dan seed ulang dari awal.",
    )
    args = parser.parse_args()

    seed_args = ["--reset"] if args.reset else ["--update"]

    # Step 1: Migration
    run_migration()

    # Step 2: Seed PokéAPI
    run_seeder("seed_pokedex.py", seed_args, "🔴 Step 2/3: Seeding PokéAPI (main series)")

    # Step 3: Seed Pokemon GO + CPM
    run_seeder("seed_pogo.py", seed_args, "🟢 Step 3/3: Seeding Pokemon GO API + CPM")

    banner("🎉 All done! Database is ready.")
    print("  Tables created ✔")
    print("  PokéAPI data seeded ✔")
    print("  Pokemon GO + CPM data seeded ✔")
    print()


if __name__ == "__main__":
    main()
