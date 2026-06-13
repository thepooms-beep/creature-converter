"""One-off patch: inject the missing 'resistances' field into already-
hand-edited release/monster_manual_*.js files without disturbing any
other edits.

Why this exists: the flatten layer used to drop damage_resistances on
the floor (fixed in c0feef6). If you've already exported manual files
and hand-edited them afterwards, re-running the converter's
Build release bundle would wipe those edits. This script patches the
existing release files in place instead.

Usage (from the project root, with venv activated):

    python scripts/patch_release_resistances.py

For every record in every release/monster_manual_*.js, looks up its
matching nested JSON in unedited/ by id (via slugify(name) — the same
mapping flatten_creature uses), reads damage_resistances, and inserts a
"resistances": "<comma-joined>" field right before "immunities". If the
record already has "resistances", leaves it alone.

Records whose id can't be matched in unedited/ get "resistances": ""
(safe default — better than skipping, since an empty field is harmless
to DM CM and signals the operator that no resistances were found).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to sys.path so we can import the app package when
# invoked from scripts/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import storage  # noqa: E402


def build_id_lookup() -> dict[str, list[str]]:
    """Walk every nested creature JSON and map slugify(name) -> damage_resistances."""
    lookup: dict[str, list[str]] = {}
    if not storage.UNEDITED_DIR.exists():
        return lookup
    for source_dir in storage.UNEDITED_DIR.iterdir():
        if not source_dir.is_dir():
            continue
        for path in source_dir.glob("*.json"):
            if path.name == "_manifest.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"  skip (bad JSON): {path}")
                continue
            if not isinstance(data, dict):
                continue
            name = (data.get("name") or "").strip()
            if not name:
                continue
            rid = storage.slugify(name)
            lookup[rid] = data.get("damage_resistances") or []
    return lookup


def inject_resistances(record: dict, value: str) -> dict:
    """Return a new dict with 'resistances' inserted just before
    'immunities' (or appended if there's no immunities key)."""
    out: dict = {}
    inserted = False
    for k, v in record.items():
        if k == "immunities" and not inserted:
            out["resistances"] = value
            inserted = True
        out[k] = v
    if not inserted:
        out["resistances"] = value
    return out


def patch_file(js_path: Path, lookup: dict[str, list[str]]) -> tuple[int, int]:
    """Patch one release file in place. Returns (patched, already_had)."""
    text = js_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines: list[str] = []
    patched = 0
    already_had = 0
    for line in lines:
        trailing_comma = line.endswith(",")
        candidate = line[:-1] if trailing_comma else line
        candidate = candidate.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                record = json.loads(candidate)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue
            if "resistances" in record:
                already_had += 1
                new_lines.append(line)
                continue
            rid = record.get("id", "")
            res_list = lookup.get(rid, [])
            res_str = ", ".join(res_list)
            patched_record = inject_resistances(record, res_str)
            rendered = json.dumps(patched_record, separators=(",", ":"), ensure_ascii=False)
            if trailing_comma:
                rendered += ","
            new_lines.append(rendered)
            patched += 1
        else:
            new_lines.append(line)
    js_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return patched, already_had


def main() -> int:
    release_dir = storage.ROOT / "release"
    if not release_dir.exists():
        print(f"ERROR: {release_dir} does not exist. Build release bundle first.")
        return 1

    print("Building id -> damage_resistances lookup from unedited/...")
    lookup = build_id_lookup()
    with_res = sum(1 for v in lookup.values() if v)
    print(f"  found {len(lookup)} nested creatures, {with_res} with non-empty resistances\n")

    files = sorted(release_dir.glob("monster_manual_*.js"))
    if not files:
        print(f"No monster_manual_*.js files found in {release_dir}")
        return 1

    total_patched = 0
    total_already = 0
    for js_path in files:
        patched, already_had = patch_file(js_path, lookup)
        total_patched += patched
        total_already += already_had
        print(f"  {js_path.name}: patched {patched}, already had {already_had}")

    print(f"\nDone. Patched {total_patched} records, {total_already} already had resistances.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
