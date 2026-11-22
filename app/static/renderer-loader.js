// Starts loading the WebGL live-object renderer (MapLibre) for moving trains
// and aircraft. A file rather than an inline script, so the site's Content
// Security Policy (script-src 'self') allows it; app.js falls back to Leaflet
// markers if it does not load.
window.UKActivityLiveRendererModule = import('/static/live-object-renderer.js?v=16376');
