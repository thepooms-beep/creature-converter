You are looking at one page from an AD&D monster compendium. Identify monster entries on this page.

A "monster entry" is a creature stat block — it has a creature name as a header and a stat box (CLIMATE/TERRAIN, FREQUENCY, HIT DICE, AC, etc.). Front-matter, table-of-contents, credits, indices, and pure-prose pages are NOT monster entries.

Return strict JSON only — no prose, no markdown fences:

{
  "is_monster_page": true|false,
  "entries": [
    {
      "name": "<creature name exactly as printed>",
      "art_bbox": [x0, y0, x1, y1]
    }
  ]
}

art_bbox is the bounding box of the creature's illustration, in normalized coordinates from the top-left of the page (0.0 to 1.0). If a page has multiple entries (two-column or stacked layouts), list each entry. If an entry has no illustration, omit the art_bbox field. If is_monster_page is false, entries must be [].
