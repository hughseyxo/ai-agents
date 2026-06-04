function plantApp() {
  return {
    // Views and navigation
    currentView: 'dashboard', // dashboard, detail, form, photo
    activeFilter: 'all', // all, indoor, outdoor
    
    // Data stores
    plants: [],
    weatherData: [],
    agentStatus: {},
    activePlant: null,
    
    // UI Loading states
    weatherCount: 0,
    showWeatherModal: false,
    showStatusModal: false,
    activePlantLoading: false,
    formSubmitting: false,
    
    // Stats
    stats: {
      overdue: 0,
      healthy: 0
    },

    // Form data
    formMode: 'add', // add, edit
    formData: {
      name: '',
      frequency_days: 7,
      location: 'indoor',
      sunlight: 'partial shade',
      water_sensitivity: 'medium'
    },

    // Photo upload and diagnostic states
    photoSelected: false,
    photoUploading: false,
    diagnosticResult: null,

    init() {
      this.fetchPlants();
      this.fetchWeather();
      this.fetchStatus();
      
      // Auto refresh list every 60s
      setInterval(() => {
        if (this.currentView === 'dashboard') {
          this.fetchPlants();
        }
      }, 60000);
    },

    setView(view) {
      this.currentView = view;
      // Scroll to top
      window.scrollTo(0, 0);
    },

    setFilter(filter) {
      this.activeFilter = filter;
    },

    async fetchPlants() {
      try {
        const response = await fetch('/api/plants');
        if (!response.ok) throw new Error('Failed to fetch plants');
        const data = await response.json();
        
        // Add reactive loading status per plant card
        this.plants = data.map(p => ({ ...p, loading: false }));
        this.updateStats();
      } catch (err) {
        console.error('Error loading plants:', err);
      }
    },

    async fetchWeather() {
      try {
        const response = await fetch('/api/weather');
        if (!response.ok) throw new Error('Failed to fetch weather');
        this.weatherData = await response.json();
        
        // Filter weather cache to find active adjustments
        // A plant is adjusted if its entry exists (we can calculate count)
        this.weatherCount = this.weatherData.length;
      } catch (err) {
        console.error('Error loading weather:', err);
      }
    },

    async fetchStatus() {
      try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error('Failed to fetch status');
        this.agentStatus = await response.json();
      } catch (err) {
        console.error('Error loading status:', err);
      }
    },

    updateStats() {
      this.stats.overdue = this.plants.filter(p => p.overdue_days > 0).length;
      this.stats.healthy = this.plants.filter(p => p.overdue_days === 0 && p.status_label === 'Healthy').length;
    },

    filteredPlants() {
      if (this.activeFilter === 'all') return this.plants;
      return this.plants.filter(p => p.location === this.activeFilter);
    },

    async viewPlantDetail(name) {
      this.activePlantLoading = true;
      try {
        const response = await fetch(`/api/plants/${encodeURIComponent(name)}`);
        if (!response.ok) throw new Error('Failed to fetch plant detail');
        this.activePlant = await response.json();
        this.setView('detail');
      } catch (err) {
        alert('Error loading plant details: ' + err.message);
      } finally {
        this.activePlantLoading = false;
      }
    },

    async waterPlant(name) {
      // Find plant in list
      const plant = this.plants.find(p => p.name === name);
      if (plant) plant.loading = true;
      
      try {
        const response = await fetch(`/api/plants/${encodeURIComponent(name)}/water`, {
          method: 'POST'
        });
        if (!response.ok) throw new Error('Failed to water plant');
        
        // Refresh plants
        await this.fetchPlants();
      } catch (err) {
        alert('Failed to water plant: ' + err.message);
      } finally {
        if (plant) plant.loading = false;
      }
    },

    async waterActivePlant() {
      if (!this.activePlant) return;
      const name = this.activePlant.plant.name;
      this.activePlantLoading = true;
      
      try {
        const response = await fetch(`/api/plants/${encodeURIComponent(name)}/water`, {
          method: 'POST'
        });
        if (!response.ok) throw new Error('Failed to water plant');
        
        // Refresh detail view
        await this.viewPlantDetail(name);
        // Refresh dashboard list background
        this.fetchPlants();
      } catch (err) {
        alert('Failed to water plant: ' + err.message);
      } finally {
        this.activePlantLoading = false;
      }
    },

    async waterAll(location) {
      if (!confirm(`Are you sure you want to mark all ${location} plants as watered?`)) return;
      
      try {
        const response = await fetch('/api/plants/water-all', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ location })
        });
        if (!response.ok) throw new Error('Failed to water all plants');
        
        const result = await response.json();
        alert(`Successfully marked ${result.waterED_count} ${location} plants as watered.`);
        
        await this.fetchPlants();
      } catch (err) {
        alert('Failed: ' + err.message);
      }
    },

    openAddForm() {
      this.formMode = 'add';
      this.formData = {
        name: '',
        frequency_days: 7,
        location: 'indoor',
        sunlight: 'partial shade',
        water_sensitivity: 'medium'
      };
      this.setView('form');
    },

    openEditForm(plant) {
      this.formMode = 'edit';
      this.formData = {
        name: plant.name,
        frequency_days: plant.baseline_frequency_days || plant.frequency_days,
        location: plant.location,
        sunlight: plant.sunlight || 'partial shade',
        water_sensitivity: plant.water_sensitivity || 'medium'
      };
      this.setView('form');
    },

    cancelForm() {
      if (this.formMode === 'edit') {
        this.setView('detail');
      } else {
        this.setView('dashboard');
      }
    },

    async saveForm() {
      this.formSubmitting = true;
      try {
        let url = '/api/plants';
        let method = 'POST';
        
        if (this.formMode === 'edit') {
          url = `/api/plants/${encodeURIComponent(this.formData.name)}`;
          method = 'PATCH';
        }
        
        const bodyData = {
          name: this.formData.name,
          frequency_days: this.formData.frequency_days,
          location: this.formData.location,
          sunlight: this.formData.sunlight,
          water_sensitivity: this.formData.water_sensitivity
        };

        if (this.formMode === 'edit') {
          // Send baseline frequency update field
          bodyData.baseline_frequency_days = this.formData.frequency_days;
        }
        
        const response = await fetch(url, {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(bodyData)
        });
        
        if (!response.ok) {
          const errData = await response.json();
          throw new Error(errData.detail || 'Failed to save plant');
        }
        
        await this.fetchPlants();
        
        if (this.formMode === 'edit') {
          await this.viewPlantDetail(this.formData.name);
        } else {
          this.setView('dashboard');
        }
      } catch (err) {
        alert('Error: ' + err.message);
      } finally {
        this.formSubmitting = false;
      }
    },

    async deletePlant(name) {
      if (!confirm(`Are you sure you want to permanently remove "${name}"? This deletes the plant from the database and removes its profile document.`)) return;
      
      try {
        const response = await fetch(`/api/plants/${encodeURIComponent(name)}`, {
          method: 'DELETE'
        });
        if (!response.ok) throw new Error('Failed to delete plant');
        
        await this.fetchPlants();
        this.setView('dashboard');
      } catch (err) {
        alert('Failed: ' + err.message);
      }
    },

    // Photo and Vision actions
    openPhotoUpload() {
      this.photoSelected = false;
      this.photoUploading = false;
      this.diagnosticResult = null;
      this.setView('photo');
      
      // Reset input element value
      setTimeout(() => {
        const input = document.getElementById('plant-photo-input');
        if (input) input.value = '';
      }, 50);
    },

    handlePhotoSelect(event) {
      const file = event.target.files[0];
      if (file) {
        this.photoSelected = true;
        
        // Show preview
        const reader = new FileReader();
        reader.onload = function(e) {
          const preview = document.getElementById('photo-preview');
          if (preview) preview.src = e.target.result;
        };
        reader.readAsDataURL(file);
      }
    },

    clearPhoto() {
      this.photoSelected = false;
      this.diagnosticResult = null;
      const input = document.getElementById('plant-photo-input');
      if (input) input.value = '';
    },

    async submitPhoto() {
      const input = document.getElementById('plant-photo-input');
      const file = input.files[0];
      if (!file || !this.activePlant) return;
      
      this.photoUploading = true;
      this.diagnosticResult = null;
      
      const formData = new FormData();
      formData.append('file', file);
      
      try {
        const name = this.activePlant.plant.name;
        const response = await fetch(`/api/plants/${encodeURIComponent(name)}/photo`, {
          method: 'POST',
          body: formData
        });
        
        if (!response.ok) {
          const errData = await response.json();
          throw new Error(errData.detail || 'Analysis failed');
        }
        
        const result = await response.json();
        this.diagnosticResult = result;
        
        // Refresh active plant data in background
        this.fetchPlants();
      } catch (err) {
        alert('Diagnostic Failed: ' + err.message);
      } finally {
        this.photoUploading = false;
      }
    },

    async applySuggestedFrequency(days, reason) {
      if (!this.activePlant) return;
      const name = this.activePlant.plant.name;
      
      try {
        const response = await fetch(`/api/plants/${encodeURIComponent(name)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            baseline_frequency_days: days
          })
        });
        if (!response.ok) throw new Error('Failed to update frequency');
        
        alert(`Successfully set baseline frequency to ${days} days.`);
        this.diagnosticResult.frequency_suggestion = null;
        
        // Refresh data
        await this.viewPlantDetail(name);
        this.fetchPlants();
      } catch (err) {
        alert('Failed: ' + err.message);
      }
    },

    dismissSuggestedFrequency() {
      if (this.diagnosticResult) {
        this.diagnosticResult.frequency_suggestion = null;
      }
    },

    // Formatters
    formatDate(dateStr) {
      if (!dateStr) return 'Never';
      try {
        const date = new Date(dateStr);
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
      } catch (e) {
        return dateStr;
      }
    },

    formatDateTime(dateTimeStr) {
      if (!dateTimeStr) return 'Never';
      try {
        const date = new Date(dateTimeStr);
        return date.toLocaleDateString(undefined, { 
          month: 'short', 
          day: 'numeric', 
          hour: '2-digit', 
          minute: '2-digit' 
        });
      } catch (e) {
        return dateTimeStr;
      }
    },

    renderMarkdown(markdownStr) {
      if (!markdownStr) return '';
      return window.DOMPurify.sanitize(window.marked.parse(markdownStr));
    }
  };
}

// Register service worker
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then(reg => console.log('Service worker registered:', reg.scope))
      .catch(err => console.error('Service worker registration failed:', err));
  });
}
