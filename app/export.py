"""Slice 4c: bundle every approved creature into one
`release/monster_manual_<source-slug>.js` file per compendium PDF, plus a
shared `release/assets/monster_images/` folder.

Per the chosen design, every exported file contributes to DM CM's
`window.MONSTER_MANUALS_DATA` array via a single `.push(...)` call — so
DM CM can load any number of manual files in any order without them
overwriting each other. The user pastes (or `<script src>`-includes)
each file into DM CM and copies the webps into `assets/monster_images/`.

We never touch DM CM's existing `MONSTERS_BUILTIN`.

File format (note the exact opening + closing lines — DM CM relies on
them):

    (window.MONSTER_MANUALS_DATA = window.MONSTER_MANUALS_DATA || []).push(
    {...record 1...},
    {...record 2...}
    );

One record per line for diff-friendly git history, each record itself
minified to match the formatting style of the existing builtin records.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import storage

RELEASE_DIR = storage.ROOT / "release"
RELEASE_IMAGES_DIR = RELEASE_DIR / "assets" / "monster_images"
MANUAL_FILE_PREFIX = "monster_manual_"
JS_OPENING = "(window.MONSTER_MANUALS_DATA = window.MONSTER_MANUALS_DATA || []).push("
JS_CLOSING = ");"


class IdCollisionError(ValueError):
    """Two approved creatures share the same `id` — refuse to export."""


def manual_file_name(source_slug: str) -> str:
    return f"{MANUAL_FILE_PREFIX}{source_slug}.js"


def collect_records() -> list[tuple[str, Path, dict[str, Any]]]:
    """Walk `edited/` and return (source_slug, json_path, record) for every
    approved creature, in stable order (sorted by source then id)."""
    if not storage.EDITED_DIR.exists():
        return []
    out: list[tuple[str, Path, dict[str, Any]]] = []
    for source_dir in sorted(storage.EDITED_DIR.iterdir()):
        if not source_dir.is_dir():
            continue
        for json_path in sorted(source_dir.glob("*.json")):
            try:
                record = json.loads(json_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Approved file {json_path} is not valid JSON: {e}"
                ) from e
            if not isinstance(record, dict) or not record.get("id"):
                raise ValueError(f"Approved file {json_path} is missing an 'id' field")
            out.append((source_dir.name, json_path, record))
    return out


def group_by_source(
    records: list[tuple[str, Path, dict[str, Any]]],
) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    """Bucket records by source_slug, preserving the order from collect_records."""
    grouped: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for source_slug, path, rec in records:
        grouped.setdefault(source_slug, []).append((path, rec))
    return grouped


def _detect_collisions(
    records: list[tuple[str, Path, dict[str, Any]]],
) -> None:
    seen: dict[str, Path] = {}
    for _, path, rec in records:
        rid = rec["id"]
        if rid in seen:
            raise IdCollisionError(
                f"Duplicate id '{rid}' in {seen[rid]} and {path}"
            )
        seen[rid] = path


def render_manual_js(records: list[dict[str, Any]]) -> str:
    """Wrap minified records in the `window.MONSTER_MANUALS_DATA.push(...)`
    envelope DM CM expects. One record per line, no trailing comma."""
    inner = ",\n".join(
        json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in records
    )
    if not inner:
        # An empty .push() is a no-op in JS but visually odd; keep it
        # consistent so an empty manual still produces a valid file.
        return f"{JS_OPENING}\n{JS_CLOSING}\n"
    return f"{JS_OPENING}\n{inner}\n{JS_CLOSING}\n"


def _reset_release_dir() -> None:
    """Wipe and recreate the release tree so stale entries from a previous
    export never linger."""
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def export_all() -> dict[str, Any]:
    """Build one `release/monster_manual_<source>.js` per source plus copy
    every approved webp into `release/assets/monster_images/`.

    Returns a per-manual summary so the UI can show what was bundled.
    """
    records = collect_records()
    _detect_collisions(records)

    _reset_release_dir()

    grouped = group_by_source(records)
    manuals: list[dict[str, Any]] = []
    images_copied: list[str] = []
    images_missing: list[str] = []

    for source_slug, entries in grouped.items():
        file_name = manual_file_name(source_slug)
        flat_records = [rec for _, rec in entries]
        (RELEASE_DIR / file_name).write_text(
            render_manual_js(flat_records), encoding="utf-8"
        )
        manuals.append(
            {
                "source_slug": source_slug,
                "file_name": file_name,
                "record_count": len(flat_records),
                "slugs": [r["id"] for r in flat_records],
            }
        )
        for path, rec in entries:
            slug = rec["id"]
            src_webp = path.with_suffix(".webp")
            if src_webp.exists():
                shutil.copy2(src_webp, RELEASE_IMAGES_DIR / f"{slug}.webp")
                images_copied.append(slug)
            else:
                images_missing.append(slug)

    return {
        "ok": True,
        "release_dir": str(RELEASE_DIR.relative_to(storage.ROOT)),
        "manuals": manuals,
        "total_records": sum(m["record_count"] for m in manuals),
        "images_copied": images_copied,
        "images_missing": images_missing,
    }
