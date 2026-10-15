"""FastAPI app for the Way of Kings spoiler-free companion site.

Static, read-only data (no DB) — chapter/character/region content is
authored in wok_tracker/data/content.py. "Mark as read" progress lives in
the browser's localStorage, not on the server, since the site has no login.
"""
import hashlib
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from prometheus_client import Counter, make_asgi_app

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from wok_tracker.wok_model import list_chapters, get_chapter, list_regions

app = FastAPI(title="Way of Kings Companion")

REQUEST_COUNT = Counter(
    "wok_tracker_requests_total", "Total HTTP requests", ["method", "path", "status"]
)


@app.middleware("http")
async def _count_requests(request, call_next):
    response = await call_next(request)
    REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
    return response


STATIC_DIR = REPO_ROOT / "wok_tracker" / "static"
INDEX_PATH = REPO_ROOT / "wok_tracker" / "templates" / "index.html"


def _compute_asset_version() -> str:
    """Content hash of the versioned static assets — busts client/CDN caches on any change."""
    h = hashlib.sha256()
    for name in ("style.css", "app.js", "map.svg"):
        path = STATIC_DIR / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _compute_asset_version()


@app.get("/api/chapters")
def api_list_chapters():
    return list_chapters()


@app.get("/api/chapters/{chapter_id}")
def api_get_chapter(chapter_id: str):
    chapter = get_chapter(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter '{chapter_id}' not found")
    return chapter


@app.get("/api/regions")
def api_list_regions():
    return list_regions()


@app.get("/healthz")
def healthz():
    if not list_chapters():
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "ok"}


# --- Frontend SPA ---

@app.get("/", response_class=HTMLResponse)
def serve_index():
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return INDEX_PATH.read_text().replace("{{V}}", ASSET_VERSION)


@app.get("/sw.js", response_class=FileResponse)
def serve_sw():
    sw = STATIC_DIR / "sw.js"
    if not sw.exists():
        raise HTTPException(status_code=404, detail="Service worker not found")
    return FileResponse(sw, media_type="application/javascript")


STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/metrics", make_asgi_app())
