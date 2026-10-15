const READ_KEY = "wok-tracker-read-chapters";

function wokApp() {
  return {
    chapters: [],
    currentId: null,
    detail: null,
    mapSvg: "",
    readSet: new Set(),
    mapZoom: 1,
    mapPan: { x: 0, y: 0 },

    async init() {
      const v = window.WOK_ASSET_VERSION || "";
      const [chaptersRes, mapRes] = await Promise.all([
        fetch("/api/chapters"),
        fetch(`/static/map.svg?v=${v}`),
      ]);
      this.chapters = await chaptersRes.json();
      this.mapSvg = await mapRes.text();
      this.loadReadSet();

      const savedLast = localStorage.getItem("wok-tracker-last-chapter");
      this.currentId = savedLast && this.chapters.some(c => c.id === savedLast)
        ? savedLast
        : (this.chapters[0] ? this.chapters[0].id : null);

      await this.load();
      this.$nextTick(() => this.setupMapControls());
    },

    zoomBy(delta) {
      const next = Math.min(3.5, Math.max(1, this.mapZoom + delta));
      this.mapZoom = next;
      if (next === 1) this.mapPan = { x: 0, y: 0 };
      else this.clampPan();
    },

    zoomReset() {
      this.mapZoom = 1;
      this.mapPan = { x: 0, y: 0 };
    },

    clampPan() {
      const vp = this.$refs.mapViewport;
      if (!vp) return;
      const rect = vp.getBoundingClientRect();
      // transform-origin is top-left (0 0), so zoomed content only overflows
      // to the right/bottom — valid pan range is [-(overflow), 0], not symmetric.
      const overflowX = rect.width * (this.mapZoom - 1);
      const overflowY = rect.height * (this.mapZoom - 1);
      this.mapPan.x = Math.min(0, Math.max(-overflowX, this.mapPan.x));
      this.mapPan.y = Math.min(0, Math.max(-overflowY, this.mapPan.y));
    },

    setupMapControls() {
      const vp = this.$refs.mapViewport;
      if (!vp) return;

      // Mouse wheel zoom.
      vp.addEventListener("wheel", (e) => {
        e.preventDefault();
        this.zoomBy(e.deltaY < 0 ? 0.3 : -0.3);
      }, { passive: false });

      // Mouse drag pan.
      let dragging = false, lastX = 0, lastY = 0;
      vp.addEventListener("mousedown", (e) => {
        if (this.mapZoom === 1) return;
        dragging = true;
        vp.classList.add("panning");
        lastX = e.clientX; lastY = e.clientY;
      });
      window.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        this.mapPan.x += e.clientX - lastX;
        this.mapPan.y += e.clientY - lastY;
        lastX = e.clientX; lastY = e.clientY;
        this.clampPan();
      });
      window.addEventListener("mouseup", () => {
        dragging = false;
        vp.classList.remove("panning");
      });

      // Touch: one-finger pan, two-finger pinch zoom.
      let touchLastX = 0, touchLastY = 0, pinchStartDist = 0, pinchStartZoom = 1;
      const dist = (t0, t1) => Math.hypot(t0.clientX - t1.clientX, t0.clientY - t1.clientY);

      vp.addEventListener("touchstart", (e) => {
        if (e.touches.length === 2) {
          pinchStartDist = dist(e.touches[0], e.touches[1]);
          pinchStartZoom = this.mapZoom;
        } else if (e.touches.length === 1 && this.mapZoom > 1) {
          touchLastX = e.touches[0].clientX;
          touchLastY = e.touches[0].clientY;
        }
      }, { passive: true });

      vp.addEventListener("touchmove", (e) => {
        if (e.touches.length === 2) {
          e.preventDefault();
          const scale = dist(e.touches[0], e.touches[1]) / (pinchStartDist || 1);
          this.mapZoom = Math.min(3.5, Math.max(1, pinchStartZoom * scale));
          this.clampPan();
        } else if (e.touches.length === 1 && this.mapZoom > 1) {
          e.preventDefault();
          this.mapPan.x += e.touches[0].clientX - touchLastX;
          this.mapPan.y += e.touches[0].clientY - touchLastY;
          touchLastX = e.touches[0].clientX;
          touchLastY = e.touches[0].clientY;
          this.clampPan();
        }
      }, { passive: false });

      vp.addEventListener("touchend", (e) => {
        if (e.touches.length === 0 && this.mapZoom === 1) {
          this.mapPan = { x: 0, y: 0 };
        }
      });
    },

    optionLabel(c) {
      return c.part === "Interludes" || c.number === null ? c.label : `${c.label}`;
    },

    async load() {
      if (!this.currentId) return;
      localStorage.setItem("wok-tracker-last-chapter", this.currentId);
      const res = await fetch(`/api/chapters/${this.currentId}`);
      this.detail = res.ok ? await res.json() : null;
      this.$nextTick(() => this.highlightMap());
    },

    highlightMap() {
      document.querySelectorAll(".map-zoom-target .region").forEach(el => el.classList.remove("active"));
      if (this.detail && this.detail.location) {
        const el = document.getElementById(`region-${this.detail.location}`);
        if (el) el.classList.add("active");
      }
    },

    indexOfCurrent() {
      return this.chapters.findIndex(c => c.id === this.currentId);
    },

    atStart() {
      return this.indexOfCurrent() <= 0;
    },

    atEnd() {
      return this.indexOfCurrent() >= this.chapters.length - 1;
    },

    step(delta) {
      const idx = this.indexOfCurrent() + delta;
      if (idx < 0 || idx >= this.chapters.length) return;
      this.currentId = this.chapters[idx].id;
      this.load();
    },

    loadReadSet() {
      try {
        const raw = localStorage.getItem(READ_KEY);
        this.readSet = new Set(raw ? JSON.parse(raw) : []);
      } catch {
        this.readSet = new Set();
      }
    },

    saveReadSet() {
      localStorage.setItem(READ_KEY, JSON.stringify([...this.readSet]));
    },

    isRead(id) {
      return this.readSet.has(id);
    },

    toggleRead(id) {
      const idx = this.chapters.findIndex(c => c.id === id);
      if (this.readSet.has(id)) {
        this.readSet.delete(id);
      } else {
        // Reading is linear: marking a chapter read implies everything before it too.
        for (let i = 0; i <= idx; i++) {
          this.readSet.add(this.chapters[i].id);
        }
      }
      this.saveReadSet();
    },
  };
}
