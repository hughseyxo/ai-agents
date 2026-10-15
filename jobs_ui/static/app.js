// Job Hunt PWA — Alpine component over the JSON API in jobs_ui/server.py.
// All user/third-party text is rendered with x-text (never x-html).

function jobsApp() {
  return {
    tab: 'matches',
    loaded: false,
    error: '',
    cv: null,
    jobs: [],
    applications: [],
    queries: [],
    jobFilter: 'strong',
    jobFilters: [
      { key: 'strong', label: 'Strong (75+)' },
      { key: 'all', label: 'All open' },
      { key: 'applied', label: 'Applied / rejected' },
      { key: 'dismissed', label: 'Dismissed' },
    ],
    appStatuses: ['applied', 'interview', 'offer', 'rejected', 'withdrawn'],
    newApp: { company: '', role: '', status: 'applied' },
    newQuery: '',

    async init() {
      if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
      await Promise.all([this.loadJobs(), this.loadApplications(), this.loadQueries(), this.loadCv()]);
      this.loaded = true;
    },

    async api(path, opts = {}) {
      this.error = '';
      const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { const d = await res.json(); detail = typeof d.detail === 'string' ? d.detail : 'Invalid input'; } catch (_) {}
        this.error = detail;
        throw new Error(detail);
      }
      return res.status === 204 ? null : res.json();
    },

    jobQuery() {
      if (this.jobFilter === 'strong') return '?min_score=75';
      if (this.jobFilter === 'applied') return '?status=applied';
      if (this.jobFilter === 'dismissed') return '?status=dismissed';
      return '';
    },
    async loadJobs() {
      let jobs = await this.api('/api/jobs' + this.jobQuery());
      if (this.jobFilter === 'strong' || this.jobFilter === 'all') jobs = jobs.filter(j => j.status !== 'applied');
      this.jobs = jobs;
    },
    async setJobFilter(key) { this.jobFilter = key; await this.loadJobs(); },
    async setJobStatus(job, status) {
      await this.api(`/api/jobs/${encodeURIComponent(job.job_id)}`, { method: 'PATCH', body: { status } });
      await Promise.all([this.loadJobs(), this.loadApplications()]);
    },
    scoreClass(score) {
      if (score === null || score === undefined) return 'pending';
      if (score >= 85) return 'great';
      if (score >= 75) return 'good';
      if (score >= 50) return 'ok';
      return 'poor';
    },

    async loadApplications() { this.applications = await this.api('/api/applications'); },
    sortedApplications() {
      const order = Object.fromEntries(['interview', 'offer', 'applied', 'rejected', 'withdrawn'].map((s, i) => [s, i]));
      return [...this.applications].sort((a, b) => (order[a.status] - order[b.status]) || b.updated_at.localeCompare(a.updated_at));
    },
    async addApplication() {
      if (!this.newApp.company.trim()) return;
      await this.api('/api/applications', { method: 'POST', body: this.newApp });
      this.newApp = { company: '', role: '', status: 'applied' };
      await this.loadApplications();
    },
    async updateApplication(app, patch) {
      const updated = await this.api(`/api/applications/${app.id}`, { method: 'PATCH', body: patch });
      Object.assign(app, updated);
    },

    async loadQueries() { this.queries = await this.api('/api/queries'); },
    async addQuery() {
      if (!this.newQuery.trim()) return;
      await this.api('/api/queries', { method: 'POST', body: { query: this.newQuery } });
      this.newQuery = '';
      await this.loadQueries();
    },
    async deleteQuery(q) {
      await this.api(`/api/queries/${q.id}`, { method: 'DELETE' });
      await this.loadQueries();
    },

    async loadCv() { this.cv = (await this.api('/api/cv')).cv; },
  };
}
