"""API + affordability tests for the Villa Grabau (Italy) wedding budget PWA.

Mirrors tests/test_wedding_ui_api.py but pins the Italian defaults: a
Villa-Grabau-sized guest count and Tuscan 2026 average costs.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from grabau_ui.server import app, get_store
from grabau_ui import auth
from grabau_ui.budget_model import (
    BudgetStore, BudgetConfig, CostItem, DEFAULT_ITEMS, compute_budget, line_total,
)


@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "test_grabau.db"


PASSCODE = "test-passcode"


@pytest.fixture(autouse=True)
def _passcode(monkeypatch):
    monkeypatch.setenv("GRABAU_PASSCODE", PASSCODE)
    auth.reset_throttle()


@pytest.fixture
def store(temp_db_path):
    s = BudgetStore(temp_db_path)
    app.dependency_overrides[get_store] = lambda: BudgetStore(temp_db_path)
    yield s
    app.dependency_overrides.clear()
    s.close()


@pytest.fixture
def anon(store):
    """Client with no session cookie."""
    return TestClient(app)


@pytest.fixture
def client(anon):
    """Logged-in client — what the rest of the suite exercises."""
    r = anon.post("/login", data={"passcode": PASSCODE}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return anon


# --- seeding & totals ---

def test_budget_seeds_italian_defaults(client):
    data = client.get("/api/budget").json()
    assert len(data["items"]) == len(DEFAULT_ITEMS) + 1  # + contingency buffer card
    # Villa Grabau tops out around 140 guests; default sits comfortably below.
    assert data["config"]["guests"] == 120
    assert data["config"]["guests"] <= 140
    assert data["tables"] == 12


def test_grand_total_math(client):
    data = client.get("/api/budget").json()
    cfg = BudgetConfig()
    subtotal = sum(line_total(i, cfg) for i in DEFAULT_ITEMS)
    assert data["subtotal"] == pytest.approx(subtotal)
    assert data["grand_total"] == pytest.approx(subtotal * 1.10)


def test_default_total_in_researched_tuscany_range(client):
    """2026 guides put a ~120-guest Tuscan villa wedding at roughly EUR 90k-150k."""
    data = client.get("/api/budget").json()
    assert 90_000 <= data["grand_total"] <= 150_000


def test_venue_and_planner_are_italian_specific():
    keys = {i.key for i in DEFAULT_ITEMS}
    # Destination-wedding line items the Irish version has no equivalent for.
    for key in ("villa_hire", "planner", "civil_ceremony", "legal_paperwork",
                "guest_shuttle", "welcome_dinner", "siae_licence"):
        assert key in keys, f"missing Italian line item: {key}"
    villa = next(i for i in DEFAULT_ITEMS if i.key == "villa_hire")
    assert "Grabau" in villa.label


def test_catering_uses_tuscan_per_head_rate():
    catering = next(i for i in DEFAULT_ITEMS if i.key == "catering")
    assert catering.scaling == "per_guest"
    assert 150 <= catering.unit_cost <= 220  # mid-range plated dinner, 2026


def test_per_guest_and_per_table_scaling():
    cfg = BudgetConfig(guests=120, seats_per_table=10)
    catering = next(i for i in DEFAULT_ITEMS if i.key == "catering")
    centrepieces = next(i for i in DEFAULT_ITEMS if i.key == "centrepieces")
    assert line_total(catering, cfg) == catering.unit_cost * 120
    assert line_total(centrepieces, cfg) == centrepieces.unit_cost * 12


# --- affordability waterfall ---

def test_waterfall_funds_by_priority():
    cfg = BudgetConfig(guests=10, seats_per_table=10, savings=1000, contingency_pct=0)
    items = [
        CostItem(key="b", label="B", category="X", unit_cost=800, priority=2),
        CostItem(key="a", label="A", category="X", unit_cost=600, priority=1),
    ]
    rows = {r["key"]: r for r in compute_budget(cfg, items)["items"]}
    assert rows["a"]["status"] == "funded"
    assert rows["b"]["status"] == "partial"
    assert rows["b"]["pct_funded"] == pytest.approx(50.0)


def test_partial_per_guest_reports_whole_units():
    cfg = BudgetConfig(guests=100, seats_per_table=10, savings=1850, contingency_pct=0)
    items = [CostItem(key="catering", label="Catering", category="Food",
                      unit_cost=185, scaling="per_guest", priority=1)]
    row = compute_budget(cfg, items)["items"][0]
    assert row["status"] == "partial"
    assert row["units_covered"] == 10
    assert row["units_needed"] == 100


def test_fully_funded_flag(client):
    budget = client.get("/api/budget").json()
    data = client.patch("/api/config", json={"savings": budget["grand_total"]}).json()
    assert data["fully_funded"] is True
    assert data["gap"] == 0


# --- CRUD API ---

def test_patch_config_recomputes(client):
    data = client.patch("/api/config", json={"guests": 140}).json()
    assert data["config"]["guests"] == 140
    assert data["tables"] == 14


def test_patch_item_unit_cost(client):
    data = client.patch("/api/items/catering", json={"unit_cost": 200}).json()
    row = next(r for r in data["items"] if r["key"] == "catering")
    assert row["unit_cost"] == 200
    assert row["line_total"] == 200 * data["config"]["guests"]


def test_patch_unknown_item_404(client):
    assert client.patch("/api/items/nope", json={"unit_cost": 1}).status_code == 404


def test_add_and_delete_item(client):
    payload = {"key": "fireworks", "label": "Fireworks", "category": "Extras",
               "unit_cost": 1500, "scaling": "fixed", "priority": 9}
    data = client.post("/api/items", json=payload).json()
    assert any(r["key"] == "fireworks" for r in data["items"])
    assert client.post("/api/items", json=payload).status_code == 400
    data = client.request("DELETE", "/api/items/fireworks").json()
    assert not any(r["key"] == "fireworks" for r in data["items"])
    assert client.request("DELETE", "/api/items/fireworks").status_code == 404


def test_reset_restores_defaults(client):
    client.patch("/api/config", json={"guests": 40})
    client.patch("/api/items/catering", json={"unit_cost": 1})
    data = client.post("/api/reset").json()
    assert data["config"]["guests"] == 120
    catering = next(r for r in data["items"] if r["key"] == "catering")
    assert catering["unit_cost"] == next(i.unit_cost for i in DEFAULT_ITEMS if i.key == "catering")


# --- isolation from the Irish app ---

def test_uses_its_own_db_namespace():
    from grabau_ui import budget_model as gm
    from wedding_ui import budget_model as wm
    assert gm.DB_AGENT != wm.DB_AGENT
    assert gm.DEFAULT_GRABAU_DB != wm.DEFAULT_WEDDING_DB


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_index_and_sw_served(client):
    assert "Villa Grabau" in client.get("/").text
    assert client.get("/sw.js").status_code == 200


# --- in-page passcode login ---
# Replaces Traefik basic auth: iOS in-app browsers and installed PWAs never
# render a basic-auth prompt, so the site was unreachable from a chat-app link.

def test_anonymous_page_redirects_to_login(anon):
    r = anon.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_anonymous_api_gets_401_json(anon):
    r = anon.get("/api/budget")
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_anonymous_write_api_is_blocked(anon):
    assert anon.patch("/api/config", json={"guests": 5}).status_code == 401
    assert anon.post("/api/reset").status_code == 401
    assert anon.request("DELETE", "/api/items/catering").status_code == 401


def test_login_page_is_public(anon):
    r = anon.get("/login")
    assert r.status_code == 200
    assert "passcode" in r.text.lower()


def test_static_and_healthz_are_public(anon):
    assert anon.get("/healthz").status_code == 200
    assert anon.get("/static/style.css").status_code == 200
    assert anon.get("/sw.js").status_code == 200


def test_wrong_passcode_rejected(anon):
    r = anon.post("/login", data={"passcode": "wrong"}, follow_redirects=False)
    assert r.status_code == 401
    assert anon.get("/api/budget").status_code == 401


def test_login_grants_access_and_persists(anon):
    anon.post("/login", data={"passcode": PASSCODE})
    assert anon.get("/api/budget").status_code == 200
    assert anon.get("/", follow_redirects=False).status_code == 200


def test_session_cookie_is_hardened(anon):
    r = anon.post("/login", data={"passcode": PASSCODE}, follow_redirects=False)
    raw = r.headers["set-cookie"].lower()
    assert "httponly" in raw
    assert "samesite=lax" in raw


def test_logout_clears_session(client):
    assert client.get("/api/budget").status_code == 200
    client.post("/logout")
    assert client.get("/api/budget").status_code == 401


def test_forged_cookie_rejected(anon):
    anon.cookies.set(auth.COOKIE_NAME, "9999999999.deadbeef")
    assert anon.get("/api/budget").status_code == 401


def test_token_signed_with_other_secret_rejected():
    good, bad = b"secret-a", b"secret-b"
    token = auth.make_token(good)
    assert auth.verify_token(good, token)
    assert not auth.verify_token(bad, token)


def test_expired_token_rejected():
    token = auth.make_token(b"s", issued_at=1)  # epoch 1970
    assert not auth.verify_token(b"s", token)


def test_fails_closed_when_no_passcode_configured(monkeypatch, anon):
    monkeypatch.delenv("GRABAU_PASSCODE", raising=False)
    assert anon.get("/api/budget").status_code == 503
    assert anon.post("/login", data={"passcode": "anything"}).status_code == 503


def test_brute_force_throttled(anon):
    for _ in range(auth.MAX_ATTEMPTS):
        anon.post("/login", data={"passcode": "wrong"})
    r = anon.post("/login", data={"passcode": PASSCODE}, follow_redirects=False)
    assert r.status_code == 429
