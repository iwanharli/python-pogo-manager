"""
tests/test_extract.py — Unit tests for extract helper functions (seed_pokedex.py).

Run: python -m pytest tests/ -v
"""

import pytest
from seed_pokedex import (
    extract_names,
    extract_genera,
    extract_flavor_texts,
    extract_dex_numbers,
    extract_egg_groups,
    extract_types,
    extract_stats,
    extract_abilities,
    extract_sprites,
)


# ─── extract_names ───────────────────────────────────────────

class TestExtractNames:
    def test_basic(self):
        data = [
            {"name": "Pikachu", "language": {"name": "en"}},
            {"name": "ピカチュウ", "language": {"name": "ja"}},
        ]
        result = extract_names(data)
        assert result == {"en": "Pikachu", "ja": "ピカチュウ"}

    def test_empty(self):
        assert extract_names([]) == {}

    def test_missing_language(self):
        data = [{"name": "Pikachu", "language": {}}]
        result = extract_names(data)
        assert result == {"": "Pikachu"}


# ─── extract_genera ──────────────────────────────────────────

class TestExtractGenera:
    def test_basic(self):
        data = [
            {"genus": "Mouse Pokémon", "language": {"name": "en"}},
            {"genus": "ねずみポケモン", "language": {"name": "ja"}},
        ]
        result = extract_genera(data)
        assert result == {"en": "Mouse Pokémon", "ja": "ねずみポケモン"}

    def test_empty(self):
        assert extract_genera([]) == {}


# ─── extract_flavor_texts ────────────────────────────────────

class TestExtractFlavorTexts:
    def test_basic(self):
        data = [
            {
                "flavor_text": "It loves\nketchup.",
                "language": {"name": "en"},
                "version": {"name": "red"},
            }
        ]
        result = extract_flavor_texts(data)
        assert len(result) == 1
        assert result[0]["lang"] == "en"
        assert result[0]["version"] == "red"
        assert "\n" not in result[0]["text"]  # newlines replaced

    def test_null_version(self):
        data = [
            {
                "flavor_text": "Test",
                "language": {"name": "en"},
                "version": None,
            }
        ]
        result = extract_flavor_texts(data)
        assert result[0]["version"] is None

    def test_empty(self):
        assert extract_flavor_texts([]) == []


# ─── extract_dex_numbers ─────────────────────────────────────

class TestExtractDexNumbers:
    def test_basic(self):
        data = [
            {"pokedex": {"name": "national"}, "entry_number": 25},
            {"pokedex": {"name": "kanto"}, "entry_number": 25},
        ]
        result = extract_dex_numbers(data)
        assert result == {"national": 25, "kanto": 25}

    def test_empty(self):
        assert extract_dex_numbers([]) == {}


# ─── extract_egg_groups ──────────────────────────────────────

class TestExtractEggGroups:
    def test_basic(self):
        data = [{"name": "field"}, {"name": "fairy"}]
        assert extract_egg_groups(data) == ["field", "fairy"]

    def test_empty(self):
        assert extract_egg_groups([]) == []


# ─── extract_types ───────────────────────────────────────────

class TestExtractTypes:
    def test_basic(self):
        data = [
            {"slot": 1, "type": {"name": "electric"}},
        ]
        result = extract_types(data)
        assert result == [{"slot": 1, "name": "electric"}]

    def test_dual_type(self):
        data = [
            {"slot": 1, "type": {"name": "fire"}},
            {"slot": 2, "type": {"name": "flying"}},
        ]
        result = extract_types(data)
        assert len(result) == 2
        assert result[0]["name"] == "fire"
        assert result[1]["name"] == "flying"

    def test_empty(self):
        assert extract_types([]) == []


# ─── extract_stats ───────────────────────────────────────────

class TestExtractStats:
    def test_basic(self):
        data = [
            {"stat": {"name": "hp"}, "base_stat": 35, "effort": 0},
            {"stat": {"name": "attack"}, "base_stat": 55, "effort": 0},
            {"stat": {"name": "speed"}, "base_stat": 90, "effort": 2},
        ]
        result = extract_stats(data)
        assert len(result) == 3
        assert result[0] == {"name": "hp", "base_stat": 35, "effort": 0}
        assert result[2]["effort"] == 2

    def test_empty(self):
        assert extract_stats([]) == []


# ─── extract_abilities ───────────────────────────────────────

class TestExtractAbilities:
    def test_basic(self):
        data = [
            {"ability": {"name": "static"}, "slot": 1, "is_hidden": False},
            {"ability": {"name": "lightning-rod"}, "slot": 3, "is_hidden": True},
        ]
        result = extract_abilities(data)
        assert len(result) == 2
        assert result[0]["name"] == "static"
        assert result[0]["is_hidden"] is False
        assert result[1]["is_hidden"] is True

    def test_empty(self):
        assert extract_abilities([]) == []


# ─── extract_sprites ─────────────────────────────────────────

class TestExtractSprites:
    def test_basic(self):
        data = {
            "front_default": "https://example.com/front.png",
            "front_shiny": "https://example.com/shiny.png",
            "front_female": None,
            "back_default": None,
            "back_shiny": None,
            "other": {
                "official-artwork": {
                    "front_default": "https://example.com/official.png",
                    "front_shiny": None,
                },
                "home": {"front_default": "https://example.com/home.png"},
                "dream_world": {"front_default": None},
            },
        }
        result = extract_sprites(data)
        assert "front_default" in result
        assert "front_shiny" in result
        assert "front_female" not in result  # null → skipped
        assert "official_artwork" in result
        assert "home" in result
        assert "dream_world" not in result  # null → skipped

    def test_empty(self):
        result = extract_sprites({})
        assert result == {}

    def test_no_other(self):
        data = {"front_default": "https://example.com/front.png"}
        result = extract_sprites(data)
        assert result == {"front_default": "https://example.com/front.png"}
