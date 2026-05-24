"""Folder + manifest helpers. Folder-as-database, per the brief."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
UNEDITED_DIR = ROOT / "unedited"
EDITED_DIR = ROOT / "edited"
OUTPUT_DIR = ROOT / "output"


def ensure_dirs() -> None:
    for d in (SOURCES_DIR, UNEDITED_DIR, EDITED_DIR, OUTPUT_DIR, OUTPUT_DIR / "monster_images"):
        d.mkdir(parents=True, exist_ok=True)


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def manifest_path(source_slug: str) -> Path:
    return UNEDITED_DIR / source_slug / "_manifest.json"


def read_manifest(source_slug: str) -> dict[str, Any]:
    p = manifest_path(source_slug)
    if not p.exists():
        return {"source_slug": source_slug, "entries": []}
    return json.loads(p.read_text())


def write_manifest(source_slug: str, manifest: dict[str, Any]) -> None:
    p = manifest_path(source_slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2))


def list_sources() -> list[str]:
    if not UNEDITED_DIR.exists():
        return []
    return sorted(p.name for p in UNEDITED_DIR.iterdir() if p.is_dir())
