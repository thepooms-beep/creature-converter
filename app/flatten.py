"""Flatten transformer + approve workflow.

Turns the nested converter JSON (matching system_prompt.md schema) into
the flat shape DM CM's monsters.js consumes, per the exact mapping in
the brief. Pure function so it can be unit-tested.

Approving moves the flat record + the export-quality webp into
edited/<source-slug>/. The unedited copies stay in place so a
re-conversion / re-pick is still possible after approval.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import image_gen, storage

# Abilities in DM CM's canonical order.
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")


def _mod(score: int) -> int:
    """D&D ability modifier."""
    return (int(score) - 10) // 2


def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def _cr_string(cr: Any) -> str:
    """Per brief: 0.125->'1/8', 0.25->'1/4', 0.5->'1/2', else str(int)."""
    if cr is None:
        return "0"
    if isinstance(cr, str):
        return cr
    if cr == 0.125:
        return "1/8"
    if cr == 0.25:
        return "1/4"
    if cr == 0.5:
        return "1/2"
    return str(int(cr))


def _speed_string(speeds: list[dict[str, Any]]) -> str:
    """'30 ft., Fly 60 ft., Swim 20 ft.' — walk speed is unlabeled."""
    if not speeds:
        return ""
    parts: list[str] = []
    for s in speeds:
        t = (s.get("type") or "walk").lower()
        v = s.get("value")
        if v is None:
            continue
        if t == "walk":
            parts.append(f"{int(v)} ft.")
        else:
            parts.append(f"{t.capitalize()} {int(v)} ft.")
    return ", ".join(parts)


def _skills_string(skills: dict[str, Any]) -> str:
    """'Perception +5, Stealth +6'."""
    if not skills:
        return ""
    return ", ".join(f"{k} {_signed(int(v))}" for k, v in skills.items())


def _immunities_string(damage_imm: list[str], cond_imm: list[str]) -> str:
    """'<damage>; <conditions>' — drop the ';' if one side missing."""
    damage_part = ", ".join(damage_imm) if damage_imm else ""
    cond_part = ", ".join(cond_imm) if cond_imm else ""
    if damage_part and cond_part:
        return f"{damage_part}; {cond_part}"
    return damage_part or cond_part


def _senses_string(senses: list[str], passive: int | None = None) -> str:
    """Comma-joined senses, with 'Passive Perception N' appended if not
    already present. Conversion JSON usually already includes the passive
    line — only append if missing."""
    if not senses:
        senses = []
    has_passive = any("passive perception" in s.lower() for s in senses)
    parts = list(senses)
    if not has_passive and passive is not None:
        parts.append(f"Passive Perception {passive}")
    return ", ".join(parts)


def _block_string(items: list[dict[str, Any]]) -> str:
    """traits / actions / etc.: 'Name. Text\\nName. Text'."""
    if not items:
        return ""
    return "\n".join(
        f"{(it.get('name') or '').strip()}. {(it.get('text') or '').strip()}"
        for it in items
        if (it.get("name") or it.get("text"))
    )


def _save_string(creature: dict[str, Any], ability_key: str) -> str:
    """+N save string: mod + (PB if ability in save_proficiencies else 0)."""
    abilities = creature.get("abilities") or {}
    score = int(abilities.get(ability_key, 10))
    proficient = ability_key.upper() in (
        s.upper() for s in (creature.get("save_proficiencies") or [])
    )
    pb = int(creature.get("pb") or 2)
    bonus = _mod(score) + (pb if proficient else 0)
    return _signed(bonus)


def _passive_from_skills(creature: dict[str, Any]) -> int:
    """If senses[] lacks 'Passive Perception N', compute from Wis mod + skill."""
    wis_mod = _mod(int((creature.get("abilities") or {}).get("wis", 10)))
    perception = (creature.get("skills") or {}).get("Perception")
    if perception is not None:
        return 10 + int(perception)
    return 10 + wis_mod


def flatten_creature(
    nested: dict[str, Any],
    image_url: str | None = None,
) -> dict[str, Any]:
    """Transform a nested converter record into the flat shape monsters.js
    expects. Pure function — no I/O.

    image_url: if provided, used directly (e.g. existing aidedd.org URL).
    If None, defaults to 'assets/monster_images/<slug>.webp' (relative path
    per the brief for new records)."""
    name = (nested.get("name") or "").strip()
    slug = storage.slugify(name)

    flat: dict[str, Any] = {
        "id": slug,
        "name": name,
        "size": nested.get("size", ""),
        "type": nested.get("type", ""),
        "alignment": nested.get("alignment", ""),
        "ac": int((nested.get("ac") or {}).get("value", 10)),
        "hp": int((nested.get("hp") or {}).get("average", 0)),
        "speed": _speed_string(nested.get("speeds") or []),
        "initiative": nested.get("initiative", ""),
    }

    abilities = nested.get("abilities") or {}
    for a in ABILITIES:
        flat[a] = int(abilities.get(a, 10))
        flat[f"{a}_save"] = _save_string(nested, a)

    flat["skills"] = _skills_string(nested.get("skills") or {})
    flat["immunities"] = _immunities_string(
        nested.get("damage_immunities") or [],
        nested.get("condition_immunities") or [],
    )
    flat["senses"] = _senses_string(
        nested.get("senses") or [],
        passive=_passive_from_skills(nested),
    )
    flat["languages"] = ", ".join(nested.get("languages") or [])
    flat["cr"] = _cr_string(nested.get("cr"))
    flat["xp"] = int(nested.get("xp") or 0)

    flat["traits"] = _block_string(nested.get("traits") or [])
    flat["actions"] = _block_string(nested.get("actions") or [])
    flat["bonus_actions"] = _block_string(nested.get("bonus_actions") or [])
    flat["reactions"] = _block_string(nested.get("reactions") or [])
    legendary = _block_string(nested.get("legendary_actions") or [])
    flat["legendary_actions"] = legendary
    if legendary:
        flat["legendary"] = True

    flat["source"] = (nested.get("source") or {}).get("title", "")
    flat["setting"] = nested.get("setting", "")
    flat["custom"] = False
    flat["image_url"] = image_url or f"assets/monster_images/{slug}.webp"

    # New optional fields appended unchanged (per brief).
    if nested.get("read_aloud"):
        flat["read_aloud"] = nested["read_aloud"]
    if nested.get("salient_features"):
        flat["salient_features"] = nested["salient_features"]
    if nested.get("habitat"):
        flat["habitat"] = nested["habitat"]

    # conversion_notes is dropped intentionally per brief.
    return flat


# ---------------------------------------------------------------------------
# Approve workflow: write flat JSON + copy export webp into edited/.
# ---------------------------------------------------------------------------

def edited_dir(source_slug: str) -> Path:
    return storage.EDITED_DIR / source_slug


def approve_creature(source_slug: str, creature_slug: str) -> dict[str, Any]:
    """Read nested JSON, flatten, write to edited/, copy export webp."""
    nested = storage.read_creature(source_slug, creature_slug)
    if nested is None:
        raise FileNotFoundError(
            f"No converted JSON for {source_slug}/{creature_slug}. Run Convert first."
        )
    out_dir = edited_dir(source_slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    flat = flatten_creature(nested)
    flat_path = out_dir / f"{creature_slug}.json"
    flat_path.write_text(json.dumps(flat, indent=2, ensure_ascii=False), encoding="utf-8")

    # Copy the export-quality webp if it exists; image may not be approved yet.
    src_webp = image_gen.final_image_path(source_slug, creature_slug)
    image_copied = False
    if src_webp.exists():
        shutil.copy2(src_webp, out_dir / f"{creature_slug}.webp")
        image_copied = True

    return {
        "ok": True,
        "flat_path": str(flat_path.relative_to(storage.ROOT)),
        "image_copied": image_copied,
    }
