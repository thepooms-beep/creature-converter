"""Slice 4c: bundle every approved creature into a single
`release/manually_entered.js` file plus a `release/assets/monster_images/`
folder.

Per the chosen design, exported records live in their own
`const MANUALLY_ENTERED = [...]` array — separate from DM CM's existing
`MONSTERS_BUILTIN`. The user pastes (or `<script src>`-includes) the
generated file into DM CM and copies the webps into
`assets/monster_images/`. We never touch the user's real monsters.js.

The released file is one minified line, matching the formatting style of
the existing builtin records (`{...},{...}` with no whitespace).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import storage

RELEASE_DIR = storage.ROOT / "release"
RELEASE_JS = RELEASE_DIR / "manually_entered.js"
RELEASE_IMAGES_DIR = RELEASE_DIR / "assets" / "monster_images"


class IdCollisionError(ValueError):
    """Two approved creatures share the same `id` — refuse to export."""


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


def _render_js(records: list[dict[str, Any]]) -> str:
    """One-line `const MANUALLY_ENTERED = [{...},{...}];` matching the
    minified style of the existing builtin records."""
    body = ",".join(
        json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in records
    )
    return f"const MANUALLY_ENTERED = [{body}];"


def _reset_release_dir() -> None:
    """Wipe and recreate the release tree so stale entries from a previous
    export never linger."""
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def export_all() -> dict[str, Any]:
    """Build `release/manually_entered.js` and copy approved webps.

    Returns a summary with counts and the list of exported slugs so the UI
    can show what was bundled.
    """
    records = collect_records()
    _detect_collisions(records)

    _reset_release_dir()

    flat_records = [r for _, _, r in records]
    RELEASE_JS.write_text(_render_js(flat_records), encoding="utf-8")

    images_copied: list[str] = []
    images_missing: list[str] = []
    for source_slug, json_path, rec in records:
        slug = rec["id"]
        src_webp = json_path.with_suffix(".webp")
        if src_webp.exists():
            shutil.copy2(src_webp, RELEASE_IMAGES_DIR / f"{slug}.webp")
            images_copied.append(slug)
        else:
            images_missing.append(slug)

    return {
        "ok": True,
        "release_dir": str(RELEASE_DIR.relative_to(storage.ROOT)),
        "js_file": str(RELEASE_JS.relative_to(storage.ROOT)),
        "record_count": len(flat_records),
        "slugs": [r["id"] for r in flat_records],
        "images_copied": images_copied,
        "images_missing": images_missing,
    }
