"""AD&D -> D&D 2024 stat-block conversion via Claude vision."""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image

from . import storage

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CONVERSION_PROMPT = (PROMPTS_DIR / "conversion_prompt.md").read_text(encoding="utf-8")

# Opus 4.7 for the actual conversion -- complex reasoning over the stat math,
# CR snap, save proficiencies, AD&D -> 5.5e trait translation. Override via env.
CONVERSION_MODEL = os.getenv("CONVERSION_MODEL", "claude-opus-4-7")
MAX_TOKENS = 8192


def _load_anthropic_client():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env or export it before running convert."
        )
    from anthropic import Anthropic
    return Anthropic()


def _png_bytes(path: Path) -> bytes:
    with Image.open(path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _strip_json_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


# AD&D source uses single letters; the converter occasionally echoes them
# instead of the full 5.5e word. Normalize so the form's <select> matches
# and the flatten layer outputs the correct token.
_SIZE_LETTER_MAP = {
    "T": "Tiny", "S": "Small", "M": "Medium",
    "L": "Large", "H": "Huge", "G": "Gargantuan",
}
_SIZE_VALID = set(_SIZE_LETTER_MAP.values())


def _normalize_size(creature: dict[str, Any]) -> None:
    """In-place: coerce size to one of Tiny/Small/Medium/Large/Huge/Gargantuan."""
    raw = creature.get("size")
    if not isinstance(raw, str):
        return
    stripped = raw.strip()
    if stripped in _SIZE_VALID:
        creature["size"] = stripped
        return
    # "M (Man-sized)" → "M" → "Medium"; "huge" → "Huge"
    head = stripped.split()[0].rstrip("().,;:") if stripped else ""
    if head.upper() in _SIZE_LETTER_MAP:
        creature["size"] = _SIZE_LETTER_MAP[head.upper()]
        return
    titled = stripped.title()
    if titled in _SIZE_VALID:
        creature["size"] = titled


def _image_block(path: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(_png_bytes(path)).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def convert_creature(
    source_image_paths: list[Path],
    target_name: str | None = None,
    setting_hint: str | None = None,
    source_title: str | None = None,
    notes: str | None = None,
    prior_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the conversion prompt against one or more source-page images.
    For re-runs, pass the prior JSON and a note describing what to change.
    Pass target_name when multiple creatures share the stat block — the
    model converts only that one."""
    if not source_image_paths:
        raise ValueError("At least one source image is required")
    client = _load_anthropic_client()

    content: list[dict[str, Any]] = [_image_block(p) for p in source_image_paths]

    hint_lines: list[str] = []
    if target_name:
        hint_lines.append(
            f"Convert ONLY the creature named \"{target_name}\". "
            "If the source pages contain a shared stat block with multiple variants "
            "(e.g. Hurrum/Critic/Renk/Ock'n on one page, or Worker/Soldier/Queen "
            "antloids), extract just the column for this creature and ignore the others. "
            "Return a single JSON object for this one creature — NOT an array."
        )
    if setting_hint:
        hint_lines.append(f"Campaign setting hint: {setting_hint}.")
    if source_title:
        hint_lines.append(f"Source book: {source_title}.")
    if len(source_image_paths) > 1:
        hint_lines.append(
            f"The entry spans {len(source_image_paths)} pages, shown above in order. "
            "Read all pages to assemble the full stat block and prose."
        )
    if hint_lines:
        content.append({"type": "text", "text": "\n".join(hint_lines)})

    if prior_json is not None and notes:
        content.append(
            {
                "type": "text",
                "text": (
                    "You previously produced this JSON for this creature:\n\n"
                    + json.dumps(prior_json, indent=2)
                    + "\n\nRevise it per this note from the user, then return the full updated JSON:\n\n"
                    + notes
                ),
            }
        )

    msg = client.messages.create(
        model=CONVERSION_MODEL,
        max_tokens=MAX_TOKENS,
        system=CONVERSION_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    raw = _strip_json_fences(text)
    parsed = json.loads(raw)
    # Defensive: if the model still returns a list (shared stat-block pages),
    # pick the entry whose name matches target_name, else the first one.
    if isinstance(parsed, list):
        if target_name:
            match = next(
                (c for c in parsed if isinstance(c, dict)
                 and storage.slugify(c.get("name", "")) == storage.slugify(target_name)),
                None,
            )
            if match is not None:
                _normalize_size(match)
                return match
        picked = parsed[0] if parsed else {}
        if isinstance(picked, dict):
            _normalize_size(picked)
        return picked
    if isinstance(parsed, dict):
        _normalize_size(parsed)
    return parsed


def convert_for_entry(
    source_slug: str,
    creature_slug: str,
    target_name: str | None = None,
    setting_hint: str | None = None,
    source_title: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """High-level helper: read manifest, load source pages, optionally include
    prior JSON for a re-run, run conversion, persist the result."""
    image_paths = storage.source_image_paths(source_slug, creature_slug)
    if not image_paths:
        raise FileNotFoundError(
            f"No source-page images found for {source_slug}/{creature_slug}"
        )
    prior = storage.read_creature(source_slug, creature_slug) if notes else None
    result = convert_creature(
        image_paths,
        target_name=target_name,
        setting_hint=setting_hint,
        source_title=source_title,
        notes=notes,
        prior_json=prior,
    )
    storage.write_creature(source_slug, creature_slug, result)
    return result
