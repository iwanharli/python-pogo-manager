"""CPM (CP Multiplier) extraction from PokeMiners GameMaster.

Known CPM values: community-verified, levels 1.0–51.0.
Source: https://pokemongo.fandom.com/wiki/CP_Multiplier

Niantic obfuscates CPM above level 28 in the GameMaster.
For obfuscated values, we fall back to known community data.
"""

import json
import hashlib
import logging
from decimal import Decimal

log = logging.getLogger("pogo-seeder")

# ── Known CPM values (community-verified, levels 1.0–51.0) ──

KNOWN_CPM = {
    Decimal("1.0"):  Decimal("0.094"),
    Decimal("1.5"):  Decimal("0.135137432"),
    Decimal("2.0"):  Decimal("0.16639787"),
    Decimal("2.5"):  Decimal("0.192650919"),
    Decimal("3.0"):  Decimal("0.21573247"),
    Decimal("3.5"):  Decimal("0.236572661"),
    Decimal("4.0"):  Decimal("0.25572005"),
    Decimal("4.5"):  Decimal("0.273530381"),
    Decimal("5.0"):  Decimal("0.29024988"),
    Decimal("5.5"):  Decimal("0.306057377"),
    Decimal("6.0"):  Decimal("0.3210876"),
    Decimal("6.5"):  Decimal("0.335445036"),
    Decimal("7.0"):  Decimal("0.34921268"),
    Decimal("7.5"):  Decimal("0.362457751"),
    Decimal("8.0"):  Decimal("0.37523559"),
    Decimal("8.5"):  Decimal("0.387592406"),
    Decimal("9.0"):  Decimal("0.39956728"),
    Decimal("9.5"):  Decimal("0.411193551"),
    Decimal("10.0"): Decimal("0.42250001"),
    Decimal("10.5"): Decimal("0.432926419"),
    Decimal("11.0"): Decimal("0.44310755"),
    Decimal("11.5"): Decimal("0.4530599578"),
    Decimal("12.0"): Decimal("0.46279839"),
    Decimal("12.5"): Decimal("0.472336083"),
    Decimal("13.0"): Decimal("0.48168495"),
    Decimal("13.5"): Decimal("0.4908558"),
    Decimal("14.0"): Decimal("0.49985844"),
    Decimal("14.5"): Decimal("0.508701765"),
    Decimal("15.0"): Decimal("0.51739395"),
    Decimal("15.5"): Decimal("0.525942511"),
    Decimal("16.0"): Decimal("0.53435433"),
    Decimal("16.5"): Decimal("0.542635767"),
    Decimal("17.0"): Decimal("0.55079269"),
    Decimal("17.5"): Decimal("0.558830576"),
    Decimal("18.0"): Decimal("0.56675452"),
    Decimal("18.5"): Decimal("0.574569153"),
    Decimal("19.0"): Decimal("0.58227891"),
    Decimal("19.5"): Decimal("0.589887917"),
    Decimal("20.0"): Decimal("0.59740001"),
    Decimal("20.5"): Decimal("0.604818814"),
    Decimal("21.0"): Decimal("0.61215729"),
    Decimal("21.5"): Decimal("0.619399365"),
    Decimal("22.0"): Decimal("0.62656713"),
    Decimal("22.5"): Decimal("0.633644533"),
    Decimal("23.0"): Decimal("0.64065295"),
    Decimal("23.5"): Decimal("0.647576426"),
    Decimal("24.0"): Decimal("0.65443563"),
    Decimal("24.5"): Decimal("0.661214806"),
    Decimal("25.0"): Decimal("0.667934"),
    Decimal("25.5"): Decimal("0.674577537"),
    Decimal("26.0"): Decimal("0.68116492"),
    Decimal("26.5"): Decimal("0.687680648"),
    Decimal("27.0"): Decimal("0.69414365"),
    Decimal("27.5"): Decimal("0.700538673"),
    Decimal("28.0"): Decimal("0.70688421"),
    Decimal("28.5"): Decimal("0.713164996"),
    Decimal("29.0"): Decimal("0.71939909"),
    Decimal("29.5"): Decimal("0.725571552"),
    Decimal("30.0"): Decimal("0.7317"),
    Decimal("30.5"): Decimal("0.734741009"),
    Decimal("31.0"): Decimal("0.73776948"),
    Decimal("31.5"): Decimal("0.740785574"),
    Decimal("32.0"): Decimal("0.74378943"),
    Decimal("32.5"): Decimal("0.746781211"),
    Decimal("33.0"): Decimal("0.74976104"),
    Decimal("33.5"): Decimal("0.752729087"),
    Decimal("34.0"): Decimal("0.75568551"),
    Decimal("34.5"): Decimal("0.758630378"),
    Decimal("35.0"): Decimal("0.76156384"),
    Decimal("35.5"): Decimal("0.764486065"),
    Decimal("36.0"): Decimal("0.76739717"),
    Decimal("36.5"): Decimal("0.770297266"),
    Decimal("37.0"): Decimal("0.7731865"),
    Decimal("37.5"): Decimal("0.776064962"),
    Decimal("38.0"): Decimal("0.77893275"),
    Decimal("38.5"): Decimal("0.781790055"),
    Decimal("39.0"): Decimal("0.784637"),
    Decimal("39.5"): Decimal("0.787473578"),
    Decimal("40.0"): Decimal("0.79030001"),
    Decimal("40.5"): Decimal("0.792803968"),
    Decimal("41.0"): Decimal("0.79530001"),
    Decimal("41.5"): Decimal("0.797800015"),
    Decimal("42.0"): Decimal("0.8003"),
    Decimal("42.5"): Decimal("0.802799995"),
    Decimal("43.0"): Decimal("0.8053"),
    Decimal("43.5"): Decimal("0.807800012"),
    Decimal("44.0"): Decimal("0.81029999"),
    Decimal("44.5"): Decimal("0.812799985"),
    Decimal("45.0"): Decimal("0.81529999"),
    Decimal("45.5"): Decimal("0.81779999"),
    Decimal("46.0"): Decimal("0.82029999"),
    Decimal("46.5"): Decimal("0.82279999"),
    Decimal("47.0"): Decimal("0.82529999"),
    Decimal("47.5"): Decimal("0.82779999"),
    Decimal("48.0"): Decimal("0.83029999"),
    Decimal("48.5"): Decimal("0.83279999"),
    Decimal("49.0"): Decimal("0.83529999"),
    Decimal("49.5"): Decimal("0.83779999"),
    Decimal("50.0"): Decimal("0.84029999"),
    Decimal("50.5"): Decimal("0.84279999"),
    Decimal("51.0"): Decimal("0.84529999"),
}


# ── Functions ────────────────────────────────────────────────

def extract_cpm_from_gm(gm_data: list) -> list[dict]:
    """Extract CPM values from GameMaster, fallback to known values if obfuscated."""
    cpm_raw = None
    for item in gm_data:
        if item.get("templateId") == "PLAYER_LEVEL_SETTINGS":
            settings = item.get("data", {}).get("playerLevel", {})
            cpm_raw = settings.get("cpMultiplier", [])
            break

    if not cpm_raw:
        log.warning("  ⚠ PLAYER_LEVEL_SETTINGS not found in GameMaster!")
        log.info("  ℹ Using full known CPM table as fallback")
        return [{"level": lvl, "cpm": cpm} for lvl, cpm in sorted(KNOWN_CPM.items())]

    log.info("  ℹ Found %d CPM values in GameMaster", len(cpm_raw))

    # Detect obfuscation: 4+ consecutive identical values
    obfuscation_start = None
    for i in range(3, len(cpm_raw)):
        if cpm_raw[i] == cpm_raw[i - 1] == cpm_raw[i - 2] == cpm_raw[i - 3]:
            level = Decimal(str((i - 3) // 2 + 1 + (0.5 if (i - 3) % 2 else 0)))
            obfuscation_start = level
            log.warning("  ⚠ Obfuscation detected starting at level %.1f (CPM=%s)",
                        obfuscation_start, cpm_raw[i])
            break

    results = []
    for i, val in enumerate(cpm_raw):
        level = Decimal(str((i // 2) + 1 + (0.5 if i % 2 else 0)))
        cpm_val = Decimal(str(val))

        if obfuscation_start and level >= obfuscation_start:
            known = KNOWN_CPM.get(level)
            if known:
                cpm_val = known
                log.debug("  Level %.1f: using known CPM %s (GM obfuscated)", level, known)
            else:
                log.warning("  ⚠ Level %.1f: no known CPM, skipping", level)
                continue
        else:
            log.debug("  Level %.1f: using GM CPM %s", level, cpm_val)

        results.append({"level": level, "cpm": cpm_val})

    # Add levels beyond GM range from known table
    gm_max_level = Decimal(str((len(cpm_raw) - 1) // 2 + 1 + (0.5 if (len(cpm_raw) - 1) % 2 else 0)))
    for level, cpm in sorted(KNOWN_CPM.items()):
        if level > gm_max_level:
            results.append({"level": level, "cpm": cpm})
            log.debug("  Level %.1f: added from known table (beyond GM)", level)

    return results


def update_gm_hash(cur, gm_data: list):
    """Store GameMaster metadata in pogo_api_hashes."""
    raw = json.dumps(gm_data, sort_keys=True, ensure_ascii=False)
    sha = hashlib.sha512(raw.encode()).hexdigest()

    cur.execute("""
        INSERT INTO pogo_api_hashes (endpoint, sha512, fetched_at)
        VALUES (%s, %s, now())
        ON CONFLICT (endpoint) DO UPDATE SET
            sha512 = EXCLUDED.sha512,
            fetched_at = now(),
            updated_at = now()
    """, ("gamemaster.json", sha))

    log.info("  ✔ GameMaster hash stored: %s...%s", sha[:16], sha[-16:])
    return sha
