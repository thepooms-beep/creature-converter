"""Re-flatten every approved creature so edited/ matches the latest
flatten_creature() output.

Why this exists: when the flatten layer adds new fields (e.g. the
resistances field added in c0feef6), existing files in edited/ are
stale until each creature is re-approved. Re-approving 76 creatures
through the UI is tedious; this script does the same work in one shot.

Reads each unedited/<source>/<slug>.json, re-runs flatten_creature on
it, and overwrites the corresponding edited/<source>/<slug>.json. The
existing webp in edited/ is preserved (approve_creature only re-copies
it if a newer one exists in unedited/, which is harmless).

Usage (from project root, venv activated):

    python scripts/reflatten_edited.py

WARNING: this overwrites edited/<source>/<slug>.json with the freshly-
flattened version from unedited/. Any direct hand-edits to flat JSON
files in edited/ will be LOST. Hand-edits made via the UI form are
safe — they live in unedited/ and propagate through.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import flatten, storage  # noqa: E402


def main() -> int:
    if not storage.EDITED_DIR.exists():
        print(f"ERROR: {storage.EDITED_DIR} does not exist.")
        return 1

    ok = 0
    fail = 0
    for source_dir in sorted(storage.EDITED_DIR.iterdir()):
        if not source_dir.is_dir():
            continue
        print(f"\n{source_dir.name}:")
        for json_path in sorted(source_dir.glob("*.json")):
            try:
                flatten.approve_creature(source_dir.name, json_path.stem)
                ok += 1
                print(f"  ok  {json_path.stem}")
            except Exception as e:
                fail += 1
                print(f"  FAIL {json_path.stem} -> {type(e).__name__}: {e}")

    print(f"\nDone. {ok} re-flattened, {fail} failed.")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
