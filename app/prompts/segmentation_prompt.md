You are looking at one page from an AD&D monster compendium. Detect two things separately: the creature ENTRIES listed on the page, and the drawn FIGURES (illustrations) on the page. Most pages have more entries than figures — that is expected.

Return strict JSON only — no prose, no markdown fences:

{
  "is_monster_page": true|false,
  "entries": [
    {"name": "<creature name exactly as printed>"}
  ],
  "figures": [
    {"depicts": "<creature name from entries that this drawing depicts>", "bbox": [x0, y0, x1, y1]}
  ]
}

DEFINITIONS:

- An "entry" is a creature with a stat block on this page. A multi-creature stat-block page (one shared stat box covering Hurrum, Critic, Renk, Ock'n) yields four entries — one per creature, in the order their columns appear in the stat block.

- A "figure" is a hand-drawn or painted ILLUSTRATION of a creature: ink line work, pencil shading, painted artwork. Figures depict creatures; they are NOT typography, NOT stat-box rows, NOT the PSIONIC SUMMARY table, NOT page-header logos like "DARK·SUN", NOT decorative rules, NOT the ©TSR footer.

- A figure is OPTIONAL. Most multi-creature pages have ONE figure for one of the variants; the others have no figure at all. Some entries on the page may have no figure. The `figures` array can be empty, can have one element, or can have multiple elements — emit only the figures that are actually drawn on the page.

- ANTI-PATTERN: If you find yourself emitting multiple figure entries with the same or near-identical bbox, STOP. One drawn illustration = one figure entry. Do not list the same illustration under multiple `depicts` names just because several creatures share a stat block. Pick the one creature the figure most clearly depicts (usually the variant whose anatomy matches the drawing) and emit a SINGLE figure entry for it.

- The `depicts` value MUST be a name string that appears in `entries`. If you cannot confidently say which entry the drawing depicts, emit the figure anyway with your best guess. Do NOT invent figures for entries that are not drawn.

NOT A MONSTER PAGE — set is_monster_page=false and emit empty arrays for: table of contents, indices, credits, pure prose / lore pages with no stat block, blank pages.

art_bbox FORMAT — coordinates are normalized 0.0 to 1.0 from the top-left of the page.

CRITICAL — when you DO emit a figure, the bbox MUST fully contain the entire drawn figure with NO clipping. Include head, limbs, tail, wings, weapons, trailing fabric, claws, antennae, mount — everything the artist drew. It is far better to include surrounding text or whitespace inside the box than to clip any part of the figure. When in doubt about the figure's extent, make the box LARGER. Leave a comfortable margin around the figure on all four sides.
