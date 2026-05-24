# Creatures Converter

A local web app that converts AD&D 2e monster entries (from scanned PDF compendiums) into D&D 2024 ("5.5e") stat blocks compatible with the DM Campaign Manager's `monsters.js` database, and generates matching concept art via `gpt-image-2`.

This is a **standalone sister app** to `dm-npc-creator`. It runs entirely on your machine — no GitHub push, no cloud storage. The end product is two artifacts you upload manually to the DM CM repo:
1. An updated `monsters.js`
2. A folder of `.webp` images

## One-time setup

You need Python 3.11+ and the system tool `poppler-utils` (for rasterizing PDFs).

### macOS

```bash
brew install poppler
```

### Ubuntu / Debian

```bash
sudo apt install poppler-utils
```

### Then, in this folder

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then edit `.env` and paste your API keys
```

## Running

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Then open <http://localhost:8000> in your browser.

## How a session works

1. **Upload your current `monsters.js`** — sets the base for the final export.
2. **Upload a compendium PDF** — the app segments it into per-creature page crops and extracts the creature art from each page.
3. **For each creature**: convert the stat block to 5.5e, edit if needed, generate or upload an image, then approve.
4. **Hit Export** — produces `output/monsters.js` (your existing records + new ones) and `output/monster_images/<slug>.webp`. Drop these into the DM CM repo manually.

## Folder layout

```
creature-converter/
├── app/                            # FastAPI backend + static UI
├── sources/                        # uploaded PDFs + uploaded monsters.js (gitignored)
├── unedited/<source-slug>/         # nested JSON + page/art crops (gitignored)
├── edited/<source-slug>/           # flat JSON + .webp (gitignored)
└── output/                         # final monsters.js + image folder (gitignored)
```
