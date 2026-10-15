"""FastAPI app for the Villa Grabau (Italy) wedding budget PWA.

Serves a single-page Alpine.js PWA and a small JSON API over BudgetStore. The
headline endpoint, GET /api/budget, returns the full budget summary plus the
priority-waterfall affordability breakdown computed in budget_model.
"""
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, make_asgi_app

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from grabau_ui import auth
from grabau_ui.budget_model import (
    BudgetStore, BudgetConfig, CostItem, compute_budget, Scaling,
)

app = FastAPI(title="Villa Grabau Wedding Budget PWA")

REQUEST_COUNT = Counter(
    "grabau_ui_requests_total", "Total HTTP requests", ["method", "path", "status"]
)


@app.middleware("http")
async def _count_requests(request, call_next):
    response = await call_next(request)
    REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
    return response


STATIC_DIR = REPO_ROOT / "grabau_ui" / "static"
INDEX_PATH = REPO_ROOT / "grabau_ui" / "templates" / "index.html"
LOGIN_PATH = REPO_ROOT / "grabau_ui" / "templates" / "login.html"


def get_store() -> BudgetStore:
    store = BudgetStore()
    try:
        yield store
    finally:
        store.close()


# --- auth gate ---
# Everything is private except the login page, the health check and the static
# shell (which must load before login so the page renders). See grabau_ui.auth
# for why this replaced Traefik basic auth.
PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/sw.js"}
PUBLIC_PREFIXES = ("/static/", "/metrics")


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _is_authenticated(request: Request) -> bool:
    return auth.verify_token(auth.get_secret(), request.cookies.get(auth.COOKIE_NAME))


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path
    if not _is_public(path):
        if not auth.get_passcode():
            # Fail closed: never serve the budget with no passcode configured.
            return JSONResponse({"detail": "Server not configured"}, status_code=503)
        if not _is_authenticated(request):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


def _login_page(error: str = "") -> str:
    html = LOGIN_PATH.read_text()
    if error:
        html = html.replace("<!--ERROR-->", f'<p class="login-error">{error}</p>')
    return html


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if auth.get_passcode() and _is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_login_page())


@app.post("/login")
def login_submit(request: Request, passcode: str = Form(default="")):
    if not auth.get_passcode():
        return JSONResponse({"detail": "Server not configured"}, status_code=503)

    ip = auth.client_ip(request)
    if auth.is_throttled(ip):
        return HTMLResponse(
            _login_page("Too many attempts. Try again in 15 minutes."), status_code=429
        )

    if not auth.check_passcode(passcode):
        auth.record_failure(ip)
        return HTMLResponse(_login_page("Wrong passcode."), status_code=401)

    auth.clear_failures(ip)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.make_token(auth.get_secret()),
        max_age=auth.MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        # Only mark Secure when actually on HTTPS, so local/test HTTP works.
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        path="/",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


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


@app.get("/healthz")
def healthz(store: BudgetStore = Depends(get_store)):
    try:
        store.get_config()
    except Exception:
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "ok"}


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
app.mount("/metrics", make_asgi_app())
