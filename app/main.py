"""FastAPI app entry point. Slice 1: scaffold + ingest."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import storage

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
    """PDF upload + vision segmentation. Wired in slice 1b once the segmentation prompt is finalized."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload must be a .pdf file")
    source_slug = storage.slugify(Path(file.filename).stem)
    pdf_path = storage.SOURCES_DIR / f"{source_slug}.pdf"
    pdf_path.write_bytes(await file.read())
    # TODO(slice 1b): rasterize via pdf2image, call Claude vision to segment, write _manifest.json.
    return JSONResponse(
        {
            "source_slug": source_slug,
            "saved_to": str(pdf_path.relative_to(storage.ROOT)),
            "status": "uploaded (segmentation not yet wired)",
        }
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
