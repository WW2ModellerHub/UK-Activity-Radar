(() => {
  const stats = window.__leafletStats = window.__leafletStats || {
    markersCreated: 0,
    markerMoves: 0,
    markerIconUpdates: 0,
    markerTooltipUpdates: 0,
    clusterRefreshes: 0,
    heatLayersCreated: 0,
    heatLayerUpdates: 0,
    heatLayersRemoved: 0,
  };
  class Events {
    constructor() { this.handlers = {}; }
    on(names, fn) { String(names).split(/\s+/).forEach(name => (this.handlers[name] ||= []).push(fn)); return this; }
    fire(name, value = {}) { (this.handlers[name] || []).forEach(fn => fn(value)); return this; }
  }
  class Layer extends Events {
    addTo(map) { this._map = map; map._layers.add(this); this._attach?.(); return this; }
  }
  class Marker extends Layer {
    constructor(latlng, options = {}) { super(); this.latlng = latlng; this.options = options; stats.markersCreated += 1; }
    _attach() {
      if (this._el || !this._map) return;
      const el = document.createElement('div');
      el.className = `leaflet-marker-icon ${this.options.icon?.options?.className || ''}`;
      el.innerHTML = this.options.icon?.options?.html || '';
      el.dataset.clustered = String(Boolean(this._clustered));
      el.style.position = 'relative';
      el.style.display = 'inline-block';
      el.style.margin = '4px';
      el.addEventListener('click', () => this.fire('click'));
      el.addEventListener('mouseenter', () => {
        if (!this.tooltip) return;
        const tip = document.createElement('div');
        tip.className = `leaflet-tooltip ${this.tooltip.options?.className || ''}`;
        tip.innerHTML = this.tooltip.html;
        this._map._container.appendChild(tip); this._tooltipEl = tip;
      });
      el.addEventListener('mouseleave', () => this._tooltipEl?.remove());
      this._map._markerPane.appendChild(el); this._el = el;
    }
    bindTooltip(html, options) { this.tooltip = { html, options }; return this; }
    getTooltip() { return this.tooltip || null; }
    setTooltipContent(html) { if (this.tooltip) this.tooltip.html = html; stats.markerTooltipUpdates += 1; return this; }
    bindPopup() { return this; }
    openPopup() { return this; }
    openTooltip() { return this; }
    setLatLng(latlng) { this.latlng = latlng; stats.markerMoves += 1; return this; }
    setIcon(icon) { this.options.icon = icon; stats.markerIconUpdates += 1; if (this._el) { this._el.className = `leaflet-marker-icon ${icon.options?.className || ''}`; this._el.innerHTML = icon.options?.html || ''; } return this; }
    setZIndexOffset() { return this; }
  }
  class Group extends Layer {
    constructor(clustered = false) { super(); this.layers = new Set(); this.clustered = clustered; }
    addLayer(layer) { this.layers.add(layer); layer._clustered = this.clustered; if (this._map) { layer._map = this._map; layer._attach?.(); if(layer._el)layer._el.dataset.clustered=String(this.clustered); } return this; }
    removeLayer(layer) { this.layers.delete(layer); layer._el?.remove(); layer._el = null; return this; }
    addTo(map) { super.addTo(map); this.layers.forEach(layer => { layer._map = map; layer._attach?.(); }); return this; }
    refreshClusters() { stats.clusterRefreshes += 1; return this; }
  }
  class HeatLayer extends Layer {
    constructor(points = [], options = {}) {
      super(); this.points = points; this.options = options; this._isHeatLayer = true;
      stats.heatLayersCreated += 1;
    }
    _attach() {
      if (this._el || !this._map) return;
      const el = document.createElement('div');
      el.className = 'leaflet-heat-layer';
      el.setAttribute('aria-hidden', 'true');
      this._map._container.appendChild(el); this._el = el;
      this._sync();
    }
    _sync() {
      if (this._el) this._el.dataset.pointCount = String(this.points.length);
      stats.heatLayerPoints = this.points.length;
    }
    setLatLngs(points = []) { this.points = points; stats.heatLayerUpdates += 1; this._sync(); return this; }
  }
  class MapStub extends Events {
    constructor(id) {
      super(); this._container = document.getElementById(id); this._layers = new Set(); this._zoom = 6;
      this._center = { lat: 54.1, lng: -2.5 }; this._panes = {};
      this._container.classList.add('leaflet-container');
      this._markerPane = document.createElement('div'); this._markerPane.className = 'leaflet-marker-pane';
      this._container.appendChild(this._markerPane);
      this._container.addEventListener('click', event => {
        if (event.target === this._container) this.fire('click', { latlng:this.getCenter(), originalEvent:event });
      });
    }
    syncView() { this._container.dataset.mapZoom = String(this._zoom); this._container.dataset.mapCenter = `${this._center.lat},${this._center.lng}`; }
    setView(latlng, zoom) { this._center = { lat: Number(latlng[0]), lng: Number(latlng[1]) }; if (zoom != null) this._zoom = zoom; this.syncView(); return this; }
    panTo(latlng) { this._center = { lat: Number(latlng[0]), lng: Number(latlng[1]) }; this.syncView(); return this; }
    getCenter() { return { ...this._center }; }
    getZoom() { return this._zoom; }
    setZoom(zoom) { this._zoom = zoom; this.syncView(); return this; }
    getBounds() { return { getWest:()=>-12, getSouth:()=>49, getEast:()=>4, getNorth:()=>61, contains:()=>true }; }
    fitBounds() { return this; }
    createPane(name) { const pane = document.createElement('div'); this._panes[name] = pane; return pane; }
    getPane(name) { return this._panes[name]; }
    hasLayer(layer) { return this._layers.has(layer); }
    removeLayer(layer) { this._layers.delete(layer); if (layer._isHeatLayer) stats.heatLayersRemoved += 1; layer._el?.remove(); layer._el = null; return this; }
    invalidateSize() { return this; }
  }
  const passive = () => new Layer();
  window.L = {
    map: id => new MapStub(id),
    tileLayer: passive,
    marker: (latlng, options) => new Marker(latlng, options),
    markerClusterGroup: () => new Group(true),
    layerGroup: () => new Group(),
    divIcon: options => ({ options }),
    circle: passive,
    circleMarker: passive,
    polyline: passive,
    heatLayer: (points, options) => new HeatLayer(points, options),
    latLngBounds: value => value,
    popup: () => ({ setLatLng() { return this; }, setContent() { return this; }, openOn() { return this; } })
  };
})();
