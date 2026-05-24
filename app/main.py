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

from . import conversion, segmentation, storage

load_dotenv()

app = FastAPI(title="Creatures Converter", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"

storage.ensure_dirs()


@app.on_event("startup")
def on_startup() -> None:
    storage.ensure_dirs()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text())


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


@app.post("/api/monsters-js")
async def upload_monsters_js(file: UploadFile = File(...)) -> dict:
    """Stash the user's current monsters.js. Parser arrives in slice 4."""
    target = storage.SOURCES_DIR / "monsters.js"
    target.write_bytes(await file.read())
    return {"saved_to": str(target.relative_to(storage.ROOT)), "size_bytes": target.stat().st_size}


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


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/unedited", StaticFiles(directory=str(storage.UNEDITED_DIR)), name="unedited")
