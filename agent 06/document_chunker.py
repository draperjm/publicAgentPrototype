"""
Document Chunker Service
Accepts a PDF + chunking metadata, renders each chunk as a PNG, and returns
a manifest JSON describing every chunk in sequence.

Chunk strategies
----------------
page-split      : one PNG per page (for oversized text docs)
quadrant-split  : divide each page into a grid of sub-images based on page_size
none            : render whole document page-by-page (same as page-split)

Grid layout per page_size for quadrant-split
---------------------------------------------
A2  →  4 chunks  →  2 cols × 2 rows
A1  →  6 chunks  →  3 cols × 2 rows
A0  →  9 chunks  →  3 cols × 3 rows
large-format → 12 chunks → 4 cols × 3 rows
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="Document Chunker", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = Path(os.getenv("CHUNK_OUTPUT_DIR", "/output/chunks"))

# Grid (cols, rows) for each supported chunk count
_GRID: dict[int, Tuple[int, int]] = {
    1:  (1, 1),
    2:  (2, 1),
    4:  (2, 2),
    6:  (3, 2),
    9:  (3, 3),
    12: (4, 3),
}

# Human-readable region names in row-major order for each grid
_REGION_NAMES: dict[Tuple[int, int], List[str]] = {
    (1, 1): ["full"],
    (2, 1): ["left", "right"],
    (2, 2): ["top-left", "top-right", "bottom-left", "bottom-right"],
    (3, 2): [
        "top-left",    "top-center",    "top-right",
        "bottom-left", "bottom-center", "bottom-right",
    ],
    (3, 3): [
        "top-left",    "top-center",    "top-right",
        "mid-left",    "mid-center",    "mid-right",
        "bottom-left", "bottom-center", "bottom-right",
    ],
    (4, 3): [
        "top-1",    "top-2",    "top-3",    "top-4",
        "mid-1",    "mid-2",    "mid-3",    "mid-4",
        "bottom-1", "bottom-2", "bottom-3", "bottom-4",
    ],
}

# Chunks-per-page for each paper size under quadrant-split
_CHUNKS_PER_PAGE: dict[str, int] = {
    "A2": 4,
    "A1": 6,
    "A0": 9,
    "large-format": 12,
}


def _grid_for_page_size(page_size: str) -> Tuple[int, int]:
    n = _CHUNKS_PER_PAGE.get(page_size, 4)
    return _GRID.get(n, (2, 2))


def _region_labels(cols: int, rows: int) -> List[str]:
    return _REGION_NAMES.get(
        (cols, rows),
        [f"r{r+1}c{c+1}" for r in range(rows) for c in range(cols)],
    )


def _split_image(img, cols: int, rows: int, overlap: float = 0.08) -> List:
    """
    Crop a PIL Image into a (cols × rows) grid, row-major order.
    Each tile extends by `overlap` fraction into adjacent tiles so text
    at tile boundaries is not split mid-character.
    Edge tiles absorb any remainder pixels so nothing is clipped.
    """
    from PIL import Image  # local import to keep startup fast

    w, h = img.size
    cw, ch = w // cols, h // rows
    ox, oy = int(cw * overlap), int(ch * overlap)
    tiles = []
    for row in range(rows):
        for col in range(cols):
            left  = max(col * cw - ox, 0)
            top   = max(row * ch - oy, 0)
            right = min((col + 1) * cw + ox, w) if col < cols - 1 else w
            bot   = min((row + 1) * ch + oy, h) if row < rows - 1 else h
            tiles.append(img.crop((left, top, right, bot)))
    return tiles


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "document-chunker"}


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@app.post("/chunk")
async def chunk_document(
    file: UploadFile = File(...),
    chunk_strategy: str = Form("page-split"),
    page_size: str = Form("A4"),
    dpi: int = Form(150),
    job_id: Optional[str] = Form(None),
    output_base: Optional[str] = Form(None),
):
    """
    Parameters
    ----------
    file            PDF file to chunk
    chunk_strategy  "page-split" | "quadrant-split" | "none"
    page_size       "A4" | "A3" | "A2" | "A1" | "A0" | "large-format"
    dpi             Render resolution (default 150; use 200-300 for OCR quality)
    job_id          Optional idempotency key; auto-generated if omitted
    """
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="pdf2image is not installed. Add it to requirements.",
        )

    job_id = job_id or str(uuid.uuid4())
    base_dir = Path(output_base) if output_base else OUTPUT_DIR
    job_dir = base_dir / job_id
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        job_dir = OUTPUT_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

    raw = await file.read()
    source_name = file.filename or "document.pdf"

    # Render all pages to PIL Images
    try:
        pages = convert_from_bytes(raw, dpi=dpi)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF render failed: {exc}")

    chunks: List[dict] = []
    seq = 1

    if chunk_strategy == "quadrant-split":
        cols, rows = _grid_for_page_size(page_size)
        labels = _region_labels(cols, rows)

        for page_num, page_img in enumerate(pages, start=1):
            tiles = _split_image(page_img, cols, rows)
            for tile_idx, tile in enumerate(tiles):
                fname = f"chunk_{seq:04d}.png"
                fpath = job_dir / fname
                tile.save(str(fpath), "PNG")
                w, h = tile.size
                chunks.append({
                    "chunk_id":    f"chunk_{seq:04d}",
                    "sequence":    seq,
                    "page_number": page_num,
                    "region":      labels[tile_idx] if tile_idx < len(labels) else f"region-{tile_idx + 1}",
                    "grid":        f"{cols}x{rows}",
                    "col":         (tile_idx % cols) + 1,
                    "row":         (tile_idx // cols) + 1,
                    "filename":    fname,
                    "filepath":    str(fpath),
                    "width_px":    w,
                    "height_px":   h,
                })
                seq += 1

    else:
        # page-split or none — one PNG per page
        for page_num, page_img in enumerate(pages, start=1):
            fname = f"chunk_{seq:04d}.png"
            fpath = job_dir / fname
            page_img.save(str(fpath), "PNG")
            w, h = page_img.size
            chunks.append({
                "chunk_id":    f"chunk_{seq:04d}",
                "sequence":    seq,
                "page_number": page_num,
                "region":      "full-page",
                "grid":        "1x1",
                "col":         1,
                "row":         1,
                "filename":    fname,
                "filepath":    str(fpath),
                "width_px":    w,
                "height_px":   h,
            })
            seq += 1

    manifest = {
        "job_id":           job_id,
        "source_file":      source_name,
        "chunk_strategy":   chunk_strategy,
        "page_size":        page_size,
        "dpi":              dpi,
        "total_pages":      len(pages),
        "total_chunks":     len(chunks),
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "output_directory": str(job_dir),
        "chunks":           chunks,
    }

    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return {
        "status":        "ok",
        "job_id":        job_id,
        "total_chunks":  len(chunks),
        "manifest_file": str(manifest_path),
        "manifest":      manifest,
    }


# ---------------------------------------------------------------------------
# Retrieve endpoints (for downstream consumers)
# ---------------------------------------------------------------------------

@app.get("/chunks/{job_id}/manifest")
def get_manifest(job_id: str):
    path = OUTPUT_DIR / job_id / "manifest.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return json.loads(path.read_text())


@app.get("/chunks/{job_id}/{filename}")
def get_chunk_image(job_id: str, filename: str):
    path = OUTPUT_DIR / job_id / filename
    if not path.exists() or not filename.endswith(".png"):
        raise HTTPException(status_code=404, detail="Chunk image not found")
    return FileResponse(str(path), media_type="image/png")
