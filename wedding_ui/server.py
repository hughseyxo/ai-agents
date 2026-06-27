"""FastAPI app for the Wedding Budget PWA.

Serves a single-page Alpine.js PWA and a small JSON API over BudgetStore. The
headline endpoint, GET /api/budget, returns the full budget summary plus the
priority-waterfall affordability breakdown computed in budget_model.
"""
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from wedding_ui.budget_model import (
    BudgetStore, BudgetConfig, CostItem, compute_budget, Scaling,
)

app = FastAPI(title="Wedding Budget PWA")

STATIC_DIR = REPO_ROOT / "wedding_ui" / "static"
INDEX_PATH = REPO_ROOT / "wedding_ui" / "templates" / "index.html"


def get_store() -> BudgetStore:
    store = BudgetStore()
    try:
        yield store
    finally:
        store.close()


# --- Request models ---

class ConfigUpdate(BaseModel):
    guests: Optional[int] = Field(default=None, ge=1, le=5000)
    seats_per_table: Optional[int] = Field(default=None, ge=1, le=50)
    savings: Optional[float] = Field(default=None, ge=0)
    contingency_pct: Optional[float] = Field(default=None, ge=0, le=100)


class ItemUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, min_length=1, max_length=60)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    scaling: Optional[Scaling] = None
    priority: Optional[int] = Field(default=None, ge=1, le=99)
    note: Optional[str] = Field(default=None, max_length=300)


class ItemCreate(CostItem):
    pass


# --- API ---

@app.get("/api/budget")
def get_budget(store: BudgetStore = Depends(get_store)):
    return compute_budget(store.get_config(), store.get_items())


@app.patch("/api/config")
def patch_config(data: ConfigUpdate, store: BudgetStore = Depends(get_store)):
    cfg = store.get_config()
    updated = cfg.model_copy(update=data.model_dump(exclude_unset=True))
    store.save_config(updated)
    return compute_budget(updated, store.get_items())


@app.patch("/api/items/{key}")
def patch_item(key: str, data: ItemUpdate, store: BudgetStore = Depends(get_store)):
    items = store.get_items()
    idx = next((i for i, it in enumerate(items) if it.key == key), -1)
    if idx == -1:
        raise HTTPException(status_code=404, detail=f"Item '{key}' not found")
    items[idx] = items[idx].model_copy(update=data.model_dump(exclude_unset=True))
    store.save_items(items)
    return compute_budget(store.get_config(), items)


@app.post("/api/items")
def add_item(data: ItemCreate, store: BudgetStore = Depends(get_store)):
    items = store.get_items()
    if any(it.key == data.key for it in items):
        raise HTTPException(status_code=400, detail=f"Item '{data.key}' already exists")
    items.append(data)
    store.save_items(items)
    return compute_budget(store.get_config(), items)


@app.delete("/api/items/{key}")
def delete_item(key: str, store: BudgetStore = Depends(get_store)):
    items = store.get_items()
    remaining = [it for it in items if it.key != key]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail=f"Item '{key}' not found")
    store.save_items(remaining)
    return compute_budget(store.get_config(), remaining)


@app.post("/api/reset")
def reset_budget(store: BudgetStore = Depends(get_store)):
    store.reset()
    return compute_budget(store.get_config(), store.get_items())


# --- Frontend SPA ---

@app.get("/", response_class=HTMLResponse)
def serve_index():
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return INDEX_PATH.read_text()


@app.get("/sw.js", response_class=FileResponse)
def serve_sw():
    sw = STATIC_DIR / "sw.js"
    if not sw.exists():
        raise HTTPException(status_code=404, detail="Service worker not found")
    return FileResponse(sw, media_type="application/javascript")


STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
