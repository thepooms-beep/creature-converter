"""Concept art generation via gpt-image-2.

Parses the verbatim Master Prompt + 4 backgrounds per setting from
app/prompts/image_prompts.md (per the brief — do NOT rewrite these
prompts). Substitutes the Setting fragment into the master and
calls OpenAI's images endpoint.

Three modes:
- recreate: sends the auto-cropped or uploaded sketch as a reference
  via /v1/images/edits. Preserves pose / silhouette.
- describe: no sketch. Swaps the opening "based on the attached
  reference sketch — preserve ... exactly as drawn." clause with a
  one-line description distilled from the converted JSON. Calls
  /v1/images/generations.
- upload: user-provided image; no model call, just normalize to webp.
"""
from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image

from . import storage

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
IMAGE_PROMPTS_FILE = PROMPTS_DIR / "image_prompts.md"

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2")
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "auto"
CANDIDATES_PER_CALL = 2

# Two image tiers per user spec:
# - Candidates and the hi-res archive copy: raw PNG (~2 MB) at full quality
#   from gpt-image-2, so the lightbox preview shows true detail and the
#   archival copy is bit-perfect.
# - Export copy for DM CM (Monsterpedia thumbnail loads): webp targeting
#   ~100 KB. SOFT target — stops degrading quality below webp 60 even if
#   the result is over 100 KB, to keep images looking decent.
EXPORT_TARGET_KB = 100
EXPORT_QUALITY_FLOOR = 60

# Per brief: "based on the attached reference sketch — preserve the
# character's pose, proportions, silhouette, gear, and key design details
# exactly as drawn." This clause appears at the start of every master
# prompt and gets swapped out in describe mode.
SKETCH_CLAUSE = (
    "based on the attached reference sketch — preserve the character's "
    "pose, proportions, silhouette, gear, and key design details exactly as drawn"
)

# Maps creature.setting values to the keys used in image_prompts.md.
SETTING_TO_KEY: dict[str, str] = {
    "athas": "dark_sun",
    "dark sun": "dark_sun",
    "dark sun (athas)": "dark_sun",
    "ravenloft": "ravenloft",
}


def _load_anthropic_unused() -> None:
    """No-op marker so a code search for anthropic finds nothing here."""


def _load_openai_client():
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env or export it before running image generation."
        )
    from openai import OpenAI
    return OpenAI()


# ---------------------------------------------------------------------------
# Parse image_prompts.md once into a structured catalog.
# ---------------------------------------------------------------------------

def _parse_prompts_md() -> dict[str, dict[str, Any]]:
    """Return {setting_key: {label, master, backgrounds: [{key, label, text}, ...]}}."""
    text = IMAGE_PROMPTS_FILE.read_text(encoding="utf-8")

    # Split by H1 (single # at line start). Each H1 starts a setting section.
    sections = re.split(r"(?m)^# ", text)[1:]
    out: dict[str, dict[str, Any]] = {}
    for sec in sections:
        header_line, body = sec.split("\n", 1)
        # Strip any emoji at the start, e.g. "🏜️ Dark Sun (Athas)"
        label = re.sub(r"^[^A-Za-z]+", "", header_line).strip()
        if not label:
            continue
        setting_key = _to_key(label)
        master_match = re.search(
            r"## Master Prompt\s*\n+```text\n(.*?)```",
            body, re.DOTALL,
        )
        if not master_match:
            continue
        master = master_match.group(1).strip()

        backgrounds: list[dict[str, str]] = []
        for bg_match in re.finditer(
            r"### \d+ — ([^\n]+)\n+```text\n(.*?)```",
            body, re.DOTALL,
        ):
            bg_label = bg_match.group(1).strip()
            bg_text = bg_match.group(2).strip()
            backgrounds.append(
                {"key": _to_key(bg_label), "label": bg_label, "text": bg_text}
            )

        out[setting_key] = {"label": label, "master": master, "backgrounds": backgrounds}
    return out


def _to_key(s: str) -> str:
    # Drop parenthetical aliases first: "Dark Sun (Athas)" -> "Dark Sun".
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s.lower()).strip("_")
    return s


# Cached at import; image_prompts.md is read-only once the app starts.
PROMPT_CATALOG = _parse_prompts_md()


def list_settings() -> list[dict[str, Any]]:
    """Public catalog for the UI dropdowns."""
    return [
        {
            "key": k,
            "label": v["label"],
            "backgrounds": [{"key": b["key"], "label": b["label"]} for b in v["backgrounds"]],
        }
        for k, v in PROMPT_CATALOG.items()
    ]


def setting_key_for(creature_setting: str | None) -> str:
    """Map the creature's setting field to one of the catalog keys.
    Athas/Dark Sun -> dark_sun, Ravenloft -> ravenloft, anything else -> generic_fantasy."""
    if not creature_setting:
        return "generic_fantasy"
    return SETTING_TO_KEY.get(creature_setting.strip().lower(), "generic_fantasy")


# ---------------------------------------------------------------------------
# Prompt assembly.
# ---------------------------------------------------------------------------

def _describe_from_creature(creature: dict[str, Any]) -> str:
    """Distill a one-line visual description from the converted JSON, for
    use in describe mode."""
    name = creature.get("name") or "the creature"
    size = creature.get("size") or ""
    ctype = creature.get("type") or "creature"
    read_aloud = (creature.get("read_aloud") or "").strip().strip("*")
    sf_titles = [
        (sf.get("title") or "").strip()
        for sf in (creature.get("salient_features") or [])
        if sf.get("title")
    ]
    parts: list[str] = []
    parts.append(f"a {size} {ctype}".strip().lower())
    if read_aloud:
        parts.append(read_aloud.rstrip("."))
    if sf_titles:
        parts.append("Notable features: " + ", ".join(sf_titles))
    return "; ".join(parts)


def build_prompt(
    setting_key: str,
    background_key: str,
    mode: str,
    creature: dict[str, Any] | None = None,
    injection: str | None = None,
) -> str:
    """Assemble the final gpt-image-2 prompt by substituting the Setting
    fragment into the Master Prompt placeholder, optionally swapping the
    sketch-clause for describe mode, and appending user injection text."""
    setting = PROMPT_CATALOG.get(setting_key)
    if not setting:
        raise ValueError(f"Unknown setting_key {setting_key!r}")
    background = next(
        (b for b in setting["backgrounds"] if b["key"] == background_key),
        None,
    )
    if not background:
        raise ValueError(
            f"Unknown background_key {background_key!r} for setting {setting_key!r}"
        )

    master = setting["master"]

    # Per brief: replace the "[user selects options 1 to 4 below]" placeholder
    # with the chosen background's "Setting:" block. The background text
    # already includes its own Color palette line — do not duplicate.
    placeholder = "Setting: [user selects options 1 to 4 below]"
    if placeholder in master:
        master = master.replace(placeholder, background["text"])

    # Some master prompts also have a standalone "Color palette: [described in
    # Setting options 1 to 4]" line. The background's color palette is already
    # baked in, so remove this scaffold line.
    master = re.sub(
        r"\n+Color palette:\s*\[described in Setting options 1 to 4\]\n*",
        "\n",
        master,
    )

    # Describe mode: replace the opening sketch-fidelity clause with a
    # description derived from the converted JSON.
    if mode == "describe":
        if not creature:
            raise ValueError("describe mode requires the converted creature JSON")
        name = creature.get("name") or "the creature"
        description = _describe_from_creature(creature)
        # The master prompt opens with "Concept art illustration <SKETCH_CLAUSE>."
        # so replace SKETCH_CLAUSE with "of <name>: <description>".
        master = master.replace(
            SKETCH_CLAUSE,
            f"of {name}: {description}",
        )

    if injection:
        injection = injection.strip()
        if injection:
            master = master.rstrip() + "\n\nAdditional direction from user: " + injection

    return master


# ---------------------------------------------------------------------------
# OpenAI calls.
# ---------------------------------------------------------------------------

def _extract_b64(image_obj: Any) -> str | None:
    """gpt-image-1 returns b64_json. dall-e-3 can return url. Handle both."""
    if hasattr(image_obj, "b64_json") and image_obj.b64_json:
        return image_obj.b64_json
    # If only url is provided we'd need to fetch it; for now return None.
    return None


def _normalize_to_png(raw: bytes) -> bytes:
    """Normalize whatever bytes we got to a 1024x1024 RGB PNG (archive tier)."""
    with Image.open(io.BytesIO(raw)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (1024, 1024):
            img = img.resize((1024, 1024), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _compress_to_webp(raw: bytes, target_kb: int = EXPORT_TARGET_KB,
                      quality_floor: int = EXPORT_QUALITY_FLOOR) -> bytes:
    """Compress to webp targeting ~target_kb. Tries descending quality and
    returns the first result that fits the target. If none fit by the time
    we hit quality_floor, returns the quality_floor result anyway so we
    don't degrade past visual usability."""
    with Image.open(io.BytesIO(raw)) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (1024, 1024):
            img = img.resize((1024, 1024), Image.LANCZOS)
        last = b""
        for q in (90, 80, 75, 70, 65, quality_floor):
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=q, method=6)
            last = buf.getvalue()
            if len(last) <= target_kb * 1024:
                return last
        return last  # at quality_floor; may exceed target_kb (soft cap).


def generate_candidates(
    prompt: str,
    mode: str,
    reference_image_path: Path | None = None,
    n: int = CANDIDATES_PER_CALL,
) -> list[bytes]:
    """Call gpt-image-2 and return n candidate images as webp bytes."""
    client = _load_openai_client()
    common_kwargs = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
        "n": n,
    }
    if mode == "recreate":
        if not reference_image_path or not reference_image_path.exists():
            raise FileNotFoundError(
                f"recreate mode requires a reference image; "
                f"{reference_image_path} not found"
            )
        with open(reference_image_path, "rb") as f:
            resp = client.images.edit(image=f, **common_kwargs)
    else:
        # describe mode (no reference image)
        resp = client.images.generate(**common_kwargs)

    out: list[bytes] = []
    for item in resp.data:
        b64 = _extract_b64(item)
        if not b64:
            continue
        raw = base64.b64decode(b64)
        out.append(_normalize_to_png(raw))
    return out


# ---------------------------------------------------------------------------
# Disk persistence for candidates + selection.
# ---------------------------------------------------------------------------

def candidate_path(source_slug: str, creature_slug: str, idx: int) -> Path:
    return storage.UNEDITED_DIR / source_slug / f"{creature_slug}-cand-{idx}.png"


def write_candidates(source_slug: str, creature_slug: str, images: list[bytes]) -> list[Path]:
    """Append candidates after any existing ones, return the new paths.
    Candidates are stored as full-quality PNG for lightbox preview."""
    src_dir = storage.UNEDITED_DIR / source_slug
    src_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(src_dir.glob(f"{creature_slug}-cand-*.png"))
    start = len(existing)
    paths: list[Path] = []
    for i, blob in enumerate(images):
        p = candidate_path(source_slug, creature_slug, start + i)
        p.write_bytes(blob)
        paths.append(p)
    return paths


def list_candidates(source_slug: str, creature_slug: str) -> list[Path]:
    src_dir = storage.UNEDITED_DIR / source_slug
    return sorted(src_dir.glob(f"{creature_slug}-cand-*.png"))


def clear_candidates(source_slug: str, creature_slug: str) -> None:
    for p in list_candidates(source_slug, creature_slug):
        p.unlink(missing_ok=True)


def final_image_path(source_slug: str, creature_slug: str) -> Path:
    """The export-quality webp (~100 KB target) DM CM will load."""
    return storage.UNEDITED_DIR / source_slug / f"{creature_slug}.webp"


def hires_image_path(source_slug: str, creature_slug: str) -> Path:
    """The full-quality PNG archive (~2 MB) for re-derivation later."""
    return storage.UNEDITED_DIR / source_slug / f"{creature_slug}-hires.png"


def _save_dual_output(source_slug: str, creature_slug: str, raw_png_bytes: bytes) -> Path:
    """Save the archival hi-res PNG and the compressed export webp.
    Returns the path to the export webp."""
    hires = hires_image_path(source_slug, creature_slug)
    hires.write_bytes(raw_png_bytes)
    final = final_image_path(source_slug, creature_slug)
    final.write_bytes(_compress_to_webp(raw_png_bytes))
    return final


def promote_candidate(source_slug: str, creature_slug: str, candidate_filename: str) -> Path:
    """Promote the chosen candidate: keep PNG as <slug>-hires.png archive,
    write a compressed <slug>.webp for export, discard other candidates."""
    src_dir = storage.UNEDITED_DIR / source_slug
    src = src_dir / candidate_filename
    if not src.exists():
        raise FileNotFoundError(f"candidate {candidate_filename} not found")
    raw_png = src.read_bytes()
    final = _save_dual_output(source_slug, creature_slug, raw_png)
    clear_candidates(source_slug, creature_slug)
    return final


def save_uploaded_image(source_slug: str, creature_slug: str, raw: bytes) -> Path:
    """Normalize and save a user-uploaded image: hi-res PNG archive plus
    compressed export webp."""
    png_bytes = _normalize_to_png(raw)
    final = _save_dual_output(source_slug, creature_slug, png_bytes)
    clear_candidates(source_slug, creature_slug)
    return final
