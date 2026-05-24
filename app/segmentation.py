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
SEG_PROMPT = (PROMPTS_DIR / "segmentation_prompt.md").read_text(encoding="utf-8")

PAGE_DPI = 150
# Haiku 4.5 is fast + cheap and handles structured-output vision well.
# Override with SEGMENTATION_MODEL env var if needed.
VISION_MODEL = os.getenv("SEGMENTATION_MODEL", "claude-haiku-4-5-20251001")
# Belt-and-suspenders: pad each art bbox by this fraction of page width/height
# before cropping, so thin extremities (tails, wings, weapons) the model missed
# still land inside the crop. gpt-image-2 ignores stray text but suffers from
# clipped figures, so we err toward more margin.
ART_BBOX_PAD = 0.06


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
        temperature=0,
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
    x0 = max(0.0, x0 - ART_BBOX_PAD)
    y0 = max(0.0, y0 - ART_BBOX_PAD)
    x1 = min(1.0, x1 + ART_BBOX_PAD)
    y1 = min(1.0, y1 + ART_BBOX_PAD)
    return page_img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))


def _looks_like_page_banner(bbox: list[float]) -> bool:
    """Reject bboxes that are clearly the DARK·SUN / RAVENLOFT page header logo
    or other top/bottom decorative bands: top-anchored, short, wide."""
    x0, y0, x1, y1 = bbox
    height = y1 - y0
    return y0 < 0.1 and height < 0.25


def _dedupe_figures(figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop figures that are likely page banners, and collapse duplicates.
    The model occasionally emits one figure per stat-block entry even when
    only one illustration exists on the page; this filters both."""
    kept: list[dict[str, Any]] = []
    for fig in figures:
        bbox = fig.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        if _looks_like_page_banner(bbox):
            continue
        is_dup = any(
            all(abs(bbox[i] - k["bbox"][i]) < 0.05 for i in range(4))
            for k in kept
        )
        if not is_dup:
            kept.append(fig)
    return kept


def _bbox_area(bbox: list[float] | None) -> float:
    if not bbox or len(bbox) != 4:
        return 0.0
    x0, y0, x1, y1 = bbox
    return max(0.0, (x1 - x0) * (y1 - y0))


def ingest_pdf(pdf_path: Path, source_slug: str) -> dict[str, Any]:
    """Rasterize PDF, segment each page, merge same-name entries across pages,
    write source crops + best art crop, and emit the manifest."""
    client = _load_anthropic_client()

    source_dir = storage.UNEDITED_DIR / source_slug
    source_dir.mkdir(parents=True, exist_ok=True)

    poppler_path = os.getenv("POPPLER_PATH") or None
    pages = convert_from_path(str(pdf_path), dpi=PAGE_DPI, poppler_path=poppler_path)

    # Pass 1: segment each page. Entries and figures are detected separately
    # so we never invent a figure for an entry that wasn't drawn.
    raw_hits: dict[str, list[dict[str, Any]]] = {}
    for page_idx, page_img in enumerate(pages, start=1):
        result = segment_page(client, page_img)
        if not result.get("is_monster_page"):
            continue
        entries_on_page = result.get("entries", [])
        figures_on_page = _dedupe_figures(result.get("figures", []))
        # Map slug -> first matching figure bbox on this page.
        figure_by_slug: dict[str, list[float]] = {}
        for fig in figures_on_page:
            depicts = (fig.get("depicts") or "").strip()
            bbox = fig.get("bbox")
            if not depicts or not bbox:
                continue
            figure_by_slug.setdefault(storage.slugify(depicts), bbox)
        for entry in entries_on_page:
            name = (entry.get("name") or "").strip()
            if not name:
                continue
            slug = storage.slugify(name)
            raw_hits.setdefault(slug, []).append(
                {
                    "page": page_idx,
                    "page_img": page_img,
                    "name": name,
                    "bbox": figure_by_slug.get(slug),
                }
            )

    # Pass 2: merge hits per slug. Save every page the creature appears on,
    # and crop the art from the page whose bbox covers the largest area.
    entries: list[dict[str, Any]] = []
    for slug, hits in raw_hits.items():
        name = hits[0]["name"]
        source_images: list[str] = []
        seen_pages: set[int] = set()
        for h in sorted(hits, key=lambda x: x["page"]):
            if h["page"] in seen_pages:
                continue
            seen_pages.add(h["page"])
            sp = source_dir / f"{slug}-source-p{h['page']}.png"
            h["page_img"].save(sp, format="PNG")
            source_images.append(sp.name)

        best_hit = max(hits, key=lambda h: _bbox_area(h.get("bbox")))
        art_image_name: str | None = None
        art_bbox = None
        if _bbox_area(best_hit.get("bbox")) > 0:
            art_crop = _crop_art(best_hit["page_img"], best_hit["bbox"])
            if art_crop is not None:
                ap = source_dir / f"{slug}-art.png"
                art_crop.save(ap, format="PNG")
                art_image_name = ap.name
                art_bbox = best_hit["bbox"]

        entries.append(
            {
                "slug": slug,
                "name": name,
                "pages": sorted(seen_pages),
                "source_images": source_images,
                "art_image": art_image_name,
                "art_bbox": art_bbox,
                "status": "unedited",
            }
        )

    entries.sort(key=lambda e: (e["pages"][0] if e["pages"] else 0, e["slug"]))

    manifest = {
        "source_slug": source_slug,
        "pdf_filename": pdf_path.name,
        "page_count": len(pages),
        "entries": entries,
    }
    storage.write_manifest(source_slug, manifest)
    return manifest
