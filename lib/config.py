"""Shared configuration — reads .env once, exposes constants."""

import os
import pathlib

from dotenv import load_dotenv

load_dotenv()

# ── Database ──────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "db_pogo_manager")
DB_USER = os.getenv("DB_USER", "iwanharli")
DB_PASS = os.getenv("DB_PASS", "")

# ── API Sources ───────────────────────────────────────────────
POKEAPI_BASE_URL = os.getenv("POKEAPI_BASE_URL", "https://pokeapi.co/api/v2")
POGO_API_BASE_URL = os.getenv("POGO_API_BASE_URL",
                               "https://pokemon-go-api.github.io/pokemon-go-api")
GAMEMASTER_URL = os.getenv("GAMEMASTER_URL",
    "https://raw.githubusercontent.com/PokeMiners/game_masters/master/latest/latest.json")

# ── Options ───────────────────────────────────────────────────
SEED_LIMIT = int(os.getenv("SEED_LIMIT", "0"))

# ── Paths ─────────────────────────────────────────────────────
ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
CACHE_DIR = pathlib.Path(os.getenv("CACHE_DIR", str(ROOT_DIR / "cache")))
LOG_DIR = pathlib.Path(os.getenv("LOG_DIR", str(ROOT_DIR / "logs")))
MIGRATION_DIR = ROOT_DIR / "migrations"
