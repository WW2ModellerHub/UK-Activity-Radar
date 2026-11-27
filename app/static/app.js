(() => {
  // MarkerCluster requires a map-level maxZoom immediately. The commercial
  // basemap is attached asynchronously, so it cannot be the first component
  // to supply this value.
  const DEFAULT_MAP_VIEW = [54.1, -2.5];
  const map = L.map('map', { zoomControl: true, preferCanvas: true, maxZoom: 20, doubleClickZoom: false }).setView(DEFAULT_MAP_VIEW, 6);
  map.attributionControl?.addAttribution?.('<a href="/data-licences">Rail data & track geometry licences</a>');
  let lastKnownDeviceLocation = null;
  let deviceLocationMarker = null;
  // The navigation drawer owns the left edge of the map on every breakpoint.
  // Keep Leaflet's controls on the opposite edge so they can never sit over it.
  map.zoomControl?.setPosition?.('bottomright');

  const tileSets = {standard:null,rail:null};
  let currentBase = null;
  let liveObjectRenderer = null;
  let bookingAffiliates = {};
  let baseMapConfigAttempt = 0;
  let baseMapRetryTimer = null;
  let baseMapConfigInFlight = false;
  // Hover cards only make sense with a mouse. On touch screens a tap would open
  // the card and the detail panel together, showing the same thing twice.
  const hoverCards = !!window.matchMedia?.('(hover: hover) and (pointer: fine)')?.matches;
  function bindHoverCard(marker, html, options) { if (hoverCards) marker.bindTooltip(html, options); return marker; }

  function baseTileLayer(config) {
    const direct=Boolean(config?.configured && config?.tile_url);
    return L.tileLayer(
      direct ? config.tile_url : '/api/map/tile/{z}/{x}/{y}.png?style=street-light',
      {
        maxZoom:direct ? Number(config.max_zoom || 20) : 22,
        tileSize:direct ? Number(config.tile_size || 512) : 256,
        zoomOffset:direct ? Number(config.zoom_offset ?? -1) : 0,
        keepBuffer:3,
        updateWhenIdle:false,
        updateWhenZooming:true,
        updateInterval:100,
        crossOrigin:direct,
        attribution:direct
          ? config.attribution
          : '<a href="/data-licences">Temporary map fallback &copy; TomTom</a>'
      }
    );
  }

  async function configureBaseMap() {
    if(baseMapConfigInFlight)return;
    baseMapConfigInFlight=true;
    let config=null;
    let configAvailable=false;
    // The public, read-only map configuration is embedded in the initial HTML
    // so the first tile request is not held behind another network round trip.
    // Keep the endpoint fallback for old/cached HTML and transient parse errors.
    try {
      const embedded=document.body?.dataset?.mapConfig;
      if(embedded){
        config=JSON.parse(embedded);
        configAvailable=Boolean(config&&typeof config==='object');
      }
    } catch {}
    const controller=typeof AbortController==='function'?new AbortController():null;
    const timeout=controller?window.setTimeout(()=>controller.abort(),6000):null;
    try {
      // Map provider credentials can be enabled after a deployment. Never keep
      // the earlier "not configured" response in a visitor's browser cache.
      if(!configAvailable){
        const response=await fetch('/api/map/config',{cache:'no-store',signal:controller?.signal});
        if(response.ok){
          config=await response.json();
          configAvailable=Boolean(config&&typeof config==='object');
        }
      }
      bookingAffiliates=(config&&config.affiliates)||{};
    } catch {}
    finally {
      if(timeout!==null)window.clearTimeout(timeout);
      baseMapConfigInFlight=false;
    }
    const mapNode=document.getElementById('map');
    if(!configAvailable){
      // A Render restart can briefly return 502 here. That is not evidence that
      // Mapbox is unconfigured: installing the tile proxy fallback would leave
      // a visitor with missing grey tile squares for the rest of the session.
      mapNode.dataset.mapProvider='loading';
      mapNode.setAttribute('aria-busy','true');
      baseMapConfigAttempt+=1;
      const retryDelay=Math.min(8000,750*(2**Math.min(baseMapConfigAttempt-1,4)));
      window.clearTimeout(baseMapRetryTimer);
      baseMapRetryTimer=window.setTimeout(configureBaseMap,retryDelay);
      return;
    }
    window.clearTimeout(baseMapRetryTimer);
    baseMapRetryTimer=null;
    baseMapConfigAttempt=0;
    mapNode.removeAttribute('aria-busy');
    if(config?.aircraft_attribution){
      map.attributionControl?.addAttribution?.(config.aircraft_attribution);
    }
    tileSets.standard=baseTileLayer(config);
    tileSets.rail=config?.configured&&config?.rail_tile_url
      ? baseTileLayer({...config,tile_url:config.rail_tile_url})
      : null;
    mapNode.dataset.mapProvider=config?.configured?'mapbox':'tomtom-fallback';
    syncBaseMap();
    liveRendererAllowed=config?.live_object_renderer!==false;
    ensureLiveRenderer();
  }

  // The WebGL renderer for moving trains and aircraft starts the first time
  // one of those layers is on; until it is ready, Leaflet markers show them.
  let liveRendererAllowed=false,liveRendererStarting=false;
  async function ensureLiveRenderer(){
    if(liveObjectRenderer||liveRendererStarting||!liveRendererAllowed)return;
    if(!enabledActivityTypes.has('train'))return;
    const load=window.UKActivityLoadLiveRenderer||(()=>window.UKActivityLiveRendererModule);
    if(!window.UKActivityMotionEngine||typeof load!=='function')return;
    liveRendererStarting=true;
    try{
      const module=await load();
      if(!module)throw new Error('renderer not available');
      liveObjectRenderer=new module.LiveObjectRenderer({
        container:document.getElementById('live-object-map'),leafMap:map,motionEngine:window.UKActivityMotionEngine,
        onReady:()=>renderMap(),
      });
      document.getElementById('map').dataset.liveRenderer='maplibre';
    }catch(error){
      document.getElementById('map').dataset.liveRenderer='leaflet-fallback';
      console.warn('MapLibre live renderer unavailable; retaining Leaflet markers.',error);
    }finally{liveRendererStarting=false;}
  }
  configureBaseMap();

  function railMapContextActive() {
    const selected=selectedId ? activityById(selectedId) : null;
    return selected?.type==='train'
      || placeMode==='rail'
      || selectedType==='train'
      || selectedType==='cool-rail'
      || enabledActivityTypes.has('train');
  }

  function syncBaseMap() {
    const wantsRail=railMapContextActive();
    const nextKey=wantsRail&&tileSets.rail?'rail':'standard';
    const next=tileSets[nextKey];
    const mapNode=document.getElementById('map');
    if(mapNode){
      mapNode.dataset.mapContext=wantsRail?'rail':'standard';
      mapNode.dataset.baseStyle=nextKey;
    }
    if(!next||currentBase===next)return;
    if(currentBase&&map.hasLayer(currentBase))map.removeLayer(currentBase);
    currentBase=next.addTo(map);
    currentBase.bringToBack?.();
  }

  map.createPane('trafficPane');
  map.getPane('trafficPane').style.zIndex = '360';
  map.getPane('trafficPane').style.pointerEvents = 'none';
  map.createPane('railLinePane');
  // The railway network sits above the base map and road overlays but below
  // a selected train's route and every marker.
  map.getPane('railLinePane').style.zIndex = '365';
  map.getPane('railLinePane').style.pointerEvents = 'none';
  map.createPane('trainRoutePane');
  // Keep the selected railway leg above road overlays, while train and place
  // markers remain selectable above the route.
  map.getPane('trainRoutePane').style.zIndex = '470';
  map.getPane('trainRoutePane').style.pointerEvents = 'none';
  map.createPane('journeyPane');
  map.getPane('journeyPane').style.zIndex = '660';
  map.getPane('journeyPane').style.pointerEvents = 'none';
  map.createPane('placePane');
  // A station is a reference point, not a cover over trains reporting there.
  map.getPane('placePane').style.zIndex = '580';
  let trafficLayer = null;
  let trafficIncidentLayer = null;
  let trafficMode = 'off';
  let trafficConfigured = false;
  let trafficTileErrorShown = false;

  // Aircraft silhouettes, top-down on a 24px grid pointing north. The same
  // shapes are drawn by the fast map renderer (live-object-renderer.js).
  const AIRCRAFT_SHAPES = {
    airliner:'<path class="ac-body" d="M12 1.5c.9 0 1.5 1 1.5 2.4V9l8 4.6v2.1l-8-2.4v5.2l2.4 1.8v1.6L12 21l-3.9.9v-1.6l2.4-1.8v-5.2l-8 2.4v-2.1l8-4.6V3.9c0-1.4.6-2.4 1.5-2.4z"/>',
    heavy:'<path class="ac-body" d="M12 1c1 0 1.6 1.1 1.6 2.6V8.6l9.4 5v2.2l-9.4-2.6v5.6l2.6 1.8v1.7L12 21.4l-4.2.9v-1.7l2.6-1.8v-5.6L1 15.8v-2.2l9.4-5V3.6C10.4 2.1 11 1 12 1z"/><path class="ac-body" d="M5.2 12.6h1.8v2.6H5.2zM8 11.1h1.8v2.6H8zM14.2 11.1H16v2.6h-1.8zM17 12.6h1.8v2.6H17z"/>',
    fastjet:'<path class="ac-body" d="M12 1l1.6 5 .4 5 8 6v2.2l-8-1.6-.6 2.6 3 2v1.6L12 22.2l-4.4.6v-1.6l3-2-.6-2.6-8 1.6V17l8-6 .4-5z"/>',
    propeller:'<path class="ac-body" d="M12 4c.8 0 1.3.8 1.3 2v3.6l8.7 1.2v2.2l-8.7.6-.4 5.6 2.6 1.4v1.6L12 21.6l-3.5.6v-1.6l2.6-1.4-.4-5.6-8.7-.6v-2.2l8.7-1.2V6c0-1.2.5-2 1.3-2z"/><path class="ac-rotor" d="M8.4 3h7.2"/>',
    helicopter:'<path class="ac-rotor" d="M3 2.5l18 15M21 2.5l-18 15"/><ellipse class="ac-body" cx="12" cy="10" rx="3.8" ry="5.6"/><path class="ac-body" d="M11.2 14.5h1.6v6h-1.6zM8.8 19.8h6.4v1.8H8.8z"/>',
    apache:'<path class="ac-rotor" d="M3 2.5l18 15M21 2.5l-18 15"/><ellipse class="ac-body" cx="12" cy="9.6" rx="2.8" ry="6"/><path class="ac-body" d="M6 10.4h12v2.4H6zM11.2 14.5h1.6v6h-1.6zM8.8 19.8h6.4v1.8H8.8z"/>',
    chinook:'<path class="ac-rotor" d="M6.5 1.5l11 8.6M17.5 1.5l-11 8.6M6.5 13.9l11 8.6M17.5 13.9l-11 8.6"/><rect class="ac-body" x="9.6" y="2.4" width="4.8" height="19.2" rx="2.4"/>',
  };

  // One line-icon set for the whole interface (24px grid, stroked).
  const ICON_PATHS = {
    train:'<rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 11h14M9 14h.01M15 14h.01M8 21l1.5-4M16 21l-1.5-4"/>',
    // A coach side on: sloped windscreen, a row of windows, two wheels.
    coach:'<path d="M3 17V7.5A1.5 1.5 0 0 1 4.5 6H17l4 4.5V17z"/><path d="M3 10.5h18M7.5 6v4.5M12 6v4.5M16.5 6v4.5"/><circle cx="7" cy="18" r="1.7"/><circle cx="17" cy="18" r="1.7"/>',
    // A container wagon side on: ribbed box on a frame with two bogies.
    freight:'<rect x="2.5" y="5" width="19" height="10" rx="1"/><path d="M7.25 5v10M12 5v10M16.75 5v10M2 17.5h20"/><circle cx="6.5" cy="19.5" r="1.6"/><circle cx="17.5" cy="19.5" r="1.6"/>',
    bus:'<rect x="4" y="3" width="16" height="14" rx="3"/><path d="M4 10h16M8 14h.01M16 14h.01M7 17v3M17 17v3"/>',
    aircraft:'<path d="M12 2.5c.8 0 1.3.9 1.3 2.1V9.2l7.2 4.2v1.9l-7.2-2.2v4.7l2.2 1.6v1.5L12 20.2l-3.5.7v-1.5l2.2-1.6v-4.7l-7.2 2.2v-1.9l7.2-4.2V4.6c0-1.2.5-2.1 1.3-2.1z"/>',
    sar:'<circle cx="12" cy="10" r="3.2"/><path d="M4.5 3.5l15 13M19.5 3.5l-15 13M12 13.2V21M9.5 21h5"/>',
    vessel:'<path d="M3 16l2 4h14l2-4zM6 16V9h12v7M12 9V4M9 6h6"/>',
    rescue:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M5.6 5.6l3.6 3.6M14.8 14.8l3.6 3.6M18.4 5.6l-3.6 3.6M9.2 14.8l-3.6 3.6"/>',
    incident:'<path d="M12 3l10 18H2z"/><path d="M12 10v4M12 17h.01"/>',
    road:'<path d="M8 20l3-15h2l3 15M5 20h14M9.4 14h5.2M10.4 9.5h3.2"/>',
    roadfeature:'<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
    flood:'<path d="M2 9c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 5 2M2 15c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 5 2"/>',
    weather:'<path d="M7 15a4 4 0 1 1 1-7.9A6 6 0 0 1 19 9a3.5 3.5 0 0 1-1 6.9z"/><path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
    earthquake:'<path d="M2 12h4l2-6 4 12 3-9 2 3h5"/>',
    grid:'<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    airquality:'<path d="M3 8h10a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h8"/>',
    bike:'<circle cx="6" cy="17" r="3.5"/><circle cx="18" cy="17" r="3.5"/><path d="M6 17l4-8h5l3 8M10 9 9 6H7M15 9l-3 8"/>',
    space:'<path d="M12 3l2.4 5.6L20 9.5l-4.2 3.9 1.2 6.1L12 16.6 7 19.5l1.2-6.1L4 9.5l5.6-.9z"/>',
    satellite:'<path d="M4 10l3-3 4 4-3 3zM13 13l3-3 4 4-3 3zM9 9l6 6M14 7a3 3 0 0 1 3 3M14 3a7 7 0 0 1 7 7"/>',
    outage:'<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5M3 3l18 18"/>',
    gritter:'<path d="M12 2v20M3.3 7l17.4 10M20.7 7 3.3 17"/>',
    camera:'<path d="M3 8h4l2-3h6l2 3h4v11H3z"/><circle cx="12" cy="13" r="3.5"/>',
    station:'<rect x="5" y="3" width="14" height="13" rx="2"/><path d="M5 10h14M8 20l2-4M16 20l-2-4M7 20h10"/>',
    busstop:'<path d="M8 3h8v8H8zM12 11v10M9 21h6"/>',
    pin:'<path d="M12 21s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/>',
    traffic:'<rect x="8" y="2" width="8" height="20" rx="3"/><path d="M12 7h.01M12 12h.01M12 17h.01"/>',
    route:'<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M6 15.5V10a4 4 0 0 1 4-4h5.5M18 8.5V14a4 4 0 0 1-4 4H8.5"/>',
    database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    signal:'<path d="M5 12h14M12 5v14"/><circle cx="12" cy="12" r="9"/>',
    car:'<path d="M5 16V11l2-5h10l2 5v5M3 16h18v3H3zM7 19v2M17 19v2"/><path d="M7.5 13.5h.01M16.5 13.5h.01"/>',
    walk:'<circle cx="13" cy="4" r="1.8"/><path d="M10 21l2-6-2-3 1-5 4 3 3 1M9 11l-2 4M12 15l3 6"/>',
    other:'<circle cx="12" cy="12" r="3"/>',
  };
  const CATEGORY_ICON = { rail:'train', air:'aircraft', sea:'vessel', rescue:'rescue', rare:'space', steam:'train', naval:'vessel' };
  function coolIconFor(a, info) {
    // Category (rail / air / sea / rescue) is the reliable signal; themes
    // such as "steam" or "naval" are presentation names.
    return CATEGORY_ICON[a?.cool_category] || CATEGORY_ICON[info?.theme] || ({train:'train',aircraft:'aircraft',sar:'sar',vessel:'vessel',rescue:'rescue'})[a?.type] || 'space';
  }
  ICON_PATHS.river = ICON_PATHS.flood; ICON_PATHS.tide = ICON_PATHS.flood; ICON_PATHS.wave = ICON_PATHS.flood;
  // Long-distance coaches come through the bus feed; these operators are
  // shown as coaches, with a link to book. Matches COACH_OPERATORS on the server.
  const COACH_OPERATORS={NATX:{name:'National Express',partner:'national-express'}};
  function coachInfo(a){return a?.type==='bus'?COACH_OPERATORS[String(a.operator||'').toUpperCase()]||null:null;}
  function coachBookingHtml(a){
    const coach=coachInfo(a);
    if(!coach)return '';
    const affiliate=Boolean(bookingAffiliates[coach.partner]);
    return `<p class="coach-book"><a class="primary-button" href="/go/${esc(coach.partner)}" target="_blank" rel="${affiliate?'sponsored ':''}nofollow noopener">Book on ${esc(coach.name)}</a><small>${affiliate?'Affiliate link: UK Activity Radar may earn a commission if you book.':`Tickets are sold by ${esc(coach.name)}.`}</small></p>`;
  }

  function iconSvg(name, extraClass='') {
    return `<svg class="ico${extraClass?` ${extraClass}`:''}" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATHS[name] || ICON_PATHS.other}</svg>`;
  }
  const icons = Object.fromEntries(Object.keys(ICON_PATHS).map(name => [name, iconSvg(name)]));
  const markers = new Map();
  let focusMarker = null;
  const markerGroup = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({ disableClusteringAtZoom: 10, maxClusterRadius: 48, showCoverageOnHover: false, spiderfyOnMaxZoom: true })
    : L.layerGroup();
  markerGroup.addTo(map);
  // At national zoom, thousands of individual aircraft obscure the map. Keep
  // them selectable from regional zoom while clustering the country-wide view.
  const aerialMarkerGroup = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({ disableClusteringAtZoom: 8, maxClusterRadius: 56, showCoverageOnHover: false,
        spiderfyOnMaxZoom: true, iconCreateFunction: cluster => L.divIcon({
          html:`<span>${cluster.getChildCount()}</span>`, className:'aerial-cluster', iconSize:[34,34]
        }) })
    : L.layerGroup();
  aerialMarkerGroup.addTo(map);
  const cameraMarkerGroup = L.layerGroup().addTo(map);
  const cameraMarkers = new Map();
  let cameras = [];
  let webcamsEnabled = false;
  let cameraRequest = 0;
  let cameraDetailRequest = 0;

  const placeIcons = {rail_station:iconSvg('station'),bus_stop:iconSvg('busstop'),rain_gauge:iconSvg('weather')};
  const placeMarkers = new Map();
  const placeGroup = typeof L.markerClusterGroup === 'function'
    // Station groups are small and white so they never read as trains.
    ? L.markerClusterGroup({ disableClusteringAtZoom: 14, maxClusterRadius: 40, showCoverageOnHover:false,
        iconCreateFunction: cluster => L.divIcon({ html:`<span>${cluster.getChildCount()}</span>`, className:'station-cluster', iconSize:[28,28] }) })
    : L.layerGroup();
  placeGroup.addTo(map);
  let places = [];
  let placeMode = 'off';
  let lastEnabledLayer = null;
  let lateTrainsOnly = false;
  let fetchedPlaceMode = 'off';
  let placeFetchSeq = 0;
  let selectedPlaceId = null;
  let returnPlaceId = null;
  const placeBoardCache = new Map();
  let placeBoardRefreshTimer = null;
  let placeBoardController = null;
  let placeBoardRequest = 0;
  let boardLinkRequest = 0;
  let overheadActive = false;
  let transientNearestBusPlaceId = null;
  let overheadAutoEnabledAircraft = false;
  let nearbyAircraftRows = [];
  let nearbyAircraftIds = new Set();
  let overheadInjectedAircraftIds = new Set();
  let overheadRefreshTimer = null;
  let overheadRefreshRequest = 0;
  const detailBackButton=document.createElement('button');
  detailBackButton.id='detail-back';detailBackButton.className='detail-back hidden';detailBackButton.type='button';
  detailBackButton.setAttribute('aria-label','Back to station board');detailBackButton.textContent='←';
  document.querySelector('#detail-panel .detail-head')?.prepend(detailBackButton);
  let placeLinkLine = null;
  let journeyRouteLayer = null;
  let journeyRouteOutline = null;
  let journeyIssueMarkers = [];
  let lastJourneyPayload = null;
  const movementHistory = new UKActivityMovementHistory({maxPoints:120,maxAgeMs:30*60*1000});
  let selectedTrailLayer = null;
  let selectedTrainRoute = null;
  let selectedTrainPublishedRoute = null;
  let detailRouteFitPending = false;
  let detailRouteAwaitPublishedFit = false;

  let activities = [];
  let activitiesById = new Map();
  const deferredTrainRemovals = new Map();
  let globalCoolActivities = [];
  let globalCoolAvailability = {fullAccess:false,total:0,counts:{air:0,rail:0,rescue:0}};
  let globalCoolById = new Map();
  let globalCoolRevision = 0;
  let globalCoolRequest = 0;
  let globalCoolLoadedAt = 0;
  let globalCoolRefreshTimer = null;
  let activityRevision = 0;
  let filteredCacheKey = '';
  let filteredCacheRows = [];
  let visibleActivityIds = new Set();
  const searchableCache = new WeakMap();
  const interestCache = new WeakMap();
  const coolCache = new WeakMap();
  let selectedId = null;
  let selectedActivitySnapshot = null;
  let selectedType = 'useful';
  let selectedRailSubtype = 'all';
  let selectedOperator = 'all';
  let aircraftLandingView = '';
  // Which aircraft to show, from the chips under the Aircraft toggle. Only
  // planes are filtered; trains, buses and the rest are untouched.
  let aircraftFilter = '';
  try { aircraftFilter = localStorage.getItem('uk-radar-aircraft-filter') || ''; } catch {}
  // The rest of the Filter drawer: trains, buses, roads. Remembered per device.
  let trainFilter = '', busFilter = '', roadFilter = '';
  try { ({train:trainFilter='',bus:busFilter='',road:roadFilter=''} = JSON.parse(localStorage.getItem('uk-radar-map-filters') || '{}')); } catch {}
  if (trainFilter === 'late') lateTrainsOnly = true;
  let operatorOptionsSignature = '';
  let trackedActivityId = null;
  const usefulActivityTypeOrder = ['train','bus','aircraft','road','roadfeature','incident','weather','flood'];
  const toggleableActivityTypes = new Set(usefulActivityTypeOrder);
  const densityActivityTypes = {
    train: ['train'],
    bus: ['bus'],
    road: ['road'],
    incident: ['incident'],
    weather: ['weather','flood'],
  };
  // Useful mode deliberately starts with a blank map on every launch. Layer
  // buttons are independent switches, so the map never silently restores a
  // wall of vehicles from an earlier session.
  const enabledActivityTypes = new Set();
  let currentWorkspace = 'useful';
  let searchText = '';
  let socket = null;
  let streamMeta = {};
  let latestSources = [];
  let viewportTimer = null;
  let coverageRequest = 0;
  let coverageLoadedFor = null;
  let intelligenceRequest = 0;
  let intelligenceLoadedFor = null;
  let intelligenceRefreshTimer = null;
  let intelligenceController = null;
  let mediaRequest = 0;
  let mediaLoadedFor = null;
  let searchMarker = null;
  let heatLayer = null;
  let heatMode = 'off';
  let heatSnapshotPending = false;
  let heatPointCount = 0;
  let nearbyCircle = null;
  let nearbyMarker = null;
  let nearbyState = { active: false, lat: null, lon: null, radiusMiles: 10, source: null };
  let watchZones = [];
  let watchCircles = [];
  let watchPrimed = false;
  const watchSeen = new Set();
  const followed = new Set();
  let savedPlaces = new Map();
  let myRadarInterests = new Set();
  let todayLoadedAt = 0;
  let todayLoadPromise = null;
  const MY_RADAR_PLACES_KEY = 'ukRadarSavedPlacesV2';
  const MY_RADAR_INTERESTS_KEY = 'ukRadarInterestsV2';
  const TODAY_LAST_VISIT_KEY = 'ukRadarTodayLastVisitV1';
  const ALERT_DEVICE_KEY = 'ukRadarAlertDeviceV1';
  let commuteAlertStatus={configured:false,subscribed:false,public_key:null,preferences:null};
  let billingState = {enabled:false,plus:false,features:[],price_display:''};
  const DISCOVERY_COLLECTION_KEY = 'ukRadarDiscoveryCollectionV1';
  const DISCOVERY_INTERESTS_KEY = 'ukRadarDiscoveryInterestsV1';
  const DISCOVERY_LAST_VISIT_KEY = 'ukRadarDiscoveryLastVisitV1';
  const discoverySessionStartedAt = Date.now();
  let discoveryPreviousVisit = 0;
  let discoveryVisitRecorded = false;
  let discoveryCatalogue = [];
  let discoveryMissions = [];
  let discoveryWeek = null;
  let discoveryCollection = new Map();
  let discoveryInterests = new Set();
  let discoveryView = 'now';
  let discoveryLoadPromise = null;
  let discoveryApiAvailable = true;
  let discoveryClaimPending = false;
  let discoveryNowRenderKey = '';
  let discoveryGuideRenderKey = '';
  let discoveryMissionRenderKey = '';
  const fallbackDiscoveryCatalogue = [
    ['display-aircraft','air','Display aircraft','Identified display or aerobatic aircraft.','▲'],
    ['bomber','air','Military bomber','Identified military bomber aircraft.','▲'],
    ['combat-aircraft','air','Fast jet','Identified high-performance combat aircraft.','▲'],
    ['airborne-surveillance','air','Airborne surveillance','Identified early-warning or surveillance aircraft.','▲'],
    ['military-tanker','air','Aerial tanker','Identified military refuelling aircraft.','▲'],
    ['maritime-patrol','air','Maritime patrol aircraft','Identified maritime-patrol aircraft.','▲'],
    ['intelligence-aircraft','air','Intelligence aircraft','Identified electronic-surveillance aircraft.','▲'],
    ['military-helicopter','air','Military helicopter','Identified operational military helicopter.','▲'],
    ['military-transport','air','Military airlifter','Identified military transport aircraft.','▲'],
    ['jet-trainer','air','Jet trainer','Identified fast-jet trainer, such as the RAF Hawk.','▲'],
    ['rare-airliner','air','Big or rare airliner','An A380, 747, Beluga, An-124 or another big or rare airliner.','▲'],
    ['steam','rail','Steam movement','A live steam-hauled railway movement.','♨'],
    ['railtour-special','rail','Railtour','A charter or special passenger movement.','◆'],
    ['test-measurement','rail','Measurement train','A railway test or measurement movement.','◇'],
    ['engineering','rail','Engineering train','A specialist railway engineering movement.','⚒'],
    ['freight','rail','Freight train','A live non-passenger freight movement.','▰'],
    ['locomotive-move','rail','Special locomotive move','A special locomotive movement.','◆'],
  ].map(([subtype,category,title,explanation,icon])=>({id:`${category}-${subtype}`,key:subtype,category,subtype,title,explanation,icon,badge:title.toUpperCase(),safety_note:null}));
  let deferredInstallPrompt = null;
  let pendingSocketPayloads = [];
  let socketRenderFrame = null;
  let secondaryRenderTimer = null;
  let secondaryRenderIdle = null;
  let lastSecondaryRenderAt = 0;
  let selectedDetailSignature = '';
  let reconnectTimer = null;
  const SECONDARY_RENDER_INTERVAL_MS = 1500;
  const diagnostics = window.__radarDiagnostics = window.__radarDiagnostics || {
    receivedSnapshots: 0,
    appliedSnapshots: 0,
    markersCreated: 0,
    markerMoves: 0,
    markerVisualUpdates: 0,
    markerTooltipUpdates: 0,
    lastRenderMs: 0,
    lastDeltaRenderMs: 0,
    fullMapRenders: 0,
    deltaMapRenders: 0,
    unchangedSnapshots: 0,
    startupExtrasAtMs: null,
  };
  const sessionId = (() => {
    try {
      let id = localStorage.getItem('ukRadarSessionId');
      if (!id) { id = (crypto.randomUUID ? crypto.randomUUID() : `session-${Date.now()}-${Math.random().toString(16).slice(2)}`); localStorage.setItem('ukRadarSessionId', id); }
      return id;
    } catch { return `session-${Date.now()}`; }
  })();

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const fmt = (n) => Number(n || 0).toLocaleString('en-GB');
  const safeUrl = (value) => {
    try {
      const u = new URL(String(value));
      return ['http:', 'https:'].includes(u.protocol) ? u.href : '#';
    } catch { return '#'; }
  };
  const timeText = (value) => {
    if (!value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d.toLocaleString('en-GB', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' });
  };
  const relativeAge = (seconds) => {
    if (seconds == null || !Number.isFinite(Number(seconds))) return 'no successful update yet';
    const s = Number(seconds);
    if (s < 10) return 'just now';
    if (s < 60) return `${Math.floor(s)}s ago`;
    if (s < 3600) return `${Math.floor(s/60)}m ago`;
    return `${Math.floor(s/3600)}h ago`;
  };
  const analyticsConsentKey='ukRadarAnalyticsConsentV1';
  const ga4MeasurementId=String(document.body?.dataset.ga4MeasurementId||'').trim().toUpperCase();
  const gtmContainerId=String(document.body?.dataset.gtmContainerId||'').trim().toUpperCase();
  const gtmConfigured=/^GTM-[A-Z0-9]{4,12}$/.test(gtmContainerId);
  const ga4Configured=/^G-[A-Z0-9]{6,20}$/.test(ga4MeasurementId)||gtmConfigured;
  let ga4Loaded=false;

  function loadGoogleAnalytics() {
    if(!ga4Configured||ga4Loaded)return;
    ga4Loaded=true;
    window.dataLayer=window.dataLayer||[];
    window.gtag=window.gtag||function(){window.dataLayer.push(arguments);};
    // Measurement only: never advertising, whatever the container holds.
    window.gtag('consent','default',{analytics_storage:'granted',ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});
    if(gtmConfigured){
      // Tag Manager holds the GA4 tag itself, so GA4 is not set up twice.
      window.dataLayer.push({'gtm.start':Date.now(),event:'gtm.js'});
      const script=document.createElement('script');
      script.async=true;
      script.src=`https://www.googletagmanager.com/gtm.js?id=${encodeURIComponent(gtmContainerId)}`;
      document.head.appendChild(script);
      return;
    }
    window.gtag('js',new Date());
    window.gtag('config',ga4MeasurementId,{
      anonymize_ip:true,
      allow_google_signals:false,
      allow_ad_personalization_signals:false,
    });
    const script=document.createElement('script');
    script.async=true;
    script.src=`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(ga4MeasurementId)}`;
    document.head.appendChild(script);
  }

  function analyticsChoice() {
    try{return localStorage.getItem(analyticsConsentKey)||'';}catch{return '';}
  }

  function setAnalyticsChoice(choice) {
    try{localStorage.setItem(analyticsConsentKey,choice);}catch{}
    $('analytics-consent')?.classList.add('hidden');
    if(choice==='granted')loadGoogleAnalytics();
    else if(typeof window.gtag==='function'){
      window.gtag('consent','update',{analytics_storage:'denied'});
      document.cookie.split(';').map(row=>row.trim().split('=')[0]).filter(name=>name.startsWith('_ga')).forEach(name=>{document.cookie=`${name}=; Max-Age=0; path=/; SameSite=Lax`;});
    }
  }

  function initAnalyticsConsent() {
    if(!ga4Configured)return;
    $('analytics-preferences')?.classList.remove('hidden');
    const choice=analyticsChoice();
    if(choice==='granted')loadGoogleAnalytics();
    else if(choice!=='denied')$('analytics-consent')?.classList.remove('hidden');
    $('analytics-accept')?.addEventListener('click',()=>setAnalyticsChoice('granted'));
    $('analytics-decline')?.addEventListener('click',()=>setAnalyticsChoice('denied'));
    $('analytics-settings')?.addEventListener('click',()=>$('analytics-consent')?.classList.remove('hidden'));
  }

  async function trackProduct(eventName, feature='') {
    if(analyticsChoice()==='granted'&&gtmConfigured&&Array.isArray(window.dataLayer)){
      // A Tag Manager "Custom Event" trigger picks these up by name.
      window.dataLayer.push({event:eventName,feature_label:String(feature||'').slice(0,64),event_category:'product'});
    }else if(analyticsChoice()==='granted'&&typeof window.gtag==='function'){
      window.gtag('event',eventName,{feature_label:String(feature||'').slice(0,64),event_category:'product'});
    }
    try {
      await fetch('/api/product/event', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,event_name:eventName,feature})});
    } catch {}
  }

  function hasPlusFeature(feature) {
    return Boolean(billingState.plus && billingState.features.includes(feature));
  }

  let billingRetry=null;
  async function loadBillingStatus() {
    try {
      const response=await fetch('/api/billing/status',{cache:'no-store'});
      if(!response.ok)throw new Error('access status unavailable');
      billingState=await response.json();
    } catch {
      // A failed check (the server busy for a moment) must not take Radar+
      // away or hide the buttons: keep what we knew, say so, try again.
      billingState={enabled:false,plus:false,features:[],price_display:'',...billingState,unavailable:true};
      if(!billingRetry)billingRetry=setTimeout(()=>{billingRetry=null;loadBillingStatus();},15000);
    }
    renderBillingStatus();
    return billingState;
  }

  function renderBillingStatus() {
    const plus=Boolean(billingState.plus);
    const price=$('plus-price'); if(price)price.textContent=billingState.price_display||'';
    const note=$('plus-note');
    if(note)note.textContent=billingState.unavailable?'Couldn’t check Radar+ just now. Trying again…':plus?'Radar+ is active on this device.':'The public live map stays free. Payment is handled securely by Stripe.';
    // Cancelling must work from any device, not only the one Radar+ is on.
    const login=$('radar-plus-portal-login');
    if(login){const url=String(billingState.portal_login_url||'');login.classList.toggle('hidden',plus||!url);if(url)login.href=url;}
    const summary=$('product-summary');
    if(summary)summary.textContent=plus?'Active on this device.':(billingState.enabled?`Not active. Radar+ is ${billingState.price_display||'available'}.`:'Not available yet.');
    $('radar-plus-checkout')?.classList.toggle('hidden',plus||!billingState.enabled);
    // Monthly and, when offered, yearly; a free trial is said up front.
    const yearlyOn=Boolean(billingState.yearly_available)&&!plus&&billingState.enabled;
    const yearly=$('radar-plus-yearly');
    if(yearly){yearly.classList.toggle('hidden',!yearlyOn);yearly.textContent=`${billingState.yearly_price_display||'Yearly'} · best value`;}
    if(yearlyOn&&price)price.textContent='';  // both prices are on the buttons
    const monthly=$('radar-plus-checkout');
    if(monthly){monthly.textContent=yearlyOn?(billingState.price_display||'Monthly'):'Get Radar+';monthly.classList.toggle('ghost-button',yearlyOn);monthly.classList.toggle('primary-button',!yearlyOn);}
    const trial=$('plus-trial');
    if(trial){const days=Number(billingState.trial_days)||0;trial.classList.toggle('hidden',plus||!billingState.enabled||!days);trial.textContent=days?`Free for ${days} days, then charged unless you cancel. Cancel any time.`:'';}
    $('radar-plus-portal')?.classList.toggle('hidden',!plus);
    $('radar-plus-logout')?.classList.toggle('hidden',!plus);
    $('radar-plus-preview')?.classList.toggle('hidden',plus||!billingState.preview_available);
    $('radar-plus-device')?.classList.toggle('hidden',!plus||!billingState.enabled);
    if(!plus)$('radar-plus-code-box')?.classList.add('hidden');
    $('radar-plus-have')?.classList.toggle('hidden',plus||!billingState.enabled);
    // Paid features say so up front, once Radar+ can be bought.
    document.querySelectorAll('[data-plus]').forEach(el=>el.classList.toggle('needs-plus',Boolean(billingState.enabled)&&!hasPlusFeature(el.dataset.plus)));
    const top=$('radar-plus'); if(top)top.textContent=plus?'Radar+ ✓':'Radar+';
  }

  // Alerts beyond one saved train are Radar+, but only once Radar+ can be
  // bought: while payments are off, everything stays open.
  function alertsLimited(){ return Boolean(billingState.enabled)&&!hasPlusFeature('alerts_pro'); }
  function requireAlertsPlus(what,label){
    if(!alertsLimited())return true;
    trackProduct('plus_limit_reached',label);
    openPlusDialog();showToast(`${what} are part of Radar+.`);return false;
  }
  function requirePlus(feature,message='This advanced tool is included with Radar+.') {
    if(hasPlusFeature(feature))return true;
    openPlusDialog(); showToast(message); return false;
  }

  async function postBilling(path) {
    const response=await fetch(path,{method:'POST',headers:{'Accept':'application/json'}});
    const payload=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:(payload.detail?.message||'Billing is unavailable right now.'));
    return payload;
  }

  const compass = (bearing) => {
    const n = Number(bearing);
    if (!Number.isFinite(n)) return null;
    return ['N','NE','E','SE','S','SW','W','NW'][Math.round(((n % 360) + 360) % 360 / 45) % 8];
  };
  const compactSpeed = (a) => a.speed == null ? null : `${Math.round(Number(a.speed))} ${a.speed_unit || ''}`.trim();

  function distanceKm(aLat, aLon, bLat, bLon) {
    const R = 6371;
    const toRad = x => x * Math.PI / 180;
    const dLat = toRad(bLat - aLat);
    const dLon = toRad(bLon - aLon);
    const q = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(aLat)) * Math.cos(toRad(bLat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.atan2(Math.sqrt(q), Math.sqrt(1 - q));
  }

  function ageMinutes(a) {
    const t = new Date(a.last_updated).getTime();
    return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 60000) : 999;
  }

  function interestInfo(a) {
    const cached = interestCache.get(a);
    if (cached) return cached;
    let score = 0;
    const reasons = [];
    const text = searchable(a);
    const tracking = Number(a.tracking_count || 0);
    if (tracking > 0) {
      score += Math.min(30, Math.log2(tracking + 1) * 4);
      if (tracking >= 100) reasons.push(`${fmt(tracking)} people tracking`);
    }
    if (a.interesting) score += 9;
    if (a.kind === 'event') score += 7;
    if (['sar','rescue'].includes(a.type)) { score += 20; reasons.push(a.type === 'rescue' ? 'live rescue activity' : 'SAR / HEMS activity'); }
    if (a.type === 'incident') { score += 10; reasons.push('active incident'); }
    if (a.type === 'road' && /closed|closure|collision|accident|blocked|severe|major/.test(text)) { score += 12; reasons.push('significant road disruption'); }
    if (a.type === 'weather' && /red|amber|severe/.test(text)) { score += 14; reasons.push('higher-impact weather warning'); }
    if (a.type === 'flood' && /severe|warning/.test(text)) { score += 11; reasons.push('active flood warning'); }
    if (a.type === 'earthquake') { const m=Number((String(a.status||'').match(/[0-9]+(?:\.[0-9]+)?/)||[])[0]||0); score += m>=3?20:10; reasons.push(`recent UK earthquake${m?` M${m}`:''}`); }
    if (a.type === 'wave' && /(?:[4-9]|\d{2,})\.?\d* m/.test(text)) { score += 13; reasons.push('large live wave reading'); }
    if (a.type === 'outage') { score += a.interesting ? 13 : 5; reasons.push('live power outage'); }
    if (a.type === 'space') { score += 16; reasons.push('aurora / space-weather trigger'); }
    if (a.type === 'aircraft') {
      const classified = coolInfo(a);
      if (classified.candidate && classified.score >= 45) {
        score += 10;
        reasons.push(...(classified.reasons.length ? classified.reasons : ['verified unusual aircraft']));
      }
    }
    if (a.type === 'gritter') { score += 10; reasons.push('live winter-service vehicle'); }
    if (a.type === 'satellite') { score += 10; reasons.push('ISS currently in map area'); }
    if (a.type === 'airquality' && /high|very high|index [7-9]|index 10/.test(text)) { score += 12; reasons.push('elevated air pollution'); }
    if (a.type === 'train' && /steam|special|charter|heritage|measurement|test train|royal/.test(text)) { score += 18; reasons.push('unusual rail movement'); }
    const delay = Math.abs(Number(a.delay_minutes || 0));
    if (delay >= 15) { score += Math.min(15, delay / 3); reasons.push(`${delay} minute rail delay`); }
    const age = ageMinutes(a);
    if (age <= 5) { score += 8; reasons.push('just updated'); }
    else if (age <= 15) score += 4;
    if (!reasons.length && a.kind !== 'event') reasons.push('live movement');
    const result = { score, reasons: [...new Set(reasons)].slice(0, 3) };
    interestCache.set(a, result);
    return result;
  }

  function coolInfo(a) {
    const cached = coolCache.get(a);
    if (cached) return cached;
    // v1.3 classification belongs to the backend.  This branch also honours a
    // score of zero, so routine or unresolved military traffic cannot fall
    // through to the old broad keyword rules and be promoted as a top live find.
    if (Object.prototype.hasOwnProperty.call(a, 'cool_score')) {
      const result = {
        score: Math.max(0, Math.min(100, Number(a.cool_score) || 0)),
        label: a.cool_badge || null,
        icon: a.cool_icon || '✦',
        theme: a.cool_theme || 'rare',
        subtype: a.cool_subtype || null,
        candidate: Boolean(a.cool_candidate),
        reasons: a.cool_explanation ? [String(a.cool_explanation)] : [],
      };
      coolCache.set(a, result);
      return result;
    }
    const text = searchable(a).toUpperCase();
    const head = String(a.headcode || '').toUpperCase().replace(/\s+/g,'');
    let score = 0, label = null, icon = '✦', theme = 'rare';
    const reasons = [];

    if (a.type === 'sar') {
      if (/COASTGUARD|BRITISHRESCUE|RESCUE|SR[DG]|G-MCG/.test(text)) { score=98; label='COASTGUARD'; icon='✚'; theme='rescue'; reasons.push('Coastguard / SAR aircraft'); }
      else if (/HELIMED|AIRAMB|AMBULANCE|HEMS/.test(text)) { score=91; label='HEMS'; icon='✚'; theme='rescue'; reasons.push('Air ambulance / HEMS'); }
      else { score=88; label='SAR AIR'; icon='✚'; theme='rescue'; reasons.push('Search & rescue aircraft'); }
    } else if (a.type === 'aircraft') {
      score=90; label='MIL / SPECIAL'; icon='▲'; theme='air'; reasons.push('Military / special-interest aircraft');
      if (/RED ARROW|REDARROW|HAWK|VAMP|DISPLAY/.test(text)) { score=100; label='DISPLAY'; reasons.unshift('Display / fast-jet activity'); }
      else if (/AWACS|E-?3|SENTRY|NATO/.test(text)) { score=98; label='AWACS'; reasons.unshift('Airborne surveillance aircraft'); }
      else if (/TANKER|VOYAGER|QID/.test(text)) { score=96; label='TANKER'; reasons.unshift('Military tanker'); }
      else if (/RRR|RFR|ASCOT|RCH|CNV|BAF|IAM|MMF/.test(text)) { score=94; label='MILITARY'; }
    } else if (a.type === 'rescue') {
      score=96; label='RNLI'; icon='◆'; theme='rescue'; reasons.push('Live lifeboat / rescue activity');
    } else if (a.type === 'train') {
      if (/STEAM|PACIFIC|TORNADO|FLYING SCOTSMAN|60103|60163|70000|46100|45596|34067/.test(text)) { score=100; label='STEAM'; icon='♨'; theme='steam'; reasons.push('Steam-hauled movement'); }
      else if (/RAILTOUR|RAIL TOUR|CHARTER|HERITAGE/.test(text)) { score=94; label='RAILTOUR'; icon='◆'; theme='rail'; reasons.push('Charter / special passenger movement'); }
      else if (/^1Z/.test(head)) { score=72; label='SPECIAL'; icon='◆'; theme='rail'; reasons.push('Special passenger working'); }
      else if (/MEASUREMENT|TEST TRAIN|NMT|NETWORK RAIL/.test(text) || /^3Q/.test(head)) { score=82; label='TEST TRAIN'; icon='◇'; theme='rail'; reasons.push('Test / measurement movement'); }
      else if (/ENGINEERING|TAMPER|GRINDER|BALLAST/.test(text)) { score=78; label='ENGINEERING'; icon='⚒'; theme='rail'; reasons.push('Engineering train'); }
      else if (/FREIGHT|CARGO|DB CARGO|FREIGHTLINER|GBRF|COLAS/.test(text) || /^[467]/.test(head)) { score=61; label='FREIGHT'; icon='▰'; theme='freight'; reasons.push('Freight / non-passenger movement'); }
      else if (/^0Z/.test(head)) { score=68; label='LOCO MOVE'; icon='◆'; theme='rail'; reasons.push('Special locomotive movement'); }
    } else if (a.type === 'vessel') {
      if (/\bHMS\b|\bRFA\b|WARSHIP|ROYAL NAVY|NAVAL|FRIGATE|DESTROYER/.test(text)) { score=100; label='NAVAL'; icon='⚓'; theme='naval'; reasons.push('Naval / military vessel'); }
      else if (/RNLI|LIFEBOAT/.test(text)) { score=97; label='LIFEBOAT'; icon='◆'; theme='rescue'; reasons.push('RNLI / lifeboat AIS target'); }
      else if (/COASTGUARD|RESCUE|PATROL/.test(text)) { score=90; label='PATROL'; icon='⚓'; theme='rescue'; reasons.push('Coastguard / patrol vessel'); }
      else if (/QUEEN MARY|CUNARD|CRUISE|PRINCESS|CELEBRITY|AIDA|MSC /.test(text)) { score=76; label='CRUISE'; icon='▤'; theme='sea'; reasons.push('Cruise / passenger ship'); }
      else if (/RESEARCH|SURVEY|OCEANOGRAPH|EXPLORER|CABLE|OFFSHORE/.test(text)) { score=72; label='SPECIALIST'; icon='⌁'; theme='sea'; reasons.push('Specialist / research vessel'); }
      else if (/TUG|TOW/.test(text)) { score=52; label='TUG'; icon='⚓'; theme='sea'; reasons.push('Tug / tow operation'); }
    }

    if (!score && a.interesting && ['train','vessel','aircraft','sar','rescue'].includes(a.type)) { score=48; label='NOTABLE'; reasons.push('Flagged as unusual by its live source'); }
    if (score) {
      const age=ageMinutes(a); if(age<=5){score+=4;reasons.push('live now');} else if(age>30) score-=8;
    }
    const result = {score:Math.max(0,Math.min(104,score)),label,icon,theme,subtype:null,candidate:score>=45,reasons:[...new Set(reasons)].slice(0,3)};
    coolCache.set(a, result);
    return result;
  }

  function coolBadgeHtml(a, compact=false) {
    const c=coolInfo(a); if(!c.label || c.score<45) return '';
    return `<span class="cool-badge cool-${esc(c.theme)}${compact?' compact':''}"><b>${iconSvg(coolIconFor(a,c))}</b><em>${esc(c.label)}</em></span>`;
  }

  function railSubtypeMatch(a, subtype=selectedRailSubtype) {
    if (a.type !== 'train') return false;
    if (subtype === 'all') return true;
    const info = coolInfo(a);
    const label = info.label || '';
    const classified = info.subtype || '';
    const text=searchable(a).toUpperCase();
    const head=String(a.headcode || '').trim().toUpperCase();
    if (subtype === 'special') return ['steam','railtour-special','special-passenger','locomotive-move'].includes(classified) || ['STEAM','RAILTOUR','SPECIAL','LOCO MOVE'].includes(label) || /^(?:0Z|1Z)/.test(head);
    if (subtype === 'test') return ['test-measurement','engineering'].includes(classified) || ['TEST TRAIN','ENGINEERING'].includes(label) || /^[13]Q/.test(head);
    if (subtype === 'freight') return classified === 'freight' || label === 'FREIGHT' || /^[467]/.test(head) || /\b(FREIGHT|GBRF|DB CARGO|FREIGHTLINER|COLAS RAIL)\b/.test(text);
    return true;
  }

  // Railway network layer: drawn whenever trains are on.
  const railLines = {
    group: null, casingRenderer: null, lineRenderer: null, casings: null, drawnLevel: null, drawn: new Set(), cells: new Map(), national: null, seq: 0, wanted: false,
  };

  function railLineLevel(zoom) { return zoom < 9 ? 0 : zoom < 12 ? 1 : 2; }

  function railLineStyle(level) {
    return level === 0
      ? [{color:'#ffffff', weight:4, opacity:.9}, {color:'#1f3a52', weight:2, opacity:.85}]
      : level === 1
        ? [{color:'#ffffff', weight:6, opacity:.95}, {color:'#1f3a52', weight:3, opacity:.9}]
        : [{color:'#ffffff', weight:8, opacity:.95}, {color:'#1f3a52', weight:4, opacity:.95}];
  }

  function clearRailLines() {
    railLines.group?.clearLayers?.();
    railLines.casings?.clearLayers?.();
    railLines.drawn.clear();
    railLines.drawnLevel = null;
    $('map')?.setAttribute('data-rail-lines', '0');
  }

  function drawRailLines(level, lines) {
    if (!railLines.group) {
      // Two canvases: every white outline below every line, so junctions
      // join cleanly instead of being cut by a later line's outline.
      const canvas = () => typeof L.canvas === 'function' ? L.canvas({ pane:'railLinePane', padding:.3 }) : undefined;
      railLines.casingRenderer = canvas();
      railLines.lineRenderer = canvas();
      railLines.casings = L.layerGroup().addTo(map);
      railLines.group = L.layerGroup().addTo(map);
    }
    if (railLines.drawnLevel !== level) clearRailLines();
    railLines.drawnLevel = level;
    const [casing, line] = railLineStyle(level);
    for (const row of lines || []) {
      if (!row || railLines.drawn.has(row.id) || !Array.isArray(row.points) || row.points.length < 2) continue;
      railLines.drawn.add(row.id);
      const options = { pane:'railLinePane', interactive:false, lineCap:'round', lineJoin:'round' };
      railLines.casings.addLayer(L.polyline(row.points, { ...options, ...casing, renderer:railLines.casingRenderer }));
      railLines.group.addLayer(L.polyline(row.points, { ...options, ...line, renderer:railLines.lineRenderer }));
    }
    $('map')?.setAttribute('data-rail-lines', String(railLines.drawn.size));
  }

  async function fetchRailLineJson(url) {
    const response = await fetch(url, { cache:'force-cache' });
    if (!response.ok) throw new Error('rail lines unavailable');
    return response.json();
  }

  function railCellKeys(b) {
    const cell = 0.5, keys = [];
    for (let cx = Math.floor(b.getWest()/cell); cx <= Math.floor(b.getEast()/cell); cx += 1) {
      for (let cy = Math.floor(b.getSouth()/cell); cy <= Math.floor(b.getNorth()/cell); cy += 1) keys.push([cx, cy]);
    }
    return keys;
  }

  function railCell(level, cx, cy) {
    const key = `${level}:${cx}:${cy}`;
    if (!railLines.cells.has(key)) railLines.cells.set(key, fetchRailLineJson(`/api/rail/lines/${level}/${cx}/${cy}`).catch(() => { railLines.cells.delete(key); return { lines:[] }; }));
    return railLines.cells.get(key);
  }

  // Start downloading the railway lines for where the map is about to go,
  // so they are ready when a pan or zoom animation ends rather than only
  // being requested then.
  function prefetchRailLinesAt(lat, lon, zoom=map.getZoom()) {
    if (!enabledActivityTypes.has('train') || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))) return;
    const level = railLineLevel(zoom);
    if (level === 0) return;
    try {
      const size = map.getSize().divideBy(2);
      const centre = map.project(L.latLng(Number(lat), Number(lon)), zoom);
      const bounds = L.latLngBounds(map.unproject(centre.subtract(size), zoom), map.unproject(centre.add(size), zoom));
      const keys = railCellKeys(bounds);
      if (keys.length <= 64) keys.forEach(([cx, cy]) => railCell(level, cx, cy));
    } catch {}
  }

  async function updateRailLines() {
    const seq = ++railLines.seq;
    if (!enabledActivityTypes.has('train')) { clearRailLines(); return; }
    const level = railLineLevel(map.getZoom());
    try {
      if (level === 0) {
        railLines.national = railLines.national || await fetchRailLineJson('/api/rail/lines/national');
        if (seq === railLines.seq) drawRailLines(0, railLines.national.lines);
        return;
      }
      const keys = railCellKeys(map.getBounds());
      if (keys.length > 64) return;
      // Fetch everything in parallel, but paint the cell under the map centre
      // first. A slow/empty cell at the viewport edge must not leave a phone
      // showing stations on a blank map while Promise.all waits for it.
      const centre = map.getCenter();
      const centreCx = Math.floor(centre.lng / 0.5), centreCy = Math.floor(centre.lat / 0.5);
      keys.sort((a,b)=>(Math.abs(a[0]-centreCx)+Math.abs(a[1]-centreCy))-(Math.abs(b[0]-centreCx)+Math.abs(b[1]-centreCy)));
      const requests = keys.map(([cx, cy]) => railCell(level, cx, cy));
      if (requests.length) {
        const central = await requests[0];
        if (seq !== railLines.seq) return;
        drawRailLines(level, central?.lines || []);
      }
      await Promise.all(requests.slice(1).map(async request => {
        const result = await request;
        if (seq === railLines.seq) drawRailLines(level, result?.lines || []);
      }));
    } catch {}
  }

  // ---- Line view and delay hotspots ----
  // Tap a railway line to list the trains on that stretch, in order, like a
  // signal box diagram. "Delays" shows the stretches where trains run late.
  const railView = { highlight:null, delaysOn:false, delaysTimer:null, lookup:0, hotspots:[] };

  function clearRailHighlight() {
    if (railView.highlight) { map.removeLayer(railView.highlight); railView.highlight=null; }
    $('map')?.removeAttribute('data-rail-line-view');
  }

  function drawRailHighlight(stretches) {
    clearRailHighlight();
    const group=L.layerGroup();
    for (const row of stretches) {
      if (!Array.isArray(row.points) || row.points.length<2) continue;
      group.addLayer(L.polyline(row.points,{pane:'railLinePane',color:row.color||'#0b63ce',weight:row.weight||7,opacity:.9,lineCap:'round',lineJoin:'round',interactive:false}));
    }
    railView.highlight=group.addTo(map);
  }

  function stretchTitle(row) {
    const start=humanTransitLabel(row?.from||''), end=humanTransitLabel(row?.to||'');
    return start&&end ? `${start} – ${end}` : start||end ? `Near ${start||end}` : 'Railway line';
  }

  function openInfoPanel(title, subtitle, html) {
    closeDetailSelection();
    $('detail-panel')?.classList.add('has-selection'); $('detail-sidebar')?.classList.remove('is-empty');
    $('detail-title').textContent=title;
    $('detail-subtitle').textContent=subtitle;
    $('detail-fields').innerHTML='';
    // Nothing from a previous station board, photo or journey may linger
    // under a list (it showed "Next published buses" under Trains near you).
    clearPlaceBoard?.();
    for(const id of ['detail-placeboard','detail-photo','detail-intelligence','detail-related','detail-operator','detail-discovery','detail-coverage']){const el=$(id);if(el){el.classList.add('hidden');el.innerHTML='';}}
    const box=$('detail-insight'); box.classList.remove('hidden'); box.innerHTML=html;
    $('follow')?.classList.add('hidden'); $('share-activity')?.classList.add('hidden'); $('track-activity')?.classList.add('hidden'); $('save-place')?.classList.add('hidden');
    return box;
  }

  function lineTrainRow(row) {
    const status=trainStatusLabel({delay_minutes:row.delay_minutes,cancelled_at:row.cancelled?1:null});
    const tone=row.cancelled ? 'is-bad' : punctualityClass(row.delay_minutes);
    const away=Number.isFinite(Number(row.distance_km))?(Number(row.distance_km)<1?`${Math.round(Number(row.distance_km)*1000)} m away`:`${Number(row.distance_km).toFixed(1)} km away`):null;
    const where=[row.destination?`to ${humanTransitLabel(row.destination)}`:null,away||humanTransitLabel(row.operator||'')||null].filter(Boolean).join(' · ');
    return `<button type="button" class="line-train" data-line-train="${esc(row.id)}"><i class="line-dot ${esc(tone)}" aria-hidden="true"></i><span><strong>${esc(row.name)}</strong>${where?`<small>${esc(where)}</small>`:''}</span><b class="${esc(tone)}">${esc(status==='Live'?'':status)}</b></button>`;
  }

  function showRailLine(result) {
    const trains=result.trains||[];
    const ends=result.from&&result.to?`<p class="line-ends"><span>${esc(humanTransitLabel(result.from))}</span><i aria-hidden="true"></i><span>${esc(humanTransitLabel(result.to))}</span></p>`:'';
    const list=trains.length?`<div class="line-trains">${trains.map(lineTrainRow).join('')}</div>`:'<p class="transport-empty">No trains on this stretch right now.</p>';
    const box=openInfoPanel(stretchTitle(result),`${trains.length} train${trains.length===1?'':'s'} on this line${result.length_km?` · ${Math.round(result.length_km)} km`:''}`,`<div class="line-view">${ends}${list}</div>`);
    box.querySelectorAll('[data-line-train]').forEach(button=>button.addEventListener('click',()=>{ if(railView.delaysOn)$('map')?.removeAttribute('data-rail-line-view'); else clearRailHighlight(); selectActivity(button.dataset.lineTrain,true); }));
    drawRailHighlight([{points:result.points}]);
    $('map')?.setAttribute('data-rail-line-view', String(trains.length));
  }

  async function lookupRailLine(latlng) {
    const seq=++railView.lookup;
    try {
      const response=await fetch(`/api/rail/line?lat=${encodeURIComponent(latlng.lat)}&lon=${encodeURIComponent(latlng.lng)}&zoom=${encodeURIComponent(map.getZoom())}`,{cache:'no-store'});
      if(!response.ok||seq!==railView.lookup)return false;
      const result=await response.json();
      if(!result?.found||seq!==railView.lookup)return false;
      showRailLine(result);
      return true;
    } catch { return false; }
  }

  async function refreshDelayHotspots(openPanel=false) {
    if(!railView.delaysOn)return;
    try {
      const response=await fetch('/api/rail/delays',{cache:'no-store'});
      if(!response.ok||!railView.delaysOn)return;
      railView.hotspots=(await response.json()).hotspots||[];
    } catch { return; }
    const worst=Math.max(1,...railView.hotspots.map(row=>row.average_delay||0));
    drawRailHighlight(railView.hotspots.map(row=>({points:row.points,color:(row.average_delay||0)>=15||row.cancelled?'#d64545':'#e8930c',weight:6+Math.round(6*(row.average_delay||0)/worst)})));
    $('map')?.setAttribute('data-delay-hotspots', String(railView.hotspots.length));
    if(!openPanel)return;
    const rows=railView.hotspots.map((row,index)=>{
      const facts=[row.late?`${row.late} late, about ${row.average_delay} min`:null,row.cancelled?`${row.cancelled} cancelled`:null].filter(Boolean).join(' · ');
      return `<button type="button" class="line-train delay-spot" data-delay-spot="${index}"><i class="line-dot ${(row.average_delay||0)>=15||row.cancelled?'is-very-late':'is-late'}" aria-hidden="true"></i><span><strong>${esc(stretchTitle(row))}</strong><small>${esc(facts)}</small></span><b>›</b></button>`;
    }).join('');
    const box=openInfoPanel('Delays now',railView.hotspots.length?'Where trains are running late':'Trains are running well',rows?`<div class="line-trains">${rows}</div>`:'<p class="transport-empty">No stretch of line has late trains right now.</p>');
    drawRailHighlight(railView.hotspots.map(row=>({points:row.points,color:(row.average_delay||0)>=15||row.cancelled?'#d64545':'#e8930c',weight:6+Math.round(6*(row.average_delay||0)/worst)})));
    box.querySelectorAll('[data-delay-spot]').forEach(button=>button.addEventListener('click',()=>{
      const row=railView.hotspots[Number(button.dataset.delaySpot)];
      if(!row)return;
      map.fitBounds(L.latLngBounds(row.points),{padding:[40,40],maxZoom:12});
      lookupRailLine({lat:row.center[0],lng:row.center[1]});
    }));
  }

  function setDelayHotspots(on) {
    railView.delaysOn=Boolean(on)&&enabledActivityTypes.has('train');
    document.querySelectorAll('[data-rail-delays]').forEach(chip=>chip.setAttribute('aria-pressed',String(railView.delaysOn)));
    clearInterval(railView.delaysTimer); railView.delaysTimer=null;
    if(railView.delaysOn){
      refreshDelayHotspots(true);
      railView.delaysTimer=setInterval(()=>refreshDelayHotspots(false),60000);
    } else {
      clearRailHighlight();
      $('map')?.removeAttribute('data-delay-hotspots');
    }
  }

  // ---- Quick answers: the four everyday jobs, one tap each ----
  // Planes above me, trains near me, my next bus, my usual journey. Each
  // asks for location once (reused for two minutes) and answers in the panel.
  let lastFix=null, pendingPlanesList=false;
  function currentFix() {
    if(lastFix&&Date.now()-lastFix.at<120000)return Promise.resolve(lastFix);
    if(!navigator.geolocation)return Promise.reject(new Error('Location isn’t available. Search for a place instead.'));
    return new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(pos=>{
      lastFix={lat:pos.coords.latitude,lon:pos.coords.longitude,at:Date.now()};
      resolve(lastFix);
    },()=>reject(new Error('Turn on location to see what is near you.')),{enableHighAccuracy:false,timeout:8000,maximumAge:120000}));
  }
  function withLocation(done) {
    currentFix().then(fix=>done(fix.lat,fix.lon)).catch(error=>showToast(error.message));
  }

  function awayText(km) {
    const n=Number(km);
    return !Number.isFinite(n)?'':n<1?`${Math.round(n*1000)} m away`:`${n<10?n.toFixed(1):Math.round(n)} km away`;
  }

  function showPlanesList() {
    const miles=Number(nearbyState.radiusMiles)||25;
    const all=[...nearbyAircraftRows].map(a=>({a,km:distanceKm(nearbyState.lat,nearbyState.lon,Number(a.lat),Number(a.lon))}))
      .filter(x=>Number.isFinite(x.km)).sort((x,y)=>x.km-y.km);
    const within=all.filter(x=>x.km<=miles*1.609344);
    // Nothing that close: say so, and show the nearest few instead.
    const rows=(within.length?within:all.slice(0,5)).slice(0,12);
    const list=rows.map(({a,km})=>{
      const height=a.altitude_ft!=null?`${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} ft`:null;
      const facts=[a.aircraft_model||a.aircraft_type_code||null,height,awayText(km)].filter(Boolean).join(' · ');
      return `<button type="button" class="line-train" data-job-plane="${esc(a.id)}"><i class="line-dot" aria-hidden="true"></i><span><strong>${esc(a.name)}</strong><small data-plane-route>${esc(facts)}</small></span><b>›</b></button>`;
    }).join('');
    const box=openInfoPanel(within.length?'Planes above you':'Nearest planes',within.length?`${within.length} within ${miles} miles`:(rows.length?`None within ${miles} miles, so these are the closest`:`None within ${miles} miles right now`),list?`<div class="line-trains">${list}</div>`:'<p class="transport-empty">No aircraft are being tracked near you right now.</p>');
    box.querySelectorAll('[data-job-plane]').forEach(button=>button.addEventListener('click',()=>focusActivityById(button.dataset.jobPlane,map.getZoom())));
    // Add where each flight is going, for the closest few.
    rows.slice(0,6).forEach(({a})=>fetch(`/api/activity/${encodeURIComponent(a.id)}/flight-route?callsign=${encodeURIComponent(a.name||'')}&lat=${encodeURIComponent(a.lat)}&lon=${encodeURIComponent(a.lon)}`,{cache:'no-store'}).then(r=>r.ok?r.json():null).then(payload=>{
      if(!payload?.found)return;
      flightRouteById.set(a.id,payload.route);
      const small=box.querySelector(`[data-job-plane="${CSS.escape(a.id)}"] [data-plane-route]`);
      if(small)small.textContent=`${flightRouteText(payload.route)} · ${small.textContent}`;
    }).catch(()=>{}));
  }

  // What's that overhead: point the top of the phone at an aircraft and the
  // one in that direction is named. Uses the phone's compass and tilt with
  // the "planes above me" list.
  const skyPointer={active:false,heading:null,elevation:null,fix:null,lastDraw:0};
  const COMPASS_POINTS=['N','NE','E','SE','S','SW','W','NW'];
  const compassPoint=degrees=>COMPASS_POINTS[Math.round((((degrees%360)+360)%360)/45)%8];
  function bearingBetween(lat1,lon1,lat2,lon2){
    const toRad=x=>x*Math.PI/180,y=Math.sin(toRad(lon2-lon1))*Math.cos(toRad(lat2)),x=Math.cos(toRad(lat1))*Math.sin(toRad(lat2))-Math.sin(toRad(lat1))*Math.cos(toRad(lat2))*Math.cos(toRad(lon2-lon1));
    return (Math.atan2(y,x)*180/Math.PI+360)%360;
  }
  // Where each aircraft is in the sky from here, and how far off the pointing direction.
  function skyCandidates() {
    const {fix,heading,elevation}=skyPointer;
    if(!fix)return [];
    return nearbyAircraftRows.map(a=>{
      const km=distanceKm(fix.lat,fix.lon,Number(a.lat),Number(a.lon));
      if(!Number.isFinite(km)||a.altitude_ft==null)return null;
      const up=Math.atan2(Number(a.altitude_ft)*0.3048,Math.max(1,km*1000))*180/Math.PI;
      const bearing=bearingBetween(fix.lat,fix.lon,Number(a.lat),Number(a.lon));
      let off=null;
      if(heading!=null&&elevation!=null){
        const turn=Math.abs(((bearing-heading+540)%360)-180)*Math.cos(Math.max(up,elevation)*Math.PI/180);
        off=Math.hypot(turn,up-elevation);
      }
      return {a,km,up,bearing,off};
    }).filter(Boolean);
  }
  function renderSkyPointer() {
    if(!skyPointer.active)return;
    const box=$('detail-insight');if(!box||$('detail-title').textContent!=='Point at the sky'){stopSkyPointer();return;}
    const rows=skyCandidates();
    const hasSensor=skyPointer.heading!=null&&skyPointer.elevation!=null;
    const pointing=hasSensor?`Pointing ${compassPoint(skyPointer.heading)}, ${Math.max(0,Math.round(skyPointer.elevation))}° up`:'Waiting for your phone’s compass…';
    const best=hasSensor?rows.filter(row=>row.off<=20).sort((x,y)=>x.off-y.off)[0]:null;
    const card=row=>{
      const a=row.a,height=`${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} ft`;
      return `<button type="button" class="line-train sky-match" data-sky-plane="${esc(a.id)}"><i class="line-dot is-on-time" aria-hidden="true"></i><span><strong>${esc(a.name||'Aircraft')}</strong><small>${esc([a.aircraft_model||a.aircraft_type_code,height,awayText(row.km)].filter(Boolean).join(' · '))}</small></span><b>›</b></button>`;
    };
    let body;
    if(!rows.length)body='<p class="transport-empty">No aircraft are being tracked near you right now.</p>';
    else if(best)body=`<p class="sky-found">That’s this one:</p>${card(best)}`;
    else {
      const nearest=[...rows].sort((x,y)=>(hasSensor?x.off-y.off:x.km-y.km))[0];
      body=`<p class="transport-empty">${hasSensor?'Nothing in that direction.':'Hold your phone up and point its top edge at an aircraft.'} The closest to where you’re pointing is to the ${compassPoint(nearest.bearing)}, ${Math.round(nearest.up)}° up:</p>${card(nearest)}`;
    }
    box.innerHTML=`<div class="sky-pointer"><p class="sky-direction" data-sky-direction>${esc(pointing)}</p>${body}</div>`;
    $('detail-subtitle').textContent=`${rows.length} aircraft near you`;
    box.querySelectorAll('[data-sky-plane]').forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.skyPlane;stopSkyPointer();focusActivityById(id,map.getZoom());}));
  }
  function onSkyOrientation(event) {
    if(!skyPointer.active)return;
    // iPhone gives a true compass heading; elsewhere alpha counts the other way round.
    const heading=Number.isFinite(event.webkitCompassHeading)?event.webkitCompassHeading:(event.absolute&&Number.isFinite(event.alpha)?(360-event.alpha)%360:null);
    if(heading==null||!Number.isFinite(event.beta))return;
    skyPointer.heading=heading;skyPointer.elevation=Math.max(-10,Math.min(90,event.beta));
    // Redraw a few times a second at most, and never drop the last reading.
    const wait=400-(Date.now()-skyPointer.lastDraw);
    clearTimeout(skyPointer.timer);
    if(wait<=0){skyPointer.lastDraw=Date.now();renderSkyPointer();}
    else skyPointer.timer=setTimeout(()=>{skyPointer.lastDraw=Date.now();renderSkyPointer();},wait);
  }
  function stopSkyPointer() {
    skyPointer.active=false;skyPointer.heading=null;skyPointer.elevation=null;
    window.removeEventListener('deviceorientation',onSkyOrientation);
    window.removeEventListener('deviceorientationabsolute',onSkyOrientation);
  }
  async function startSkyPointer() {
    if(!('DeviceOrientationEvent' in window)){showToast('This needs a phone with a compass.');return;}
    // iPhone asks for permission, and only from a tap.
    try {
      if(typeof DeviceOrientationEvent.requestPermission==='function'&&await DeviceOrientationEvent.requestPermission()!=='granted'){showToast('Allow motion and orientation access to point at aircraft.');return;}
    } catch { showToast('Allow motion and orientation access to point at aircraft.');return; }
    let fix;
    try { fix=await currentFix(); } catch(error){ showToast(error.message); return; }
    stopSkyPointer();
    Object.assign(skyPointer,{active:true,fix,lastDraw:0});
    openInfoPanel('Point at the sky','Finding aircraft near you…','<div class="sky-pointer"><p class="sky-direction">Waiting for your phone’s compass…</p></div>');
    window.addEventListener('deviceorientation',onSkyOrientation);
    window.addEventListener('deviceorientationabsolute',onSkyOrientation);
    if(overheadActive&&nearbyAircraftRows.length)renderSkyPointer(); else $('overhead-now')?.click();
    trackProduct('sky_pointer_opened','aircraft');
  }

  // Camera view: the phone held upright like a camera, with a label over
  // each aircraft, train and bus in that direction. Nothing is recognised
  // from the picture; labels are placed from live positions, the phone's
  // location, its compass and its tilt.
  const cameraView={active:false,fix:null,heading:null,elevation:null,stream:null,ground:[],frame:0,refresh:null,kinds:new Set()};
  const AR_VERTICAL_FOV=62;
  function cameraObjects() {
    const fix=cameraView.fix;if(!fix)return [];
    const sky=nearbyAircraftRows.filter(a=>a.altitude_ft!=null).map(a=>{
      const km=distanceKm(fix.lat,fix.lon,Number(a.lat),Number(a.lon));
      const rate=Number(a.vertical_rate_fpm);
      const movement=Number.isFinite(rate)&&rate>150?'Climbing':Number.isFinite(rate)&&rate<-150?'Descending':'Level';
      return {id:a.id,type:'aircraft',title:a.name||'Aircraft',journey:a.aircraft_model||a.aircraft_type_code||'Aircraft',facts:[`${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} ft`,movement],distance:awayText(km),
        km,bearing:bearingBetween(fix.lat,fix.lon,Number(a.lat),Number(a.lon)),up:Math.atan2(Number(a.altitude_ft)*0.3048,Math.max(1,km*1000))*180/Math.PI};
    }).filter(row=>Number.isFinite(row.km)&&row.km<=60);
    // Trains keep moving between refreshes: carry each one along its
    // estimated track direction for up to 20 seconds rather than freezing it.
    const elapsed=Math.min(20,Math.max(0,(Date.now()-(cameraView.groundAt||Date.now()))/1000));
    const kmPerLat=111.32,kmPerLon=111.32*Math.cos(fix.lat*Math.PI/180);
    const ground=cameraView.ground.map(v=>{
      const vLat=Number(v.v_lat)||0,vLon=Number(v.v_lon)||0;
      const lat=Number(v.lat)+vLat*elapsed,lon=Number(v.lon)+vLon*elapsed;
      const km=distanceKm(fix.lat,fix.lon,lat,lon);
      const late=v.type==='train'&&Number(v.delay_minutes)>=1?`${Math.round(Number(v.delay_minutes))} min late`:null;
      const onTime=v.type==='train'&&v.delay_minutes!=null&&!late?'On time':null;
      // Closest approach: where its straight-line path passes nearest to you.
      const px=(lon-fix.lon)*kmPerLon,py=(lat-fix.lat)*kmPerLat,vx=vLon*kmPerLon,vy=vLat*kmPerLat,speed=Math.hypot(vx,vy);
      let pass=null,approaching=false,passIn=null;
      if(v.type==='train'&&speed>0.002){
        const t=-(px*vx+py*vy)/(speed*speed);
        if(t>5&&t<900){
          approaching=true;
          const closest=Math.hypot(px+vx*t,py+vy*t);
          pass=`passes ${closest<0.15?'right by you':`${awayText(closest).replace(' away','')} away`} in ${t<60?'under a minute':`~${Math.round(t/60)} min`}`;
          passIn=t;
        } else pass='moving away';
      }
      const freight=v.type==='train'&&Boolean(v.freight);
      const where=v.destination?humanTransitLabel(v.destination):'';
      const code=v.headcode||v.name;
      return {id:v.id,type:v.type,icon:freight?'freight':v.type,
        title:v.type==='train'?(where?`${freight?'Freight to':'To'} ${where}`:(freight?'Freight train':code)):v.name,
        journey:v.type==='train'?[code,humanTransitLabel(v.operator||'')].filter(Boolean).join(' · '):(where?`to ${where}`:'Live vehicle'),
        facts:[pass&&approaching?pass:null,late||onTime,pass&&!approaching?pass:null].filter(Boolean),distance:awayText(km),approaching,passIn,
        km,bearing:bearingBetween(fix.lat,fix.lon,lat,lon),up:0};
    }).filter(row=>Number.isFinite(row.km)&&row.km>=0.03);
    // Only what was asked for: opened from Trains, it shows trains.
    return [...sky,...ground].filter(row=>cameraView.kinds.has(row.type));
  }

  function arrangeCameraLabels(rows,width,height) {
    const placed=[];
    // Keep every label wholly on screen: half its width in from each side.
    const half=Math.min(270,width-28)/2+6;
    const minX=Math.min(width/2,half),maxX=Math.max(width/2,width-half);
    const minY=104,maxY=Math.max(minY,height-178);
    for(const object of rows){
      const x=Math.max(minX,Math.min(maxX,object.x));
      const baseY=Math.max(minY,Math.min(maxY,object.y));
      const candidates=[baseY];
      for(let step=1;step<=5;step++){candidates.push(baseY+step*76,baseY-step*76);}
      const y=candidates.map(value=>Math.max(minY,Math.min(maxY,value))).find(value=>!placed.some(prior=>Math.abs(prior.x-x)<262&&Math.abs(prior.y-value)<72)) ?? baseY;
      const arranged={...object,x,y};placed.push(arranged);
    }
    return placed;
  }

  function drawCameraView() {
    cameraView.frame=0;
    if(!cameraView.active)return;
    const layer=$('ar-labels'),direction=$('ar-direction');if(!layer)return;
    const width=layer.clientWidth||window.innerWidth,height=layer.clientHeight||window.innerHeight;
    const ready=cameraView.heading!=null&&cameraView.elevation!=null;
    if(direction&&!(cameraView.fix==null&&/location/.test(direction.textContent)))direction.textContent=!cameraView.fix?'Finding your location…':ready?`Looking ${compassPoint(cameraView.heading)}, ${Math.round(cameraView.elevation)}° ${cameraView.elevation>=0?'up':'down'}`:'Waiting for your phone’s compass…';
    if(!ready){layer.querySelectorAll('[data-ar-id]').forEach(node=>node.remove());return;}
    const halfV=AR_VERTICAL_FOV/2*Math.PI/180,tanV=Math.tan(halfV),tanH=tanV*width/height;
    const shown=[],offLeft=[],offRight=[];
    const objects=cameraObjects();
    for(const object of objects){
      const turn=(((object.bearing-cameraView.heading+540)%360)-180);
      const dx=turn*Math.PI/180,dy=(object.up-cameraView.elevation)*Math.PI/180;
      const x=Math.abs(dx)<Math.PI/2?width/2+Math.tan(dx)/tanH*width/2:(turn<0?-1e4:1e4);
      if(x<-40||x>width+40){(turn<0?offLeft:offRight).push({...object,turn});continue;}
      if(Math.abs(dy)>=Math.PI/2)continue;
      const y=height/2-Math.tan(dy)/tanV*height/2;
      if(y<40||y>height-40)continue;
      shown.push({...object,x,y});
    }
    drawCameraEdges(offLeft,offRight);
    // Nearest first, and no more than a readable handful.
    shown.sort((a,b)=>a.km-b.km);
    // Labels are moved, not rebuilt: rebuilding them every frame swallowed
    // taps (on the labels and on Close) on a real phone.
    const wanted=arrangeCameraLabels(shown.slice(0,8),width,height),keep=new Set(wanted.map(object=>object.id));
    layer.querySelectorAll('[data-ar-id]').forEach(node=>{ if(!keep.has(node.dataset.arId))node.remove(); });
    for(const object of wanted){
      let node=layer.querySelector(`[data-ar-id="${CSS.escape(object.id)}"]`);
      const text=`${object.title}|${object.journey}|${object.facts.filter(Boolean).join(' · ')}|${object.distance}`;
      if(!node){
        node=document.createElement('button');node.type='button';node.className=`ar-label ar-${object.type}${object.icon==='freight'?' ar-freight':''}`;node.dataset.arId=object.id;
        layer.appendChild(node);
      }
      if(node.dataset.text!==text){
        node.dataset.text=text;
        node.setAttribute('aria-label',`${object.title}, ${object.journey}, ${object.distance}`);
        node.innerHTML=`<i aria-hidden="true">${icons[object.icon||object.type]||''}</i><span><strong>${esc(object.title)}</strong><small>${esc(object.journey)}</small>${object.facts.filter(Boolean).length?`<em>${esc(object.facts.filter(Boolean).join(' · '))}</em>`:''}</span><b>${esc(object.distance)}</b>`;
      }
      node.style.left=`${object.x.toFixed(0)}px`;node.style.top=`${object.y.toFixed(0)}px`;
      node.classList.toggle('is-approaching',Boolean(object.approaching));
    }
    layer.dataset.shown=String(wanted.length);
    const hint=$('ar-hint');
    // The next train to pass you, wherever it is: the thing worth waiting for.
    const next=objects.filter(o=>o.type==='train'&&o.approaching&&o.passIn!=null).sort((a,b)=>a.passIn-b.passIn)[0];
    if(hint)hint.textContent=next
      ? `Next past you: ${next.title.replace(/^To /,'')} train ${next.passIn<60?'in under a minute':`in ~${Math.round(next.passIn/60)} min`}${(()=>{const turn=(((next.bearing-cameraView.heading+540)%360)-180);return Math.abs(turn)<25?', ahead':turn<0?', to your left':', to your right';})()}.`
      : wanted.length
      ? `${wanted.length} live ${wanted.length===1?'vehicle':'vehicles'} in view · Tap a label to follow it on the map.`
      : (offLeft.length||offRight.length)?'Turn towards the arrows to find them.':'Slowly look left, right, up and down. Live labels appear when you face them.';
  }
  // Vehicles out of shot: an arrow at the edge of the screen on the side to
  // turn, nearest two each side, so nothing behind you goes unnoticed.
  function drawCameraEdges(left,right){
    const layer=$('ar-labels');if(!layer)return;
    const wanted=[...left.sort((a,b)=>a.km-b.km).slice(0,2).map(o=>({...o,side:'left'})),...right.sort((a,b)=>a.km-b.km).slice(0,2).map(o=>({...o,side:'right'}))];
    const keep=new Set(wanted.map(o=>`edge:${o.id}`));
    layer.querySelectorAll('[data-ar-edge]').forEach(node=>{if(!keep.has(node.dataset.arEdge))node.remove();});
    const counts={left:0,right:0};
    for(const object of wanted){
      const key=`edge:${object.id}`;
      let node=layer.querySelector(`[data-ar-edge="${CSS.escape(key)}"]`);
      if(!node){node=document.createElement('span');node.dataset.arEdge=key;layer.appendChild(node);}
      node.className=`ar-edge ar-edge-${object.side}${object.approaching?' is-approaching':''}`;
      const text=`${object.title} · ${object.distance.replace(' away','')}`;
      // The arrow is its own element so a long name never cuts it off.
      if(node.dataset.text!==text){node.dataset.text=text;node.innerHTML=`${object.side==='left'?'<b aria-hidden="true">←</b>':''}<span>${esc(text)}</span>${object.side==='right'?'<b aria-hidden="true">→</b>':''}`;}
      // Under the compass line, clear of the labels along the horizon.
      node.style.top=`calc(env(safe-area-inset-top, 0px) + ${92+counts[object.side]*40}px)`;counts[object.side]+=1;
    }
  }
  function onCameraOrientation(event) {
    if(!cameraView.active)return;
    const heading=Number.isFinite(event.webkitCompassHeading)?event.webkitCompassHeading:(event.absolute&&Number.isFinite(event.alpha)?(360-event.alpha)%360:null);
    if(heading==null||!Number.isFinite(event.beta))return;
    // Upright (beta 90) looks at the horizon; tipping the top back looks up.
    const elevation=Math.max(-60,Math.min(85,event.beta-90));
    // Smooth the readings so labels do not jitter; the heading wraps at north.
    if(cameraView.heading==null){cameraView.heading=heading;cameraView.elevation=elevation;}
    else {
      cameraView.heading=(cameraView.heading+((((heading-cameraView.heading+540)%360)-180)*0.35)+360)%360;
      cameraView.elevation+=(elevation-cameraView.elevation)*0.35;
    }
    // About fifteen redraws a second is smooth enough and leaves the phone free to answer taps.
    // The last reading is always drawn, a moment later if need be.
    if(cameraView.frame||cameraView.pending)return;
    const wait=66-(Date.now()-(cameraView.drawnAt||0));
    const draw=()=>{cameraView.pending=null;cameraView.drawnAt=Date.now();if(!cameraView.frame)cameraView.frame=requestAnimationFrame(drawCameraView);};
    if(wait<=0)draw(); else cameraView.pending=setTimeout(draw,wait);
  }
  async function refreshCameraGround() {
    if(!cameraView.active||!cameraView.fix)return;
    try {
      const response=await fetch(`/api/around?lat=${encodeURIComponent(cameraView.fix.lat)}&lon=${encodeURIComponent(cameraView.fix.lon)}`,{cache:'no-store'});
      if(response.ok&&cameraView.active){cameraView.ground=(await response.json()).vehicles||[];cameraView.groundAt=Date.now();if(!cameraView.frame)cameraView.frame=requestAnimationFrame(drawCameraView);}
    } catch {}
  }
  function stopCameraView() {
    if(!cameraView.active)return;
    cameraView.active=false;
    cameraView.stream?.getTracks?.().forEach(track=>track.stop());cameraView.stream=null;
    const video=$('ar-video');if(video)video.srcObject=null;
    clearInterval(cameraView.refresh);cameraView.refresh=null;
    clearInterval(cameraView.tick);cameraView.tick=null;
    if(cameraView.watch!=null){navigator.geolocation?.clearWatch?.(cameraView.watch);cameraView.watch=null;}
    clearTimeout(cameraView.pending);cameraView.pending=null;
    window.removeEventListener('deviceorientation',onCameraOrientation);
    window.removeEventListener('deviceorientationabsolute',onCameraOrientation);
    cameraView.heading=null;cameraView.elevation=null;cameraView.ground=[];
    const view=$('ar-view');if(view)view.hidden=true;
    document.body.classList.remove('ar-open');
  }
  function syncCameraKinds() {
    document.querySelectorAll('[data-ar-kind]').forEach(button=>button.setAttribute('aria-pressed',String(cameraView.kinds.has(button.dataset.arKind))));
    // Aircraft come from "planes above me", which is only switched on when wanted.
    if(cameraView.active&&cameraView.kinds.has('aircraft')&&!overheadActive)$('overhead-now')?.click();
    if(cameraView.active&&!cameraView.frame)cameraView.frame=requestAnimationFrame(drawCameraView);
  }
  document.querySelectorAll('[data-ar-kind]').forEach(button=>button.addEventListener('click',()=>{
    const kind=button.dataset.arKind;
    if(cameraView.kinds.has(kind))cameraView.kinds.delete(kind);else cameraView.kinds.add(kind);
    syncCameraKinds();
  }));
  async function startCameraView(kind) {
    if(!('DeviceOrientationEvent' in window)||!navigator.mediaDevices?.getUserMedia){showToast('This needs a phone with a camera and a compass.');return;}
    // iPhone asks for motion access, and only from a tap, so ask first.
    try {
      if(typeof DeviceOrientationEvent.requestPermission==='function'&&await DeviceOrientationEvent.requestPermission()!=='granted'){showToast('Allow motion and orientation access to use the camera view.');return;}
    } catch { showToast('Allow motion and orientation access to use the camera view.');return; }
    let stream;
    try { stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false}); }
    catch { showToast('The camera couldn’t be opened. Allow camera access for this site, or use Point at sky.');return; }
    // Open straight away; find where you are while the camera runs. It used
    // to wait for a location first and gave up after a few seconds, so it
    // only worked if something else had already found you.
    stopSkyPointer();
    const known=lastFix||(lastKnownDeviceLocation?{...lastKnownDeviceLocation,at:Date.now()}:null);
    Object.assign(cameraView,{active:true,fix:known,stream,heading:null,elevation:null,ground:[],kinds:new Set(kind==='all'?['train','bus','aircraft']:[['train','bus','aircraft'].includes(kind)?kind:'aircraft'])});
    const onCameraFix=position=>{
      if(!cameraView.active)return;
      const first=!cameraView.fix;
      cameraView.fix={lat:position.coords.latitude,lon:position.coords.longitude,at:Date.now()};
      lastFix=cameraView.fix;
      if(first){refreshCameraGround();if(cameraView.kinds.has('aircraft')&&!overheadActive)$('overhead-now')?.click();}
      if(!cameraView.frame)cameraView.frame=requestAnimationFrame(drawCameraView);
    };
    const onCameraFixError=()=>{ if(cameraView.active&&!cameraView.fix)$('ar-direction').textContent='Turn on location for this app to see what is around you'; };
    // A quick answer first (a recent fix will do), then follow the phone as it moves.
    navigator.geolocation?.getCurrentPosition?.(onCameraFix,()=>{},{enableHighAccuracy:false,maximumAge:120000,timeout:30000});
    cameraView.watch=navigator.geolocation?.watchPosition?.(onCameraFix,onCameraFixError,{enableHighAccuracy:true,maximumAge:30000,timeout:30000});
    const view=$('ar-view'),video=$('ar-video');
    video.srcObject=stream;video.play?.().catch(()=>{});
    view.hidden=false;document.body.classList.add('ar-open');
    $('ar-labels').innerHTML='';$('ar-direction').textContent='Waiting for your phone’s compass…';
    window.addEventListener('deviceorientation',onCameraOrientation);
    window.addEventListener('deviceorientationabsolute',onCameraOrientation);
    syncCameraKinds();
    refreshCameraGround();
    // Every 5 seconds, and labels glide in between (see cameraObjects).
    cameraView.refresh=setInterval(refreshCameraGround,5000);
    // Moving trains need redrawing even when the phone is held still.
    cameraView.tick=setInterval(()=>{if(cameraView.active&&!cameraView.frame)cameraView.frame=requestAnimationFrame(drawCameraView);},250);
    trackProduct('camera_view_opened','ar');
  }
  // Close on the first touch, without waiting for a full click.
  for(const type of ['pointerdown','click'])$('ar-close')?.addEventListener(type,event=>{event.preventDefault();stopCameraView();});
  // Pinching anywhere but the map used to zoom the whole page on an iPhone,
  // which is hard to undo. The map still pinch-zooms; the page does not.
  for(const type of ['gesturestart','gesturechange','gestureend'])document.addEventListener(type,event=>event.preventDefault(),{passive:false});
  document.addEventListener('touchmove',event=>{ if(event.touches.length>1&&!event.target.closest?.('#map'))event.preventDefault(); },{passive:false});

  document.addEventListener('click',event=>{ if(event.target.closest?.('[data-cool-more]')){trackProduct('plus_limit_reached','discover');openPlusDialog();} });
  $('ar-labels')?.addEventListener('click',event=>{
    const label=event.target.closest?.('[data-ar-id]');if(!label)return;
    const id=label.dataset.arId,type=label.classList.contains('ar-train')?'train':label.classList.contains('ar-bus')?'bus':'aircraft';
    stopCameraView();
    if(type!=='aircraft'&&!enabledActivityTypes.has(type)){enabledActivityTypes.add(type);updateLayerToggleUi();sendViewport();}
    focusActivityById(id,Math.max(13,map.getZoom()));
  });
  document.addEventListener('visibilitychange',()=>{ if(document.hidden)stopCameraView(); });

  function jobPlanes() {
    if(overheadActive&&nearbyAircraftRows.length){showPlanesList();return;}
    pendingPlanesList=true;
    $('overhead-now')?.click();
  }

  function jobTrains() {
    withLocation(async (lat,lon)=>{
      if(!enabledActivityTypes.has('train')){enabledActivityTypes.add('train');updateLayerToggleUi();}
      map.setView([lat,lon],13,{animate:true});
      try {
        const response=await fetch(`/api/trains/near?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&km=5`,{cache:'no-store'});
        const payload=response.ok?await response.json():{trains:[]};
        const trains=payload.trains||[];
        const list=trains.length?`<div class="line-trains">${trains.map(lineTrainRow).join('')}</div>`:'<p class="transport-empty">No trains within 5 km right now.</p>';
        const box=openInfoPanel('Trains near you',trains.length?`${trains.length} within 5 km`:'Nothing passing right now',`<div class="line-view">${list}<button type="button" class="tool-button job-station" data-job-station>Departures from your nearest station</button></div>`);
        box.querySelectorAll('[data-line-train]').forEach(button=>button.addEventListener('click',()=>focusActivityById(button.dataset.lineTrain,Math.max(13,map.getZoom()))));
        box.querySelector('[data-job-station]')?.addEventListener('click',()=>$('station-boards')?.click());
      } catch { showToast('Trains near you couldn’t load right now.'); }
    });
  }

  const JOURNEY_KEY='ukRadarLastJourneyV1';
  function rememberedJourney() { try { return JSON.parse(localStorage.getItem(JOURNEY_KEY)||'null'); } catch { return null; } }
  function jobJourney() {
    const saved=rememberedJourney();
    if($('journey-origin')&&!$('journey-origin').value)$('journey-origin').value=saved?.origin||'My location';
    if($('journey-destination')&&!$('journey-destination').value&&saved?.destination)$('journey-destination').value=saved.destination;
    if($('journey-mode'))$('journey-mode').value='car';
    openJourneyPlanner();
    // A remembered journey runs straight away: one tap to see what is in the way.
    if(saved?.destination)$('journey-form')?.requestSubmit?.();
  }

  document.querySelectorAll('[data-job]').forEach(button=>button.addEventListener('click',()=>{
    const job=button.dataset.job;
    trackProduct('quick_job',job);
    if(job==='planes')jobPlanes();
    else if(job==='trains')jobTrains();
    else if(job==='bus')$('near-me')?.click();
    else if(job==='journey')jobJourney();
    else if(job==='camera')startCameraView('all');
  }));

  // A pull-out Filter drawer on the right of the map, with a section for
  // each layer that is on.
  const FILTER_NAMES={military:'Military',helicopters:'Helicopters','big-rare':'Big and rare',low:'Below 10,000 ft',
    passenger:'Passenger trains',freight:'Freight',late:'Late trains',cancelled:'Cancelled',coaches:'Coaches',now:'Closures now'};
  function setAircraftDrawer(open) {
    $('aircraft-drawer')?.classList.toggle('open',open);
    $('aircraft-drawer-tab')?.setAttribute('aria-expanded',String(open));
  }
  function saveMapFilters() {
    try{
      localStorage.setItem('uk-radar-aircraft-filter',aircraftFilter);
      localStorage.setItem('uk-radar-map-filters',JSON.stringify({train:trainFilter,bus:busFilter,road:roadFilter}));
    }catch{}
  }
  function updateAircraftFilterUi() {
    const drawer=$('aircraft-drawer');
    if(!drawer)return;
    const on=type=>enabledActivityTypes.has(type);
    const sections={train:on('train'),bus:on('bus'),operator:on('train')||on('bus'),road:on('road')||on('incident'),aircraft:on('aircraft')};
    drawer.hidden=!Object.values(sections).some(Boolean);
    if(drawer.hidden)setAircraftDrawer(false);
    if(sections.operator)fillDrawerOperators();
    drawer.querySelectorAll('[data-filter-section]').forEach(section=>{section.hidden=!sections[section.dataset.filterSection];});
    const value={train:trainFilter,bus:busFilter,road:roadFilter};
    drawer.querySelectorAll('[data-map-filter]').forEach(button=>{
      const [group,choice]=button.dataset.mapFilter.split(':');
      button.setAttribute('aria-pressed',String(value[group]===choice));
    });
    drawer.querySelectorAll('[data-aircraft-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.aircraftFilter===aircraftFilter)));
    // Only filters on a visible layer count.
    const active=[sections.aircraft&&aircraftFilter,sections.train&&trainFilter,sections.bus&&busFilter,sections.road&&roadFilter].filter(Boolean);
    const operator=sections.operator&&selectedOperator!=='all'?selectedOperator:null;
    const count=active.length+(operator?1:0);
    drawer.classList.toggle('is-filtering',count>0);
    const label=$('aircraft-drawer-label');
    if(label)label.textContent=count===0?'Filter':count>1?`${count} filters`:(operator||FILTER_NAMES[active[0]]||'Filter');
  }
  function applyMapFilters() {
    filteredCacheKey='';
    saveMapFilters();
    updateAircraftFilterUi();
    updateSubChips();
    renderMap();renderStats();renderRanking();
  }
  $('aircraft-drawer-tab')?.addEventListener('click',()=>setAircraftDrawer(!$('aircraft-drawer').classList.contains('open')));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('aircraft-drawer')?.classList.contains('open'))setAircraftDrawer(false);});
  document.querySelectorAll('[data-aircraft-filter]').forEach(button=>button.addEventListener('click',()=>{
    aircraftFilter=button.dataset.aircraftFilter||'';
    applyMapFilters();
  }));
  document.querySelectorAll('[data-map-filter]').forEach(button=>button.addEventListener('click',()=>{
    const [group,choice]=button.dataset.mapFilter.split(':');
    if(group==='train'){trainFilter=choice;lateTrainsOnly=choice==='late';}
    if(group==='bus')busFilter=choice;
    if(group==='road')roadFilter=choice;
    applyMapFilters();
  }));
  $('drawer-operator')?.addEventListener('change',event=>{
    const main=$('operator-filter');
    if(main){main.value=event.target.value;main.dispatchEvent(new Event('change'));}
    else{selectedOperator=event.target.value||'all';filteredCacheKey='';renderMap();}
    updateAircraftFilterUi();
  });

  function updateLayerToggleUi() {
    updateAircraftFilterUi();
    document.querySelectorAll('[data-layer-chip]').forEach(chip => {
      chip.setAttribute('aria-pressed', String(enabledActivityTypes.has(chip.dataset.layerChip)));
    });
    document.querySelectorAll('[data-layer-toggle]').forEach(button => {
      const visible = enabledActivityTypes.has(button.dataset.layerToggle);
      button.classList.toggle('active', visible);
      button.setAttribute('aria-pressed', String(visible));
      const state = button.querySelector('em');
      if (state) state.textContent = visible ? 'ON' : 'OFF';
    });
    const clear = $('clear-map');
    if (clear) clear.disabled = !enabledActivityTypes.size && placeMode === 'off' && trafficMode === 'off' && heatMode === 'off' && !webcamsEnabled;
    if (effectivePlaceMode() !== fetchedPlaceMode) fetchPlaces();
    const wantRailLines = enabledActivityTypes.has('train');
    if (wantRailLines !== railLines.wanted) { railLines.wanted = wantRailLines; updateRailLines(); }
    if (!wantRailLines && railView.delaysOn) setDelayHotspots(false);
    if (!wantRailLines && lateTrainsOnly) { lateTrainsOnly = false; if (trainFilter === 'late') { trainFilter = ''; saveMapFilters(); } }
    updateSubChips();
    if((enabledActivityTypes.has('weather')&&rainRadar.wanted)!==Boolean(rainRadar.refresh))syncRainRadar();
    syncBaseMap();
  }

  // The second row under the layer chips: things to do with the layer that
  // was switched on last (Trains -> Delays, Late only, Near me...).
  const SUB_LAYERS=['train','bus','aircraft','road','incident','weather'];
  function subLayerGroup(type){return type==='incident'?'road':type;}
  function activeSubLayer(){
    if(lastEnabledLayer&&enabledActivityTypes.has(lastEnabledLayer)&&SUB_LAYERS.includes(lastEnabledLayer))return subLayerGroup(lastEnabledLayer);
    const first=SUB_LAYERS.find(type=>enabledActivityTypes.has(type));
    return first?subLayerGroup(first):null;
  }
  // Rain radar: the open EUMETNET OPERA composite, shown with the Weather
  // layer. The server keeps the last two hours of 15-minute frames.
  const rainRadar={wanted:true,frames:[],bounds:null,overlay:null,shown:null,loadedAt:0,timer:null,refresh:null,attribution:''};
  function rainRadarUrl(frame){return `/api/weather/radar/${encodeURIComponent(frame.key)}.png`;}
  function showRainRadarFrame(frame){
    if(!frame||!rainRadar.bounds)return;
    if(!rainRadar.overlay){
      rainRadar.overlay=L.imageOverlay(rainRadarUrl(frame),rainRadar.bounds,{opacity:.8,interactive:false,attribution:rainRadar.attribution,className:'rain-radar-layer'}).addTo(map);
    } else rainRadar.overlay.setUrl(rainRadarUrl(frame));
    rainRadar.shown=frame.key;
    const at=new Date(frame.time);
    const minutes=Math.round((Date.now()-at.getTime())/60000);
    const clock=at.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
    const time=$('radar-time');if(time)time.textContent=rainRadar.timer?`Rain at ${clock}`:`Rain radar ${clock}${minutes>=0&&minutes<180?` (${minutes} min ago)`:''}`;
    $('map')?.setAttribute('data-rain-radar',frame.key);
  }
  async function loadRainRadar(){
    try {
      const response=await fetch('/api/weather/radar',{cache:'no-store'});
      if(!response.ok)throw new Error();
      const index=await response.json();
      if(!(enabledActivityTypes.has('weather')&&rainRadar.wanted))return;
      rainRadar.loadedAt=Date.now();
      rainRadar.frames=index.frames||[];rainRadar.bounds=index.bounds;rainRadar.attribution=index.attribution||'';
      if(!rainRadar.frames.length){const time=$('radar-time');if(time)time.textContent='Rain radar is not available right now';return;}
      // Warm the browser cache so playing back is smooth.
      rainRadar.frames.forEach(frame=>{const image=new Image();image.src=rainRadarUrl(frame);});
      if(!rainRadar.timer)showRainRadarFrame(rainRadar.frames[rainRadar.frames.length-1]);
    } catch { const time=$('radar-time');if(time)time.textContent='Rain radar is not available right now'; }
  }
  function stopRainRadarPlay(){
    clearInterval(rainRadar.timer);rainRadar.timer=null;
    const latest=rainRadar.frames[rainRadar.frames.length-1];if(latest)showRainRadarFrame(latest);
  }
  function toggleRainRadarPlay(){
    if(rainRadar.timer){stopRainRadarPlay();return;}
    if(rainRadar.frames.length<2){showToast('Not enough radar history yet.');return;}
    let index=0;
    rainRadar.timer=setInterval(()=>{
      index+=1;
      // One pass from two hours ago to now, then rest on the latest.
      if(index>=rainRadar.frames.length){stopRainRadarPlay();updateSubChips();return;}
      showRainRadarFrame(rainRadar.frames[index]);
    },700);
    showRainRadarFrame(rainRadar.frames[0]);
  }
  function syncRainRadar(){
    const on=enabledActivityTypes.has('weather')&&rainRadar.wanted;
    const key=$('radar-key');if(key)key.hidden=!on;
    if(on){
      if(!rainRadar.refresh){loadRainRadar();rainRadar.refresh=setInterval(loadRainRadar,5*60000);}
    } else {
      if(rainRadar.timer)stopRainRadarPlay();
      clearInterval(rainRadar.refresh);rainRadar.refresh=null;
      if(rainRadar.overlay){map.removeLayer(rainRadar.overlay);rainRadar.overlay=null;rainRadar.shown=null;}
      $('map')?.removeAttribute('data-rain-radar');
    }
  }

  function updateSubChips(){
    const row=$('sub-chips');
    if(!row)return;
    const group=activeSubLayer();
    row.hidden=!group;
    row.dataset.layer=group||'';
    row.querySelectorAll('[data-sub-for]').forEach(chip=>{chip.hidden=chip.dataset.subFor!==group;});
    row.querySelectorAll('[data-late-only]').forEach(chip=>chip.setAttribute('aria-pressed',String(lateTrainsOnly)));
    row.querySelectorAll('[data-sub-action="bus-stops"]').forEach(chip=>chip.setAttribute('aria-pressed',String(placeMode==='bus')));
    row.querySelectorAll('[data-sub-action="traffic"]').forEach(chip=>chip.setAttribute('aria-pressed',String(trafficMode!=='off')));
    row.querySelectorAll('[data-sub-action="webcams"]').forEach(chip=>chip.setAttribute('aria-pressed',String(webcamsEnabled)));
    row.querySelectorAll('[data-sub-action="planes"]').forEach(chip=>chip.setAttribute('aria-pressed',String(overheadActive&&nearbyState.source==='device')));
    row.querySelectorAll('[data-sub-action="radar"]').forEach(chip=>chip.setAttribute('aria-pressed',String(rainRadar.wanted)));
    row.querySelectorAll('[data-sub-action="radar-play"]').forEach(chip=>{chip.setAttribute('aria-pressed',String(Boolean(rainRadar.timer)));chip.hidden=chip.dataset.subFor!==group||!rainRadar.wanted;});
  }

  // Every train operator in one list: good, minor or severe, worst first.
  async function showLineStatus(){
    const box=openInfoPanel('Line status','Checking every operator…','<p class="transport-empty">Checking live trains…</p>');
    let operators=[];
    try {
      const response=await fetch('/api/rail/status',{cache:'no-store'});
      if(!response.ok)throw new Error();
      operators=(await response.json()).operators||[];
    } catch { box.innerHTML='<p class="transport-empty">Line status couldn’t load right now.</p>';$('detail-subtitle').textContent='';return; }
    const troubled=operators.filter(row=>['severe','minor'].includes(row.status)).length;
    $('detail-subtitle').textContent=!operators.length?'No operators are reporting right now':troubled?`${troubled} operator${troubled===1?'':'s'} with delays`:'Good service everywhere';
    const tone={severe:'is-very-late',minor:'is-late',good:'is-on-time',unknown:''};
    box.innerHTML=operators.length?`<div class="line-trains line-status">${operators.map((row,index)=>{
      const facts=row.status==='unknown'?`${row.running} running`:[`${row.late} of ${row.running} late`,row.late?`about ${row.average_delay} min`:null,row.cancelled?`${row.cancelled} cancelled`:null].filter(Boolean).join(' · ');
      const worst=(row.worst||[]).map(train=>`<button type="button" class="line-train line-status-train" data-line-train="${esc(train.id)}"><i class="line-dot ${train.cancelled?'is-bad':'is-late'}" aria-hidden="true"></i><span><strong>${esc(train.name)}</strong>${train.destination?`<small>to ${esc(humanTransitLabel(train.destination))}</small>`:''}</span><b class="${train.cancelled?'is-bad':'is-late'}">${train.cancelled?'Cancelled':`${train.delay_minutes} min late`}</b></button>`).join('');
      // National Rail's own words for this operator, with its current incidents.
      const official=row.official&&row.official.disrupted?`<p class="line-official"><span>${esc(row.official.text)}</span>${(row.official.incidents||[]).filter(i=>/^https:\/\//.test(i.url||'')).map(i=>`<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.place)} ↗</a>`).join('')}</p>`:'';
      const liveFacts=row.running?facts:'No trains reporting live';
      return `<div class="line-status-row" data-line-status="${esc(row.status)}"><button type="button" class="line-train" data-line-operator="${index}" aria-expanded="false"><i class="line-dot ${tone[row.status]||''}" aria-hidden="true"></i><span><strong>${esc(row.operator)}</strong><small>${esc(liveFacts)}</small></span><b class="${row.status==='severe'?'is-bad':row.status==='minor'?'is-late':''}">${esc(row.label)}</b></button>${official}${worst?`<div class="line-status-worst" hidden>${worst}</div>`:''}</div>`;
    }).join('')}</div>`:'<p class="transport-empty">No operators are reporting right now.</p>';
    box.querySelectorAll('[data-line-operator]').forEach(button=>button.addEventListener('click',()=>{
      const list=button.parentElement.querySelector('.line-status-worst');if(!list)return;
      list.hidden=!list.hidden;button.setAttribute('aria-expanded',String(!list.hidden));
    }));
    box.querySelectorAll('[data-line-train]').forEach(button=>button.addEventListener('click',()=>focusActivityById(button.dataset.lineTrain,Math.max(11,map.getZoom()))));
  }

  async function showBusDisruptions(){
    const bounds=map.getBounds?.();
    const box=openInfoPanel('Bus disruptions','Checking this area…','<p class="transport-empty">Loading official cancellations, diversions and stop closures…</p>');
    let rows=[];
    try {
      if(!bounds)throw new Error('Map bounds unavailable');
      const params=new URLSearchParams({west:bounds.getWest(),south:bounds.getSouth(),east:bounds.getEast(),north:bounds.getNorth(),limit:'30'});
      const response=await fetch(`/api/buses/disruptions?${params}`,{cache:'no-store'});
      if(!response.ok)throw new Error('Bus disruptions unavailable');
      const payload=await response.json();
      rows=Array.isArray(payload.disruptions)?payload.disruptions:[];
      rows.forEach(row=>globalCoolById.set(row.id,row));
    } catch {
      box.innerHTML='<p class="transport-empty">Bus disruptions couldn’t refresh right now. Try again in a moment.</p>';
      $('detail-subtitle').textContent='Official feed unavailable';
      return;
    }
    const list=rows.slice(0,30).map(a=>{
      const facts=[a.impact?.where,a.impact?.when].filter(Boolean).join(' · ');
      return `<button type="button" class="line-train" data-bus-disruption="${esc(a.id)}"><i class="line-dot is-bad" aria-hidden="true"></i><span><strong>${esc(a.impact?.title||a.name)}</strong>${facts?`<small>${esc(facts)}</small>`:''}</span><b>›</b></button>`;
    }).join('');
    $('detail-subtitle').textContent=rows.length?`${rows.length} in this area`:'None in this area right now';
    box.innerHTML=list?`<div class="line-trains">${list}</div>`:'<p class="transport-empty">No stop closures or diversions are reported here.</p>';
    box.querySelectorAll('[data-bus-disruption]').forEach(button=>button.addEventListener('click',()=>focusActivityById(button.dataset.busDisruption,Math.max(15,map.getZoom()))));
  }

  function updateCoolRailSubtypeUi() {
    const bar = $('cool-rail-subfilters');
    if (!bar) return;
    bar.classList.toggle('hidden', currentWorkspace !== 'cool' || selectedType !== 'cool-rail');
    bar.querySelectorAll('[data-cool-rail-subtype]').forEach(button => {
      const active = button.dataset.coolRailSubtype === selectedRailSubtype;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
  }

  function trafficTileStyle() {
    if (trafficMode === 'delays') return 'relative-delay';
    return 'relative0';
  }

  function renderTrafficLayer() {
    if (trafficLayer && map.hasLayer(trafficLayer)) map.removeLayer(trafficLayer);
    if (trafficIncidentLayer && map.hasLayer(trafficIncidentLayer)) map.removeLayer(trafficIncidentLayer);
    trafficLayer = null;
    trafficIncidentLayer = null;
    const legend = $('traffic-legend');
    $('map').dataset.traffic = trafficMode;
    if (trafficMode === 'off' || !trafficConfigured) {
      legend?.classList.add('hidden');
      return;
    }
    trafficTileErrorShown = false;
    const wantsFlow = ['flow','delays','combined'].includes(trafficMode);
    const wantsIncidents = ['incidents','combined'].includes(trafficMode);
    if (wantsFlow) {
      const style = trafficTileStyle();
      trafficLayer = L.tileLayer('/api/traffic/tile/{z}/{x}/{y}.png?style=' + encodeURIComponent(style), {
        pane: 'trafficPane', opacity: trafficMode === 'delays' ? 0.86 : 0.72,
        maxZoom: 18, minZoom: 5, updateWhenIdle: true, keepBuffer: 1,
        attribution: 'Live traffic © TomTom'
      });
      trafficLayer.on('tileerror', () => {
        if (trafficTileErrorShown) return;
        trafficTileErrorShown = true;
        showToast('Live traffic could not load. Try again shortly.');
      });
      trafficLayer.addTo(map);
    }
    if (wantsIncidents) {
      trafficIncidentLayer = L.tileLayer('/api/traffic/incidents/tile/{z}/{x}/{y}.png', {
        pane:'trafficPane', opacity:.95, maxZoom:18, minZoom:5, updateWhenIdle:true, keepBuffer:1,
        attribution:'Traffic incidents © TomTom'
      });
      trafficIncidentLayer.on('tileerror',()=>{
        if (trafficTileErrorShown) return;
        trafficTileErrorShown=true;
        showToast('Traffic incidents could not load. Try again shortly.');
      });
      trafficIncidentLayer.addTo(map);
    }
    legend?.classList.remove('hidden');
  }

  async function setTrafficMode(mode, quiet=false) {
    if (!['off','flow','delays','incidents','combined'].includes(mode)) mode = 'off';
    if (mode !== 'off') {
      try {
        const r = await fetch('/api/traffic/status', {cache:'no-store'});
        const payload = await r.json();
        trafficConfigured = Boolean(payload.configured);
      } catch { trafficConfigured = false; }
      if (!trafficConfigured) {
        mode = 'off';
        if (!quiet) showToast('Live traffic is not available right now.');
      }
    }
    trafficMode = mode;
    const select = $('traffic-style');
    if (select) {
      if (mode !== 'off') select.value = mode;
      select.disabled = mode === 'off';
    }
    const button = $('traffic-quick');
    button?.classList.toggle('active', mode !== 'off');
    button?.setAttribute('aria-pressed', String(mode !== 'off'));
    const status = button?.querySelector('small');
    if (status) status.textContent = mode === 'off' ? 'Show road speeds' : `On · ${mode === 'combined' ? 'speeds and incidents' : mode === 'delays' ? 'slow traffic only' : mode === 'incidents' ? 'incidents only' : 'speeds only'}`;
    renderTrafficLayer();
    updateLayerToggleUi();
    if (!quiet) trackProduct('layer_selected', mode === 'off' ? 'traffic-off' : `traffic-${mode}`);
  }

  function setMapMode(mode) {
    mode = 'standard';
    syncBaseMap();
    $('map').dataset.mapMode = mode;
    try { localStorage.setItem('ukRadarMapMode', mode); } catch {}
    if (trafficMode !== 'off') renderTrafficLayer();
    trackProduct('map_mode_selected', mode);
  }

  function tooltipHtml(a) {
    if (a.kind === 'event' && a.impact) {
      const im = a.impact;
      const pills = [im.impact, im.when].filter(Boolean).map(text => `<span class="tooltip-pill ${im.active && im.rank >= 2 ? 'warn' : ''}">${esc(text)}</span>`).join('');
      return `<div class="activity-tooltip-card"><div class="tooltip-top"><strong>${icons[a.type] || '•'} ${esc(im.title || im.what)}</strong></div>${im.where ? `<div class="tooltip-sub">${esc(humanTransitLabel(im.where))}</div>` : ''}<div class="tooltip-pills">${pills}</div></div>`;
    }
    const route = a.origin && a.destination ? `${a.origin} → ${a.destination}` : (a.route || a.subtitle || a.position_label || '');
    const speed = compactSpeed(a);
    const heading = compass(a.bearing);
    const delayN = a.delay_minutes == null ? null : Number(a.delay_minutes);
    const delay = delayN == null ? null : (delayN === 0 ? 'On time' : `${delayN > 0 ? '+' : ''}${delayN} min`);
    const movement = [heading, speed].filter(Boolean).join(' • ');
    const next = a.next_location ? `Next ${a.next_location}` : null;
    const isTrain = a.type === 'train';
    const status = isTrain ? null : (a.status || a.severity || null);
    const pills = [];
    if (status) pills.push(`<span class="tooltip-pill">${esc(status)}</span>`);
    if (isTrain) { const label = trainStatusLabel(a); if (label !== 'Live') pills.push(`<span class="tooltip-pill ${/late/.test(label) && delayN > 5 ? 'warn' : 'good'}">${esc(label)}</span>`); }
    else if (delay) pills.push(`<span class="tooltip-pill ${delayN > 5 ? 'warn' : 'good'}">${esc(delay)}</span>`);
    if (movement) pills.push(`<span class="tooltip-pill">${esc(movement)}</span>`);
    if (next) pills.push(`<span class="tooltip-pill">${esc(next)}</span>`);
    if (a.type === 'train' && a.position_method === 'estimated') pills.push('<span class="tooltip-pill warn">Estimated between reports</span>');
    if (a.type === 'vessel' && a.vehicle_ref) pills.push(`<span class="tooltip-pill">MMSI ${esc(a.vehicle_ref)}</span>`);
    if (['sar','aircraft'].includes(a.type) && (a.aircraft_model || a.vehicle_ref)) pills.push(`<span class="tooltip-pill">${esc(a.aircraft_model || a.vehicle_ref)}</span>`);
    const badge = currentWorkspace === 'cool' ? coolBadgeHtml(a,true) : '';
    const staleTrain=a.type==='train'&&a.restored_snapshot===true;
    if(staleTrain)pills.unshift('<span class="tooltip-pill warn">Last known position</span>');
    const liveLabel=a.kind==='event'?'EVENT':staleTrain?'STALE':'LIVE';
    return `<div class="activity-tooltip-card"><div class="tooltip-top"><strong>${icons[a.type] || '•'} ${esc(a.name)}</strong><span class="tooltip-live${staleTrain?' stale':''}">${liveLabel}</span></div>${badge}${route ? `<div class="tooltip-sub">${esc(route)}</div>` : ''}<div class="tooltip-pills">${pills.slice(0,4).join('')}</div><div class="tooltip-hint">${a.type==='train'?'Last railway report':'Updated'} ${esc(relativeAge(Math.max(0,(Date.now()-new Date(a.last_updated||0).getTime())/1000)))} • click for detail</div></div>`;
  }

  function operatorTheme(a) {
    if (!a || !['aircraft','bus','train'].includes(a.type)) return null;
    if(a.type==='train'){
      const name=providerName(a.operator || a.operator_name || a.toc || a.owner);
      if(!name)return null;
      const theme=trainLiveryTheme('',name);
      return {name,primary:theme.primary,accent:theme.accent,ink:theme.ink,kind:'TRAIN OPERATOR'};
    }
    const text=[a.operator,a.name,a.subtitle,a.route,a.source_id].filter(Boolean).join(' ').toUpperCase();
    const themes=a.type==='aircraft' ? [
      [/BRITISH AIRWAYS|\bBAW[A-Z0-9]*\b|SPEEDBIRD/, ['British Airways','#071b3d','#d71920','#f4f7fb']],
      [/RYANAIR|\bRYR[A-Z0-9]*\b/, ['Ryanair','#073590','#f1c933','#ffffff']],
      [/EASYJET|\bEZY[A-Z0-9]*\b/, ['easyJet','#ff6600','#242424','#ffffff']],
      [/JET2|\bEXS[A-Z0-9]*\b|CHANNEX/, ['Jet2','#d71920','#283b62','#ffffff']],
      [/TUI AIRWAYS|\bTOM[A-Z0-9]*\b/, ['TUI Airways','#70cbf4','#d40e14','#12243a']],
      [/VIRGIN ATLANTIC|\bVIR[A-Z0-9]*\b/, ['Virgin Atlantic','#7b1230','#d71920','#ffffff']],
      [/LOGANAIR|\bLOG[A-Z0-9]*\b/, ['Loganair','#203864','#e4477e','#ffffff']],
      [/WIZZ|\bWUK[A-Z0-9]*\b|\bWZZ[A-Z0-9]*\b/, ['Wizz Air','#c6007e','#00aeef','#ffffff']],
      [/EMIRATES|\bUAE[A-Z0-9]*\b/, ['Emirates','#c8102e','#d9b65d','#ffffff']],
      [/LUFTHANSA|\bDLH[A-Z0-9]*\b/, ['Lufthansa','#05164d','#ffad00','#ffffff']],
    ] : [
      [/ARRIVA/, ['Arriva','#004c45','#72d6c9','#ffffff']],
      [/STAGECOACH/, ['Stagecoach','#005daa','#ee7523','#ffffff']],
      [/FIRST BUS|FIRSTGROUP|FIRST WEST|FIRST SOUTH|FIRST EAST/, ['First Bus','#542683','#c91f7c','#ffffff']],
      [/NATIONAL EXPRESS/, ['National Express','#007e8a','#98d9d4','#ffffff']],
      [/GO[- ]?AHEAD|GO NORTH EAST|BRIGHTON & HOVE|OXFORD BUS/, ['Go-Ahead','#003b70','#16a4a8','#ffffff']],
      [/TRANSDEV|HARROGATE BUS|BURNLEY BUS|COASTLINER/, ['Transdev','#d71920','#262b32','#ffffff']],
    ];
    const matched=themes.find(([pattern])=>pattern.test(text));
    if(!matched)return null;
    const [name,primary,accent,ink]=matched[1];
    return {name,primary,accent,ink,kind:a.type==='aircraft'?'AIRLINE':'BUS OPERATOR'};
  }

  function activityOperatorName(a) {
    const themed=operatorTheme(a);
    if(themed?.name)return themed.name;
    if(a?.type==='train')return providerName(a.operator || a.operator_name || a.toc || a.owner);
    if(['aircraft','bus'].includes(a?.type))return providerName(a.operator || a.operator_name);
    return '';
  }

  function operatorKey(value) {
    return String(value || '').trim().toLocaleLowerCase('en-GB');
  }

  function operatorMatches(a) {
    return selectedOperator==='all' || operatorKey(activityOperatorName(a))===operatorKey(selectedOperator);
  }

  function updateOperatorFilterOptions() {
    const select=$('operator-filter');
    if(!select)return;
    const names=[...new Set(activities.map(activityOperatorName).filter(Boolean))]
      .sort((a,b)=>a.localeCompare(b,'en-GB',{sensitivity:'base'}));
    if(selectedOperator!=='all' && !names.some(name=>operatorKey(name)===operatorKey(selectedOperator)))names.unshift(selectedOperator);
    const signature=`${selectedOperator}|${names.join('|')}`;
    if(signature===operatorOptionsSignature)return;
    operatorOptionsSignature=signature;
    select.innerHTML='<option value="all">All operators</option>'+names.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
    select.value=selectedOperator;
  }

  // The drawer lists operators of the trains and buses switched on.
  let drawerOperatorSignature='';
  function fillDrawerOperators() {
    const select=$('drawer-operator');
    if(!select)return;
    const shown=[...new Set(activities.filter(a=>['train','bus'].includes(a.type)&&enabledActivityTypes.has(a.type)).map(activityOperatorName).filter(Boolean))]
      .sort((a,b)=>a.localeCompare(b,'en-GB',{sensitivity:'base'}));
    if(selectedOperator!=='all'&&!shown.includes(selectedOperator))shown.unshift(selectedOperator);
    const signature=`${selectedOperator}|${shown.join('|')}`;
    if(signature===drawerOperatorSignature)return;
    drawerOperatorSignature=signature;
    select.innerHTML='<option value="all">All operators</option>'+shown.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
    select.value=selectedOperator;
  }

  function clearOperatorIdentity() {
    const box=$('detail-operator');
    if(!box)return;
    box.classList.add('hidden');
    box.innerHTML='';
    box.removeAttribute('style');
    const panel=$('detail-panel');
    panel?.classList.remove('operator-themed-detail','operator-theme-train','operator-theme-aircraft','operator-theme-bus');
    panel?.removeAttribute('style');
  }

  function renderOperatorIdentity(a) {
    const box=$('detail-operator');
    if(!box)return;
    const theme=operatorTheme(a);
    if(!theme){clearOperatorIdentity();return;}
    box.classList.remove('hidden');
    box.style.cssText=`--operator-primary:${theme.primary};--operator-accent:${theme.accent};--operator-ink:${theme.ink}`;
    const panel=$('detail-panel');
    panel?.classList.add('operator-themed-detail');
    panel?.classList.remove('operator-theme-train','operator-theme-aircraft','operator-theme-bus');
    panel?.classList.add(`operator-theme-${a.type}`);
    if(panel)panel.style.cssText=`--operator-primary:${theme.primary};--operator-accent:${theme.accent};--operator-ink:${theme.ink}`;
    const movement=a.type==='aircraft'
      ? [a.aircraft_model||a.aircraft_type_code,a.vehicle_ref].filter(Boolean).join(' • ')
      : a.type==='train'
        ? (a.headcode && a.headcode!==a.name ? a.headcode : '')
        : [a.route?`Route ${a.route}`:null,a.vehicle_ref?`Vehicle ${a.vehicle_ref}`:null].filter(Boolean).join(' • ');
    box.innerHTML=`<i aria-hidden="true"></i><span><small>${esc(theme.kind)}</small><strong>${esc(theme.name)}</strong>${movement?`<b>${esc(movement)}</b>`:''}</span>`;
  }

  function clearPhoto() {
    const box = $('detail-photo');
    if (!box) return;
    box.classList.add('hidden');
    box.innerHTML = '';
    mediaLoadedFor = null;
  }

  // Photos already found this visit are not asked for again: reopening a
  // train or plane shows its picture at once.
  const mediaCache=new Map();
  async function mediaFor(a, signal) {
    const hit=mediaCache.get(a.id);
    if(hit&&Date.now()-hit.at<10*60000)return hit.payload;
    const q=new URLSearchParams();
    if(['aircraft','sar'].includes(a.type)){
      q.set('kind',a.type==='sar'?'sar':'aircraft');
      // Planes from "above me" are not kept on the server: say what this is,
      // so an example of the type can be found when there is no exact photo.
      if(a.aircraft_model||a.aircraft_type_code)q.set('model',a.aircraft_model||a.aircraft_type_code);
      if(a.name)q.set('callsign',a.name);
      if(a.operator)q.set('operator',a.operator);
    }
    const response=await fetch(`/api/activity/${encodeURIComponent(a.id)}/media${String(q)?`?${q}`:''}`,{signal});
    if(!response.ok)throw new Error('Image lookup unavailable');
    const payload=await response.json();
    mediaCache.set(a.id,{at:Date.now(),payload});
    if(mediaCache.size>200)mediaCache.delete(mediaCache.keys().next().value);
    return payload;
  }

  async function loadPhoto(a) {
    const box = $('detail-photo');
    if (!box) return;
    if (mediaLoadedFor === a.id) return;
    mediaLoadedFor = a.id;
    const requestId = ++mediaRequest;
    box.classList.remove('hidden');
    box.innerHTML = '<div class="photo-loading">Looking for a matching photo…</div>';
    const controller = new AbortController();
    const timeout = setTimeout(()=>controller.abort(), 8000);
    try {
      const payload = await mediaFor(a, controller.signal);
      if (requestId !== mediaRequest || selectedId !== a.id) return;
      if (!payload.ok || !payload.thumbnail_url) {
        // A missing image is cleaner than a plausible-but-wrong historic photo.
        // For a classified Cool train, explain that decision and offer only the
        // constrained Commons search produced by the backend.
        if (a.type === 'train' && coolInfo(a).candidate) {
          const search = payload.search_url ? safeUrl(payload.search_url) : null;
          box.classList.remove('hidden');
          box.innerHTML = `<div class="detail-photo-copy"><strong>No matching train photo found</strong><span>We do not know the exact locomotive yet.</span>${search ? `<span><a target="_blank" rel="noopener" href="${esc(search)}">Search Wikimedia Commons ↗</a></span>` : ''}</div>`;
        } else if (a.type === 'vessel') {
          const search = payload.search_url ? safeUrl(payload.search_url) : null;
          box.innerHTML = `<div class="detail-photo-copy"><strong>No matching vessel photo found</strong>${search ? `<span><a target="_blank" rel="noopener" href="${esc(search)}">Search Wikimedia Commons ↗</a></span>` : ''}</div>`;
        } else {
          box.classList.add('hidden');
          box.innerHTML = '';
        }
        return;
      }
      const attribution = [payload.artist, payload.license].filter(Boolean).join(' • ');
      const sourceUrl = safeUrl(payload.page_url || payload.search_url);
      const licenceUrl = safeUrl(payload.license_url || payload.page_url || payload.search_url);
      let imageTruth = null;
      if (payload.match_kind === 'type_reference' && payload.reference_label) imageTruth = payload.reference_label;
      else if (a.type === 'train') {
        imageTruth = `Photo of this vehicle or class${a.vehicle_ref ? ` ${a.vehicle_ref}` : ''} — not live`;
      } else if (a.type === 'bus') imageTruth = `Photo matched to vehicle ${a.vehicle_ref} — not live`;
      else if (a.type === 'vessel') imageTruth = 'Photo of this vessel — not live';
      box.innerHTML = `<a target="_blank" rel="noopener" href="${esc(sourceUrl)}"><img src="${esc(safeUrl(payload.thumbnail_url))}" alt="${esc(payload.title || a.name)}" loading="eager" fetchpriority="high" decoding="async"></a><div class="detail-photo-copy"><strong>${esc(payload.title || a.name)}</strong>${imageTruth ? `<b class="image-truth">${esc(imageTruth)}</b>` : ''}<span>${esc(attribution || payload.source || 'Wikimedia Commons')} • <a target="_blank" rel="noopener" href="${esc(sourceUrl)}">image source ↗</a> • <a target="_blank" rel="noopener" href="${esc(licenceUrl)}">licence ↗</a></span></div>`;
    } catch {
      if (requestId !== mediaRequest) return;
      box.classList.add('hidden'); box.innerHTML = '';
    } finally { clearTimeout(timeout); }
  }

  async function loadAircraftInfo(a) {
    const box = $('detail-photo');
    if (!box) return;
    if (mediaLoadedFor === a.id) return;
    mediaLoadedFor = a.id;
    const requestId = ++mediaRequest;
    box.classList.remove('hidden');
    // Do not flash a generic type photo and replace it a second later. Hold a
    // stable, sized placeholder until the one final image choice is ready.
    box.innerHTML = '<div class="photo-loading aircraft-photo-loading"><i aria-hidden="true"></i><span>Finding this aircraft…</span></div>';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const reference=await mediaFor(a, controller.signal);
      if (requestId !== mediaRequest || selectedId !== a.id) return;
      if(!reference.ok || !reference.thumbnail_url){
        const spotterUrl=a.source_id?`https://www.planespotters.net/hex/${encodeURIComponent(String(a.source_id).toUpperCase())}`:null;
        box.classList.remove('hidden');
        box.innerHTML=`<div class="detail-photo-copy"><strong>${reference.reason==='planespotters_unavailable'?'Planespotters photo unavailable':'No Planespotters photo for this aircraft'}</strong>${spotterUrl?`<span><a target="_blank" rel="noopener" href="${esc(spotterUrl)}">Check Planespotters ↗</a></span>`:''}</div>`;
        return;
      }
      const reg = a.vehicle_ref || null;
      const model = a.aircraft_model || a.aircraft_type_code || null;
      const identity = [reg, model].filter(Boolean).join(' • ');
      const imageUrl=reference.thumbnail_url;
      const imagePage=reference.page_url || reference.search_url;
      const width=Number(reference.thumbnail_width)||420, height=Number(reference.thumbnail_height)||280;
      const image = `<a target="_blank" rel="noopener" href="${esc(safeUrl(imagePage))}"><img src="${esc(safeUrl(imageUrl))}" alt="${esc(identity || a.name)}" width="${width}" height="${height}" loading="eager" fetchpriority="high" decoding="async"></a>`;
      const imageTruth=reference.match_kind==='type_reference'?(reference.reference_label||'Example of this aircraft type — not this exact aircraft'):'Archive photo of this aircraft registration — not live';
      const imageCredit=[reference.artist,reference.license,reference.source].filter(Boolean).join(' • ');
      const links = a.source_id ? `<a target="_blank" rel="noopener" href="https://www.planespotters.net/hex/${esc(String(a.source_id).toUpperCase())}">Planespotters ↗</a>` : '';
      box.innerHTML = `${image}<div class="detail-photo-copy"><b class="image-truth">${esc(imageTruth)}</b><span>${esc(imageCredit || 'Planespotters.net')}${links ? ` • ${links}` : ''}</span></div>`;
      const remoteImage=box.querySelector('img');
      remoteImage?.addEventListener('error',()=>{
        if(requestId!==mediaRequest || selectedId!==a.id)return;
        const spotterUrl=a.source_id?`https://www.planespotters.net/hex/${encodeURIComponent(String(a.source_id).toUpperCase())}`:null;
        box.innerHTML=`<div class="detail-photo-copy"><strong>Planespotters photo could not load</strong>${spotterUrl?`<span><a target="_blank" rel="noopener" href="${esc(spotterUrl)}">Open on Planespotters ↗</a></span>`:''}</div>`;
      },{once:true});
    } catch {
      if (requestId !== mediaRequest) return;
      box.classList.add('hidden'); box.innerHTML='';
    } finally {
      clearTimeout(timeout);
    }
  }

  async function loadNearestCamera(a) {
    const box=$('detail-photo'); if(!box)return;
    const cacheKey=`camera:${a.id}`;
    if(mediaLoadedFor===cacheKey)return;
    mediaLoadedFor=cacheKey;
    const requestId=++cameraDetailRequest;
    box.classList.remove('hidden');
    box.innerHTML='<div class="photo-loading">Finding the nearest verified live traffic camera…</div>';
    try {
      const response=await fetch(`/api/cameras/nearest?lat=${encodeURIComponent(a.lat)}&lon=${encodeURIComponent(a.lon)}&max_km=25`,{cache:'no-store'});
      if(!response.ok)throw new Error('camera lookup unavailable');
      const payload=await response.json();
      if(requestId!==cameraDetailRequest || selectedId!==a.id)return;
      const camera=payload.camera;
      if(!camera){box.classList.add('hidden');box.innerHTML='';return;}
      box.innerHTML=`${cameraMediaHtml(camera)}<div class="detail-photo-copy"><strong>Nearest live camera • ${esc(camera.distance_km)} km</strong><span>${esc([camera.name,camera.view,camera.source].filter(Boolean).join(' • '))}</span><button class="mini-button show-nearest-camera" type="button">Show camera on map</button></div>`;
      box.querySelector('.show-nearest-camera')?.addEventListener('click',()=>{
        if(!cameras.some(row=>row.id===camera.id))cameras.push(camera);
        webcamsEnabled=true; renderCameraMarkers(); selectCamera(camera,true);
      });
    } catch {
      if(requestId!==cameraDetailRequest || selectedId!==a.id)return;
      box.classList.add('hidden');box.innerHTML='';
    }
  }

  // ATC-style data block: callsign, then flight level (or feet when low),
  // climbing/descending and ground speed. The data line shows from regional
  // zoom; it is updated in place so moving planes keep gliding.
  function aircraftCallsign(v) {
    const name=String(v.name||'').trim();
    if(name&&!/^(special aircraft|aircraft|unknown)\b/i.test(name))return name.toUpperCase().slice(0,10);
    return String(v.vehicle_ref||v.source_id||name||'').toUpperCase().slice(0,10);
  }
  // A value the feed did not send is unknown, never 0. Number(null) is 0,
  // which turned "no altitude" into "0ft": EZY72GD on Birmingham's runway.
  function aircraftNumber(value) {
    return value==null||value===''?NaN:Number(value);
  }
  function aircraftSpeedKt(v) {
    let speed=aircraftNumber(v.speed);
    const unit=String(v.speed_unit||'').toLowerCase();
    if(unit==='m/s')speed*=1.94384; else if(unit==='km/h')speed/=1.852;
    return speed;
  }
  // The change since the plane's previous report, to tell a landing roll
  // (slowing, coming down) from a take-off roll (speeding up, going up).
  // Only a report from the last two minutes counts: a plane back in range
  // after half an hour has not "sped up".
  const aircraftReports=new Map();
  function aircraftTrend(v) {
    const now={at:String(v.last_updated||''),ms:Date.parse(v.last_updated||''),alt:aircraftNumber(v.altitude_ft),speed:aircraftSpeedKt(v)};
    let entry=aircraftReports.get(v.id);
    if(!entry){if(aircraftReports.size>4000)aircraftReports.clear();entry={cur:now,prev:null};aircraftReports.set(v.id,entry);}
    else if(entry.cur.at!==now.at){entry.prev=entry.cur;entry.cur=now;}
    const {cur,prev}=entry;
    if(!prev||!(cur.ms-prev.ms<=120000))return null;
    return {dAlt:cur.alt-prev.alt,dSpeed:cur.speed-prev.speed};
  }
  // The airport a plane is at or just above, and how far it is from the
  // airport's middle: 4 km covers the runways and the last mile of approach
  // or climb-out.
  function nearestAirfield(v) {
    const lat=Number(v.lat),lon=Number(v.lon);
    if(!Number.isFinite(lat)||!Number.isFinite(lon))return null;
    let best=null,bestKm=4;
    for(const field of airportLayer.rows||[]){
      const km=distanceKm(lat,lon,Number(field.lat),Number(field.lon));
      if(km<bestKm){best=field;bestKm=km;}
    }
    return best?{field:best,km:bestKm}:null;
  }
  // On or just above a runway. Aircraft report barometric altitude, so on the
  // ground it reads about the airport's height (Birmingham ~341 ft), 0 ft or
  // even below, depending on the weather, and some send no altitude at all
  // during the take-off or landing roll. Say what the plane is doing instead.
  function aircraftGroundPhase(v) {
    const alt=aircraftNumber(v.altitude_ft),speed=aircraftSpeedKt(v),rate=aircraftNumber(v.vertical_rate_fpm);
    const trend=aircraftTrend(v);
    const onGround=/on ground/i.test(String(v.status||''));
    // Helicopters hover and fly low and slow away from airports.
    if(!onGround&&aircraftMapShape(v).rotorcraft)return null;
    const nearest=nearestAirfield(v),field=nearest?.field||null;
    const height=Number.isFinite(alt)?alt-(Number(field?.elevation_ft)||0):NaN;
    // No altitude sent, over the airfield itself (within 2 km of its middle):
    // on or just above the runway. Live: EZY682G at Birmingham, 122 kt.
    const overField=!Number.isFinite(alt)&&Number.isFinite(speed)&&nearest!=null&&nearest.km<2;
    const low=onGround||(Number.isFinite(height)&&height<150)||overField;
    // Within 800 ft of the airport's height: the weather alone can move the
    // reading by a few hundred feet, so this is only used with a clear trend.
    const near=Boolean(field)&&Number.isFinite(height)&&height<800;
    if(!low&&!near)return null;
    if(!onGround&&Number.isFinite(speed)&&speed>200)return null;   // a low pass, not a runway
    const at=field?` at ${field.name} Airport`:'',from=field?` from ${field.name} Airport`:'';
    if(!Number.isFinite(speed))return onGround?{short:'On ground',chip:'On the ground',sentence:`is on the ground${at}`}:null;
    // No altitude and a runway speed not yet borne out by its movement (a
    // first report): too often a taxiing plane's bad speed, so wait for the
    // next report before saying it is landing or taking off.
    if(speed>=40&&!Number.isFinite(alt)&&!v.speed_verified)return {short:'On ground',chip:'On the ground',sentence:`is on the ground${at}`};
    // A plane cannot fly at walking pace: slow means it is on the ground.
    // Live Heathrow reports: parked 0-1 kt, pushing back or taxiing from 3 kt.
    if(speed<2)return {short:'Parked',chip:'On the ground',sentence:`is on the ground${at}`};
    const knots=Math.round(speed);
    if(speed<40)return {short:'Taxiing',chip:'On the ground',sentence:`is taxiing${at} at ${knots} knots`};
    const down=(Number.isFinite(rate)&&rate<-150)||Boolean(trend&&(trend.dAlt<-40||trend.dSpeed<-3));
    const up=(Number.isFinite(rate)&&rate>150)||Boolean(trend&&(trend.dAlt>40||trend.dSpeed>3));
    // On the runway with no climb, descent or change of speed to go on, the
    // flight's usual route says which: from this airport is a take-off.
    const code=String(field?.iata||'').toUpperCase();
    const fromHere=Boolean(code)&&String(v.origin||'').toUpperCase()===code;
    const toHere=Boolean(code)&&String(v.destination||'').toUpperCase()===code;
    const still=low&&!down&&!up;
    if((down&&!up)||(still&&toHere&&!fromHere))return {short:'Landing',chip:'Landing',sentence:low?`is landing${at} at ${knots} knots`:`is coming in to land${at} at ${knots} knots`};
    if((up&&!down)||(still&&fromHere&&!toHere))return {short:'Taking off',chip:'Taking off',sentence:low?`is taking off${from} at ${knots} knots`:`has just taken off${from}, climbing at ${knots} knots`};
    if(!low)return null;
    return {short:'On runway',chip:'On the runway',sentence:`is on the runway${at} at ${knots} knots, landing or taking off`};
  }
  // "Taking off 131kt", or the phase alone when it has no speed to show.
  function aircraftPhaseText(phase,speed) {
    return ['Parked','On ground'].includes(phase.short)||!Number.isFinite(speed)?phase.short:`${phase.short} ${Math.round(speed)}kt`;
  }
  function aircraftDataLine(v) {
    const speed=aircraftSpeedKt(v);
    const phase=aircraftGroundPhase(v);
    if(phase)return aircraftPhaseText(phase,speed);
    const alt=aircraftNumber(v.altitude_ft),rate=aircraftNumber(v.vertical_rate_fpm);
    // Below 100 ft the barometric reading says little (it can be negative on
    // a high-pressure day), so a helicopter over the sea reads "Low". With no
    // altitude sent, the line shows the speed alone.
    const level=!Number.isFinite(alt)?'':alt>=6000?`FL${String(Math.round(alt/100)).padStart(3,'0')}`:alt<100?'Low':`${(Math.round(alt/100)*100).toLocaleString('en-GB')}ft`;
    const trend=Number.isFinite(rate)?(rate>300?'↑':rate<-300?'↓':''):'';
    const pace=Number.isFinite(speed)&&speed>=1?`${Math.round(speed)}kt`:'';
    return [level&&trend?`${level} ${trend}`:level,pace].filter(Boolean).join(' ');
  }
  // Not heard from for 2 minutes: its last known position, drawn faded. The
  // national collector now refreshes each UK region about once a minute.
  function aircraftIsStale(v) {
    const updated=Date.parse(v.last_updated||'');
    return Number.isFinite(updated)&&(Date.now()-updated)/1000>120;
  }

  function aircraftMapIdentity(v) {
    const text=[v.aircraft_model,v.aircraft_type_code,v.subtitle,v.name].filter(Boolean).join(' ').toUpperCase();
    const known=[
      [/LYNX|WILDCAT/, 'LYNX'],
      [/APACHE|AH-?64/, 'APACHE'], [/CHINOOK|CH-?47|\bH47\b/, 'CHINOOK'],
      [/EUROFIGHTER|TYPHOON|\bEUFI\b/, 'TYPHOON'], [/\bF-?35\b|LIGHTNING II/, 'F-35'],
      [/\bA400M?\b|\bATLAS\b/, 'ATLAS'], [/\bC-?17\b|GLOBEMASTER/, 'C-17'],
      [/VOYAGER|\bKC-?2\b|A330 MRTT/, 'VOYAGER'], [/\bC-?130\b|HERCULES|\bC30J\b/, 'HERCULES'],
      [/\bHAWK\b/, 'HAWK'], [/OSPREY|\bV-?22\b/, 'OSPREY'], [/AWACS|SENTRY|\bE-?3\b/, 'AWACS'],
      [/COASTGUARD|SIKORSKY S-?92|\bS92\b/, 'COASTGUARD'],
      [/SPITFIRE|\bSPIT\b/, 'SPITFIRE'], [/HURRICANE/, 'HURRICANE'], [/LANCASTER/, 'LANCASTER'],
      [/CESSNA\s*152|\bC152\b/, 'CESSNA 152'], [/CESSNA\s*172|\bC172\b/, 'CESSNA 172'],
      [/\bKC-?135\b|STRATOTANKER/, 'KC-135'],
    ];
    for (const [pattern,label] of known) if(pattern.test(text)) return label;
    const fallback=String(v.aircraft_model || v.aircraft_type_code || v.vehicle_ref || (v.type==='sar'?'RESCUE':'AIRCRAFT')).trim();
    return fallback.replace(/^(BOEING|AIRBUS|LEONARDO|LOCKHEED MARTIN|SIKORSKY)\s+/i,'').toUpperCase().slice(0,18);
  }

  function aircraftMapShape(v) {
    const text=[v.aircraft_model,v.aircraft_type_code,v.aircraft_category,v.subtitle].filter(Boolean).join(' ').toUpperCase();
    // Flat top-down silhouettes on a 24px grid, pointing north.
    const shape=(kind,body)=>`<svg class="map-aircraft-svg map-${kind}-svg" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
    const P=AIRCRAFT_SHAPES;
    const helicopter=shape('helicopter',P.helicopter), chinook=shape('chinook',P.chinook), apache=shape('apache',P.apache);
    const fastJet=shape('fastjet',P.fastjet), heavy=shape('heavy',P.heavy), airliner=shape('airliner',P.airliner), propeller=shape('propeller',P.propeller);
    if(/CHINOOK|CH-?47|\bH47\b/.test(text)) return {kind:'chinook',html:chinook,rotorcraft:true};
    if(/APACHE|AH-?64/.test(text)) return {kind:'apache',html:apache,rotorcraft:true};
    if(v.type==='sar' || /LYNX|WILDCAT|MERLIN|GAZELLE|PUMA|HELICOPTER|SIKORSKY|ROBINSON|AGUSTA|EUROCOPTER|DAUPHIN|SQUIRREL|JETRANGER|\b(?:R22|R44|R66|EC20|EC30|EC35|EC45|EC55|EC75|AS50|AS55|AS65|AS32|AS3B|A109|A119|A139|A169|A189|AW09|B06|B06T|B407|B412|B429|B505|H125|H130|H135|H145|H160|H175|S76|S92|MD52|MD60|EXPL|G2CA|EN28|EN48|H269|S300|BK17|NH90)\b/.test(text)) return {kind:'helicopter',html:helicopter,rotorcraft:true};
    if(/TYPHOON|EUROFIGHTER|\b(?:F-?35|F-?16|F-?15|F-?18|TORNADO|HAWK|GRIPEN|RAFALE)\b/.test(text)) return {kind:'fastjet',html:fastJet,rotorcraft:false};
    if(/\b(?:KC-?135|STRATOTANKER|VOYAGER|A330|A340|A350|A380|747|C-?17|GLOBEMASTER|A400M?|ATLAS|HERCULES|C-?130|AWACS|SENTRY|RIVET)\b/.test(text)) return {kind:'heavy',html:heavy,rotorcraft:false};
    if(/SPITFIRE|HURRICANE|LANCASTER|MUSTANG|DAKOTA|CESSNA|PIPER|CIRRUS|BEECH|DIAMOND|\bC1(?:52|72|82)\b|\bPA-?\d+\b|\bSR2\d\b|\bDA\d{2}\b|\bDH8\b|DASH\s*8|ATR\s*\d+/.test(text)) return {kind:'propeller',html:propeller,rotorcraft:false};
    return {kind:'airliner',html:airliner,rotorcraft:false};
  }

  function aircraftDisplayBearing(v) {
    const points=movementHistory.points(v.id);
    if(points.length>1){
      const from=points[points.length-2],to=points[points.length-1];
      if(distanceKm(from.lat,from.lon,to.lat,to.lon)>.05){
        const p1=from.lat*Math.PI/180,p2=to.lat*Math.PI/180,d=(to.lon-from.lon)*Math.PI/180;
        const y=Math.sin(d)*Math.cos(p2);
        const x=Math.cos(p1)*Math.sin(p2)-Math.sin(p1)*Math.cos(p2)*Math.cos(d);
        return (Math.atan2(y,x)*180/Math.PI+360)%360;
      }
    }
    const supplied=Number(v.bearing);
    return Number.isFinite(supplied)?((supplied%360)+360)%360:0;
  }

  // Freight (and light engines/departmental 4, 6 and 7 headcodes) gets its
  // own wagon icon and colour, so it is told apart from passenger trains at
  // a glance.
  function isFreightTrain(a) {
    if(a?.type!=='train')return false;
    if(a.cool_subtype==='freight')return true;
    const head=String(a.headcode||String(a.name||'').match(/\b[0-9][A-Z][0-9]{2}\b/i)?.[0]||'').trim();
    return /^[467]/.test(head);
  }

  function markerIcon(v) {
    const type = v.type || 'other';
    const method = v.position_method || 'static';
    const eventClass = v.kind === 'event' ? ' marker-event' : '';
    const bearing = Number(v.bearing);
    const speed = compactSpeed(v);
    const numericSpeed = Number(v.speed);
    const directional = method === 'gps' && map.getZoom() >= 9 && Number.isFinite(bearing) && (!Number.isFinite(numericSpeed) || numericSpeed >= 1) && ['bus','vessel','sar','aircraft','gritter'].includes(type);
    const heading=((bearing % 360)+360)%360;
    const pointer = directional && !['aircraft','sar'].includes(type) ? `<div class="motion-pointer" style="transform:rotate(${heading}deg)"></div>` : '';
    const speedTag = directional && speed && map.getZoom() >= 12 ? `<span class="motion-speed">${esc(speed)}</span>` : '';
    const cool=coolInfo(v);
    if (['aircraft','sar'].includes(type)) {
      const shape=aircraftMapShape(v);
      const identity=aircraftMapIdentity(v);
      const priority=currentWorkspace === 'cool' && cool.score >= 45 ? `<em>${esc(cool.label)}</em>` : '';
      const observedBearing=aircraftDisplayBearing(v);
      const rotation = observedBearing;
      const callsign=aircraftCallsign(v);
      const kind=identity&&identity!==callsign?`<i class="ac-type">${esc(identity)}</i>`:'';
      return L.divIcon({
        className:'marker-wrap aerial-marker-wrap',
        html:`<div class="motion-shell aerial-motion-shell"><div class="aerial-map-marker aerial-${esc(type)} ac-${shape.kind} aircraft-shape-${shape.kind}${aircraftIsStale(v)?' is-stale':''}" title="${esc(callsign)} · ${esc(identity)}"><span class="aircraft-silhouette${shape.rotorcraft?' rotorcraft-silhouette':''}" style="transform:rotate(${rotation}deg)">${shape.html}</span><span class="ac-block"><b class="ac-callsign">${esc(callsign)}</b>${kind}<small class="ac-data">${esc(aircraftDataLine(v))}</small></span>${priority}</div>${pointer}</div>`,
        iconSize:[44,44],iconAnchor:[22,22]
      });
    }
    if(currentWorkspace==='cool' && cool.score>=45){
      return L.divIcon({
        className:`marker-wrap marker-wrap-${esc(type)} cool-marker-wrap`,
        html:`<div class="motion-shell"><div class="cool-map-marker cool-${esc(cool.theme)}" title="${esc(cool.label)} — ${esc(v.name)}"><span>${iconSvg(coolIconFor(v,cool))}</span><small>${esc(cool.label)}</small></div>${pointer}${speedTag}</div>`,
        iconSize:[58,52],iconAnchor:[29,26]
      });
    }
    const trainTheme=type==='train' ? trainLiveryTheme('',v.operator || v.operator_name || v.toc || v.owner) : null;
    const freight=isFreightTrain(v);
    const coach=coachInfo(v);
    const trainStyle=!freight && trainTheme && trainTheme.key!=='reference'
      ? ` style="background:${esc(trainTheme.primary)}"`
      : '';
    const coincidentCount=type==='train' ? Math.max(1,Number(v._coincidentCount)||1) : 1;
    const coincidentSlot=type==='train' ? Math.max(0,Number(v._coincidentSlot)||0) : 0;
    const offsetX=coincidentCount>1 ? Number(v._coincidentX)||0 : 0;
    const offsetY=coincidentCount>1 ? Number(v._coincidentY)||0 : 0;
    const stemLength=Math.hypot(offsetX,offsetY);
    const stemAngle=Math.atan2(-offsetY,-offsetX)*180/Math.PI;
    const trainFan=coincidentCount>1
      ? `<i class="train-coincident-stem" style="width:${stemLength.toFixed(1)}px;transform:rotate(${stemAngle.toFixed(1)}deg)"></i><i class="train-coincident-origin" style="transform:translate(${-offsetX}px,${-offsetY}px)"></i>`
      : '';
    const trainIndex=coincidentCount>1 ? `<b class="train-coincident-index" aria-hidden="true">${coincidentSlot+1}</b>` : '';
    const cancelled=type==='train'&&Boolean(v.cancelled_at);
    const punctuality=type==='train'&&!cancelled ? punctualityClass(v.delay_minutes) : '';
    const punctualityDot=punctuality ? `<i class="train-punctuality ${punctuality}" aria-hidden="true"></i>` : '';
    const sharedNote=coincidentCount>1 ? ` • ${coincidentCount} trains share this reported point` : '';
    // Buses carry their route number, and once zoomed in to street level
    // where they are going, so the right bus is found without tapping each.
    const busRoute=type==='bus'?String(v.route||String(v.name||'').replace(/^Bus\s+/i,'')).trim().slice(0,5):'';
    const busWhere=type==='bus'&&v.destination?humanTransitLabel(String(v.destination)).replace(/\s+(?:Stand|Stop|Bay)\s*[A-Z0-9]{0,3}$/i,'').replace(/\s+(?:Bus|Coach)\s+Station$/i,'').slice(0,22):'';
    const busTag=busRoute?`<b class="bus-route-tag">${esc(busRoute)}${busWhere?`<small class="bus-destination"> · ${esc(busWhere)}</small>`:''}</b>`:'';
    const trainWhere=type==='train'&&v.destination?humanTransitLabel(String(v.destination)).slice(0,28):'';
    const trainTag=trainWhere?`<b class="train-destination-tag" title="to ${esc(humanTransitLabel(String(v.destination)))}"><small>to</small> ${esc(trainWhere)}</b>`:'';
    const trainService=String(v.headcode||String(v.name||'').match(/\b[0-9][A-Z][0-9]{2}\b/i)?.[0]||coincidentSlot+1).toUpperCase();
    const trainServiceTag=type==='train'&&coincidentCount>1?`<small class="train-service-tag">${esc(trainService)}</small>`:'';
    return L.divIcon({
      className: `marker-wrap marker-wrap-${esc(type)}${coincidentCount>1?' train-coincident-wrap':''}`,
      html: `<div class="motion-shell${type==='train'?' train-marker-shell':''}">${trainFan}<div class="marker-bubble marker-${esc(type)} marker-${esc(method)}${eventClass}${freight?' marker-freight':''}${coach?' marker-coach':''}${cancelled?' is-cancelled':''}"${cancelled?'':trainStyle} title="${esc((freight?'Freight train • ':'')+(coach?`${coach.name} coach • `:'')+(v.position_label||'')+sharedNote)}"><span>${(freight?icons.freight:coach?icons.coach:icons[type]) || '•'}</span>${trainIndex}${punctualityDot}</div>${pointer}${speedTag}${busTag}${trainTag}${trainServiceTag}</div>`,
      iconSize: [42,42], iconAnchor: [21-offsetX,21-offsetY],
      tooltipAnchor: type==='train' ? [offsetX,offsetY-17] : [0,0]
    });
  }

  function placeMarkerIcon(p) {
    const icon = placeIcons[p.place_type] || iconSvg('pin');
    const isBusStop=p.place_type==='bus_stop';
    const markerContent=isBusStop
      ? `<span class="bus-stop-dot" aria-hidden="true">${icons.bus}</span>`
      : icon;
    if(p.place_type==='rail_station'){
      return L.divIcon({
        className: 'place-marker-wrap station-marker-wrap',
        html: `<div class="place-marker place-rail_station station-marker" title="${esc(p.name)}"><i aria-hidden="true"></i></div><span class="station-label">${esc(humanTransitLabel(p.name))}</span>`,
        iconSize:[12,12], iconAnchor:[6,6]
      });
    }
    return L.divIcon({
      className: 'place-marker-wrap',
      html: `<div class="place-marker place-${esc(p.place_type || 'other')}" title="${esc(p.name)}">${markerContent}</div>`,
      // A stop is a small dot on the spot itself, not a big sign on a pin.
      iconSize:isBusStop?[28,28]:[27,27], iconAnchor:isBusStop?[14,14]:[13,13]
    });
  }

  function cameraMarkerIcon(camera) {
    return L.divIcon({
      className:'camera-marker-wrap',
      html:`<div class="camera-map-marker${camera.available === false ? ' unavailable' : ''}" title="${esc(camera.name)}">${icons.camera}</div>`,
      iconSize:[34,34],iconAnchor:[17,17]
    });
  }

  function cameraMediaHtml(camera) {
    const image = safeUrl(camera?.image_url || '');
    const video = safeUrl(camera?.video_url || '');
    if (video !== '#') return `<video controls muted playsinline preload="metadata"${image !== '#' ? ` poster="${esc(image)}"` : ''}><source src="${esc(video)}" type="video/mp4"></video>`;
    if (image !== '#') return `<img class="camera-live-media" src="${esc(image)}" alt="Live view from ${esc(camera.name || 'traffic camera')}" loading="lazy">`;
    return '<div class="camera-unavailable">This camera has no playable image at the moment.</div>';
  }

  function updateWebcamControl() {
    const button = $('live-webcams');
    if (!button) return;
    button.classList.toggle('active', webcamsEnabled);
    button.setAttribute('aria-pressed', String(webcamsEnabled));
    $('map').dataset.webcams = webcamsEnabled ? 'on' : 'off';
    updateSubChips();
    const label = button.querySelector('small');
    if (label) label.textContent = webcamsEnabled ? `${cameras.length} mapped live cameras` : 'Official mapped feeds in this view';
  }

  function renderCameraMarkers() {
    const shown = new Set(webcamsEnabled ? cameras.map(camera => camera.id) : []);
    for (const [id, marker] of cameraMarkers.entries()) {
      if (!shown.has(id)) { cameraMarkerGroup.removeLayer(marker); cameraMarkers.delete(id); }
    }
    if (!webcamsEnabled) { updateWebcamControl(); return; }
    for (const camera of cameras) {
      let marker = cameraMarkers.get(camera.id);
      if (!marker) {
        marker = L.marker([camera.lat,camera.lon], {icon:cameraMarkerIcon(camera),riseOnHover:true});
        bindHoverCard(marker,`<div class="activity-tooltip-card"><div class="tooltip-top"><strong>${esc(camera.name)}</strong><span class="tooltip-live">LIVE CAM</span></div><div class="tooltip-sub">${esc([camera.view,camera.source].filter(Boolean).join(' • '))}</div><div class="tooltip-hint">Click for the live image</div></div>`,{direction:'top',offset:[0,-11],opacity:.97,className:'activity-tooltip'});
        marker.on('click',()=>{ const current=cameras.find(row=>row.id===camera.id) || camera; selectCamera(current,true); });
        cameraMarkerGroup.addLayer(marker); cameraMarkers.set(camera.id,marker);
      } else {
        marker.setLatLng([camera.lat,camera.lon]);
        marker.setIcon(cameraMarkerIcon(camera));
      }
    }
    updateWebcamControl();
  }

  async function fetchCameras(force=false) {
    if (!webcamsEnabled) { cameras=[]; renderCameraMarkers(); return; }
    const b=map.getBounds(), requestId=++cameraRequest;
    $('live-webcams')?.setAttribute('aria-busy','true');
    try {
      const url=`/api/cameras?west=${encodeURIComponent(b.getWest())}&south=${encodeURIComponent(b.getSouth())}&east=${encodeURIComponent(b.getEast())}&north=${encodeURIComponent(b.getNorth())}&limit=250`;
      const response=await fetch(url,{cache:'no-store'});
      if(!response.ok)throw new Error('camera feed unavailable');
      const payload=await response.json(); if(requestId!==cameraRequest)return;
      cameras=payload.cameras||[]; renderCameraMarkers();
      if(force && !cameras.length)showToast('No traffic cameras in this area.');
    } catch {
      if(requestId!==cameraRequest)return;
      renderCameraMarkers();
      if(force)showToast('Traffic cameras are unavailable right now.');
    } finally {
      if(requestId===cameraRequest)$('live-webcams')?.removeAttribute('aria-busy');
    }
  }

  function selectCamera(camera, pan) {
    if (!camera) return;
    selectedId=null; selectedPlaceId=null; trackedActivityId=null; selectedDetailSignature=''; sendWatching(null); clearPlaceLink(); clearPhoto(); clearPlaceBoard(); clearActivityIntelligence(); clearCoverage(); clearDiscoveryDetail();
    syncDetailRouteControl(null);
    $('detail-panel')?.classList.add('has-selection'); $('detail-sidebar')?.classList.remove('is-empty');
    $('detail-title').textContent=camera.name || 'Live traffic camera';
    $('detail-subtitle').textContent=[camera.view,camera.source].filter(Boolean).join(' • ') || 'Traffic camera';
    $('detail-fields').innerHTML=[field('View',camera.view),field('Status',camera.available===false?'Temporarily unavailable':'Available'),field('Source',camera.source),field('Latitude',Number(camera.lat).toFixed(5)),field('Longitude',Number(camera.lon).toFixed(5))].join('');
    for(const id of ['detail-insight','detail-related']){const el=$(id);if(el){el.classList.add('hidden');el.innerHTML='';}}
    const photo=$('detail-photo'); photo.classList.remove('hidden'); photo.innerHTML=`${cameraMediaHtml(camera)}<div class="detail-photo-copy"><strong>Current camera</strong><span>Refreshes about every ${esc(camera.refresh_seconds || 180)} seconds.</span></div>`;
    $('follow').classList.add('hidden'); $('track-activity')?.classList.add('hidden');
    if(pan)map.setView([camera.lat,camera.lon],Math.max(map.getZoom(),14),{animate:true});
  }

  function renderPlaces() {
    const shown = new Set(places.map(p => p.id));
    let clusterDirty=false;
    for (const [id, marker] of placeMarkers.entries()) {
      if (!shown.has(id)) { placeGroup.removeLayer(marker); placeMarkers.delete(id); clusterDirty=true; }
    }
    for (const p of places) {
      let marker=placeMarkers.get(p.id);
      const positionSignature=`${Number(p.lat).toFixed(6)}|${Number(p.lon).toFixed(6)}`;
      if (!marker) {
        marker=L.marker([p.lat,p.lon],{icon:placeMarkerIcon(p),pane:'placePane',riseOnHover:true,zIndexOffset:1600});
        bindHoverCard(marker,`<div class="activity-tooltip-card"><strong>${placeIcons[p.place_type]||iconSvg('pin')} ${esc(p.name)}</strong><span>${esc(p.subtitle || p.source || 'Place')}</span><small>Click for departures</small></div>`,{direction:'top',offset:[0,-11],opacity:.97,className:'activity-tooltip'});
        marker.on('click',()=>selectPlace(p.id,true));
        marker.__positionSignature=positionSignature;
        placeGroup.addLayer(marker); placeMarkers.set(p.id,marker);clusterDirty=true;
      } else if(marker.__positionSignature!==positionSignature){marker.setLatLng([p.lat,p.lon]);marker.__positionSignature=positionSignature;clusterDirty=true;}
    }
    if (clusterDirty&&typeof placeGroup.refreshClusters === 'function') placeGroup.refreshClusters();
  }

  function effectivePlaceMode() {
    // Stations are part of the railway picture: show them whenever trains are
    // on, in addition to anything chosen under "Stations and stops".
    if(!enabledActivityTypes.has('train'))return placeMode;
    if(placeMode==='off')return 'rail';
    if(placeMode==='bus')return 'all';
    return placeMode;
  }

  async function fetchPlaces() {
    const placeModeNow=effectivePlaceMode();
    fetchedPlaceMode=placeModeNow;
    if (placeModeNow === 'off') { placeFetchSeq+=1; places=[]; renderPlaces(); return; }
    const b=map.getBounds(), seq=++placeFetchSeq;
    try {
      const u=`/api/places?west=${encodeURIComponent(b.getWest())}&south=${encodeURIComponent(b.getSouth())}&east=${encodeURIComponent(b.getEast())}&north=${encodeURIComponent(b.getNorth())}&zoom=${encodeURIComponent(map.getZoom())}&mode=${encodeURIComponent(placeModeNow)}`;
      const r=await fetch(u,{cache:'no-store'}); if(!r.ok) throw new Error('places unavailable');
      const payload=await r.json(); if(seq!==placeFetchSeq)return;
      places=(payload.places||[]).filter(place=>place.place_type!=='ferry_terminal'); renderPlaces(); return places;
    } catch { if(seq===placeFetchSeq){places=[];renderPlaces();} return places; }
  }

  function clearPlaceBoard() {
    clearTimeout(placeBoardRefreshTimer);
    placeBoardRefreshTimer=null;
    placeBoardRequest+=1;
    boardLinkRequest+=1;
    placeBoardController?.abort();
    placeBoardController=null;
    const box=$('detail-placeboard');
    if(box){box.classList.add('hidden');box.classList.remove('rail-departure-board','bus-departure-board');box.innerHTML='';}
  }

  function trainMatchesBoard(a, row, allowUndated=false) {
    if(a?.type!=='train')return false;
    const normalize=value=>String(value || '').trim().toUpperCase();
    if(row.train_uid && normalize(a.train_uid)!==normalize(row.train_uid))return false;
    if(row.service_date && a.service_date && a.service_date!==row.service_date)return false;
    if(row.service_date && !a.service_date && !allowUndated)return false;
    if(row.headcode && normalize(a.headcode)!==normalize(row.headcode))return false;
    return true;
  }

  function boardClock(value) {
    if(!value)return '';
    const text=String(value);
    if(!text.includes('T'))return text.slice(0,5);
    const parsed=new Date(text);
    return Number.isFinite(parsed.getTime()) ? parsed.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}) : '';
  }

  function boardRow(row, kind) {
    // One readable line per service: booked time, where it goes (or comes
    // from), platform and operator, and a plain status on the right.
    const booked=boardClock(row.scheduled || row.clock || row.expected || row.actual) || (row.minutes!=null?`${row.minutes} min`:'—');
    const expected=boardClock(row.expected);
    const delay=Number(row.delay_minutes);
    const statusText=String(row.status || '').trim();
    let status='', statusClass='';
    if(/cancel/i.test(statusText)){status='Cancelled';statusClass='is-bad';}
    else if(Number.isFinite(delay)&&delay>=1&&expected&&expected!==booked){status=expected;statusClass='is-late';}
    else if(Number.isFinite(delay)&&delay<=0){status='On time';statusClass='is-good';}
    else if(/^arrived$/i.test(statusText)){status='Arrived';}
    else if(kind==='bus'&&row.minutes!=null){status=row.minutes<=0?'Due':`${row.minutes} min`;statusClass=/live/i.test(statusText)?'is-good':'';}
    else if(/^live$/i.test(statusText)){status='Live';statusClass='is-good';}
    const place=kind==='arrival' ? humanTransitLabel(row.origin || '') : humanTransitLabel(row.destination || row.route || row.name || '');
    const operator=humanTransitLabel(row.operator_name || (kind==='bus'?row.operator:'') || '');
    const moved=row.platform_changed&&row.booked_platform;
    // Freight: where from, what it carries, and whether it stops or runs through.
    const facts=kind==='freight' ? [
      row.cargo?`<b>${esc(row.cargo)}</b>`:null, row.passes?'passes through':'stops here',
      row.headcode?esc(row.headcode):null, row.origin?`from ${esc(humanTransitLabel(row.origin))}`:null,
    ].filter(Boolean).join(' · ') : [
      row.platform?(moved?`<b class="platform-changed">Platform ${esc(row.platform)} (was ${esc(row.booked_platform)})</b>`:`Platform ${esc(row.platform)}`):null,
      row.usual_platform?`<span class="usual-platform" title="From the platforms this train has used recently">${row.platform?'usually':'Usually platform'} ${esc(row.usual_platform.platform)} <small>(${esc(row.usual_platform.seen)} of last ${esc(row.usual_platform.of)})</small></span>`:null,
      kind==='bus'&&row.route?`Route ${esc(row.route)}`:null, operator?esc(operator):null,
    ].filter(Boolean).join(' · ');
    const live=row.live_train_id || row.live_vehicle_id;
    const serviceRef=live || row.headcode || row.trip_id || row.vehicle_ref;
    const attrs=[
      live?`data-live-id="${esc(live)}"`:'',
      row.headcode?`data-headcode="${esc(row.headcode)}"`:'',
      row.train_uid?`data-train-uid="${esc(row.train_uid)}"`:'',
      row.service_date?`data-service-date="${esc(row.service_date)}"`:'',
      row.trip_id?`data-trip-id="${esc(row.trip_id)}"`:'',
      row.vehicle_ref?`data-vehicle-ref="${esc(row.vehicle_ref)}"`:'',
      row.origin?`data-origin="${esc(row.origin)}"`:'',
      row.destination?`data-destination="${esc(row.destination)}"`:'',
      row.operator?`data-operator="${esc(row.operator)}"`:'',
      row.platform?`data-platform="${esc(row.platform)}"`:'',
      statusText?`data-status="${esc(statusText)}"`:'',
      `data-activity-type="${kind==='bus'?'bus':'train'}"`,
    ].filter(Boolean).join(' ');
    const label=[kind==='arrival'?`From ${place||'unknown origin'}`:(place||'Service'),booked,status].filter(Boolean).join(', ');
    return `<button type="button" class="board-row board-${kind}${kind==='freight'?' board-departure':''}${serviceRef?' board-clickable':''}${live?' is-live':''}${status==='Cancelled'?' is-cancelled':''}" aria-label="${esc(label)}" ${attrs}><span class="board-time">${esc(booked)}</span><span class="board-main"><strong>${kind==='arrival'&&place?`<small>from</small> `:''}${esc(place || 'Service')}</strong>${facts?`<span>${facts}</span>`:''}</span><span class="board-status ${statusClass}">${esc(status)}</span></button>`;
  }

  async function openScheduledTrain(btn) {
    const uid=btn.dataset.trainUid || '';
    if(!uid)return false;
    selectedId=null; selectedPlaceId=null; trackedActivityId=null; sendWatching(null);
    syncDetailRouteControl(null);
    if(focusMarker){map.removeLayer(focusMarker);focusMarker=null;}
    clearPlaceLink(); clearPlaceBoard(); clearPhoto(); clearOperatorIdentity(); clearDiscoveryDetail();
    $('detail-panel')?.classList.add('has-selection');
    $('detail-sidebar')?.classList.remove('is-empty');
    $('detail-title').textContent=btn.dataset.headcode || 'Scheduled train';
    $('detail-subtitle').textContent=[btn.dataset.origin,btn.dataset.destination].filter(Boolean).join(' → ') || 'Published train route';
    $('detail-fields').innerHTML=[
      field('Service','Scheduled'),field('Platform',btn.dataset.platform),field('Status',btn.dataset.status),
      field('Operator',btn.dataset.operator),field('Train UID',uid),
    ].join('');
    $('detail-insight')?.classList.add('hidden');
    $('detail-related')?.classList.add('hidden');
    $('detail-coverage')?.classList.add('hidden');
    $('save-place')?.classList.add('hidden'); $('save-discovery')?.classList.add('hidden');
    $('share-discovery')?.classList.add('hidden'); $('share-activity')?.classList.add('hidden');
    $('track-activity')?.classList.add('hidden'); $('follow')?.classList.add('hidden');
    const box=$('detail-intelligence');
    if(!box)return true;
    const requestId=++intelligenceRequest;
    intelligenceLoadedFor=`schedule:${uid}`;
    box.classList.remove('hidden');
    box.innerHTML='<small>PUBLISHED TRAIN ROUTE</small><p class="coverage-loading">Loading calling points…</p>';
    try {
      const params=new URLSearchParams({headcode:btn.dataset.headcode || ''});
      if(btn.dataset.serviceDate)params.set('service_date',btn.dataset.serviceDate);
      const response=await fetch(`/api/rail/schedule/${encodeURIComponent(uid)}?${params}`,{cache:'no-store'});
      const payload=await response.json().catch(()=>({}));
      if(requestId!==intelligenceRequest)return true;
      if(!response.ok)throw new Error(payload?.detail || 'route unavailable');
      box.innerHTML=renderRailIntelligence(payload?.detail || payload).replace('LIVE TRAIN JOURNEY','PUBLISHED TRAIN ROUTE');
    } catch {
      if(requestId!==intelligenceRequest)return true;
      box.innerHTML='<small>PUBLISHED TRAIN ROUTE</small><div class="transport-unavailable"><strong>Route unavailable</strong><span>The timetable entry is valid, but its calling points could not be loaded.</span></div>';
    }
    return true;
  }

  function bindBoardLiveLinks(box) {
    box.querySelectorAll('.board-clickable').forEach(btn=>btn.addEventListener('click',async()=>{
      const originPlaceId=selectedPlaceId;
      const requestId=++boardLinkRequest;
      const current=()=>requestId===boardLinkRequest && selectedPlaceId===originPlaceId;
      const row={train_uid:btn.dataset.trainUid,headcode:btn.dataset.headcode,service_date:btn.dataset.serviceDate};
      const openActivity=a=>{
        if(!current())return;
        globalCoolById.set(a.id,a);
        if(toggleableActivityTypes.has(a.type)){enabledActivityTypes.add(a.type);updateLayerToggleUi();}
        if(originPlaceId){returnPlaceId=originPlaceId;detailBackButton.classList.remove('hidden');}
        renderMap();selectActivity(a.id,true,12);sendViewport();
      };
      btn.setAttribute('aria-busy','true');btn.disabled=true;
      try {
      let id=btn.dataset.liveId || '';
      if(!id && btn.dataset.activityType==='train' && row.train_uid){
        // Headcodes repeat across operators and dates. Only an unambiguous
        // service identity can join a timetable row to a streamed train.
        const matches=activities.filter(a=>trainMatchesBoard(a,row));
        if(matches.length===1)id=matches[0].id;
      }
      if(!id && btn.dataset.tripId){
        const wanted=btn.dataset.tripId.trim();
        id=activities.find(a=>a.type==='bus'&&String(a.journey_ref||'').trim()===wanted)?.id || '';
      }
      if(!id && btn.dataset.vehicleRef){
        const wanted=btn.dataset.vehicleRef.trim();
        id=activities.find(a=>a.type==='bus'&&String(a.vehicle_ref||'').trim()===wanted)?.id || '';
      }
      let a=id?activityById(id):null;
      if(a && btn.dataset.activityType==='train' && !trainMatchesBoard(a,row,true)){a=null;id='';}
      if(!a && id && ['train','bus'].includes(btn.dataset.activityType)){
        try {
          const response=await fetch(`/api/activity/${encodeURIComponent(id)}`,{cache:'no-store',signal:AbortSignal.timeout(10000)});
          if(response.ok){const item=await response.json();if(item?.id && (btn.dataset.activityType!=='train'||trainMatchesBoard(item,row,true)))a=item;}
        }catch{}
      }
      if(!current())return;
      if(!a && btn.dataset.activityType==='train' && (btn.dataset.trainUid || btn.dataset.headcode)){
        try{
          const params=new URLSearchParams();
          if(btn.dataset.trainUid)params.set('train_uid',btn.dataset.trainUid);
          if(btn.dataset.headcode)params.set('headcode',btn.dataset.headcode);
          if(btn.dataset.serviceDate)params.set('service_date',btn.dataset.serviceDate);
          const response=await fetch(`/api/rail/locate?${params}`,{cache:'no-store',signal:AbortSignal.timeout(10000)});
          const payload=await response.json().catch(()=>({}));
          if(response.ok && payload?.activity?.id && trainMatchesBoard(payload.activity,row,true))a=payload.activity;
        }catch{}
      }
      if(!current())return;
      if(a)openActivity(a);
      else if(btn.dataset.activityType==='train' && btn.dataset.trainUid){
        if(originPlaceId){returnPlaceId=originPlaceId;detailBackButton.classList.remove('hidden');}
        showToast('No live train position is published right now; showing its route instead.');
        await openScheduledTrain(btn);
      }
      else showToast('A live position is not available for this service right now.');
      } finally {btn.removeAttribute('aria-busy');btn.disabled=false;}
    }));
  }

  function clearPlaceLink() {
    if(placeLinkLine && map.hasLayer(placeLinkLine)) map.removeLayer(placeLinkLine);
    placeLinkLine=null;
  }

  function focusNextLive(place, liveId) {
    clearPlaceLink();
    if(!liveId)return;
    const a=activityById(liveId); if(!a)return;
    placeLinkLine=L.polyline([[place.lat,place.lon],[a.lat,a.lon]],{weight:2,opacity:.72,dashArray:'5 7',interactive:false,className:'place-link-line'}).addTo(map);
    const marker=markers.get(liveId);
    if(marker){ try{marker.openTooltip(); marker.setZIndexOffset?.(900);}catch{} }
    const bounds=L.latLngBounds([[place.lat,place.lon],[a.lat,a.lon]]);
    if(!map.getBounds().contains([a.lat,a.lon])) map.fitBounds(bounds,{maxZoom:13,padding:[55,55]});
  }

  const boardTabByPlace=new Map();
  // "More details" only when there is something more to show.
  const detailFieldsBox=$('detail-fields');
  if(detailFieldsBox){
    const syncTechToggle=()=>$('detail-tech-toggle')?.classList.toggle('hidden',!detailFieldsBox.querySelector('.is-technical'));
    new MutationObserver(syncTechToggle).observe(detailFieldsBox,{childList:true});
    syncTechToggle();
  }

  function railBoardSnapshot(board) {
    const rows=Array.isArray(board.departures)?board.departures:[];
    if(!rows.length)return '';
    let onTime=0,late=0,cancelled=0;
    const destinations=new Set(),operators=new Set();
    for(const row of rows){
      const status=String(row.status||'');
      const isCancelled=Boolean(row.cancelled)||/cancel/i.test(status);
      // Some boards say "On time" without a minutes figure.
      const delay=row.delay_minutes!=null&&row.delay_minutes!==''?Number(row.delay_minutes):(/^on time$/i.test(status)?0:NaN);
      if(isCancelled)cancelled+=1;
      else if(Number.isFinite(delay)){if(delay>5)late+=1;else onTime+=1;}
      if(row.destination)destinations.add(humanTransitLabel(row.destination));
      if(row.operator_name||row.operator)operators.add(humanTransitLabel(row.operator_name||row.operator));
    }
    const known=onTime+late+cancelled;
    // One plain sentence, not a grid of jargon; a bar only with enough trains.
    let headline, tone='';
    if(known<3) headline=`Next ${rows.length} departure${rows.length===1?'':'s'}`;
    else if(!late&&!cancelled){headline=known===rows.length?`All ${rows.length} next trains on time`:`${onTime} of ${known} trains reporting are on time`;tone='good';}
    else {headline=[late?`${late} of ${known} running late`:null,cancelled?`${cancelled} cancelled`:null].filter(Boolean).join(' · ');tone=cancelled||late*2>known?'bad':'late';}
    const segments=known>=3?[
      ['good',onTime,'On time or within 5 minutes'],['late',late,'More than 5 minutes late'],['bad',cancelled,'Cancelled'],
    ].filter(([,count])=>count).map(([t,count,label])=>`<i class="${t}" style="width:${(count*100/known).toFixed(2)}%" title="${esc(label)}: ${count}"></i>`).join(''):'';
    const summary=`${onTime} on time or within 5 minutes, ${late} late, ${cancelled} cancelled`;
    const destinationList=[...destinations].slice(0,6);
    return `<section class="station-live-snapshot station-summary" aria-label="How trains are running"><p class="station-headline${tone?` is-${tone}`:''}">${esc(headline)}</p>${segments?`<div class="station-status-chart" role="img" aria-label="${esc(summary)}">${segments}</div>`:''}${destinationList.length?`<p class="station-destinations"><b>Direct to</b>${destinationList.map(value=>`<span>${esc(value)}</span>`).join('')}${destinations.size>destinationList.length?`<em>+${destinations.size-destinationList.length}</em>`:''}</p>`:''}</section>`;
  }

  // Engineering works: days in the next week when buses replace trains here.
  // Official disruption messages for a station now (National Rail via the
  // Rail Data Marketplace), already plain text from the server.
  function stationNoticesHtml(notices){
    const rows=(Array.isArray(notices)?notices:[]).filter(n=>n&&n.text);
    if(!rows.length)return '';
    return `<section class="station-notices" aria-label="Disruption at this station"><strong>Disruption now</strong><ul>${rows.map(n=>`<li class="notice-${esc(String(n.severity||'').toLowerCase())}"><b>${esc(n.category||'')}</b><span>${esc(n.text)}</span>${n.link&&/^https:\/\//.test(n.link)?` <a href="${esc(n.link)}" target="_blank" rel="noopener">More</a>`:''}</li>`).join('')}</ul></section>`;
  }
  function engineeringNoticeHtml(days,stops){
    const rows=(Array.isArray(days)?days:[]).slice(0,3);
    if(!rows.length)return '';
    // Where the buses leave from (Rail Data Marketplace), each place once.
    const seen=new Set();
    const places=(Array.isArray(stops)?stops:[]).filter(stop=>stop?.description&&!seen.has(stop.description)&&seen.add(stop.description)).slice(0,3);
    const where=places.length?`<div class="engineering-stops"><b>Buses leave from</b>${places.map(stop=>`<p>${esc(stop.description)} <button type="button" class="link-button" data-replacement-stop="${Number(stop.lat)},${Number(stop.lon)}">Show on map</button></p>`).join('')}</div>`:'';
    const today=new Date(Date.now());today.setHours(12,0,0,0);
    const label=day=>{
      const d=new Date(`${day}T12:00:00`);const diff=Math.round((d-today)/86400000);
      return diff===0?'Today':diff===1?'Tomorrow':d.toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short'});
    };
    const items=rows.map(row=>`<li><b>${esc(label(row.day))}</b> ${esc(row.buses)} replacement bus${row.buses===1?'':'es'}${row.first&&row.last?` · ${esc(row.first)}–${esc(row.last)}`:''}${row.between?.length?` · ${esc(row.between.slice(0,3).map(humanTransitLabel).join(', '))}`:''}</li>`).join('');
    return `<section class="engineering-notice" aria-label="Engineering works"><strong>Buses replace some trains</strong><ul>${items}</ul>${where}<small>Engineering works from the published timetable. Check before you travel.</small></section>`;
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-replacement-stop]');
    if(!button)return;
    const [lat,lon]=button.dataset.replacementStop.split(',').map(Number);
    if(Number.isFinite(lat)&&Number.isFinite(lon)){map.setView([lat,lon],18);collapseWorkspaceOnPhone?.();}
  });

  // Save and the station page, side by side under a station board.
  function stationActions(place){
    if(!place||!/^[A-Z]{3}$/.test(String(place.crs||'')))return '';
    const saved=savedPlaces.has(place.id);
    return `<div class="station-actions"><button type="button" class="station-save${saved?' is-saved':''}" data-station-save>${saved?'Saved ✓':'Save station'}</button><a class="station-page-button" href="/station/s-${esc(String(place.crs).toLowerCase())}">Station page →</a></div>`;
  }

  function renderPlaceBoard(place, payload) {
    const box=$('detail-placeboard'); if(!box)return;
    box.classList.remove('rail-departure-board','bus-departure-board');
    const board=payload.board||{}; let html='';
    if(place.place_type==='rail_station') {
      box.classList.add('rail-departure-board');
      const deps=(board.departures||[]).map(r=>boardRow(r,'departure')).join('');
      const arrivalRows=(board.arrivals&&board.arrivals.length ? board.arrivals : (board.recent_arrivals||[]));
      const arr=arrivalRows.map(r=>boardRow(r,'arrival')).join('');
      const arrivalLabel=(board.arrivals&&board.arrivals.length)?'Arrivals':'Recent arrivals';
      const waiting=board.mode==='schedule_sync'?'<p class="board-empty board-waiting">Timetable loading…</p>':'';
      const freightRows=Array.isArray(board.freight)?board.freight:[];
      const freight=freightRows.map(r=>boardRow(r,'freight')).join('');
      let tab=boardTabByPlace.get(place.id)||'departures';
      if(tab==='freight'&&!freight)tab='departures';
      const freightTab=freight?`<button type="button" role="tab" data-board-tab="freight" aria-selected="${tab==='freight'}">Freight <small>${freightRows.length}</small></button>`:'';
      const freightList=freight?`<div class="board-list" data-board-list="freight"${tab==='freight'?'':' hidden'}><p class="board-note">Freight and engineering trains due in the next two hours.</p>${freight}</div>`:'';
      html=`${waiting}${stationNoticesHtml(board.station_notices)}${engineeringNoticeHtml(board.engineering,board.replacement_stops)}<div class="board-switch${freight?' has-three':''}" role="tablist" aria-label="Departures, arrivals or freight"><button type="button" role="tab" data-board-tab="departures" aria-selected="${tab==='departures'}">Departures</button><button type="button" role="tab" data-board-tab="arrivals" aria-selected="${tab==='arrivals'}">${esc(arrivalLabel)}</button>${freightTab}</div><div class="board-list" data-board-list="departures"${tab==='departures'?'':' hidden'}>${deps||'<p class="board-empty">No departures due.</p>'}</div><div class="board-list" data-board-list="arrivals"${tab==='arrivals'?'':' hidden'}>${arr||'<p class="board-empty">No arrivals due.</p>'}</div>${freightList}${railBoardSnapshot(board)}${stationActions(place)}`;
    } else if(place.place_type==='bus_stop') {
      box.classList.add('bus-departure-board');
      const rows=(board.rows||[]).map(r=>boardRow(r,'bus')).join('');
      const targeted=(board.targeted_live||[]).map(r=>boardRow(r,'bus')).join('');
      let mode='Bus stop';
      if(board.mode==='live_predictions') mode='Live predicted buses';
      else if(board.mode==='published_timetable') mode='Next published buses';
      else if(board.mode==='live_next_stop') mode='Live bus targeting this stop';
      else if(board.mode==='timetable_sync') mode='Loading timetable…';
      else mode='No due services found';
      const targetBlock=targeted?`<div class="board-secondary"><p class="board-heading">Also approaching</p>${targeted}</div>`:'';
      html=`<p class="board-heading">${esc(mode)}</p>${rows||'<p class="board-empty">No buses due.</p>'}${targetBlock}`;
    }
    box.classList.remove('hidden');
    const nextHtml=html||'<p class="board-empty">Nothing due right now.</p>';
    box.querySelector('[data-board-refresh-error]')?.remove();
    if(box.innerHTML!==nextHtml){
      const focused=document.activeElement?.closest('.board-row');
      const focusKey=focused&&box.contains(focused)?[focused.dataset.trainUid,focused.dataset.liveId,focused.dataset.headcode,focused.dataset.serviceDate,focused.className].join('|'):null;
      const panel=$('detail-panel'),scrollTop=panel?.scrollTop || 0;
      box.innerHTML=nextHtml;bindBoardLiveLinks(box);
      box.querySelector('[data-station-save]')?.addEventListener('click',event=>{
        $('save-place')?.click();
        const saved=savedPlaces.has(place.id);
        event.currentTarget.textContent=saved?'Saved ✓':'Save station';
        event.currentTarget.classList.toggle('is-saved',saved);
      });
      box.querySelectorAll('[data-board-tab]').forEach(button=>button.addEventListener('click',()=>{
        const tab=button.dataset.boardTab;
        boardTabByPlace.set(place.id,tab);
        box.querySelectorAll('[data-board-tab]').forEach(other=>other.setAttribute('aria-selected',String(other===button)));
        box.querySelectorAll('[data-board-list]').forEach(list=>{list.hidden=list.dataset.boardList!==tab;});
      }));
      if(focusKey){const replacement=[...box.querySelectorAll('.board-row')].find(row=>[row.dataset.trainUid,row.dataset.liveId,row.dataset.headcode,row.dataset.serviceDate,row.className].join('|')===focusKey);replacement?.focus({preventScroll:true});}
      if(panel)panel.scrollTop=scrollTop;
    }
    focusNextLive(place,board.next_live_id);
  }

  async function refreshPlaceBoard(place) {
    if(selectedPlaceId!==place.id)return;
    clearTimeout(placeBoardRefreshTimer);
    if(document.hidden){placeBoardRefreshTimer=setTimeout(()=>refreshPlaceBoard(place),10000);return;}
    const requestId=++placeBoardRequest;
    placeBoardController?.abort();
    const controller=new AbortController();placeBoardController=controller;
    const boardTimeout=setTimeout(()=>controller.abort(),12000);
    let refreshDelay=place.place_type==='rail_station'?10000:20000;
    try {
      const response=await fetch(`/api/places/${encodeURIComponent(place.id)}/board?limit=10`,{cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error('board unavailable');
      const payload=await response.json();
      if(requestId!==placeBoardRequest || selectedPlaceId!==place.id)return;
      placeBoardCache.set(place.id,{at:Date.now(),place,payload});
      renderPlaceBoard(place,payload);
      if(['schedule_sync','timetable_sync'].includes(payload.board?.mode))refreshDelay=Math.max(3,Math.min(20,Number(payload.board.retry_seconds)||5))*1000;
    } catch {
      if(requestId!==placeBoardRequest || selectedPlaceId!==place.id)return;
      const box=$('detail-placeboard');
      if(placeBoardCache.has(place.id)){
        if(!box.querySelector('[data-board-refresh-error]'))box.insertAdjacentHTML('beforeend','<p class="board-empty" data-board-refresh-error role="status">Times may be out of date · retrying…</p>');
      } else box.innerHTML='<small>LIVE TIMES</small><p class="board-empty" role="status">Times unavailable right now · retrying…</p>';
    } finally {
      clearTimeout(boardTimeout);
      if(requestId===placeBoardRequest){
        placeBoardController=null;
        if(selectedPlaceId===place.id)placeBoardRefreshTimer=setTimeout(()=>refreshPlaceBoard(place),refreshDelay);
      }
    }
  }

  async function selectPlace(id, pan) {
    const p=places.find(x=>x.id===id) || placeBoardCache.get(id)?.place; if(!p)return;
    clearPlaceBoard();
    selectedPlaceId=id; selectedId=null; trackedActivityId=null; selectedDetailSignature=''; sendWatching(null); clearPlaceLink(); clearOperatorIdentity(); clearActivityIntelligence();
    syncDetailRouteControl(null);
    returnPlaceId=null; detailBackButton.classList.add('hidden');
    $('detail-panel')?.classList.add('has-selection');
    $('detail-sidebar')?.classList.remove('is-empty');
    $('detail-title').textContent=humanTransitLabel(p.name);
    $('detail-subtitle').textContent=p.subtitle || ({rail_station:'Rail station',bus_stop:'Bus stop',rain_gauge:'Rain gauge'}[p.place_type]||'Place');
    $('detail-fields').innerHTML=[field('Type',(p.place_type||'').replaceAll('_',' ')),field('Code',p.crs||p.atco_code||p.naptan_code),field('Operator',p.operator),field('Source',p.source),field('Latitude',Number(p.lat).toFixed(5)),field('Longitude',Number(p.lon).toFixed(5))].join('');
    $('follow').classList.add('hidden'); $('track-activity')?.classList.add('hidden'); $('share-activity')?.classList.add('hidden');
    const savePlace=$('save-place'); if(savePlace){savePlace.classList.remove('hidden');savePlace.textContent=savedPlaces.has(p.id)?'Saved ✓':'Save place';}
    clearCoverage(); clearDiscoveryDetail();
    for(const id2 of ['detail-insight','detail-photo','detail-related']){const el=$(id2);if(el){el.classList.add('hidden');el.innerHTML='';}}
    const box=$('detail-placeboard'); box.classList.remove('hidden');
    const cached=placeBoardCache.get(id);
    // A link to the station's page: passenger numbers, lines and history.
    if(p.place_type==='rail_station'&&/^[A-Z]{3}$/.test(String(p.crs||''))){
      // Save and the station page sit in one row under the board instead.
      $('detail-fields').innerHTML='';
      $('save-place')?.classList.add('hidden');
    }
    if(cached) renderPlaceBoard(p,cached.payload);
    else box.innerHTML='<small>LIVE TIMES</small><p class="coverage-loading">Loading…</p>';
    if(pan) map.panTo([p.lat,p.lon],{animate:true});
    await refreshPlaceBoard(p);
  }

  async function loadEnvironmentContext(a) {
    if(a.type!=='tide' || !String(a.source_id||'').startsWith('https://environment.data.gov.uk/flood-monitoring/')) { clearPlaceBoard(); return; }
    const box=$('detail-placeboard'); box.classList.remove('hidden'); box.innerHTML='<small>LIVE GAUGE CONTEXT</small><p class="coverage-loading">Loading latest readings…</p>';
    try {
      const r=await fetch(`/api/environment/measure?url=${encodeURIComponent(a.source_id)}`,{cache:'no-store'}); if(!r.ok)throw new Error('readings unavailable'); const d=await r.json();
      if(selectedId!==a.id)return;
      const rows=(d.rows||[]).slice(0,5).map(x=>`<div class="reading-row"><span>${esc(timeText(x.dateTime)||'Reading')}</span><strong>${esc(x.value==null?'—':x.value)}</strong></div>`).join('');
      box.innerHTML=`<small>LIVE GAUGE CONTEXT</small><strong class="board-heading">Trend: ${esc(d.trend||'unknown')}</strong>${rows}<p class="resource-note">Latest Environment Agency readings only; this app does not store a historical timeline.</p>`;
    } catch { if(selectedId===a.id) box.innerHTML='<small>LIVE GAUGE CONTEXT</small><p class="board-empty">Latest gauge readings unavailable.</p>'; }
  }

  function searchable(a) {
    const cached = searchableCache.get(a);
    if (cached !== undefined) return cached;
    const value = [a.name, a.subtitle, a.operator, a.route, a.origin, a.destination, a.status, a.headcode,
      a.train_uid, a.vehicle_ref, a.journey_ref, a.next_location, a.category, a.road, a.direction,
      a.reason, a.source].filter(Boolean).join(' ').toLowerCase();
    searchableCache.set(a, value);
    return value;
  }

  function inNearby(a) {
    if (!nearbyState.active || nearbyState.lat == null || nearbyState.lon == null) return true;
    return distanceKm(nearbyState.lat, nearbyState.lon, Number(a.lat), Number(a.lon)) <= nearbyState.radiusMiles * 1.609344;
  }

  function isRescueContextActivity(a) {
    const category = String(a?.cool_category || '').toLowerCase();
    const subtype = String(a?.cool_subtype || '').toLowerCase();
    const theme = String(a?.cool_theme || '').toLowerCase();
    const badge = String(a?.cool_badge || '').toLowerCase();
    return category === 'rescue'
      || theme === 'rescue'
      || ['sar','rescue'].includes(a?.type)
      || /(?:rescue|lifeboat|coastguard|ambulance|hems)/i.test(`${subtype} ${badge}`);
  }

  function aircraftIsMilitary(a) {
    const classified=coolInfo(a);
    return a.type==='aircraft'&&classified.candidate&&classified.theme==='air'&&classified.subtype!=='rare-airliner';
  }
  function matchesAircraftFilter(a) {
    if(!aircraftFilter||!['aircraft','sar'].includes(a?.type))return true;
    if(aircraftFilter==='military')return aircraftIsMilitary(a);
    if(aircraftFilter==='helicopters')return aircraftMapShape(a).rotorcraft;
    if(aircraftFilter==='big-rare')return coolInfo(a).subtype==='rare-airliner';
    if(aircraftFilter==='low'){
      const alt=aircraftNumber(a.altitude_ft);
      return /on ground/i.test(String(a.status||''))||(Number.isFinite(alt)&&alt<10000);
    }
    return true;
  }

  function matchesMapFilters(a) {
    if(!matchesAircraftFilter(a))return false;
    if(a?.type==='train'){
      if(trainFilter==='passenger'&&isFreightTrain(a))return false;
      if(trainFilter==='freight'&&!isFreightTrain(a))return false;
      if(trainFilter==='cancelled'&&!a.cancelled_at)return false;
    }
    // Coaches arrive named for their operator ("National Express 420"), buses as "Bus 7".
    if(a?.type==='bus'&&busFilter==='coaches'&&/^bus\b/i.test(String(a.name||'')))return false;
    if(['road','incident'].includes(a?.type)&&roadFilter==='now'){
      const impact=a.impact||{};
      if(!(impact.active&&(impact.rank||0)>=2))return false;
    }
    return true;
  }

  function matchesAircraftLandingView(a) {
    if(!aircraftLandingView)return true;
    if(!['aircraft','sar'].includes(a?.type))return false;
    const text=searchable(a).toUpperCase();
    const shape=aircraftMapShape(a).kind;
    const helicopter=['helicopter','chinook','apache'].includes(shape);
    if(aircraftLandingView==='helicopters')return helicopter;
    if(aircraftLandingView==='police-helicopters')return helicopter&&/(?:\bNPAS\b|POLICE|POLICE AIR|UKP\d|G-POL)/.test(text);
    if(aircraftLandingView==='air-ambulances')return helicopter&&/(?:HELIMED|AIR ?AMB|AMBULANCE|\bHEMS\b|MEDIC)/.test(text);
    if(aircraftLandingView==='military-aircraft')return aircraftIsMilitary(a);
    return true;
  }

  function layerTypeMatch(a, type) {
    if (type === 'cool-off') return false;
    if (type === 'all') return true;
    if (type === 'useful') {
      const enabled=enabledActivityTypes.has(a.type)
        || (aircraftLandingView&&['aircraft','sar'].includes(a.type));
      return enabled&&matchesAircraftLandingView(a);
    }
    if (type === 'transport') return ['train','bus'].includes(a.type);
    if (type === 'cool') return a.type !== 'vessel' && coolInfo(a).score >= 45;
    if (type === 'cool-air') return a.type === 'aircraft' && !isRescueContextActivity(a) && (overheadActive || coolInfo(a).score >= 45);
    if (type === 'nearby-aircraft') return overheadActive && nearbyAircraftIds.has(a.id);
    if (type === 'cool-rail') return railSubtypeMatch(a) && coolInfo(a).score >= 45;
    if (type === 'cool-rescue') return isRescueContextActivity(a) && coolInfo(a).score >= 45;
    // Planned roadworks and unplanned incidents remain deliberately separate.
    return a.type === type;
  }

  // Some transponders on the ground send a wildly wrong ground speed. Live at
  // Birmingham, EZY682G taxied at 13-17 kt (its positions show it) while
  // reporting 122 kt, which read as "0ft 122kt" and then "Taking off". A plane
  // with no altitude, or on the ground, that reports a runway speed it could
  // not have reached in the distance it actually moved since a report 5-120 s
  // earlier gets the speed it moved at. 3 m/s² is a hard take-off push.
  const MAX_GROUND_ACCEL_MS2=3;
  function checkAircraftGroundSpeed(a) {
    if(!['aircraft','sar'].includes(a?.type)||a._speedCheckedAt===a.last_updated)return;
    const reported=aircraftSpeedKt(a);
    if(!Number.isFinite(reported)||reported<40)return;
    if(Number.isFinite(aircraftNumber(a.altitude_ft))&&!/on ground/i.test(String(a.status||'')))return;
    const at=Date.parse(a.last_updated||''),lat=Number(a.lat),lon=Number(a.lon);
    if(!Number.isFinite(at)||!Number.isFinite(lat)||!Number.isFinite(lon))return;
    const points=movementHistory.points(a.id);
    let earlier=null;
    for(let i=points.length-1;i>=0;i-=1){
      const gap=(at-points[i].at)/1000;
      if(gap>=5){earlier=gap<=120?points[i]:null;break;}
    }
    if(!earlier)return;   // nothing to check against yet: see aircraftGroundPhase
    a._speedCheckedAt=a.last_updated;
    const seconds=(at-earlier.at)/1000,metres=distanceKm(earlier.lat,earlier.lon,lat,lon)*1000;
    const v=reported*0.514444;
    const needed=seconds>=v/MAX_GROUND_ACCEL_MS2?v*v/(2*MAX_GROUND_ACCEL_MS2):v*seconds-MAX_GROUND_ACCEL_MS2*seconds*seconds/2;
    if(metres>=needed*0.8){a.speed_verified=true;return;}
    a.speed_reported=a.speed;
    a.speed=Math.round(metres/seconds/0.514444*10)/10;
    a.speed_unit='kt';
  }

  // Parked planes and airport transmitters (tower, ground, service vehicles)
  // only clutter the map and the airport lists: leave them out.
  const AIRPORT_TRANSMITTERS=new Set(['TWR','GND','SVC']);
  function aircraftNotWorthShowing(a) {
    if(!['aircraft','sar'].includes(a?.type))return false;
    if(AIRPORT_TRANSMITTERS.has(String(a.aircraft_type_code||'').toUpperCase()))return true;
    if(/airport service vehicle|emergency vehicle|obstruction/i.test(String(a.aircraft_category||'')))return true;
    return aircraftGroundPhase(a)?.short==='Parked';
  }

  function replaceActivities(nextActivities) {
    activities = Array.isArray(nextActivities) ? nextActivities : [];
    for (const activity of activities) checkAircraftGroundSpeed(activity);
    movementHistory.ingest(activities);
    activitiesById = new Map(activities.map(activity => [activity.id, activity]));
    if(selectedId&&activitiesById.has(selectedId))selectedActivitySnapshot=activitiesById.get(selectedId);
    activityRevision += 1;
    filteredCacheKey = '';
    updateOperatorFilterOptions();
    if (!$('aircraft-drawer')?.hidden) fillDrawerOperators();
  }

  function activityById(id) {
    return activitiesById.get(id) || globalCoolById.get(id) || (id===selectedId?selectedActivitySnapshot:null);
  }

  function globalCoolRows() {
    return globalCoolActivities.length ? globalCoolActivities : activities;
  }

  async function loadGlobalCool(force=false) {
    if (currentWorkspace !== 'cool' && !force) return;
    if (!force && globalCoolActivities.length && Date.now() - globalCoolLoadedAt < 8000) return;
    const requestId = ++globalCoolRequest;
    try {
      const response = await fetch('/api/cool?limit=40', {cache:'no-store'});
      if (!response.ok) throw new Error('cool feed unavailable');
      const payload = await response.json();
      if (requestId !== globalCoolRequest) return;
      const rows = Array.isArray(payload.activities) ? payload.activities : [];
      const availableCounts=payload.available_counts&&typeof payload.available_counts==='object'?payload.available_counts:{};
      globalCoolAvailability={fullAccess:Boolean(payload.full_access),total:Number(payload.available_count ?? rows.length)||0,counts:{air:Number(availableCounts.air)||0,rail:Number(availableCounts.rail)||0,rescue:Number(availableCounts.rescue)||0}};
      // Keep a visitor's Discover set stable while they pan and zoom. Refresh
      // the live data for the same finds, but do not silently swap the set as
      // viewport messages or ranking ties change underneath them.
      if (globalCoolActivities.length) {
        const fresh = new Map(rows.map(activity => [activity.id, activity]));
        const retained=globalCoolActivities.map(activity=>fresh.get(activity.id)).filter(Boolean);
        const retainedIds=new Set(retained.map(activity=>activity.id));
        globalCoolActivities=[...retained,...rows.filter(activity=>!retainedIds.has(activity.id))].slice(0,rows.length);
      } else {
        globalCoolActivities = rows;
      }
      globalCoolById = new Map(globalCoolActivities.map(activity => [activity.id, activity]));
      globalCoolRevision += 1;
      filteredCacheKey = '';
      globalCoolLoadedAt = Date.now();
      if (currentWorkspace === 'cool') {
        renderMap();
        renderStats();
        renderRanking();
        renderCoolSummary();
        renderLiveTicker();
        renderDiscoveryExperience();
      }
    } catch {
      // The viewport snapshot remains a complete fallback; an optional global
      // discovery request must never make the live map fail.
    } finally {
      clearTimeout(globalCoolRefreshTimer);
      if (currentWorkspace === 'cool') globalCoolRefreshTimer=setTimeout(()=>loadGlobalCool(true),60000);
    }
  }

  function activityPassesFilters(a) {
    if (!a || aircraftNotWorthShowing(a)) return false;
    // Never leave restored/aged bus rows looking live behind an outage
    // warning. They return automatically with the next healthy BODS snapshot.
    if (a.type === 'bus' && latestSources.find(source => source.name === 'bods')?.mode === 'error') return false;
    if (!matchesMapFilters(a)) return false;
    if (!layerTypeMatch(a, selectedType)) return false;
    if (!operatorMatches(a)) return false;
    if (!inNearby(a)) return false;
    if (lateTrainsOnly && a.type === 'train' && !(a.cancelled_at || Number(a.delay_minutes) >= 5)) return false;
    return !searchText || searchable(a).includes(searchText);
  }

  function filteredActivities() {
    const nearbyKey = nearbyState.active
      ? `${nearbyState.lat}:${nearbyState.lon}:${nearbyState.radiusMiles}`
      : 'off';
    const cacheKey = [
      activityRevision,
      globalCoolRevision,
      selectedType,
      selectedRailSubtype,
      searchText,
      nearbyKey,
      selectedOperator,
      aircraftLandingView,
      aircraftFilter,
      trainFilter, busFilter, roadFilter,
      latestSources.find(source => source.name === 'bods')?.mode || '',
      [...enabledActivityTypes].sort().join(','),
      lateTrainsOnly,
    ].join('|');
    if (cacheKey === filteredCacheKey) return filteredCacheRows;
    const source=currentWorkspace==='cool'&&globalCoolActivities.length ? globalCoolRows() : activities;
    filteredCacheRows = source.filter(activityPassesFilters);
    filteredCacheKey = cacheKey;
    return filteredCacheRows;
  }

  // To about a centimetre: at 60 frames a second a taxiing plane moves less
  // than 10 cm a frame, and coarser rounding froze it for frames at a time.
  function markerPositionSignature(a) {
    return `${Number(a.lat).toFixed(7)}|${Number(a.lon).toFixed(7)}`;
  }

  function trainCoincidentLayouts(rows) {
    const layouts=new Map();
    let groupCount=0,separatedCount=0;
    if(map.getZoom()>=10){
      const groups=new Map();
      for(const row of rows){
        if(row?.type!=='train'||!Number.isFinite(Number(row.lat))||!Number.isFinite(Number(row.lon)))continue;
        // Railway reporting points are shared coordinates. Five decimal places
        // keeps genuinely separate trains separate while grouping the same
        // station/berth point to roughly one metre.
        const key=`${Number(row.lat).toFixed(5)}|${Number(row.lon).toFixed(5)}`;
        if(!groups.has(key))groups.set(key,[]);
        groups.get(key).push(row);
      }
      for(const group of groups.values()){
        if(group.length<2)continue;
        groupCount+=1;separatedCount+=group.length;
        const ordered=[...group].sort((a,b)=>String(a.id).localeCompare(String(b.id)));
        const count=ordered.length;
        // Keep at least one full marker-width between neighbouring trains.
        // The former 14px radius made platformed trains overlap precisely
        // when the user most needed to distinguish them.
        const radius=count===2?22:Math.min(52,Math.max(26,20/Math.sin(Math.PI/count)));
        const start=count===2?0:-Math.PI/2;
        ordered.forEach((row,index)=>{
          const angle=start+(Math.PI*2*index/count);
          layouts.set(row.id,{
            count,slot:index,
            x:Math.round(Math.cos(angle)*radius),
            y:Math.round(Math.sin(angle)*radius),
          });
        });
      }
    }
    $('map').dataset.coincidentTrainGroups=String(groupCount);
    $('map').dataset.separatedTrainMarkers=String(separatedCount);
    return layouts;
  }

  function withTrainCoincidentLayout(activity,layout) {
    if(activity?.type!=='train')return activity;
    return {...activity,
      _coincidentCount:layout?.count||1,
      _coincidentSlot:layout?.slot||0,
      _coincidentX:layout?.x||0,
      _coincidentY:layout?.y||0,
    };
  }

  const reduceLiveMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');

  function pathSample(points, fraction) {
    if(window.UKActivityMotionEngine)return window.UKActivityMotionEngine.samplePath(points,fraction);
    const usable=(Array.isArray(points)?points:[])
      .map(point=>[Number(point?.[0]),Number(point?.[1])])
      .filter(point=>Number.isFinite(point[0])&&Number.isFinite(point[1]));
    if(usable.length<2)return null;
    const legs=[];let total=0;
    for(let i=1;i<usable.length;i++){
      const length=distanceKm(usable[i-1][0],usable[i-1][1],usable[i][0],usable[i][1]);
      if(!Number.isFinite(length)||length<=0)continue;
      legs.push({from:usable[i-1],to:usable[i],length,start:total});total+=length;
    }
    if(!legs.length)return null;
    const target=Math.max(0,Math.min(1,fraction))*total;
    const leg=legs.find(row=>target<=row.start+row.length)||legs[legs.length-1];
    const local=Math.max(0,Math.min(1,(target-leg.start)/leg.length));
    const lat=leg.from[0]+(leg.to[0]-leg.from[0])*local;
    const lon=leg.from[1]+(leg.to[1]-leg.from[1])*local;
    const lat1=leg.from[0]*Math.PI/180,lat2=leg.to[0]*Math.PI/180;
    const deltaLon=(leg.to[1]-leg.from[1])*Math.PI/180;
    const y=Math.sin(deltaLon)*Math.cos(lat2);
    const x=Math.cos(lat1)*Math.sin(lat2)-Math.sin(lat1)*Math.cos(lat2)*Math.cos(deltaLon);
    const bearing=(Math.atan2(y,x)*180/Math.PI+360)%360;
    const progress=[usable[0]];
    for(const row of legs){
      if(row.start+row.length>=target)break;
      progress.push(row.to);
    }
    const last=progress[progress.length-1];
    if(!last||Math.abs(last[0]-lat)>1e-8||Math.abs(last[1]-lon)>1e-8)progress.push([lat,lon]);
    return {lat,lon,bearing,total,progress,points:usable};
  }

  function pathPosition(points, fraction) {
    const sample=pathSample(points,fraction);
    return sample ? {lat:sample.lat,lon:sample.lon} : null;
  }

  function trainEstimatePath(a) {
    // Reporting points are only anchors, not a drawable railway. If the
    // backend cannot prove a physical path, hold the confirmed marker rather
    // than exposing a straight off-track live-leg guide.
    const supplied=Array.isArray(a?.estimate_path)&&a.estimate_path.length>=2?a.estimate_path:[];
    return supplied.map(point=>[Number(point?.[0]),Number(point?.[1])])
      .filter(point=>Number.isFinite(point[0])&&Number.isFinite(point[1]));
  }

  function trainEstimateFraction(a, now=Date.now()) {
    const started=Date.parse(a?.estimate_started_at||''),due=Date.parse(a?.estimate_due_at||'');
    if(!Number.isFinite(started)||!Number.isFinite(due)||due<=started)return 0;
    const serverNow=now+(Number(window.UKActivityMotionEngine?.clockOffset?.())||0);
    const shaped=window.UKActivityMotionEngine?.legFraction?.((serverNow-started)/1000,(due-started)/1000,a.estimate_accel_s,a.estimate_brake_s);
    return Math.max(0,Math.min(1,Number.isFinite(shaped)?shaped:(serverNow-started)/(due-started)));
  }

  function projectPosition(lat,lon,bearing,distance) {
    const radius=6371,angular=distance/radius,heading=bearing*Math.PI/180;
    const p1=lat*Math.PI/180,l1=lon*Math.PI/180;
    const p2=Math.asin(Math.sin(p1)*Math.cos(angular)+Math.cos(p1)*Math.sin(angular)*Math.cos(heading));
    const l2=l1+Math.atan2(Math.sin(heading)*Math.sin(angular)*Math.cos(p1),Math.cos(angular)-Math.sin(p1)*Math.sin(p2));
    return {lat:p2*180/Math.PI,lon:((l2*180/Math.PI+540)%360)-180};
  }

  function activityMapPresentation(a, now=Date.now()) {
    if(window.UKActivityMotionEngine)return window.UKActivityMotionEngine.present(a,now,Boolean(reduceLiveMotion?.matches));
    if(!a)return a;
    if(a.type==='train'&&a.reported_lat!=null&&a.reported_lon!=null&&a.next_lat!=null&&a.next_lon!=null){
      const started=Date.parse(a.estimate_started_at||''),due=Date.parse(a.estimate_due_at||'');
      if(Number.isFinite(started)&&Number.isFinite(due)&&due>started&&now>=started){
        const fraction=Math.max(0,Math.min(1,(now-started)/(due-started)));
        const path=Array.isArray(a.estimate_path)&&a.estimate_path.length>=2?a.estimate_path:[];
        const point=path.length>=2?pathPosition(path,fraction):null;
        if(point)return {...a,...point,position_method:'estimated',position_label:`Estimated along the published railway path between confirmed reports • ${a.position_label||''}`};
      }
    }
    if(reduceLiveMotion?.matches)return a;
    if(overheadActive&&['aircraft','sar'].includes(a.type)){
      const updated=Date.parse(a.last_updated||''),speed=Number(a.speed),bearing=Number(a.bearing);
      const age=(now-updated)/1000;
      if(Number.isFinite(updated)&&Number.isFinite(speed)&&Number.isFinite(bearing)&&age>0&&age<=18&&speed>=10&&speed<=800){
        const point=projectPosition(Number(a.lat),Number(a.lon),bearing,speed*1.852*age/3600);
        return {...a,...point,position_method:'estimated',position_label:`Live estimate from the latest ADS-B report • ${a.position_label||''}`};
      }
    }
    return a;
  }

  function punctualityClass(delay) {
    // Green on time, amber a few minutes late, red badly late. No dot when
    // lateness is unknown rather than implying the train is on time.
    if (delay === null || delay === undefined || delay === '' || !Number.isFinite(Number(delay))) return '';
    const minutes = Number(delay);
    return minutes <= 1 ? 'is-on-time' : minutes <= 5 ? 'is-late' : 'is-very-late';
  }

  function markerVisualSignature(a) {
    const zoomBand = map.getZoom() >= 12 ? 2 : map.getZoom() >= 9 ? 1 : 0;
    const appearance = currentWorkspace === 'cool' ? 'cool' : 'standard';
    const cool = appearance === 'cool' ? coolInfo(a) : null;
    // Only what changes the drawing belongs here. Rebuilding a marker replaces
    // its element, which cancels the glide to its next position, so position
    // and aircraft heading (turned in place by syncMarkerVisual) stay out.
    const pointerType = ['bus','vessel','gritter'].includes(a.type);
    const bearing = pointerType && zoomBand >= 1 && Number.isFinite(Number(a.bearing)) ? Math.round(Number(a.bearing)/10)*10 : '';
    const speed = pointerType && zoomBand >= 2 ? compactSpeed(a) : '';
    const speedUnit = '';
    const movementPosition='';
    return [a.type,isFreightTrain(a)?'freight':'',a.position_method,a.name,['bus','train'].includes(a.type)?`${a.route||''}>${a.destination||''}`:'',a.cancelled_at?'cancelled':punctualityClass(a.delay_minutes),a.aircraft_model,a.aircraft_type_code,bearing,movementPosition,speed,speedUnit,zoomBand,appearance,cool?.score,cool?.label,cool?.theme,cool?.icon,a._coincidentCount,a._coincidentSlot,a._coincidentX,a._coincidentY].join('|');
  }

  function markerTooltipSignature(a) {
    const appearance = currentWorkspace === 'cool' ? 'cool' : 'standard';
    return [appearance,a.name,a.type,a.kind,a.origin,a.destination,a.route,a.subtitle,a.position_label,a.speed,a.speed_unit,a.bearing,a.delay_minutes,a.next_location,a.status,a.severity,a.vehicle_ref,a.aircraft_model,a.aircraft_category,a.squawk,a.emergency,a.vertical_rate_fpm,a.indicated_airspeed_kt,a.true_airspeed_kt,a.mach,a.heading,a.selected_altitude_ft,a.selected_heading,(a.navigation_modes||[]).join(','),a.wind_direction,a.wind_speed_kt,a.outside_air_temp_c,a.roll_deg,a.aircraft_route_count,a.aircraft_route_last_day,a.position_source,(a.aircraft_flags||[]).join(','),a.last_updated,a.cool_score,a.cool_badge].join('|');
  }

  function trainMarkerDomMatches(marker,activity) {
    if(activity?.type!=='train')return true;
    const element=marker.getElement?.();
    if(!element)return true;
    const count=Math.max(1,Number(activity._coincidentCount)||1);
    const expectedIndex=Math.max(0,Number(activity._coincidentSlot)||0)+1;
    const badge=element.querySelector('.train-coincident-index');
    if(count>1){
      return element.classList.contains('train-coincident-wrap')
        && badge?.textContent?.trim()===String(expectedIndex);
    }
    return !element.classList.contains('train-coincident-wrap')&&!badge;
  }

  function turnAircraftInPlace(marker,activity) {
    if(!['aircraft','sar'].includes(activity?.type))return;
    const element=marker.getElement?.();
    const span=element?.querySelector?.('.aircraft-silhouette');
    if(!span)return;
    const turn=`rotate(${Math.round(aircraftDisplayBearing(activity))}deg)`;
    if(span.style.transform!==turn)span.style.transform=turn;
    // Altitude, speed and freshness change every report: update the data
    // block in place rather than rebuilding the marker (which stops its glide).
    const data=element.querySelector('.ac-data'),text=aircraftDataLine(activity);
    if(data&&data.textContent!==text)data.textContent=text;
    element.querySelector('.aerial-map-marker')?.classList.toggle('is-stale',aircraftIsStale(activity));
  }

  // Place a moving marker. Markers are re-placed every animation frame (see
  // animateLiveMarkers), so there is no CSS glide to start or cancel. Trains
  // and buses live in a cluster group, which re-files a marker on every move
  // (remove and re-add). From zoom 10 nothing clusters, so frames move the
  // icon alone and the group is told once, when the map zooms back out.
  const CLUSTER_FREE_ZOOM=10;
  function clusterFreeZoom(group){
    const configured=Number(group?.options?.disableClusteringAtZoom);
    return Number.isFinite(configured)?configured:CLUSTER_FREE_ZOOM;
  }
  function moveMarker(marker,lat,lon) {
    placeMarker(marker,lat,lon);
    // Leaflet rounds marker positions to whole pixels, so a slow plane ticked
    // across the screen a pixel at a time. Place it exactly instead.
    // (latLngToLayerPoint rounds too, so project without it.)
    const element=marker.getElement?.();
    if(element&&typeof map.project==='function'&&typeof map.getPixelOrigin==='function'&&L.DomUtil?.setPosition){
      L.DomUtil.setPosition(element,map.project(L.latLng(lat,lon)).subtract(map.getPixelOrigin()));
    }
  }
  // Route lines for buses on screen at street level (zoom 13+), so the
  // motion engine moves each bus along its road. Up to 30 at a time, every
  // 15 s; a bus with no line is asked about again after 5 minutes.
  const busShapeTried=new Map();
  let busShapeFetchAt=0,busShapeLoading=false;
  function refreshBusShapes() {
    const engine=window.UKActivityMotionEngine;
    if(!engine?.setBusShape||busShapeLoading||!enabledActivityTypes.has('bus'))return;
    const now=Date.now();
    if((map.getZoom?.()??0)<13||now-busShapeFetchAt<15000)return;
    const bounds=map.getBounds?.();
    const ids=[];
    for(const [id,marker] of markers){
      if(marker.__activityType!=='bus'||engine.hasBusShape(id))continue;
      if(bounds&&!bounds.contains(marker.getLatLng()))continue;
      const tried=busShapeTried.get(id);
      if(tried&&now-tried<300000)continue;
      ids.push(id);
      if(ids.length>=30)break;
    }
    if(!ids.length)return;
    if(busShapeTried.size>5000)busShapeTried.clear();
    ids.forEach(id=>busShapeTried.set(id,now));
    busShapeFetchAt=now;busShapeLoading=true;
    fetch(`/api/buses/shapes?ids=${encodeURIComponent(ids.join(','))}`).then(r=>r.ok?r.json():null).then(data=>{
      const shapes=Object.entries(data?.shapes||{});
      for(const [id,points] of shapes)engine.setBusShape(id,points);
      if(shapes.length)startLiveMotion();
    }).catch(()=>{}).finally(()=>{busShapeLoading=false;});
  }

  function placeMarker(marker,lat,lon) {
    const group=marker.__activityGroup;
    const clustered=group&&typeof group._moveChild==='function';
    if(clustered&&(map.getZoom?.()??0)>=clusterFreeZoom(group)){
      if(!marker.__treeLatLng)marker.__treeLatLng=marker.getLatLng?.();
      group._ignoreMove=true;
      try{marker.setLatLng([lat,lon]);}finally{group._ignoreMove=false;}
      return;
    }
    if(clustered&&marker.__treeLatLng){
      const from=marker.__treeLatLng;delete marker.__treeLatLng;
      group._ignoreMove=true;
      try{marker.setLatLng([lat,lon]);}finally{group._ignoreMove=false;}
      group._moveChild(marker,from,L.latLng(lat,lon));
      return;
    }
    marker.setLatLng([lat,lon]);
  }
  // Back below each group's clustering threshold: file markers moved frame by
  // frame under their real positions so clusters are right.
  function resyncClusteredMarkers() {
    for(const marker of markers.values()){
      if((map.getZoom?.()??0)>=clusterFreeZoom(marker.__activityGroup))continue;
      if(!marker.__treeLatLng)continue;
      const here=marker.getLatLng();
      moveMarker(marker,here.lat,here.lng);
    }
  }

  // Trains, buses and aircraft are re-placed about 30 times a second (15 when
  // there are very many), only those on screen, and only when they moved.
  // This replaces a once-a-second step plus a one-second CSS slide.
  const LIVE_MOTION_TYPES=new Set(['train','bus','aircraft','sar']);
  // The loop stops when nothing moved for about a third of a second (or the
  // tab is hidden), so an idle map costs no battery; the once-a-second tick
  // starts it again.
  let liveMotionLast=0,liveMotionLayouts=new Map(),liveMotionZooming=false,liveMotionRun=0,liveMotionIdle=0;
  function startLiveMotion() {
    if(liveMotionRun||document.hidden||reduceLiveMotion?.matches)return;
    const run=liveMotionRun=performance.now()||1;liveMotionIdle=0;
    requestAnimationFrame(timestamp=>animateLiveMarkers(timestamp,run));
  }
  function animateLiveMarkers(timestamp,run) {
    if(run!==liveMotionRun)return;   // a stopped (or replaced) run
    if(document.hidden||reduceLiveMotion?.matches){liveMotionRun=0;return;}
    requestAnimationFrame(next=>animateLiveMarkers(next,run));
    if(liveMotionZooming)return;
    // Every frame for a normal map; fewer when there are very many markers.
    if(timestamp-liveMotionLast<(markers.size>800?60:markers.size>250?30:0))return;
    liveMotionLast=timestamp;
    let moved=0;
    const now=Date.now();
    const bounds=typeof map.getBounds==='function'?map.getBounds()?.pad?.(0.2):null;
    for(const [id,marker] of markers){
      if(!LIVE_MOTION_TYPES.has(marker.__activityType))continue;
      // Clustered trains and buses (zoomed out) keep the once-a-second step.
      if(typeof marker.__activityGroup?._moveChild==='function'
          && (map.getZoom?.()??0)<clusterFreeZoom(marker.__activityGroup))continue;
      const here=marker.getLatLng?.();
      if(bounds&&here&&!bounds.contains(here))continue;
      const a=activityById(id);
      if(!a)continue;
      const display=withTrainCoincidentLayout(activityMapPresentation(a,now),liveMotionLayouts.get(id));
      const signature=markerPositionSignature(display);
      if(signature===marker.__positionSignature)continue;
      moveMarker(marker,display.lat,display.lon);
      marker.__positionSignature=signature;
      diagnostics.markerMoves+=1;
      moved+=1;
    }
    liveMotionIdle=moved?0:liveMotionIdle+1;
    if(liveMotionIdle>10)liveMotionRun=0;
  }

  function syncMarkerVisual(marker,activity,visualSignature,force=false) {
    if(!force&&marker.__visualSignature===visualSignature&&trainMarkerDomMatches(marker,activity)){turnAircraftInPlace(marker,activity);return false;}
    marker.setIcon(markerIcon(activity));
    marker.__visualSignature=visualSignature;
    diagnostics.markerVisualUpdates+=1;
    return true;
  }

  function repairCoincidentTrainMarkerDom(displayActivities) {
    for(const activity of displayActivities){
      if(activity?.type!=='train')continue;
      const marker=markers.get(activity.id);
      if(!marker||trainMarkerDomMatches(marker,activity))continue;
      syncMarkerVisual(marker,activity,markerVisualSignature(activity),true);
    }
  }

  // Airports with live pages, shown while the Aircraft layer is on.
  // ?v=2: the list now carries each airport's elevation, and the old one is
  // cached in browsers for a day.
  const AIRPORTS_URL='/api/airports?v=2';
  const airportLayer={markers:null,loading:false,rows:[]};
  async function syncAirports(){
    const wanted=enabledActivityTypes.has('aircraft');
    if(!wanted){if(airportLayer.markers)map.removeLayer(airportLayer.markers);return;}
    if(airportLayer.markers){if(!map.hasLayer(airportLayer.markers))airportLayer.markers.addTo(map);return;}
    if(airportLayer.loading)return;
    airportLayer.loading=true;
    try{
      const rows=await fetch(AIRPORTS_URL).then(r=>r.ok?r.json():[]);
      airportLayer.rows=Array.isArray(rows)?rows:[];
      airportLayer.markers=L.layerGroup(airportLayer.rows.map(a=>{
        const marker=L.marker([a.lat,a.lon],{icon:L.divIcon({className:'airport-marker-wrap',html:`<div class="airport-marker" title="${esc(a.name)} Airport"><b>${esc(a.iata)}</b></div>`,iconSize:[38,22],iconAnchor:[19,11]}),zIndexOffset:-200});
        marker.on('click',()=>openAirport(a));
        return marker;
      }));
      if(enabledActivityTypes.has('aircraft'))airportLayer.markers.addTo(map);
      // Planes drawn before the list arrived can now say "Landing at …".
      if(airportLayer.rows.length)renderMap();
    }catch{}finally{airportLayer.loading=false;}
  }

  function syncLeafletActivityMarker(a,displayActivity) {
    const intendedGroup = currentWorkspace==='cool' || ['aircraft','sar'].includes(a.type) ? aerialMarkerGroup : markerGroup;
    let marker = markers.get(a.id), clusterDirty = false;
    const positionSignature = markerPositionSignature(displayActivity);
    const visualSignature = markerVisualSignature(displayActivity);
    const tooltipSignature = markerTooltipSignature(displayActivity);
    if (!marker) {
      marker = L.marker([displayActivity.lat, displayActivity.lon], { icon: markerIcon(displayActivity), riseOnHover: true });
      bindHoverCard(marker, tooltipHtml(displayActivity), { direction: 'top', offset: [0,-13], opacity: 0.97, className: 'activity-tooltip', sticky: true });
      marker.on('click', () => { marker.closeTooltip?.(); selectActivity(a.id, true); });
      marker.__activityGroup = intendedGroup;
      marker.__activityType = a.type;
      marker.__positionSignature = positionSignature;
      marker.__visualSignature = visualSignature;
      marker.__tooltipSignature = tooltipSignature;
      intendedGroup.addLayer(marker);
      if(a.type==='train')marker.getElement?.()?.classList.add('live-object-proxy');
      markers.set(a.id, marker);
      clusterDirty = intendedGroup === markerGroup;
      diagnostics.markersCreated += 1;
      return clusterDirty;
    }
    if (marker.__activityGroup !== intendedGroup) {
      if (marker.__activityGroup === markerGroup || intendedGroup === markerGroup) clusterDirty = true;
      marker.__activityGroup?.removeLayer(marker);
      marker.__activityGroup = intendedGroup;
      intendedGroup.addLayer(marker);
    }
    if (marker.__positionSignature !== positionSignature) {
      moveMarker(marker, displayActivity.lat, displayActivity.lon);
      marker.__positionSignature = positionSignature;
      if (intendedGroup === markerGroup) clusterDirty = true;
      diagnostics.markerMoves += 1;
    }
    syncMarkerVisual(marker,displayActivity,visualSignature);
    if (marker.__tooltipSignature !== tooltipSignature && marker.getTooltip()) {
      marker.setTooltipContent(tooltipHtml(displayActivity));
      marker.__tooltipSignature = tooltipSignature;
      diagnostics.markerTooltipUpdates += 1;
    }
    marker.__activityType = a.type;
    if(a.type==='train')marker.getElement?.()?.classList.add('live-object-proxy');
    return clusterDirty;
  }

  function renderMapDelta(changedIds) {
    if (!changedIds?.size || currentWorkspace==='cool') return false;
    const startedAt=performance.now(), now=Date.now();
    let clusterDirty=false;
    for(const id of changedIds){
      const a=activityById(id), marker=markers.get(id);
      if(!a||!activityPassesFilters(a)){
        if(marker){
          if((marker.__activityGroup||markerGroup)===markerGroup)clusterDirty=true;
          (marker.__activityGroup||markerGroup).removeLayer(marker);
          markers.delete(id);
        }
        visibleActivityIds.delete(id);
        continue;
      }
      // Train neighbours can share an exact berth, so a train change needs
      // the full coincidence-layout pass. Non-rail deltas are independent.
      if(a.type==='train')return false;
      const display=activityMapPresentation(a,now);
      clusterDirty=syncLeafletActivityMarker(a,display)||clusterDirty;
      visibleActivityIds.add(id);
    }
    if(clusterDirty&&typeof markerGroup.refreshClusters==='function')markerGroup.refreshClusters();
    diagnostics.deltaMapRenders+=1;
    diagnostics.lastDeltaRenderMs=Math.round((performance.now()-startedAt)*10)/10;
    $('map').dataset.activityCount=String(visibleActivityIds.size);
    $('map').dataset.lastDeltaRenderMs=String(diagnostics.lastDeltaRenderMs);
    if(heatMode!=='off')renderHeatmap();
    startLiveMotion();
    return true;
  }

  function renderMap(changedIds=null) {
    if(changedIds&&renderMapDelta(changedIds))return;
    syncAirports();
    const startedAt = performance.now();
    let rows = filteredActivities();
    const routeTrain=$('detail-sidebar')?.classList.contains('route-view')?selectedTrainActivity():null;
    if(routeTrain){
      rows=rows.filter(row=>row.type!=='train'||row.id===selectedId);
      if(!rows.some(row=>row.id===routeTrain.id))rows=[...rows,routeTrain];
    }
    const now=Date.now();
    const presentations=rows.map(row=>activityMapPresentation(row,now));
    if(!liveObjectRenderer)ensureLiveRenderer();
    liveObjectRenderer?.setActivities(rows,selectedId);
    const coincidentLayouts=trainCoincidentLayouts(presentations);
    const laidOutPresentations=presentations.map((row,index)=>withTrainCoincidentLayout(row,coincidentLayouts.get(rows[index]?.id)));
    const shown = new Set(rows.map(a => a.id));
    visibleActivityIds = shown;
    let clusterDirty = false;
    for (const [id, marker] of markers.entries()) {
      if (!shown.has(id)||(liveObjectRenderer?.ready&&marker.__activityType==='train')) {
        if ((marker.__activityGroup || markerGroup) === markerGroup) clusterDirty = true;
        (marker.__activityGroup || markerGroup).removeLayer(marker);
        markers.delete(id);
      }
    }
    for (let index=0;index<rows.length;index+=1) {
      const a=rows[index];
      const displayActivity=laidOutPresentations[index];
      if(liveObjectRenderer?.ready&&a.type==='train')continue;
      // Discover is a curated, deliberately small set. Never cluster those
      // markers: each find must remain individually visible at every zoom.
      clusterDirty=syncLeafletActivityMarker(a,displayActivity)||clusterDirty;
    }
    if (clusterDirty && typeof markerGroup.refreshClusters === 'function') markerGroup.refreshClusters();
    repairCoincidentTrainMarkerDom(laidOutPresentations);
    requestAnimationFrame(()=>repairCoincidentTrainMarkerDom(laidOutPresentations));
    renderHeatmap();
    diagnostics.lastRenderMs = Math.round((performance.now() - startedAt) * 10) / 10;
    diagnostics.fullMapRenders += 1;
    $('map').dataset.activityCount = String(rows.length);
    startLiveMotion();
    $('map').dataset.lastRenderMs = String(diagnostics.lastRenderMs);
    if (selectedId && markers.has(selectedId) && focusMarker) {
      map.removeLayer(focusMarker); focusMarker=null;
    }
  }

  function refreshLiveMarkerPositions() {
    if(document.hidden||reduceLiveMotion?.matches)return;
    const now=Date.now();
    // The full feed can contain tens of thousands of buses nationally. Only
    // prepare motion for activities that survived the current map filters.
    const rows=[...visibleActivityIds].map(activityById).filter(Boolean);
    const presentations=rows.map(row=>activityMapPresentation(row,now));
    const coincidentLayouts=trainCoincidentLayouts(presentations);
    liveMotionLayouts=coincidentLayouts;
    for(let index=0;index<rows.length;index+=1){
      const a=rows[index];
      if(!LIVE_MOTION_TYPES.has(a.type))continue;
      const display=withTrainCoincidentLayout(presentations[index],coincidentLayouts.get(a.id));
      if(selectedId===a.id&&a.type==='train')updateSelectedTrainRouteProgress(a,now);
      // Follow with a one-second linear pan that matches the marker's glide,
      // so the map drifts with the train instead of jumping every second.
      if(trackedActivityId===a.id)map.panTo([display.lat,display.lon],{animate:true,duration:1,easeLinearity:1,noMoveStart:true});
      const marker=markers.get(a.id);if(!marker)continue;
      const signature=markerPositionSignature(display);
      const visualSignature=markerVisualSignature(display);
      if(signature!==marker.__positionSignature){
        moveMarker(marker,display.lat,display.lon);
        marker.__positionSignature=signature;
        diagnostics.markerMoves+=1;
      }
      syncMarkerVisual(marker,display,visualSignature);
    }
  }

  function showFocusMarker(a) {
    if (focusMarker) { map.removeLayer(focusMarker); focusMarker=null; }
    if(liveObjectRenderer?.ready&&a?.type==='train')return;
    if (!a || markers.has(a.id)) return;
    focusMarker=L.marker([a.lat,a.lon],{icon:markerIcon(a),riseOnHover:true,zIndexOffset:1200}).addTo(map);
    bindHoverCard(focusMarker,tooltipHtml(a),{direction:'top',offset:[0,-13],opacity:.97,className:'activity-tooltip'});
    focusMarker.on('click',()=>selectActivity(a.id,false));
  }

  function renderHeatmap() {
    if (heatMode === 'off' || typeof L.heatLayer !== 'function') {
      if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
      heatPointCount = 0;
      return;
    }
    // Do not redraw the previous mode's cached points while the server is
    // preparing the newly requested density snapshot.
    if (heatSnapshotPending) {
      if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
      heatPointCount = 0;
      return;
    }
    let rows = activities;
    if (heatMode === 'train') rows = rows.filter(a => a.type === 'train');
    else if (heatMode === 'bus') rows = rows.filter(a => a.type === 'bus');
    else if (heatMode === 'road') rows = rows.filter(a => a.type === 'road');
    else if (heatMode === 'incident') rows = rows.filter(a => a.type === 'incident');
    else if (heatMode === 'weather') rows = rows.filter(a => ['weather','flood'].includes(a.type));
    const points = rows.filter(a => Number.isFinite(Number(a.lat)) && Number.isFinite(Number(a.lon))).map(a => {
      const weight = Math.max(0.25, Math.min(1, interestInfo(a).score / 35));
      return [Number(a.lat), Number(a.lon), weight];
    });
    heatPointCount = points.length;
    if (!points.length) {
      if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
      return;
    }
    if (heatLayer && typeof heatLayer.setLatLngs === 'function') {
      heatLayer.setLatLngs(points);
      return;
    }
    if (heatLayer) map.removeLayer(heatLayer);
    heatLayer = L.heatLayer(points, { radius: 24, blur: 20, maxZoom: 11, minOpacity: 0.28 }).addTo(map);
  }

  function renderStats() {
    const shown = filteredActivities();
    let tracking = 0, moving = 0, interesting = 0;
    for (const a of shown) {
      tracking += Number(a.tracking_count || 0);
      if (a.kind !== 'event' && ['train','bus','vessel','sar','aircraft','satellite','gritter'].includes(a.type)) moving += 1;
      if ((currentWorkspace==='cool' ? coolInfo(a).score : interestInfo(a).score) >= (currentWorkspace==='cool' ? 45 : 15)) interesting += 1;
    }
    $('stat-visible').textContent = fmt(shown.length);
    $('stat-tracking').textContent = fmt(tracking);
    $('stat-moving').textContent = fmt(moving);
    $('stat-interesting').textContent = fmt(interesting);
    renderNearbyBar(shown);
  }

  function renderNearbyBar(shown=filteredActivities()) {
    const target=$('nearby-bar-text');
    if(!target)return;
    if(currentWorkspace==='useful'&&!enabledActivityTypes.size){target.textContent='Choose what to show on the map';return;}
    const bounds=map.getBounds?.();
    const inView=bounds?shown.filter(a=>Number.isFinite(Number(a.lat))&&Number.isFinite(Number(a.lon))&&bounds.contains([Number(a.lat),Number(a.lon)])):shown;
    const labels=[['train','train','trains'],['bus','bus','buses'],['aircraft','aircraft','aircraft'],['sar','rescue flight','rescue flights'],['road','roadwork','roadworks'],['incident','incident','incidents'],['weather','warning','warnings'],['flood','flood warning','flood warnings']];
    const parts=labels.map(([type,one,many])=>{const n=inView.filter(a=>a.type===type).length;return n?`${fmt(n)} ${n===1?one:many}`:null;}).filter(Boolean);
    target.textContent=parts.length?parts.slice(0,3).join(' · '):'Nothing live in this view yet';
  }

  function renderStreamZoomNote() {
    const note = $('bus-zoom-note');
    if (!note) return;
    const zoom = Number(streamMeta.zoom ?? map.getZoom());
    const messages = [];
    if (heatMode !== 'off') {
      const labels = {all:'All activity',train:'Rail',bus:'Bus',road:'Roadworks',incident:'Incident',weather:'Weather'};
      const label = labels[heatMode] || 'Activity';
      if (heatSnapshotPending) messages.push(`Loading ${label.toLowerCase()} density…`);
      else if (!heatPointCount) messages.push(`No ${label.toLowerCase()} density data in this view`);
      else messages.push(`${fmt(heatPointCount)} live points in ${label.toLowerCase()} density`);
    }
    const busTotal = Number(streamMeta.buses_in_memory || 0);
    const busMin = Number(streamMeta.bus_min_zoom || 7);
    const busSource = latestSources.find(source => source.name === 'bods');
    // Socket metadata describes the viewport that produced its snapshot. It
    // can briefly lag behind the markers after a pan or zoom, so count what
    // the user can actually see instead of reporting zero below bus clusters.
    const bounds = map.getBounds?.();
    const visibleBuses = filteredActivities().filter(row => row.type === 'bus'
      && Number.isFinite(Number(row.lat)) && Number.isFinite(Number(row.lon))
      && (!bounds || bounds.contains([Number(row.lat), Number(row.lon)]))).length;
    const busShown = visibleBuses || Number(streamMeta.buses_streamed || 0);
    const busesSelected = (currentWorkspace === 'useful' && enabledActivityTypes.has('bus')) || heatMode === 'bus';
    if (busesSelected && busSource?.mode === 'error') {
      messages.push('Live bus feed temporarily unavailable');
    } else if (busTotal && busesSelected) {
      messages.push(zoom < busMin ? `${fmt(busTotal)} live buses • zoom in to see them` : `${fmt(busShown)} buses in view`);
    }
    if (!messages.length) { note.classList.add('hidden'); return; }
    note.classList.remove('hidden');
    note.textContent = messages.join('   •   ');
  }

  function renderRanking() {
    const list = $('ranking'); if(!list) return;
    list.innerHTML = '';
    const coolMode=currentWorkspace==='cool' || String(selectedType).startsWith('cool');
    let shown = coolMode && globalCoolActivities.length
      ? globalCoolRows().filter(a => layerTypeMatch(a,selectedType) && operatorMatches(a) && (!nearbyState.active || inNearby(a)) && (!searchText || searchable(a).includes(searchText)))
      : [...filteredActivities()];
    if(coolMode) shown=shown.filter(a=>coolInfo(a).score>=45);
    $('ranking-title').textContent = coolMode ? (globalCoolActivities.length ? 'Cool stuff now • UK' : 'Cool stuff now') : 'Interesting now';
    const ranked = shown.sort((a,b) => {
      const as=coolMode?coolInfo(a).score:interestInfo(a).score, bs=coolMode?coolInfo(b).score:interestInfo(b).score;
      const delta=bs-as; return Math.abs(delta)>0.01?delta:new Date(b.last_updated)-new Date(a.last_updated);
    }).slice(0,12);
    if (!ranked.length) {
      list.innerHTML = `<p style="color:var(--muted);font-size:12px">${coolMode?'Nothing unusual is confidently identified in the current live view yet. Routine movements stay hidden here.':'Waiting for live activity.'}</p>`;
      return;
    }
    ranked.forEach((a, i) => {
      const isNew=coolMode&&isNewDiscoveryActivity(a),matchesInterest=coolMode&&activityMatchesInterests(a);
      const button = document.createElement('button'); button.type='button'; button.className=`rank-item${selectedId===a.id?' selected':''}${coolMode?' cool-rank-item':''}${isNew?' is-new':''}`;
      const info=coolMode?coolInfo(a):interestInfo(a);
      const right=coolMode?'LIVE':((a.tracking_count||0)>0?`${fmt(a.tracking_count)} watching`:new Date(a.last_updated).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}));
      const sub=info.reasons[0] || (a.origin&&a.destination?`${a.origin} → ${a.destination}`:(a.subtitle||a.position_label));
      const lead=coolMode?coolBadgeHtml(a,true):`<span class="rank-icon">${icons[a.type]||'•'}</span>`;
      const proof=coolMode?`${discoveryIdentity(a).label} • updated ${discoveryFreshness(a)}`:'';
      button.innerHTML=`<span class="rank-number">${i+1}</span>${lead}<span class="rank-copy"><strong>${isNew?'<em class="discovery-new-tag">NEW</em> ':''}${esc(a.name)}${matchesInterest?' <em class="interest-match">Your interest</em>':''}</strong><span>${esc(sub)}</span>${proof?`<small class="rank-proof">${esc(proof)}</small>`:''}</span><span class="rank-count">${esc(right)}</span>`;
      button.addEventListener('click',()=>selectActivity(a.id,true,coolMode?11:null)); list.appendChild(button);
    });
  }

  // Lateness the server works out for signalling-only trains, kept so a
  // live update does not blank the Status cell until the journey reloads.
  const railDelayById = new Map();

  function trainStatusLabel(a) {
    if (a?.restored_snapshot === true) return 'Last known';
    if (a?.cancelled_at) return 'Cancelled';
    if (a?.terminated_at) return 'Arrived';
    const raw = Number.isFinite(Number(a?.delay_minutes)) && a?.delay_minutes !== null ? Number(a.delay_minutes) : railDelayById.get(a?.id);
    if (raw == null || !Number.isFinite(Number(raw))) return 'Live';
    const delay = Math.round(Number(raw));
    return delay >= 1 ? `${delay} min late` : delay <= -1 ? `${-delay} min early` : 'On time';
  }

  // Flights: departure and arrival airports, looked up once per aircraft.
  const flightRouteById=new Map();
  let flightRouteRequest=0;
  function flightRouteText(route) {
    const label=airport=>airport ? (airport.iata&&airport.name!==airport.iata ? `${airport.name} (${airport.iata})` : airport.name) : '';
    return `${label(route.origin)} → ${label(route.destination)}`;
  }
  async function loadFlightRoute(a) {
    const id=a.id, seq=++flightRouteRequest;
    try {
      const response=await fetch(`/api/activity/${encodeURIComponent(id)}/flight-route?callsign=${encodeURIComponent(a.name||'')}&lat=${encodeURIComponent(a.lat)}&lon=${encodeURIComponent(a.lon)}`,{cache:'no-store'});
      if(!response.ok)return;
      const payload=await response.json();
      flightRouteById.set(id,payload?.found?payload.route:null);
      if(seq!==flightRouteRequest||selectedId!==id||!payload?.found)return;
      // The route is the subtitle; it is not repeated as a fact.
      $('detail-subtitle').textContent=flightRouteText(payload.route);
    } catch {}
  }

  // Colour the compact train status chip by lateness.
  function toneStatusField(a) {
    const cell=document.querySelector('#detail-fields [data-field="Status"]');
    if(!cell)return;
    const label=trainStatusLabel(a);
    cell.dataset.tone=/cancel/i.test(label)?'bad':/late/.test(label)?(Number(a?.delay_minutes ?? railDelayById.get(a?.id))>5?'bad':'warn'):/on time|early/i.test(label)?'good':'';
  }

  function field(label, value) {
    if (value === undefined || value === null || value === '') return '';
    const technical = new Set(['Source','ICAO / source ref','ICAO type code','Latitude','Longitude','Train UID','Headcode','Journey ref','Tracking','Bearing','Code','Type','Registration','MMSI','Identified as','Identity confidence','Position','Operator','Aircraft category','Squawk','Position source','Indicated airspeed','True airspeed','Mach','Heading','Selected altitude','Aircraft flags']);
    return `<div class="detail-field${technical.has(label) ? ' is-technical' : ''}" data-field="${esc(label)}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
  }

  function clearCoverage() {
    const box = $('detail-coverage');
    box.classList.add('hidden');
    box.innerHTML = '';
    coverageLoadedFor = null;
  }

  async function loadCoverage(a) {
    if (a.kind !== 'event') { clearCoverage(); return; }
    if (coverageLoadedFor === a.id) return;
    clearCoverage();
    coverageLoadedFor = a.id;
    const box = $('detail-coverage');
    box.classList.remove('hidden');
    box.innerHTML = '<small>LIVE STORIES & OFFICIAL LINKS</small><p class="coverage-loading">Building current story searches and official links…</p>';
    const requestId = ++coverageRequest;
    try {
      const response = await fetch(`/api/events/${encodeURIComponent(a.id)}/coverage`);
      if (!response.ok) throw new Error('Coverage unavailable');
      const payload = await response.json();
      if (requestId !== coverageRequest || selectedId !== a.id) return;
      const links = [];
      if (payload.official_url) links.push(`<a class="coverage-link official" target="_blank" rel="noopener" href="${esc(safeUrl(payload.official_url))}">${esc(payload.official_label || 'Official source')} ↗</a>`);
      for (const link of (payload.links || [])) links.push(`<a class="coverage-link" target="_blank" rel="noopener" href="${esc(safeUrl(link.url))}">${esc(link.label)} ↗</a>`);
      const articles = (payload.articles || []).map(row => `<a class="coverage-article" target="_blank" rel="noopener" href="${esc(safeUrl(row.url))}"><strong>${esc(row.title)}</strong><span>${esc(row.source || 'News')}</span></a>`).join('');
      const verification = payload.verification_note ? `<p class="coverage-verification${payload.verified_only ? ' verified-only' : ''}">${esc(payload.verification_note)}</p>` : '';
      const emptyCopy = payload.verified_only ? 'No current launch media could be verified against this exact RNLI event. Nothing old or generic has been substituted.' : 'No closely matching current articles were found right now. Official links still work.';
      box.innerHTML = `<small>LIVE STORIES & OFFICIAL LINKS</small>${verification}<div class="coverage-actions">${links.join('')}</div>${articles ? `<div class="coverage-news">${articles}</div>` : `<p class="coverage-loading">${esc(emptyCopy)}</p>`}`;
    } catch {
      if (requestId !== coverageRequest) return;
      box.innerHTML = '<small>LIVE STORIES & OFFICIAL LINKS</small><p class="coverage-loading">Story lookup unavailable. Official event data is unaffected.</p>';
    }
  }

  // "Is this train usually late?" from the radar's own day-by-day record.
  const usuallyById=new Map();
  async function loadUsually(a){
    const box=$('detail-usually');
    if(!box)return;
    box.classList.add('hidden');box.textContent='';
    if(a?.type!=='train')return;
    let found=usuallyById.get(a.id);
    if(found===undefined){
      try{const r=await fetch(`/api/activity/${encodeURIComponent(a.id)}/usually`);found=r.ok?await r.json():null;}catch{found=null;}
      usuallyById.set(a.id,found);
    }
    let lines=[];
    try{const r=await fetch(`/api/activity/${encodeURIComponent(a.id)}/lines`);lines=r.ok?((await r.json()).lines||[]):[];}catch{}
    if(selectedId!==a.id)return;
    const parts=[];
    if(found&&found.days){
      const onTime=found.days-found.late-found.cancelled;
      parts.push(esc(`Usually: on time on ${onTime} of the last ${found.days} days we saw it`+(found.late?`, late on ${found.late}`:'')+(found.cancelled?`, cancelled on ${found.cancelled}`:'')+(found.average!=null&&found.average>=1?` (about ${Math.round(found.average)} min late on average)`:'')+'.'));
    }
    if(lines.length)parts.push(`On the ${lines.map(l=>`<a href="${esc(l.path)}">${esc(l.name)}</a>`).join(' and the ')} now.`);
    if(!parts.length)return;
    box.innerHTML=parts.join('<br>');
    box.classList.remove('hidden');
  }

  function renderInsight(a) {
    const box = $('detail-insight');
    if(!box)return;
    box.classList.remove('train-live-card');
    const motorwaySign=a?.kind==='event' && String(a.category||'').toLowerCase()==='live motorway sign';
    const managedSpeed=a?.kind==='event' && String(a.category||'').toLowerCase()==='live managed speed';
    if(motorwaySign){
      box.classList.remove('hidden');box.classList.add('motorway-sign-card');
      const lines=String(a.reason||a.name||'').split(/\s*\/\s*/).filter(Boolean).slice(0,4);
      box.innerHTML=`<small>LIVE MOTORWAY SIGN</small><div class="vms-display">${lines.map(line=>`<span>${esc(line.toUpperCase())}</span>`).join('')}</div><em>${esc([a.road,a.direction].filter(Boolean).join(' • ')||'National Highways network')}</em>`;
      return;
    }
    box?.classList.remove('motorway-sign-card');
    if(managedSpeed){
      const speed=(String(a.name||'').match(/\b\d{1,3}\b/)||[])[0]||'?';
      box.classList.remove('hidden');
      box.innerHTML=`<small>LIVE MANAGED SPEED</small><div class="uk-speed-roundel" aria-label="${esc(speed)} miles per hour">${esc(speed)}</div><strong>${esc([a.road,a.direction].filter(Boolean).join(' • ')||'National Highways')}</strong><span>${esc(a.reason||'Temporary speed restriction')}</span>`;
      return;
    }
    if(a.type==='train'){
      const delayRaw=railDelayById.has(a.id)?railDelayById.get(a.id):a.delay_minutes;
      const delay=delayRaw==null?null:Number(delayRaw);
      const cancelled=Boolean(a.cancelled_at)||/cancel/i.test(String(a.status||''));
      const status=cancelled?'Cancelled':delay==null?'Live':delay>=1?`${Math.round(delay)} min late`:delay<=-1?`${Math.abs(Math.round(delay))} min early`:'On time';
      const route=[humanTransitLabel(a.origin||''),humanTransitLabel(a.destination||'')].filter(Boolean);
      const operator=humanTransitLabel(a.operator||'');
      const next=humanTransitLabel(a.next_location||'');
      const journey=route.length===2?` from ${route[0]} to ${route[1]}`:route.length?` towards ${route[0]}`:'';
      const nowCopy=cancelled
        ? `${a.name} is cancelled${journey}.`
        : `${operator?`${operator} service `:''}${a.name} is ${status.toLowerCase()}${journey}${next?`, heading next towards ${next}`:''}.`;
      const stories=[];
      if(next)stories.push(['What happens next',`The next railway report is expected around ${next}.`]);
      box.classList.remove('hidden','aircraft-live-card');
      box.classList.add('train-live-card');
      box.innerHTML=`<div class="train-live-head"><small>LIVE TRAIN STORY</small><span>${esc(operator||'UK railway network')}</span></div><div class="train-live-now"><div class="train-story-icon" aria-hidden="true">${icons.train}</div><div><small>RIGHT NOW</small><strong>${esc(nowCopy)}</strong></div></div><div class="train-live-tags"><b data-tone="${cancelled?'bad':delay>=5?'bad':delay>=1?'warn':'good'}">${esc(status)}</b>${a.platform?`<b>Platform ${esc(a.platform)}</b>`:''}${a.position_method==='estimated'?'<b>Between reports</b>':'<b>Confirmed report</b>'}</div><div class="train-story-list">${stories.map(([label,value])=>`<div><strong>${esc(label)}</strong><span>${esc(value)}</span></div>`).join('')}</div>`;
      return;
    }
    let info;
    if(a.type==='aircraft'){
      const classified=coolInfo(a);
      const number=value=>value==null||!Number.isFinite(Number(value))?null:Number(value);
      const compass=value=>{
        const degrees=number(value);if(degrees==null)return null;
        const names=['N','NE','E','SE','S','SW','W','NW'];
        return `${Math.round((degrees+360)%360).toString().padStart(3,'0')}° ${names[Math.round(((degrees+360)%360)/45)%8]}`;
      };
      const directionWord=value=>{
        const degrees=number(value);if(degrees==null)return null;
        return ['north','north-east','east','south-east','south','south-west','west','north-west'][Math.round(((degrees+360)%360)/45)%8];
      };
      const rate=number(a.vertical_rate_fpm);
      const track=compass(a.bearing),heading=compass(a.heading),targetHeading=compass(a.selected_heading);
      const modes=(Array.isArray(a.navigation_modes)?a.navigation_modes:[]).map(mode=>({autopilot:'Autopilot',vnav:'VNAV',althold:'Altitude hold',approach:'Approach',lnav:'LNAV',tcas:'TCAS'}[String(mode).toLowerCase()]||String(mode).toUpperCase()));
      const routeCount=number(a.aircraft_route_count);
      const identity=a.aircraft_model||a.aircraft_type_code||'aircraft';
      const movement=rate==null?'is airborne':rate>100?`is climbing at ${Math.round(rate).toLocaleString('en-GB')} feet per minute`:rate<-100?`is descending at ${Math.abs(Math.round(rate)).toLocaleString('en-GB')} feet per minute`:'is holding level';
      const liveContext=[a.bearing!=null?`tracking ${directionWord(a.bearing)}`:null,a.speed!=null?`at ${Math.round(Number(a.speed)).toLocaleString('en-GB')} knots`:null,a.altitude_ft!=null?`at ${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} feet`:null].filter(Boolean).join(' ');
      // On the runway or taxiing, say so instead of "holding level at 0 feet".
      const phase=aircraftGroundPhase(a);
      const story=phase
        ? `This ${identity} ${phase.sentence}${a.bearing!=null&&!['Parked','On ground'].includes(phase.short)?`, heading ${directionWord(a.bearing)}`:''}.`
        : `This ${identity} ${movement}${liveContext?`, ${liveContext}`:''}.`;
      const stories=[];
      if(a.origin&&a.destination)stories.push(['Where it usually flies',`Receivers most often saw this callsign flying ${a.origin} to ${a.destination}${routeCount?` — ${Math.round(routeCount)} time${Math.round(routeCount)===1?'':'s'} recently`:''}. Today’s route may be different.`]);
      if(a.selected_altitude_ft!=null||targetHeading)stories.push(['What it is set to do',`The selected controls point toward ${[a.selected_altitude_ft!=null?`${Math.round(Number(a.selected_altitude_ft)).toLocaleString('en-GB')} feet`:null,targetHeading?`a ${targetHeading} heading`:null].filter(Boolean).join(' and ')}.`]);
      if(modes.length)stories.push(['Systems reporting now',`${modes.join(', ')} ${modes.length===1?'is':'are'} active in the transmitted data.`]);
      const airData=[a.mach!=null?`Mach ${Number(a.mach).toFixed(2)}`:null,a.indicated_airspeed_kt!=null?`${Math.round(Number(a.indicated_airspeed_kt))} kt indicated`:null,a.true_airspeed_kt!=null?`${Math.round(Number(a.true_airspeed_kt))} kt true airspeed`:null].filter(Boolean);
      if(airData.length)stories.push(['How fast it feels to the aircraft',`${airData.join(', ')}.`]);
      const atmosphere=[a.wind_direction!=null&&a.wind_speed_kt!=null?`wind from ${directionWord(a.wind_direction)} at ${Math.round(Number(a.wind_speed_kt))} knots`:null,a.outside_air_temp_c!=null?`outside air ${Math.round(Number(a.outside_air_temp_c))}°C`:null,a.roll_deg!=null&&Math.abs(Number(a.roll_deg))>=1?`banking ${Number(a.roll_deg)<0?'left':'right'} by ${Math.abs(Math.round(Number(a.roll_deg)))}°`:null].filter(Boolean);
      if(atmosphere.length)stories.push(['The air around it',`${atmosphere.join(', ')}.`]);
      if(a.position_source)stories.push(['How we can see it',`${a.position_source==='MLAT'?'Ground receivers calculated this position by multilateration.':`The aircraft is broadcasting an ${a.position_source} position.`}`]);
      const flags=Array.isArray(a.aircraft_flags)?a.aircraft_flags:[];
      const flagCopy={Military:'Military','Notable':'Notable aircraft',PIA:'Privacy ICAO address',LADD:'Limited display'};
      const tags=[...flags.map(flag=>flagCopy[flag]||flag),a.emergency||null].filter(Boolean);
      const source=/flyitaly/i.test(String(a.source||''))?'FlyItalyADSB':'Live ADS-B';
      const why=classified.candidate&&classified.score>=45&&classified.reasons.length?`<div class="aircraft-why"><strong>Why this is interesting</strong><span>${esc(classified.reasons.join(' • '))}</span></div>`:'';
      if(!stories.length&&!tags.length&&!why){box.classList.add('hidden');box.classList.remove('aircraft-live-card');box.innerHTML='';return;}
      box.classList.add('aircraft-live-card');
      box.classList.remove('hidden');
      const pointer=number(a.bearing)??number(a.heading)??0;
      box.innerHTML=`<div class="aircraft-live-head"><small>LIVE AIRCRAFT STORY</small><span>${esc(source)}</span></div><div class="aircraft-live-now"><div class="aircraft-direction" aria-hidden="true"><span>N</span><svg viewBox="0 0 24 24" style="transform:rotate(${pointer}deg)"><path d="M12 2l5 18-5-3-5 3z"/></svg></div><div><small>RIGHT NOW${track?` · ${esc(track)}`:''}</small><strong>${esc(story)}</strong></div></div>${tags.length?`<div class="aircraft-live-tags">${tags.map(tag=>`<b${tag===a.emergency?' data-alert="true"':''}>${esc(tag)}</b>`).join('')}</div>`:''}${stories.length?`<div class="aircraft-story-list">${stories.map(([label,value])=>`<div><strong>${esc(label)}</strong><span>${esc(value)}</span></div>`).join('')}</div>`:''}${why}`;
      return;
    }else{
      box.classList.remove('aircraft-live-card');
      info=currentWorkspace==='cool' ? coolInfo(a) : interestInfo(a);
    }
    if (info.score < (currentWorkspace==='cool'?45:10)) { box.classList.add('hidden'); box.innerHTML=''; return; }
    box.classList.remove('hidden');
    box.innerHTML = `<strong>${currentWorkspace==='cool'?'Why this is unusual':'Why this is interesting'}</strong><span>${esc(info.reasons.join(' • ') || 'Current live activity')}</span>`;
  }

  function renderRelated(a) {
    const box = $('detail-related');
    if (!box) return;
    // Removed from the detail panel: a list of whatever else is within
    // 25 km rarely related to the selected item and pushed details down.
    box.classList.add('hidden'); box.innerHTML=''; return;
    const related = activities.filter(x => x.id !== a.id).map(x => ({x, km: distanceKm(Number(a.lat), Number(a.lon), Number(x.lat), Number(x.lon))})).filter(row => row.km <= 25).sort((m,n)=>m.km-n.km).slice(0,8);
    if (!related.length) { box.classList.add('hidden'); box.innerHTML=''; return; }
    const counts = {};
    related.forEach(({x}) => { counts[x.type] = (counts[x.type] || 0) + 1; });
    const summary = Object.entries(counts).map(([type,count])=>`${icons[type] || '•'} ${count} ${type}`).join(' • ');
    box.classList.remove('hidden');
    box.innerHTML = `<small>ALSO NEARBY</small><p class="related-summary">${esc(summary)} within 25 km.</p><div class="related-list">${related.slice(0,5).map(({x,km})=>`<button type="button" data-related-id="${esc(x.id)}"><span>${icons[x.type] || '•'} ${esc(x.name)}</span><small>${km.toFixed(1)} km</small></button>`).join('')}</div>`;
    box.querySelectorAll('[data-related-id]').forEach(btn => btn.addEventListener('click',()=>selectActivity(btn.dataset.relatedId,true)));
  }

  function renderTrackingControl(a) {
    const button = $('track-activity');
    if (!button) return;
    const trackable = a?.kind !== 'event' && ['train','bus','vessel','sar','aircraft','satellite','gritter'].includes(a?.type);
    if (!trackable) {
      button.classList.add('hidden');
      button.classList.remove('active');
      button.setAttribute('aria-pressed','false');
      return;
    }
    const active = trackedActivityId === a.id;
    button.classList.remove('hidden');
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    button.textContent = active ? 'Stop following' : 'Follow on map';
  }

  function trailPanel() {
    let box = $('detail-trail');
    if (box) return box;
    box = document.createElement('div');
    box.id = 'detail-trail';
    box.className = 'detail-trail hidden';
    $('detail-insight')?.insertAdjacentElement('afterend', box);
    return box;
  }

  function selectedTrainActivity() {
    const activity=selectedId ? activityById(selectedId) : null;
    return activity?.type==='train' ? activity : null;
  }

  function syncDetailRouteControl(activity=selectedTrainActivity()) {
    const train=activity?.type==='train' ? activity : null;
    const button=$('detail-route-view');
    button?.classList.toggle('hidden',!train);
    const title=$('detail-route-peek-title');
    if(title)title.textContent=train?.name || 'Selected train';
    if(!train)setDetailRouteView(false);
  }

  function focusSelectedTrainRoute({whole=false}={}) {
    const route=selectedTrainRoute;
    const points=whole ? route?.points : (route?.livePoints?.length>=2 ? route.livePoints : route?.points);
    if(!Array.isArray(points)||points.length<2){detailRouteFitPending=true;return false;}
    detailRouteFitPending=false;
    detailRouteAwaitPublishedFit=selectedTrainPublishedRoute?.id!==selectedTrainRoute.id;
    const mobile=window.matchMedia('(max-width: 760px)').matches;
    const bounds=L.latLngBounds(points);
    const paddingTopLeft=mobile?[84,64]:[60,60];
    const paddingBottomRight=mobile?[24,140]:[420,80];
    const padding=[Math.max(paddingTopLeft[0],paddingBottomRight[0]),Math.max(paddingTopLeft[1],paddingBottomRight[1])];
    const fitted=map.getBoundsZoom?.(bounds,false,padding) ?? 11;
    const zoom=whole?Math.min(13,fitted):Math.max(8,Math.min(13,fitted));
    const center=bounds.getCenter?.() || {
      lat:(Math.min(...points.map(point=>Number(point[0])))+Math.max(...points.map(point=>Number(point[0]))))/2,
      lng:(Math.min(...points.map(point=>Number(point[1])))+Math.max(...points.map(point=>Number(point[1]))))/2,
    };
    map.setView(center,zoom,{animate:true});
    return true;
  }

  function setDetailRouteView(active,{fit=false,focusPeek=false,focusDetails=false}={}) {
    const sidebar=$('detail-sidebar');
    const panel=$('detail-panel');
    const peek=$('detail-route-peek');
    const collapsed=Boolean(active&&selectedTrainActivity());
    sidebar?.classList.toggle('route-view',collapsed);
    if(sidebar)sidebar.dataset.detailView=collapsed?'route':'full';
    peek?.classList.toggle('hidden',!collapsed);
    panel?.setAttribute('aria-hidden',String(collapsed));
    if('inert' in (panel||{}))panel.inert=collapsed;
    if(!collapsed){detailRouteFitPending=false;detailRouteAwaitPublishedFit=false;}
    if(collapsed&&fit)focusSelectedTrainRoute();
    renderMap();
    if(collapsed&&focusPeek)$('detail-route-restore')?.focus({preventScroll:true});
    if(!collapsed&&focusDetails)$('detail-route-view')?.focus({preventScroll:true});
  }

  function clearSelectedTrail() {
    if (selectedTrailLayer) map.removeLayer(selectedTrailLayer);
    selectedTrailLayer = null;
    clearSelectedTrainRoute();
    const box = trailPanel();
    box?.classList.add('hidden');
    box?.classList.remove('rail-leg');
    if (box) box.innerHTML = '';
    $('map')?.removeAttribute('data-selected-trail-points');
  }

  function clearSelectedTrainRoute(clearPublished=true) {
    if(selectedTrainRoute?.group&&map.hasLayer(selectedTrainRoute.group))map.removeLayer(selectedTrainRoute.group);
    selectedTrainRoute=null;
    if(clearPublished)selectedTrainPublishedRoute=null;
    const mapNode=$('map');
    mapNode?.removeAttribute('data-selected-rail-route-points');
    mapNode?.removeAttribute('data-selected-rail-leg-points');
    mapNode?.removeAttribute('data-selected-rail-route-arrows');
    mapNode?.removeAttribute('data-selected-rail-route-progress');
    mapNode?.removeAttribute('data-selected-rail-route-coverage');
  }

  function updateSelectedTrainRouteProgress(a, now=Date.now()) {
    if(!selectedTrainRoute||selectedTrainRoute.id!==a?.id||!selectedTrainRoute.progress||selectedTrainRoute.livePoints.length<2)return;
    const fraction=trainEstimateFraction(a,now);
    const sample=pathSample(selectedTrainRoute.livePoints,fraction);
    if(!sample)return;
    selectedTrainRoute.progress.setLatLngs(sample.progress);
    const mapNode=$('map');
    if(mapNode)mapNode.dataset.selectedRailRouteProgress=String(Math.round(fraction*100));
  }

  function renderSelectedTrainRoute(a, now=Date.now()) {
    const livePoints=trainEstimatePath(a);
    const publishedPoints=selectedTrainPublishedRoute?.id===a?.id
      ? selectedTrainPublishedRoute.points
      : [];
    const points=publishedPoints.length>=2 ? publishedPoints : livePoints;
    if(points.length<2){clearSelectedTrainRoute(false);return false;}
    const signature=`${a.id}|route:${points.map(point=>point.join(',')).join('|')}|leg:${livePoints.map(point=>point.join(',')).join('|')}`;
    if(selectedTrainRoute?.signature!==signature){
      clearSelectedTrainRoute(false);
      const outline=L.polyline(points,{pane:'trainRoutePane',color:'#07131b',weight:12,opacity:.82,interactive:false,className:'train-published-route-outline'});
      const guide=L.polyline(points,{pane:'trainRoutePane',color:'#54d7f0',weight:6,opacity:.96,interactive:false,className:'train-published-route-guide'});
      const liveGuide=livePoints.length>=2
        ? L.polyline(livePoints,{pane:'trainRoutePane',color:'#f7fbff',weight:3,opacity:.96,dashArray:'3 7',interactive:false,className:'train-live-leg-guide'})
        : null;
      const progress=livePoints.length>=2
        ? L.polyline([livePoints[0],livePoints[0]],{pane:'trainRoutePane',color:'#ffd34e',weight:7,opacity:1,interactive:false,className:'train-live-leg-progress'})
        : null;
      const samples=(pathSample(points,.36)?.total||0)<2 ? [.58] : [.34,.72];
      const arrows=samples.map(fraction=>{
        const sample=pathSample(points,fraction);
        if(!sample)return null;
        return L.marker([sample.lat,sample.lon],{
          pane:'trainRoutePane',interactive:false,keyboard:false,
          icon:L.divIcon({
            className:'train-route-arrow',
            html:`<span aria-hidden="true" style="transform:rotate(${Math.round(sample.bearing-90)}deg)">➤</span>`,
            iconSize:[24,24],iconAnchor:[12,12]
          })
        });
      }).filter(Boolean);
      const group=L.layerGroup([outline,guide,liveGuide,progress,...arrows].filter(Boolean)).addTo(map);
      selectedTrainRoute={id:a.id,signature,points,livePoints,outline,guide,liveGuide,progress,arrows,group};
      const mapNode=$('map');
      if(mapNode){
        mapNode.dataset.selectedRailRoutePoints=String(points.length);
        mapNode.dataset.selectedRailLegPoints=String(livePoints.length);
        mapNode.dataset.selectedRailRouteArrows=String(arrows.length);
        if(selectedTrainPublishedRoute?.coveragePercent!=null){
          mapNode.dataset.selectedRailRouteCoverage=String(selectedTrainPublishedRoute.coveragePercent);
        }
      }
    }
    updateSelectedTrainRouteProgress(a,now);
    if(detailRouteFitPending&&$('detail-sidebar')?.classList.contains('route-view')){
      requestAnimationFrame(()=>focusSelectedTrainRoute());
    }
    return true;
  }

  function renderSelectedTrail(a = selectedId ? activityById(selectedId) : null) {
    if (selectedTrailLayer) map.removeLayer(selectedTrailLayer);
    selectedTrailLayer = null;
    const box = trailPanel();
    if(a?.type==='train'){
      const shown=renderSelectedTrainRoute(a);
      $('map')?.removeAttribute('data-selected-trail-points');
      if(!box)return;
      // The route is drawn on the map; no explanatory box is needed.
      box.classList.add('hidden');
      box.classList.remove('rail-leg');
      box.innerHTML='';
      return;
      const routePointSummary=selectedTrainPublishedRoute?.id===a.id&&selectedTrainPublishedRoute.mappedTimingPoints
        ? `Cyan is locked to physical track through ${selectedTrainPublishedRoute.mappedTimingPoints} timetable points${selectedTrainPublishedRoute.coveragePercent!=null?` (${selectedTrainPublishedRoute.coveragePercent}% mapped)`:''}.`
        : 'Cyan is locked to the physical railway for this live leg.';
      box.innerHTML=shown
        ? `${selectedTrainPublishedRoute?.id===a.id
          ? `<strong>PUBLISHED TRAIN ROUTE</strong><span>${esc(selectedTrainPublishedRoute.origin&&selectedTrainPublishedRoute.destination?`${selectedTrainPublishedRoute.origin} → ${selectedTrainPublishedRoute.destination}`:(a.next_location?`Towards ${a.next_location}`:'Route shown on map'))}</span>`
          : `<strong>CURRENT RAIL LEG</strong><span>${esc(a.next_location?`Towards ${a.next_location}`:'Direction shown on map')}</span>`}<small>${esc(routePointSummary)} Gold shows live estimated progress between confirmed reports.</small>`
        : '';
      return;
    }
    clearSelectedTrainRoute();
    box?.classList.remove('rail-leg');
    if (!a || !['aircraft','sar'].includes(a.type)) {
      box?.classList.add('hidden');
      if (box) box.innerHTML = '';
      $('map')?.removeAttribute('data-selected-trail-points');
      return;
    }
    const points = movementHistory.points(a.id);
    $('map')?.setAttribute('data-selected-trail-points', String(points.length));
    if (points.length > 1) {
      selectedTrailLayer = L.polyline(points.map(point=>[point.lat,point.lon]), {
        color:a.type==='sar'?'#ff8b6a':'#6fc8ff', weight:3, opacity:.86, dashArray:'7 7', interactive:false
      }).addTo(map);
    }
    // The dashed trail on the map says it all; no explanatory box.
    if (box) { box.classList.add('hidden'); box.innerHTML=''; }
  }

  // The selected bus's route follows the operator's published GTFS shape when
  // available. Stop-to-stop lines are only a fallback for feeds without one.
  let busRouteLayer=null;
  function clearBusRoute(){ if(busRouteLayer){map.removeLayer(busRouteLayer);busRouteLayer=null;} $('map')?.removeAttribute('data-bus-route-stops'); $('map')?.removeAttribute('data-bus-route-geometry'); }
  function drawBusRoute(id,detail){
    clearBusRoute();
    if(selectedId!==id)return;
    const stops=(Array.isArray(detail?.stops)?detail.stops:[]).filter(stop=>Number.isFinite(Number(stop?.lat))&&Number.isFinite(Number(stop?.lon)));
    if(stops.length<2)return;
    const nextIndex=Math.max(0,stops.findIndex(stop=>stop.state==='next'));
    const fallback=stops.map(stop=>[Number(stop.lat),Number(stop.lon)]);
    const shape=(Array.isArray(detail?.shape)?detail.shape:[]).map(point=>[Number(point?.[0]),Number(point?.[1])]).filter(point=>Number.isFinite(point[0])&&Number.isFinite(point[1]));
    const path=shape.length>=2?shape:fallback;
    const vehicle=activityById(id);
    let splitIndex=Math.max(1,Math.round((nextIndex/Math.max(1,stops.length-1))*(path.length-1)));
    if(vehicle&&Number.isFinite(Number(vehicle.lat))&&Number.isFinite(Number(vehicle.lon))){
      let best=Infinity;
      path.forEach((point,index)=>{const distance=distanceKm(Number(vehicle.lat),Number(vehicle.lon),point[0],point[1]);if(distance<best){best=distance;splitIndex=index;}});
    }
    splitIndex=Math.max(1,Math.min(path.length-2,splitIndex));
    const done=path.slice(0,splitIndex+1);
    const ahead=path.slice(splitIndex);
    const group=L.layerGroup();
    if(done.length>1)group.addLayer(L.polyline(done,{pane:'trainRoutePane',color:'#9aa8b5',weight:4,opacity:.9,interactive:false}));
    if(ahead.length>1)group.addLayer(L.polyline(ahead,{pane:'trainRoutePane',color:'#0b63ce',weight:5,opacity:.9,interactive:false}));
    for(const stop of stops)group.addLayer(L.circleMarker([Number(stop.lat),Number(stop.lon)],{pane:'trainRoutePane',radius:3.5,color:'#fff',weight:1.5,fillColor:stop.state==='passed'?'#9aa8b5':'#0b63ce',fillOpacity:1,interactive:false}));
    busRouteLayer=group.addTo(map);
    $('map')?.setAttribute('data-bus-route-stops',String(stops.length));
    $('map')?.setAttribute('data-bus-route-geometry',shape.length>=2?'gtfs-shape':'stop-fallback');
  }

  function clearActivityIntelligence() {
    clearBusRoute();
    clearTimeout(intelligenceRefreshTimer);
    intelligenceRefreshTimer=null;
    intelligenceController?.abort();
    intelligenceController=null;
    intelligenceRequest += 1;
    intelligenceLoadedFor = null;
    const box = $('detail-intelligence');
    if (!box) return;
    box.classList.add('hidden');
    box.innerHTML = '';
  }

  function providerName(value) {
    if (value == null) return '';
    if (typeof value === 'string' || typeof value === 'number') {
      const label=humanTransitLabel(String(value).trim());
      return /^(?:ATOC|TOC|OPERATOR CODE)\s+[A-Z0-9]+$/i.test(label) ? 'Rail operator' : label;
    }
    if (typeof value !== 'object') return '';
    return providerName(value.name || value.station || value.location || value.display_name || value.crs || value.code);
  }

  const KEEP_UPPER=new Set(['LNER','GWR','SWR','TFL','TFW','DLR','LUL','LU','NR','BR','RAF','RNLI','HM','HMS','USAF','NHS','UK','BBC','LO','CTRL','MK','XC','EMR','TPE','ATC','DMU','EMU','HST','IET']);
  const KEEP_LOWER=new Set(['of','on','upon','the','and','in','by','le','la','de','under','over','next','sea','super']);
  function tidyCapitals(text) {
    // CORPUS and timetable names arrive in capitals ("BIRMINGHAM NEW STREET").
    // Mixed-case text and codes with digits (1V17, M6) are left as they are.
    if(!/[A-Z]{3}/.test(text)||/[a-z]/.test(text))return text;
    let first=true;
    return text.replace(/[A-Za-z]+(?:'[A-Za-z]+)?/g,word=>{
      const upper=word.toUpperCase();
      let out;
      if(KEEP_UPPER.has(upper))out=upper;
      else if(!first&&KEEP_LOWER.has(word.toLowerCase())&&word.length<=5)out=word.toLowerCase();
      else out=word.charAt(0).toUpperCase()+word.slice(1).toLowerCase();
      first=false;
      return out;
    });
  }

  function humanTransitLabel(value) {
    // Feeds write "&" as "_": M_S is M&S and B_Q is B&Q (one letter each
    // side); St_Margaret_s is St Margaret's.
    return tidyCapitals(String(value ?? '').replace(/(^|[^A-Za-z])([A-Z])_([A-Z])(?![A-Za-z])/g,'$1$2&$3').replace(/_s(?=_|$)/gi,"'s").replace(/[_\s]+/g,' ').trim());
  }

  function providerClock(value) {
    if (!value) return '';
    if (typeof value === 'object') {
      const estimate = value.estimate && typeof value.estimate === 'object' ? value.estimate.at : null;
      return providerClock(estimate || value.expected || value.actual || value.at || value.scheduled || value.time);
    }
    const raw = String(value).trim();
    if (!raw) return '';
    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime()) && /[T\-]/.test(raw)) return parsed.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
    return raw.length > 8 ? raw.slice(0,8) : raw;
  }

  function durationText(seconds, explicitMinutes=null) {
    const total = explicitMinutes == null ? Math.round(Number(seconds || 0) / 60) : Math.round(Number(explicitMinutes || 0));
    if (!Number.isFinite(total) || total <= 0) return '';
    if (total < 60) return `${total} min`;
    const hours=Math.floor(total/60), minutes=total%60;
    return `${hours} hr${minutes ? ` ${minutes} min` : ''}`;
  }

  function railCallingPoints(run) {
    const candidates = [run?.calling_points,run?.locations,run?.calls,run?.stops,run?.progress?.calling_points,run?.train?.calling_points];
    const rows=candidates.find(Array.isArray) || [];
    return rows.filter(point=>{
      const event=String(point?.event || point?.status || point?.type || '').trim();
      return !/^pass(?:es|ing|ed)?$/i.test(event);
    });
  }

  function railPublishedPath(detail) {
    const runs=Array.isArray(detail?.runs)?detail.runs:[];
    for(const run of runs){
      const supplied=Array.isArray(run?.route_path)?run.route_path:[];
      // Calling points constrain the service but are not track geometry.
      // Joining them draws field-crossing chords and makes trains look as if
      // they float. Only render a route proven by the physical rail graph.
      const points=supplied
        .map(point=>[Number(point?.[0]),Number(point?.[1])])
        .filter(point=>Number.isFinite(point[0])&&Number.isFinite(point[1]));
      const deduped=points.filter((point,index)=>index===0||point[0]!==points[index-1][0]||point[1]!==points[index-1][1]);
      if(deduped.length>=2){
        const resolution=run?.route_resolution&&typeof run.route_resolution==='object'?run.route_resolution:{};
        return {
          points:deduped,
          origin:providerName(run?.origin),
          destination:providerName(run?.destination),
          mappedTimingPoints:Number.isFinite(Number(resolution.mapped_timing_points))?Number(resolution.mapped_timing_points):null,
          coveragePercent:Number.isFinite(Number(resolution.coverage_percent))?Number(resolution.coverage_percent):null,
        };
      }
    }
    return null;
  }

  function applySelectedTrainPublishedRoute(a,detail) {
    if(a?.type!=='train'||selectedId!==a.id)return;
    const route=railPublishedPath(detail);
    if(!route)return;
    selectedTrainPublishedRoute={id:a.id,...route};
    renderSelectedTrail(a);
    if(detailRouteAwaitPublishedFit&&$('detail-sidebar')?.classList.contains('route-view')){
      requestAnimationFrame(()=>focusSelectedTrainRoute());
    }
  }

  // One stop list for trains and buses. It opens at the stop before the
  // vehicle, so where it is now and what comes next are in view; earlier
  // stops sit behind a button. Between stops a marker shows where it is.
  let earlierStopsOpenFor=null;
  function stopTimelineHtml(rows, {vehicle='Train', hasProgress=false, className=''}={}) {
    if(!rows.length)return '';
    const states=rows.map(row=>['passed','current','next','upcoming'].includes(row.state)?row.state:'');
    const atStop=states.includes('current');
    const anyPassed=states.includes('passed');
    const firstActive=states.findIndex(state=>state==='current'||state==='next');
    const start=hasProgress&&firstActive>1 ? firstActive-1 : 0;
    const open=earlierStopsOpenFor!==null&&earlierStopsOpenFor===selectedId;
    const items=rows.map((row,index)=>{
      const state=states[index];
      const here=state==='next'&&!atStop&&anyPassed
        ? `<li class="train-here" aria-label="${esc(vehicle)} is here"><i></i><span><small>${esc(vehicle)} is here</small></span><b></b></li>`
        : '';
      const classes=[state,row.cancelled?'is-cancelled':'',index<start?'is-earlier':''].filter(Boolean).join(' ');
      return `${here}<li class="${classes}"${state==='current'?` aria-label="${esc(vehicle)} is at this stop"`:''}><i></i><span><strong>${esc(row.name)}</strong>${row.detailHtml?`<small>${row.detailHtml}</small>`:''}</span><b>${esc(row.booked||'')}${row.lateClock?`<em class="stop-late">${esc(row.lateClock)}</em>`:''}</b></li>`;
    });
    const toggle=start>0
      ? `<li class="earlier-toggle"><button type="button" data-show-earlier>${open?'Hide earlier stops':`Show ${start} earlier stop${start===1?'':'s'}`}</button></li>`
      : '';
    return `<ol class="transport-stops ${className}${hasProgress?' has-progress':''}${open?' earlier-open':''}">${toggle}${items.join('')}</ol>`;
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-show-earlier]');
    if(!button)return;
    const list=button.closest('ol');
    const open=!list.classList.contains('earlier-open');
    list.classList.toggle('earlier-open',open);
    earlierStopsOpenFor=open?selectedId:null;
    const hidden=list.querySelectorAll('li.is-earlier').length;
    button.textContent=open?'Hide earlier stops':`Show ${hidden} earlier stop${hidden===1?'':'s'}`;
  });

  // Leave now: pick the stop you are boarding at and how long it takes to
  // get there; the phone says when to set off, allowing for lateness.
  let leaveStops=[], leaveFormOpenFor=null, leaveMinutesDraft=15;
  function leaveDepartIso(value) {
    if(!value)return null;
    const text=String(typeof value==='object'?(value.at||value.scheduled||value.expected||value.time||''):value);
    if(text.includes('T')){const parsed=new Date(text);return Number.isFinite(parsed.getTime())?parsed.toISOString():null;}
    const match=text.match(/^(\d{1,2}):(\d{2})/);if(!match)return null;
    const when=new Date();when.setHours(Number(match[1]),Number(match[2]),0,0);
    if(Date.now()-when.getTime()>6*3600*1000)when.setDate(when.getDate()+1);
    return when.toISOString();
  }
  function leavePlans() { return (commuteAlertStatus.subscribed&&commuteAlertStatus.preferences?.leave_plans)||[]; }
  function leavePlanHtml(callingPoints) {
    if(!selectedId||!commuteAlertStatus.configured)return '';
    const plan=leavePlans().find(row=>row.train_id===selectedId);
    if(plan){
      const clock=new Date(plan.depart).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
      return `<p class="leave-plan is-set"><span><strong>You’ll be told when to leave</strong><small>For the ${esc(clock)} from ${esc(plan.station)}, allowing ${esc(plan.minutes)} min to get there.</small></span><button type="button" class="mini-button" data-leave-cancel>Cancel</button></p>`;
    }
    leaveStops=callingPoints.filter(point=>point?.state!=='passed'&&!point?.cancelled).map(point=>({
      name:providerName(point?.station||point?.location||point?.name),
      depart:leaveDepartIso(point?.scheduled||point?.expected||point?.time),
    })).filter(stop=>stop.name&&stop.depart&&new Date(stop.depart).getTime()>Date.now()).slice(0,-1);
    if(!leaveStops.length)return '';
    const options=leaveStops.map((stop,stopIndex)=>`<option value="${stopIndex}">${esc(humanTransitLabel(stop.name))} · ${esc(new Date(stop.depart).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}))}</option>`).join('');
    return `<details class="leave-plan"${leaveFormOpenFor===selectedId?' open':''}><summary>When should I leave?</summary>${alertsNeedHomeScreen()?HOME_SCREEN_HINT:''}<div class="leave-plan-form"><label><span>Boarding at</span><select data-leave-stop class="tool-select">${options}</select></label><label><span>Minutes to get there</span><input type="number" data-leave-minutes min="1" max="180" inputmode="numeric" value="${esc(leaveMinutesDraft)}"></label><button type="button" class="primary-button" data-leave-set>Tell me when to leave</button></div></details>`;
  }
  document.addEventListener('toggle',event=>{ if(event.target.matches?.('details.leave-plan'))leaveFormOpenFor=event.target.open?selectedId:null; },true);
  document.addEventListener('input',event=>{ if(event.target.matches?.('[data-leave-minutes]'))leaveMinutesDraft=Math.max(1,Math.min(180,Number(event.target.value)||15)); });
  document.addEventListener('click',async event=>{
    const set=event.target.closest?.('[data-leave-set]'),cancel=event.target.closest?.('[data-leave-cancel]');
    if(!set&&!cancel)return;
    const trainId=selectedId;if(!trainId)return;
    const others=leavePlans().filter(row=>row.train_id!==trainId);
    if(cancel){
      const ok=await savePushPreferences({...currentPushPreferences(),leave_plans:others});
      showToast(ok?'You won’t be told when to leave for this train.':'That couldn’t be changed right now.');
    } else {
      if(pushUnsupportedReason()){showToast(pushUnsupportedReason());return;}
      if(!requireAlertsPlus('When-to-leave alerts','leave-now'))return;
      const form=set.closest('.leave-plan-form');
      const stop=leaveStops[Number(form.querySelector('[data-leave-stop]').value)];
      const minutes=Math.max(1,Math.min(180,Number(form.querySelector('[data-leave-minutes]').value)||15));
      if(!stop)return;
      // The train is saved too, so a cancellation or platform change also reaches the phone.
      if(!followed.has(trainId)){followed.add(trainId);saveFollowed();renderMyRadar();$('follow').textContent='Saved ✓';}
      const train=activityById(trainId);
      const ok=await savePushPreferences({...currentPushPreferences(),leave_plans:[...others,{train_id:trainId,train_name:train?.name||'',station:humanTransitLabel(stop.name),minutes,depart:stop.depart}]});
      showToast(ok?`You’ll be told when to leave for ${humanTransitLabel(stop.name)}.`:'Alerts couldn’t be turned on. Check notifications are allowed for this site.');
      if(ok){leaveFormOpenFor=null;trackProduct('leave_plan_set','train');}
    }
    const live=activityById(trainId);
    if(live&&selectedId===trainId)loadActivityIntelligence(live,true);
  });

  function renderRailRun(run, index) {
    const train=run?.train && typeof run.train==='object' ? run.train : {};
    const progress=run?.progress && typeof run.progress==='object' ? run.progress : {};
    const next=providerName(progress.next_location || progress.next || run?.next_location);
    const serviceLabel=providerName(run?.service_name || run?.service_label);
    const callingPoints=railCallingPoints(run);
    const hasProgress=callingPoints.some(point=>point?.state);
    let atStation=callingPoints.some(point=>point?.state==='current');
    const anyPassed=callingPoints.some(point=>point?.state==='passed');
    // Always show the train somewhere: at the first stop before it leaves,
    // at the last stop once every stop is behind it.
    if(hasProgress&&!atStation){
      const allPassed=callingPoints.length&&callingPoints.every(point=>point?.state==='passed');
      const target=!anyPassed ? callingPoints[0] : (allPassed ? callingPoints[callingPoints.length-1] : null);
      if(target){callingPoints.splice(callingPoints.indexOf(target),1,{...target,state:'current'});atStation=true;}
    }
    const rows=callingPoints.map((point,pointIndex)=>{
      const name=providerName(point?.station || point?.location || point?.name);
      const booked=providerClock(point?.scheduled || point?.expected || point?.actual || point?.time);
      const expected=point?.scheduled&&point?.expected ? providerClock(point.expected) : '';
      const platform=providerName(point?.platform);
      const movedFrom=point?.platform_changed ? providerName(point?.booked_platform) : '';
      const cancelled=Boolean(point?.cancelled);
      const detailHtml=[
        cancelled ? '<em class="stop-cancelled">Cancelled</em>' : '',
        platform ? `<span class="${movedFrom?'platform-changed':''}">Platform ${esc(platform)}${movedFrom?` (was ${esc(movedFrom)})`:''}</span>` : '',
      ].filter(Boolean).join(' · ');
      if(!name && !booked)return null;
      return {name:name||`Call ${pointIndex+1}`,booked,lateClock:expected&&expected!==booked?expected:'',detailHtml,state:point?.state,cancelled};
    }).filter(Boolean);
    const points=stopTimelineHtml(rows,{vehicle:'Train',hasProgress,className:'train-full-route'});
    // Only facts a traveller uses: lateness, the stops and, for special
    // workings, what kind of service it is. The route is already the panel
    // subtitle; data-source and inference notes are not shown.
    const facts=points ? [] : [next?`Next: ${next}`:null].filter(Boolean);
    const unknown=!points ? '<p class="transport-empty">Stops not published for this service.</p>' : '';
    const cancelledFrom=providerName(run?.cancelled_from);
    const why=run?.delay_explanation;
    const whyText=why ? [
      `${why.late} min late.`,
      why.between ? `Most of it (${why.grew_by} min) was lost between ${humanTransitLabel(why.between[0])} and ${humanTransitLabel(why.between[1])}.`
        : why.near ? `${why.grew_by} min was lost near ${humanTransitLabel(why.near)}.`
        : why.already_late ? 'It was already late when it was first seen today.' : '',
      why.others_late ? `${why.others_late} other trains nearby are late too, so the problem is likely on the line rather than this train.` : '',
    ].filter(Boolean).join(' ') : '';
    const whyBox=whyText ? `<p class="delay-why"><strong>Why is it late?</strong> <span>${esc(whyText)}</span></p>` : '';
    // A "Special" or "Loco move" label should say what it means.
    const selected=activityById(selectedId);
    const headcode=String(selected?.headcode||run?.headcode||train.headcode||selected?.name||'').toUpperCase();
    const subtype=selected?coolInfo(selected).subtype:null;
    const specialText=subtype==='special-passenger'
      ? `${headcode.slice(0,4)}: a Z in the headcode marks a one-off working that is not in the regular timetable. On a normal operator's route like this it is usually an extra or relief train; it can also be a charter. It is not a railtour unless it says so.`
      : subtype==='locomotive-move' ? `${headcode.slice(0,4)}: a locomotive running on its own, with no passengers or wagons, usually to or from a depot or a job.` : '';
    const leaveBox=index===0&&!run?.cancelled&&!run?.freight ? leavePlanHtml(callingPoints) : '';
    const specialTitle=subtype==='special-passenger'&&coolInfo(selected).score<45?'Extra train':'Why is it special?';
    const specialBox=specialText ? `<p class="delay-why special-why"><strong>${specialTitle}</strong> <span>${esc(specialText)}</span></p>` : '';
    const cancelledBanner=run?.cancelled ? `<p class="train-cancelled-banner">${iconSvg('incident')}<span>${cancelledFrom?`Cancelled from ${esc(humanTransitLabel(cancelledFrom))}`:'This train is cancelled'}</span></p>` : '';
    const operator=providerName(train.operator || run?.operator || run?.operator_code);
    const operatorTheme=trainLiveryTheme('',operator);
    const operatorStyle=operatorTheme.key!=='reference' ? ` style="--train-primary:${operatorTheme.primary};--train-secondary:${operatorTheme.secondary};--train-accent:${operatorTheme.accent};--train-ink:${operatorTheme.ink}"` : '';
    return `<article class="transport-run-card${operatorStyle?' operator-themed':''}" data-livery-theme="${esc(operatorTheme.key)}"${operatorStyle}>${cancelledBanner}${specialBox}${whyBox}${leaveBox}${serviceLabel?`<small class="transport-service-label">${esc(serviceLabel)}</small>`:''}${facts.length?`<div class="transport-facts">${facts.map(value=>`<span>${esc(value)}</span>`).join('')}</div>`:''}${points}${unknown}</article>`;
  }

  function trainLiveryTheme(livery, operator) {
    const text=`${livery || ''} ${operator || ''}`.toUpperCase();
    const themes=[
      [/INTERCITY|INTER-CITY|SWALLOW/, ['#25272b','#f3ead7','#d7352a','#ffffff','intercity']],
      [/GREAT WESTERN|\bGWR\b/, ['#0b3b2e','#d7c7a0','#b99b5e','#ffffff','gwr']],
      [/LNER|EAST COAST/, ['#b5122b','#f4eee5','#121820','#ffffff','lner']],
      [/AVANTI/, ['#17233f','#7ac943','#ef8354','#ffffff','avanti']],
      [/LUMO/, ['#152b47','#4cbf5f','#f2f4f5','#ffffff','lumo']],
      [/GATWICK EXPRESS/, ['#b7192e','#f1f1ef','#1e252c','#ffffff','gatwick-express']],
      [/CALEDONIAN|SLEEPER/, ['#18253f','#c5a45a','#f5f0e4','#ffffff','caledonian']],
      [/SCOTRAIL/, ['#163b73','#f3f7fb','#56a0d3','#ffffff','scotrail']],
      [/NORTHERN/, ['#26205d','#00a7b5','#e85d9e','#ffffff','northern']],
      [/TRANSPENNINE|TPE/, ['#273264','#68c5d1','#eb4b80','#ffffff','transpennine']],
      [/CROSSCOUNTRY/, ['#5a214f','#e6a83e','#d5d8dc','#ffffff','crosscountry']],
      [/SOUTHEASTERN/, ['#243b7b','#63b645','#f2c84b','#ffffff','southeastern']],
      [/ISLAND LINE/, ['#1f2937','#5fb5a7','#f6f7f8','#ffffff','island-line']],
      [/SOUTH WESTERN|\bSWR\b/, ['#1f2937','#e44c65','#f6f7f8','#ffffff','south-western']],
      [/GREATER ANGLIA/, ['#cf202f','#ffffff','#1f4788','#ffffff','greater-anglia']],
      [/EAST MIDLANDS|\bEMR\b/, ['#4b2142','#f0e7ef','#e9518d','#ffffff','east-midlands']],
      [/WEST MIDLANDS (?:TRAINS|RAILWAY)/, ['#5a275f','#f1ecef','#ff8a3d','#ffffff','west-midlands']],
      [/LONDON NORTHWESTERN|\bLNWR\b/, ['#183f37','#f1f3ef','#b9d532','#ffffff','london-northwestern']],
      [/\bSOUTHERN\b/, ['#1c6b3a','#f3f5ef','#8cc63f','#ffffff','southern']],
      [/THAMESLINK/, ['#273b8f','#f2f4f7','#e85b27','#ffffff','thameslink']],
      [/GREAT NORTHERN/, ['#2b3f8f','#f2f4f7','#e6343b','#ffffff','great-northern']],
      [/CHILTERN/, ['#183b70','#f5f3ea','#65a9dd','#ffffff','chiltern']],
      [/HULL TRAINS/, ['#79245f','#f2ecef','#d3a340','#ffffff','hull-trains']],
      [/GRAND CENTRAL/, ['#1d252c','#f1f1ed','#e0a823','#ffffff','grand-central']],
      [/ELIZABETH LINE|CROSSRAIL/, ['#5b2d82','#f4f1f6','#b9a5ca','#ffffff','elizabeth-line']],
      [/LONDON OVERGROUND|OVERGROUND/, ['#e86a10','#f4f2eb','#2857a5','#ffffff','london-overground']],
      [/MERSEYRAIL/, ['#f1d431','#34383d','#ffffff','#15191d','merseyrail']],
      [/\bC2C\b/, ['#84247b','#f1f2f3','#d9da31','#ffffff','c2c']],
      [/HEATHROW EXPRESS/, ['#522b83','#f1eff5','#e8b82e','#ffffff','heathrow-express']],
      [/TRANSPORT FOR WALES|TRAFNIDIAETH CYMRU|\bTFW\b/, ['#d71920','#20282e','#f1eee8','#ffffff','transport-for-wales']],
      [/FREIGHTLINER/, ['#1c6333','#f2d447','#20282b','#ffffff','freightliner']],
      [/GB RAILFREIGHT|GBRF/, ['#f0b323','#253b77','#eef0f2','#15191d','gbrf']],
      [/DIRECT RAIL SERVICES|\bDRS\b/, ['#153d75','#f0f1e9','#65a9d8','#ffffff','drs']],
      [/RAIL OPERATIONS GROUP|\bROG\b/, ['#244c76','#f0efea','#ef6b21','#ffffff','rail-operations-group']],
      [/DC RAIL/, ['#25386b','#f0eee8','#d73d35','#ffffff','dc-rail']],
      [/DB CARGO|EWS/, ['#a51d2d','#f1c84b','#3b3e42','#ffffff','db-cargo']],
      [/COLAS/, ['#ed6b21','#f3cf35','#20262c','#ffffff','colas']],
      [/WEST COAST RAILWAYS|WCRC/, ['#6d2138','#ead9b5','#c89b3c','#ffffff','west-coast-railways']],
      [/LOCOMOTIVE SERVICES|LSL/, ['#1e3a62','#e8e3d5','#b39355','#ffffff','locomotive-services']],
      [/NETWORK RAIL|YELLOW/, ['#e5c928','#202936','#ffffff','#15191d','network-rail']],
      [/PULLMAN|CREAM.*BROWN|BROWN.*CREAM/, ['#4b241b','#ead9b4','#b08c55','#ffffff','pullman']],
      [/BR BLUE|BLUE.*GREY|BLUE.*GRAY/, ['#24508a','#d8d8d2','#f0c735','#ffffff','br-blue']],
      [/BR GREEN|DARK GREEN/, ['#174c38','#e7d8a8','#d5ad3d','#ffffff','br-green']],
      [/MAROON|CRIMSON/, ['#711f31','#e7d6b5','#c89b3c','#ffffff','maroon']],
      [/ORANGE/, ['#d86619','#202832','#f3c84b','#ffffff','orange']],
      [/PURPLE/, ['#5a357c','#d6c6e8','#f1bc4a','#ffffff','purple']],
      [/RED/, ['#9f2432','#e5e7e9','#f0c64a','#ffffff','red']],
      [/GREEN/, ['#235f43','#d8e1d7','#e0bf4d','#ffffff','green']],
      [/BLUE/, ['#285184','#d9e3ed','#e4bc46','#ffffff','blue']],
    ];
    const matched=themes.find(([pattern])=>pattern.test(text));
    const [primary,secondary,accent,ink,key]=matched?.[1] || ['#293744','#c8d4dc','#efc573','#ffffff','reference'];
    return {primary,secondary,accent,ink,key};
  }

  function trainLiveryStyle(stock) {
    const theme=trainLiveryTheme(stock?.livery,stock?.operator || stock?.owner);
    return {
      key:theme.key,
      css:`--train-primary:${theme.primary};--train-secondary:${theme.secondary};--train-accent:${theme.accent};--train-ink:${theme.ink}`,
    };
  }

  // Delay Repay: passengers on a train 15+ minutes late (or cancelled) can
  // usually claim part of their fare back from the operator.
  function delayRepayHtml(run) {
    if(!run||run.freight)return '';
    const head=String(run.headcode||'').toUpperCase();
    if(!/^[129]/.test(head)||/^1Z/.test(head))return '';
    const delay=Number(run.delay_minutes);
    const cancelled=Boolean(run.cancelled);
    if(!cancelled&&!(Number.isFinite(delay)&&delay>=15))return '';
    const operator=humanTransitLabel(run.operator||'')||'the train company';
    const band=cancelled?'Cancelled trains count too, if it delays your journey.'
      :delay>=120?'At 2 hours or more, most operators refund the whole return fare.'
      :delay>=60?'At 1 hour or more, most operators refund the whole single fare.'
      :delay>=30?'At 30 minutes or more, most operators refund half of a single fare.'
      :'From 15 minutes, most operators refund a quarter of a single fare.';
    return `<section class="delay-repay" aria-label="Delay Repay"><strong>${cancelled?'Cancelled':`Running ${esc(delay)} min late`}: you may be owed money back</strong><span>${esc(band)} Claims go to ${esc(operator)} through Delay Repay, usually within 28 days; keep your ticket.</span><a class="secondary-button" href="/go/delay-repay" target="_blank" rel="nofollow noopener">How to claim Delay Repay</a></section>`;
  }

  // A drawn side view of the train, in its operator's colours: its coaches
  // from National Rail (Darwin) when published, else its class's usual length
  // with a note saying so. Replaces the photo: Commons rarely has the right unit.
  const WHEELCHAIR_ICON='<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="4" r="1.8"/><path d="M11 7v6h5l2 5M11 10h4M8.5 11.5A5 5 0 1 0 15 18"/></svg>';
  const TRAIN_FACILITY_ICONS={
    air_conditioning:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2v20M4 7l16 10M20 7L4 17M8 4l4 3 4-3M8 20l4-3 4 3M3 11l4 1-1 4M21 11l-4 1 1 4"/></svg>',
    wifi:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9a14 14 0 0 1 18 0M6 13a9 9 0 0 1 12 0M9.5 17a4 4 0 0 1 5 0"/><circle cx="12" cy="20" r="1" fill="currentColor" stroke="none"/></svg>',
    bicycle_spaces:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="17" r="4"/><circle cx="18" cy="17" r="4"/><path d="m6 17 4-8 4 8H6l4-5h5l3 5M9 6h4"/></svg>',
    wheelchair_spaces:WHEELCHAIR_ICON,
    toilets:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h10v5a5 5 0 0 1-10 0V3ZM5 8v2a7 7 0 0 0 14 0V8M8 18h8v3H8z"/></svg>',
  };
  const TRAIN_FACILITY_LABELS={air_conditioning:'Air conditioning',wifi:'Wi-Fi',bicycle_spaces:'Cycle spaces',wheelchair_spaces:'Wheelchair space',toilets:'Toilets'};

  function trainFacilitiesHtml(formation){
    const facilities=formation?.facilities||{};
    const entries=Object.keys(TRAIN_FACILITY_LABELS).filter(key=>facilities[key]===true);
    if(!entries.length)return '';
    const badges=entries.map(key=>`<span class="train-facility" title="${esc(TRAIN_FACILITY_LABELS[key])}">${TRAIN_FACILITY_ICONS[key]}<span>${esc(TRAIN_FACILITY_LABELS[key])}</span></span>`).join('');
    return `<div class="train-facilities" aria-label="On-board facilities">${badges}</div>`;
  }
  // Generated broadside profiles, cut into one image per coach
  // (scripts/build_train_profiles.py): [folder, width of each coach image at
  // 96 px high]. The timetable chooses the class; the operator the livery.
  const TRAIN_PROFILE_SETS={
    '142':{northern:['class-142-northern-2',[360,364]],default:['class-142-northern-2',[360,364]]},
    '143':{'transport-for-wales':['class-143-transport-for-wales-2',[341,348]],default:['class-143-transport-for-wales-2',[341,348]]},
    '144':{northern:['class-144-northern-2',[357,362]],default:['class-144-northern-2',[357,362]]},
    '139':{'west-midlands':['class-139-west-midlands-1',[374]]},
    '390':{default:['class-390-pendolino',[292,165,161,154,147,162,283]]},
    '170':{crosscountry:['class-170-crosscountry-turbostar',[268,254,278]],scotrail:['class-170-scotrail-3',[344,364,354]],northern:['class-170-northern-3',[290,259,283]],'west-midlands':['class-170-west-midlands-3',[346,291,328]],'transport-for-wales':['class-170-transport-for-wales-3',[387,355,362]],'east-midlands':['class-170-east-midlands-3',[262,246,270]],chiltern:['class-170-chiltern-3',[260,257,253]]},
    '195':{northern:['class-195-northern-civity',[354,319,354]]},
    '220':{crosscountry:['class-220-crosscountry-voyager',[453,314,432]]},
    '221':{crosscountry:['class-220-crosscountry-voyager',[453,314,432]]},
    '222':{'east-midlands':['class-222-emr-meridian',[286,225,244,222,285]]},
    '350':{'london-northwestern':['class-350-lnwr-desiro',[252,194,205,252]],'west-midlands':['class-350-lnwr-desiro',[252,194,205,252]]},
    '800':{lner:['class-800-lner-azuma',[215,187,176,171,206]],gwr:['class-800-gwr-iet',[292,216,263,196,281]]},
    '801':{lner:['class-800-lner-azuma',[215,187,176,171,206]]},
    '802':{gwr:['class-800-gwr-iet',[292,216,263,196,281]],transpennine:['class-802-transpennine-5',[349,243,252,226,248]],'hull-trains':['class-802-hull-trains-5',[368,259,280,249,264]]},
    '810':{'east-midlands':['class-810-emr-aurora',[275,209,245,202,267]]},
    '150':{'northern':['class-150-northern',[367,368]],'transport-for-wales':['class-150-transport-for-wales-2',[386,389]],gwr:['class-150-gwr-2',[389,389]]},
    '158':{'northern':['class-158-northern',[377,375]],scotrail:['class-158-scotrail-2',[424,406]],'transport-for-wales':['class-158-transport-for-wales-2',[422,407]],gwr:['class-158-gwr-2',[431,406]],'east-midlands':['class-158-east-midlands-2',[436,432]],'south-western':['class-158-south-western-2',[435,431]]},
    '159':{'south-western':['class-159-south-western',[317,280,328]]},
    '165':{'gwr':['class-165-gwr',[317,303,316]],chiltern:['class-165-chiltern-3',[317,302,318]]},
    '166':{'gwr':['class-165-gwr',[317,303,316]]},
    '168':{'chiltern':['class-168-chiltern',[291,267,263,292]]},
    '185':{'transpennine':['class-185-transpennine',[274,311,266]]},
    '196':{'west-midlands':['class-196-west-midlands',[288,230,239,287]]},
    '197':{'transport-for-wales':['class-197-transport-for-wales',[261,279,264]]},
    '345':{'elizabeth-line':['class-345-elizabeth-line',[222,142,135,133,146,126,137,128,223]]},
    '377':{'southern':['class-377-southern',[302,277,267,300]],southeastern:['class-377-southeastern-4',[289,263,263,276]]},
    '379':{'great-northern':['class-379-great-northern-4',[247,218,210,245]]},
    '385':{'scotrail':['class-385-scotrail',[203,167,167,213]]},
    '395':{'southeastern':['class-395-southeastern',[251,180,190,184,186,250]]},
    '444':{'south-western':['class-444-south-western',[286,260,263,262,283]]},
    '700':{'thameslink':['class-700-thameslink',[225,123,133,125,129,125,125,223]],'great-northern':['class-700-thameslink',[225,123,133,125,129,125,125,223]]},
    '720':{'greater-anglia':['class-720-greater-anglia',[197,198,224,188,208]],c2c:['class-720-c2c-5',[223,187,188,183,176]]},
    '153':{'transport-for-wales':['class-153-transport-for-wales-1',[432]],'east-midlands':['class-153-east-midlands-1',[408]]},
    '155':{northern:['class-155-northern-2',[427,414]]},
    '156':{northern:['class-156-northern-2',[360,370]],scotrail:['class-156-scotrail-2',[441,421]]},
    '171':{southern:['class-171-southern-2',[419,348]]},
    '172':{'west-midlands':['class-172-west-midlands-2',[419,406]],chiltern:['class-172-chiltern-2',[412,393]]},
    '175':{'transport-for-wales':['class-175-transport-for-wales-3',[281,495,307]]},
    '180':{'grand-central':['class-180-grand-central-5',[375,272,265,262,347]]},
    '230':{'transport-for-wales':['class-230-transport-for-wales-3',[295,257,262]]},
    '231':{'transport-for-wales':['class-231-transport-for-wales-4',[347,246,262,291]]},
    '318':{scotrail:['class-318-scotrail-3',[253,233,244]]},
    '320':{scotrail:['class-320-scotrail-3',[295,283,294]]},
    '321':{'greater-anglia':['class-321-greater-anglia-4',[251,225,216,255]],default:['class-321-greater-anglia-4',[251,225,216,255]]},
    '323':{northern:['class-323-northern-3',[267,250,256]],'west-midlands':['class-323-west-midlands-3',[257,262,266]]},
    '331':{northern:['class-331-northern-3',[245,229,241]]},
    '333':{northern:['class-333-northern-4',[254,189,204,204]]},
    '334':{scotrail:['class-334-scotrail-3',[249,214,250]]},
    '357':{c2c:['class-357-c2c-4',[209,180,187,186]]},
    '360':{'east-midlands':['class-360-east-midlands-4',[304,249,252,260]]},
    '365':{'great-northern':['class-365-great-northern-4',[296,232,232,287]],default:['class-365-great-northern-4',[296,232,232,287]]},
    '375':{southeastern:['class-375-southeastern-4',[320,231,248,267]]},
    '376':{southeastern:['class-376-southeastern-4',[246,198,216,206]]},
    '378':{'london-overground':['class-378-london-overground-5',[237,189,196,197,211]]},
    '380':{scotrail:['class-380-scotrail-3',[253,250,241]]},
    '387':{'great-northern':['class-387-great-northern-4',[266,215,218,229]],southern:['class-387-southern-4',[235,200,219,208]],gwr:['class-387-gwr-4',[224,213,226,219]],'gatwick-express':['class-387-gatwick-express-4',[269,220,228,271]],'heathrow-express':['class-387-heathrow-express-4',[259,209,212,224]]},
    '397':{transpennine:['class-397-transpennine-5',[250,193,184,188,209]]},
    '398':{'transport-for-wales':['class-398-transport-for-wales-3',[246,179,247]]},
    '399':{default:['class-399-stagecoach-supertram-3',[200,169,178]]},
    '450':{'south-western':['class-450-south-western-4',[307,274,268,270]]},
    '455':{'south-western':['class-455-south-western-4',[306,269,263,320]]},
    '456':{'south-western':['class-456-south-western-2',[428,421]],default:['class-456-south-western-2',[428,421]]},
    '458':{'south-western':['class-458-south-western-4',[302,268,274,306]]},
    '465':{southeastern:['class-465-southeastern-4',[279,243,241,246]]},
    '466':{southeastern:['class-466-southeastern-4',[304,270,260,271]]},
    '484':{'island-line':['class-484-island-line-2',[368,368]]},
    '507':{merseyrail:['class-507-merseyrail-3',[354,348,354]],default:['class-507-merseyrail-3',[354,348,354]]},
    '508':{merseyrail:['class-508-merseyrail-3',[376,375,382]],default:['class-508-merseyrail-3',[376,375,382]]},
    '701':{'south-western':['class-701-south-western-5',[293,247,249,246,240]]},
    '707':{southeastern:['class-707-southeastern-5',[223,179,186,185,223]],'south-western':['class-707-south-western-5',[294,212,226,212,259]]},
    '710':{'london-overground':['class-710-london-overground-5',[225,203,214,192,210]]},
    '717':{'great-northern':['class-717-great-northern-6',[223,176,164,162,183,182]]},
    '730':{'west-midlands':['class-730-west-midlands-5',[179,130,144,135,180]],'london-northwestern':['class-730-london-northwestern-5',[201,168,172,183,198]]},
    '755':{'greater-anglia':['class-755-greater-anglia-4',[353,251,228,351]]},
    '756':{'transport-for-wales':['class-756-transport-for-wales-4',[341,283,279,302]]},
    '769':{'transport-for-wales':['class-769-transport-for-wales-4',[297,259,259,267]],gwr:['class-769-gwr-4',[216,212,211,210]]},
    '777':{merseyrail:['class-777-merseyrail-4',[257,241,234,255]]},
    '803':{lumo:['class-803-lumo-5',[294,225,235,211,293]]},
    '805':{avanti:['class-805-avanti-evero-5',[342,272,262,257,259]]},
    '807':{avanti:['class-807-avanti-evero-7',[275,198,158,172,169,184,212]]},
  };
  // Whole-formation assets supplement the newer coach-by-coach sets. They are
  // selected only from an explicit timetable class or service description.
  const WHOLE_TRAIN_PROFILE_IMAGES={
    // The articulated FLIRT has no clean gap between carriages to cut at.
    '745':{'greater-anglia':'class-745-greater-anglia.webp'},
    '43':{scotrail:'class-43-scotrail-inter7city.webp',gwr:'class-43-gwr-castle.webp'},
  };
  const SPECIAL_TRAIN_PROFILE_IMAGES=[
    [/\b(?:LONDON\s+UNDERGROUND|TUBE|UNDERGROUND)\b/i,'london-underground.webp'],[/\b(?:CROYDON|TRAMLINK)\b/i,'tram-croydon.webp'],
    [/\b(?:MANCHESTER|METROLINK)\b/i,'tram-manchester.webp'],[/\bEDINBURGH\s+TRAM/i,'tram-edinburgh.webp'],
    [/\b(?:NOTTINGHAM|NET)\s+TRAM/i,'tram-nottingham.webp'],[/\b(?:SLEEPER|CALEDONIAN)\b/i,'sleeper-train.webp'],
    [/\b(?:STEAM|HERITAGE|CHARTER)\b/i,'heritage-steam-charter.webp'],[/\b(?:ENGINEERING|ON[- ]TRACK|MAINTENANCE)\b/i,'rail-engineering-machine.webp'],
    [/\b(?:POSTAL|MAIL)\b/i,'postal-train.webp'],
  ];
  function wholeTrainProfileImage(formation,operator){
    const identity=[formation?.name,formation?.type,formation?.description].filter(Boolean).join(' ');
    const special=SPECIAL_TRAIN_PROFILE_IMAGES.find(([pattern])=>pattern.test(identity));
    if(special)return `/static/assets/train-profiles/${special[1]}`;
    const choices=WHOLE_TRAIN_PROFILE_IMAGES[String(formation?.class||'')];
    const livery=trainLiveryTheme(null,formation?.operator||operator).key;
    const file=choices?.[livery]||choices?.default;
    return file?`/static/assets/train-profiles/${file}`:null;
  }
  function trainProfileSet(formation,operator){
    const choices=TRAIN_PROFILE_SETS[String(formation?.class||'')];
    if(!choices)return null;
    // The live feed sometimes supplies the owning company rather than the
    // customer-facing Thameslink brand. Class 700 is the Thameslink fleet, so
    // this is an identity alias rather than a guessed livery.
    if(String(formation?.class||'')==='700'&&/GOVIA THAMESLINK/i.test(String(formation?.operator||operator||'')))return choices.thameslink;
    const livery=trainLiveryTheme(null,formation?.operator||operator).key;
    return choices[livery]||choices.default||null;
  }
  // Which cut coach image draws each of `count` coaches, left to right: whole
  // units when the train is a multiple of the drawn unit (two cab ends meet in
  // the middle, as when units couple), else the drawn unit's ends with its
  // middle coaches repeated to the right length.
  function profilePieces(pieces,count){
    if(count<=1)return [0];
    if(count%pieces===0)return Array.from({length:count},(_,i)=>i%pieces);
    const middles=pieces>2?Array.from({length:pieces-2},(_,i)=>i+1):[0];
    return [0,...Array.from({length:count-2},(_,i)=>middles[i%middles.length]),pieces-1];
  }
  function coachMarksHtml(coach){
    return [coach.first?'<b class="coach-first" title="First class">1st</b>':'',
      coach.toilet==='accessible'?`<span class="coach-accessible" title="Accessible toilet">${WHEELCHAIR_ICON}</span>`:coach.toilet==='standard'?'<b class="coach-wc" title="Toilet">WC</b>':'',
      coach.bicycle?`<span title="Cycle spaces">${TRAIN_FACILITY_ICONS.bicycle_spaces}</span>`:'',
      coach.wheelchair_space&&coach.toilet!=='accessible'?`<span title="Wheelchair space">${WHEELCHAIR_ICON}</span>`:''].join('');
  }
  function profileTrainHtml(set,coaches){
    const [folder,widths]=set;
    const pieces=profilePieces(widths.length,coaches.length);
    const labelled=coaches.some(coach=>coach.letter||coach.first||coach.toilet||coach.bicycle||coach.wheelchair_space);
    const cells=coaches.map((coach,i)=>{
      const piece=pieces[i];
      const label=coach.letter?`Coach ${coach.letter}${coach.first?', first class':''}${coach.toilet==='accessible'?', accessible toilet':coach.toilet==='standard'?', toilet':''}${coach.bicycle?', cycle spaces':''}${coach.wheelchair_space?', wheelchair space':''}`:'';
      return `<figure class="profile-coach" style="flex:${widths[piece]} 1 0"${label?` aria-label="${esc(label)}"`:''}>${labelled?`<small>${esc(coach.letter||'')}</small>`:''}<img src="/static/assets/train-profiles/${folder}/${piece}.webp" width="${widths[piece]}" height="96" alt="" loading="eager" fetchpriority="high" decoding="async">${labelled?`<span class="coach-marks">${coachMarksHtml(coach)}</span>`:''}</figure>`;
    }).join('');
    // Keep every broadside tall enough to read.  The old 18 px scale made a
    // nine/eleven-car Pendolino roughly a third of the height of a three-car
    // unit.  Long formations deliberately scroll instead of being crushed to
    // fit the card.
    const total=pieces.reduce((sum,piece)=>sum+widths[piece],0);
    return `<div class="profile-scroll"><div class="profile-train${labelled?' is-labelled':''}" style="min-width:${Math.round(total*44/96)}px">${cells}</div></div>`;
  }
  function pendolinoCoachSvg(index,count){
    const first=index===0,last=index===count-1;
    // Class 390: low, continuously curved bodyside, dark window ribbon,
    // orange-red driving noses and the distinctive Avanti green sweep.
    const body=first
      ? 'M18 7 Q7 9 3 20 L3 29 Q3 34 10 35 L118 35 L118 8 Z'
      : last
        ? 'M2 8 L2 35 L110 35 Q117 34 117 29 L117 20 Q113 9 102 7 Z'
        : 'M2 8 Q60 4 118 8 L118 35 L2 35 Z';
    const windowStart=first?25:8, windowEnd=last?95:112;
    const windows=[];
    for(let x=windowStart;x<windowEnd;x+=12) windows.push(`<rect x="${x}" y="13" width="8" height="8" rx="2"/>`);
    const cab=first
      ? '<path d="M5 21 Q8 12 18 10 L21 21 Z" fill="#17233f"/><path d="M3 23 Q9 18 20 18 L20 35 L9 35 Q3 33 3 29 Z" fill="#ef6548"/>'
      : last
        ? '<path d="M115 21 Q112 12 102 10 L99 21 Z" fill="#17233f"/><path d="M117 23 Q111 18 100 18 L100 35 L111 35 Q117 33 117 29 Z" fill="#ef6548"/>'
        : '';
    const doorX=first?91:last?23:(index%2?88:24);
    const pantograph=(index===Math.floor(count*.36)||index===Math.floor(count*.72))
      ? '<path d="M45 7 L55 1 L65 7 M48 1 H62" fill="none" stroke="#353b42" stroke-width="1.5"/>' : '';
    return `<svg class="coach-svg pendolino-svg" viewBox="0 0 120 44" preserveAspectRatio="none" aria-hidden="true">${pantograph}<path d="${body}" fill="#e9ecee" stroke="#aeb5ba" stroke-width=".8"/><path d="M${first?12:2} 10 Q60 6 ${last?108:118} 10" fill="none" stroke="#c7ccd0" stroke-width="2"/><rect x="${first?18:2}" y="11" width="${120-(first?18:2)-(last?18:2)}" height="13" rx="3" fill="#27313b"/><g fill="#111922">${windows.join('')}</g><rect x="${doorX}" y="11" width="7" height="23" rx="2" fill="#ef6548"/><path d="M${first?20:2} 28 Q60 22 ${last?100:118} 27" fill="none" stroke="#72bf44" stroke-width="2.6"/>${cab}<rect x="${first?9:2}" y="34" width="${120-(first?9:2)-(last?9:2)}" height="2" fill="#58616a"/><g fill="#30363c"><circle cx="${first?27:18}" cy="39" r="3.2"/><circle cx="${last?93:102}" cy="39" r="3.2"/></g></svg>`;
  }

  function trainCoachSvg(index,count,theme,trainClass){
    if(String(trainClass||'')==='390')return pendolinoCoachSvg(index,count);
    const first=index===0,last=index===count-1;
    // Body with a sloped cab at each end of the train.
    const left=first?'M14 6 Q4 8 2 20 L2 30 Q2 34 6 34':'M3 6 L3 34';
    const right=last?'L94 34 Q98 34 98 30 L98 20 Q96 8 86 6 Z':'L97 34 L97 6 Z';
    const body=`${left} ${right}`;
    const windows=[];
    for(let x=first?20:10;x<(last?78:88);x+=14)windows.push(`<rect x="${x}" y="11" width="10" height="8" rx="1.5"/>`);
    const doors=[22,72].map(x=>`<rect x="${x}" y="10" width="6" height="22" rx="1" fill="${theme.accent}"/>`).join('');
    const cab=(first?`<path d="M5 19 Q7 11 13 10 L13 19 Z" fill="#1f2a33"/>`:'')+(last?`<path d="M95 19 Q93 11 87 10 L87 19 Z" fill="#1f2a33"/>`:'');
    return `<svg class="coach-svg" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><path d="${body}" fill="${theme.primary}"/><rect x="${first?4:3}" y="22" width="${100-(first?4:3)-(last?4:3)}" height="3" fill="${theme.secondary}"/><g fill="#1f2a33">${windows.join('')}</g>${cab}${doors}<rect x="${first?6:3}" y="31" width="${100-(first?6:3)-(last?6:3)}" height="3" fill="rgba(0,0,0,.35)"/><circle cx="22" cy="37" r="2.6" fill="#3a3f45"/><circle cx="78" cy="37" r="2.6" fill="#3a3f45"/></svg>`;
  }
  // Freight: an illustrative locomotive and wagons for what the train is
  // timetabled to carry (CIF train category). Generic drawings, not this train.
  const WAGON_UNDER='<rect x="5" y="30" width="90" height="3" fill="#2d3238"/><circle cx="20" cy="36" r="3.2" fill="#2d3238"/><circle cx="80" cy="36" r="3.2" fill="#2d3238"/>';
  const CONTAINER_COLOURS=['#2f5d8a','#a83b32','#2f6b4a','#d9822b','#e4e2db','#5b6168'];
  function wagonSvg(kind,index){
    const box=(x,w,fill)=>`<rect x="${x}" y="11" width="${w}" height="16" rx="1" fill="${fill}"/>`+Array.from({length:Math.floor(w/6)},(_,i)=>`<line x1="${x+3+i*6}" y1="12" x2="${x+3+i*6}" y2="26" stroke="rgba(0,0,0,.18)" stroke-width="1"/>`).join('');
    const shapes={
      coal:'<path d="M6 11 L94 11 L88 30 L12 30 Z" fill="#5b6067"/><path d="M7 11 Q50 1 93 11 Z" fill="#141618"/><rect x="6" y="11" width="88" height="2" fill="#41464c"/>',
      aggregate:'<rect x="6" y="12" width="88" height="18" rx="1" fill="#6f7c88"/><path d="M8 12 Q50 3 92 12 Z" fill="#b5ab97"/><g stroke="rgba(0,0,0,.2)"><line x1="30" y1="13" x2="30" y2="29"/><line x1="50" y1="13" x2="50" y2="29"/><line x1="70" y1="13" x2="70" y2="29"/></g>',
      containers:`<rect x="4" y="27" width="92" height="3" fill="#41464c"/>${box(6,42,CONTAINER_COLOURS[(index*2)%6])}${box(52,42,CONTAINER_COLOURS[(index*2+1)%6])}`,
      cars:'<rect x="5" y="4" width="90" height="26" rx="3" fill="#c3c9cf"/><rect x="9" y="8" width="82" height="6" rx="1" fill="#8d959d"/><rect x="9" y="18" width="82" height="6" rx="1" fill="#8d959d"/><g fill="#d24a3a"><rect x="14" y="10" width="14" height="4" rx="2"/><rect x="58" y="20" width="14" height="4" rx="2"/></g><g fill="#3f6fa3"><rect x="40" y="10" width="14" height="4" rx="2"/><rect x="20" y="20" width="14" height="4" rx="2"/></g>',
      tank:'<rect x="6" y="11" width="88" height="17" rx="8.5" fill="#25292d"/><rect x="45" y="7" width="10" height="5" rx="1" fill="#25292d"/><rect x="6" y="18" width="88" height="2" fill="#f2c84b"/>',
      chemical:'<rect x="6" y="11" width="88" height="17" rx="8.5" fill="#e6e6e1"/><rect x="45" y="7" width="10" height="5" rx="1" fill="#c9c9c3"/><rect x="6" y="18" width="88" height="2" fill="#d9822b"/>',
      metals:'<rect x="4" y="25" width="92" height="5" fill="#41464c"/><g fill="#9aa3ab" stroke="#6d757d" stroke-width="1.5"><circle cx="24" cy="18" r="7"/><circle cx="50" cy="18" r="7"/><circle cx="76" cy="18" r="7"/></g>',
      ballast:'<path d="M6 12 L94 12 L88 30 L12 30 Z" fill="#e5c928"/><path d="M8 12 Q50 4 92 12 Z" fill="#8f8c86"/>',
      van:'<rect x="5" y="8" width="90" height="22" rx="3" fill="#7d8894"/><rect x="40" y="11" width="20" height="17" fill="#6c7782"/>',
      brake:'<rect x="22" y="12" width="56" height="18" rx="2" fill="#7a3b2e"/><rect x="14" y="27" width="72" height="3" fill="#2d3238"/><rect x="30" y="15" width="8" height="6" fill="#1f2a33"/><rect x="62" y="15" width="8" height="6" fill="#1f2a33"/>',
    };
    return `<svg class="coach-svg" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">${shapes[kind]||shapes.van}${WAGON_UNDER}</svg>`;
  }
  function locoSvg(theme,electric){
    const pantograph=electric?'<path d="M40 5 L50 1 L60 5" fill="none" stroke="#2d3238" stroke-width="1.6"/><rect x="38" y="5" width="24" height="1.5" fill="#2d3238"/>':'';
    return `<svg class="coach-svg" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">${pantograph}<path d="M8 7 L92 7 Q97 8 97 14 L97 30 L3 30 L3 14 Q3 8 8 7 Z" fill="${theme.primary}"/><rect x="3" y="21" width="94" height="3" fill="${theme.secondary}"/><path d="M5 10 L14 10 L14 18 L4 18 Z" fill="#1f2a33"/><path d="M95 10 L86 10 L86 18 L96 18 Z" fill="#1f2a33"/><g stroke="rgba(0,0,0,.28)" stroke-width="1">${[26,29,32,35,62,65,68,71].map(x=>`<line x1="${x}" y1="11" x2="${x}" y2="18"/>`).join('')}</g><rect x="44" y="10" width="12" height="9" rx="1" fill="rgba(0,0,0,.15)"/><rect x="3" y="30" width="94" height="3" fill="#2d3238"/><g fill="#2d3238"><circle cx="13" cy="36" r="3"/><circle cx="23" cy="36" r="3"/><circle cx="77" cy="36" r="3"/><circle cx="87" cy="36" r="3"/></g></svg>`;
  }
  const FREIGHT_WAGONS={A0:'coal',E0:'coal',B0:'coal',B1:'metals',B4:'aggregate',B5:'containers',B6:'aggregate',B7:'tank',
    H0:'containers',H1:'containers',H2:'cars',H3:'cars',H4:'containers',H5:'containers',H6:'containers',H8:'containers',H9:'containers',
    J2:'van',J3:'containers',J4:'aggregate',J5:'chemical',J6:'aggregate',J8:'containers',J9:'containers',
    DD:'ballast',DH:'ballast',DI:'ballast',DQ:'ballast',DY:'ballast',PP:'van',PM:'van',JJ:'van',PV:'van'};
  const FREIGHT_PROFILE_IMAGES={
    containers:'/static/assets/train-profiles/freight-wagon-intermodal.webp',
    tank:'/static/assets/train-profiles/freight-wagon-tank.webp',
    chemical:'/static/assets/train-profiles/freight-wagon-tank.webp',
    aggregate:'/static/assets/train-profiles/freight-wagon-aggregate.webp',
    coal:'/static/assets/train-profiles/freight-wagon-aggregate.webp',
    ballast:'/static/assets/train-profiles/freight-wagon-aggregate.webp',
    metals:'/static/assets/train-profiles/freight-wagon-steel.webp',
    cars:'/static/assets/train-profiles/freight-wagon-automotive.webp',
    biomass:'/static/assets/train-profiles/freight-wagon-biomass.webp',
    nuclear:'/static/assets/train-profiles/freight-wagon-nuclear-flask.webp',
    timber:'/static/assets/train-profiles/freight-wagon-timber.webp',
    engineering:'/static/assets/train-profiles/freight-wagon-engineering.webp',
  };
  const FREIGHT_LOCOMOTIVE_IMAGES={
    '37':{'rail-operations-group':'/static/assets/train-profiles/freight-class-37-rail-operations-group.webp'},
    '60':{'db-cargo':'/static/assets/train-profiles/freight-class-60-db-cargo.webp'},
    '66':{
      'db-cargo':'/static/assets/train-profiles/freight-class-66-db-cargo.webp',
      freightliner:'/static/assets/train-profiles/freight-class-66-freightliner.webp',
      gbrf:'/static/assets/train-profiles/freight-class-66-gbrf.webp',
    },
    '68':{drs:'/static/assets/train-profiles/freight-class-68-drs.webp'},
    '69':{gbrf:'/static/assets/train-profiles/freight-class-69-gbrf.webp'},
    '70':{
      freightliner:'/static/assets/train-profiles/freight-class-70-freightliner.webp',
      colas:'/static/assets/train-profiles/freight-class-70-colas.webp',
    },
    '90':{freightliner:'/static/assets/train-profiles/freight-class-90-freightliner.webp'},
    '92':{gbrf:'/static/assets/train-profiles/freight-class-92-gbrf.webp'},
  };
  // The feeds rarely give the exact locomotive: the freight card is an
  // illustration, so it shows the operator's usual type (the most common
  // freight locomotive when the operator is not known).
  const TYPICAL_FREIGHT_LOCO={
    'db-cargo':['66','90'],freightliner:['66','90'],gbrf:['66','92'],drs:['68','68'],
    colas:['70','70'],'rail-operations-group':['37','37'],
  };
  function typicalFreightLoco(theme,electric){
    // A known operator with no picture (Colas, ROG...) keeps the drawn
    // locomotive in its own colours rather than another company's.
    if(!TYPICAL_FREIGHT_LOCO[theme.key]&&theme.key!=='reference')return null;
    const [diesel,electricClass]=TYPICAL_FREIGHT_LOCO[theme.key]||['66'];
    const key=TYPICAL_FREIGHT_LOCO[theme.key]?theme.key:'db-cargo';
    return FREIGHT_LOCOMOTIVE_IMAGES[electric&&electricClass?electricClass:diesel]?.[key]
      ||FREIGHT_LOCOMOTIVE_IMAGES[diesel]?.[key]||FREIGHT_LOCOMOTIVE_IMAGES['66']['db-cargo'];
  }
  function freightVehicleHtml(kind,index,theme,electric,isLoco=false,exactClass=null,trainKind=null){
    const image=isLoco
      ?(FREIGHT_LOCOMOTIVE_IMAGES[exactClass]?.[theme.key]||(trainKind==='engineering'?'/static/assets/train-profiles/rail-engineering-machine.webp':typicalFreightLoco(theme,electric)))
      :FREIGHT_PROFILE_IMAGES[kind];
    const visual=image?`<img class="freight-profile-asset" src="${image}" alt="" loading="lazy" decoding="async">`
      :isLoco?locoSvg(theme,electric):wagonSvg(kind,index);
    return `<div class="coach"><small></small>${visual}<div class="coach-marks"></div></div>`;
  }
  function freightProfileHtml(freight, operator, vehicleRef) {
    if(!freight)return '';
    const theme=trainLiveryTheme(null, operator);
    const code=String(freight.category||'').toUpperCase();
    const electric=/electric/i.test(String(freight.traction||''))&&!/diesel/i.test(String(freight.traction||''));
    const cargo=String(freight.cargo||'');
    const kind=/biomass/i.test(cargo)?'biomass':/nuclear|flask/i.test(cargo)?'nuclear':/timber|logs?/i.test(cargo)?'timber':/engineering|railhead|infrastructure/i.test(cargo)?'engineering':FREIGHT_WAGONS[code];
    // Light engines run alone; a locomotive and brake van is just that.
    const wagons=code==='ZZ'?[]:code==='ZB'?['brake']:Array.from({length:5},()=>kind||'van');
    const exactClass=(String(vehicleRef||'').match(/(?:CLASS\s*)?(37|60|66|68|69|70|90|92)\d{3}\b/i)||[])[1]||null;
    const vehicles=[{loco:true},...wagons.map((w,i)=>({kind:w,index:i}))];   // locomotive first: visible without scrolling
    const cells=vehicles.map(item=>freightVehicleHtml(item.kind,item.index,theme,electric,item.loco,exactClass,kind)).join('');
    const cargoLabel=(cargo||'Freight').replace(/\s+train$/i,'');
    const title=code==='ZZ'?'Light locomotive':code==='ZB'?'Locomotive and brake van':`${cargoLabel} train`;
    return `<section class="train-formation is-freight" aria-label="${esc(title)}"><header><strong>${esc(title)}</strong></header><div class="formation-row" style="--coaches:${vehicles.length}">${cells}</div><p>Illustration: ${wagons.length?'wagons drawn for what this train is timetabled to carry':'drawn from its timetable'}, not this exact train.</p></section>`;
  }

  function trainFormationHtml(formation, operator) {
    if(!formation)return '';
    const theme=trainLiveryTheme(null, formation.operator||operator);
    const known=formation.coaches_known&&Array.isArray(formation.coaches)&&formation.coaches.length;
    const published=formation.coaches_published===true
      ||(formation.coaches_published==null&&formation.source==='Darwin (National Rail)'&&Boolean(known));
    const usual=(formation.usual||[]).filter(n=>Number(n)>0);
    const count=known?formation.coaches.length:(usual[usual.length-1]||usual[0]||(formation.generic?(formation.hauled?5:3):0));
    if(!count)return '';
    // Coach A is at the front: drawn on the right, as the train heads right.
    const coaches=known?[...formation.coaches].reverse():Array.from({length:count},()=>({}));
    const title=[formation.class?`Class ${formation.class}`:null,formation.name].filter(Boolean).join(' ')||(formation.traction==='diesel'?'Diesel train':formation.traction==='electric'?'Electric train':'Passenger train');
    const length=known?`${count} coach${count===1?'':'es'}`:!usual.length?'':usual.length>1?`usually ${usual.slice(0,-1).join(', ')} or ${usual[usual.length-1]} coaches`:`usually ${usual[0]} coach${usual[0]===1?'':'es'}`;
    const profile=trainProfileSet(formation,operator);
    const wholeProfile=profile?null:wholeTrainProfileImage(formation,operator);
    const units=(formation.units||[]).filter(Boolean);
    const unitsHtml=units.length?`<p class="formation-units">${units.map(unit=>`<b>${esc(unit)}</b>`).join('')}</p>`:'';
    const facilitiesHtml=trainFacilitiesHtml(formation);
    if(profile){
      // Without published coaches: the drawn unit's own length if that is one
      // the class runs at, else its usual length.
      const drawn=known?coaches:Array.from({length:usual.includes(profile[1].length)?profile[1].length:count},()=>({}));
      return `<section class="train-formation train-profile-image profile-class-${esc(formation.class)}" aria-label="${esc(`${title}, ${length}`)}"><header><strong>${esc(title)}</strong>${length?`<span>${esc(length)}</span>`:''}</header>${profileTrainHtml(profile,drawn)}${facilitiesHtml}${unitsHtml}</section>`;
    }
    if(wholeProfile){
      const coachData=known?coaches.map(coach=>`<li><strong>${coach.letter?`Coach ${esc(coach.letter)}`:'Coach'}</strong><div>${coach.first?'<b class="coach-first">First class</b>':'<span>Standard</span>'}${coach.toilet==='accessible'?`<span class="coach-facility">${WHEELCHAIR_ICON} Accessible toilet</span>`:coach.toilet==='standard'?'<span class="coach-facility">WC Toilet</span>':''}${coach.bicycle?'<span class="coach-facility">Cycle spaces</span>':''}${coach.wheelchair_space?'<span class="coach-facility">Wheelchair space</span>':''}</div></li>`).join(''):'';
      return `<section class="train-formation train-profile-image profile-class-${esc(formation.class)}" aria-label="${esc(`${title}, ${length}`)}"><header><strong>${esc(title)}</strong>${length?`<span>${esc(length)}</span>`:''}</header><div class="train-profile-stage"><img src="${wholeProfile}" alt="" loading="eager" fetchpriority="high" decoding="async"></div>${facilitiesHtml}${coachData?`<div class="formation-data"><strong>${count} ${published?'published':'allocated'} coaches</strong><ul>${coachData}</ul></div>`:''}${unitsHtml}</section>`;
    }
    if(formation.hauled&&!known)coaches.push({loco:true});
    const cells=coaches.map((coach,i)=>{
      if(coach.loco)return `<div class="coach"><small></small>${locoSvg(theme,formation.traction==='electric')}<div class="coach-marks"></div></div>`;
      const marks=[coach.first?'<b class="coach-first" title="First class">1st</b>':'',
        coach.toilet==='accessible'?`<span title="Accessible toilet">${WHEELCHAIR_ICON}</span>`:coach.toilet==='standard'?'<b class="coach-wc" title="Toilet">WC</b>':'',
        coach.bicycle?`<span title="Cycle spaces">${TRAIN_FACILITY_ICONS.bicycle_spaces}</span>`:'',
        coach.wheelchair_space&&coach.toilet!=='accessible'?`<span title="Wheelchair space">${WHEELCHAIR_ICON}</span>`:''].join('');
      return `<div class="coach"><small>${coach.letter?`Coach ${esc(coach.letter)}`:''}</small>${trainCoachSvg(i,coaches.length,theme,formation.class)}<div class="coach-marks">${marks}</div></div>`;
    }).join('');
    const inference=formation.inference_reason||'operator and timetable characteristics';
    const note=published?'Coaches as published by National Rail for this train.':formation.facilities_typical?'Coach layout from the allocated unit; facilities may change on the day.':formation.generic?"Illustration: this train's class and coaches are not in its timetable.":formation.inferred?`Class worked out from the timetable (${inference}); coaches not published, drawn at its usual length.`:"This train's coaches are not published; drawn at its class's usual length.";
    return `<section class="train-formation${known?'':' is-typical is-plain'}${formation.class?` profile-class-${esc(formation.class)}`:''}" aria-label="${esc(`${title}, ${length}`)}"><header><strong>${esc(title)}</strong>${length?`<span>${esc(length)}</span>`:''}</header>${facilitiesHtml}<div class="formation-row" style="--coaches:${coaches.length}">${cells}</div><p>${esc(note)}</p></section>`;
  }

  function renderRailIntelligence(detail) {
    const runs=Array.isArray(detail?.runs) ? detail.runs : [];
    const terms=detail?.terms_url ? safeUrl(detail.terms_url) : null;
    const cards=runs.slice(0,3).map(renderRailRun).join('');
    const message='This service has no published stops.';
    const stock=detail?.rolling_stock;
    const livery=stock?.matched ? trainLiveryStyle(stock) : null;
    const stockTitle=stock?.matched ? [stock.train_class?`Class ${stock.train_class}`:null,stock.number,stock.name].filter(Boolean).join(' · ') : '';
    // A traveller's facts only: the operator is already the panel heading,
    // and fleet codes (pool, works number, formation) mean little to them.
    const stockFacts=stock?.matched ? [
      ['Built',stock.year_built],['Depot',stock.depot],['Preserved at',stock.preserved_at],
    ].filter(([,value])=>value) : [];
    const stockCard=stock?.matched ? `<article class="rolling-stock-card" data-livery-theme="${esc(livery.key)}" style="${livery.css}"><div class="rolling-stock-stripe" aria-hidden="true"><i></i><i></i><i></i></div><small>Rolling stock</small><strong>${esc(stockTitle)}</strong>${stock.livery?`<b class="rolling-stock-livery">${esc(stock.livery)}</b>`:''}${stockFacts.length?`<dl>${stockFacts.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`:''}</article>` : '';
    const liveState=detail?.live_marker===false?'<p class="transport-note">Timetable only · not live on the map</p>':'';
    const freight=runs[0]?.freight;
    // What a freight train carries, from its working timetable: cargo,
    // booked weight, traction and the top speed it is timed for.
    const freightFacts=freight?[
      freight.tonnes?[Number(freight.tonnes).toLocaleString('en-GB'),'tonnes']:null,
      freight.traction?[freight.traction,'power']:null,
      freight.max_mph?[`${freight.max_mph} mph`,'top speed']:null,
    ].filter(Boolean):[];
    const repay=delayRepayHtml(runs[0]);
    const freightCard=freight?`<section class="freight-card" aria-label="What this train is carrying"><b class="freight-cargo">${esc(freight.cargo)}</b>${freightFacts.length?`<dl>${freightFacts.map(([value,label])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`:''}</section>`:'';
    const freightVehicle=runs[0]?.vehicle_ref||detail?.vehicle_ref||detail?.rolling_stock?.number||null;
    const formation=freight?freightProfileHtml(freight, runs[0]?.operator,freightVehicle):trainFormationHtml(detail?.formation, runs[0]?.operator);
    return `<div class="rail-intelligence-shell${stock?.matched?' has-rolling-stock':''}"${livery?` style="${livery.css}"`:''}>${formation}${liveState}${repay}${freightCard}${cards || `<div class="transport-unavailable"><strong>Stops not published</strong><span>${esc(message)}</span></div>`}${stockCard}${terms?`<p class="transport-source"><a target="_blank" rel="noopener" href="${esc(terms)}">Data terms ↗</a></p>`:''}</div>`;
  }

  function renderRailSummary(detail) {
    const run=Array.isArray(detail?.runs)?detail.runs[0]:null;
    const formation=trainFormationHtml(detail?.formation,run?.operator);
    if(!formation)return '';
    return `<div class="rail-intelligence-shell">${formation}<div class="rail-route-loading" role="status" aria-live="polite"><span></span><span>Loading calling points…</span></div></div>`;
  }

  function busLatenessLabel(detail) {
    const raw=detail?.delay_minutes ?? detail?.live_prediction?.delay_minutes;
    const delay=Number(raw);
    if(raw==null||!Number.isFinite(delay))return null;
    const about=detail?.delay_source==='estimated'?'About ':'';
    if(delay>=1)return {text:`${about}${delay} min late`,tone:delay>5?'bad':'warn'};
    if(delay<=-1)return {text:`${-delay} min early`,tone:'good'};
    return {text:'On time',tone:'good'};
  }

  const BUS_PROFILE_IMAGES = [
    {pattern:/\b(?:ALEXANDER\s+DENNIS\s+)?(?:ENVIRO\s*200|E200)(?:\s*MMC)?\b/i,name:'Alexander Dennis Enviro200 MMC',kind:'single-deck bus',src:'/static/assets/bus-profiles/enviro200-mmc.webp'},
    {pattern:/\b(?:ALEXANDER\s+DENNIS\s+)?(?:ENVIRO\s*400|E400)(?!\s*CITY)(?:\s*MMC)?\b/i,name:'Alexander Dennis Enviro400 MMC',kind:'double-deck bus',src:'/static/assets/bus-profiles/enviro400-mmc.webp'},
    {pattern:/\bWRIGHT\s+STREETLITE\b|\bSTREETLITE\b/i,name:'Wright StreetLite',kind:'single-deck bus',src:'/static/assets/bus-profiles/wright-streetlite.webp'},
    {pattern:/\bWRIGHT\s+STREETDECK(?!\s+ELECTROLINER)\b|\bSTREETDECK(?!\s+ELECTROLINER)\b/i,name:'Wright StreetDeck',kind:'double-deck bus',src:'/static/assets/bus-profiles/wright-streetdeck.webp'},
    {pattern:/\b(?:MERCEDES(?:-BENZ)?\s+)?CITARO\b|\bO\s*530\b/i,name:'Mercedes-Benz Citaro',kind:'single-deck bus',src:'/static/assets/bus-profiles/mercedes-citaro.webp'},
    {pattern:/\bYUTONG\s+E(?:10|12)\b|\bE12EV\b/i,name:'Yutong electric bus',kind:'electric single-deck bus',src:'/static/assets/bus-profiles/yutong-e12.webp'},
    {pattern:/\bNEW\s+ROUTEMASTER\b|\bNEW\s+BUS\s+FOR\s+LONDON\b|\bNB4L\b/i,name:'New Routemaster',kind:'London double-deck bus',src:'/static/assets/bus-profiles/new-routemaster.webp'},
    {pattern:/\bENVIRO\s*400\s*CITY\b|\bE400\s*CITY\b/i,name:'Alexander Dennis Enviro400 City',kind:'double-deck bus',src:'/static/assets/bus-profiles/enviro400-city.webp'},
    {pattern:/\bB5LH\b|\bGEMINI\s*3\b/i,name:'Volvo B5LH / Wright Gemini 3',kind:'hybrid double-deck bus',src:'/static/assets/bus-profiles/volvo-b5lh-gemini3.webp'},
    {pattern:/\bELECTROLINER\b/i,name:'Wright StreetDeck Electroliner',kind:'electric double-deck bus',src:'/static/assets/bus-profiles/wright-electroliner.webp'},
    {pattern:/\b(?:BYD\s*)?(?:ENVIRO\s*200EV|E200EV)\b/i,name:'BYD–Alexander Dennis electric bus',kind:'electric single-deck bus',src:'/static/assets/bus-profiles/byd-adl-electric.webp'},
    {pattern:/\b(?:OPTARE|SWITCH)\s+SOLO\b|\bSOLO\s*SR\b/i,name:'Switch Solo',kind:'compact single-deck bus',src:'/static/assets/bus-profiles/switch-solo.webp'},
    {pattern:/\b(?:OPTARE|SWITCH)\s+METROCITY\b|\bMETROCITY\b/i,name:'Switch Metrocity',kind:'single-deck bus',src:'/static/assets/bus-profiles/switch-metrocity.webp'},
    {pattern:/\b(?:MCV\s+)?EVORA\b|\bB8RLE\b/i,name:'Volvo B8RLE / MCV Evora',kind:'single-deck bus',src:'/static/assets/bus-profiles/mcv-evora.webp'},
    {pattern:/\bOPEN[- ]?TOP\b|\bSIGHTSEEING\b/i,name:'Open-top sightseeing bus',kind:'open-top double-deck bus',src:'/static/assets/bus-profiles/open-top.webp'},
  ];

  function busProfileMatch(detail, a) {
    // BODS usually publishes a fleet number, not a body model. Only make an
    // exact model claim when the provider identity itself contains a model
    // token. A known coach operator gets a clearly-labelled generic coach.
    const identity=[detail?.vehicle_ref,a?.vehicle_ref,a?.name,a?.subtitle].filter(Boolean).join(' ');
    const exact=BUS_PROFILE_IMAGES.find(profile=>profile.pattern.test(identity));
    if(exact)return {...exact,exact:true};
    if(coachInfo(a))return {name:'Intercity coach',kind:'coach',src:'/static/assets/bus-profiles/generic-intercity-coach.webp',exact:false};
    return null;
  }

  function busProfileHtml(detail, a) {
    const profile=busProfileMatch(detail,a);
    if(!profile)return '';
    const note=profile.exact
      ? 'Body model matched from the published vehicle identity.'
      : 'Typical coach illustration. The live feed does not publish this vehicle’s exact body model.';
    return `<section class="bus-profile-card${profile.exact?'':' is-typical'}" aria-label="${esc(profile.name)}"><header><strong>${esc(profile.name)}</strong><span>${esc(profile.kind)}</span></header><div class="bus-profile-stage"><img src="${profile.src}" alt="" loading="lazy" decoding="async"></div><p>${esc(note)}</p></section>`;
  }

  function renderBusIntelligence(detail, a=null) {
    // Same stop list as trains: opens at the stop before the bus, marks where
    // it is, and shows a later expected time beside each stop when it runs late.
    const stops=Array.isArray(detail?.stops) ? detail.stops : [];
    const rows=stops.map((stop,index)=>{
      const booked=providerClock(stop?.clock || stop?.scheduled);
      const expected=providerClock(stop?.expected_clock || stop?.expected);
      return {
        name:humanTransitLabel(stop?.name || `Stop ${index+1}`),
        booked:booked||'—',
        lateClock:expected&&expected!==booked?expected:'',
        detailHtml:'',
        state:stop?.state,
      };
    });
    const heading=humanTransitLabel([detail?.route?`Route ${detail.route}`:null,detail?.destination?`to ${detail.destination}`:null].filter(Boolean).join(' '));
    const operator=humanTransitLabel(detail?.operator||'');
    const lateness=busLatenessLabel(detail);
    const head=heading||operator||lateness
      ? `<div class="transport-route-head"><strong>${esc(heading||'Bus journey')}</strong>${operator?`<span>${esc(operator)}</span>`:''}${lateness?`<em class="bus-lateness is-${lateness.tone}">${esc(lateness.text)}</em>`:''}</div>`
      : '';
    const vehicleWord=coachInfo(a)?'Coach':'Bus';
    const list=stopTimelineHtml(rows,{vehicle:vehicleWord,hasProgress:rows.some(row=>row.state),className:'bus-stops train-full-route'});
    const empty=`<div class="transport-unavailable"><strong>${detail?.mode==='timetable_sync'?'Timetable preparing':'Stops not available'}</strong><span>This ${vehicleWord.toLowerCase()} has not been linked to a timetabled journey yet.</span></div>`;
    const fleet=detail?.fleet_path?`<p class="fleet-link"><a href="${esc(detail.fleet_path)}">Where else this ${coachInfo(a)?'coach':'bus'} has worked →</a></p>`:'';
    return `${busProfileHtml(detail,a)}${head}${coachBookingHtml(a)}${list||empty}${fleet}`;
  }

  async function loadActivityIntelligence(a, force=false) {
    if (!a || !['train','bus'].includes(a.type)) { clearActivityIntelligence(); return; }
    if (!force && intelligenceLoadedFor === a.id) return;
    clearTimeout(intelligenceRefreshTimer);
    intelligenceRefreshTimer=null;
    intelligenceController?.abort();
    const controller=new AbortController();
    intelligenceController=controller;
    intelligenceLoadedFor = a.id;
    const box=$('detail-intelligence');
    if(!box)return;
    const requestId=++intelligenceRequest;
    let keepRefreshing=true;
    let responsePayload={};
    let fullRailResponse=false;
    if(!force){box.classList.remove('hidden');box.innerHTML=`<small>${a.type==='train'?'LIVE TRAIN JOURNEY':'LIVE BUS JOURNEY'}</small><p class="coverage-loading">Loading route…</p>`;}
    try {
      // The full physical route can take longer on its first request. Fetch a
      // schedule-free train summary alongside it so the correct formation is
      // useful immediately, while reserving a stable place for calling points.
      if(a.type==='train'&&!force){
        fetch(`/api/activity/${encodeURIComponent(a.id)}/rail?summary=true`,{cache:'no-store',signal:controller.signal})
          .then(response=>response.ok?response.json():null)
          .then(payload=>{
            if(fullRailResponse||requestId!==intelligenceRequest||selectedId!==a.id)return;
            const summary=payload?.detail&&typeof payload.detail==='object'?payload.detail:payload;
            const html=renderRailSummary(summary);
            if(html)box.innerHTML=html;
          }).catch(()=>{});
      }
      const response=await fetch(`/api/activity/${encodeURIComponent(a.id)}/${a.type==='train'?'rail':'bus'}`,{cache:'no-store',signal:controller.signal});
      const payload=await response.json().catch(()=>({}));
      fullRailResponse=a.type==='train';
      responsePayload=payload;
      if(requestId!==intelligenceRequest || selectedId!==a.id)return;
      if(response.status===404&&a.type==='train'){
        // The server no longer holds this id: the service was merged into
        // another live identity, or a restored "last known" train expired.
        // Re-link to the same service if it is still running; otherwise stop
        // asking every 10 seconds for a train that cannot come back.
        keepRefreshing=false;
        const relinked=await relinkSelectedTrain(a);
        if(requestId!==intelligenceRequest || selectedId!==a.id)return;
        if(relinked)return;
        box.innerHTML=`<small>LIVE TRAIN JOURNEY</small><div class="transport-unavailable"><strong>No longer reporting</strong><span>This train has no current live railway position. It may have finished its journey.</span></div>`;
        return;
      }
      if(response.status===402){
        box.innerHTML=`<small>${a.type==='train'?'LIVE TRAIN JOURNEY':'LIVE BUS JOURNEY'}</small><div class="transport-unavailable radar-plus-locked"><strong>Available with Radar+</strong><span>Upgrade to unlock this advanced live transport detail.</span></div>`;
        return;
      }
      if(!response.ok)throw new Error(payload?.detail || 'route detail unavailable');
      const detail=payload?.detail && typeof payload.detail==='object' ? payload.detail : payload;
      if(a.type==='train'){
        applySelectedTrainPublishedRoute(a,detail);
        const runDelay=Array.isArray(detail?.runs)?detail.runs[0]?.delay_minutes:null;
        if(runDelay!=null&&Number.isFinite(Number(runDelay))){
          railDelayById.set(a.id,Number(runDelay));
          const label=trainStatusLabel(activityById(a.id)||a);
          const cell=document.querySelector('#detail-fields [data-field="Status"] strong');
          if(cell)cell.textContent=label;
          else if(!['Live','Last known'].includes(label))$('detail-fields')?.insertAdjacentHTML('afterbegin',field('Status',label));
          toneStatusField(activityById(a.id)||a);
        }
      }
      const html=a.type==='train' ? renderRailIntelligence(detail) : renderBusIntelligence(detail,a);
      if(a.type==='bus')drawBusRoute(a.id,detail);
      if(box.innerHTML!==html){const panel=$('detail-panel'),scrollTop=panel?.scrollTop||0;box.innerHTML=html;if(panel)panel.scrollTop=scrollTop;}
    } catch {
      if(requestId!==intelligenceRequest || selectedId!==a.id)return;
      if(!force)box.innerHTML=`<small>${a.type==='train'?'LIVE TRAIN JOURNEY':'LIVE BUS JOURNEY'}</small><div class="transport-unavailable"><strong>Route details not available yet</strong><span>Live position shown on the map.</span></div>`;
    } finally {
      if(requestId===intelligenceRequest){
        intelligenceController=null;
        const requestedRetry=Number(responsePayload?.detail?.retry_seconds ?? responsePayload?.retry_seconds);
        const refreshMs=a.type==='train'?10000:(Number.isFinite(requestedRetry)?Math.max(2000,Math.min(20000,requestedRetry*1000)):12000);
        if(keepRefreshing&&selectedId===a.id)intelligenceRefreshTimer=setTimeout(()=>{const current=activityById(a.id);if(current)loadActivityIntelligence(current,true);},refreshMs);
      }
    }
  }

  async function relinkSelectedTrain(a) {
    const uid=String(a?.train_uid||'').trim();
    if(!uid)return false;
    try{
      const params=new URLSearchParams({train_uid:uid});
      if(a.service_date)params.set('service_date',a.service_date);
      const response=await fetch(`/api/rail/locate?${params}`,{cache:'no-store',signal:AbortSignal.timeout(10000)});
      const payload=await response.json().catch(()=>({}));
      const found=payload?.activity;
      if(!response.ok||!found?.id||found.id===a.id||selectedId!==a.id)return false;
      if(String(found.train_uid||'').trim()!==uid)return false;
      if(a.service_date&&found.service_date&&found.service_date!==a.service_date)return false;
      globalCoolById.set(found.id,found);
      selectActivity(found.id,false);
      return true;
    }catch{return false;}
  }

  function selectActivity(id, pan, focusZoom=null) {
    const selectionChanged = selectedId !== id || selectedPlaceId !== null;
    if (selectionChanged && trackedActivityId && trackedActivityId !== id) trackedActivityId = null;
    if (selectionChanged) selectedTrainPublishedRoute=null;
    selectedPlaceId = null;
    selectedId = id;
    const a = activityById(id);
    if (!a) return;
    const detailFreshness=document.querySelector('#detail-panel .detail-head small');
    const staleTrain=a.type==='train'&&a.restored_snapshot===true;
    if(detailFreshness)detailFreshness.textContent=staleTrain?'Last known position':'';
    selectedActivitySnapshot=a;
    $('detail-panel')?.classList.toggle('is-train',a.type==='train');
    $('detail-panel')?.classList.toggle('is-bus',a.type==='bus');
    $('detail-panel')?.classList.toggle('is-aircraft',['aircraft','sar'].includes(a.type));
    liveObjectRenderer?.setActivities(filteredActivities(),selectedId);
    setDetailRouteView(false);
    syncDetailRouteControl(a);
    syncBaseMap();
    showFocusMarker(a);
    $('save-place')?.classList.add('hidden'); $('share-activity')?.classList.remove('hidden');
    selectedDetailSignature = activityDetailSignature(a);
    $('detail-panel')?.classList.add('has-selection');
    $('detail-sidebar')?.classList.remove('is-empty');
    sendWatching(id);
    if (pan) { trackProduct('activity_selected', a.type); collapseWorkspaceOnPhone(); }
    const detailCool=coolInfo(a);
    const roadImpact=a.kind==='event'?a.impact:null;
    $('detail-panel')?.classList.toggle('is-traffic',Boolean(roadImpact));
    $('detail-title').innerHTML = roadImpact ? esc(roadImpact.title||roadImpact.what||a.name) : `${currentWorkspace==='cool' && detailCool.score>=45 ? coolBadgeHtml(a,true)+' ' : ''}${esc(a.name)}`;
    const flightRoute=['sar','aircraft'].includes(a.type) ? flightRouteById.get(a.id) : null;
    const providerFlightRoute=['sar','aircraft'].includes(a.type)&&a.origin&&a.destination ? `${a.origin} → ${a.destination}` : '';
    // Aircraft: the route, or nothing. Registration, type and operator are
    // already on the operator line below; repeating them here was noise.
    // Aircraft: the route; and when no airline line will show (military,
    // private), the type and registration, so a Hawk says it is a Hawk.
    const aircraftIdentity=['aircraft','sar'].includes(a.type)&&!operatorTheme(a)?[a.aircraft_model||a.aircraft_type_code,a.vehicle_ref].filter(Boolean).join(' • '):'';
    $('detail-subtitle').textContent = roadImpact ? humanTransitLabel(roadImpact.where||'') : ['aircraft','sar'].includes(a.type) ? [flightRoute?flightRouteText(flightRoute):providerFlightRoute,aircraftIdentity].filter(Boolean).join(' · ') : humanTransitLabel(a.kind === 'event' ? (a.subtitle || a.category || a.position_label) : (a.origin && a.destination ? `${a.origin} → ${a.destination}` : (a.subtitle || a.route || a.position_label)));
    renderOperatorIdentity(a);
    renderInsight(a);
    if (selectionChanged) loadUsually(a);
    renderDiscoveryDetail(a);
    if (selectionChanged) { clearPhoto(); clearActivityIntelligence(); }
    if (['sar','aircraft'].includes(a.type)) { loadAircraftInfo(a); if (!flightRouteById.has(a.id)) loadFlightRoute(a); }
    // No bus photos: Commons rarely has the right bus, and a wrong one misleads.
    // Trains are drawn (trainFormationHtml, freightProfileHtml), never photographed.
    else if (a.type==='vessel') loadPhoto(a);
    else if (['road','incident'].includes(a.type)) loadNearestCamera(a);
    renderRelated(a);
    loadEnvironmentContext(a);
    loadActivityIntelligence(a);
    if (roadImpact) {
      // What it means for you first: the impact, when, and why. The feed's
      // own fields are under More details.
      $('detail-fields').innerHTML = [
        field('Impact', roadImpact.impact || (roadImpact.active ? null : 'Not in force now')), field('When', roadImpact.when), field('Why', roadImpact.why),
        field('Source', a.source), field('Code', a.source_id), field('Position', a.position_label),
      ].join('');
      const impactCell=document.querySelector('#detail-fields [data-field="Impact"]');
      if(impactCell)impactCell.dataset.tone=roadImpact.active&&roadImpact.rank>=2?'bad':roadImpact.active?'warn':'';
      const note=$('detail-insight');
      if(note){
        if(roadImpact.note){note.classList.remove('hidden');note.innerHTML=`<p class="traffic-note">${esc(roadImpact.note)}</p>`;}
        else {note.classList.add('hidden');note.innerHTML='';}
      }
      $('follow').classList.add('hidden');
      if(a.type==='incident')loadCoverage(a); else clearCoverage();
    } else if (a.kind === 'event') {
      $('detail-fields').innerHTML = [field('Category', a.category), field('Status', a.status), field('Severity', a.severity), field('Road', a.road), field('Direction', a.direction), field('Lanes', a.lanes_closed), field('Reason', a.reason), field('Started', timeText(a.start_time)), field('Ends', timeText(a.end_time)), field('Location', a.position_label), field('Source', a.source), field('Updated', timeText(a.last_updated))].join('');
      $('follow').classList.remove('hidden');
      $('follow').textContent = followed.has(id) ? 'Saved ✓' : 'Save';
      loadCoverage(a);
    } else {
      const speed = a.speed == null ? null : `${a.speed} ${a.speed_unit || ''}`.trim()+(a.speed_reported!=null?` (from its movement; the feed said ${a.speed_reported})`:'');
      const delay = a.delay_minutes == null ? null : (a.delay_minutes === 0 ? 'On time' : `${a.delay_minutes > 0 ? '+' : ''}${a.delay_minutes} min`);
      const bearing = a.bearing == null ? null : `${Math.round(a.bearing)}°`;
      const identityLabel = a.type === 'vessel' ? 'MMSI' : (['sar','aircraft'].includes(a.type) ? 'Registration' : 'Vehicle');
      const groundPhase=['sar','aircraft'].includes(a.type)?aircraftGroundPhase(a):null;
      const altitude = groundPhase ? groundPhase.chip : a.altitude_ft == null ? null : `${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} ft`;
      const isAircraft=['sar','aircraft'].includes(a.type);
      const aircraftType=isAircraft ? (a.aircraft_model || a.aircraft_type_code || 'Type not identified by live feed') : null;
      const publishedRoute=a.route || (a.origin&&a.destination?`${a.origin} → ${a.destination}`:null);
      if (a.type === 'train') {
        // A traveller's three facts, as in the agreed design. Identifiers and
        // positions stay available behind "More details".
        const age = relativeAge(Math.max(0,(Date.now()-new Date(a.last_updated||0).getTime())/1000));
        $('detail-fields').innerHTML = [
          // "Live" says nothing a moving marker does not, and "Last known" is
          // already the heading above the title; leave both out.
          field('Status', ['Live','Last known'].includes(trainStatusLabel(a)) ? null : trainStatusLabel(a)), field('Next', humanTransitLabel(a.next_location)), field('Updated', age),
          field('Headcode', a.headcode), field('Train UID', a.train_uid), field('Position', a.position_label),
          field('Source', a.source), field('Latitude', Number.isFinite(Number(a.lat)) ? Number(a.lat).toFixed(5) : null),
          field('Longitude', Number.isFinite(Number(a.lon)) ? Number(a.lon).toFixed(5) : null),
        ].join('');
        toneStatusField(a);
      } else if (isAircraft) {
        // Altitude, speed and update time as chips. The type is already in
        // the panel heading and the route is the subtitle; identifiers stay
        // under More details.
        const age = relativeAge(Math.max(0,(Date.now()-new Date(a.last_updated||0).getTime())/1000));
        const typeCode = a.aircraft_type_code && a.aircraft_type_code !== a.aircraft_model ? a.aircraft_type_code : null;
        const verticalRate=a.vertical_rate_fpm==null?null:(Number(a.vertical_rate_fpm)>100?`Climbing ${Math.round(Number(a.vertical_rate_fpm)).toLocaleString('en-GB')} ft/min`:Number(a.vertical_rate_fpm)<-100?`Descending ${Math.abs(Math.round(Number(a.vertical_rate_fpm))).toLocaleString('en-GB')} ft/min`:'Level');
        const selectedAltitude=a.selected_altitude_ft==null?null:`${Math.round(Number(a.selected_altitude_ft)).toLocaleString('en-GB')} ft`;
        const heading=a.heading==null?null:`${Math.round(Number(a.heading))}°`;
        const ias=a.indicated_airspeed_kt==null?null:`${Math.round(Number(a.indicated_airspeed_kt))} kt`;
        const tas=a.true_airspeed_kt==null?null:`${Math.round(Number(a.true_airspeed_kt))} kt`;
        const mach=a.mach==null?null:`Mach ${Number(a.mach).toFixed(2)}`;
        const flags=Array.isArray(a.aircraft_flags)?a.aircraft_flags.join(' • '):null;
        $('detail-fields').innerHTML = [
          field('Alert', a.emergency), field('Altitude', altitude), field('Speed', speed), field('Vertical rate', verticalRate), field('Updated', age),
          field('Registration', a.vehicle_ref), field('Identified as', a.identity_label),
          field('ICAO type code', typeCode), field('Type', a.aircraft_model), field('Aircraft category', a.aircraft_category),
          field('Squawk', a.squawk), field('Position source', a.position_source), field('Indicated airspeed', ias),
          field('True airspeed', tas), field('Mach', mach), field('Heading', heading),
          field('Selected altitude', selectedAltitude), field('Aircraft flags', flags),
          field('Source', a.source), field('ICAO / source ref', a.source_id),
          field('Latitude', Number.isFinite(Number(a.lat)) ? Number(a.lat).toFixed(5) : null),
          field('Longitude', Number.isFinite(Number(a.lon)) ? Number(a.lon).toFixed(5) : null),
        ].join('');
        const alertCell=document.querySelector('#detail-fields [data-field="Alert"]');
        if(alertCell)alertCell.dataset.tone='bad';
      } else if (a.type === 'bus') {
        // Same compact chips as trains; the route and lateness are in the
        // journey card below, and fleet identifiers stay under More details.
        const age = relativeAge(Math.max(0,(Date.now()-new Date(a.last_updated||0).getTime())/1000));
        $('detail-fields').innerHTML = [
          field('Speed', speed), field('Updated', age), field('Vehicle', a.vehicle_ref), field('Journey ref', a.journey_ref), field('Operator', a.operator),
          field('Source', a.source), field('Latitude', Number.isFinite(Number(a.lat)) ? Number(a.lat).toFixed(5) : null),
          field('Longitude', Number.isFinite(Number(a.lon)) ? Number(a.lon).toFixed(5) : null),
        ].join('');
      } else
      $('detail-fields').innerHTML = [field('Route', isAircraft ? (flightRoute ? flightRouteText(flightRoute) : null) : publishedRoute), field(identityLabel, a.vehicle_ref), field('Identified as', a.identity_label), field('Identity confidence', a.identity_confidence==='type'?'Type identified':(a.identity_confidence==='exact'?'Exact live identifier':null)), field('Aircraft type', aircraftType), field('ICAO type code', a.aircraft_type_code), field('Altitude', altitude), field('Headcode', a.headcode), field('Train UID', a.train_uid), field('Journey ref', a.journey_ref), field('Status', a.status), field('Position', a.position_label), field('Next report', a.next_location), field('Speed', speed), field('Bearing', bearing), field('Delay', delay), field('Occupancy', a.occupancy), field('Operator', a.operator), field('Source', a.source), field('ICAO / source ref', a.source_id), field('Tracking', fmt(a.tracking_count)), field('Updated', timeText(a.last_updated)), field('Latitude', Number.isFinite(Number(a.lat)) ? Number(a.lat).toFixed(5) : null), field('Longitude', Number.isFinite(Number(a.lon)) ? Number(a.lon).toFixed(5) : null)].join('');
      $('follow').classList.remove('hidden');
      $('follow').textContent = followed.has(id) ? 'Saved ✓' : 'Save';
      clearCoverage();
    }
    renderTrackingControl(a);
    renderSelectedTrail(a);
    if (pan && focusZoom != null) { prefetchRailLinesAt(a.lat, a.lon, Math.max(map.getZoom(), focusZoom)); map.setView([a.lat, a.lon], Math.max(map.getZoom(), focusZoom), {animate:true}); }
    else if (pan || trackedActivityId === id) { prefetchRailLinesAt(a.lat, a.lon); map.panTo([a.lat, a.lon], { animate: true }); }
    renderRanking();
  }

  async function focusActivityById(id, focusZoom=11) {
    let activity=activityById(id);
    if(!activity){
      try {
        const response=await fetch(`/api/activity/${encodeURIComponent(id)}`,{cache:'no-store'});
        const payload=await response.json().catch(()=>({}));
        if(!response.ok)throw new Error(payload?.detail || 'This live item has ended.');
        activity=payload;
        globalCoolById.set(activity.id,activity);
      } catch(error) {
        showToast('This is no longer live.');
        loadTodayRadar(true);
        return false;
      }
    }
    if(toggleableActivityTypes.has(activity.type)){
      enabledActivityTypes.add(activity.type);
      updateLayerToggleUi();
    }
    selectActivity(activity.id,true,focusZoom);
    sendViewport();
    return true;
  }

  const sourceMeta = {
    network_rail:{label:'TRUST movements',icon:'train',group:'RAIL ENGINE'}, td:{label:'TD signalling',icon:'traffic',group:'RAIL ENGINE'}, vstp:{label:'VSTP schedules',icon:'route',group:'RAIL ENGINE'}, schedule:{label:'Full SCHEDULE',icon:'database',group:'RAIL ENGINE'}, rail_reference:{label:'Rail references',icon:'pin',group:'RAIL ENGINE'},
    bods:{label:'BODS bus GPS',icon:'bus',group:'MOVING VEHICLES'}, bods_timetable:{label:'BODS published bus timetable',icon:'bus',group:'SMART PLACES'}, bods_sx:{label:'Bus disruptions',icon:'busstop',group:'INCIDENTS & WARNINGS'}, adsb_sar:{label:'SAR / special aircraft',icon:'sar',group:'RESCUE & RESPONSE'}, rnli:{label:'RNLI launches',icon:'rescue',group:'RESCUE & RESPONSE'},
    national_highways:{label:'National Highways roadworks + incidents',icon:'road',group:'ROAD INTELLIGENCE'}, national_highways_vms:{label:'Live motorway signs',icon:'signal',group:'ROAD INTELLIGENCE'}, national_highways_speed:{label:'Managed speed limits',icon:'signal',group:'ROAD INTELLIGENCE'}, national_highways_features:{label:'Roadside network features',icon:'roadfeature',group:'ROAD INTELLIGENCE'}, traffic_flow:{label:'Live traffic flow',icon:'traffic',group:'ROAD INTELLIGENCE'},
    ea_floods:{label:'EA flood warnings',icon:'flood',group:'INCIDENTS & WARNINGS'}, met_office:{label:'Weather warnings',icon:'weather',group:'INCIDENTS & WARNINGS'}, london_fire:{label:'London Fire incidents',icon:'incident',group:'RESCUE & RESPONSE'},
    bgs_earthquakes:{label:'BGS earthquakes',icon:'earthquake',group:'EARTH & ENVIRONMENT'}, ea_levels:{label:'Tide gauges',icon:'flood',group:'EARTH & ENVIRONMENT'}, wavenet:{label:'WaveNet buoys',icon:'flood',group:'EARTH & ENVIRONMENT'}, uk_air:{label:'DEFRA air quality',icon:'airquality',group:'EARTH & ENVIRONMENT'},
    carbon_intensity:{label:'Grid carbon intensity',icon:'grid',group:'ENERGY & SPACE'}, power_outages:{label:'Power outages',icon:'outage',group:'ENERGY & SPACE'}, space_weather:{label:'Aurora / space weather',icon:'space',group:'ENERGY & SPACE'}, iss:{label:'ISS tracker',icon:'satellite',group:'ENERGY & SPACE'},
    tfl_live:{label:'TfL cycle hire',icon:'bike',group:'LOCAL MOBILITY'}, gbfs:{label:'GBFS shared mobility',icon:'bike',group:'LOCAL MOBILITY'}, gritters:{label:'Traffic Scotland gritters',icon:'gritter',group:'LOCAL MOBILITY'}, smart_places:{label:'Clickable stations / stops / terminals',icon:'pin',group:'SMART PLACES'}, demo:{label:'Demo',icon:'other',group:'OTHER'}
  };

  const groupOrder = ['MOVING VEHICLES','SMART PLACES','ROAD INTELLIGENCE','INCIDENTS & WARNINGS','RESCUE & RESPONSE','EARTH & ENVIRONMENT','ENERGY & SPACE','LOCAL MOBILITY','RAIL ENGINE','OTHER'];

  function feedStatusClass(s) {
    if (!s.enabled || ['off','setup'].includes(s.mode)) return 'off';
    if (s.mode === 'error') return 'error';
    if (s.connected && s.mode === 'live') return 'on';
    if (s.connected && ['ready','gtfs-rt','bulk-siri'].includes(s.mode)) return 'ready';
    if (['sync','connecting','waiting'].includes(s.mode)) return 'sync';
    return s.connected ? 'ready' : 'sync';
  }

  const layerSourceNames = {
    useful:['network_rail','bods','national_highways','met_office','ea_floods'], transport:['network_rail','bods'], train:['network_rail'], bus:['bods'], rescue:['rnli'], sar:['adsb_sar'],
    cool:['network_rail','adsb_sar','rnli'], 'cool-air':['adsb_sar'], 'cool-rail':['network_rail'], 'cool-rescue':['adsb_sar','rnli'],
    road:['national_highways'], roadfeature:['national_highways_features'], incident:['national_highways','national_highways_vms','national_highways_speed','bods_sx','london_fire'],
    weather:['met_office'], flood:['ea_floods'], aircraft:['adsb_sar'], gritter:['gritters'],
    earthquake:['bgs_earthquakes'], tide:['ea_levels'], wave:['wavenet'],
    grid:['carbon_intensity'], airquality:['uk_air'], bike:['tfl_live','gbfs'],
    outage:['power_outages'], space:['space_weather'], satellite:['iss']
  };

  function renderLayerHealth(sources=[]) {
    const byName = new Map((sources || []).map(x => [x.name, x]));
    const priority = {error:7, stale:6, setup:5, sync:4, live:3, ready:2, offline:1, off:0};
    document.querySelectorAll('.filter[data-type]').forEach(button => {
      const type = button.dataset.type || 'all';
      if(type==='cool-off')return;
      const count = type === 'all' ? activities.length : activities.filter(a => layerTypeMatch(a, type)).length;
      let badge = button.querySelector('.layer-count');
      if (!badge) { badge=document.createElement('span'); badge.className='layer-count'; button.appendChild(badge); }
      badge.textContent = count > 999 ? '999+' : String(count);
      if (type === 'all') { button.dataset.feedHealth = activities.length ? 'live' : 'sync'; button.title=`${count} live objects in the current streamed area`; return; }
      const states=(layerSourceNames[type] || []).map(n=>byName.get(n)).filter(Boolean);
      let chosen=null, chosenHealth='off';
      for(const st of states){ const h=st.health || feedStatusClass(st); if(!chosen || (priority[h]??0) > (priority[chosenHealth]??0)){chosen=st;chosenHealth=h;} }
      button.dataset.feedHealth=chosenHealth;
      const feedText=chosen ? `${String(chosenHealth).toUpperCase()} • ${chosen.message || chosen.name}` : 'No dedicated source status yet';
      button.title=`${count} current ${type === 'road' ? 'planned roadworks/closures' : (type === 'incident' ? 'unplanned incidents' : type)} items • ${feedText}`;
    });
  }

  function renderFeeds(sources) {
    sources = sources || [];
    latestSources = sources;
    renderLayerHealth(latestSources);
    const wrap = $('feed-status');
    if (!sources || !sources.length) { wrap.innerHTML = '<div class="feed-row"><div class="feed-copy"><p>Starting data collectors…</p></div></div>'; $('mode-pill').textContent = 'CONNECTING'; renderHealth([]); return; }
    const visibleSources=sources.filter(s=>s.enabled || !['off','disabled'].includes(String(s.mode || '').toLowerCase()));
    const groups = new Map();
    for (const s of visibleSources) { const meta = sourceMeta[s.name] || {label:(s.name || 'Feed'),icon:'other',group:'OTHER'}; if (!groups.has(meta.group)) groups.set(meta.group, []); groups.get(meta.group).push({s,meta}); }
    const html = [];
    for (const group of groupOrder) {
      const rows = groups.get(group); if (!rows?.length) continue;
      html.push(`<div class="feed-group"><div class="feed-group-title">${esc(group)}</div>`);
      for (const {s,meta} of rows) {
        const cls = s.health === 'stale' ? 'stale' : feedStatusClass(s); let setup = '';
        if (s.name === 'network_rail' && s.mode === 'setup') setup = '<p>Run <strong>configure_network_rail.bat</strong>. The launcher can also recover missing Network Rail credentials from older Radar folders without overwriting other API keys.</p>';
        else if (s.name === 'national_highways' && s.mode === 'setup') setup = '<p><a class="inline-link" target="_blank" rel="noopener" href="https://developer.data.nationalhighways.co.uk/signup">Get free key ↗</a> then run <strong>configure_roads.bat</strong>.</p>';
        else if (s.name === 'traffic_flow' && s.mode === 'setup') setup = '<p><a class="inline-link" target="_blank" rel="noopener" href="https://developer.tomtom.com/">Get free traffic key ↗</a> then run <strong>configure_traffic.bat</strong>.</p>';
        else if (s.name === 'bods' && s.mode === 'error') setup = '<p>Run <strong>configure_buses.bat</strong> if your BODS key is missing. The app also repairs legacy BODS URLs automatically.</p>';
        const freshness = `<div class="feed-freshness"><span>${esc(String(s.health || s.mode).toUpperCase())}</span><span>update ${esc(relativeAge(s.age_seconds))}</span><span>${fmt(s.messages_seen || 0)} records/messages</span></div>`;
        html.push(`<div class="feed-row feed-${esc(s.name || 'other')}${s.health === 'stale' ? ' is-stale' : ''}"><span class="feed-icon" aria-hidden="true">${iconSvg(meta.icon)}</span><div class="feed-copy"><strong><span class="feed-name">${esc(meta.label)}</span><i class="status-led ${cls}"></i><small class="feed-mode">${esc(s.health || s.mode)}</small></strong><p>${esc(s.message)}</p>${freshness}${setup}</div></div>`);
      }
      html.push('</div>');
    }
    wrap.innerHTML = html.join('');
    const trafficStatus = sources.find(s => s.name === 'traffic_flow');
    trafficConfigured = Boolean(trafficStatus && trafficStatus.enabled && !['setup','off'].includes(trafficStatus.mode));
    const coreNames = ['network_rail','bods','national_highways','traffic_flow','ea_floods','rnli','adsb_sar','london_fire','bgs_earthquakes','ea_levels','wavenet','carbon_intensity','uk_air','tfl_live','gbfs','space_weather','iss','power_outages','gritters'];
    const provenLive = sources.filter(s => coreNames.includes(s.name) && s.health === 'live');
    const ready = sources.filter(s => coreNames.includes(s.name) && ['live','ready'].includes(s.health));
    const degraded = sources.some(s => coreNames.includes(s.name) && ['error','stale'].includes(s.health));
    const connecting = sources.some(s => coreNames.includes(s.name) && s.enabled && ['sync','connecting','waiting'].includes(s.mode));
    const demo = sources.find(s => s.name === 'demo' && s.connected);
    $('mode-pill').textContent = degraded && ready.length ? `LIVE • ${ready.length} OK • CHECK FEEDS` : (provenLive.length >= 3 ? 'LIVE UK DATA' : (provenLive.length ? `LIVE • ${provenLive.length} ACTIVE` : (ready.length ? `READY • ${ready.length} FEEDS` : (connecting ? 'CONNECTING LIVE' : (demo ? 'DEMO DATA' : 'OFFLINE')))));
    renderHealth(sources);
  }

  function renderHealth(sources) {
    const badge = $('health-badge');
    const box = $('health-summary');
    if (!badge || !box) return;
    const core = (sources || []).filter(s => s.enabled && !['schedule','rail_reference','demo'].includes(s.name));
    const live = core.filter(s => s.health === 'live').length;
    const ready = core.filter(s => s.health === 'ready').length;
    const problems = core.filter(s => ['error','stale'].includes(s.health));
    const setup = core.filter(s => s.health === 'setup');
    const syncing = core.filter(s => ['sync','offline'].includes(s.health));
    const overall = problems.length ? 'degraded' : ((live || ready) ? 'healthy' : (setup.length ? 'setup' : 'starting'));
    badge.className = `health-badge ${overall}`;
    badge.textContent = overall.toUpperCase();
    const totalOk = live + ready;
    const chips = [
      `<span class="health-chip good">${totalOk} healthy/ready</span>`,
      problems.length ? `<span class="health-chip bad">${problems.length} error/stale</span>` : '',
      setup.length ? `<span class="health-chip warn">${setup.length} need setup</span>` : '',
      syncing.length ? `<span class="health-chip warn">${syncing.length} starting</span>` : ''
    ].filter(Boolean).join('');
    const issueText = problems.length ? problems.slice(0,3).map(s => `${sourceMeta[s.name]?.label || s.name}: ${s.health}`).join(' • ') : 'All enabled feeds are up to date.';
    box.innerHTML = `<div class="health-row"><span>Core data feeds</span><strong>${totalOk}/${core.length || 0} ready</strong></div><div class="health-mini">${chips}</div><div class="health-row"><span>${esc(issueText)}</span></div>`;
  }

  function showToast(text) {
    const toast = $('watch-toast');
    if (!toast) return;
    toast.textContent = text;
    toast.classList.remove('hidden');
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(()=>toast.classList.add('hidden'), 4000);
  }

  function setWorkspaceCollapsed(collapsed) {
    const shell=$('workspace-shell'),button=$('workspace-collapse');
    if(!shell||!button)return;
    shell.classList.toggle('collapsed',Boolean(collapsed));
    button.setAttribute('aria-expanded',String(!collapsed));
    button.title=collapsed?'Open controls':'Collapse controls';
    button.childNodes[0].nodeValue=collapsed?'›':'‹';
    setTimeout(()=>map.invalidateSize(),190);
  }

  function isPhoneLayout() {
    return window.matchMedia('(max-width: 760px)').matches;
  }

  function collapseWorkspaceOnPhone() {
    if(isPhoneLayout())setWorkspaceCollapsed(true);
  }

  function openMapPanel(focusSelector) {
    activateWorkspace('useful',false);
    const target=focusSelector?document.querySelector(focusSelector):null;
    if(target)requestAnimationFrame(()=>target.scrollIntoView({block:'start'}));
  }

  function renderNearbyOverlay() {
    if (nearbyCircle) { map.removeLayer(nearbyCircle); nearbyCircle = null; }
    if (nearbyMarker) { map.removeLayer(nearbyMarker); nearbyMarker = null; }
    const note = $('nearby-note');
    if (!nearbyState.active) { note.classList.add('hidden'); $('near-clear').classList.add('hidden'); return; }
    const metres = nearbyState.radiusMiles * 1609.344;
    nearbyCircle = L.circle([nearbyState.lat, nearbyState.lon], { radius: metres, color:'#56d6a4', weight:1.5, fillColor:'#56d6a4', fillOpacity:0.05, interactive:false }).addTo(map);
    nearbyMarker = L.circleMarker([nearbyState.lat, nearbyState.lon], { radius:6, color:'#fff', weight:2, fillColor:'#56d6a4', fillOpacity:1, interactive:false }).addTo(map);
    note.classList.remove('hidden'); $('near-clear').classList.remove('hidden');
    // A small pill that says what the map is limited to, with its own off
    // switch: the only other way out was buried in the side panel.
    const where = nearbyState.source === 'device' ? 'you' : nearbyState.source === 'airport' ? `${nearbyState.label} airport` : 'this spot';
    const label = overheadActive ? `Aircraft within ${nearbyState.radiusMiles} miles of ${where}` : `Within ${nearbyState.radiusMiles} miles of ${where}`;
    note.innerHTML = `<span>${esc(label)}</span><button type="button" class="nearby-note-close" aria-label="Show everything again">×</button>`;
    note.querySelector('.nearby-note-close')?.addEventListener('click', () => {
      if (overheadActive && $('overhead-now')) $('overhead-now').click();
      else $('near-clear')?.click();
    });
  }

  function setNearby(lat, lon, source) {
    nearbyState = { active:true, lat:Number(lat), lon:Number(lon), radiusMiles:Number($('near-radius').value || 10), source };
    renderNearbyOverlay(); renderMap(); renderStats(); renderRanking();
  }

  function stopOverheadRefresh() {
    clearTimeout(overheadRefreshTimer);
    overheadRefreshTimer=null;
    overheadRefreshRequest+=1;
  }

  function seedNearbyAircraftFromLiveMap() {
    if(nearbyState.lat==null||nearbyState.lon==null)return;
    const radiusKm=(Number(nearbyState.radiusMiles)||25)*1.609344;
    const retained=activities.filter(row=>['aircraft','sar'].includes(row.type)
      &&Number.isFinite(Number(row.lat))&&Number.isFinite(Number(row.lon))
      &&distanceKm(nearbyState.lat,nearbyState.lon,Number(row.lat),Number(row.lon))<=radiusKm);
    if(!retained.length)return;
    nearbyAircraftRows=retained;
    nearbyAircraftIds=new Set(retained.map(row=>row.id));
    renderMap();renderStats();renderRanking();
  }

  async function refreshOverheadAircraft({announce=false}={}) {
    if(!overheadActive||nearbyState.lat==null||nearbyState.lon==null)return;
    const requestId=++overheadRefreshRequest;
    const controller=new AbortController();
    const requestTimeout=setTimeout(()=>controller.abort(),7000);
    try{
      const radiusMiles=Number(nearbyState.radiusMiles)||25;
      const response=await fetch(`/api/aircraft/nearby?lat=${encodeURIComponent(nearbyState.lat)}&lon=${encodeURIComponent(nearbyState.lon)}&radius_miles=${encodeURIComponent(radiusMiles)}`,{cache:'no-store',signal:controller.signal});
      const payload=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(payload.detail||'aircraft lookup unavailable');
      if(requestId!==overheadRefreshRequest||!overheadActive)return;
      const received=Array.isArray(payload.activities)?payload.activities:[];
      // If the precise point feed is temporarily unavailable, keep anything
      // already received by the broad live map instead of making every icon
      // disappear while the automatic retry runs.
      if(received.length||!payload.degraded)nearbyAircraftRows=received;
      nearbyAircraftIds=new Set(nearbyAircraftRows.map(row=>row.id));
      const existingIds=new Set(activities.map(row=>row.id));
      nearbyAircraftRows.forEach(row=>{
        if(row?.id&&!existingIds.has(row.id))overheadInjectedAircraftIds.add(row.id);
      });
      replaceActivities([...new Map([...activities,...nearbyAircraftRows].map(row=>[row.id,row])).values()]);
      renderMap();renderStats();renderRanking();renderCoolSummary();
      const note=$('overhead-now')?.querySelector('small');
      if(note)note.textContent=payload.degraded
        ? `${nearbyAircraftRows.length} retained aircraft • live lookup retrying`
        : `${nearbyAircraftRows.length} live aircraft • refreshing automatically`;
      if(skyPointer.active)renderSkyPointer();
      if(cameraView.active&&!cameraView.frame)cameraView.frame=requestAnimationFrame(drawCameraView);
      if(airportOpen&&nearbyState.source==='airport'&&!selectedId&&$('detail-title')?.textContent===`${airportOpen.name} Airport`)renderAirportPanel(airportOpen);
      if(pendingPlanesList){pendingPlanesList=false;showPlanesList();}
      else if(announce)showToast(payload.degraded
        ? (nearbyAircraftRows.length?`Showing ${nearbyAircraftRows.length} retained aircraft while the live lookup retries.`:'The live aircraft lookup is retrying; nothing retained nearby yet.')
        : (nearbyAircraftRows.length?`${nearbyAircraftRows.length} aircraft near you.`:'No aircraft near you right now.'));
    }catch(error){
      const note=$('overhead-now')?.querySelector('small');
      if(note)note.textContent=nearbyAircraftRows.length
        ? `${nearbyAircraftRows.length} retained aircraft • live lookup retrying`
        : 'Live lookup is retrying automatically';
      if(pendingPlanesList){pendingPlanesList=false;showPlanesList();}
      if(announce)showToast(nearbyAircraftRows.length
        ? `Showing ${nearbyAircraftRows.length} retained aircraft while the live lookup retries.`
        : 'The live aircraft lookup is retrying automatically.');
    }finally{
      clearTimeout(requestTimeout);
      if(requestId===overheadRefreshRequest&&overheadActive){
        clearTimeout(overheadRefreshTimer);
        overheadRefreshTimer=setTimeout(()=>refreshOverheadAircraft(),12000);
      }
    }
  }

  function clearTransientLocationModes({clearNearestStop=true}={}) {
    stopOverheadRefresh();
    overheadActive=false;
    if(overheadInjectedAircraftIds.size){
      replaceActivities(activities.filter(row=>!overheadInjectedAircraftIds.has(row.id)));
    }
    overheadInjectedAircraftIds=new Set();
    nearbyAircraftRows=[];
    nearbyAircraftIds=new Set();
    nearbyState.active=false;
    const overhead=$('overhead-now');
    overhead?.classList.remove('active');
    const overheadNote=overhead?.querySelector('small');
    if(overheadNote)overheadNote.textContent='Aircraft received within 50 miles';
    if(overheadAutoEnabledAircraft)enabledActivityTypes.delete('aircraft');
    overheadAutoEnabledAircraft=false;
    if(clearNearestStop&&transientNearestBusPlaceId){
      places=places.filter(place=>place.id!==transientNearestBusPlaceId);
      transientNearestBusPlaceId=null;
      renderPlaces();
    }
    filteredCacheKey='';
    renderNearbyOverlay();
    updateLayerToggleUi();
  }

  function openPlusDialog() {
    trackProduct('radar_plus_opened','radar-plus');
    const dialog=$('plus-dialog'); if(!dialog)return;
    loadBillingStatus();
    if(typeof dialog.showModal==='function') dialog.showModal(); else dialog.setAttribute('open','');
  }

  function loadDiscoveryLocalState() {
    try {
      const parsed=JSON.parse(localStorage.getItem(DISCOVERY_COLLECTION_KEY)||'[]');
      const rows=Array.isArray(parsed)?parsed:(Array.isArray(parsed?.discoveries)?parsed.discoveries:Object.values(parsed?.discoveries||{}));
      discoveryCollection=new Map(rows.filter(row=>row&&typeof row==='object'&&String(row.category||'').toLowerCase()!=='sea').map((row,index)=>[String(row.id||row.key||row.subtype||`saved-${index}`),row]));
    } catch { discoveryCollection=new Map(); }
    try {
      const raw=JSON.parse(localStorage.getItem(DISCOVERY_INTERESTS_KEY)||'[]');
      discoveryInterests=new Set(Array.isArray(raw)?raw.filter(value=>['air','rail'].includes(value)):[]);
    } catch { discoveryInterests=new Set(); }
    try { discoveryPreviousVisit=Math.max(0,Number(localStorage.getItem(DISCOVERY_LAST_VISIT_KEY)||0)); }
    catch { discoveryPreviousVisit=0; }
  }

  function saveDiscoveryLocalState() {
    try { localStorage.setItem(DISCOVERY_COLLECTION_KEY,JSON.stringify([...discoveryCollection.values()])); } catch {}
    try { localStorage.setItem(DISCOVERY_INTERESTS_KEY,JSON.stringify([...discoveryInterests])); } catch {}
  }

  function normaliseCollectible(row,index=0) {
    if(!row||typeof row!=='object')return null;
    const subtype=String(row.subtype||row.key||'').trim();
    const category=String(row.category||'').trim().toLowerCase();
    if(!subtype||!['air','rail'].includes(category))return null;
    return {
      id:String(row.id||`${category}-${subtype}-${index}`), key:String(row.key||subtype), category, subtype,
      title:String(row.title||row.badge||subtype.replace(/-/g,' ')), explanation:String(row.explanation||'A source-classified live discovery.'),
      icon:String(row.icon||({air:'▲',rail:'◆'}[category])), badge:String(row.badge||row.title||subtype),
      safety_note:row.safety_note?String(row.safety_note):null,
    };
  }

  function fallbackDiscoveryMissions() {
    const idFor=(subtype)=>fallbackDiscoveryCatalogue.find(item=>item.subtype===subtype)?.id;
    return [
      {id:'across-britain',icon:'◇',title:'Britain in Motion',description:'Find one air and one rail discovery on the live map.',targets:[],categories:['air','rail'],total:2,distinct:true,safety_note:'Map-only: never travel towards a moving target or restricted site.'},
      {id:'rail-specials',icon:'◆',title:'Special Railway Week',description:'Find two different kinds of special railway movement.',targets:['steam','railtour-special','test-measurement'].map(idFor).filter(Boolean),categories:['rail'],total:2,distinct:true,safety_note:'Observe only from lawful public places and stay behind railway boundaries.'},
      {id:'air-power',icon:'▲',title:'Aircraft Families',description:'Find two different identified military aircraft roles.',targets:['combat-aircraft','military-helicopter','military-tanker','military-transport'].map(idFor).filter(Boolean),categories:['air'],total:2,distinct:true,safety_note:'This is a map mission. Do not approach airfields or military sites.'},
    ];
  }

  async function loadDiscoveryData(force=false) {
    if(discoveryLoadPromise&&!force)return discoveryLoadPromise;
    discoveryLoadPromise=(async()=>{
      let catalogueOk=false;
      try {
        const response=await fetch('/api/discoveries/catalogue',{cache:'no-store'});
        if(!response.ok)throw new Error('catalogue unavailable');
        const payload=await response.json();
        const rows=Array.isArray(payload)?payload:(payload.collectibles||payload.catalogue||payload.items||[]);
        const parsed=rows.map(normaliseCollectible).filter(Boolean);
        if(parsed.length){discoveryCatalogue=parsed;catalogueOk=true;}
      } catch {}
      if(!discoveryCatalogue.length)discoveryCatalogue=fallbackDiscoveryCatalogue.map((row,index)=>normaliseCollectible(row,index)).filter(Boolean);
      discoveryApiAvailable=catalogueOk;
      renderDiscoveryExperience();
    })().finally(()=>{discoveryLoadPromise=null;});
    return discoveryLoadPromise;
  }

  function discoveryCategoryForActivity(a) {
    const declared=String(a?.cool_category||'').toLowerCase();
    if(['air','rail','sea','rescue'].includes(declared))return declared;
    if(['aircraft'].includes(a?.type))return 'air';
    if(a?.type==='train')return 'rail';
    if(a?.type==='vessel')return 'sea';
    if(['sar','rescue'].includes(a?.type))return 'rescue';
    return null;
  }

  function collectibleForActivity(a) {
    const subtype=String(coolInfo(a).subtype||a?.cool_subtype||'');
    const category=discoveryCategoryForActivity(a);
    return discoveryCatalogue.find(item=>item.subtype===subtype&&item.category===category)
      || discoveryCatalogue.find(item=>item.key===subtype)
      || fallbackDiscoveryCatalogue.find(item=>item.subtype===subtype&&item.category===category)
      || null;
  }

  function activityIsCollectible(a) {
    if(!a||a.kind==='event'||!coolInfo(a).candidate)return false;
    const category=discoveryCategoryForActivity(a);
    return ['air','rail'].includes(category)&&!isRescueContextActivity(a);
  }

  function collectedRecordFor(item) {
    if(!item)return null;
    return [...discoveryCollection.values()].find(row=>String(row.id||'')===item.id||String(row.key||'')===item.key||String(row.subtype||'')===item.subtype)||null;
  }

  function collectedRecordForActivity(a) {
    const item=collectibleForActivity(a);
    return collectedRecordFor(item)||[...discoveryCollection.values()].find(row=>row.activity_id===a?.id)||null;
  }

  function discoveryIdentity(a,record=null) {
    if(record?.identity_level){
      return {level:record.identity_level,label:record.identity_level==='exact'?'Identified':'Type known',note:record.identity_note||''};
    }
    const exact=a?.type==='train'?Boolean(a.vehicle_ref):(['aircraft','vessel'].includes(a?.type)?Boolean(a.vehicle_ref||a.source_id):false);
    if(exact)return {level:'exact',label:'Identified',note:'Matched to this live vehicle.'};
    if(a?.type==='train')return {level:'type',label:'Type known',note:'Exact locomotive unknown.'};
    return {level:'type',label:'Type known',note:'Exact vehicle unknown.'};
  }

  function discoveryFreshness(a,record=null) {
    const seconds=record?.age_seconds==null?Math.max(0,(Date.now()-new Date(a?.last_updated||0).getTime())/1000):Number(record.age_seconds);
    return Number.isFinite(seconds)?relativeAge(seconds):'freshness unavailable';
  }

  function isoWeekKey(value=Date.now()) {
    const date=new Date(value);if(Number.isNaN(date.getTime()))return null;
    const day=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),date.getUTCDate()));
    day.setUTCDate(day.getUTCDate()+4-(day.getUTCDay()||7));
    const yearStart=new Date(Date.UTC(day.getUTCFullYear(),0,1));
    const week=Math.ceil((((day-yearStart)/86400000)+1)/7);
    return `${day.getUTCFullYear()}-W${String(week).padStart(2,'0')}`;
  }

  function currentMissionWeek() { return discoveryWeek||isoWeekKey(); }
  function discoveryWeeks(record) {
    const raw=record?.weeks||record?.mission_weeks||[];
    return Array.isArray(raw)?raw.map(String).filter(Boolean):[];
  }
  function recordedThisMissionWeek(record) {
    const week=currentMissionWeek();
    return Boolean(week&&discoveryWeeks(record).includes(week));
  }

  function isNewDiscoveryActivity(a) {
    if(!discoveryPreviousVisit)return false;
    const updated=new Date(a?.last_updated||0).getTime();
    return Number.isFinite(updated)&&updated>discoveryPreviousVisit;
  }

  function activityMatchesInterests(a) {
    return discoveryInterests.size>0&&discoveryInterests.has(discoveryCategoryForActivity(a));
  }

  function setDiscoveryView(view) {
    discoveryView=['now','guide'].includes(view)?view:'now';
    document.querySelectorAll('[data-cool-view]').forEach(button=>{
      const active=button.dataset.coolView===discoveryView;
      button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));
    });
    document.querySelectorAll('[data-cool-view-panel]').forEach(panel=>panel.classList.toggle('active',panel.dataset.coolViewPanel===discoveryView));
    renderDiscoveryExperience();
    if(discoveryView==='guide')trackProduct('field_guide_opened','field-guide');
  }

  function renderDiscoveryNowStatus() {
    const box=$('discovery-now-status');if(!box)return;
    const candidates=globalCoolRows().filter(activityIsCollectible);
    const newCount=discoveryPreviousVisit?candidates.filter(isNewDiscoveryActivity).length:0;
    const interestCount=discoveryInterests.size?candidates.filter(activityMatchesInterests).length:0;
    const found=discoveryCatalogue.filter(item=>collectedRecordFor(item)).length;
    const total=discoveryCatalogue.length;
    const renderKey=[newCount,interestCount,found,total,discoveryPreviousVisit?1:0,discoveryInterests.size,discoveryApiAvailable?1:0].join('|');
    if(renderKey===discoveryNowRenderKey)return;discoveryNowRenderKey=renderKey;
    const newCopy=discoveryPreviousVisit?`<b>${newCount}</b><span>new since last visit</span>`:'<b>NEW</b><span>Your first live hunt</span>';
    box.innerHTML=`<div class="discovery-status-chip new">${newCopy}</div><button type="button" class="discovery-status-chip collection" data-open-guide><b>${found}/${total||'—'}</b><span>Field Guide</span></button>${discoveryInterests.size?`<div class="discovery-status-chip interests"><b>${interestCount}</b><span>match your interests</span></div>`:''}${!discoveryApiAvailable?'<div class="discovery-status-chip local"><b>PRIVATE</b><span>Local field guide mode</span></div>':''}`;
    box.querySelector('[data-open-guide]')?.addEventListener('click',()=>setDiscoveryView('guide'));
    const navCount=$('field-guide-nav-count');if(navCount)navCount.textContent=`${found} found`;
  }

  function renderDiscoveryInterests() {
    const box=$('discovery-interests');if(!box)return;
    const choices=[['air',iconSvg('aircraft'),'Air'],['rail',iconSvg('train'),'Rail'],['sea',iconSvg('vessel'),'Sea']];
    box.innerHTML=`<button type="button" class="interest-chip${discoveryInterests.size?'':' active'}" data-discovery-interest="all">All</button>${choices.map(([id,icon,label])=>`<button type="button" class="interest-chip${discoveryInterests.has(id)?' active':''}" data-discovery-interest="${id}" aria-pressed="${discoveryInterests.has(id)}">${icon} ${label}</button>`).join('')}`;
    box.querySelectorAll('[data-discovery-interest]').forEach(button=>button.addEventListener('click',()=>{
      const value=button.dataset.discoveryInterest;
      if(value==='all')discoveryInterests.clear();
      else if(discoveryInterests.has(value))discoveryInterests.delete(value);else discoveryInterests.add(value);
      saveDiscoveryLocalState();renderDiscoveryExperience();trackProduct('discovery_interest_changed',value);
    }));
  }

  function renderFieldGuide() {
    const progress=$('field-guide-progress'),grid=$('field-guide-grid');if(!progress||!grid)return;
    const renderKey=JSON.stringify([discoveryCatalogue, [...discoveryCollection.values()], [...discoveryInterests].sort()]);
    if(renderKey===discoveryGuideRenderKey)return;discoveryGuideRenderKey=renderKey;
    const found=discoveryCatalogue.filter(item=>collectedRecordFor(item)).length,total=discoveryCatalogue.length;
    const percent=total?Math.round(found/total*100):0;
    progress.innerHTML=`<div><strong>${found} of ${total||'—'} discovered</strong><span>${percent}% of this field guide</span></div><div class="field-guide-meter"><i style="width:${percent}%"></i></div>`;
    renderDiscoveryInterests();
    let rows=[...discoveryCatalogue];
    if(discoveryInterests.size)rows=rows.filter(item=>discoveryInterests.has(item.category));
    rows.sort((a,b)=>Number(Boolean(collectedRecordFor(b)))-Number(Boolean(collectedRecordFor(a)))||a.category.localeCompare(b.category)||a.title.localeCompare(b.title));
    if(!rows.length){grid.innerHTML='<p class="left-empty">No entries match those interests yet.</p>';return;}
    grid.innerHTML=rows.map(item=>{
      const saved=collectedRecordFor(item),identity=saved?discoveryIdentity(null,saved):null;
      const when=saved&&timeText(saved.first_discovered_at||saved.discovered_at||saved.claimed_at);
      return `<article class="field-guide-card${saved?' discovered':' locked'}"><div class="field-guide-card-head"><span class="field-guide-icon ${esc(item.category)}">${iconSvg(CATEGORY_ICON[item.category] || 'space')}</span><div><small>${esc(item.category.toUpperCase())}</small><strong>${esc(item.title)}</strong></div><b>${saved?'FOUND':'TO FIND'}</b></div><p>${esc(item.explanation)}</p>${saved?`<div class="field-guide-proof"><span>${esc(identity.label)}</span>${recordedThisMissionWeek(saved)?'<span>Logged this week</span>':''}${saved.broad_region?`<span>${esc(saved.broad_region)}</span>`:''}${when?`<span>First found ${esc(when)}</span>`:''}</div>${identity.note?`<small class="field-guide-truth">${esc(identity.note)}</small>`:''}`:`<small class="field-guide-hint">Find a live, source-classified example on the map to add it.</small>`}${item.safety_note?`<small class="field-guide-safety">${esc(item.safety_note)}</small>`:''}</article>`;
    }).join('');
  }

  function missionProgress(mission) {
    const saved=[...discoveryCollection.values()].filter(recordedThisMissionWeek);
    const targets=Array.isArray(mission.targets)?mission.targets.map(String):[];
    const categories=Array.isArray(mission.categories)?mission.categories.map(String):[];
    let count=0;
    if(mission.distinct==='category'){
      const found=new Set();
      saved.forEach(row=>{
        const item=discoveryCatalogue.find(x=>x.id===String(row.id)||x.key===String(row.key)||x.subtype===String(row.subtype));
        const category=String(row.category||item?.category||'');
        const targetMatch=!targets.length||targets.includes(String(row.id))||targets.includes(String(row.key))||targets.includes(String(item?.id));
        if(targetMatch&&(!categories.length||categories.includes(category)))found.add(category);
      });
      count=found.size;
    } else if(targets.length){
      const found=new Set();
      saved.forEach(row=>{
        const item=discoveryCatalogue.find(x=>x.id===String(row.id)||x.key===String(row.key)||x.subtype===String(row.subtype));
        for(const target of targets)if(target===String(row.id)||target===String(row.key)||target===String(row.subtype)||target===item?.id)found.add(target);
      });
      count=found.size;
    } else if(categories.length) {
      count=mission.distinct!==false?new Set(saved.map(row=>row.category).filter(category=>categories.includes(category))).size:saved.filter(row=>categories.includes(row.category)).length;
    }
    const total=Math.max(1,Number(mission.total||targets.length||categories.length||1));
    return {count:Math.min(count,total),total,complete:count>=total};
  }

  function renderDiscoveryMissions() {
    const box=$('discovery-missions');if(!box)return;
    const renderKey=JSON.stringify([discoveryWeek,discoveryMissions,[...discoveryCollection.values()].map(row=>[row.id,row.key,row.subtype,row.category,discoveryWeeks(row)])]);
    if(renderKey===discoveryMissionRenderKey)return;discoveryMissionRenderKey=renderKey;
    if(!discoveryMissions.length){box.innerHTML='<p class="left-empty">Weekly missions are unavailable. Live discoveries still work.</p>';return;}
    box.innerHTML=discoveryMissions.map(mission=>{
      const state=missionProgress(mission),percent=Math.round(state.count/state.total*100);
      const end=timeText(mission.ends_at);
      return `<article class="mission-card${state.complete?' complete':''}"><div class="mission-card-head"><span>${iconSvg('space')}</span><div><small>${state.complete?'COMPLETE':'MAP MISSION'}</small><strong>${esc(mission.title||'Weekly mission')}</strong></div><b>${state.count}/${state.total}</b></div><p>${esc(mission.description||'Discover live activity on the map.')}</p><div class="mission-meter"><i style="width:${percent}%"></i></div>${end?`<small class="mission-ends">Available until ${esc(end)}</small>`:''}<small class="mission-safety">${esc(mission.safety_note||'Map-only. Observe safely from lawful public places.')}</small></article>`;
    }).join('');
  }

  function renderDiscoveryExperience() {
    renderDiscoveryNowStatus();renderDiscoveryMissions();renderFieldGuide();
  }

  function clearDiscoveryDetail() {
    const box=$('detail-discovery');if(box){box.classList.add('hidden');box.innerHTML='';}
    $('save-discovery')?.classList.add('hidden');$('share-discovery')?.classList.add('hidden');
  }

  function renderDiscoveryDetail(a) {
    const box=$('detail-discovery'),save=$('save-discovery'),share=$('share-discovery');
    if(!box||!save||!share||currentWorkspace!=='cool'||!coolInfo(a).candidate){clearDiscoveryDetail();return;}
    const collectible=activityIsCollectible(a),record=collectedRecordForActivity(a),identity=discoveryIdentity(a,record);
    const confidence=record?.confidence?`${String(record.confidence).replace(/^./,c=>c.toUpperCase())} confidence`:'Live find';
    const freshness=discoveryFreshness(a,record);
    box.classList.remove('hidden');
    if(!collectible){
      box.innerHTML='<div class="discovery-truth-row"><b class="identity-pill context">LIVE CONTEXT ONLY</b><span>Rescue and emergency activity is never collectible.</span></div><small>Use the official information responsibly. Never travel towards an active response.</small>';
      save.classList.add('hidden');share.classList.add('hidden');return;
    }
    if(a.type==='train'&&identity.level==='type'){
      box.classList.add('hidden');box.innerHTML='';
      save.classList.remove('hidden');share.classList.add('hidden');
      save.disabled=discoveryClaimPending;save.textContent=record?'Field Guide ✓':'Save to Field Guide';
      return;
    }
    const plainIdentity=identity.label;
    box.innerHTML=`<div class="discovery-truth-row"><b class="identity-pill ${identity.level}">${esc(plainIdentity)}</b><span>Updated ${esc(freshness)}</span></div>${identity.level==='type'?`<small>${esc(identity.note)}</small>`:''}`;
    // One Share button: the live link. (A second, identically named one
    // used to appear beside it.)
    save.classList.remove('hidden');share.classList.add('hidden');
    save.disabled=discoveryClaimPending;
    save.textContent=record?'Field Guide ✓':'Save to Field Guide';
  }

  function storeDiscovery(record) {
    if(!record)return;
    const key=String(record.id||record.key||record.subtype||record.activity_id);
    const prior=discoveryCollection.get(key)||[...discoveryCollection.values()].find(row=>row.id===record.id||row.key===record.key||row.subtype===record.subtype)||null;
    const week=discoveryWeek||isoWeekKey(record.discovered_at)||isoWeekKey();
    const weeks=new Set(discoveryWeeks(prior));if(week)weeks.add(week);
    discoveryCollection.set(key,{...prior,...record,first_discovered_at:prior?.first_discovered_at||prior?.discovered_at||record.discovered_at||new Date().toISOString(),claimed_at:record.discovered_at||new Date().toISOString(),weeks:[...weeks].sort()});
    saveDiscoveryLocalState();renderDiscoveryExperience();
  }

  async function claimSelectedDiscovery() {
    const a=activityById(selectedId);if(!a)return;
    if(!activityIsCollectible(a)){showToast('Rescues can’t be added to your Field Guide.');return;}
    const existing=collectedRecordForActivity(a);
    if(existing){setDiscoveryView('guide');showToast('Already in your Field Guide.');return;}
    if(discoveryClaimPending)return;
    discoveryClaimPending=true;renderDiscoveryDetail(a);
    try {
      let response;
      try {response=await fetch('/api/discoveries/claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({activity_id:a.id})});}
      catch {throw new Error('Could not confirm this find. Nothing was saved; please try again.');}
      if(!response.ok){
        let payload=null;try{payload=await response.json();}catch{}
        const detail=payload?.detail;
        if(response.status===404&&(!detail||detail==='Not Found'))throw new Error('Could not confirm this find. Nothing was saved; please try again.');
        throw new Error(typeof detail==='object'?(detail.message||'Discovery could not be verified'):(detail||'Discovery could not be verified'));
      }
      const payload=await response.json();
      if(!payload?.discovery?.id)throw new Error('The live discovery could not be verified. Nothing was saved; please retry.');
      storeDiscovery(payload.discovery);
      trackProduct('discovery_claimed',discoveryCategoryForActivity(a)||'other');showToast(`${collectibleForActivity(a)?.title||a.name} added to your Field Guide.`);
    } catch(error) {showToast(error?.message||'Couldn’t save that right now.');}
    finally {discoveryClaimPending=false;renderDiscoveryDetail(a);}
  }

  async function shareSelectedDiscovery() {
    const a=activityById(selectedId);if(!a||!activityIsCollectible(a))return;
    const info=coolInfo(a),record=collectedRecordForActivity(a),identity=discoveryIdentity(a,record);
    const title=record?.title||collectibleForActivity(a)?.title||a.name;
    const lines=[`UK Activity Radar — ${title}`,`Live find: ${a.name}`,`Why it matters: ${info.reasons[0]||a.cool_explanation||'Unusual live activity'}`,`Identity: ${identity.label} — ${identity.note}`,`Freshness: updated ${discoveryFreshness(a,record)}`,record?.broad_region?`Broad area: ${record.broad_region}`:null,a.source?`Source: ${a.source}`:null,'No precise position is included in this share.'].filter(Boolean);
    const text=lines.join('\n');
    try {
      await shareOrCopy({title,text,url:location.origin,successMessage:'Factual discovery card copied—no precise location included.'});
      trackProduct('discovery_shared',discoveryCategoryForActivity(a)||'other');
    } catch(error){if(error?.name!=='AbortError')showToast('Sharing is unavailable in this browser.');}
  }

  function loadFollowed() {
    try {
      const raw = JSON.parse(localStorage.getItem('ukRadarFollowed') || '[]');
      if (Array.isArray(raw)) raw.forEach(id => typeof id === 'string' && followed.add(id));
    } catch {}
  }
  // A tab left open across a deploy keeps running the old app. When the page
  // comes back into view (or every 10 minutes), check the published version
  // and reload once if it has changed.
  const LOADED_APP_VERSION=(document.querySelector('script[src*="/static/app.js"]')?.getAttribute('src').match(/v=(\d+)/)||[])[1]||'';
  let versionCheckAt=0;
  async function reloadIfNewVersion() {
    if(!LOADED_APP_VERSION||Date.now()-versionCheckAt<60000)return;
    versionCheckAt=Date.now();
    try {
      const html=await (await fetch('/',{cache:'no-store'})).text();
      const live=(html.match(/\/static\/app\.js\?v=(\d+)/)||[])[1];
      if(!live||live===LOADED_APP_VERSION)return;
      // Reload once per published version, so a stale cache can never loop.
      let tried='';try{tried=sessionStorage.getItem('ukRadarReloadedFor')||'';}catch{}
      if(tried===live)return;
      try{sessionStorage.setItem('ukRadarReloadedFor',live);}catch{}
      location.reload();
    } catch {}
  }
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden)reloadIfNewVersion(); });
  setInterval(()=>{ if(!document.hidden)reloadIfNewVersion(); },600000);

  function saveFollowed() { try { localStorage.setItem('ukRadarFollowed', JSON.stringify([...followed])); } catch {} }
  // What each saved thing was, so it can still be named when it is not live.
  let followedInfo={};
  try { followedInfo=JSON.parse(localStorage.getItem('ukRadarFollowedInfoV1')||'{}')||{}; } catch { followedInfo={}; }
  function saveFollowedInfo() { try { localStorage.setItem('ukRadarFollowedInfoV1', JSON.stringify(followedInfo)); } catch {} }
  function rememberFollowed(a) {
    if(!a?.id)return;
    const detail=['aircraft','sar'].includes(a.type)?(a.aircraft_model||a.aircraft_type_code||a.operator||''):(a.destination?`to ${humanTransitLabel(a.destination)}`:humanTransitLabel(a.operator||''));
    const next={type:a.type,name:a.name,detail};
    const before=followedInfo[a.id];
    if(before&&before.name===next.name&&before.detail===next.detail)return;
    followedInfo[a.id]=next;saveFollowedInfo();
  }

  // Saved trains: alert when one is cancelled, becomes late or changes
  // platform. The server works out the alerts; each is shown once.
  const SAVED_TRAIN_ALERTS_KEY='ukRadarSavedTrainAlertsV1';
  let savedTrainAlerted={};
  try { savedTrainAlerted=JSON.parse(localStorage.getItem(SAVED_TRAIN_ALERTS_KEY)||'{}')||{}; } catch { savedTrainAlerted={}; }
  function savedTrainIds() { return [...followed].filter(id=>/^(nr|td):/.test(id)).slice(0,12); }
  function notifySavedTrain(alert, trainId) {
    showToast(alert.title);
    if(!document.hidden||!('Notification' in window)||Notification.permission!=='granted')return;
    navigator.serviceWorker?.getRegistration?.().then(registration=>registration?.showNotification(alert.title,{
      body:alert.body, tag:alert.key, icon:'/static/icon-192.png', data:{url:`/?open=${encodeURIComponent(trainId)}`},
    })).catch(()=>{});
  }
  async function checkSavedTrains() {
    const ids=savedTrainIds();
    if(!ids.length)return;
    try {
      const response=await fetch(`/api/trains/status?ids=${encodeURIComponent(ids.join(','))}`,{cache:'no-store'});
      if(!response.ok)return;
      const payload=await response.json();
      for(const train of payload.trains||[]){
        if(!train?.found)continue;
        const seen=Array.isArray(savedTrainAlerted[train.id])?savedTrainAlerted[train.id]:[];
        for(const alert of train.alerts||[]){
          if(seen.includes(alert.key))continue;
          seen.push(alert.key);
          notifySavedTrain(alert,train.id);
        }
        savedTrainAlerted[train.id]=seen.slice(-12);
      }
      for(const id of Object.keys(savedTrainAlerted))if(!followed.has(id))delete savedTrainAlerted[id];
      try { localStorage.setItem(SAVED_TRAIN_ALERTS_KEY,JSON.stringify(savedTrainAlerted)); } catch {}
    } catch {}
  }
  // Yours: saved stations, stops and trains answered at the top of the Map
  // panel, so opening the app is enough to know how the journey looks.
  let yoursRequest=0, yoursKey='', yoursAt=0;
  let yoursShownAtStart=false;
  function yoursBoardLine(row, kind) {
    if(!row)return null;
    const place=humanTransitLabel(row.destination||row.route||row.name||'');
    if(kind==='bus'){
      const when=row.minutes!=null?(Number(row.minutes)<=0?'due now':`in ${row.minutes} min`):boardClock(row.expected||row.scheduled||row.clock);
      return {text:[[row.route||row.line,place&&`to ${place}`].filter(Boolean).join(' '),when].filter(Boolean).join(' · '),tone:''};
    }
    const booked=boardClock(row.scheduled||row.clock||row.expected), expected=boardClock(row.expected), delay=Number(row.delay_minutes);
    const cancelled=row.cancelled||/cancel/i.test(String(row.status||''));
    const late=!cancelled&&Number.isFinite(delay)&&delay>=1&&expected&&expected!==booked;
    const status=cancelled?'Cancelled':late?`expected ${expected}`:'On time';
    return {text:[`${booked} ${place}`.trim(),row.platform?`Plat ${row.platform}`:row.usual_platform?`usually plat ${row.usual_platform.platform}`:null,status].filter(Boolean).join(' · '),tone:cancelled?'is-bad':late?'is-late':''};
  }
  async function renderYours(force=false) {
    const section=$('yours'),list=$('yours-list');if(!section||!list)return;
    const wantedPlaces=[...savedPlaces.values()].filter(place=>['rail_station','bus_stop'].includes(place.place_type)).slice(0,3);
    const trainIds=savedTrainIds().slice(0,4);
    const key=[...wantedPlaces.map(place=>place.id),...trainIds].join('|');
    if(!key){section.classList.add('hidden');list.innerHTML='';yoursKey='';return;}
    if(!force&&key===yoursKey&&Date.now()-yoursAt<45000)return;
    yoursKey=key;yoursAt=Date.now();
    const request=++yoursRequest;
    section.classList.remove('hidden');
    const row=(attribute,icon,title,text,tone)=>`<button type="button" class="location-quick-action job-row yours-row" ${attribute}><span>${icon}</span><div><strong>${esc(title)}</strong><small class="${esc(tone||'')}">${esc(text)}</small></div><b>›</b></button>`;
    if(!list.children.length)list.innerHTML=wantedPlaces.map(place=>row(`data-yours-place="${esc(place.id)}"`,placeIcons[place.place_type]||iconSvg('pin'),place.name,'Checking live times…','')).join('');
    const boards=wantedPlaces.map(place=>fetch(`/api/places/${encodeURIComponent(place.id)}/board?limit=4`,{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null));
    const trains=trainIds.length?fetch(`/api/trains/status?ids=${encodeURIComponent(trainIds.join(','))}`,{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null):Promise.resolve(null);
    const [boardPayloads,trainPayload]=await Promise.all([Promise.all(boards),trains]);
    if(request!==yoursRequest)return;
    const html=[];
    wantedPlaces.forEach((place,index)=>{
      const board=boardPayloads[index]?.board;
      const rail=place.place_type==='rail_station';
      const due=(rail?board?.departures:board?.rows)||[];
      const first=yoursBoardLine(due[0],rail?'departure':'bus');
      const then=rail&&due[1]?boardClock(due[1].scheduled||due[1].clock||due[1].expected):'';
      const text=!board?'Live times are unavailable right now':first?`${first.text}${then?` · then ${then}`:''}`:'Nothing due right now';
      html.push(row(`data-yours-place="${esc(place.id)}"`,placeIcons[place.place_type]||iconSvg('pin'),place.name,text,first?.tone));
    });
    for(const train of trainPayload?.trains||[]){
      if(!train?.found)continue;
      const title=train.destination?`${train.name} to ${humanTransitLabel(train.destination)}`:train.name;
      const state=train.cancelled?'Cancelled':train.arrived?'Arrived':trainStatusLabel({delay_minutes:train.delay_minutes});
      const platform=train.platform?(train.platform_changed?`now platform ${train.platform}`:`Plat ${train.platform}`):null;
      html.push(row(`data-yours-train="${esc(train.id)}"`,icons.train,title,[state==='Live'?'Running':state,platform].filter(Boolean).join(' · '),train.cancelled?'is-bad':Number(train.delay_minutes)>=5?'is-late':''));
    }
    list.innerHTML=html.join('');
    section.classList.toggle('hidden',!html.length);
    // On a phone the app starts on the bare map. With saved things to
    // answer, start with the panel open so they are the first thing seen.
    if(html.length&&!yoursShownAtStart){
      yoursShownAtStart=true;
      if(performance.now()<10000&&isPhoneLayout()&&!selectedId&&!selectedPlaceId&&currentWorkspace==='useful')setWorkspaceCollapsed(false);
    }
    list.querySelectorAll('[data-yours-place]').forEach(button=>button.addEventListener('click',async()=>{
      const place=savedPlaces.get(button.dataset.yoursPlace);if(!place)return;
      trackProduct('yours_opened',place.place_type);
      if(!places.some(p=>p.id===place.id))places.push(place);renderPlaces();await selectPlace(place.id,true);if(map.getZoom()<11)map.setZoom(11);
    }));
    list.querySelectorAll('[data-yours-train]').forEach(button=>button.addEventListener('click',()=>{
      trackProduct('yours_opened','train');
      if(!enabledActivityTypes.has('train')){enabledActivityTypes.add('train');updateLayerToggleUi();sendViewport();}
      focusActivityById(button.dataset.yoursTrain,Math.max(12,map.getZoom()));
    }));
  }
  setInterval(()=>{ if(!document.hidden&&currentWorkspace==='useful')renderYours(); },60000);
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden)renderYours(); });

  // Why this device cannot take phone alerts, in words someone can act on.
  // iPhones and iPads only deliver web alerts to a site added to the Home
  // Screen and opened from there. Android and computers do in the browser.
  function isAppleMobile(){ return /iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1); }
  function isInstalledApp(){ return Boolean(window.matchMedia?.('(display-mode: standalone)').matches||navigator.standalone); }
  function alertsNeedHomeScreen(){ return isAppleMobile()&&!isInstalledApp(); }
  function showInstallHints(){
    document.querySelectorAll('[data-install-hint]').forEach(el=>{el.hidden=!alertsNeedHomeScreen();});
  }
  const HOME_SCREEN_HINT='<div class="install-hint"><strong>Alerts need Radar on your Home Screen</strong><span>On iPhone and iPad, phone alerts only work when Radar is opened from your Home Screen. Tap <b>Share</b>, then <b>Add to Home Screen</b>, then open Radar from that icon.</span></div>';
  function pushUnsupportedReason() {
    if(alertsNeedHomeScreen())return 'On iPhone and iPad, alerts only work from the Home Screen app: tap Share, then Add to Home Screen, then open Radar from that icon.';
    if('serviceWorker' in navigator&&'Notification' in window&&'PushManager' in window)return '';
    return 'This browser does not support phone alerts.';
  }
  // Saving a train is enough to be told about it: turn phone alerts on for
  // saved trains alone, with no place or hours to choose.
  async function enableSavedTrainAlerts() {
    if(commuteAlertStatus.subscribed||!commuteAlertStatus.configured||pushUnsupportedReason())return false;
    try {
      const permission=Notification.permission==='granted'?'granted':await Notification.requestPermission();
      if(permission!=='granted')return false;
      const registration=await pushWorker();
      let subscription=await registration.pushManager.getSubscription();
      if(!subscription)subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:vapidKeyBytes(commuteAlertStatus.public_key)});
      const preferences={mode:'trains-only',saved_trains:savedTrainIds(),spotter:commuteAlertStatus.preferences?.spotter||null,journey:commuteAlertStatus.preferences?.journey||null};
      const response=await fetch('/api/alerts/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_token:alertDeviceToken(),subscription:subscription.toJSON(),preferences})});
      if(!response.ok)return false;
      commuteAlertStatus={...commuteAlertStatus,subscribed:true,preferences};renderCommuteAlertStatus();
      trackProduct('commute_alert_enabled','trains-only');
      return true;
    } catch { return false; }
  }
  // One place that makes sure this device is registered for phone alerts
  // and stores what it wants to hear about.
  // The worker that receives alerts. Never wait for it for ever: a button
  // that silently does nothing is worse than a plain reason.
  function pushWorker() {
    if(!navigator.serviceWorker)return Promise.reject(new Error('this browser has no background worker'));
    return Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_,reject)=>setTimeout(()=>reject(new Error('the app’s background worker didn’t start; close the app fully and open it again')),8000)),
    ]);
  }
  let pushProblem='';
  function pushBlockedText() {
    const ios=/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
    return ios?'Notifications are blocked for this app. Open iPhone Settings, then Notifications, find this app and allow notifications. Then tap Turn alerts on again.'
      :'Notifications are blocked for this site. Allow them in your browser’s site settings, then tap Turn alerts on again.';
  }
  async function savePushPreferences(preferences) {
    pushProblem='';
    if(!commuteAlertStatus.configured){pushProblem='Alerts are not available yet.';return false;}
    if(pushUnsupportedReason()){pushProblem=pushUnsupportedReason();return false;}
    try {
      const permission=Notification.permission==='granted'?'granted':await Notification.requestPermission();
      if(permission!=='granted'){pushProblem=pushBlockedText();renderCommuteAlertStatus();return false;}
      const registration=await pushWorker();
      let subscription=await registration.pushManager.getSubscription();
      if(!subscription)subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:vapidKeyBytes(commuteAlertStatus.public_key)});
      const response=await fetch('/api/alerts/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_token:alertDeviceToken(),subscription:subscription.toJSON(),preferences})});
      if(!response.ok){
        const payload=await response.json().catch(()=>({}));
        pushProblem=`The server didn’t accept this phone’s alert registration (${typeof payload.detail==='string'?payload.detail:`error ${response.status}`}).`;
        renderCommuteAlertStatus();return false;
      }
      commuteAlertStatus={...commuteAlertStatus,subscribed:true,preferences};
      renderCommuteAlertStatus();renderSpotter();
      return true;
    } catch(error) {
      pushProblem=`This phone couldn’t register for alerts (${error?.message||error?.name||'unknown error'}).`;
      renderCommuteAlertStatus();return false;
    }
  }
  function currentPushPreferences() {
    return commuteAlertStatus.subscribed&&commuteAlertStatus.preferences
      ? {...commuteAlertStatus.preferences,saved_trains:savedTrainIds()}
      : {mode:'trains-only',saved_trains:savedTrainIds()};
  }
  // Spotter alerts: unusual things near where you are when you switch it on.
  function renderSpotter() {
    showInstallHints();
    const state=$('spotter-state'),note=$('spotter-note'),button=$('spotter-toggle'),radius=$('spotter-radius');
    if(!state||!note||!button)return;
    const spotter=commuteAlertStatus.subscribed?commuteAlertStatus.preferences?.spotter:null;
    state.textContent=spotter?'ON':'OFF';state.classList.toggle('active',Boolean(spotter));
    button.textContent=spotter?'Turn off':'Turn on';
    button.classList.toggle('primary-button',!spotter);button.classList.toggle('mini-button',Boolean(spotter));
    if(spotter&&radius)radius.value=String(spotter.radius_miles);
    if(spotter){
      const legacy={rail:['steam','test','loco'],air:['military']};
      const kinds=new Set((spotter.kinds||[]).flatMap(kind=>legacy[kind]||[kind]));
      document.querySelectorAll('[data-spotter-kind]').forEach(box=>{box.checked=kinds.has(box.dataset.spotterKind);});
    }
    const unsupported=pushUnsupportedReason();
    note.textContent=!commuteAlertStatus.configured?'Alerts are not available yet.'
      : spotter?`On within ${spotter.radius_miles} miles of where you switched it on. Turn it off and on again to move it.`
      : (alertsNeedHomeScreen()?'':unsupported)||'A phone alert when one of these comes near you, 7am to 10pm.';
  }
  function spotterKinds(){ return [...document.querySelectorAll('[data-spotter-kind]:checked')].map(box=>box.dataset.spotterKind); }
  async function toggleSpotter() {
    const on=Boolean(commuteAlertStatus.subscribed&&commuteAlertStatus.preferences?.spotter);
    if(on){
      const ok=await savePushPreferences({...currentPushPreferences(),spotter:null});
      showToast(ok?'Spotter alerts are off.':'That couldn’t be changed right now.');
      return;
    }
    if(pushUnsupportedReason()){showToast(pushUnsupportedReason());return;}
    if(!commuteAlertStatus.configured){showToast('Alerts are not available yet.');return;}
    if(!requireAlertsPlus('Spotter alerts','spotter'))return;
    let fix;
    try { fix=await currentFix(); } catch(error){ showToast(error.message); return; }
    const radius=Number($('spotter-radius')?.value||10);
    const kinds=spotterKinds();
    if(!kinds.length){showToast('Tick at least one kind of thing to be told about.');return;}
    const ok=await savePushPreferences({...currentPushPreferences(),spotter:{lat:fix.lat,lon:fix.lon,radius_miles:radius,kinds}});
    showToast(ok?`Spotter alerts are on within ${radius} miles of you.`:'Alerts couldn’t be turned on. Check notifications are allowed for this site.');
    if(ok)trackProduct('spotter_enabled',String(radius));
  }
  async function sendTestAlert() {
    try {
      const response=await fetch('/api/alerts/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_token:alertDeviceToken()})});
      if(response.status===410||response.status===404){commuteAlertStatus={...commuteAlertStatus,subscribed:false,preferences:null};renderCommuteAlertStatus();showToast('Alerts had switched off on this device. Turn them on again.');return;}
      showToast(response.ok?'Test alert sent. It should arrive in a few seconds.':'The test alert could not be sent.');
    } catch { showToast('The test alert could not be sent.'); }
  }
  async function syncSavedTrainsToPush() {
    // Phone alerts while the app is closed reuse the travel-alert push
    // subscription, when the visitor has turned it on.
    if(!commuteAlertStatus.subscribed||!commuteAlertStatus.preferences)return;
    try {
      const registration=await pushWorker();
      const subscription=await registration?.pushManager?.getSubscription();
      if(!subscription)return;
      const preferences={...commuteAlertStatus.preferences,saved_trains:savedTrainIds()};
      const response=await fetch('/api/alerts/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_token:alertDeviceToken(),subscription:subscription.toJSON(),preferences})});
      if(response.ok)commuteAlertStatus={...commuteAlertStatus,preferences};
    } catch {}
  }

  function loadMyRadarPreferences() {
    try {
      const rows=JSON.parse(localStorage.getItem(MY_RADAR_PLACES_KEY)||'[]');
      savedPlaces=new Map((Array.isArray(rows)?rows:[]).filter(row=>row&&row.id).map(row=>[String(row.id),row]));
    } catch { savedPlaces=new Map(); }
    try {
      const rows=JSON.parse(localStorage.getItem(MY_RADAR_INTERESTS_KEY)||'[]');
      myRadarInterests=new Set((Array.isArray(rows)?rows:[]).filter(value=>['train','bus','vessel','incident','weather','flood','aircraft'].includes(value)));
    } catch { myRadarInterests=new Set(); }
  }

  function saveMyRadarPreferences() {
    try { localStorage.setItem(MY_RADAR_PLACES_KEY,JSON.stringify([...savedPlaces.values()])); } catch {}
    try { localStorage.setItem(MY_RADAR_INTERESTS_KEY,JSON.stringify([...myRadarInterests])); } catch {}
  }

  function renderMyRadarInterests() {
    const box=$('my-radar-interests'),badge=$('my-radar-interest-count'); if(!box||!badge)return;
    const maritimeAvailable=activities.some(row=>row.type==='vessel') || latestSources.some(source=>source.name==='aisstream'&&source.enabled&&!['off','disabled'].includes(String(source.mode||'').toLowerCase()));
    const options=[['train',icons.train,'Trains'],['bus',icons.bus,'Buses'],...(maritimeAvailable?[['vessel',icons.vessel,'Boats & ships']]:[]),['aircraft',icons.aircraft,'Special aircraft'],['incident',icons.incident,'Incidents'],['weather',icons.weather,'Weather'],['flood',icons.flood,'Floods']];
    badge.textContent=options.filter(([type])=>myRadarInterests.has(type)).length;
    box.innerHTML=options.map(([type,icon,label])=>`<button type="button" data-radar-interest="${type}" class="${myRadarInterests.has(type)?'active':''}" aria-pressed="${myRadarInterests.has(type)}"><b>${icon}</b><span>${label}</span></button>`).join('');
    box.querySelectorAll('[data-radar-interest]').forEach(button=>button.addEventListener('click',()=>{
      const type=button.dataset.radarInterest;
      if(!myRadarInterests.has(type)&&!billingState.plus&&myRadarInterests.size>=3){openPlusDialog();showToast('You can pick three interests for free. Radar+ removes the limit.');return;}
      if(myRadarInterests.has(type))myRadarInterests.delete(type);else myRadarInterests.add(type);
      saveMyRadarPreferences();renderMyRadar();trackProduct('my_radar_interest_changed',type);
    }));
  }

  function renderSavedPlaces() {
    const box=$('saved-places-list'),badge=$('saved-places-count');if(!box||!badge)return;
    badge.textContent=savedPlaces.size;
    badge.classList.toggle('hidden',!savedPlaces.size);
    // Next departures, nearby activity and alerts all need a saved place;
    // until there is one, the Saved tab shows only how to add one.
    document.querySelector('[data-workspace-panel="live"]')?.classList.toggle('has-saved-places',savedPlaces.size>0);
    const rows=[...savedPlaces.values()];
    renderCommuteAlertStatus();
    if(!rows.length){box.innerHTML='<p class="my-radar-empty">No stations or stops saved yet.</p><button id="my-radar-add-place" class="primary-button my-radar-add-place" type="button">Add a station or bus stop</button>';$('my-radar-add-place')?.addEventListener('click',startAddingMyRadarPlace);return;}
    const chosen=rows.some(p=>p.id===chosenSavedPlace)?chosenSavedPlace:rows[0].id;
    chosenSavedPlace=chosen;
    box.innerHTML=`${rows.map(p=>`<div class="saved-place-row${p.id===chosen?' is-chosen':''}"><button type="button" data-saved-place="${esc(p.id)}" aria-pressed="${p.id===chosen}"><b>${placeIcons[p.place_type]||iconSvg('pin')}</b><span><strong>${esc(humanTransitLabel(p.name))}</strong><small>${esc(p.place_type==='rail_station'?'Rail station':p.place_type==='bus_stop'?'Bus stop':'Place')}${p.id===chosen?' · shown below':''}</small></span></button><button type="button" class="saved-place-map" data-saved-place-map="${esc(p.id)}" aria-label="Show ${esc(humanTransitLabel(p.name))} on the map">${iconSvg('pin')}<span>Map</span></button><button type="button" data-remove-place="${esc(p.id)}" aria-label="Remove ${esc(humanTransitLabel(p.name))}">×</button></div>`).join('')}<button id="my-radar-add-place" class="ghost-button my-radar-add-place" type="button">＋ Add another place</button>`;
    // Tapping a saved place chooses it for this page: its departures and
    // its alerts below. The map button opens it on the map instead.
    box.querySelectorAll('[data-saved-place]').forEach(button=>button.addEventListener('click',()=>chooseSavedPlace(button.dataset.savedPlace)));
    box.querySelectorAll('[data-saved-place-map]').forEach(button=>button.addEventListener('click',async()=>{
      const row=savedPlaces.get(button.dataset.savedPlaceMap);if(!row)return;
      if(!places.some(p=>p.id===row.id))places.push(row);renderPlaces();await selectPlace(row.id,true);if(map.getZoom()<11)map.setZoom(11);
    }));
    box.querySelectorAll('[data-remove-place]').forEach(button=>button.addEventListener('click',()=>{savedPlaces.delete(button.dataset.removePlace);saveMyRadarPreferences();renderMyRadar();}));
    $('my-radar-add-place')?.addEventListener('click',startAddingMyRadarPlace);
  }

  async function startAddingMyRadarPlace() {
    placeMode='all';if($('place-mode'))$('place-mode').value='all';
    if(map.getZoom()<13)map.setZoom(13);
    await fetchPlaces();updateLayerToggleUi();
    showToast('Tap a station or bus stop on the map, then tap Save place.');
    trackProduct('my_radar_add_place_started','place');
  }

  function alertDeviceToken() {
    try {
      let token=localStorage.getItem(ALERT_DEVICE_KEY);if(token)return token;
      const bytes=new Uint8Array(24);crypto.getRandomValues(bytes);
      token=btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
      localStorage.setItem(ALERT_DEVICE_KEY,token);return token;
    } catch { return ''; }
  }

  let chosenSavedPlace=null;
  function chooseSavedPlace(id) {
    if(!savedPlaces.has(id))return;
    chosenSavedPlace=id;
    for(const selectId of ['passing-next-place','commute-alert-place']){const select=$(selectId);if(select&&[...select.options].some(option=>option.value===id))select.value=id;}
    renderSavedPlaces();
    loadPassingNext();
    renderCommuteAlertStatus();
  }
  function renderCommuteAlertPlaces() {
    const select=$('commute-alert-place');if(!select)return;
    const current=select.value||commuteAlertStatus.preferences?.place_id||'';
    // Train delays make sense near a station or a general place (home,
    // work), never near a bus stop.
    const trains=$('commute-alert-mode')?.value==='train';
    const rows=[...savedPlaces.values()].filter(place=>!(trains&&place.place_type==='bus_stop'));
    select.innerHTML=rows.length?rows.map(place=>`<option value="${esc(place.id)}">${esc(humanTransitLabel(place.name))}</option>`).join(''):`<option value="">${trains?'Save a station first':'Save a place first'}</option>`;
    if(rows.some(place=>place.id===chosenSavedPlace))select.value=chosenSavedPlace;
    else if(rows.some(place=>place.id===current))select.value=current;
    renderPassingNextPlaces();
  }

  function renderPassingNextPlaces() {
    const select=$('passing-next-place');if(!select)return;
    const current=select.value;
    const rows=[...savedPlaces.values()].filter(place=>['rail_station','bus_stop'].includes(place.place_type));
    select.innerHTML=rows.length?rows.map(place=>`<option value="${esc(place.id)}">${esc(humanTransitLabel(place.name))}</option>`).join(''):'<option value="">Save a station or stop first</option>';
    if(rows.some(place=>place.id===chosenSavedPlace))select.value=chosenSavedPlace;
    else if(rows.some(place=>place.id===current))select.value=current;
    else if(rows[0])select.value=rows[0].id;
  }

  async function loadPassingNext() {
    const box=$('passing-next'),select=$('passing-next-place');if(!box||!select)return;
    const place=savedPlaces.get(select.value||'');
    if(!place){box.innerHTML='<p class="my-radar-empty">Save a station or bus stop to see what is due.</p>';return;}
    box.innerHTML='<p class="passing-next-loading">Checking live times…</p>';
    try {
      const response=await fetch(`/api/places/${encodeURIComponent(place.id)}/board?limit=6`,{cache:'no-store'});
      if(!response.ok)throw new Error();
      const payload=await response.json(),board=payload.board||{};
      const rows=place.place_type==='rail_station' ? (board.departures||[]).slice(0,5).map(row=>boardRow(row,'departure')).join('') : (board.rows||[]).slice(0,5).map(row=>boardRow(row,'bus')).join('');
      const label=place.place_type==='rail_station'?'NEXT DEPARTURES':'NEXT BUSES';
      box.innerHTML=rows?`<div class="passing-next-label"><span>${label==='NEXT BUSES'?'Next buses from':'Next trains from'}</span> <b>${esc(humanTransitLabel(place.name))}</b></div>${rows}`:'<p class="my-radar-empty">Nothing due right now.</p>';
      bindBoardLiveLinks(box);
    } catch { box.innerHTML='<p class="my-radar-empty">Live times are unavailable right now.</p>'; }
  }

  function renderCommuteAlertStatus() {
    showInstallHints();
    const state=$('commute-alert-state'),note=$('commute-alert-note'),enable=$('commute-alert-enable'),disable=$('commute-alert-disable');
    if(!state||!note||!enable||!disable)return;
    const active=Boolean(commuteAlertStatus.subscribed);
    const p=(active&&commuteAlertStatus.preferences)||{};
    document.querySelector('[data-workspace-panel="live"]')?.classList.toggle('has-saved-trains',followed.size>0||active);
    state.textContent=active?'ON':'OFF';state.classList.toggle('active',active);
    disable.classList.toggle('hidden',!active);
    $('commute-alert-test')?.classList.toggle('hidden',!active);
    // Say plainly whether this phone gets alerts, and about what.
    const summary=$('commute-alert-summary');
    const chosen=savedPlaces.get($('commute-alert-place')?.value||'');
    const placeOn=active&&p.mode&&p.mode!=='trains-only';
    if(summary){
      summary.classList.toggle('is-off',!active);
      if(active){
        const trains=(p.saved_trains||[]).length;
        const items=[
          `Your saved trains: late, cancelled, platform or formation changes${trains?` (${trains} saved)`:' (none saved yet)'}`,
          placeOn?`${p.mode==='train'?'Train':'Driving'} delays near ${p.place_name||'your saved place'}, ${String(p.start_hour).padStart(2,'0')}:00 to ${String(p.end_hour).padStart(2,'0')}:00`:null,
          p.spotter?`Unusual trains and aircraft within ${p.spotter.radius_miles} miles`:null,
          p.journey?`Trouble on ${p.journey.name} before you leave`:null,
          (p.leave_plans||[]).length?`When to leave for ${(p.leave_plans||[]).length} train${(p.leave_plans||[]).length===1?'':'s'}`:null,
        ].filter(Boolean);
        summary.innerHTML=`<strong>Alerts are on for this phone.</strong><span>You’ll be told about:</span><ul>${items.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`;
      } else summary.innerHTML='<strong>Alerts are off.</strong><span>Nothing is sent to this phone. Saving a station does not turn them on; the button below does.</span>';
    }
    // The button says exactly what tapping it will do.
    const chosenName=chosen?humanTransitLabel(chosen.name):'';
    const samePlace=placeOn&&chosen&&p.place_id===chosen.id;
    enable.textContent=!active?(chosen?`Turn alerts on for ${chosenName}`:'Turn alerts on')
      : !chosen?'Update alerts' : samePlace?`Update alerts for ${chosenName}` : `Add alerts for ${chosenName}`;
    enable.classList.toggle('hidden',active&&!savedPlaces.size);
    // The Home Screen message is already shown above; don't repeat it here.
    const unsupported=alertsNeedHomeScreen()?'':pushUnsupportedReason();
    note.classList.toggle('is-problem',Boolean(pushProblem)||(!active&&Boolean(unsupported)));
    note.textContent=pushProblem ? pushProblem
      : !commuteAlertStatus.configured ? 'Alerts are not available yet.'
      : active ? 'Use “Send a test alert” to check they reach this phone.'
      : unsupported || 'Your phone will ask for permission the first time.';
    renderCommuteAlertPlaces();
    renderSpotter();
    if(active&&p.mode&&p.mode!=='trains-only'&&!renderCommuteAlertStatus.filled){renderCommuteAlertStatus.filled=true;if($('commute-alert-mode'))$('commute-alert-mode').value=p.mode||'driving';if($('commute-alert-radius'))$('commute-alert-radius').value=String(p.radius_miles||12);if($('commute-alert-hours'))$('commute-alert-hours').value=`${p.start_hour}:${p.end_hour}`;if(!chosenSavedPlace&&savedPlaces.has(p.place_id))chosenSavedPlace=p.place_id;}
  }

  async function loadCommuteAlertStatus() {
    try {
      const response=await fetch('/api/alerts/status',{cache:'no-store',headers:{'X-Radar-Device':alertDeviceToken()}});
      if(!response.ok)throw new Error();commuteAlertStatus=await response.json();
    } catch { commuteAlertStatus={configured:false,subscribed:false,public_key:null,preferences:null}; }
    renderCommuteAlertStatus();
  }

  function vapidKeyBytes(value) {
    const padding='='.repeat((4-value.length%4)%4),raw=atob((value+padding).replace(/-/g,'+').replace(/_/g,'/'));
    return Uint8Array.from([...raw].map(character=>character.charCodeAt(0)));
  }

  async function enableCommuteAlerts() {
    const place=savedPlaces.get($('commute-alert-place')?.value||'');
    let preferences;
    if(place&&!requireAlertsPlus('Alerts near your places','place-alerts'))return;
    if(place){
      const [startHour,endHour]=String($('commute-alert-hours')?.value||'5:11').split(':').map(Number);
      preferences={...currentPushPreferences(),mode:$('commute-alert-mode')?.value||'driving',place_id:place.id,place_name:humanTransitLabel(place.name),lat:Number(place.lat),lon:Number(place.lon),radius_miles:Number($('commute-alert-radius')?.value||12),start_hour:startHour,end_hour:endHour,activity_types:[...myRadarInterests].filter(type=>['train','bus','vessel','aircraft'].includes(type)),saved_trains:savedTrainIds()};
    } else preferences=currentPushPreferences();  // no saved place: saved trains alone
    const button=$('commute-alert-enable');
    if(button){button.disabled=true;button.textContent='Turning on…';}
    const ok=await savePushPreferences(preferences);
    if(button)button.disabled=false;
    renderCommuteAlertStatus();
    if(ok){showToast('Alerts are on for this phone.');trackProduct('commute_alert_enabled',preferences.mode);}
    else showToast(pushProblem||'Alerts couldn’t be turned on.');
  }

  async function disableCommuteAlerts() {
    try {
      await fetch('/api/alerts/unsubscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_token:alertDeviceToken()})});
      const registration=await pushWorker();const subscription=await registration.pushManager.getSubscription();await subscription?.unsubscribe();
    } catch {}
    pushProblem='';commuteAlertStatus={...commuteAlertStatus,subscribed:false,preferences:null};renderCommuteAlertStatus();showToast('Alerts are off for this phone.');trackProduct('commute_alert_disabled','alerts');
  }

  function renderMyRadarArea() {
    const box=$('my-radar-area');if(!box)return;
    const anchors=[...savedPlaces.values()].map(p=>({name:p.name,lat:Number(p.lat),lon:Number(p.lon),radius:16}));
    anchors.push(...watchZones.map(z=>({name:z.name,lat:Number(z.lat),lon:Number(z.lon),radius:Number(z.radiusMiles)*1.609344})));
    if(!anchors.length){box.innerHTML='<p class="left-empty">Save a place or watch zone to build your local radar.</p>';return;}
    const matching=activities.filter(a=>anchors.some(anchor=>distanceKm(anchor.lat,anchor.lon,Number(a.lat),Number(a.lon))<=anchor.radius));
    const matchesInterest=a=>myRadarInterests.has(a.type)||(a.type==='sar'&&myRadarInterests.has('aircraft'));
    const interesting=matching.filter(a=>myRadarInterests.size?matchesInterest(a):interestInfo(a).score>=15).sort((a,b)=>interestInfo(b).score-interestInfo(a).score);
    const top=interesting.slice(0,4);
    box.innerHTML=`${top.length?top.map(a=>`<button type="button" data-area-activity="${esc(a.id)}"><span>${icons[a.type]||'•'}</span><div><strong>${esc(a.name)}</strong>${(a.cool_explanation||a.subtitle)?`<small>${esc(a.cool_explanation||a.subtitle)}</small>`:''}</div><b>›</b></button>`).join(''):'<p class="my-radar-empty">Nothing of interest near your places right now.</p>'}`;
    box.querySelectorAll('[data-area-activity]').forEach(button=>button.addEventListener('click',()=>selectActivity(button.dataset.areaActivity,true,11)));
  }

  function renderMyRadar() {
    const box = $('my-radar-list');
    const badge = $('my-radar-count');
    if (!box || !badge) return;
    renderMyRadarInterests();renderSavedPlaces();renderMyRadarArea();renderYours();
    badge.textContent = followed.size;
    badge.classList.toggle('hidden', !followed.size);
    if (!followed.size) { box.innerHTML = '<p class="my-radar-empty">Nothing saved yet. Tap Save on a train to be told if it runs late, is cancelled, changes platform or gets a confirmed formation.</p>'; return; }
    // Everything saved is listed. What is not on the map right now (a plane
    // out of range, a train that has finished) says so instead of vanishing.
    const rows=[...followed].map(id=>{
      const a=activityById(id);
      if(a){rememberFollowed(a);return {id,live:true,type:a.type,name:a.name,text:a.type==='train'?trainStatusLabel(a):(a.status||a.subtitle||'Live')};}
      const kept=followedInfo[id]||{};
      const type=kept.type||(/^(nr|td):/.test(id)?'train':/^bus:/.test(id)?'bus':/^(aircraft|sar):/.test(id)?'aircraft':'');
      const away=['aircraft','sar'].includes(type)?'Not in range right now':type==='train'?'Not running right now':'Not on the map right now';
      return {id,live:false,type,name:kept.name||(type==='train'?'Saved train':type==='bus'?'Saved bus':['aircraft','sar'].includes(type)?'Saved aircraft':'Saved item'),text:[kept.detail,away].filter(Boolean).join(' · ')};
    }).sort((a,b)=>Number(b.live)-Number(a.live));
    box.innerHTML = rows.slice(0,12).map(row => `<div class="saved-place-row my-radar-saved${row.live?'':' is-away'}"><button type="button" class="my-radar-row" data-my-radar="${esc(row.id)}"><b>${icons[row.type] || '•'}</b><span><strong>${esc(row.name)}</strong><span>${esc(row.text)}</span></span>${row.live?'<em>LIVE</em>':''}</button><button type="button" data-unsave="${esc(row.id)}" aria-label="Remove ${esc(row.name)}">×</button></div>`).join('');
    box.querySelectorAll('[data-my-radar]').forEach(btn=>btn.addEventListener('click',()=>{
      const id=btn.dataset.myRadar;
      if(activityById(id))selectActivity(id,true);
      else focusActivityById(id,Math.max(11,map.getZoom())).catch?.(()=>{});
    }));
    box.querySelectorAll('[data-unsave]').forEach(btn=>btn.addEventListener('click',()=>{
      followed.delete(btn.dataset.unsave);delete followedInfo[btn.dataset.unsave];saveFollowed();saveFollowedInfo();
      if(selectedId===btn.dataset.unsave)$('follow').textContent='Save';
      renderMyRadar();syncSavedTrainsToPush();
    }));
  }

  async function copyShareText(text) {
    if(navigator.clipboard?.writeText){
      try{await navigator.clipboard.writeText(text);return true;}catch{}
    }
    try{
      const area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();
      const copied=document.execCommand?.('copy')===true;area.remove();return copied;
    }catch{return false;}
  }

  async function shareOrCopy({title,text,url,successMessage}) {
    if(typeof navigator.share==='function'){
      try{await navigator.share({title,text,url});return true;}
      catch(error){if(error?.name==='AbortError')return false;}
    }
    const copied=await copyShareText([text,url].filter(Boolean).join('\n'));
    if(copied){showToast(successMessage);return true;}
    window.open(url,'_blank','noopener');showToast('Opened in a new tab.');return true;
  }

  async function shareSelectedActivity() {
    const a=activityById(selectedId);if(!a)return;
    try {
      // Keep the native share call inside the original click gesture. Waiting
      // for an API response first caused several browsers to reject it.
      const url=`${location.origin}/live/${encodeURIComponent(a.id)}`;
      const text=[a.name,a.cool_explanation||a.reason||a.subtitle||a.status,`Updated ${timeText(a.last_updated)}`].filter(Boolean).join(' • ');
      await shareOrCopy({title:`${a.name} — UK Activity Radar`,text,url,successMessage:'Live link and summary copied.'});
      trackProduct('activity_shared',a.type||'other');
    } catch(error){if(error?.name!=='AbortError')showToast('Sharing isn’t available right now.');}
  }

  function todayRow(row,label) {
    if(!row)return '';
    return `<button type="button" class="today-row" data-today-activity="${esc(row.activity_id)}"><span>${icons[row.type]||icons.space}</span><div>${label?`<small>${esc(label)}</small>`:''}<strong>${esc(row.name)}</strong>${(row.reason||row.status||row.subtitle)?`<p>${esc(row.reason||row.status||row.subtitle)}</p>`:''}</div><b>›</b></button>`;
  }

  async function loadTodayRadar(force=false) {
    const box=$('today-radar');if(!box||(!force&&Date.now()-todayLoadedAt<60000))return;
    // Startup and the first live snapshot can arrive in the same frame. Share
    // that request instead of making the server build the same panel twice.
    if(todayLoadPromise)return todayLoadPromise;
    todayLoadedAt=Date.now();
    todayLoadPromise=(async()=>{
      let since='';try{since=localStorage.getItem(TODAY_LAST_VISIT_KEY)||'';}catch{}
      try {
        const where=lastFix||(map?.getCenter?{lat:map.getCenter().lat,lon:map.getCenter().lng}:null);
        const params=new URLSearchParams();
        if(since)params.set('since',new Date(Number(since)).toISOString());
        // Disruptions near you (or near the map) rather than anywhere in Britain.
        if(where&&Number.isFinite(Number(where.lat))&&map.getZoom()>=8){params.set('lat',Number(where.lat).toFixed(3));params.set('lon',Number(where.lon??where.lng).toFixed(3));}
        const response=await fetch(`/api/today${params.toString()?`?${params}`:''}`,{cache:'no-store'});
        if(!response.ok)throw new Error('summary unavailable');const payload=await response.json();
        const fresh=(payload.new_since||[])[0],disruption=(payload.disruptions||[])[0];
        box.innerHTML=[todayRow(payload.best,''),fresh&&fresh.activity_id!==payload.best?.activity_id?todayRow(fresh,'New'):'',disruption?todayRow(disruption,'Disruption'):'' ].filter(Boolean).join('')||'<p class="left-empty">Nothing notable right now.</p>';
        box.querySelectorAll('[data-today-activity]').forEach(button=>button.addEventListener('click',()=>focusActivityById(button.dataset.todayActivity,11)));
        try{localStorage.setItem(TODAY_LAST_VISIT_KEY,String(Date.now()));}catch{}
      } catch { box.innerHTML='<p class="left-empty">Reconnecting…</p>'; }
    })().finally(()=>{todayLoadPromise=null;});
    return todayLoadPromise;
  }

  function loadWatchZones() {
    try {
      const raw = JSON.parse(localStorage.getItem('ukRadarWatchZones') || '[]');
      watchZones = Array.isArray(raw) ? raw.filter(z => Number.isFinite(z.lat) && Number.isFinite(z.lon) && Number.isFinite(z.radiusMiles)) : [];
    } catch { watchZones = []; }
  }
  function saveWatchZones() { try { localStorage.setItem('ukRadarWatchZones', JSON.stringify(watchZones)); } catch {} }

  function renderWatchZones() {
    watchCircles.forEach(c => map.removeLayer(c)); watchCircles = [];
    const box = $('watch-zones');
    if (!watchZones.length) { box.innerHTML = '<p class="watch-empty">No watch zones yet. Centre the map somewhere, choose 5/10/25 miles above and tap Add.</p>'; return; }
    box.innerHTML = watchZones.map(z => {
      const live = activities.filter(a => distanceKm(z.lat,z.lon,Number(a.lat),Number(a.lon)) <= z.radiusMiles*1.609344 && interestInfo(a).score >= 15).length;
      return `<div class="watch-row"><button type="button" data-watch-go="${esc(z.id)}"><strong>${esc(z.name)}</strong><span>${z.radiusMiles} mi • ${live} interesting now</span></button><button type="button" class="watch-delete" data-watch-delete="${esc(z.id)}" aria-label="Delete watch zone">×</button></div>`;
    }).join('');
    for (const z of watchZones) watchCircles.push(L.circle([z.lat,z.lon], {radius:z.radiusMiles*1609.344,color:'#f4c96b',weight:1,fillOpacity:0,dashArray:'5 6',interactive:false}).addTo(map));
    box.querySelectorAll('[data-watch-go]').forEach(btn=>btn.addEventListener('click',()=>{const z=watchZones.find(x=>x.id===btn.dataset.watchGo); if(z) map.setView([z.lat,z.lon], Math.max(map.getZoom(), 9));}));
    box.querySelectorAll('[data-watch-delete]').forEach(btn=>btn.addEventListener('click',()=>{watchZones=watchZones.filter(x=>x.id!==btn.dataset.watchDelete); saveWatchZones(); renderWatchZones();renderMyRadar(); trackProduct('watch_zone_removed','watch-zone');}));
  }

  function checkWatchZones() {
    const interestType=a=>a.type==='sar'?'aircraft':a.type;
    const savedAnchors=[...savedPlaces.values()].map(place=>({id:`place-${place.id}`,name:place.name,lat:Number(place.lat),lon:Number(place.lon),radiusMiles:Number($('commute-alert-radius')?.value||12),interestsOnly:true}));
    const anchors=[...watchZones.map(zone=>({...zone,interestsOnly:false})),...savedAnchors];
    if (!anchors.length) return;
    const matches = [];
    for (const z of anchors) {
      for (const a of activities) {
        if (z.interestsOnly && (!myRadarInterests.size || !myRadarInterests.has(interestType(a)))) continue;
        if (!z.interestsOnly && interestInfo(a).score < 15) continue;
        if (distanceKm(z.lat,z.lon,Number(a.lat),Number(a.lon)) > z.radiusMiles*1.609344) continue;
        const key = `${z.id}:${a.id}`;
        matches.push(key);
        if (watchPrimed && !watchSeen.has(key)) showToast(`${a.name} entered ${z.name}`);
        watchSeen.add(key);
      }
    }
    if (!watchPrimed) { matches.forEach(k=>watchSeen.add(k)); watchPrimed = true; }
  }

  function renderNearbySummary() {
    const box = $('nearby-summary'); if (!box) return;
    const centre = nearbyState.active ? {lat:nearbyState.lat,lng:nearbyState.lon} : map.getCenter();
    const radiusMiles = nearbyState.active ? nearbyState.radiusMiles : 12;
    const km = radiusMiles * 1.609344;
    const rows = activities.filter(a => Number.isFinite(Number(a.lat)) && Number.isFinite(Number(a.lon)) && distanceKm(centre.lat, centre.lng, Number(a.lat), Number(a.lon)) <= km);
    const groups = [
      ['transport',icons.train,'Transport', rows.filter(a=>['train','bus'].includes(a.type)), 'trains & buses moving nearby'],
      ['road',icons.road,'Roadworks', rows.filter(a=>a.type==='road'), 'planned works / closures'],
      ['incident',icons.incident,'Incidents', rows.filter(a=>a.type==='incident'), 'unplanned live incidents'],
      ['weather',icons.weather,'Weather', rows.filter(a=>['weather','flood'].includes(a.type)), 'warnings in this area']
    ];
    box.innerHTML = groups.map(([,icon,label,list,desc]) => `<div class="nearby-summary-row"><span class="ns-icon">${icon}</span><span><strong>${label}</strong><span>${list.length ? esc(desc) : 'Nothing current in range'}</span></span><b>${list.length}</b></div>`).join('') || '<div class="nearby-summary-empty">Nothing live is currently mapped around this point.</div>';
  }

  function renderCoolSummary() {
    const box=$('cool-summary'); if(!box)return;
    const centre=nearbyState.active?{lat:nearbyState.lat,lng:nearbyState.lon}:map.getCenter();
    const radius=nearbyState.active ? nearbyState.radiusMiles*1.609344 : null;
    const scopedCandidates=globalCoolRows().map(a=>({a,c:coolInfo(a),km:distanceKm(centre.lat,centre.lng,Number(a.lat),Number(a.lon))}))
      .filter(x=>x.c.score>=45 && Number.isFinite(x.km) && (radius==null || x.km<=radius));
    const candidates=scopedCandidates.filter(x=>layerTypeMatch(x.a,selectedType) && operatorMatches(x.a)).sort((x,y)=>y.c.score-x.c.score || x.km-y.km);
    const countSource=selectedType==='cool'?scopedCandidates:candidates;
    const cats=[['AIR','air',x=>discoveryCategoryForActivity(x.a)==='air'],['RAIL','rail',x=>discoveryCategoryForActivity(x.a)==='rail'],['RESCUE','rescue',x=>discoveryCategoryForActivity(x.a)==='rescue'||isRescueContextActivity(x.a)]];
    const previewCounts=globalCoolAvailability.counts||{};
    const counts=cats.map(([label,theme,fn])=>({label,theme,n:selectedType==='cool'&&!globalCoolAvailability.fullAccess?Number(previewCounts[theme]||0):countSource.filter(fn).length}));
    const top=candidates[0];
    const more=candidates.slice(1,6);
    const emptyScope=radius==null?'in the current UK live feed':`within ${Math.round(radius/1.609344)} miles`;
    // Every find is the same simple row: icon, name, what it is, and when.
    const findRow=(x,first)=>{
      const fresh=isNewDiscoveryActivity(x.a);
      const meta=[fresh?'<em class="find-new">New</em>':'',`updated ${esc(discoveryFreshness(x.a))}`,activityMatchesInterests(x.a)?'your interest':'',first&&activityIsCollectible(x.a)?`${x.km<1?'under 1':Math.round(x.km)} km away`:''].filter(Boolean).join(' · ');
      const attr=first?`class="discover-row cool-hero-hit${fresh?' is-new':''}" data-cool-top="${esc(x.a.id)}"`:`class="discover-row cool-find-row${fresh?' is-new':''}" data-cool-find="${esc(x.a.id)}"`;
      return `<button type="button" ${attr}><span class="discover-icon cool-${esc(x.c.theme||'air')}">${iconSvg(coolIconFor(x.a,x.c))}</span><span class="discover-text"><strong>${esc(x.a.name)}</strong><small>${esc(x.c.reasons[0]||x.a.subtitle||'')}</small><i>${meta}</i></span><b aria-hidden="true">›</b></button>`;
    };
    const moreHtml=more.length?`<div class="cool-find-list">${more.map(x=>findRow(x,false)).join('')}</div>`:'';
    const hiddenFinds=globalCoolAvailability.total-candidates.length;
    const previewNote=!globalCoolAvailability.fullAccess&&hiddenFinds>0?`<button type="button" class="cool-preview-note" data-cool-more>+${hiddenFinds} more find${hiddenFinds===1?'':'s'} right now with Radar+</button>`:'';
    box.innerHTML=`${previewNote}${top?`<div class="discover-list">${findRow(top,true)}${moreHtml}</div>`:`<div class="cool-empty">Nothing unusual spotted ${emptyScope} right now.</div>`}`;
    box.querySelector('[data-cool-top]')?.addEventListener('click',e=>selectActivity(e.currentTarget.dataset.coolTop,true,11));
    box.querySelectorAll('[data-cool-find]').forEach(button=>button.addEventListener('click',()=>selectActivity(button.dataset.coolFind,true,11)));
  }

  function renderLiveTicker() {
    const host=$('live-ticker'); if(!host)return; const target=host.querySelector('.ticker-items'); if(!target)return;
    const label=host.querySelector('.ticker-label'); const coolMode=currentWorkspace==='cool';
    if(label) label.textContent=coolMode?'COOL':'LIVE';
    let rows=coolMode?[...globalCoolRows()]:[...activities];
    if(coolMode) rows=rows.filter(a=>coolInfo(a).score>=45&&layerTypeMatch(a,selectedType)&&operatorMatches(a)).sort((a,b)=>coolInfo(b).score-coolInfo(a).score);
    else rows=rows.filter(a=>layerTypeMatch(a,'useful')&&operatorMatches(a)).sort((a,b)=>interestInfo(b).score-interestInfo(a).score);
    rows=rows.slice(0,6);
    if(!rows.length){target.textContent=coolMode?'No unusual live activity identified yet.':'Map clear • choose a layer to start';return;}
    target.innerHTML=rows.map((a,i)=>`${i?'<span class="ticker-sep">•</span>':''}<button type="button" class="ticker-item" data-ticker-id="${esc(a.id)}">${coolMode?coolBadgeHtml(a,true):(icons[a.type]||'•')} ${esc(a.name)}${a.delay_minutes>0?` +${esc(a.delay_minutes)}m`:''}</button>`).join('');
    target.querySelectorAll('[data-ticker-id]').forEach(btn=>btn.addEventListener('click',()=>selectActivity(btn.dataset.tickerId,true,coolMode?11:null)));
  }

  function activateWorkspace(name, autoSelect=true) {
    if(!['useful','cool','live','settings'].includes(name)) name='useful';
    if(name!==currentWorkspace)clearTransientLocationModes();
    currentWorkspace=name;
    document.body.dataset.experience=name;
    document.querySelectorAll('.workspace-tab[data-workspace]').forEach(b=>b.classList.toggle('active',b.dataset.workspace===name));
    document.querySelectorAll('[data-workspace-panel]').forEach(p=>p.classList.toggle('active',p.dataset.workspacePanel===name));
    $('workspace-shell')?.classList.remove('collapsed');
    const workspaceDrawer=document.querySelector('.workspace-drawer');
    if(workspaceDrawer)workspaceDrawer.scrollTop=0;
    if (autoSelect) {
      const defaults={useful:'useful',cool:'cool',live:'useful',settings:'useful'};
      if(defaults[name]){
        selectedType=defaults[name];
        document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x.dataset.type===selectedType));
      }
    }
    updateCoolRailSubtypeUi();
    renderMap();renderStats();renderRanking();renderNearbySummary();renderCoolSummary();renderLiveTicker();
    clearTimeout(globalCoolRefreshTimer);
    globalCoolRefreshTimer=null;
    if(name==='cool') {
      loadGlobalCool(false);
      loadDiscoveryData(false);
      renderDiscoveryExperience();
      if(!discoveryVisitRecorded){
        discoveryVisitRecorded=true;
        try{localStorage.setItem(DISCOVERY_LAST_VISIT_KEY,String(discoverySessionStartedAt));}catch{}
      }
    }
    if(name==='live') loadPassingNext();
    if(selectedId){const selected=activityById(selectedId);if(selected)renderDiscoveryDetail(selected);}
    try{localStorage.setItem('ukRadarWorkspace',name)}catch{}
    sendViewport();
    setTimeout(()=>map.invalidateSize(),190);
  }

  function activityDetailSignature(a) {
    return a ? `${currentWorkspace}|${JSON.stringify(a)}` : '';
  }

  function renderSecondary() {
    secondaryRenderTimer = null;
    secondaryRenderIdle = null;
    lastSecondaryRenderAt = Date.now();
    renderRanking();
    renderFeeds(latestSources);
    if (watchZones.length) renderWatchZones();
    if (watchZones.length || savedPlaces.size) checkWatchZones();
    renderMyRadar();
    loadTodayRadar();
    renderNearbySummary();
    renderCoolSummary();
    renderLiveTicker();
    if(currentWorkspace==='cool')renderDiscoveryExperience();
    if (selectedId) {
      const selected = activityById(selectedId);
      const signature = activityDetailSignature(selected);
      if (selected && signature !== selectedDetailSignature) selectActivity(selectedId, false);
    }
  }

  function scheduleSecondaryRender(force=false) {
    if (force) {
      if (secondaryRenderTimer) clearTimeout(secondaryRenderTimer);
      if (secondaryRenderIdle && 'cancelIdleCallback' in window) window.cancelIdleCallback(secondaryRenderIdle);
      secondaryRenderTimer = null;
      secondaryRenderIdle = null;
      renderSecondary();
      return;
    }
    if (secondaryRenderTimer || secondaryRenderIdle) return;
    const delay = Math.max(0, SECONDARY_RENDER_INTERVAL_MS - (Date.now() - lastSecondaryRenderAt));
    secondaryRenderTimer = setTimeout(() => {
      secondaryRenderTimer = null;
      if ('requestIdleCallback' in window) {
        secondaryRenderIdle = window.requestIdleCallback(renderSecondary, {timeout:500});
      } else {
        secondaryRenderTimer = setTimeout(renderSecondary, 0);
      }
    }, delay);
  }

  function renderAll(sources,changedIds=null) {
    renderMap(changedIds);
    renderSelectedTrail();
    renderStats();
    renderStreamZoomNote();
    // Feed cards and rankings are useful but do not need to churn at the
    // movement rate. Render them immediately on first contact, then at a
    // measured cadence using the newest snapshot.
    if (diagnostics.appliedSnapshots <= 1) scheduleSecondaryRender(true);
    else scheduleSecondaryRender();
    const tracked = trackedActivityId ? activityById(trackedActivityId) : null;
    if (tracked) map.panTo([tracked.lat,tracked.lon], {animate:true});
  }

  function applySocketPayload(payload) {
    const isDelta = payload.activity_delta && !Array.isArray(payload.activities);
    const deltaChanged = isDelta && (
      (payload.activity_delta.remove || []).length > 0
      || (payload.activity_delta.upsert || []).length > 0
    );
    streamMeta = payload.stream || {};
    latestSources = payload.sources || [];
    heatSnapshotPending = false;
    diagnostics.appliedSnapshots += 1;
    // Source health can change without a vehicle moving. Refresh the quiet
    // secondary panels, but do not rebuild activity indexes or walk markers.
    if (isDelta && !deltaChanged && !overheadActive) {
      diagnostics.unchangedSnapshots += 1;
      scheduleSecondaryRender();
      return;
    }
    let streamed, deltaIds=null, deltaHasTrain=false;
    if (isDelta) {
      const merged = new Map(activities.map(row=>[row.id,row]));
      deltaIds=new Set();
      for (const id of payload.activity_delta.remove || []) {
        const current=merged.get(id);
        if(current?.type==='train'){
          deltaHasTrain=true;
          if(deferredTrainRemovals.has(id))continue;
          const timer=setTimeout(()=>{
            deferredTrainRemovals.delete(id);
            if(!activitiesById.has(id))return;
            replaceActivities(activities.filter(row=>row.id!==id));
            renderAll(latestSources);
          },90000);
          deferredTrainRemovals.set(id,timer);
        }else {merged.delete(id);deltaIds.add(id);}
      }
      for (const row of payload.activity_delta.upsert || []) if (row?.id) {
        if(row.type==='train'||merged.get(row.id)?.type==='train')deltaHasTrain=true;
        deltaIds.add(row.id);
        const pending=deferredTrainRemovals.get(row.id);
        if(pending){clearTimeout(pending);deferredTrainRemovals.delete(row.id);}
        merged.set(row.id,row);
      }
      streamed = [...merged.values()];
    } else {
      streamed = payload.activities || [...(payload.vehicles || []), ...(payload.events || [])];
      for(const row of streamed){
        const pending=deferredTrainRemovals.get(row?.id);
        if(pending){clearTimeout(pending);deferredTrainRemovals.delete(row.id);}
      }
    }
    // A plane that has now arrived through the normal UK stream is no longer
    // owned solely by the temporary airport/above-me view and must survive
    // when that view is switched off.
    if(overheadActive){
      for(const row of streamed)if(row?.id)overheadInjectedAircraftIds.delete(row.id);
    }
    const nextActivities = overheadActive
      ? [...new Map([...streamed,...nearbyAircraftRows].map(row=>[row.id,row])).values()]
      : streamed;
    replaceActivities(nextActivities);
    renderAll(latestSources,isDelta&&!deltaHasTrain?deltaIds:null);
  }

  function queueSocketPayload(payload) {
    diagnostics.receivedSnapshots += 1;
    // A full snapshot replaces everything queued before it, but a delta only
    // says what changed since the last message: dropping one lost a bus's
    // move. Deltas are applied in order.
    if (payload && payload.activity_delta) pendingSocketPayloads.push(payload);
    else pendingSocketPayloads = [payload];
    if (socketRenderFrame !== null) return;
    const applyLatest = () => {
      socketRenderFrame = null;
      const batch = pendingSocketPayloads;
      pendingSocketPayloads = [];
      for (const latest of batch) if (latest) applySocketPayload(latest);
    };
    // requestAnimationFrame naturally drops intermediate snapshots when the
    // main thread is busy. A background tab uses a short timer because rAF is
    // intentionally suspended by browsers there.
    socketRenderFrame = document.hidden
      ? setTimeout(applyLatest, 50)
      : requestAnimationFrame(applyLatest);
  }

  function sendWatching(activityId) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({action:'watching',activity_id:activityId || null}));
  }

  function viewportActivityTypes() {
    // Cool and "All activity" deliberately request the complete supported
    // pool. Every other density mode is additive: its data is streamed for
    // the heat layer while the user's ordinary Useful marker choices remain.
    if (currentWorkspace === 'cool') return selectedType === 'cool-off' ? [] : null;
    if (heatMode === 'all') return null;
    const requested = new Set(enabledActivityTypes);
    for (const type of densityActivityTypes[heatMode] || []) requested.add(type);
    return usefulActivityTypeOrder.filter(type => requested.has(type));
  }

  function sendViewport() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const b = map.getBounds();
    const types = viewportActivityTypes();
    socket.send(JSON.stringify({ action: 'viewport', bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], zoom: map.getZoom(), types }));
  }
  function queueViewport() { clearTimeout(viewportTimer); viewportTimer = setTimeout(sendViewport, 120); }

  function hideSearchResults() { const box = $('search-results'); box.classList.add('hidden'); box.innerHTML = ''; }

  function addSearchLocation(row) {
    if (searchMarker) map.removeLayer(searchMarker);
    const icon = L.divIcon({className:'marker-wrap',html:'<div class="search-location-marker"></div>',iconSize:[18,18],iconAnchor:[9,9]});
    searchMarker = L.marker([row.lat,row.lon],{icon}).addTo(map).bindTooltip(esc(row.name),{direction:'top'});
    if (Array.isArray(row.bbox) && row.bbox.length === 4) {
      const [south,north,west,east] = row.bbox;
      if ([south,north,west,east].every(Number.isFinite)) map.fitBounds([[south,west],[north,east]],{maxZoom:14,padding:[25,25]}); else map.setView([row.lat,row.lon],13);
    } else map.setView([row.lat,row.lon],13);
  }

  // A bus route can be one character ("5"); anything else needs two.
  const ROUTE_QUERY=/^(?:bus\s+|route\s+)?[a-z]{0,3}\d{1,4}[a-z]{0,2}$/i;
  let searchRequest=0, searchTimer=null;
  async function runSearch() {
    const raw = $('search').value.trim();
    clearTimeout(searchTimer);
    if (raw.length < 2 && !ROUTE_QUERY.test(raw)) { hideSearchResults(); return; }
    trackProduct('search_used', 'search');
    const requestId = ++searchRequest;
    const box = $('search-results'); box.classList.remove('hidden'); box.innerHTML = '<div class="search-empty">Searching…</div>';
    try {
      // Where the map is looking: "5" finds the nearest buses on route 5.
      const centre = map.getCenter();
      const response = await fetch(`/api/search?q=${encodeURIComponent(raw)}&lat=${centre.lat.toFixed(4)}&lon=${centre.lng.toFixed(4)}`); if (!response.ok) throw new Error('search failed');
      const payload = await response.json(); const rows = payload.matches || [];
      if (requestId !== searchRequest) return;   // a newer search has started
      const buttons = rows.map(row => {
        const isPlace = row.kind === 'place';
        const attr = isPlace ? `data-place="${esc(row.id)}"` : `data-activity="${esc(row.id)}"`;
        const icon = isPlace ? (placeIcons[row.place_type] || iconSvg('pin')) : (icons[row.type] || '•');
        return `<button type="button" class="search-result" ${attr}><span class="search-result-icon">${icon}</span><span><strong>${esc(row.name)}</strong><span>${esc([row.distance_km != null ? `${row.distance_km} km away` : null,row.subtitle || row.source || (isPlace?'Place':'Live activity')].filter(Boolean).join(' • '))}</span></span></button>`;
      });
      // The place itself first: "Market Bosworth" → the town, then anything live there.
      if (payload.location) {
        const [first, ...rest] = String(payload.location.name || raw).split(',').map(part => part.trim()).filter(Boolean);
        buttons.unshift(`<button type="button" class="search-result search-place-result" data-location="1"><span class="search-result-icon">${iconSvg('pin')}</span><span><strong>${esc(first || raw)}</strong><span>${esc(rest.slice(0,2).join(', ') || 'Go to this place')}</span></span></button>`);
      }
      const noRoute = payload.intent?.route ? `No buses on route ${esc(payload.intent.route)} are reporting right now.` : 'No matching place or live activity.';
      box.innerHTML = buttons.length ? buttons.join('') : `<div class="search-empty">${noRoute}</div>`;
      box.querySelectorAll('[data-activity]').forEach(btn => btn.addEventListener('click',async()=>{ const id=btn.dataset.activity; searchText=''; $('search').value=''; hideSearchResults(); await focusActivityById(id,11); }));
      box.querySelectorAll('[data-place]').forEach(btn => btn.addEventListener('click',()=>{ const id=btn.dataset.place; const row=rows.find(x=>x.id===id); if(row){ if(!places.some(p=>p.id===id)) places.push(row); renderPlaces(); searchText=''; $('search').value=''; map.setView([row.lat,row.lon],row.place_type==='bus_stop'?15:12,{animate:true}); selectPlace(id,false); } hideSearchResults(); }));
      const locationButton=box.querySelector('[data-location]'); if(locationButton && payload.location) locationButton.addEventListener('click',()=>{ addSearchLocation(payload.location); hideSearchResults(); });
    } catch { if (requestId === searchRequest) box.innerHTML = '<div class="search-empty">Search is busy for a moment. Try again.</div>'; }
  }

  async function resolveJourneyPlace(query) {
    const raw=String(query || '').trim();
    if(raw.length<2)return null;
    if(/^my location$/i.test(raw)){const fix=await currentFix();return {query:raw,name:'Your location',lat:fix.lat,lon:fix.lon};}
    const response=await fetch(`/api/search?q=${encodeURIComponent(raw)}`,{cache:'no-store'});
    if(!response.ok)throw new Error('Place search is unavailable.');
    const payload=await response.json();
    const valid=row=>row && Number.isFinite(Number(row.lat)) && Number.isFinite(Number(row.lon));
    const matches=(Array.isArray(payload?.matches)?payload.matches:[]).filter(valid);
    const queryKey=raw.toLocaleLowerCase('en-GB');
    const exact=matches.find(row=>String(row.name||'').trim().toLocaleLowerCase('en-GB')===queryKey);
    const location=valid(payload?.location) ? payload.location : null;
    const chosen=exact || location || matches[0] || null;
    if(!chosen)return null;
    return {name:String(chosen.name || raw),lat:Number(chosen.lat),lon:Number(chosen.lon),query:raw};
  }

  function journeyTime(value) {
    if(!value)return '';
    const parsed=new Date(value);
    if(Number.isNaN(parsed.getTime()))return providerClock(value);
    return parsed.toLocaleString('en-GB',{weekday:'short',hour:'2-digit',minute:'2-digit'});
  }

  function modeLabel(mode) {
    const key=String(mode || '').toUpperCase();
    return ({WALK:'Walk',BICYCLE:'Cycle',BIKE:'Cycle',CAR:'Drive',BUS:'Bus',COACH:'Coach',RAIL:'Train',SUBWAY:'Underground',TRAM:'Tram',FERRY:'Ferry'}[key] || key.replaceAll('_',' ').toLowerCase().replace(/^./,c=>c.toUpperCase()));
  }

  function modeIcon(mode) {
    const key=String(mode || '').toUpperCase();
    if(['RAIL','SUBWAY','TRAM','MONORAIL','FUNICULAR'].includes(key))return icons.train;
    if(['BUS','COACH','TROLLEYBUS'].includes(key))return icons.bus;
    if(key==='FERRY')return icons.vessel;
    if(['BICYCLE','BIKE','SCOOTER'].includes(key))return icons.bike;
    if(key==='CAR')return icons.car;
    return icons.walk;
  }

  function renderJourneyLeg(leg) {
    const route=leg?.route && typeof leg.route==='object' ? (leg.route.short_name || leg.route.long_name) : leg?.route;
    const from=providerName(leg?.from), to=providerName(leg?.to);
    const depart=journeyTime(leg?.depart_at || leg?.scheduled_departure);
    const arrive=journeyTime(leg?.arrive_at || leg?.scheduled_arrival);
    const heading=route || leg?.headsign;
    const agency=providerName(leg?.agency);
    const distance=Number(leg?.distance_m);
    const facts=[agency,durationText(leg?.duration_seconds),Number.isFinite(distance)&&distance>0?`${distance>=1000?(distance/1000).toFixed(1)+' km':Math.round(distance)+' m'}`:null].filter(Boolean);
    return `<li class="journey-leg"><span class="journey-leg-icon">${modeIcon(leg?.mode)}</span><div><strong>${esc(modeLabel(leg?.mode))}${heading?` • ${esc(heading)}`:''}</strong><span>${esc([from,to].filter(Boolean).join(' → '))}</span>${facts.length?`<small>${esc(facts.join(' • '))}</small>`:''}</div><b>${esc([depart,arrive].filter(Boolean).join(' – '))}</b></li>`;
  }

  function renderOtpItinerary(option,index) {
    const duration=durationText(option?.duration_seconds);
    const transfers=Number(option?.transfers);
    const changeText=Number.isFinite(transfers) ? (transfers===0?'Direct':`${transfers} change${transfers===1?'':'s'}`) : '';
    const depart=journeyTime(option?.depart_at), arrive=journeyTime(option?.arrive_at);
    const walking=Number(option?.walk_distance_m);
    const trafficDelay=Number(option?.traffic_delay_seconds);
    const instructions=Array.isArray(option?.instructions)?option.instructions:[];
    const legs=(Array.isArray(option?.legs)?option.legs:[]).map(renderJourneyLeg).join('');
    const issues=Array.isArray(option?.route_issues)?option.route_issues:[];
    const geometry=Array.isArray(option?.geometry)?option.geometry:[];
    const traffic=Number.isFinite(trafficDelay)&&trafficDelay>0 ? `Traffic adds ${durationText(trafficDelay)}` : (geometry.length>1?'No reported traffic delay':null);
    const issueRows=issues.slice(0,5).map(issue=>`<li><span>${issue.type==='incident'?icons.incident:icons.road}</span><div><strong>${esc(issue.name || issue.reason || 'Road issue')}</strong><small>${esc([issue.road,issue.status,Number.isFinite(Number(issue.distance_km))?`${Number(issue.distance_km).toFixed(1)} km from route`:null].filter(Boolean).join(' • '))}</small></div></li>`).join('');
    const mapButton=geometry.length>1?`<button type="button" class="primary-button journey-show-map" data-journey-option="${index}">Show on map</button>`:'';
    const directionRows=instructions.slice(0,30).map(step=>`<li><span>${esc(step.message)}</span>${step.road?`<small>${esc(step.road)}</small>`:''}</li>`).join('');
    const directions=directionRows?`<details class="journey-directions"><summary>Driving directions (${instructions.length})</summary><ol>${directionRows}</ol></details>`:'';
    const walkText=Number.isFinite(walking)&&walking>0?`${walking>=1000?(walking/1000).toFixed(1)+' km':Math.round(walking)+' m'} walking`:null;
    const facts=[traffic,walkText].filter(Boolean).map(value=>`<span>${esc(value)}</span>`).join('');
    const steps=legs?`<details class="journey-steps"><summary>View journey steps</summary><ol class="journey-legs">${legs}</ol></details>`:'<p class="transport-empty">Detailed steps are not available for this option.</p>';
    return `<article class="journey-option"><div class="journey-option-head"><div><small>${index===0?'BEST MATCH':`OPTION ${index+1}`}</small><strong>${esc([duration,changeText].filter(Boolean).join(' • ') || 'Journey option')}</strong></div><b>${esc([depart,arrive].filter(Boolean).join(' → '))}</b></div>${facts?`<div class="journey-summary-facts">${facts}</div>`:''}${steps}${issueRows?`<details class="journey-issues journey-steps"><summary>Issues near this route</summary><ul>${issueRows}</ul></details>`:''}${directions}<div class="journey-option-actions">${mapButton}</div></article>`;
  }

  function showJourneyRoute(index) {
    const option=lastJourneyPayload?.itineraries?.[Number(index)];
    const geometry=Array.isArray(option?.geometry)?option.geometry:[];
    const points=geometry.map(point=>[Number(point.lat),Number(point.lon)]).filter(([lat,lon])=>Number.isFinite(lat)&&Number.isFinite(lon));
    if(points.length<2){showToast('This route can’t be drawn on the map.');return;}
    clearJourneyRoute();
    journeyRouteOutline=L.polyline(points,{pane:'journeyPane',color:'#071019',weight:13,opacity:.92,interactive:false,className:'journey-route-outline'}).addTo(map);
    journeyRouteLayer=L.polyline(points,{pane:'journeyPane',color:'#77f3ff',weight:7,opacity:1,interactive:false,className:'journey-route-line'}).addTo(map);
    (Array.isArray(option.route_issues)?option.route_issues:[]).forEach(issue=>{
      const lat=Number(issue.lat), lon=Number(issue.lon); if(!Number.isFinite(lat)||!Number.isFinite(lon))return;
      const marker=L.marker([lat,lon],{icon:L.divIcon({className:'journey-issue-icon',html:`<span>${issue.type==='incident'?icons.incident:icons.road}</span>`,iconSize:[30,30],iconAnchor:[15,15]})}).addTo(map);
      marker.bindTooltip?.(`${issue.name || issue.reason || 'Road issue'}${issue.road?` • ${issue.road}`:''}`,{direction:'top'});
      journeyIssueMarkers.push(marker);
    });
    const mapNode=$('map'); if(mapNode)mapNode.dataset.journeyRoutePoints=String(points.length);
    $('journey-route-clear')?.classList.remove('hidden');
    diagnostics.journeyRoutesShown=(diagnostics.journeyRoutesShown||0)+1;
    closeJourneyPlanner();
    map.fitBounds(L.latLngBounds(points),{padding:[45,45],maxZoom:13});
    showToast(journeyIssueMarkers.length?`Route shown with ${journeyIssueMarkers.length} problem${journeyIssueMarkers.length===1?'':'s'} on the way.`:'Route shown on the map.');
  }

  function clearJourneyRoute() {
    if(journeyRouteLayer && map.hasLayer(journeyRouteLayer))map.removeLayer(journeyRouteLayer);
    if(journeyRouteOutline && map.hasLayer(journeyRouteOutline))map.removeLayer(journeyRouteOutline);
    journeyRouteLayer=null;journeyRouteOutline=null;
    journeyIssueMarkers.forEach(marker=>{if(map.hasLayer(marker))map.removeLayer(marker);});
    journeyIssueMarkers=[];
    $('journey-route-clear')?.classList.add('hidden');
    $('map')?.removeAttribute('data-journey-route-points');
  }

  function railJourneyLegs(option) {
    const candidates=[option?.legs,option?.trains,option?.services,option?.journey];
    const rows=candidates.find(Array.isArray);
    if(rows)return rows;
    return option?.leg && typeof option.leg==='object' ? [option.leg] : [];
  }

  function renderRailJourney(option,index) {
    const depart=providerClock(option?.departs || option?.departure || option?.depart_at);
    const arrive=providerClock(option?.arrives || option?.arrival || option?.arrive_at);
    const duration=durationText(null,option?.duration_minutes);
    const type=String(option?.type || 'rail').replaceAll('_',' ');
    const legs=railJourneyLegs(option).map(leg=>{
      const train=leg?.train && typeof leg.train==='object' ? leg.train : {};
      const route=train.headcode || leg?.headcode || leg?.service || leg?.service_class || 'Train';
      const from=providerName(leg?.origin || leg?.from), to=providerName(leg?.destination || leg?.to);
      const legDepart=providerClock(leg?.departs || leg?.departure), legArrive=providerClock(leg?.arrives || leg?.arrival);
      return `<li class="journey-leg"><span class="journey-leg-icon">${icons.train}</span><div><strong>${esc(route)}</strong><span>${esc([from,to].filter(Boolean).join(' → '))}</span></div><b>${esc([legDepart,legArrive].filter(Boolean).join(' – '))}</b></li>`;
    }).join('');
    return `<article class="journey-option rail-fallback"><div class="journey-option-head"><div><small>${index===0?'BEST MATCH':`RAIL OPTION ${index+1}`}</small><strong>${esc([type,duration].filter(Boolean).join(' • '))}</strong></div><b>${esc([depart,arrive].filter(Boolean).join(' → '))}</b></div>${legs?`<details class="journey-steps"><summary>View journey steps</summary><ol class="journey-legs">${legs}</ol></details>`:'<p class="transport-empty">Route steps are not available for this journey.</p>'}</article>`;
  }

  function journeyErrorText(payload,fallback) {
    if(typeof payload?.detail==='string')return payload.detail;
    if(typeof payload?.message==='string')return payload.message;
    return fallback;
  }

  function renderJourneyResults(payload,resolvedOrigin,resolvedDestination) {
    const box=$('journey-results'); if(!box)return;
    const itineraries=Array.isArray(payload?.itineraries)?payload.itineraries:[];
    const railJourneys=Array.isArray(payload?.journeys)?payload.journeys:[];
    const results=itineraries.length ? itineraries.slice(0,3).map(renderOtpItinerary).join('') : railJourneys.slice(0,3).map(renderRailJourney).join('');
    const samePlace=(row)=>row && String(row.query||'').trim().toLocaleLowerCase('en-GB')===String(row.name||'').trim().toLocaleLowerCase('en-GB');
    const resolved=[resolvedOrigin&&!samePlace(resolvedOrigin)?`${resolvedOrigin.query} → ${resolvedOrigin.name}`:null,resolvedDestination&&!samePlace(resolvedDestination)?`${resolvedDestination.query} → ${resolvedDestination.name}`:null].filter(Boolean);
    const resolution=resolved.length?`<p class="journey-resolution">Using ${esc(resolved.join(' • '))}</p>`:'';
    const errors=(Array.isArray(payload?.routing_errors)?payload.routing_errors:[]).map(row=>row?.description).filter(Boolean);
    if(!results){
      lastJourneyPayload=null;
      box.innerHTML=`<div class="journey-unavailable"><strong>${payload?.status==='no_route'?'No route found':'Journey planning unavailable'}</strong><span>${esc(journeyErrorText(payload,'No source returned a route for that request.'))}</span>${errors.length?`<small>${esc(errors.join(' • '))}</small>`:''}</div>${resolution}`;
      return;
    }
    lastJourneyPayload=payload;
    // First answer the question people open this for: is anything in the way?
    const issues=(itineraries[0]?.route_issues)||[];
    const roadworks=issues.filter(row=>row?.type==='road').length, incidents=issues.filter(row=>row?.type!=='road').length;
    const issueText=issues.length?[roadworks?`${roadworks} roadwork${roadworks===1?'':'s'}`:null,incidents?`${incidents} incident${incidents===1?'':'s'}`:null].filter(Boolean).join(' and ')+' on this route':'No roadworks or incidents on this route';
    const issueBanner=itineraries.length?`<div class="journey-issue-summary ${issues.length?'has-issues':'is-clear'}">${iconSvg(issues.length?'incident':'route')}<span><strong>${esc(issueText)}</strong>${issues.length?`<small>${esc(issues.slice(0,3).map(row=>[row.name,row.road].filter(Boolean).join(' · ')).join(' • '))}</small>`:''}</span></div>`:'';
    box.innerHTML=`${issueBanner}<div class="journey-result-head"><div><strong>${esc(payload?.message || `${itineraries.length || railJourneys.length} journey options`)}</strong></div><span>Updated now</span></div>${resolution}<div class="journey-options">${results}</div><p class="journey-truth">Check with the operator before travelling.</p>`;
    box.querySelectorAll('.journey-show-map').forEach(button=>button.addEventListener('click',()=>showJourneyRoute(button.dataset.journeyOption)));
    renderJourneyWatch();
  }

  // Journey watch: a phone alert before the usual leaving time if a crash,
  // closure or long delay is on this drive.
  function journeyWatchClock(minutes){return `${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`;}
  function renderJourneyWatch() {
    const box=$('journey-results');if(!box)return;
    box.querySelector('.journey-watch')?.remove();
    const route=lastJourneyPayload?.itineraries?.[0]?.geometry;
    const watching=commuteAlertStatus.subscribed?commuteAlertStatus.preferences?.journey:null;
    const driving=($('journey-mode')?.value||'')==='car'&&Array.isArray(route)&&route.length>1;
    if(!driving&&!watching)return;
    const wrap=document.createElement('div');wrap.className='journey-watch';
    wrap.innerHTML=watching
      ? `<span><strong>Watching ${esc(watching.name)}</strong><small>Weekdays. You’ll be told in the 45 minutes before ${esc(journeyWatchClock(watching.leave_minutes))} if something serious is on it.</small></span><button type="button" class="mini-button" data-journey-watch="off">Stop</button>`
      : `${alertsNeedHomeScreen()?HOME_SCREEN_HINT:''}<span><strong>Warn me before I leave</strong><small>A phone alert on weekdays if a crash, closure or long delay is on this drive.</small></span><label>Leave at <input type="time" id="journey-watch-time" value="07:30"></label><button type="button" class="primary-button" data-journey-watch="on">Watch this drive</button>`;
    const anchor=box.querySelector('.journey-issue-summary');
    if(anchor)anchor.after(wrap);else box.prepend(wrap);
    wrap.querySelector('[data-journey-watch]').addEventListener('click',async event=>{
      if(event.currentTarget.dataset.journeyWatch==='off'){
        const ok=await savePushPreferences({...currentPushPreferences(),journey:null});
        showToast(ok?'Stopped watching that drive.':'That couldn’t be changed right now.');renderJourneyWatch();return;
      }
      if(pushUnsupportedReason()){showToast(pushUnsupportedReason());return;}
      if(!commuteAlertStatus.configured){showToast('Alerts are not available yet.');return;}
      if(!requireAlertsPlus('Journey watch alerts','journey-watch'))return;
      const [hours,minutes]=String($('journey-watch-time')?.value||'07:30').split(':').map(Number);
      const step=Math.max(1,Math.ceil(route.length/80));
      const points=route.filter((_,index)=>index%step===0||index===route.length-1).map(point=>[Number(point.lat),Number(point.lon)]);
      const destination=$('journey-destination')?.value.trim()||'work';
      const ok=await savePushPreferences({...currentPushPreferences(),journey:{name:`your drive to ${destination}`,points,leave_minutes:hours*60+minutes,days:[0,1,2,3,4]}});
      showToast(ok?`Watching your drive to ${destination}. You’ll only hear if something serious is on it.`:'Alerts couldn’t be turned on. Check notifications are allowed for this site.');
      if(ok)trackProduct('journey_watch_enabled','car');
      renderJourneyWatch();
    });
  }

  async function submitJourney(event) {
    event.preventDefault();
    const originName=$('journey-origin')?.value.trim() || '';
    const destinationName=$('journey-destination')?.value.trim() || '';
    const mode='car';
    const box=$('journey-results'), submit=$('journey-submit');
    if(originName.length<2 || destinationName.length<2)return;
    try { localStorage.setItem(JOURNEY_KEY,JSON.stringify({origin:originName,destination:destinationName,mode})); } catch {}
    submit.disabled=true;
    const submitLabel=submit.querySelector('span'); if(submitLabel)submitLabel.textContent='Finding…';
    box.innerHTML='<div class="journey-loading"><i></i><strong>Finding routes…</strong></div>';
    try {
      const [origin,destination]=await Promise.all([
        resolveJourneyPlace(originName).catch(()=>null),
        resolveJourneyPlace(destinationName).catch(()=>null),
      ]);
      if(!origin || !destination) throw new Error('Both places must resolve to map locations for driving. Try a town, postcode or street name.');
      const response=await fetch('/api/journeys/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({origin_name:originName,destination_name:destinationName,origin_lat:origin?.lat??null,origin_lon:origin?.lon??null,destination_lat:destination?.lat??null,destination_lon:destination?.lon??null,mode,arrive_by:false,wheelchair:false})});
      const payload=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(journeyErrorText(payload,'Journey planning is unavailable right now.'));
      renderJourneyResults(payload,origin,destination);
      trackProduct('journey_planned',mode);
    } catch(error) {
      box.innerHTML=`<div class="journey-unavailable"><strong>Could not plan this journey</strong><span>${esc(error?.message || 'Journey planning is unavailable right now.')}</span></div>`;
    } finally {
      submit.disabled=false; if(submitLabel)submitLabel.textContent='Find driving route';
    }
  }

  function openJourneyPlanner() {
    if(!requirePlus('journey_planner','Journey planning is included with Radar+.'))return;
    const dialog=$('journey-dialog'); if(!dialog)return;
    if(typeof dialog.showModal==='function')dialog.showModal(); else dialog.setAttribute('open','');
    if(!window.matchMedia('(max-width: 760px)').matches)setTimeout(()=>$('journey-origin')?.focus(),30);
    trackProduct('journey_planner_opened','journey');
  }

  function closeJourneyPlanner() {
    const dialog=$('journey-dialog'); if(!dialog)return;
    if(typeof dialog.close==='function')dialog.close(); else dialog.removeAttribute('open');
  }

  function connect() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${scheme}://${location.host}/ws`);
    socket.onopen = () => { clearTimeout(reconnectTimer); reconnectTimer=null; sendViewport(); sendWatching(selectedId); };
    socket.onmessage = (event) => {
      try {
        const payload=JSON.parse(event.data);
        // Align rail/aircraft estimates with the server clock at receipt time,
        // before the payload waits for the next animation frame.
        if(payload?.server_time_ms!=null)window.UKActivityMotionEngine?.setServerTime?.(payload.server_time_ms,Date.now());
        queueSocketPayload(payload);
      }
      catch { $('mode-pill').textContent = 'LIVE DATA ERROR'; }
    };
    socket.onclose = () => {
      $('mode-pill').textContent = 'RECONNECTING';
      if (!reconnectTimer) reconnectTimer=setTimeout(()=>{ reconnectTimer=null; connect(); },1800);
    };
    socket.onerror = () => socket.close();
  }

  async function inspectTrafficAt(latlng) {
    if (trafficMode === 'off' || !trafficConfigured) return;
    try {
      const r = await fetch(`/api/traffic/segment?lat=${encodeURIComponent(latlng.lat)}&lon=${encodeURIComponent(latlng.lng)}&zoom=${Math.max(7,Math.min(18,map.getZoom()))}`, {cache:'no-store'});
      if (!r.ok) return;
      const d = await r.json();
      const delay = d.delay_seconds == null ? '—' : d.delay_seconds >= 120 ? `${Math.round(d.delay_seconds/60)} min` : `${Math.round(d.delay_seconds)} sec`;
      const speed = d.current_speed_mph == null ? '—' : `${Math.round(d.current_speed_mph)} mph`;
      const free = d.free_flow_speed_mph == null ? '—' : `${Math.round(d.free_flow_speed_mph)} mph`;
      const label = String(d.congestion || 'unknown').replace(/^./,x=>x.toUpperCase());
      let cameraUrl, cameraLabel;
      if (latlng.lat >= 54.5) { cameraUrl='https://www.traffic.gov.scot/traffic-cameras'; cameraLabel='Official Traffic Scotland cameras'; }
      else if (latlng.lon < -2.4 && latlng.lat >= 51.2 && latlng.lat <= 53.6) { cameraUrl='https://traffic.wales/road-cameras'; cameraLabel='Official Traffic Wales cameras'; }
      else { cameraUrl='https://www.motorwaycameras.co.uk/'; cameraLabel='Motorway cameras (third-party)'; }
      L.popup({maxWidth:280}).setLatLng(latlng).setContent(`<div class="traffic-popup"><strong>${esc(label)} traffic</strong><div>Current: <b>${esc(speed)}</b></div><div>Free-flow: <b>${esc(free)}</b></div><div>Extra delay: <b>${esc(delay)}</b></div><div>Confidence: <b>${esc(d.confidence == null ? '—' : Math.round(Number(d.confidence)*100)+'%')}</b></div>${d.road_closure?'<div><b>Road closure reported</b></div>':''}<a target="_blank" rel="noopener" href="${esc(cameraUrl)}">${esc(cameraLabel)} ↗</a><small>Live flow segment • TomTom</small></div>`).openOn(map);
    } catch {}
  }

  map.on('click', e => {
    const liveId=liveObjectRenderer?.featureAt(e.containerPoint);
    if(liveId){selectActivity(liveId,true);return;}
    if(enabledActivityTypes.has('train')&&railLines.drawn.size){
      lookupRailLine(e.latlng).then(found=>{ if(found&&isPhoneLayout?.())setWorkspaceCollapsed(true); });
    }
    if(window.matchMedia('(max-width: 760px)').matches){
      if(selectedTrainActivity())setDetailRouteView(true);
      else closeDetailSelection();
      setWorkspaceCollapsed(true);
      hideSearchResults();
    }
    inspectTrafficAt(e.latlng);
  });
  map.on('dblclick', e => {
    e.originalEvent?.preventDefault?.();
    const lat=Number(e.latlng?.lat),lon=Number(e.latlng?.lng);
    if(!Number.isFinite(lat)||!Number.isFinite(lon))return;
    map.setView([lat,lon],Math.min(20,map.getZoom()+1),{animate:true});
  });
  let mapSettledTimer=null;
  map.on('zoomstart',()=>map.getContainer().classList.add('map-zooming-fast'));
  map.on('moveend zoomend', () => {
    map.getContainer().classList.remove('map-zooming-fast');
    clearTimeout(mapSettledTimer);
    mapSettledTimer=setTimeout(()=>{
      queueViewport();
      map.getContainer().classList.toggle('show-station-labels',map.getZoom()>=12);
      map.getContainer().classList.toggle('show-bus-destinations',map.getZoom()>=15);
      map.getContainer().classList.toggle('show-train-destinations',map.getZoom()>=14);
      map.getContainer().classList.toggle('show-aircraft-data',map.getZoom()>=7);
      if(enabledActivityTypes.has('train'))updateRailLines();
      if(effectivePlaceMode()!=='off')fetchPlaces();
      if(webcamsEnabled)fetchCameras(false);
      renderNearbySummary();
      renderStreamZoomNote();
    },80);
  });
  $('map')?.classList.toggle('show-aircraft-data',map.getZoom()>=7);
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => {
    aircraftLandingView='';
    enabledActivityTypes.delete('sar');
    const originalType=button.dataset.type;
    const turningBackOn=originalType==='cool-off'&&selectedType==='cool-off';
    const togglingActiveCool=currentWorkspace==='cool'&&originalType!=='cool-off'&&originalType===selectedType&&originalType.startsWith('cool');
    document.querySelectorAll('.filter').forEach(x => x.classList.remove('active'));
    selectedType=turningBackOn?'cool':(togglingActiveCool?'cool-off':originalType);
    if(selectedType==='cool-rail'&&originalType==='cool-rail')selectedRailSubtype='all';
    if(selectedType==='cool-off'){
      clearTransientLocationModes({clearNearestStop:false});
    }
    document.querySelector(`.filter[data-type="${selectedType}"]`)?.classList.add('active');
    const mapSwitch=$('cool-map-switch');
    if(mapSwitch){const off=selectedType==='cool-off';mapSwitch.classList.toggle('active',off);mapSwitch.innerHTML=off?'<span>○</span> Map off':'<span>●</span> Map on';mapSwitch.setAttribute('aria-label',off?'Turn Cool markers on':'Turn Cool markers off');}
    trackProduct('layer_selected', selectedType);
    if(selectedType==='cool-off'&&focusMarker){map.removeLayer(focusMarker);focusMarker=null;}
    if (selectedType === 'train' && map.getZoom() < 7) map.setZoom(7); if (selectedType === 'bus' && map.getZoom() < 7) map.setZoom(8); if (['vessel','sar'].includes(selectedType) && map.getZoom() < 6) map.setZoom(7);
    updateCoolRailSubtypeUi();
    syncBaseMap();
    sendViewport();
    renderMap(); renderStats(); renderRanking(); renderCoolSummary(); renderLiveTicker();
    const count=activities.filter(a=>layerTypeMatch(a,selectedType)).length;
    if(!count && selectedType!=='all') {
      const names=layerSourceNames[selectedType]||[]; const st=latestSources.find(x=>names.includes(x.name));
      if(st?.mode==='setup') showToast(`${sourceMeta[st.name]?.label||st.name} isn’t available right now.`);
      else if(st?.mode==='error') showToast(`${sourceMeta[st.name]?.label||st.name} isn’t available right now.`);
      else if(selectedType==='vessel' && st?.connected) showToast('No ships here. Try nearer the coast.');
      else if(selectedType==='sar' && st?.connected) showToast('No rescue aircraft here right now.');
    }
  }));
  document.querySelectorAll('[data-cool-rail-subtype]').forEach(button => button.addEventListener('click', () => {
    selectedRailSubtype = button.dataset.coolRailSubtype || 'all';
    clearTransientLocationModes({clearNearestStop:false});
    selectedType='cool-rail';filteredCacheKey='';
    document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x.dataset.type==='cool-rail'));
    updateCoolRailSubtypeUi();
    syncBaseMap();
    trackProduct('layer_selected', `cool-rail-${selectedRailSubtype}`);
    sendViewport();loadGlobalCool(false);
    renderMap(); renderStats(); renderRanking(); renderCoolSummary(); renderLiveTicker();
  }));
  document.querySelectorAll('[data-layer-toggle]').forEach(button => button.addEventListener('click', async () => {
    const type = button.dataset.layerToggle;
    if (!toggleableActivityTypes.has(type)) return;
    aircraftLandingView='';
    if(type==='aircraft')enabledActivityTypes.delete('sar');
    if (enabledActivityTypes.has(type)) enabledActivityTypes.delete(type); else { enabledActivityTypes.add(type); lastEnabledLayer = type; }
    // Live incidents (crashes, breakdowns, queues) are fetched by the server
    // for the area on screen, so they need a regional view, not all of Britain.
    if(['incident','road'].includes(type)&&enabledActivityTypes.has(type)&&map.getZoom()<8)showToast('Zoom in to see live incidents and roadworks near you.');
    if(type==='incident'&&!enabledActivityTypes.has(type)&&trafficMode==='incidents') await setTrafficMode('off',true);
    selectedType='useful';
    updateLayerToggleUi();
    sendViewport();
    renderMap(); renderStats(); renderRanking(); renderNearbySummary(); renderCoolSummary(); renderLiveTicker();
  }));
  document.querySelectorAll('.map-mode').forEach(button => button.addEventListener('click',()=>setMapMode(button.dataset.mapMode)));
  $('heat-mode').addEventListener('change', e => {
    heatMode = e.target.value;
    heatSnapshotPending = heatMode !== 'off';
    if (heatMode === 'bus' && map.getZoom() < 7) map.setZoom(8);
    renderHeatmap();
    renderStreamZoomNote();
    updateLayerToggleUi();
    sendViewport();
  });
  $('traffic-quick')?.addEventListener('click',()=>{ if(trafficMode==='off'&&!requirePlus('traffic_pro','Live traffic is available with Radar+.'))return; const style=$('traffic-style')?.value || 'combined'; setTrafficMode(trafficMode==='off'?style:'off'); });
  $('live-webcams')?.addEventListener('click',()=>{ if(!webcamsEnabled&&!requirePlus('live_cameras','Official live cameras are included with Radar+.'))return; webcamsEnabled=!webcamsEnabled; updateWebcamControl(); if(webcamsEnabled)fetchCameras(true); else { cameras=[]; renderCameraMarkers(); } trackProduct('layer_selected',webcamsEnabled?'live-cameras-on':'live-cameras-off'); });
  $('journey-planner')?.addEventListener('click',openJourneyPlanner);
  $('journey-close')?.addEventListener('click',closeJourneyPlanner);
  $('journey-form')?.addEventListener('submit',submitJourney);
  $('journey-swap')?.addEventListener('click',()=>{ const origin=$('journey-origin'),destination=$('journey-destination'); if(!origin||!destination)return; const old=origin.value; origin.value=destination.value; destination.value=old; (destination.value?origin:destination).focus(); });
  $('journey-use-location')?.addEventListener('click',async event=>{
    const button=event.currentTarget, origin=$('journey-origin'); if(!origin)return;
    const old=button.textContent; button.disabled=true; button.textContent='Locating…';
    try { await currentFix(); origin.value='My location'; $('journey-destination')?.focus(); }
    catch { showToast('Your location could not be found. Check location access and try again.'); }
    finally { button.disabled=false; button.textContent=old; }
  });
  $('journey-dialog')?.addEventListener('click',event=>{ if(event.target===$('journey-dialog'))closeJourneyPlanner(); });
  $('station-boards')?.addEventListener('click',async()=>{
    placeMode='rail'; $('place-mode').value='rail'; if(map.getZoom()<9) map.setZoom(10);
    syncBaseMap();
    const c=map.getCenter();
    try {
      const r=await fetch(`/api/places/nearest-rail?lat=${encodeURIComponent(c.lat)}&lon=${encodeURIComponent(c.lng)}`,{cache:'no-store'});
      if(r.ok){ const d=await r.json(); const p=d.place; if(p){ if(!places.some(x=>x.id===p.id))places.push(p); renderPlaces(); await selectPlace(p.id,true);  return; } }
    } catch {}
    const rows=await fetchPlaces();
    const rail=(rows||places).filter(p=>p.place_type==='rail_station').sort((a,b)=>distanceKm(c.lat,c.lng,a.lat,a.lon)-distanceKm(c.lat,c.lng,b.lat,b.lon));
    if(rail[0]){await selectPlace(rail[0].id,true);}else showToast('Stations are still loading. Try again in a minute.');
  });
  function closeDetailSelection(){
    $('detail-panel')?.classList.remove('show-technical');
    const tt=$('detail-tech-toggle'); if(tt)tt.textContent='More details';
    const closingTransientStop=selectedPlaceId&&selectedPlaceId===transientNearestBusPlaceId;
    selectedId=null; selectedPlaceId=null; returnPlaceId=null; detailBackButton.classList.add('hidden');
    syncDetailRouteControl(null);
    trackedActivityId=null; selectedDetailSignature='';
    if(closingTransientStop)clearTransientLocationModes();
    if(focusMarker){map.removeLayer(focusMarker);focusMarker=null;}
    clearSelectedTrail(); clearPhoto(); clearOperatorIdentity(); clearPlaceLink(); clearActivityIntelligence(); clearDiscoveryDetail();
    if(!railView.delaysOn)clearRailHighlight();
    $('map')?.removeAttribute('data-rail-line-view');
    const insight=$('detail-insight'); if(insight){insight.classList.add('hidden');insight.innerHTML='';}
    sendWatching(null);
    syncBaseMap();
    $('track-activity')?.classList.add('hidden'); $('save-place')?.classList.add('hidden'); $('share-activity')?.classList.add('hidden');
    $('detail-sidebar')?.classList.add('is-empty'); $('detail-panel')?.classList.remove('has-selection');
    renderRanking();
  }
  $('detail-close')?.addEventListener('click',()=>{
    // Closing a sheet only puts the sheet away. In particular, it must not
    // also turn off the live aircraft area: the radius pill is the explicit
    // control for ending Above me / airport mode, and the active chip can
    // reopen this list without another location or aircraft lookup.
    closeDetailSelection();
  });
  document.querySelectorAll('[data-rail-delays]').forEach(chip=>chip.addEventListener('click',()=>setDelayHotspots(!railView.delaysOn)));
  $('detail-route-view')?.addEventListener('click',()=>setDetailRouteView(true,{fit:true,focusPeek:true}));
  $('detail-route-restore')?.addEventListener('click',()=>setDetailRouteView(false,{focusDetails:true}));
  $('detail-route-fit')?.addEventListener('click',()=>focusSelectedTrainRoute({whole:true}));
  $('detail-route-close')?.addEventListener('click',closeDetailSelection);
  detailBackButton.addEventListener('click',()=>{const id=returnPlaceId;if(id)selectPlace(id,false);});
  $('traffic-style')?.addEventListener('change', e => { if(trafficMode!=='off') setTrafficMode(e.target.value); });
  $('place-mode').addEventListener('change', e => { placeMode=e.target.value || 'off'; trackProduct('smart_places_selected', placeMode); fetchPlaces(); updateLayerToggleUi(); });
  $('operator-filter')?.addEventListener('change', e => {
    selectedOperator=e.target.value || 'all';
    filteredCacheKey='';
    operatorOptionsSignature='';
    updateOperatorFilterOptions();
    renderMap();renderStats();renderRanking();renderNearbySummary();renderCoolSummary();renderLiveTicker();
    showToast(selectedOperator==='all'?'Showing all operators.':`Showing ${selectedOperator} only.`);
  });
  $('clear-map')?.addEventListener('click',()=>{
    enabledActivityTypes.clear();
    clearTransientLocationModes();
    selectedOperator='all';operatorOptionsSignature='';updateOperatorFilterOptions();
    heatMode='off'; heatSnapshotPending=false; if($('heat-mode')) $('heat-mode').value='off';
    placeMode='off'; if($('place-mode')) $('place-mode').value='off';
    setTrafficMode('off',true);
    webcamsEnabled=false; cameras=[]; updateWebcamControl(); renderCameraMarkers();
    clearJourneyRoute();
    if(currentWorkspace==='cool'){selectedType='cool-off';document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active'));$('cool-map-switch')?.classList.add('active');}
    fetchPlaces(); sendViewport(); updateLayerToggleUi();
    renderMap(); renderHeatmap(); renderStats(); renderRanking(); renderNearbySummary(); renderLiveTicker();
    showToast('Map cleared.');
  });
  $('near-radius').addEventListener('change', e => { nearbyState.radiusMiles = Number(e.target.value || 10); if (nearbyState.active) { renderNearbyOverlay(); renderMap(); renderStats(); renderRanking(); } });
  function showDeviceLocation(lat,lon) {
    lastKnownDeviceLocation={lat:Number(lat),lon:Number(lon)};
    if(deviceLocationMarker)map.removeLayer(deviceLocationMarker);
    deviceLocationMarker=L.circleMarker([lat,lon],{radius:7,color:'#ffffff',weight:2,fillColor:'#56d6a4',fillOpacity:.95,interactive:false}).addTo(map);
    const button=$('recenter');
    if(button){button.textContent='My location';button.title='Centre on my current location';}
  }

  let mapPlacedByLink=false;
  function centreMapOnDeviceLocation({silent=false,startup=false}={}) {
    // Opened from a link to a place, airport or item: show where the user is,
    // but do not move the map away from what the link asked for.
    const keepView=()=>startup&&mapPlacedByLink;
    if(lastKnownDeviceLocation){
      if(keepView())return;
      map.setView([lastKnownDeviceLocation.lat,lastKnownDeviceLocation.lon],Math.max(map.getZoom(),12),{animate:!startup});
      sendViewport();
      return;
    }
    if(!navigator.geolocation){
      if(!silent)showToast('Location is unavailable in this browser.');
      return;
    }
    navigator.geolocation.getCurrentPosition(position=>{
      const lat=Number(position.coords.latitude),lon=Number(position.coords.longitude);
      if(!Number.isFinite(lat)||!Number.isFinite(lon))return;
      showDeviceLocation(lat,lon);
      if(keepView())return;
      map.setView([lat,lon],12,{animate:!startup});
      sendViewport();
      if(!startup)trackProduct('nearby_used','recenter-location');
    },()=>{
      const button=$('recenter');if(button){button.textContent='Locate me';button.title='Allow location to centre the map near you';}
      if(!silent)showToast('Location is off, so the map shows all of Britain.');
    },{enableHighAccuracy:false,timeout:8000,maximumAge:300000});
  }

  $('near-me').addEventListener('click', () => {
    const button=$('near-me'),note=button?.querySelector('small');
    if (!navigator.geolocation) { showToast('Location isn’t available. Search for a place instead.'); return; }
    if(button)button.disabled=true;if(note)note.textContent='Finding your nearest stop…';
    navigator.geolocation.getCurrentPosition(async pos => {
      const lat=pos.coords.latitude,lon=pos.coords.longitude;
      clearTransientLocationModes();
      // This action promises one nearest stop, not every stop in the viewport.
      // Keep the general places layer off so map movement cannot flood the map.
      placeMode='off';if($('place-mode'))$('place-mode').value='off';
      enabledActivityTypes.add('bus');updateLayerToggleUi();
      map.setView([lat,lon],15,{animate:true});trackProduct('nearby_used','nearest-bus');
      collapseWorkspaceOnPhone();
      try {
        const response=await fetch(`/api/places/nearest-bus?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`,{cache:'no-store'});
        const payload=await response.json().catch(()=>({}));if(!response.ok)throw new Error(payload.detail||'No nearby stop is ready');
        const place=payload.place;
        places=[place];
        transientNearestBusPlaceId=place.id;
        renderPlaces();sendViewport();await selectPlace(place.id,true);
        const distance=Number(place.distance_km);showToast(`Opened ${place.name}${Number.isFinite(distance)?` • ${distance<1?Math.round(distance*1000)+' m':distance.toFixed(1)+' km'} away`:''}.`);
      } catch(error) {
        places=[];transientNearestBusPlaceId=null;renderPlaces();showToast('Your nearest bus stop couldn’t load right now.');
      } finally {if(button)button.disabled=false;if(note)note.textContent='Nearest stop and live departures';}
    }, () => {if(button)button.disabled=false;if(note)note.textContent='Nearest stop and live departures';showToast('Location is off. Search for a stop instead.');}, {enableHighAccuracy:true,timeout:10000,maximumAge:120000});
  });
  // "Planes above" anywhere: the user (Above me) or an airport.
  async function startOverheadAt(lat,lon,{source='device',label='',zoom=9,radius=25,announce=false}={}){
    const button=$('overhead-now');
    clearTransientLocationModes();
    if($('near-radius')){if(![...$('near-radius').options].some(o=>o.value===String(radius))){const o=document.createElement('option');o.value=String(radius);o.textContent=`${radius} mi`;$('near-radius').appendChild(o);}$('near-radius').value=String(radius);}
    overheadActive=true;button?.classList.add('active');
    selectedType='nearby-aircraft';
    if(currentWorkspace==='useful'&&!enabledActivityTypes.has('aircraft')){enabledActivityTypes.add('aircraft');overheadAutoEnabledAircraft=true;updateLayerToggleUi();}
    document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x.dataset.type==='cool-air'));
    setNearby(lat,lon,source);
    nearbyState.label=label;
    renderNearbyOverlay();
    updateSubChips();
    seedNearbyAircraftFromLiveMap();
    const retainedCount=nearbyAircraftRows.length;
    if(button?.querySelector('small'))button.querySelector('small').textContent=retainedCount
      ? `${retainedCount} aircraft already on map • checking live feed…`
      : 'Checking the live aircraft feed…';
    map.setView([lat,lon],zoom,{animate:true});
    if(source==='device')collapseWorkspaceOnPhone();
    await refreshOverheadAircraft({announce});
    sendViewport();
  }

  // An airport in the app: planes around it on the map, and a panel saying
  // what is arriving, leaving, on the ground and overhead. Each flight opens.
  const AIRPORT_RADIUS_MILES=15;
  function airportGroups(airport){
    const groups={arriving:[],departing:[],ground:[],overhead:[]};
    for(const a of nearbyAircraftRows){
      if(!['aircraft','sar'].includes(a.type))continue;
      if(aircraftNotWorthShowing(a))continue;
      const km=distanceKm(airport.lat,airport.lon,Number(a.lat),Number(a.lon));
      if(!Number.isFinite(km)||km>AIRPORT_RADIUS_MILES*1.609344)continue;
      const alt=a.altitude_ft==null?null:Number(a.altitude_ft), rate=Number(a.vertical_rate_fpm)||0;
      const route=flightRouteById.get(a.id);
      const to=String(a.destination||route?.destination?.iata||'').toUpperCase(), from=String(a.origin||route?.origin?.iata||'').toUpperCase();
      // Landing or taking off counts as arriving or leaving, not on the ground.
      const phase=km<5?aircraftGroundPhase(a)?.short:null;
      const ground=phase?['Parked','On ground','Taxiing','On runway'].includes(phase):(/on ground/i.test(String(a.status||''))||(alt!=null&&alt<100&&km<3));
      const row={a,km};
      if(ground&&km<5)groups.ground.push(row);
      else if(phase==='Landing')groups.arriving.push(row);
      else if(phase==='Taking off')groups.departing.push(row);
      else if(to===airport.iata||(!to&&alt!=null&&alt<6000&&rate<-300&&km<24))groups.arriving.push(row);
      else if(from===airport.iata||(!from&&alt!=null&&alt<6000&&rate>300&&km<24))groups.departing.push(row);
      else groups.overhead.push(row);
    }
    Object.values(groups).forEach(rows=>rows.sort((x,y)=>x.km-y.km));
    return groups;
  }
  // "Landing 27L, departing 27R · Wind from 250° at 12 kt", refreshed each minute.
  const airportRunways=new Map();
  function airportRunwayLine(airport){
    const cached=airportRunways.get(airport.iata);
    if(!cached||Date.now()-cached.at>60000){
      airportRunways.set(airport.iata,{at:Date.now(),summary:cached?.summary||null});
      fetch(`/api/airports/${encodeURIComponent(airport.iata)}/runways`).then(r=>r.ok?r.json():null).then(state=>{
        if(!state)return;
        airportRunways.set(airport.iata,{at:Date.now(),summary:state.summary||null});
        if(airportOpen?.iata===airport.iata)renderAirportPanel(airport);
      }).catch(()=>{});
    }
    return airportRunways.get(airport.iata)?.summary||null;
  }
  function renderAirportPanel(airport){
    if(!overheadActive||nearbyState.source!=='airport'||nearbyState.label!==airport.iata)return;
    const g=airportGroups(airport);
    const runwayLine=airportRunwayLine(airport);
    const row=({a,km})=>{
      // The same words as the map label: "landing at 122 kt", not "0 ft".
      const phase=aircraftGroundPhase(a);
      const knots=Math.round(aircraftSpeedKt(a));
      const words={'On runway':'on the runway','On ground':'on the ground',Parked:'parked',Taxiing:'taxiing',Landing:'landing','Taking off':'taking off'};
      const alt=phase?(['Parked','On ground'].includes(phase.short)||!Number.isFinite(knots)?words[phase.short]:`${words[phase.short]} at ${knots} kt`)
        :a.altitude_ft==null?(/on ground/i.test(String(a.status||''))?'on the ground':'')
        :Number(a.altitude_ft)>=100?`${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} ft`:'low';
      const route=[a.origin,a.destination].filter(Boolean).join(' → ');
      const facts=[route,a.aircraft_model||a.aircraft_type_code,alt,awayText(km)].filter(Boolean).join(' · ');
      return `<button type="button" class="line-train" data-job-plane="${esc(a.id)}"><i class="line-dot" aria-hidden="true"></i><span><strong>${esc(a.name)}</strong><small>${esc(facts)}</small></span><b>›</b></button>`;
    };
    const section=(title,rows)=>rows.length?`<h3 class="airport-group">${esc(title)} <span>${rows.length}</span></h3><div class="line-trains">${rows.slice(0,12).map(row).join('')}</div>`:'';
    const html=[section('Arriving',g.arriving),section('Leaving',g.departing),section('On the ground',g.ground),section('Passing overhead',g.overhead)].join('')
      ||'<p class="transport-empty">No aircraft are being tracked near the airport right now. It refreshes every few seconds.</p>';
    const box=openInfoPanel(`${airport.name} Airport`,`${g.arriving.length} arriving · ${g.departing.length} leaving · ${g.ground.length} on the ground · ${g.overhead.length} overhead`,
      `${runwayLine?`<p class="airport-runways"><strong>Runways now</strong> ${esc(runwayLine)}</p>`:''}${html}<p class="airport-panel-note">From live aircraft tracking within ${AIRPORT_RADIUS_MILES} miles, not an official arrivals board. <a href="${esc(airport.path)}">Airport page</a></p>`);
    box.querySelectorAll('[data-job-plane]').forEach(button=>button.addEventListener('click',()=>focusActivityById(button.dataset.jobPlane,map.getZoom())));
  }
  let airportOpen=null;
  async function openAirport(airport){
    airportOpen=airport;
    openInfoPanel(`${airport.name} Airport`,'Finding aircraft around the airport…','<p class="coverage-loading">Checking the live aircraft feed…</p>');
    await startOverheadAt(airport.lat,airport.lon,{source:'airport',label:airport.iata,zoom:10,radius:AIRPORT_RADIUS_MILES});
    renderAirportPanel(airport);
    trackProduct('airport_opened',airport.iata);
  }

  $('overhead-now')?.addEventListener('click',()=>{
    const button=$('overhead-now');
    if(overheadActive){
      clearTransientLocationModes();
      selectedType=currentWorkspace==='cool'?'cool-off':'useful';
      renderNearbyOverlay();sendViewport();renderMap();renderStats();renderRanking();showToast('Showing everything again.');return;
    }
    if(!navigator.geolocation){showToast('Location is not available in this browser.');return;}
    if(button){button.disabled=true;button.setAttribute('aria-busy','true');button.querySelector('small').textContent='Finding your location…';}
    navigator.geolocation.getCurrentPosition(async pos=>{
      await startOverheadAt(pos.coords.latitude,pos.coords.longitude,{source:'device',zoom:9,radius:25,announce:true});
      if(button){button.disabled=false;button.removeAttribute('aria-busy');}
    },()=>{
      showToast('Location is off.');
      if(button){button.disabled=false;button.removeAttribute('aria-busy');button.querySelector('small').textContent='Aircraft received within 50 miles';}
    },{enableHighAccuracy:false,timeout:7000,maximumAge:120000});
  });
  $('near-centre').addEventListener('click',()=>{ const c=map.getCenter(); setNearby(c.lat,c.lng,'centre'); trackProduct('nearby_used','map-centre'); });
  $('near-clear').addEventListener('click',()=>{ clearTransientLocationModes({clearNearestStop:false}); sendViewport();renderMap();renderStats();renderRanking(); });
  $('add-watch-zone').addEventListener('click',()=>{ if(!billingState.plus&&watchZones.length>=1){openPlusDialog();showToast('Free My Radar includes one watch zone. Radar+ unlocks more.');return;} const c=map.getCenter(); const radius=Number($('near-radius').value || 10); const suggested=`Watch zone ${watchZones.length+1}`; const name=(window.prompt('Name this live watch zone', suggested) || '').trim(); if(!name)return; watchZones.push({id:`zone-${Date.now()}`,name,lat:c.lat,lon:c.lng,radiusMiles:radius}); saveWatchZones(); renderWatchZones();renderMyRadar(); trackProduct('watch_zone_added','watch-zone'); showToast(`${name} saved • ${radius} mile radius`); });
  $('search').addEventListener('input', (e) => {
    // The results list answers a search; the map is no longer emptied of
    // everything that does not contain the typed letters.
    const typed = e.target.value.trim();
    clearTimeout(searchTimer);
    if (!typed) { searchRequest++; hideSearchResults(); return; }
    // Results as you type, once you pause.
    searchTimer = setTimeout(runSearch, 500);
  });
  $('search').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } });
  $('search-go').addEventListener('click', runSearch);
  $('journey-route-clear')?.addEventListener('click',()=>{clearJourneyRoute();showToast('Route cleared.');});
  document.addEventListener('click', (e) => { if (!e.target.closest('.search-shell')) hideSearchResults(); });
  $('recenter').addEventListener('click', () => { if(searchMarker){map.removeLayer(searchMarker);searchMarker=null;} centreMapOnDeviceLocation(); });
  $('follow').addEventListener('click', async () => {
    if (!selectedId) return;
    const wasFollowing = followed.has(selectedId);
    if(!wasFollowing&&!billingState.plus&&followed.size>=3){openPlusDialog();showToast('You can save three items for free. Radar+ removes the limit.');return;}
    if (wasFollowing) { followed.delete(selectedId); delete followedInfo[selectedId]; saveFollowedInfo(); }
    else { followed.add(selectedId); rememberFollowed(activityById(selectedId)); }
    saveFollowed(); renderMyRadar();
    const selected = activityById(selectedId);
    if (selected?.type==='train') {
      if (!wasFollowing) {
        checkSavedTrains();
        // Phone alerts switch on with the save where the device allows it;
        // otherwise say plainly what works.
        const already=commuteAlertStatus.subscribed;
        const enabled=already||await enableSavedTrainAlerts();
        const unsupported=pushUnsupportedReason();
        const beyondFree=alertsLimited()&&savedTrainIds().indexOf(selectedId)>0;
        if(beyondFree)trackProduct('plus_limit_reached','saved-train-alerts');
        showToast(beyondFree?'Saved. On the free plan, phone alerts cover your first saved train. Radar+ covers them all.'
          : enabled?'Saved. Your phone will tell you if this train is late, cancelled, changes platform or gets a confirmed formation.'
          : unsupported&&commuteAlertStatus.configured?`Saved. ${unsupported}`
          : 'Saved. You will be told here if this train is late, cancelled, changes platform or gets a confirmed formation.');
      }
      syncSavedTrainsToPush();
      renderCommuteAlertStatus();
    }
    $('follow').textContent = followed.has(selectedId) ? 'Saved ✓' : 'Save';
    trackProduct(wasFollowing ? 'activity_unfollowed' : 'activity_followed', selected?.type || 'other');
  });
  $('track-activity')?.addEventListener('click', () => {
    if (!selectedId) return;
    const selected = activityById(selectedId);
    if (!selected) return;
    const wasTracking = trackedActivityId === selectedId;
    if(!wasTracking&&!requirePlus('live_tracking','Live object tracking is included with Radar+.'))return;
    trackedActivityId = wasTracking ? null : selectedId;
    renderTrackingControl(selected);
    if (!wasTracking) map.setView([selected.lat,selected.lon], Math.max(map.getZoom(),10), {animate:true});
    trackProduct(wasTracking ? 'activity_tracking_stopped' : 'activity_tracking_started', selected.type || 'other');
  });
  document.querySelectorAll('[data-cool-view]').forEach(button=>button.addEventListener('click',()=>setDiscoveryView(button.dataset.coolView)));
  $('save-discovery')?.addEventListener('click',claimSelectedDiscovery);
  $('share-discovery')?.addEventListener('click',shareSelectedDiscovery);
  $('share-activity')?.addEventListener('click',shareSelectedActivity);
  $('save-place')?.addEventListener('click',()=>{
    const place=places.find(row=>row.id===selectedPlaceId);if(!place)return;
    if(savedPlaces.has(place.id)){savedPlaces.delete(place.id);showToast(`${place.name} removed from Saved.`);}
    else {if(!billingState.plus&&savedPlaces.size>=2){openPlusDialog();showToast('You can save two places for free. Radar+ removes the limit.');return;}savedPlaces.set(place.id,{id:place.id,name:place.name,subtitle:place.subtitle,place_type:place.place_type,lat:Number(place.lat),lon:Number(place.lon),crs:place.crs,atco_code:place.atco_code,source:place.source});showToast(`${place.name} saved.`);}
    saveMyRadarPreferences();renderMyRadar();$('save-place').textContent=savedPlaces.has(place.id)?'Saved ✓':'Save place';trackProduct('place_saved',place.place_type||'place');
  });
  $('today-refresh')?.addEventListener('click',()=>loadTodayRadar(true));
  $('passing-next-refresh')?.addEventListener('click',loadPassingNext);
  $('passing-next-place')?.addEventListener('change',event=>chooseSavedPlace(event.target.value));
  $('commute-alert-place')?.addEventListener('change',event=>chooseSavedPlace(event.target.value));
  $('commute-alert-mode')?.addEventListener('change',()=>renderCommuteAlertPlaces());
  $('commute-alert-enable')?.addEventListener('click',enableCommuteAlerts);
  $('commute-alert-disable')?.addEventListener('click',disableCommuteAlerts);
  $('commute-alert-test')?.addEventListener('click',sendTestAlert);
  $('spotter-toggle')?.addEventListener('click',toggleSpotter);
  // Changing what to be told about takes effect at once when spotting is on.
  document.querySelectorAll('[data-spotter-kind]').forEach(box=>box.addEventListener('change',async()=>{
    const spotter=commuteAlertStatus.subscribed?commuteAlertStatus.preferences?.spotter:null;
    if(!spotter)return;
    const kinds=spotterKinds();
    if(!kinds.length){box.checked=true;showToast('Keep at least one ticked, or turn spotter alerts off.');return;}
    const ok=await savePushPreferences({...currentPushPreferences(),spotter:{...spotter,kinds}});
    if(!ok)showToast('That couldn’t be saved right now.');
  }));
  $('spotter-radius')?.addEventListener('change',()=>{ if(commuteAlertStatus.subscribed&&commuteAlertStatus.preferences?.spotter)savePushPreferences({...currentPushPreferences(),spotter:{...commuteAlertStatus.preferences.spotter,radius_miles:Number($('spotter-radius').value||10)}}); });

  document.querySelectorAll('.workspace-tab[data-workspace]').forEach(btn=>btn.addEventListener('click',()=>{
    // On a phone a tab is a new screen: put the detail sheet away. A train
    // keeps its route on the map as the compact route bar.
    if(isPhoneLayout()&&!$('detail-sidebar')?.classList.contains('is-empty')){
      if(selectedTrainActivity())setDetailRouteView(true);
      else closeDetailSelection();
    }
    // Map on a phone: coming from another tab shows the map; tapping Map
    // again while on the map opens its panel ("Near you", layers), and once
    // more puts it away.
    const reopenMapPanel=btn.dataset.workspace==='useful'&&currentWorkspace==='useful'&&$('workspace-shell')?.classList.contains('collapsed');
    activateWorkspace(btn.dataset.workspace,true);
    if(btn.dataset.workspace==='useful'&&!reopenMapPanel)collapseWorkspaceOnPhone();
  }));
  document.querySelectorAll('[data-late-only]').forEach(chip=>chip.addEventListener('click',()=>{
    lateTrainsOnly=!lateTrainsOnly;
    trainFilter=lateTrainsOnly?'late':(trainFilter==='late'?'':trainFilter);
    saveMapFilters();updateAircraftFilterUi();
    trackProduct('sub_chip',lateTrainsOnly?'late-only-on':'late-only-off');
    updateSubChips();
    renderMap(); renderStats(); renderRanking();
    if(lateTrainsOnly)showToast('Showing only trains running 5 min or more late, and cancellations.');
  }));
  document.querySelectorAll('[data-sub-action]').forEach(chip=>chip.addEventListener('click',()=>{
    const action=chip.dataset.subAction;
    trackProduct('sub_chip',action);
    if(action==='line-status')showLineStatus();
    else if(action==='trains-near')jobTrains();
    else if(action==='station')$('station-boards')?.click();
    else if(action==='bus-next')$('near-me')?.click();
    else if(action==='bus-stops'){
      placeMode=placeMode==='bus'?'off':'bus';
      if($('place-mode'))$('place-mode').value=placeMode;
      if(placeMode==='bus'&&map.getZoom()<14)showToast('Zoom in to see bus stops.');
      fetchPlaces(); updateLayerToggleUi();
    }
    else if(action==='bus-disruptions')showBusDisruptions();
    else if(action==='planes')jobPlanes();
    else if(action==='sky')startSkyPointer();
    else if(action==='camera')startCameraView(chip.dataset.subFor);
    else if(action==='journey')jobJourney();
    else if(action==='traffic')$('traffic-quick')?.click();
    else if(action==='webcams'){$('live-webcams')?.click();updateSubChips();}
    else if(action==='radar'){rainRadar.wanted=!rainRadar.wanted;syncRainRadar();updateSubChips();}
    else if(action==='radar-play'){toggleRainRadarPlay();updateSubChips();}
  }));
  document.querySelectorAll('[data-layer-chip]').forEach(chip=>chip.addEventListener('click',()=>{
    document.querySelector(`.layer-toggle[data-layer-toggle="${chip.dataset.layerChip}"]`)?.click();
  }));
  $('nearby-bar')?.addEventListener('click',()=>openMapPanel());
  $('map-layers-open')?.addEventListener('click',()=>openMapPanel('.useful-layer-switches'));
  const modePill=$('mode-pill');
  const syncLiveState=()=>{
    const text=String(modePill?.textContent||'').toUpperCase();
    document.body.dataset.liveState=/LIVE|FIXTURE|DEMO/.test(text)&&!/ERROR/.test(text)?'live':/RECONNECT|ERROR|OFFLINE|DOWN/.test(text)?'down':'starting';
  };
  if(modePill){new MutationObserver(syncLiveState).observe(modePill,{childList:true,characterData:true,subtree:true});syncLiveState();}
  $('workspace-collapse')?.addEventListener('click',()=>setWorkspaceCollapsed(!$('workspace-shell')?.classList.contains('collapsed')));
  const setFocusMode=(active)=>{
    const layout=document.querySelector('.v12-layout'),button=$('focus-map');
    if(!layout||!button)return;
    layout.classList.toggle('focus-mode',active);
    button.setAttribute('aria-pressed',String(active));
    button.innerHTML=active?'× <span>Show panels</span>':'⛶ <span>Focus map</span>';
    button.title=active?'Show panels':'Hide panels and focus on the map';
    setTimeout(()=>map.invalidateSize(),190);
  };
  $('focus-map')?.addEventListener('click',()=>setFocusMode(!document.querySelector('.v12-layout')?.classList.contains('focus-mode')));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&document.querySelector('.v12-layout')?.classList.contains('focus-mode'))setFocusMode(false);});
  $('detail-tech-toggle')?.addEventListener('click',()=>{ const panel=$('detail-panel'); if(!panel)return; panel.classList.toggle('show-technical'); $('detail-tech-toggle').textContent=panel.classList.contains('show-technical')?'Hide details':'More details'; });
  loadDiscoveryLocalState();
  discoveryCatalogue=fallbackDiscoveryCatalogue.map((row,index)=>normaliseCollectible(row,index)).filter(Boolean);
  discoveryMissions=fallbackDiscoveryMissions();
  renderDiscoveryExperience();
  // A predictable clean launch is more useful than restoring a crowded map.
  activateWorkspace('useful', false);
  collapseWorkspaceOnPhone();
  updateLayerToggleUi();

  $('radar-plus')?.addEventListener('click',openPlusDialog);
  $('network-upgrade')?.addEventListener('click',openPlusDialog);
  $('plus-close')?.addEventListener('click',()=>{const dialog=$('plus-dialog');if(typeof dialog?.close==='function')dialog.close();else dialog?.removeAttribute('open');});
  for(const id of ['radar-plus-checkout','radar-plus-yearly'])$(id)?.addEventListener('click',async event=>{
    const button=event.currentTarget,period=button.dataset.period==='year'?'year':'month',label=button.textContent;
    try{
      button.disabled=true;button.textContent='Opening secure checkout…';
      trackProduct('plus_checkout_started',period);
      const payload=await postBilling(`/api/billing/checkout?period=${period}`);
      location.assign(payload.checkout_url);
    }catch(error){showToast(error.message);}
    finally{button.disabled=false;button.textContent=label;}
  });
  $('radar-plus-portal')?.addEventListener('click',async()=>{try{const payload=await postBilling('/api/billing/portal');location.assign(payload.portal_url);}catch(error){showToast(error.message);}});
  $('radar-plus-device')?.addEventListener('click',async()=>{
    try{
      const payload=await postBilling('/api/billing/device-code');
      $('radar-plus-code').textContent=payload.code;
      $('radar-plus-code-box')?.classList.remove('hidden');
      trackProduct('plus_device_code','');
    }catch(error){showToast(error.message||'Could not make a code right now.');}
  });
  $('radar-plus-link')?.addEventListener('submit',async event=>{
    event.preventDefault();
    const input=$('radar-plus-link-code');
    const code=String(input?.value||'').trim();
    if(!code){input?.focus();return;}
    try{
      const response=await fetch('/api/billing/device-link',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({code})});
      const payload=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:'That code did not work.');
      if(input)input.value='';
      await loadBillingStatus();
      showToast('Radar+ is now on for this device.');
      trackProduct('plus_device_linked','');
    }catch(error){showToast(error.message||'That code did not work.');}
  });
  $('radar-plus-logout')?.addEventListener('click',async()=>{try{await fetch('/api/billing/logout',{method:'POST'});await loadBillingStatus();showToast('Radar+ access was signed out on this device.');}catch{showToast('Could not sign out right now.');}});
  $('radar-plus-preview')?.addEventListener('submit',async event=>{
    event.preventDefault();
    const input=$('radar-plus-preview-code'),button=event.currentTarget.querySelector('button');
    const code=String(input?.value||'').trim();if(!code)return;
    try{
      button.disabled=true;button.textContent='Checking…';
      const response=await fetch('/api/billing/preview',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({code})});
      const payload=await response.json().catch(()=>({}));if(!response.ok)throw new Error(payload.detail||'Preview code not recognised');
      if(input)input.value='';await loadBillingStatus();
      if(socket){socket.onclose=null;socket.close();}connect();
      showToast('Radar+ preview is active on this device.');
    }catch(error){showToast(error?.message||'Could not unlock preview access.');}
    finally{button.disabled=false;button.textContent='Unlock';}
  });
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferredInstallPrompt=e; $('install-app')?.classList.remove('hidden'); trackProduct('pwa_install_prompted','pwa'); });
  $('install-app')?.addEventListener('click', async()=>{ if(!deferredInstallPrompt)return; deferredInstallPrompt.prompt(); try { await deferredInstallPrompt.userChoice; } catch {} deferredInstallPrompt=null; $('install-app')?.classList.add('hidden'); });
  window.addEventListener('appinstalled',()=>trackProduct('pwa_installed','pwa'));
  if ('serviceWorker' in navigator) {
    // Registered at the top of the site so it covers this page. It used to
    // be registered under /static/, which never did; that old one is removed.
    navigator.serviceWorker.register('/service-worker.js',{scope:'/'}).catch(()=>{});
    navigator.serviceWorker.getRegistrations?.().then(list=>list.forEach(registration=>{ if(new URL(registration.scope).pathname==='/static/')registration.unregister(); })).catch(()=>{});
  }
  initAnalyticsConsent();
  trackProduct('app_open','web');

  // Search landing pages open the promised live aircraft view instead of
  // dropping the visitor onto an empty default map. Specialist filters are
  // evidence-led: unidentified aircraft stay hidden rather than being guessed.
  (()=>{
    const view=new URLSearchParams(location.search).get('view');
    const supported=new Set(['military-aircraft','helicopters','police-helicopters','air-ambulances','aircraft-near-me']);
    if(!supported.has(view))return;
    const clean=new URL(location.href);
    clean.searchParams.delete('view');
    history.replaceState(null,'',clean.pathname+(clean.search||'')+clean.hash);
    if(view==='aircraft-near-me'){
      setTimeout(()=>document.querySelector('[data-job="planes"]')?.click(),0);
      return;
    }
    if(view==='military-aircraft'){
      activateWorkspace('cool',false);
      document.querySelector('.filter[data-type="cool-air"]')?.click();
      showToast('Showing identified military and special aircraft.');
      return;
    }
    aircraftLandingView=view;
    enabledActivityTypes.add('aircraft');
    enabledActivityTypes.add('sar');
    selectedType='useful';
    updateLayerToggleUi();
    sendViewport();
    renderMap();renderStats();renderRanking();renderLiveTicker();
    const labels={helicopters:'helicopters','police-helicopters':'identified police helicopters','air-ambulances':'identified air ambulances'};
    showToast(`Showing ${labels[view]}.`);
  })();

  // A link (a shared page, a phone alert) can open the app on one thing:
  // /?open=<id>. Wait for the live connection, then show it and tidy the URL.
  // /?place=rail-station:RUG (from a station page) opens that station's board.
  (()=>{
    const placeId=new URLSearchParams(location.search).get('place');
    if(!placeId||!/^rail-station:[A-Z]{3}$/.test(placeId))return;
    mapPlacedByLink=true;
    history.replaceState(null,'',location.pathname+location.hash);
    setTimeout(async()=>{
      try {
        const response=await fetch(`/api/places/${encodeURIComponent(placeId)}/board?limit=6`,{cache:'no-store'});
        if(!response.ok)return;
        const {place}=await response.json();
        if(!place?.id)return;
        if(!places.some(p=>p.id===place.id))places.push(place);
        renderPlaces();await selectPlace(place.id,true);if(map.getZoom()<12)map.setZoom(13);
        trackProduct('deep_link_opened','station');
      } catch {}
    },400);
  })();
  // /?airport=BHX (from an airport page): the airport view in the app.
  (()=>{
    const code=String(new URLSearchParams(location.search).get('airport')||'').toUpperCase();
    if(!/^[A-Z]{3}$/.test(code))return;
    mapPlacedByLink=true;
    history.replaceState(null,'',location.pathname+location.hash);
    fetch(AIRPORTS_URL).then(r=>r.ok?r.json():[]).then(rows=>{
      const airport=rows.find(a=>a.iata===code);
      if(airport)setTimeout(()=>openAirport(airport),300);
    }).catch(()=>{});
  })();
  // /?lat=..&lon=..&zoom=..&layer=aircraft (from an airport page): open the
  // map there with that layer on.
  (()=>{
    const q=new URLSearchParams(location.search);
    const lat=Number(q.get('lat')),lon=Number(q.get('lon'));
    if(!q.has('lat')||!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>90||Math.abs(lon)>180)return;
    const zoom=Math.max(5,Math.min(16,Number(q.get('zoom'))||11));
    mapPlacedByLink=true;
    const layer=q.get('layer');
    history.replaceState(null,'',location.pathname+location.hash);
    map.setView([lat,lon],zoom);
    if(layer&&/^[a-z]+$/.test(layer)){
      const chip=document.querySelector(`[data-layer-chip="${layer}"]`);
      if(chip&&chip.getAttribute('aria-pressed')!=='true')chip.click();
    }
    trackProduct('deep_link_opened','map-view');
  })();
  (()=>{
    const wanted=new URLSearchParams(location.search).get('open');
    if(!wanted||wanted.length>120)return;
    mapPlacedByLink=true;
    history.replaceState(null,'',location.pathname+location.hash);
    let tries=0;
    const attempt=async()=>{
      tries+=1;
      const live=document.body.dataset.liveState==='live'||activities.length>0;
      if(!live&&tries<40){setTimeout(attempt,250);return;}
      if(await focusActivityById(wanted,13)===false)return;
      trackProduct('deep_link_opened',wanted.split(':')[0]||'item');
    };
    setTimeout(attempt,300);
  })();

  loadFollowed();
  loadWatchZones();
  loadMyRadarPreferences();
  renderWatchZones();
  renderMyRadar();
  try { setMapMode(localStorage.getItem('ukRadarMapMode') || 'standard'); } catch { setMapMode('standard'); }
  setTrafficMode('off', true);
  // Ask once on first load so the live map begins where the visitor is. The
  // UK overview remains visible when location is unavailable or declined.
  centreMapOnDeviceLocation({silent:true,startup:true});
  connect();
  setInterval(()=>{refreshLiveMarkerPositions();startLiveMotion();refreshBusShapes();},1000);
  startLiveMotion();
  // Leaflet animates the zoom itself; re-placing markers mid-zoom fights it.
  map.on('zoomstart',()=>{liveMotionZooming=true;});
  map.on('zoomend',()=>{liveMotionZooming=false;resyncClusteredMarkers();startLiveMotion();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshLiveMarkerPositions();startLiveMotion();}});

  // These account/editorial requests do not affect the first map paint or
  // websocket connection. Start them in the browser's first idle slice.
  const loadStartupExtras=()=>{
    diagnostics.startupExtrasAtMs=Math.round(performance.now());
    loadBillingStatus().then(()=>{
      const status=new URLSearchParams(location.search).get('billing');
      // On iPhone, Safari and the Home Screen app are separate: say how to
      // carry Radar+ across, since alerts only work from the Home Screen.
      const messages={success:alertsNeedHomeScreen()?'Radar+ is on. For alerts, add Radar to your Home Screen, then use Radar+ → Use on another device for a code.':'Radar+ is active on this device.',pending:'Payment is still processing.',failed:'Payment could not be confirmed.',unavailable:'Radar+ billing is not configured yet.','portal-return':'Subscription settings updated.'};
      if(status&&messages[status]){showToast(messages[status]);history.replaceState({},'',location.pathname+location.hash);}
      if(status==='success'&&billingState.plus)trackProduct('plus_subscribed',billingState.subscription_status||'active');
    });
    loadCommuteAlertStatus();
    checkSavedTrains();
    setInterval(checkSavedTrains,60000);
    loadTodayRadar(true);
  };
  if('requestIdleCallback' in window) window.requestIdleCallback(loadStartupExtras,{timeout:1200});
  else setTimeout(loadStartupExtras,250);
})();
