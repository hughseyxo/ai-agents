import sys
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from wedding_ui.server import app, get_store
from wedding_ui.budget_model import (
    BudgetStore, BudgetConfig, CostItem, DEFAULT_ITEMS, compute_budget, line_total,
)


@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "test_wedding.db"


@pytest.fixture
def store(temp_db_path):
    s = BudgetStore(temp_db_path)
    app.dependency_overrides[get_store] = lambda: BudgetStore(temp_db_path)
    yield s
    app.dependency_overrides.clear()
    s.close()


@pytest.fixture
def client(store):
    return TestClient(app)


# --- seeding & totals ---

def test_budget_seeds_defaults(client):
    data = client.get("/api/budget").json()
    assert len(data["items"]) == len(DEFAULT_ITEMS) + 1  # + contingency buffer card
    assert data["config"]["guests"] == 350
    assert data["tables"] == 35


def test_grand_total_math(client):
    data = client.get("/api/budget").json()
    cfg = BudgetConfig()
    subtotal = sum(line_total(i, cfg) for i in DEFAULT_ITEMS)
    assert data["subtotal"] == pytest.approx(subtotal)
    assert data["grand_total"] == pytest.approx(subtotal * 1.10)


def test_per_guest_and_per_table_scaling():
    cfg = BudgetConfig(guests=350, seats_per_table=10)
    catering = next(i for i in DEFAULT_ITEMS if i.key == "catering")
    centrepieces = next(i for i in DEFAULT_ITEMS if i.key == "centrepieces")
    assert line_total(catering, cfg) == 110 * 350
    assert line_total(centrepieces, cfg) == 70 * 35  # ceil(350/10)


# --- config changes recompute ---

def test_patch_guests_recomputes_tables_and_totals(client):
    before = client.get("/api/budget").json()
    after = client.patch("/api/config", json={"guests": 100}).json()
    assert after["config"]["guests"] == 100
    assert after["tables"] == 10
    assert after["grand_total"] < before["grand_total"]


def test_patch_savings_changes_affordability(client):
    after = client.patch("/api/config", json={"savings": 5000}).json()
    venue = next(i for i in after["items"] if i["key"] == "venue_hire")
    assert venue["status"] == "funded"
    assert after["pct_funded"] > 0


# --- item edit / add / delete / reset ---

def test_patch_item_persists(client):
    resp = client.patch("/api/items/catering", json={"unit_cost": 200})
    assert resp.status_code == 200
    catering = next(i for i in resp.json()["items"] if i["key"] == "catering")
    assert catering["unit_cost"] == 200
    assert catering["line_total"] == 200 * 350
    # persisted across a fresh GET
    again = next(i for i in client.get("/api/budget").json()["items"] if i["key"] == "catering")
    assert again["unit_cost"] == 200


def test_add_and_delete_item(client):
    new = {"key": "marquee", "label": "Marquee hire", "category": "Venue",
           "unit_cost": 4000, "scaling": "fixed", "priority": 1}
    keys = [i["key"] for i in client.post("/api/items", json=new).json()["items"]]
    assert "marquee" in keys
    # duplicate rejected
    assert client.post("/api/items", json=new).status_code == 400
    keys2 = [i["key"] for i in client.delete("/api/items/marquee").json()["items"]]
    assert "marquee" not in keys2


def test_reset_restores_defaults(client):
    client.patch("/api/items/catering", json={"unit_cost": 999})
    client.patch("/api/config", json={"guests": 50})
    data = client.post("/api/reset").json()
    assert data["config"]["guests"] == 350
    catering = next(i for i in data["items"] if i["key"] == "catering")
    assert catering["unit_cost"] == 110


# --- waterfall semantics ---

def test_waterfall_funded_partial_unfunded():
    cfg = BudgetConfig(guests=10, seats_per_table=10, savings=0, contingency_pct=0)
    items = [
        CostItem(key="a", label="A", category="X", unit_cost=100, scaling="fixed", priority=1),
        CostItem(key="b", label="B", category="X", unit_cost=100, scaling="fixed", priority=2),
        CostItem(key="c", label="C", category="X", unit_cost=100, scaling="fixed", priority=3),
    ]
    # exactly fund A, half of B, nothing for C
    cfg.savings = 150
    rows = {r["key"]: r for r in compute_budget(cfg, items)["items"]}
    assert rows["a"]["status"] == "funded"
    assert rows["b"]["status"] == "partial"
    assert rows["b"]["pct_funded"] == pytest.approx(50.0)
    assert rows["c"]["status"] == "unfunded"


def test_waterfall_per_guest_partial_units():
    cfg = BudgetConfig(guests=350, seats_per_table=10, savings=110 * 287, contingency_pct=0)
    items = [CostItem(key="catering", label="Catering", category="Food",
                      unit_cost=110, scaling="per_guest", priority=1)]
    row = compute_budget(cfg, items)["items"][0]
    assert row["status"] == "partial"
    assert row["units_covered"] == 287
    assert row["units_needed"] == 350


# --- validation ---

def test_negative_cost_rejected(client):
    assert client.patch("/api/items/catering", json={"unit_cost": -5}).status_code == 422


def test_negative_guests_rejected(client):
    assert client.patch("/api/config", json={"guests": -1}).status_code == 422


def test_unknown_item_404(client):
    assert client.patch("/api/items/nope", json={"unit_cost": 1}).status_code == 404
    assert client.delete("/api/items/nope").status_code == 404


# --- health check ---

def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_reports_db_failure(client):
    class BrokenStore:
        def get_config(self):
            raise RuntimeError("db unreachable")

    app.dependency_overrides[get_store] = lambda: BrokenStore()
    resp = client.get("/healthz")
    assert resp.status_code == 503


def test_metrics_exposes_request_counter(client):
    client.get("/api/budget")  # generate at least one counted request
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "wedding_ui_requests_total" in resp.text
