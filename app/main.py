"""FastAPI app entry point. Slice 1: scaffold + ingest."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import conversion, export, flatten, image_gen, segmentation, storage

load_dotenv()

app = FastAPI(title="Creatures Converter", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"

storage.ensure_dirs()


@app.on_event("startup")
def on_startup() -> None:
    storage.ensure_dirs()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "anthropic_key_loaded": bool(os.getenv("ANTHROPIC_API_KEY")),
        "openai_key_loaded": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.get("/api/sources")
def list_sources() -> dict:
    return {"sources": storage.list_sources()}


@app.get("/api/manifest/{source_slug}")
def get_manifest(source_slug: str) -> dict:
    return storage.read_manifest(source_slug)


@app.post("/api/ingest")
async def ingest_pdf(file: UploadFile = File(...)) -> JSONResponse:
    """PDF upload + vision segmentation. Saves the PDF, rasterizes each page,
    asks Claude vision which pages hold monster entries, then writes per-entry
    page/art crops and a `_manifest.json` row per entry."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload must be a .pdf file")
    source_slug = storage.slugify(Path(file.filename).stem)
    pdf_path = storage.SOURCES_DIR / f"{source_slug}.pdf"
    pdf_path.write_bytes(await file.read())
    try:
        manifest = segmentation.ingest_pdf(pdf_path, source_slug)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return JSONResponse(
        {
            "source_slug": source_slug,
            "saved_to": str(pdf_path.relative_to(storage.ROOT)),
            "page_count": manifest["page_count"],
            "entry_count": len(manifest["entries"]),
            "status": "segmented",
        }
    )


def _find_manifest_entry(source_slug: str, creature_slug: str) -> dict:
    manifest = storage.read_manifest(source_slug)
    for e in manifest.get("entries", []):
        if e.get("slug") == creature_slug:
            return e
    raise HTTPException(status_code=404, detail=f"Entry {creature_slug} not in {source_slug}")


@app.post("/api/convert/{source_slug}/{creature_slug}")
def convert_entry(
    source_slug: str,
    creature_slug: str,
    payload: dict = Body(default={}),
) -> dict:
    """Run AD&D -> 5.5e conversion on the entry's source pages. Pass
    {notes: "..."} in the body to re-run with a revision note; the prior
    JSON is automatically included as context."""
    entry = _find_manifest_entry(source_slug, creature_slug)
    manifest = storage.read_manifest(source_slug)
    notes = (payload.get("notes") or "").strip() or None
    setting_hint = payload.get("setting_hint")
    source_title = manifest.get("pdf_filename")
    try:
        result = conversion.convert_for_entry(
            source_slug,
            creature_slug,
            target_name=entry.get("name"),
            setting_hint=setting_hint,
            source_title=source_title,
            notes=notes,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Conversion returned unparseable JSON: {e}",
        )
    # Mark the manifest entry as converted so the UI can show the right chip.
    entry["status"] = "converted"
    for e in manifest["entries"]:
        if e["slug"] == creature_slug:
            e["status"] = "converted"
    storage.write_manifest(source_slug, manifest)
    return result


@app.get("/api/creature/{source_slug}/{creature_slug}")
def get_creature(source_slug: str, creature_slug: str) -> JSONResponse:
    _find_manifest_entry(source_slug, creature_slug)
    data = storage.read_creature(source_slug, creature_slug)
    if data is None:
        raise HTTPException(status_code=404, detail="Not converted yet")
    return JSONResponse(data)


@app.put("/api/creature/{source_slug}/{creature_slug}")
def put_creature(source_slug: str, creature_slug: str, data: dict = Body(...)) -> dict:
    _find_manifest_entry(source_slug, creature_slug)
    storage.write_creature(source_slug, creature_slug, data)
    return {"ok": True}


@app.post("/api/upload-art/{source_slug}/{creature_slug}")
async def upload_art(
    source_slug: str, creature_slug: str, file: UploadFile = File(...)
) -> dict:
    """Replace the auto-cropped art with a manually-cropped sketch.
    Accepts any common image format; written as <slug>-art.png."""
    entry = _find_manifest_entry(source_slug, creature_slug)
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read image: {e}")
    if img.mode != "RGB":
        img = img.convert("RGB")
    target = storage.creature_art_path(source_slug, creature_slug)
    img.save(target, format="PNG")
    # Reflect the upload in the manifest so the grid shows it.
    manifest = storage.read_manifest(source_slug)
    for e in manifest["entries"]:
        if e["slug"] == creature_slug:
            e["art_image"] = target.name
            e["art_bbox"] = None
            e["art_uploaded"] = True
    storage.write_manifest(source_slug, manifest)
    return {"ok": True, "art_image": target.name}


@app.get("/api/image-prompts")
def image_prompts_catalog() -> dict:
    """Settings + backgrounds the UI shows in the concept-art dropdowns."""
    return {"settings": image_gen.list_settings()}


def _set_entry_status(source_slug: str, creature_slug: str, status: str) -> None:
    manifest = storage.read_manifest(source_slug)
    for e in manifest.get("entries", []):
        if e.get("slug") == creature_slug:
            e["status"] = status
    storage.write_manifest(source_slug, manifest)


@app.post("/api/image/{source_slug}/{creature_slug}")
def generate_image(
    source_slug: str,
    creature_slug: str,
    payload: dict = Body(...),
) -> dict:
    """Generate gpt-image-2 candidates.
    Body: {mode: "recreate"|"describe", setting_key, background_key, injection?, n?}.
    Returns the candidate filenames (writes them to disk; the UI loads
    them via /unedited/<source>/<file>)."""
    entry = _find_manifest_entry(source_slug, creature_slug)
    mode = payload.get("mode") or "recreate"
    setting_key = payload.get("setting_key")
    background_key = payload.get("background_key")
    injection = (payload.get("injection") or "").strip() or None
    n = int(payload.get("n") or image_gen.CANDIDATES_PER_CALL)
    if not setting_key or not background_key:
        raise HTTPException(status_code=400, detail="setting_key and background_key are required")

    creature = storage.read_creature(source_slug, creature_slug)
    if mode == "describe" and not creature:
        raise HTTPException(
            status_code=400,
            detail="describe mode needs a converted creature JSON. Run Convert first.",
        )

    try:
        prompt = image_gen.build_prompt(
            setting_key=setting_key,
            background_key=background_key,
            mode=mode,
            creature=creature,
            injection=injection,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ref_path: Path | None = None
    if mode == "recreate":
        ref_path = storage.creature_art_path(source_slug, creature_slug)
        if not ref_path.exists():
            raise HTTPException(
                status_code=400,
                detail=(
                    "recreate mode needs an art file on disk. Either Upload "
                    "sketch first, or switch to describe mode."
                ),
            )

    try:
        blobs = image_gen.generate_candidates(
            prompt=prompt, mode=mode, reference_image_path=ref_path, n=n,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        # OpenAI SDK errors land here; surface message for diagnosis.
        raise HTTPException(status_code=502, detail=f"Image generation failed: {e}")

    paths = image_gen.write_candidates(source_slug, creature_slug, blobs)
    return {
        "candidates": [p.name for p in image_gen.list_candidates(source_slug, creature_slug)],
        "prompt": prompt,
        "added": [p.name for p in paths],
    }


@app.get("/api/image/{source_slug}/{creature_slug}/candidates")
def list_image_candidates(source_slug: str, creature_slug: str) -> dict:
    _find_manifest_entry(source_slug, creature_slug)
    paths = image_gen.list_candidates(source_slug, creature_slug)
    final = image_gen.final_image_path(source_slug, creature_slug)
    return {
        "candidates": [p.name for p in paths],
        "final": final.name if final.exists() else None,
    }


@app.post("/api/image/{source_slug}/{creature_slug}/pick")
def pick_candidate(
    source_slug: str,
    creature_slug: str,
    payload: dict = Body(...),
) -> dict:
    _find_manifest_entry(source_slug, creature_slug)
    candidate = payload.get("candidate")
    if not candidate:
        raise HTTPException(status_code=400, detail="`candidate` filename required")
    try:
        final = image_gen.promote_candidate(source_slug, creature_slug, candidate)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _set_entry_status(source_slug, creature_slug, "image-approved")
    return {"ok": True, "final": final.name}


@app.post("/api/image/{source_slug}/{creature_slug}/upload")
async def upload_final_image(
    source_slug: str, creature_slug: str, file: UploadFile = File(...)
) -> dict:
    """Upload a manually-sourced concept image; resized to 1024x1024 webp."""
    _find_manifest_entry(source_slug, creature_slug)
    raw = await file.read()
    try:
        final = image_gen.save_uploaded_image(source_slug, creature_slug, raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read image: {e}")
    _set_entry_status(source_slug, creature_slug, "image-approved")
    return {"ok": True, "final": final.name}


@app.post("/api/image/{source_slug}/{creature_slug}/clear")
def clear_image_candidates(source_slug: str, creature_slug: str) -> dict:
    _find_manifest_entry(source_slug, creature_slug)
    image_gen.clear_candidates(source_slug, creature_slug)
    return {"ok": True}


@app.post("/api/approve/{source_slug}/{creature_slug}")
def approve_creature(source_slug: str, creature_slug: str) -> dict:
    """Flatten the nested JSON to the monsters.js shape and move it into
    edited/<source>/. Also copies the export-quality webp if one exists.
    Status: 'approved' when both text and image are ready, otherwise
    'text-approved' (image still pending)."""
    entry = _find_manifest_entry(source_slug, creature_slug)
    try:
        result = flatten.approve_creature(source_slug, creature_slug)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_status = "approved" if result["image_copied"] else "text-approved"
    _set_entry_status(source_slug, creature_slug, new_status)
    result["status"] = new_status
    return result


@app.get("/api/export/summary")
def export_summary() -> dict:
    """Pending approvals grouped by source, with the manual filename each
    group will produce on export and per-creature image status."""
    records = export.collect_records()
    grouped = export.group_by_source(records)
    manuals = [
        {
            "source_slug": src,
            "file_name": export.manual_file_name(src),
            "count": len(entries),
            "entries": [
                {
                    "slug": rec["id"],
                    "name": rec.get("name", ""),
                    "has_image": path.with_suffix(".webp").exists(),
                }
                for path, rec in entries
            ],
        }
        for src, entries in grouped.items()
    ]
    return {"manuals": manuals, "total_count": len(records)}


@app.post("/api/export")
def export_release() -> dict:
    """Build release/manually_entered.js + release/assets/monster_images/.
    Refuses to write if two approved records share an id."""
    try:
        return export.export_all()
    except export.IdCollisionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/edited/{source_slug}/{creature_slug}")
def get_edited(source_slug: str, creature_slug: str) -> JSONResponse:
    """Return the flat record currently in edited/, if any. Used by the UI
    to show whether the creature has been approved."""
    _find_manifest_entry(source_slug, creature_slug)
    p = flatten.edited_dir(source_slug) / f"{creature_slug}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not approved yet")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/unedited", StaticFiles(directory=str(storage.UNEDITED_DIR)), name="unedited")
