(() => {
  class Events {
    constructor() { this.handlers = {}; }
    on(names, fn) { String(names).split(/\s+/).forEach(name => (this.handlers[name] ||= []).push(fn)); return this; }
    fire(name, value = {}) { (this.handlers[name] || []).forEach(fn => fn(value)); return this; }
  }
  class Layer extends Events {
    addTo(map) { this._map = map; map._layers.add(this); this._attach?.(); return this; }
  }
  class Marker extends Layer {
    constructor(latlng, options = {}) { super(); this.latlng = latlng; this.options = options; }
    _attach() {
      if (this._el || !this._map) return;
      const el = document.createElement('div');
      el.className = `leaflet-marker-icon ${this.options.icon?.options?.className || ''}`;
      el.innerHTML = this.options.icon?.options?.html || '';
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
    setTooltipContent(html) { if (this.tooltip) this.tooltip.html = html; return this; }
    bindPopup() { return this; }
    openPopup() { return this; }
    openTooltip() { return this; }
    setLatLng(latlng) { this.latlng = latlng; return this; }
    setIcon(icon) { this.options.icon = icon; if (this._el) { this._el.className = `leaflet-marker-icon ${icon.options?.className || ''}`; this._el.innerHTML = icon.options?.html || ''; } return this; }
    setZIndexOffset() { return this; }
  }
  class Group extends Layer {
    constructor() { super(); this.layers = new Set(); }
    addLayer(layer) { this.layers.add(layer); if (this._map) { layer._map = this._map; layer._attach?.(); } return this; }
    removeLayer(layer) { this.layers.delete(layer); layer._el?.remove(); layer._el = null; return this; }
    addTo(map) { super.addTo(map); this.layers.forEach(layer => { layer._map = map; layer._attach?.(); }); return this; }
    refreshClusters() { return this; }
  }
  class MapStub extends Events {
    constructor(id) {
      super(); this._container = document.getElementById(id); this._layers = new Set(); this._zoom = 6;
      this._center = { lat: 54.1, lng: -2.5 }; this._panes = {};
      this._container.classList.add('leaflet-container');
      this._markerPane = document.createElement('div'); this._markerPane.className = 'leaflet-marker-pane';
      this._container.appendChild(this._markerPane);
    }
    setView(latlng, zoom) { this._center = { lat: Number(latlng[0]), lng: Number(latlng[1]) }; if (zoom != null) this._zoom = zoom; return this; }
    panTo(latlng) { this._center = { lat: Number(latlng[0]), lng: Number(latlng[1]) }; return this; }
    getCenter() { return { ...this._center }; }
    getZoom() { return this._zoom; }
    setZoom(zoom) { this._zoom = zoom; return this; }
    getBounds() { return { getWest:()=>-12, getSouth:()=>49, getEast:()=>4, getNorth:()=>61, contains:()=>true }; }
    fitBounds() { return this; }
    createPane(name) { const pane = document.createElement('div'); this._panes[name] = pane; return pane; }
    getPane(name) { return this._panes[name]; }
    hasLayer(layer) { return this._layers.has(layer); }
    removeLayer(layer) { this._layers.delete(layer); layer._el?.remove(); layer._el = null; return this; }
    invalidateSize() { return this; }
  }
  const passive = () => new Layer();
  window.L = {
    map: id => new MapStub(id),
    tileLayer: passive,
    marker: (latlng, options) => new Marker(latlng, options),
    markerClusterGroup: () => new Group(),
    layerGroup: () => new Group(),
    divIcon: options => ({ options }),
    circle: passive,
    circleMarker: passive,
    polyline: passive,
    heatLayer: passive,
    latLngBounds: value => value,
    popup: () => ({ setLatLng() { return this; }, setContent() { return this; }, openOn() { return this; } })
  };
})();
