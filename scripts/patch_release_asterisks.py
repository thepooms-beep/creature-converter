"""One-off patch: strip '*' from every record in every existing
release/monster_manual_*.js file, without disturbing the envelope or
any other hand-edits.

Use this when you've hand-edited release files and don't want to
regenerate them from edited/ (which would lose those edits). For
unedited/ + edited/ JSONs, use scripts/strip_asterisks_in_place.py.

Usage (from project root, venv activated):

    python scripts/patch_release_asterisks.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import conversion, storage  # noqa: E402


def patch_file(js_path: Path) -> tuple[int, int]:
    """Patch one release file in place. Returns (cleaned, already_clean)."""
    text = js_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines: list[str] = []
    cleaned = 0
    already = 0
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
            if "*" not in candidate:
                already += 1
                new_lines.append(line)
                continue
            cleaned_record = conversion.strip_markdown_asterisks(record)
            rendered = json.dumps(cleaned_record, separators=(",", ":"), ensure_ascii=False)
            if trailing_comma:
                rendered += ","
            new_lines.append(rendered)
            cleaned += 1
        else:
            new_lines.append(line)
    js_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return cleaned, already


def main() -> int:
    release_dir = storage.ROOT / "release"
    if not release_dir.exists():
        print(f"ERROR: {release_dir} does not exist.")
        return 1

    files = sorted(release_dir.glob("monster_manual_*.js"))
    if not files:
        print(f"No monster_manual_*.js files found in {release_dir}")
        return 1

    total_cleaned = 0
    total_already = 0
    for js_path in files:
        cleaned, already = patch_file(js_path)
        total_cleaned += cleaned
        total_already += already
        print(f"  {js_path.name}: cleaned {cleaned}, already-clean {already}")

    print(f"\nDone. Cleaned {total_cleaned} records, {total_already} already-clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
