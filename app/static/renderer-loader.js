// Loads the WebGL live-object renderer (MapLibre, about 1.6 MB with its
// worker) for moving trains and aircraft. It is a file rather than an inline
// script so the Content Security Policy (script-src 'self') allows it, and it
// only starts loading when a moving layer is switched on, so pages without
// trains or aircraft never pay for it. app.js falls back to Leaflet markers
// if it does not load.
window.UKActivityLoadLiveRenderer = () => {
  window.UKActivityLiveRendererModule = window.UKActivityLiveRendererModule
    || import('/static/live-object-renderer.js?v=16380');
  return window.UKActivityLiveRendererModule;
};
