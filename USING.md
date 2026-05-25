# Using the Creatures Converter

A practical guide for running a real conversion session. For installation and dev setup, see [README.md](README.md).

---

## Quick start

```bash
venv\Scripts\activate                  # Windows
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. That's the whole UI.

---

## Stage 1 — Ingest a PDF

1. **Name the PDF deliberately before you upload it.** The filename slug becomes the manual filename in DM CM: `Dark Sun MC1.pdf` → `monster_manual_dark-sun-mc1.js`. Rename now if you want it cleaner — there's no rename feature later.
2. Drag the PDF into the **Compendium PDF** card on the home page.
3. Wait. Segmentation runs Claude vision on every page and can take several minutes for a long compendium. The status line below the upload reports progress.
4. When done, the source appears as a pill below. Click it to see all detected entries.

**If segmentation looks wrong** — wrong page count, missing entries, art bbox over the wrong region — re-upload the PDF. There's no incremental fix; segmentation runs over the whole document each time.

---

## Stage 2 — Per-creature workflow

For each entry, click into the detail page and run these steps in order. None are mandatory; skip ahead if you don't need a step.

### Convert the stat block

- Click **Convert**. Opus 4.7 reads the source pages and emits a 5.5e stat block in ~30 seconds.
- The form fills with the converted values. Everything is editable.
- **If something looks systematically wrong** (CR off, missing Frightful Presence on a dragon, weird habitat), use **Re-run conversion** with a one-line note. The previous JSON is included as context, so the model can correct rather than start over.
- **For prose tweaks** (renaming a trait, fixing flavor), edit the field by hand and hit **Save edits**. Don't re-run conversion for cosmetic stuff — you'll lose your edits.

### Adjust the art reference

The auto-cropped art appears in the left panel. If it's bad:

- **Upload sketch** lets you supply your own crop (paint your own, screenshot from the PDF, anything). Replaces the auto-crop.
- Source pages render on the right at full resolution — click any thumbnail to open the page in a new tab.

### Generate concept art (gpt-image-2)

- Pick a **Setting** (Dark Sun / Ravenloft / Generic Fantasy) — defaults from `creature.setting` if it's set.
- Pick a **Background** (5 per setting). The new **Neutral watercolor** option is the safe default for tiny creatures, oddly-posed creatures, or anything that doesn't fit the landscape backdrops.
- **Mode:**
  - **recreate** — uses your art crop as a pose/design reference. Best when the AD&D art is clear.
  - **describe** — generates from the stat block's `read_aloud` + features. Often cleaner when the source art is muddy.
  - **upload** — bypass generation, drop in any image you've sourced yourself.
- Click **Generate**. You get 4 candidates in ~25 seconds. Click a thumbnail for full-resolution preview.
- Click **Pick this one** on whichever works. The selected image is resized to 1024×1024 webp and stashed as the export-quality file.
- Don't like any of them? Hit **Discard candidates** and re-generate (or tweak the prompt injection text first).

### Approve

- Hit **Approve** at the bottom of the page.
- Status becomes:
  - **image-approved** (green) if you picked a concept image.
  - **text-approved** (amber) if no image yet. The stat block is exported but the webp is skipped. You can come back later, pick an image, and re-approve — the entry just upgrades to green.

**Spot-check the first 3–5 conversions carefully** before trusting the rest. Systematic errors (CR drift, wrong Frightful Presence placement) usually mean the prompt needs tuning — ping Claude with a sample.

---

## Stage 3 — Build the release bundle

When you've approved enough creatures (or all of them), scroll back to the home page.

1. The **Export** card lists every approved creature grouped by source PDF.
2. Each group shows the filename it'll produce (`monster_manual_<source-slug>.js`) and a roster of badges — green if the webp is ready, amber if it's text-only.
3. Hit **Build release bundle**.
4. The status line reports: number of manuals written, files generated, images copied, and any creatures missing concept art.

Output lands in `release/`:

```
release/
├── monster_manual_<source-1>.js
├── monster_manual_<source-2>.js
└── assets/monster_images/
    └── <slug>.webp        (one per approved creature with a picked image)
```

**Re-export at any time.** The `release/` folder is wiped and rebuilt from scratch, so removed approvals don't leave stale files behind.

---

## Stage 4 — Drop it into DM CM

In your DM CM checkout:

1. Copy every `release/monster_manual_*.js` into the DM CM repo root (next to `monsters.js`).
2. Copy every `release/assets/monster_images/*.webp` into `assets/monster_images/` in DM CM.
3. Commit + push (or deploy via Netlify).

DM CM picks up new manuals automatically — each one appends to `window.MONSTER_MANUALS_DATA` on page load. No code changes needed after the initial wiring.

**Before your first real export** — verify the DM CM round-trip with one creature. If you haven't seen one of these files render on the live site yet, do that single-creature test first so a whole compendium isn't blocked on a wiring issue.

**Back up `monsters.js` in DM CM** before pushing your first batch. The converter doesn't touch it, but accidents happen during manual file ops.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Build fails with "Duplicate id" | Two approvals slugify to the same id (e.g. two "Goblin" entries) | Open the loser's `edited/<source>/<slug>.json`, change `id` and `name`, re-export |
| Conversion returns 502 with raw model output | Model emitted unparseable JSON | Re-run conversion. If it happens twice in a row on the same entry, send the response detail to Claude |
| Image generation returns 503 | OpenAI rate limit | Wait ~60 seconds, retry. If persistent, you may be out of quota |
| Background dropdown shows wrong options | Stale Alpine state (should be fixed but if it recurs) | Cycle the Setting dropdown once to force refresh |
| Image candidates don't load (broken thumbnails) | Files written to disk but browser is caching | Hard-reload (`Ctrl+Shift+R`). Cache-bust query strings should prevent this |
| "No converted JSON" error on Approve | You hit Approve without running Convert | Run Convert first |
| Approve produces "text-approved" not "approved" | No final image picked yet | Pick a candidate or upload one, then re-approve |
| Re-running conversion lost my edits | Re-run uses the saved JSON as context but produces a fresh stat block | Save edits to disk before re-running. For pure prose tweaks, edit + Save without re-running |
| Habitat field empty after conversion | Source didn't have an obvious climate/terrain line | Add manually from the 13-value enum in the form |

---

## Recovering from common mistakes

**"I approved a creature, then realized the stat block is wrong."**
Edit the form, hit **Save edits**, hit **Approve** again. Re-approval is idempotent — it just rewrites `edited/<source>/<slug>.json` and copies the current webp.

**"I picked the wrong image."**
Pick a different candidate (or re-generate), then **Approve** again to copy the new webp into the bundle.

**"I want to delete a creature from the bundle."**
Delete `edited/<source>/<slug>.json` and `edited/<source>/<slug>.webp` from disk. Refresh the Export card. Next build won't include it.

**"I want to start fresh for one source."**
Delete `edited/<source>/`. All approvals for that source vanish; the next build won't produce that manual file at all.

**"I broke something in DM CM after pushing."**
Revert the DM CM commit. Your converter state is untouched — re-export and try again.

---

## What's where on disk

```
creature-converter/
├── sources/                          # uploaded PDFs (gitignored)
├── unedited/<source-slug>/           # nested JSON + page/art crops + image candidates
│   ├── _manifest.json                # entry list for this source
│   ├── <slug>.json                   # converted nested stat block
│   ├── <slug>-art.png                # auto-cropped or uploaded art reference
│   ├── <slug>-source-p*.png          # source page rasters
│   ├── <slug>-cand-*.png             # gpt-image-2 candidates
│   └── <slug>.webp                   # picked final image (1024×1024)
├── edited/<source-slug>/             # approved creatures only
│   ├── <slug>.json                   # flat record (DM CM shape)
│   └── <slug>.webp                   # copy of the picked image
└── release/                          # rebuilt fresh on every export
    ├── monster_manual_<source>.js
    └── assets/monster_images/<slug>.webp
```

Anything in `unedited/` is fair game to delete if you want to free space — only `edited/` is the authoritative record of approved work.

---

## Tips that didn't fit elsewhere

- **Re-export every time** even for small changes. Per-source files regenerate atomically and that's the only way to be sure DM CM gets a consistent view.
- **Habitat is multi-select.** A cave-dwelling forest creature should be `["Forest", "Underdark"]`. Cap at 3.
- **"Any" habitat** means the creature is found everywhere — it matches every filter in DM CM. Use sparingly (planar creatures, demons, ubiquitous vermin).
- **`custom: false`** is set on every exported creature. This puts them on equal footing with DM CM's builtin monsters (vs `custom: true` which DM CM uses for transient localStorage entries).
- **Prompts live in `app/prompts/`** as plain markdown. The conversion prompt (`conversion_prompt.md`) is read fresh on every conversion — no restart needed if you tweak it.
