"""PDF segmentation: rasterize pages, call Claude vision per page, crop art."""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image
from pdf2image import convert_from_path

from . import storage

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SEG_PROMPT = (PROMPTS_DIR / "segmentation_prompt.md").read_text()

PAGE_DPI = 150
# Haiku 4.5 is fast + cheap and handles structured-output vision well.
# Override with SEGMENTATION_MODEL env var if needed.
VISION_MODEL = os.getenv("SEGMENTATION_MODEL", "claude-haiku-4-5-20251001")


def _load_anthropic_client():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env or export it before running ingest."
        )
    from anthropic import Anthropic
    return Anthropic()


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _strip_json_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def segment_page(client, page_img: Image.Image) -> dict[str, Any]:
    """Call Claude vision on one page; return parsed JSON {is_monster_page, entries}."""
    img_b64 = base64.standard_b64encode(_png_bytes(page_img)).decode("ascii")
    msg = client.messages.create(
        model=VISION_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                    },
                    {"type": "text", "text": SEG_PROMPT},
                ],
            }
        ],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    raw = _strip_json_fences(text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"is_monster_page": False, "entries": [], "_parse_error": str(e), "_raw": raw[:500]}


def _crop_art(page_img: Image.Image, bbox: list[float]) -> Image.Image | None:
    if not bbox or len(bbox) != 4:
        return None
    w, h = page_img.size
    x0, y0, x1, y1 = bbox
    if not all(0.0 <= v <= 1.0 for v in bbox) or x1 <= x0 or y1 <= y0:
        return None
    return page_img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))


def _unique_slug(used: set[str], base: str) -> str:
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    slug = f"{base}-{i}"
    used.add(slug)
    return slug


def ingest_pdf(pdf_path: Path, source_slug: str) -> dict[str, Any]:
    """Rasterize PDF, segment each page, write crops + manifest. Returns the manifest."""
    client = _load_anthropic_client()

    source_dir = storage.UNEDITED_DIR / source_slug
    source_dir.mkdir(parents=True, exist_ok=True)

    pages = convert_from_path(str(pdf_path), dpi=PAGE_DPI)

    entries: list[dict[str, Any]] = []
    used_slugs: set[str] = set()

    for page_idx, page_img in enumerate(pages, start=1):
        result = segment_page(client, page_img)
        if not result.get("is_monster_page"):
            continue
        for entry in result.get("entries", []):
            name = (entry.get("name") or "").strip()
            if not name:
                continue
            slug = _unique_slug(used_slugs, storage.slugify(name))

            page_path = source_dir / f"{slug}-source.png"
            page_img.save(page_path, format="PNG")

            art_path: Path | None = None
            art_crop = _crop_art(page_img, entry.get("art_bbox"))
            if art_crop is not None:
                art_path = source_dir / f"{slug}-art.png"
                art_crop.save(art_path, format="PNG")

            entries.append(
                {
                    "slug": slug,
                    "name": name,
                    "page": page_idx,
                    "source_image": page_path.name,
                    "art_image": art_path.name if art_path else None,
                    "art_bbox": entry.get("art_bbox"),
                    "status": "unedited",
                }
            )

    manifest = {
        "source_slug": source_slug,
        "pdf_filename": pdf_path.name,
        "page_count": len(pages),
        "entries": entries,
    }
    storage.write_manifest(source_slug, manifest)
    return manifest
