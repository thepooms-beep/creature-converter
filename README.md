# Creatures Converter

A local web app that converts AD&D 2e monster entries (from scanned PDF compendiums) into D&D 2024 ("5.5e") stat blocks compatible with the DM Campaign Manager's `monsters.js` database, and generates matching concept art via `gpt-image-2`.

This is a **standalone sister app** to `dm-npc-creator`. It runs entirely on your machine — no cloud storage. The end product is a bundle in `release/` that you copy into the DM CM repo by hand.

## How records reach DM CM

Each compendium PDF you ingest becomes its own manual file in DM CM, named `monster_manual_<source-slug>.js`. Every manual file uses the same envelope:

```js
(window.MONSTER_MANUALS_DATA = window.MONSTER_MANUALS_DATA || []).push(
{...record 1...},
{...record 2...}
);
```

DM CM loads every `monster_manual_*.js` it finds; each file appends its records to the shared `window.MONSTER_MANUALS_DATA` array. This means:

- You can keep adding new manual files over time without touching the existing ones.
- DM CM's `MONSTERS_BUILTIN` is never modified — your converted creatures live alongside the built-ins.
- Re-exporting wipes and regenerates every `monster_manual_*.js` in `release/`. Approvals you've removed from `edited/` won't leave stale files behind.

## One-time setup

You need Python 3.11+ and `poppler` (for rasterizing PDFs).

**Windows:** download a poppler build (e.g. from the `oschwartz10612/poppler-windows` releases) and unzip somewhere stable. Set `POPPLER_PATH` in `.env` to its `bin/` folder.

**macOS:** `brew install poppler`

**Ubuntu / Debian:** `sudo apt install poppler-utils`

Then in the project folder:

```bash
python -m venv venv
venv\Scripts\activate         # Windows
# source venv/bin/activate    # macOS / Linux
pip install -r requirements.txt
copy .env.example .env        # Windows  (cp on macOS / Linux)
# edit .env and paste your ANTHROPIC_API_KEY + OPENAI_API_KEY
```

## Daily startup — after you've closed everything

Open a terminal in the project folder (`C:\Users\oscar\Documents\creature-converter`) and run:

```bash
venv\Scripts\activate
uvicorn app.main:app --reload
```

Then open <http://localhost:8000> in your browser. That's it.

To stop: `Ctrl+C` in the terminal, close the browser tab.

## Pulling updates from Claude

When Claude works on the app in a cloud session, the changes land on a branch (e.g. `claude/beautiful-knuth-4K6nz`) in your GitHub repo — not on your laptop. To see them locally:

```bash
git fetch origin
git checkout claude/beautiful-knuth-4K6nz    # or whatever branch Claude used
git pull
```

Then restart uvicorn (`Ctrl+C`, then `uvicorn app.main:app --reload`) and hard-reload the browser (`Ctrl+Shift+R`). Verify with `git log --oneline -3` — the latest commit should be at the top.

When you're happy with a branch, merge it into `main`:

```bash
git checkout main
git merge claude/beautiful-knuth-4K6nz
git push
```

## How a session works

1. **Upload a compendium PDF** — Claude vision segments it into per-creature page crops and extracts the creature art from each page.
2. **For each creature:**
   - Convert the AD&D stat block to 5.5e (Opus 4.7).
   - Edit any field in the form. Re-run conversion with notes if needed.
   - Generate concept art (gpt-image-2 in recreate or describe mode) or upload your own. Pick a candidate.
   - Hit **Approve** — flattens the JSON to the `monsters.js` shape and copies the chosen webp into `edited/`.
3. **Hit Build release bundle** (top of the home page) — for each source slug with approved creatures, writes `release/monster_manual_<source>.js` in the `window.MONSTER_MANUALS_DATA.push(...)` format DM CM expects, plus `release/assets/monster_images/<slug>.webp` for every approved creature. Copy these into your DM CM checkout by hand.

The Export card warns about any approved creatures that don't yet have concept art (amber badge) and refuses to build if two approvals collide on the same id.

## Folder layout

```
creature-converter/
├── app/                            # FastAPI backend + static UI
├── sources/                        # uploaded PDFs (gitignored)
├── unedited/<source-slug>/         # nested JSON + page/art crops + candidates (gitignored)
├── edited/<source-slug>/           # approved flat JSON + final webp (gitignored)
└── release/                        # built export bundle (gitignored)
    ├── monster_manual_<source>.js  # one file per compendium PDF
    └── assets/monster_images/
```

## Tests

```bash
python -m pytest tests/
```

51 tests covering the flatten transformer (every rule in the brief) and the release-bundle exporter (`.push()` envelope, per-source file splitting, collision detection, missing-image reporting, stale cleanup).
