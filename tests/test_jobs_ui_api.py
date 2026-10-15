import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.job_store import JobStore  # noqa: E402
from jobs_ui.server import app, get_store  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "jobs.db"


@pytest.fixture
def store(db_path):
    s = JobStore(db_path)

    def _override():
        st = JobStore(db_path)
        try:
            yield st
        finally:
            st.close()

    app.dependency_overrides[get_store] = _override
    yield s
    app.dependency_overrides.clear()
    s.close()


@pytest.fixture
def client(store):
    return TestClient(app)


def _job(store, job_id, score=None, triage="keep", **kw):
    job = {"job_id": job_id, "url": f"https://nl.linkedin.com/jobs/view/x-{job_id}",
           "title": kw.get("title", "SRE"), "company": kw.get("company", "Adyen"),
           "location": "Amsterdam", "posted": "2026-10-14"}
    store.insert_job(job)
    if triage:
        store.set_triage(job_id, triage == "keep")
    store.set_description(job_id, "long description")
    if score is not None:
        store.save_score(job_id, score, ["k8s"], ["Dutch"], "fit", None)


# --- jobs ---

def test_list_jobs_hides_dismissed_dropped_and_description(client, store):
    _job(store, "1", 90)
    _job(store, "2", 60)
    _job(store, "3", 95)
    store.set_job_status("3", "dismissed")
    _job(store, "4", triage="drop")
    data = client.get("/api/jobs").json()
    assert [j["job_id"] for j in data] == ["1", "2"]
    assert "description" not in data[0]
    assert data[0]["matched_skills"] == ["k8s"]


def test_list_jobs_filters(client, store):
    _job(store, "1", 90)
    _job(store, "2", 60)
    _job(store, "3", 95)
    store.set_job_status("3", "dismissed")
    assert [j["job_id"] for j in client.get("/api/jobs?min_score=75").json()] == ["1"]
    assert [j["job_id"] for j in client.get("/api/jobs?status=dismissed").json()] == ["3"]
    assert client.get("/api/jobs?status=bogus").status_code == 422


def test_dismiss_and_restore_job(client, store):
    _job(store, "1", 90)
    assert client.patch("/api/jobs/1", json={"status": "dismissed"}).json()["job"]["status"] == "dismissed"
    assert client.patch("/api/jobs/1", json={"status": "new"}).json()["job"]["status"] == "new"


def test_mark_applied_creates_linked_application_once(client, store):
    _job(store, "1", 90, title="Platform Engineer", company="Mollie")
    r1 = client.patch("/api/jobs/1", json={"status": "applied"}).json()
    r2 = client.patch("/api/jobs/1", json={"status": "applied"}).json()
    assert r1["application"]["id"] == r2["application"]["id"]
    apps = client.get("/api/applications").json()
    assert len(apps) == 1
    assert apps[0]["company"] == "Mollie" and apps[0]["job_id"] == "1"


def test_patch_job_errors(client, store):
    assert client.patch("/api/jobs/nope", json={"status": "dismissed"}).status_code == 404
    _job(store, "1", 90)
    assert client.patch("/api/jobs/1", json={"status": "hired"}).status_code == 422


# --- applications ---

def test_create_and_update_application(client):
    r = client.post("/api/applications", json={"company": "Bunq", "role": "SRE", "status": "applied"})
    assert r.status_code == 201
    app_id = r.json()["id"]
    r = client.patch(f"/api/applications/{app_id}", json={"status": "interview", "notes": "Tue 10:00"})
    assert r.json()["status"] == "interview" and r.json()["notes"] == "Tue 10:00"
    assert client.get("/api/applications").json()[0]["source"] == "manual"


def test_application_validation(client):
    assert client.post("/api/applications", json={"company": "", "status": "applied"}).status_code == 422
    assert client.post("/api/applications", json={"company": "X", "status": "ghosted"}).status_code == 422
    assert client.patch("/api/applications/999", json={"status": "offer"}).status_code == 404


def test_create_application_unknown_job_id(client):
    r = client.post("/api/applications", json={"company": "X", "status": "applied", "job_id": "404"})
    assert r.status_code == 400


# --- queries ---

def test_queries_crud(client, store):
    store.replace_cv_queries(["SRE"])
    r = client.post("/api/queries", json={"query": "Terraform"})
    assert r.status_code == 201
    qid = r.json()["id"]
    assert client.post("/api/queries", json={"query": "terraform"}).status_code == 400
    assert client.post("/api/queries", json={"query": "a;b"}).status_code == 400
    assert {q["query"] for q in client.get("/api/queries").json()} == {"SRE", "Terraform"}
    assert client.delete(f"/api/queries/{qid}").status_code == 204
    assert client.delete(f"/api/queries/{qid}").status_code == 404


# --- cv / health / shell ---

def test_cv_endpoint(client, store):
    assert client.get("/api/cv").json() == {"cv": None}
    store.add_cv_snapshot("f1", "CV_v5.docx", "2026-09-29T07:39:05Z", "secret cv text")
    cv = client.get("/api/cv").json()["cv"]
    assert cv["name"] == "CV_v5.docx"
    assert "text" not in cv


def test_healthz_and_index(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/")
    assert r.status_code == 200 and "jobsApp()" in r.text
    assert client.get("/sw.js").status_code == 200


def test_jobs_expose_application_status(client, store):
    _job(store, "1", 80, title="Site Reliability Engineer", company="Adyen")
    store.create_application("Adyen", "Site Reliability Engineer", "rejected", "gmail")
    store.reconcile_applications()
    job = client.get("/api/jobs?status=applied").json()[0]
    assert job["application_status"] == "rejected"
    assert client.get("/api/jobs?min_score=75").json() == [job]  # still listed; UI filters applied out
