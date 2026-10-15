import pytest

from agents.job_store import JobStore, normalise_company, validate_query


@pytest.fixture
def store(tmp_path):
    s = JobStore(tmp_path / "jobs.db")
    yield s
    s.close()


def _job(job_id="1", **kw):
    job = {
        "job_id": job_id, "url": f"https://nl.linkedin.com/jobs/view/x-{job_id}",
        "title": "Site Reliability Engineer", "company": "Adyen N.V.",
        "location": "Amsterdam", "posted": "2026-10-14",
    }
    job.update(kw)
    return job


# --- helpers ---

@pytest.mark.parametrize("q,expected", [
    ("  Site Reliability  Engineer ", "Site Reliability Engineer"),
    ("C++/Go #k8s .net", "C++/Go #k8s .net"),
    ("", None),
    ("x" * 41, None),
    ("drop table; --", None),
    ("<script>", None),
])
def test_validate_query(q, expected):
    assert validate_query(q) == expected


def test_normalise_company_strips_legal_suffixes():
    assert normalise_company("Adyen N.V.") == "adyen"
    assert normalise_company("ING Bank B.V.") == "ing bank"
    assert normalise_company("  Booking.com ") == "bookingcom"


# --- CV snapshots ---

def test_cv_snapshot_roundtrip(store):
    assert store.latest_cv() is None
    snap = store.add_cv_snapshot("f1", "CV_v5", "2026-09-29T07:39:05Z", "I do SRE")
    latest = store.latest_cv()
    assert latest["id"] == snap["id"]
    assert latest["text"] == "I do SRE"
    assert latest["text_hash"] == JobStore.text_hash("I do SRE")


# --- queries ---

def test_replace_cv_queries_keeps_manual_and_drops_invalid(store):
    store.add_manual_query("Platform Engineer")
    n = store.replace_cv_queries(["SRE", "bad;query", "Kubernetes", "SRE"])
    assert n == 2
    store.replace_cv_queries(["Cloud Engineer"])
    queries = {(q["query"], q["source"]) for q in store.list_queries()}
    assert queries == {("Platform Engineer", "manual"), ("Cloud Engineer", "cv")}


def test_add_manual_query_rejects_invalid_and_duplicate(store):
    store.add_manual_query("Terraform")
    with pytest.raises(ValueError):
        store.add_manual_query("terraform")
    with pytest.raises(ValueError):
        store.add_manual_query("rm -rf; ls")


def test_delete_query(store):
    q = store.add_manual_query("Terraform")
    assert store.delete_query(q["id"]) is True
    assert store.delete_query(q["id"]) is False


# --- jobs ---

def test_insert_job_is_idempotent(store):
    assert store.insert_job(_job()) is True
    assert store.insert_job(_job()) is False
    assert store.has_job("1")
    assert store.get_job("1")["status"] == "new"


def test_triage_and_scoring_queue(store):
    for i in ("1", "2", "3"):
        store.insert_job(_job(i))
    assert [j["job_id"] for j in store.jobs_needing_triage(10)] == ["1", "2", "3"]
    store.set_triage("1", True)
    store.set_triage("2", False)
    store.set_triage("3", True)
    store.set_job_status("3", "dismissed")
    assert store.jobs_needing_triage(10) == []
    assert [j["job_id"] for j in store.jobs_to_score(10)] == ["1"]


def test_save_score_and_list_filters(store):
    store.insert_job(_job("1"))
    store.insert_job(_job("2"))
    snap = store.add_cv_snapshot("f", "cv", "t", "text")
    store.save_score("1", 88, ["k8s"], ["Dutch"], "Strong fit", snap["id"])
    store.save_score("2", 40, [], [], "Weak", snap["id"])
    job = store.get_job("1")
    assert job["matched_skills"] == ["k8s"] and job["gaps"] == ["Dutch"]
    assert [j["job_id"] for j in store.list_jobs(min_score=50)] == ["1"]
    assert [j["job_id"] for j in store.list_jobs()] == ["1", "2"]  # score desc


def test_reset_scores_since_skips_closed_jobs(store):
    store.insert_job(_job("1"))
    store.insert_job(_job("2"))
    for jid in ("1", "2"):
        store.set_triage(jid, True)
        store.save_score(jid, 70, [], [], "ok", None)
    store.set_job_status("2", "dismissed")
    assert store.reset_scores_since(14) == 1
    assert store.get_job("1")["score"] is None
    assert store.get_job("2")["score"] == 70


def test_notify_queue(store):
    store.insert_job(_job("1"))
    store.insert_job(_job("2"))
    store.save_score("1", 80, [], [], "great", None)
    store.save_score("2", 60, [], [], "meh", None)
    assert [j["job_id"] for j in store.jobs_to_notify(75)] == ["1"]
    store.mark_notified(["1"])
    assert store.jobs_to_notify(75) == []


def test_mark_applied_creates_one_linked_application(store):
    store.insert_job(_job("1"))
    app1 = store.mark_applied("1")
    app2 = store.mark_applied("1")
    assert app1["id"] == app2["id"]
    assert app1["job_id"] == "1" and app1["source"] == "manual"
    assert store.get_job("1")["status"] == "applied"
    assert len(store.list_applications()) == 1


def test_mark_applied_unknown_job(store):
    assert store.mark_applied("nope") is None


# --- applications ---

def test_create_and_update_application(store):
    app = store.create_application("Adyen", "SRE", "applied", "manual", notes="ref A")
    updated = store.update_application(app["id"], status="interview", notes="call Tue")
    assert updated["status"] == "interview" and updated["notes"] == "call Tue"
    assert store.update_application(999, status="offer") is None


def test_find_application_fuzzy_role(store):
    app = store.create_application("Adyen N.V.", "Senior Site Reliability Engineer", "applied", "gmail")
    assert store.find_application("Adyen", "Site Reliability Engineer")["id"] == app["id"]
    assert store.find_application("Adyen", "")["id"] == app["id"]
    assert store.find_application("Adyen", "Account Manager") is None
    assert store.find_application("Mollie", "Site Reliability Engineer") is None


def test_record_email_event_dedups_application_and_logs_events(store):
    a = store.record_email_event("Adyen", "SRE", "applied", "m1", "t1", "Thanks for applying", "2026-10-01")
    b = store.record_email_event("Adyen B.V.", "SRE", "interview", "m2", "t2", "Interview invite", "2026-10-05")
    assert a["id"] == b["id"]
    apps = store.list_applications()
    assert len(apps) == 1
    assert apps[0]["status"] == "interview"
    assert [e["status"] for e in apps[0]["events"]] == ["applied", "interview"]


def test_record_email_event_does_not_downgrade_status(store):
    store.record_email_event("Adyen", "SRE", "rejected", "m1", "t1", "Unfortunately", "2026-10-05")
    app = store.record_email_event("Adyen", "SRE", "applied", "m2", "t2", "Received", "2026-10-01")
    assert app["status"] == "rejected"
    assert len(store.get_application(app["id"])["events"]) == 2


def test_record_email_event_links_matching_job(store):
    store.insert_job(_job("7", company="Adyen", title="Site Reliability Engineer"))
    app = store.record_email_event("Adyen", "Site Reliability Engineer", "applied",
                                   "m1", "t1", "Application sent", "2026-10-02")
    assert app["job_id"] == "7"
    assert store.get_job("7")["status"] == "applied"


def test_record_email_event_same_message_twice_is_noop(store):
    store.record_email_event("Adyen", "SRE", "applied", "m1", "t1", "s", "2026-10-01")
    store.record_email_event("Adyen", "SRE", "applied", "m1", "t1", "s", "2026-10-01")
    assert len(store.list_applications()[0]["events"]) == 1


# --- gmail ---

def test_gmail_seen(store):
    assert store.has_gmail_history() is False
    store.mark_gmail_seen(["a", "b", "a"])
    assert store.gmail_seen("a") and not store.gmail_seen("c")
    assert store.has_gmail_history() is True


# --- reconciling jobs with applications ---

def test_reconcile_links_matching_jobs_and_reports_status(store):
    store.insert_job(_job("1", company="Adyen", title="Senior Site Reliability Engineer"))
    store.insert_job(_job("2", company="Adyen", title="Account Executive"))
    store.insert_job(_job("3", company="Mollie", title="Site Reliability Engineer"))
    app = store.create_application("Adyen N.V.", "Site Reliability Engineer", "rejected", "gmail")
    assert store.reconcile_applications() == 1
    job = store.get_job("1")
    assert job["status"] == "applied" and job["application_id"] == app["id"]
    assert job["application_status"] == "rejected"
    assert store.get_job("2")["status"] == "new"
    assert store.get_job("3")["application_status"] is None
    assert store.get_application(app["id"])["job_id"] == "1"
    assert store.reconcile_applications() == 0  # idempotent


def test_reconcile_skips_applications_without_role(store):
    store.insert_job(_job("1", company="Harnham", title="Site Reliability Engineer"))
    store.create_application("Harnham", "", "applied", "gmail")
    assert store.reconcile_applications() == 0
    assert store.get_job("1")["status"] == "new"


def test_reconciled_jobs_leave_scoring_triage_and_notify_queues(store):
    store.insert_job(_job("1", company="Adyen", title="SRE"))
    store.set_triage("1", True)
    store.insert_job(_job("2", company="Adyen", title="SRE II"))
    store.save_score("2", 90, [], [], "fit", None)
    store.create_application("Adyen", "SRE", "applied", "gmail")
    store.reconcile_applications()
    assert store.jobs_to_score(10) == []
    assert store.jobs_needing_triage(10) == []
    assert store.jobs_to_notify(75) == []


def test_list_jobs_includes_application_status(store):
    store.insert_job(_job("1"))
    store.mark_applied("1")
    store.update_application(store.list_applications()[0]["id"], status="interview")
    assert store.list_jobs()[0]["application_status"] == "interview"


def test_migrates_old_jobs_table_without_application_id(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL, "
                 "location TEXT, url TEXT NOT NULL, posted TEXT, description TEXT, triage TEXT, score INTEGER, "
                 "matched_skills TEXT, gaps TEXT, rationale TEXT, cv_snapshot_id INTEGER, "
                 "status TEXT NOT NULL DEFAULT 'new', notified INTEGER NOT NULL DEFAULT 0, "
                 "first_seen TEXT NOT NULL, scored_at TEXT)")
    conn.execute("INSERT INTO jobs (job_id, title, company, url, first_seen) VALUES ('1','SRE','A','u','t')")
    conn.commit()
    conn.close()
    s = JobStore(path)
    assert s.get_job("1")["application_id"] is None
    s.close()
