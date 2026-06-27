function weddingApp() {
  return {
    view: 'overview',
    loaded: false,
    b: { config: { guests: 350, seats_per_table: 10, savings: 0, contingency_pct: 10 },
         tables: 0, subtotal: 0, contingency: 0, grand_total: 0, savings: 0,
         pct_funded: 0, gap: 0, fully_funded: false, items: [] },
    nu: { key: '', label: '', category: '', unit_cost: null, scaling: 'fixed', priority: 5 },

    get ringCirc() { return 2 * Math.PI * 86; },

    async init() {
      await this.load();
      this.loaded = true;
    },

    async load() {
      try {
        const r = await fetch('/api/budget');
        this.b = await r.json();
      } catch (e) { console.error('load failed', e); }
    },

    async setConfig(patch) {
      const r = await fetch('/api/config', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (r.ok) this.b = await r.json();
    },

    async setItem(key, patch) {
      const r = await fetch(`/api/items/${encodeURIComponent(key)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (r.ok) this.b = await r.json();
    },

    async delItem(key) {
      const r = await fetch(`/api/items/${encodeURIComponent(key)}`, { method: 'DELETE' });
      if (r.ok) this.b = await r.json();
    },

    async addItem() {
      if (!this.nu.key || !this.nu.label || !this.nu.category || this.nu.unit_cost == null) return;
      const r = await fetch('/api/items', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...this.nu, category: this.nu.category || 'Extras' }),
      });
      if (r.ok) {
        this.b = await r.json();
        this.nu = { key: '', label: '', category: '', unit_cost: null, scaling: 'fixed', priority: 5 };
      }
    },

    async reset() {
      if (!confirm('Reset all costs and config to the Irish averages?')) return;
      const r = await fetch('/api/reset', { method: 'POST' });
      if (r.ok) this.b = await r.json();
    },

    // ---- formatting / derived ----
    money(n) {
      return '€' + Math.round(n || 0).toLocaleString('en-IE');
    },

    scalingText(it) {
      if (it.scaling === 'per_guest') return `€${it.unit_cost}/guest × ${this.b.config.guests}`;
      if (it.scaling === 'per_table') return `€${it.unit_cost}/table × ${this.b.tables}`;
      return 'one-off';
    },

    badgeText(it) {
      if (it.status === 'funded') return 'Covered ✓';
      if (it.status === 'unfunded') return 'Not yet';
      return `${it.pct_funded}%`;
    },

    coverageText(it) {
      const unit = it.scaling === 'per_guest' ? 'guests' : it.scaling === 'per_table' ? 'tables' : null;
      if (it.status === 'funded') {
        return unit ? `All ${it.units_needed} ${unit} covered` : `Fully paid — ${this.money(it.line_total)}`;
      }
      if (unit) {
        return `${it.units_covered} of ${it.units_needed} ${unit} · ${this.money(it.line_total)} total`;
      }
      return it.status === 'unfunded'
        ? `Needs ${this.money(it.line_total)}`
        : `${it.pct_funded}% of ${this.money(it.line_total)}`;
    },

    affordHeadline() {
      const cat = this.b.items.find(i => i.key === 'catering');
      if (cat && cat.scaling === 'per_guest') {
        if (cat.status === 'funded') return `\u{1F389} The caterer is covered for all ${cat.units_needed} guests.`;
        return `That's the caterer for ${cat.units_covered} of ${cat.units_needed} guests so far.`;
      }
      return '';
    },

    categoryTotals() {
      const m = {};
      for (const it of this.b.items) {
        if (it.key === '_contingency') continue;
        m[it.category] = (m[it.category] || 0) + it.line_total;
      }
      const max = Math.max(1, ...Object.values(m));
      return Object.entries(m)
        .map(([name, total]) => ({ name, total, pct: Math.round(total / max * 100) }))
        .sort((a, b) => b.total - a.total);
    },

    groupedItems() {
      const order = [];
      const m = {};
      for (const it of this.b.items) {
        if (it.key === '_contingency') continue;
        if (!m[it.category]) { m[it.category] = []; order.push(it.category); }
        m[it.category].push(it);
      }
      return order.map(name => ({ name, items: m[name] }));
    },
  };
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(e => console.error('SW reg failed', e));
  });
}
