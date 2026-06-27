"""Data model + affordability engine for the Wedding Budget PWA.

BudgetStore wraps AgentDB (a dedicated ``wedding.db`` by default) with typed
read/write of the wedding config and editable cost line items. The affordability
engine pours the user's savings into the line items in a priority "waterfall" so
the UI can render tangible coverage ("caterer for 287 of 350 guests").

Costs are seeded from researched Irish 2025/2026 averages (see DEFAULT_ITEMS);
every item is editable in-app and ``reset`` restores these defaults.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agents.db import AgentDB

DB_AGENT = "wedding-budget"
CONFIG_KEY = "config"
ITEMS_KEY = "items"

# Dedicated db file so the public Docker container needn't mount the host repo's
# live agents.db. Overridable in tests / via constructor.
DEFAULT_WEDDING_DB = Path(__file__).resolve().parent.parent / "data" / "wedding.db"

Scaling = Literal["fixed", "per_guest", "per_table"]


class CostItem(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    unit_cost: float = Field(ge=0)
    scaling: Scaling = "fixed"
    priority: int = Field(default=5, ge=1, le=99)
    note: str = Field(default="", max_length=300)


class BudgetConfig(BaseModel):
    guests: int = Field(default=350, ge=1, le=5000)
    seats_per_table: int = Field(default=10, ge=1, le=50)
    savings: float = Field(default=0.0, ge=0)
    contingency_pct: float = Field(default=10.0, ge=0, le=100)


# Irish 2025/2026 average costs (editable in-app). priority drives the waterfall:
# essentials funded first.
DEFAULT_ITEMS: list[CostItem] = [
    CostItem(key="venue_hire", label="Venue hire (room/exclusivity)", category="Venue",
             unit_cost=3500, scaling="fixed", priority=1, note="Room/exclusivity, excl. per-head catering"),
    CostItem(key="catering", label="Catering / wedding meal", category="Food & Drink",
             unit_cost=110, scaling="per_guest", priority=2, note="IE 2026 avg ~€90–130/head"),
    CostItem(key="arrival_drinks", label="Arrival / reception drinks", category="Food & Drink",
             unit_cost=12, scaling="per_guest", priority=2),
    CostItem(key="evening_food", label="Evening food & snacks", category="Food & Drink",
             unit_cost=8, scaling="per_guest", priority=2),
    CostItem(key="cake", label="Wedding cake", category="Food & Drink",
             unit_cost=620, scaling="fixed", priority=2),
    CostItem(key="celebrant", label="Celebrant / officiant", category="Ceremony",
             unit_cost=500, scaling="fixed", priority=3),
    CostItem(key="photographer", label="Photographer", category="Photo & Film",
             unit_cost=2400, scaling="fixed", priority=3),
    CostItem(key="videographer", label="Videographer", category="Photo & Film",
             unit_cost=2000, scaling="fixed", priority=3),
    CostItem(key="band", label="Wedding band", category="Entertainment",
             unit_cost=2500, scaling="fixed", priority=4),
    CostItem(key="dj", label="DJ", category="Entertainment",
             unit_cost=700, scaling="fixed", priority=4),
    CostItem(key="centrepieces", label="Table centrepieces", category="Flowers & Decor",
             unit_cost=70, scaling="per_table", priority=5),
    CostItem(key="bouquets", label="Bouquets & ceremony flowers", category="Flowers & Decor",
             unit_cost=450, scaling="fixed", priority=5),
    CostItem(key="decor", label="Styling & extra decor", category="Flowers & Decor",
             unit_cost=800, scaling="fixed", priority=5),
    CostItem(key="dress", label="Wedding dress", category="Attire",
             unit_cost=2000, scaling="fixed", priority=6),
    CostItem(key="suit", label="Groom's attire", category="Attire",
             unit_cost=750, scaling="fixed", priority=6),
    CostItem(key="party_attire", label="Bridal party attire", category="Attire",
             unit_cost=1200, scaling="fixed", priority=6),
    CostItem(key="hair_makeup", label="Hair & makeup", category="Attire",
             unit_cost=500, scaling="fixed", priority=6),
    CostItem(key="rings", label="Wedding rings (pair)", category="Extras",
             unit_cost=1700, scaling="fixed", priority=7),
    CostItem(key="transport", label="Wedding cars / transport", category="Extras",
             unit_cost=550, scaling="fixed", priority=7),
    CostItem(key="stationery", label="Invitations & stationery", category="Extras",
             unit_cost=5, scaling="per_guest", priority=7),
    CostItem(key="favours", label="Wedding favours", category="Extras",
             unit_cost=5, scaling="per_guest", priority=7),
]


def units_needed(item: CostItem, cfg: BudgetConfig) -> int:
    """How many units of this item the wedding needs at the current config."""
    if item.scaling == "per_guest":
        return cfg.guests
    if item.scaling == "per_table":
        return tables_for(cfg)
    return 1


def tables_for(cfg: BudgetConfig) -> int:
    return math.ceil(cfg.guests / cfg.seats_per_table)


def line_total(item: CostItem, cfg: BudgetConfig) -> float:
    return item.unit_cost * units_needed(item, cfg)


def compute_budget(cfg: BudgetConfig, items: list[CostItem]) -> dict:
    """Full budget summary + per-item affordability waterfall.

    Items are funded in (priority, key) order. Each item reports how many of its
    needed units the remaining savings pool covers, with a funded/partial/unfunded
    status. Contingency is shown as a final buffer card and folded into the goal.
    """
    ordered = sorted(items, key=lambda i: (i.priority, i.key))
    tables = tables_for(cfg)

    subtotal = sum(line_total(i, cfg) for i in items)
    contingency = subtotal * cfg.contingency_pct / 100.0
    grand_total = subtotal + contingency

    pool = cfg.savings
    item_rows: list[dict] = []
    for item in ordered:
        need = units_needed(item, cfg)
        total = item.unit_cost * need
        if total <= 0:
            covered, status, pct = need, "funded", 100.0
        elif pool >= total:
            covered, status, pct = need, "funded", 100.0
            pool -= total
        elif pool <= 0:
            covered, status, pct = 0, "unfunded", 0.0
        else:
            # Partial. Per-unit items show whole units covered; fixed items can't be
            # partially bought, so covered stays 0 but pct reflects progress.
            pct = round(pool / total * 100.0, 1)
            covered = min(need, int(pool // item.unit_cost)) if item.unit_cost > 0 else need
            status = "partial"
            pool = 0.0
        item_rows.append({
            "key": item.key,
            "label": item.label,
            "category": item.category,
            "scaling": item.scaling,
            "unit_cost": item.unit_cost,
            "priority": item.priority,
            "note": item.note,
            "units_needed": need,
            "units_covered": covered,
            "line_total": total,
            "pct_funded": pct,
            "status": status,
        })

    # Contingency buffer card (funded last from whatever pool remains).
    if contingency > 0:
        if pool >= contingency:
            buf_status, buf_pct = "funded", 100.0
        elif pool <= 0:
            buf_status, buf_pct = "unfunded", 0.0
        else:
            buf_status, buf_pct = "partial", round(pool / contingency * 100.0, 1)
        item_rows.append({
            "key": "_contingency", "label": "Contingency buffer", "category": "Buffer",
            "scaling": "fixed", "unit_cost": contingency, "priority": 99, "note":
            f"{cfg.contingency_pct:g}% safety net", "units_needed": 1,
            "units_covered": 1 if buf_status == "funded" else 0,
            "line_total": contingency, "pct_funded": buf_pct, "status": buf_status,
        })

    pct_funded = round(cfg.savings / grand_total * 100.0, 1) if grand_total > 0 else 100.0
    return {
        "config": cfg.model_dump(),
        "tables": tables,
        "subtotal": subtotal,
        "contingency": contingency,
        "grand_total": grand_total,
        "savings": cfg.savings,
        "pct_funded": pct_funded,
        "gap": max(0.0, grand_total - cfg.savings),
        "fully_funded": cfg.savings >= grand_total,
        "items": item_rows,
    }


class BudgetStore:
    """Typed read/write of wedding config + cost items in AgentDB."""

    def __init__(self, db_path: str | Path = DEFAULT_WEDDING_DB):
        self._db = AgentDB(db_path)

    def close(self) -> None:
        self._db.close()

    def get_config(self) -> BudgetConfig:
        raw = self._db.get_state(DB_AGENT, CONFIG_KEY)
        return BudgetConfig(**raw) if raw else BudgetConfig()

    def save_config(self, cfg: BudgetConfig) -> None:
        self._db.set_state(DB_AGENT, CONFIG_KEY, cfg.model_dump(mode="json"))

    def get_items(self) -> list[CostItem]:
        raw = self._db.get_state(DB_AGENT, ITEMS_KEY)
        if not raw:
            self.save_items(DEFAULT_ITEMS)
            return list(DEFAULT_ITEMS)
        return [CostItem(**r) for r in raw]

    def save_items(self, items: list[CostItem]) -> None:
        self._db.set_state(DB_AGENT, ITEMS_KEY, [i.model_dump(mode="json") for i in items])

    def reset(self) -> None:
        self.save_items(DEFAULT_ITEMS)
        self.save_config(BudgetConfig())
