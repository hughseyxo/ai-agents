"""Data model + affordability engine for the Villa Grabau (Italy) wedding PWA.

Sibling of ``wedding_ui`` — same BudgetStore/waterfall engine, but seeded for a
destination wedding at **Villa Grabau**, a 16th-century villa in a nine-hectare
botanical park at San Pancrazio, just outside Lucca in Tuscany.

Two things differ from the Irish app beyond the numbers:

* **Scale.** Villa Grabau hosts roughly 50–140 guests, so the default config is
  120 guests / 12 tables rather than 350.
* **Destination line items.** A wedding abroad carries costs an Irish hotel
  wedding does not: an Italy-based planner, the comune's civil-ceremony fee,
  nulla osta / Atto Notorio paperwork and translations, guest shuttles from
  Lucca, a welcome dinner, a wet-weather marquee and the SIAE music licence.

Costs are researched 2026 mid-range Tuscany averages (see DEFAULT_ITEMS notes);
every figure is editable in-app and ``reset`` restores these defaults.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agents.db import AgentDB

DB_AGENT = "grabau-budget"
CONFIG_KEY = "config"
ITEMS_KEY = "items"

# Dedicated db file, independent of both agents.db and the Irish wedding.db.
DEFAULT_GRABAU_DB = Path(__file__).resolve().parent.parent / "data" / "grabau.db"

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
    guests: int = Field(default=120, ge=1, le=5000)
    seats_per_table: int = Field(default=10, ge=1, le=50)
    savings: float = Field(default=0.0, ge=0)
    contingency_pct: float = Field(default=10.0, ge=0, le=100)


# Tuscany / Villa Grabau 2026 mid-range averages (editable in-app). priority
# drives the waterfall: venue, planner and feeding people are funded first.
DEFAULT_ITEMS: list[CostItem] = [
    # --- Venue ---
    CostItem(key="villa_hire", label="Villa Grabau exclusive hire", category="Venue",
             unit_cost=12000, scaling="fixed", priority=1,
             note="Villa + gardens, lemon house & Verzura theatre. Tuscan villa hire 2026: €10–25k"),
    CostItem(key="villa_accommodation", label="On-site villa accommodation (wedding party)",
             category="Venue", unit_cost=6000, scaling="fixed", priority=6,
             note="Casa di Annadora / Orazio / La Stalletta / L'Arancera — ~55 beds, multi-night"),
    CostItem(key="marquee", label="Marquee / wet-weather backup", category="Venue",
             unit_cost=5000, scaling="fixed", priority=4, note="Tuscany 2026: €3–15k"),
    # --- Planning ---
    CostItem(key="planner", label="Wedding planner (Italy-based, full service)", category="Planning",
             unit_cost=9000, scaling="fixed", priority=2,
             note="Effectively mandatory for a destination wedding. Partial €5–12k, full €12–35k"),
    CostItem(key="scouting_travel", label="Scouting trips & couple's travel", category="Planning",
             unit_cost=2500, scaling="fixed", priority=7, note="Flights + stays for venue/vendor visits"),
    # --- Food & Drink ---
    CostItem(key="catering", label="Catering / wedding banquet", category="Food & Drink",
             unit_cost=185, scaling="per_guest", priority=2,
             note="Tuscany 2026 plated dinner: €150–220/head"),
    CostItem(key="bar_service", label="Bar & wine service", category="Food & Drink",
             unit_cost=30, scaling="per_guest", priority=2, note="Tuscany 2026: €25–50/head"),
    CostItem(key="aperitivo", label="Arrival aperitivo & prosecco", category="Food & Drink",
             unit_cost=22, scaling="per_guest", priority=2),
    CostItem(key="welcome_dinner", label="Welcome dinner (night before)", category="Food & Drink",
             unit_cost=55, scaling="per_guest", priority=6, note="Destination-wedding staple"),
    CostItem(key="late_night", label="Late-night food", category="Food & Drink",
             unit_cost=15, scaling="per_guest", priority=5),
    CostItem(key="cake", label="Wedding cake", category="Food & Drink",
             unit_cost=900, scaling="fixed", priority=3, note="Tuscany 2026: €500–1,500"),
    # --- Ceremony ---
    CostItem(key="civil_ceremony", label="Civil ceremony fee (Comune di Lucca)", category="Ceremony",
             unit_cost=1500, scaling="fixed", priority=3,
             note="Foreign-couple rates vary by comune; Florence runs €2.5–5.1k"),
    CostItem(key="legal_paperwork", label="Nulla osta, Atto Notorio & translations", category="Ceremony",
             unit_cost=600, scaling="fixed", priority=3,
             note="Consular paperwork + sworn translations, €300–800"),
    CostItem(key="celebrant", label="Celebrant / symbolic officiant", category="Ceremony",
             unit_cost=600, scaling="fixed", priority=4),
    CostItem(key="ceremony_music", label="Ceremony music (string quartet)", category="Ceremony",
             unit_cost=1000, scaling="fixed", priority=5, note="Tuscany 2026: €500–1,500"),
    # --- Photo & Film ---
    CostItem(key="photographer", label="Photographer", category="Photo & Film",
             unit_cost=4500, scaling="fixed", priority=3, note="Tuscany 2026: €2.5–10k"),
    CostItem(key="videographer", label="Videographer", category="Photo & Film",
             unit_cost=3500, scaling="fixed", priority=4, note="Tuscany 2026: €2–8k"),
    # --- Entertainment ---
    CostItem(key="band", label="Live band", category="Entertainment",
             unit_cost=4000, scaling="fixed", priority=4, note="Tuscany 2026: €3–8k"),
    CostItem(key="dj", label="DJ", category="Entertainment",
             unit_cost=1200, scaling="fixed", priority=5, note="Tuscany 2026: €800–2,000"),
    CostItem(key="siae_licence", label="SIAE music licence", category="Entertainment",
             unit_cost=450, scaling="fixed", priority=4,
             note="Mandatory Italian performing-rights fee, ~€270–580"),
    # --- Flowers & Decor ---
    CostItem(key="centrepieces", label="Table centrepieces", category="Flowers & Decor",
             unit_cost=120, scaling="per_table", priority=5),
    CostItem(key="bouquets", label="Bouquets & ceremony flowers", category="Flowers & Decor",
             unit_cost=1800, scaling="fixed", priority=5),
    CostItem(key="styling_lighting", label="Styling, lighting & AV", category="Flowers & Decor",
             unit_cost=2500, scaling="fixed", priority=5, note="Garden festoons + AV, €1–5k"),
    # --- Attire ---
    CostItem(key="dress", label="Wedding dress", category="Attire",
             unit_cost=2500, scaling="fixed", priority=6),
    CostItem(key="suit", label="Groom's attire", category="Attire",
             unit_cost=900, scaling="fixed", priority=6),
    CostItem(key="party_attire", label="Bridal party attire", category="Attire",
             unit_cost=1400, scaling="fixed", priority=7),
    CostItem(key="hair_makeup", label="Hair & makeup", category="Attire",
             unit_cost=1200, scaling="fixed", priority=6, note="Bride + party, €800–2,000"),
    # --- Extras ---
    CostItem(key="guest_shuttle", label="Guest shuttles (Lucca ⇄ villa)", category="Extras",
             unit_cost=2000, scaling="fixed", priority=5, note="Tuscany 2026: €1–3k"),
    CostItem(key="rings", label="Wedding rings (pair)", category="Extras",
             unit_cost=1800, scaling="fixed", priority=7),
    CostItem(key="couple_car", label="Couple's car", category="Extras",
             unit_cost=600, scaling="fixed", priority=8),
    CostItem(key="stationery", label="Invitations & stationery", category="Extras",
             unit_cost=8, scaling="per_guest", priority=7),
    CostItem(key="welcome_bags", label="Welcome bags", category="Extras",
             unit_cost=18, scaling="per_guest", priority=8, note="Tuscany 2026: €10–30/guest"),
    CostItem(key="favours", label="Wedding favours (bomboniere)", category="Extras",
             unit_cost=7, scaling="per_guest", priority=8),
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
    """Typed read/write of Villa Grabau config + cost items in AgentDB."""

    def __init__(self, db_path: str | Path = DEFAULT_GRABAU_DB):
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
