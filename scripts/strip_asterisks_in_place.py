"""One-off patch: strip '*' characters from every string in every nested
JSON in unedited/ and every flat JSON in edited/.

The conversion model sometimes emits markdown emphasis (*italic*,
**bold**) in prose fields, but DM CM has no markdown processor — the
asterisks render literally. Fixed at conversion + flatten time, but
legacy data on disk still has them. This script cleans both unedited/
(so the form shows clean text on re-open) and edited/ (so the next
per-manual build produces a clean release file).

For hand-edited release/*.js files, use scripts/patch_release_asterisks.py
instead — that one preserves the file's surrounding envelope and
respects hand-edits.

Usage (from project root, venv activated):

    python scripts/strip_asterisks_in_place.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import conversion, storage  # noqa: E402


def strip_dir(dir_path: Path) -> tuple[int, int]:
    """Returns (cleaned, untouched). Files with no asterisks are
    rewritten as no-ops so the output is deterministic."""
    cleaned = 0
    untouched = 0
    if not dir_path.exists():
        return 0, 0
    for source_dir in sorted(dir_path.iterdir()):
        if not source_dir.is_dir():
            continue
        for path in sorted(source_dir.glob("*.json")):
            if path.name == "_manifest.json":
                continue
            text = path.read_text(encoding="utf-8")
            if "*" not in text:
                untouched += 1
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                print(f"  skip (bad JSON): {path} ({e})")
                continue
            cleaned_data = conversion.strip_markdown_asterisks(data)
            # Preserve indentation style of the original (2-space, per the
            # rest of the codebase).
            path.write_text(
                json.dumps(cleaned_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            cleaned += 1
    return cleaned, untouched


def main() -> int:
    print("Stripping asterisks from unedited/...")
    u_cleaned, u_untouched = strip_dir(storage.UNEDITED_DIR)
    print(f"  cleaned {u_cleaned}, already-clean {u_untouched}")

    print("Stripping asterisks from edited/...")
    e_cleaned, e_untouched = strip_dir(storage.EDITED_DIR)
    print(f"  cleaned {e_cleaned}, already-clean {e_untouched}")

    print(f"\nDone. Total: cleaned {u_cleaned + e_cleaned}, already-clean {u_untouched + e_untouched}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
