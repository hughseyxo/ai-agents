"""FastAPI app for the Job Hunt PWA.

A thin view over JobStore (data/jobs/jobs.db): CV-scored LinkedIn matches, the
application pipeline (Gmail-derived + manual), and search queries. No LLM —
all fetching/scoring happens in the job-hunt agent on the host. Tailscale-only,
so there is no login.
"""
import sys
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import Counter, make_asgi_app
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.job_store import JobStore  # noqa: E402

app = FastAPI(title="Job Hunt")

REQUEST_COUNT = Counter("jobs_ui_requests_total", "Total HTTP requests", ["method", "path", "status"])


@app.middleware("http")
async def _count_requests(request, call_next):
    response = await call_next(request)
    # Route template, not raw path, so job ids don't explode label cardinality.
    route = request.scope.get("route")
    REQUEST_COUNT.labels(request.method, getattr(route, "path", "other"), response.status_code).inc()
    return response


STATIC_DIR = REPO_ROOT / "jobs_ui" / "static"
INDEX_PATH = REPO_ROOT / "jobs_ui" / "templates" / "index.html"

JobStatus = Literal["new", "seen", "dismissed", "applied"]
AppStatus = Literal["applied", "interview", "rejected", "offer", "withdrawn"]


def get_store():
    store = JobStore()
    try:
        yield store
    finally:
        store.close()


class JobUpdate(BaseModel):
    status: JobStatus


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=120)
    role: str = Field(default="", max_length=160)
    status: AppStatus = "applied"
    notes: str = Field(default="", max_length=2000)
    job_id: Optional[str] = Field(default=None, max_length=40)


class ApplicationUpdate(BaseModel):
    status: Optional[AppStatus] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class QueryCreate(BaseModel):
    query: str = Field(max_length=80)


def _public_job(job: dict) -> dict:
    job = dict(job)
    job.pop("description", None)
    return job


# --- jobs ---

@app.get("/api/jobs")
def list_jobs(status: Optional[JobStatus] = None,
              min_score: Optional[int] = Query(default=None, ge=0, le=100),
              store: JobStore = Depends(get_store)):
    jobs = store.list_jobs(status=status, min_score=min_score)
    jobs = [j for j in jobs if j["triage"] != "drop"]
    if status is None:
        jobs = [j for j in jobs if j["status"] != "dismissed"]
    return [_public_job(j) for j in jobs]


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: str, data: JobUpdate, store: JobStore = Depends(get_store)):
    if not store.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    application = None
    if data.status == "applied":
        application = store.mark_applied(job_id)
    else:
        store.set_job_status(job_id, data.status)
    return {"job": _public_job(store.get_job(job_id)), "application": application}


# --- applications ---

@app.get("/api/applications")
def list_applications(store: JobStore = Depends(get_store)):
    return store.list_applications()


@app.post("/api/applications", status_code=201)
def create_application(data: ApplicationCreate, store: JobStore = Depends(get_store)):
    if data.job_id:
        job = store.get_job(data.job_id)
        if not job:
            raise HTTPException(status_code=400, detail="Unknown job_id")
        if job.get("application_id"):  # already tracked — one application per job
            return store.get_application(job["application_id"])
        app = store.mark_applied(data.job_id)
        return store.update_application(app["id"], status=data.status, notes=data.notes)
    return store.create_application(data.company.strip(), data.role.strip(), data.status,
                                    "manual", job_id=data.job_id, notes=data.notes)


@app.patch("/api/applications/{app_id}")
def update_application(app_id: int, data: ApplicationUpdate, store: JobStore = Depends(get_store)):
    updated = store.update_application(app_id, status=data.status, notes=data.notes)
    if updated is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return updated


# --- search queries / CV ---

@app.get("/api/queries")
def list_queries(store: JobStore = Depends(get_store)):
    return store.list_queries()


@app.post("/api/queries", status_code=201)
def add_query(data: QueryCreate, store: JobStore = Depends(get_store)):
    try:
        return store.add_manual_query(data.query)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/queries/{query_id}", status_code=204)
def delete_query(query_id: int, store: JobStore = Depends(get_store)):
    if not store.delete_query(query_id):
        raise HTTPException(status_code=404, detail="Query not found")
    return Response(status_code=204)


@app.get("/api/cv")
def current_cv(store: JobStore = Depends(get_store)):
    cv = store.latest_cv()
    if not cv:
        return {"cv": None}
    return {"cv": {k: cv[k] for k in ("name", "modified_time", "created_at")}}


@app.get("/healthz")
def healthz(store: JobStore = Depends(get_store)):
    try:
        store.latest_cv()
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
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/metrics", make_asgi_app())
