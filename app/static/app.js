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
    let config=null;
    try {
      // Map provider credentials can be enabled after a deployment. Never keep
      // the earlier "not configured" response in a visitor's browser cache.
      const response=await fetch('/api/map/config',{cache:'no-store'});
      if(response.ok)config=await response.json();
    } catch {}
    tileSets.standard=baseTileLayer(config);
    tileSets.rail=config?.configured&&config?.rail_tile_url
      ? baseTileLayer({...config,tile_url:config.rail_tile_url})
      : null;
    document.getElementById('map').dataset.mapProvider=config?.configured?'mapbox':'tomtom-fallback';
    syncBaseMap();
    if(config?.live_object_renderer!==false&&window.UKActivityLiveRendererModule&&window.UKActivityMotionEngine){
      try{
        const module=await window.UKActivityLiveRendererModule;
        liveObjectRenderer=new module.LiveObjectRenderer({
          container:document.getElementById('live-object-map'),leafMap:map,motionEngine:window.UKActivityMotionEngine,
          onReady:()=>renderMap(),
        });
        document.getElementById('map').dataset.liveRenderer='maplibre';
      }catch(error){
        document.getElementById('map').dataset.liveRenderer='leaflet-fallback';
        console.warn('MapLibre live renderer unavailable; retaining Leaflet markers.',error);
      }
    }
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

  const icons = { train:'🚆', bus:'🚌', vessel:'🚢', sar:'🚁', aircraft:'✈️', rescue:'🛟', incident:'⚠️', road:'🚧', roadfeature:'☎', flood:'🌊', weather:'🌦️', earthquake:'🌐', river:'🏞️', tide:'🌊', wave:'🌊', grid:'⚡', airquality:'💨', bike:'🚲', space:'🌌', satellite:'🛰️', outage:'🔌', gritter:'❄️', camera:'📷', other:'•' };
  const markers = new Map();
  let focusMarker = null;
  const markerGroup = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({ disableClusteringAtZoom: 10, maxClusterRadius: 48, showCoverageOnHover: false, spiderfyOnMaxZoom: true })
    : L.layerGroup();
  markerGroup.addTo(map);
  // Aircraft must remain individually selectable at national zoom levels. Dense
  // ground/sea activity can still use clusters, but aircraft never enter one.
  const aerialMarkerGroup = L.layerGroup().addTo(map);
  const cameraMarkerGroup = L.layerGroup().addTo(map);
  const cameraMarkers = new Map();
  let cameras = [];
  let webcamsEnabled = false;
  let cameraRequest = 0;
  let cameraDetailRequest = 0;

  const placeIcons = {rail_station:'🚉',bus_stop:'▣',rain_gauge:'🌧️'};
  const placeMarkers = new Map();
  const placeGroup = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({ disableClusteringAtZoom: 14, maxClusterRadius: 40, showCoverageOnHover:false })
    : L.layerGroup();
  placeGroup.addTo(map);
  let places = [];
  let placeMode = 'off';
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
  const searchableCache = new WeakMap();
  const interestCache = new WeakMap();
  const coolCache = new WeakMap();
  let selectedId = null;
  let selectedActivitySnapshot = null;
  let selectedType = 'useful';
  let selectedRailSubtype = 'all';
  let selectedOperator = 'all';
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
    ['steam','rail','Steam movement','A live steam-hauled railway movement.','♨'],
    ['railtour-special','rail','Railtour','A charter or special passenger movement.','◆'],
    ['test-measurement','rail','Measurement train','A railway test or measurement movement.','◇'],
    ['engineering','rail','Engineering train','A specialist railway engineering movement.','⚒'],
    ['freight','rail','Freight train','A live non-passenger freight movement.','▰'],
    ['locomotive-move','rail','Special locomotive move','A special locomotive movement.','◆'],
  ].map(([subtype,category,title,explanation,icon])=>({id:`${category}-${subtype}`,key:subtype,category,subtype,title,explanation,icon,badge:title.toUpperCase(),safety_note:null}));
  let deferredInstallPrompt = null;
  let pendingSocketPayload = null;
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
  const ga4Configured=/^G-[A-Z0-9]{6,20}$/.test(ga4MeasurementId);
  let ga4Loaded=false;

  function loadGoogleAnalytics() {
    if(!ga4Configured||ga4Loaded)return;
    ga4Loaded=true;
    window.dataLayer=window.dataLayer||[];
    window.gtag=window.gtag||function(){window.dataLayer.push(arguments);};
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
    if(analyticsChoice()==='granted'&&typeof window.gtag==='function'){
      window.gtag('event',eventName,{feature_label:String(feature||'').slice(0,64),event_category:'product'});
    }
    try {
      await fetch('/api/product/event', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,event_name:eventName,feature})});
    } catch {}
  }

  function hasPlusFeature(feature) {
    return Boolean(billingState.plus && billingState.features.includes(feature));
  }

  async function loadBillingStatus() {
    try {
      const response=await fetch('/api/billing/status',{cache:'no-store'});
      if(!response.ok)throw new Error('access status unavailable');
      billingState=await response.json();
    } catch { billingState={enabled:false,plus:false,features:[],price_display:''}; }
    renderBillingStatus();
    return billingState;
  }

  function renderBillingStatus() {
    const plus=Boolean(billingState.plus);
    const price=$('plus-price'); if(price)price.textContent=billingState.price_display||'';
    const note=$('plus-note');
    if(note)note.textContent=plus?'Radar+ is active on this device.':'The public live map stays free. Payment is handled securely by Stripe.';
    const summary=$('product-summary');
    if(summary)summary.textContent=plus?'Radar+ active • advanced network tools unlocked':(billingState.enabled?`Free access • Radar+ ${billingState.price_display||''}`:'Free access • paid checkout will appear when billing is connected');
    $('radar-plus-checkout')?.classList.toggle('hidden',plus||!billingState.enabled);
    $('radar-plus-portal')?.classList.toggle('hidden',!plus);
    $('radar-plus-logout')?.classList.toggle('hidden',!plus);
    $('radar-plus-preview')?.classList.toggle('hidden',plus||!billingState.preview_available);
    const top=$('radar-plus'); if(top)top.textContent=plus?'Radar+ ✓':'Radar+';
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
    return `<span class="cool-badge cool-${esc(c.theme)}${compact?' compact':''}"><b>${esc(c.icon)}</b><em>${esc(c.label)}</em></span>`;
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

  function updateLayerToggleUi() {
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
    syncBaseMap();
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
        if (!quiet) showToast('Live traffic needs your TomTom API key. Run configure_traffic.bat, then restart the radar.');
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
    if (status) status.textContent = mode === 'off' ? 'Off • click to show' : `${mode === 'combined' ? 'Flow + incidents' : mode === 'delays' ? 'Delays only' : mode === 'incidents' ? 'Incidents only' : 'Live flow'} • on`;
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
    const route = a.origin && a.destination ? `${a.origin} → ${a.destination}` : (a.route || a.subtitle || a.position_label || '');
    const speed = compactSpeed(a);
    const heading = compass(a.bearing);
    const delayN = a.delay_minutes == null ? null : Number(a.delay_minutes);
    const delay = delayN == null ? null : (delayN === 0 ? 'On time' : `${delayN > 0 ? '+' : ''}${delayN} min`);
    const movement = [heading, speed].filter(Boolean).join(' • ');
    const next = a.next_location ? `Next ${a.next_location}` : null;
    const status = a.status || a.severity || null;
    const pills = [];
    if (status) pills.push(`<span class="tooltip-pill">${esc(status)}</span>`);
    if (delay) pills.push(`<span class="tooltip-pill ${delayN > 5 ? 'warn' : 'good'}">${esc(delay)}</span>`);
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

  const AIRCRAFT_TYPE_PHOTOS = {
    chinook: {
      src: '/static/assets/aircraft/chinook-raf.jpg',
      title: 'RAF Boeing CH-47 Chinook',
      page: 'https://commons.wikimedia.org/wiki/File:Raf_ch-47_chinook_hc2_za707_arp.jpg',
      licence: 'https://commons.wikimedia.org/wiki/File:Raf_ch-47_chinook_hc2_za707_arp.jpg#Licensing',
      credit: 'Adrian Pingstone • Public domain • Wikimedia Commons',
    },
    apache: {
      src: '/static/assets/aircraft/apache-army.jpg',
      title: 'British Army Apache helicopters',
      page: 'https://commons.wikimedia.org/wiki/File:Army_Air_Corps_Apache_Helicopters_MOD_45153319.jpg',
      licence: 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/1/',
      credit: 'Ministry of Defence / Crown copyright • OGL v1.0',
    },
    kc135: {
      src: '/static/assets/aircraft/kc135-usaf.jpg',
      title: 'USAF Boeing KC-135 Stratotanker',
      page: 'https://commons.wikimedia.org/wiki/File:2022_Milwaukee_Air_%26_Water_Show_085_(Wisconsin_Air_National_Guard_Boeing_KC-135_Stratotanker).jpg',
      licence: 'https://creativecommons.org/licenses/by-sa/4.0/',
      credit: 'Michael Barera • CC BY-SA 4.0 • Wikimedia Commons',
    },
    typhoon: {
      src: '/static/assets/aircraft/typhoon-raf.jpg',
      title: 'Royal Air Force Eurofighter Typhoon',
      page: 'https://commons.wikimedia.org/wiki/File:Typhoon.f2.zj922.ff2006.arp.jpg',
      licence: 'https://commons.wikimedia.org/wiki/File:Typhoon.f2.zj922.ff2006.arp.jpg#Licensing',
      credit: 'Adrian Pingstone • Public domain • Wikimedia Commons',
    },
    a400m: {
      src: '/static/assets/aircraft/a400m-raf.jpg',
      title: 'Royal Air Force Airbus A400M Atlas',
      page: 'https://commons.wikimedia.org/wiki/File:RAF_A400M_Atlas_Aircraft_MOD_45158356.jpg',
      licence: 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/1/',
      credit: 'Andrew Linnett / Ministry of Defence • OGL v1.0',
    },
    c17: {
      src: '/static/assets/aircraft/c17-raf.jpg',
      title: 'Royal Air Force Boeing C-17 Globemaster III',
      page: 'https://commons.wikimedia.org/wiki/File:RAF_C17_Aircraft_in_Afghanistan_MOD_45157241.jpg',
      licence: 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/1/',
      credit: 'Ministry of Defence • OGL v1.0',
    },
    coastguard: {
      src: '/static/assets/aircraft/s92-coastguard.jpg',
      title: 'UK Coastguard Sikorsky S-92',
      page: 'https://commons.wikimedia.org/wiki/File:Stornoway_Coastguard_S92_Helicopter.jpg',
      licence: 'https://creativecommons.org/publicdomain/zero/1.0/',
      credit: 'Edinburgh838 • CC0 • Wikimedia Commons',
    },
  };

  function aircraftTypePhoto(a) {
    const text=[a.aircraft_model,a.aircraft_type_code,a.subtitle,a.name].filter(Boolean).join(' ').toUpperCase();
    const kind=/KC-?135|K35R|STRATOTANKER/.test(text)?'kc135'
      : /CHINOOK|CH-?47|\bH47\b/.test(text)?'chinook'
      : /APACHE|AH-?64/.test(text)?'apache'
      : /EUROFIGHTER|TYPHOON|\bEF2000\b/.test(text)?'typhoon'
      : /A400M|ATLAS C?\.?1|\bA4M\b/.test(text)?'a400m'
      : /C-?17|GLOBEMASTER|\bC17\b/.test(text)?'c17'
      : /COASTGUARD|SIKORSKY S-?92|\bS92\b/.test(text)?'coastguard'
      : aircraftMapShape(a).kind;
    const media=AIRCRAFT_TYPE_PHOTOS[kind];
    if(!media)return null;
    return {
      ...media,
      image:`<a target="_blank" rel="noopener" href="${esc(media.page)}"><img class="aircraft-type-photo" src="${esc(media.src)}" alt="${esc(media.title)}" loading="eager" fetchpriority="high"></a>`,
      truth:'Reference photo of this aircraft type — not necessarily this exact airframe',
    };
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
    const timeout = setTimeout(()=>controller.abort(), 3000);
    try {
      const response = await fetch(`/api/activity/${encodeURIComponent(a.id)}/media`,{signal:controller.signal});
      if (!response.ok) throw new Error('Image lookup unavailable');
      const payload = await response.json();
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
      if (a.type === 'train') {
        imageTruth = `Photo of this vehicle or class${a.vehicle_ref ? ` ${a.vehicle_ref}` : ''} — not live`;
      } else if (a.type === 'bus') imageTruth = `Photo matched to vehicle ${a.vehicle_ref} — not live`;
      else if (a.type === 'vessel') imageTruth = 'Photo of this vessel — not live';
      box.innerHTML = `<a target="_blank" rel="noopener" href="${esc(sourceUrl)}"><img src="${esc(safeUrl(payload.thumbnail_url))}" alt="${esc(payload.title || a.name)}" loading="lazy"></a><div class="detail-photo-copy"><strong>${esc(payload.title || a.name)}</strong>${imageTruth ? `<b class="image-truth">${esc(imageTruth)}</b>` : ''}<span>${esc(attribution || payload.source || 'Wikimedia Commons')} • <a target="_blank" rel="noopener" href="${esc(sourceUrl)}">image source ↗</a> • <a target="_blank" rel="noopener" href="${esc(licenceUrl)}">licence ↗</a></span></div>`;
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
    const knownIdentity = [a.vehicle_ref, a.aircraft_model || a.aircraft_type_code].filter(Boolean).join(' • ');
    const fallback=aircraftTypePhoto(a);
    const fallbackVisual=fallback?.image || '';
    box.classList.remove('hidden');
    box.innerHTML = fallback
      ? `${fallbackVisual}<div class="detail-photo-copy aircraft-known"><strong>${esc(fallback.title)}</strong><span>${esc(knownIdentity || 'Live aircraft')}</span><b class="image-truth">${esc(fallback.truth)}</b><span>${esc(fallback.credit)} • <a target="_blank" rel="noopener" href="${esc(fallback.page)}">image source ↗</a> • <a target="_blank" rel="noopener" href="${esc(fallback.licence)}">licence ↗</a></span></div>`
      : '<div class="photo-loading">Looking for a licensed aircraft photo…</div>';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3500);
    try {
      const response=await fetch(`/api/activity/${encodeURIComponent(a.id)}/media`,{cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error('lookup unavailable');
      const reference=await response.json();
      if (requestId !== mediaRequest || selectedId !== a.id) return;
      const reg = a.vehicle_ref || null;
      const model = a.aircraft_model || a.aircraft_type_code || null;
      const identity = [reg, model].filter(Boolean).join(' • ');
      const referenceImage=reference.ok && reference.thumbnail_url;
      const imageUrl=referenceImage ? reference.thumbnail_url : null;
      const imagePage=reference.page_url || reference.search_url;
      const image = imageUrl ? `<a target="_blank" rel="noopener" href="${esc(safeUrl(imagePage))}"><img src="${esc(safeUrl(imageUrl))}" alt="${esc(identity || a.name)}" loading="lazy"></a>` : '';
      const imageTruth=referenceImage ? (reference.match_kind==='exact_airframe' ? 'Archive photo of this aircraft registration — not live' : 'Example of this aircraft type — not necessarily this exact airframe') : null;
      const imageCredit=referenceImage ? [reference.artist,reference.license,'Wikimedia Commons'].filter(Boolean).join(' • ') : fallback?.credit;
      const links = a.source_id ? `<a target="_blank" rel="noopener" href="https://www.planespotters.net/hex/${esc(String(a.source_id).toUpperCase())}">Planespotters ↗</a>` : '';
      const visibleTruth=imageTruth || fallback?.truth;
      if(!image && !fallbackVisual){box.classList.add('hidden');box.innerHTML='';return;}
      box.innerHTML = `${image || fallbackVisual}<div class="detail-photo-copy"><strong>${esc(referenceImage ? (model || a.aircraft_model || a.aircraft_type_code || 'Aircraft type not resolved') : fallback.title)}</strong><span>${esc(identity || a.name)}</span>${visibleTruth?`<b class="image-truth">${esc(visibleTruth)}</b>`:''}<span>${esc(imageCredit || 'Licensed aircraft reference')}${!referenceImage && fallback ? ` • <a target="_blank" rel="noopener" href="${esc(fallback.page)}">image source ↗</a> • <a target="_blank" rel="noopener" href="${esc(fallback.licence)}">licence ↗</a>` : ''}${links ? ` • ${links}` : ''}</span></div>`;
      const remoteImage=referenceImage ? box.querySelector('img') : null;
      remoteImage?.addEventListener('error',()=>{
        if(requestId!==mediaRequest || selectedId!==a.id)return;
        if(!fallback){box.classList.add('hidden');box.innerHTML='';return;}
        box.innerHTML=`${fallbackVisual}<div class="detail-photo-copy aircraft-known"><strong>${esc(fallback.title)}</strong><span>${esc(identity || a.name)}</span><b class="image-truth">${esc(fallback.truth)}</b><span>${esc(fallback.credit)} • <a target="_blank" rel="noopener" href="${esc(fallback.page)}">image source ↗</a> • <a target="_blank" rel="noopener" href="${esc(fallback.licence)}">licence ↗</a></span></div>`;
      },{once:true});
    } catch {
      if (requestId !== mediaRequest) return;
      if (knownIdentity && fallback) {
        box.classList.remove('hidden');
        box.innerHTML = `${fallbackVisual}<div class="detail-photo-copy aircraft-known"><strong>${esc(fallback.title)}</strong><span>${esc(knownIdentity)}</span><b class="image-truth">${esc(fallback.truth)}</b><span>${esc(fallback.credit)} • <a target="_blank" rel="noopener" href="${esc(fallback.page)}">image source ↗</a> • <a target="_blank" rel="noopener" href="${esc(fallback.licence)}">licence ↗</a></span></div>`;
      } else { box.classList.add('hidden'); box.innerHTML=''; }
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

  function aircraftMapIdentity(v) {
    const text=[v.aircraft_model,v.aircraft_type_code,v.subtitle,v.name].filter(Boolean).join(' ').toUpperCase();
    const known=[
      [/LYNX|WILDCAT/, 'LYNX'],
      [/APACHE|AH-?64/, 'APACHE'], [/CHINOOK|CH-?47|\bH47\b/, 'CHINOOK'],
      [/EUROFIGHTER|TYPHOON|\bEUFI\b/, 'TYPHOON'], [/F-?35|LIGHTNING II/, 'F-35'],
      [/A400M|\bA400\b|ATLAS/, 'ATLAS'], [/C-?17|GLOBEMASTER/, 'C-17'],
      [/VOYAGER|KC-?2|A330 MRTT/, 'VOYAGER'], [/C-?130|HERCULES|\bC30J\b/, 'HERCULES'],
      [/HAWK/, 'HAWK'], [/OSPREY|V-?22/, 'OSPREY'], [/AWACS|SENTRY|E-?3/, 'AWACS'],
      [/COASTGUARD|SIKORSKY S-?92|\bS92\b/, 'COASTGUARD'],
      [/SPITFIRE|\bSPIT\b/, 'SPITFIRE'], [/HURRICANE/, 'HURRICANE'], [/LANCASTER/, 'LANCASTER'],
      [/CESSNA\s*152|\bC152\b/, 'CESSNA 152'], [/CESSNA\s*172|\bC172\b/, 'CESSNA 172'],
      [/KC-?135|STRATOTANKER/, 'KC-135'],
    ];
    for (const [pattern,label] of known) if(pattern.test(text)) return label;
    const fallback=String(v.aircraft_model || v.aircraft_type_code || v.vehicle_ref || (v.type==='sar'?'RESCUE':'AIRCRAFT')).trim();
    return fallback.replace(/^(BOEING|AIRBUS|LEONARDO|LOCKHEED MARTIN|SIKORSKY)\s+/i,'').toUpperCase().slice(0,18);
  }

  function aircraftMapShape(v) {
    const text=[v.aircraft_model,v.aircraft_type_code,v.subtitle].filter(Boolean).join(' ').toUpperCase();
    // Original category-specific silhouettes; details remain visible at map size.
    const helicopter='<svg class="map-aircraft-svg map-helicopter-svg" viewBox="0 0 64 64" aria-hidden="true"><path class="rotor" d="M4 22h56M11 8l42 28M11 36 53 8"/><circle class="rotor-hub" cx="32" cy="22" r="3"/><path class="airframe" d="M32 13c-7 0-11 5-11 13 0 8 5 13 9 15l-2 14h8l-2-14c4-2 9-7 9-15 0-8-4-13-11-13Z"/><path class="tail-boom" d="M32 39v20"/><path class="tail-plane" d="M22 53h20"/><path class="tail-rotor" d="M32 54v10m-5-5h10"/></svg>';
    const chinook='<svg class="map-aircraft-svg map-chinook-svg" viewBox="0 0 64 64" aria-hidden="true"><path class="rotor" d="M4 17h34M12 5l18 24M12 29 30 5M26 47h34M34 35l18 24M34 59l18-24"/><circle class="rotor-hub" cx="21" cy="17" r="3"/><circle class="rotor-hub" cx="43" cy="47" r="3"/><path class="airframe" d="M20 12c-5 2-7 7-5 12l18 29c3 5 10 6 15 2 4-3 5-9 2-14L31 12c-2-3-7-4-11 0Z"/><path class="tail-plane" d="M38 48h18M15 17 7 24"/></svg>';
    const apache='<svg class="map-aircraft-svg map-apache-svg" viewBox="0 0 64 64" aria-hidden="true"><path class="rotor" d="M3 23h58M10 7l44 32M10 39 54 7"/><circle class="rotor-hub" cx="32" cy="23" r="3"/><path class="airframe" d="M32 10c-5 0-9 5-9 13l4 18 2 15h6l2-15 4-18c0-8-4-13-9-13Z"/><path class="stub-wing" d="M13 37h38l-5 5H18Z"/><path class="tail-rotor" d="M32 53v11m-5-5h10"/></svg>';
    const fastJet='<svg class="map-aircraft-svg map-fastjet-svg" viewBox="0 0 64 64" aria-hidden="true"><path class="airframe" d="M32 2c-3 4-5 12-5 20L8 43v6l19-8 1 10-9 7v3l13-4 13 4v-3l-9-7 1-10 19 8v-6L37 22c0-8-2-16-5-20Z"/><path class="jet-intake" d="m22 32 5-7v13Zm20 0-5-7v13Z"/><path class="tail-detail" d="m24 54 8-4 8 4"/></svg>';
    const heavy='<svg class="map-aircraft-svg map-heavy-svg" viewBox="0 0 64 64" aria-hidden="true"><path class="airframe" d="M32 2c-4 4-6 13-6 23L3 40v8l23-8 2 11-10 7v3l14-4 14 4v-3l-10-7 2-11 23 8v-8L38 25c0-10-2-19-6-23Z"/><ellipse class="engine" cx="13" cy="42" rx="3" ry="5"/><ellipse class="engine" cx="22" cy="37" rx="3" ry="5"/><ellipse class="engine" cx="42" cy="37" rx="3" ry="5"/><ellipse class="engine" cx="51" cy="42" rx="3" ry="5"/></svg>';
    const airliner='<svg class="map-aircraft-svg map-airliner-svg" viewBox="0 0 64 64" aria-hidden="true"><path class="airframe" d="M32 2c-3 4-5 13-5 25L5 42v6l22-8 1 12-9 6v3l13-4 13 4v-3l-9-6 1-12 22 8v-6L37 27c0-12-2-21-5-25Z"/><ellipse class="engine" cx="18" cy="42" rx="3" ry="5"/><ellipse class="engine" cx="46" cy="42" rx="3" ry="5"/></svg>';
    const propeller='<svg class="map-aircraft-svg map-propeller-svg" viewBox="0 0 64 64" aria-hidden="true"><path class="airframe" d="M32 8c-3 4-4 12-4 21L9 38v7l19-5 1 12-9 6v3l12-4 12 4v-3l-9-6 1-12 19 5v-7l-19-9c0-9-1-17-4-21Z"/><circle class="prop-hub" cx="32" cy="8" r="3"/><path class="prop-line" d="M12 8h40M32 3v10"/><path class="cockpit-detail" d="M29 18h6"/></svg>';
    if(/CHINOOK|CH-?47|\bH47\b/.test(text)) return {kind:'chinook',html:chinook,rotorcraft:true};
    if(/APACHE|AH-?64/.test(text)) return {kind:'apache',html:apache,rotorcraft:true};
    if(v.type==='sar' || /LYNX|WILDCAT|MERLIN|GAZELLE|PUMA|HELICOPTER|SIKORSKY|AW1|EC1|H135|H145|DAUPHIN|\bS92\b/.test(text)) return {kind:'helicopter',html:helicopter,rotorcraft:true};
    if(/TYPHOON|EUROFIGHTER|F-?35|F-?16|F-?15|F-?18|TORNADO|HAWK|GRIPEN|RAFALE/.test(text)) return {kind:'fastjet',html:fastJet,rotorcraft:false};
    if(/KC-?135|STRATOTANKER|VOYAGER|A330|A340|A350|A380|747|C-?17|GLOBEMASTER|A400M|ATLAS|HERCULES|C-?130|AWACS|SENTRY|RIVET/.test(text)) return {kind:'heavy',html:heavy,rotorcraft:false};
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
      return L.divIcon({
        className:'marker-wrap aerial-marker-wrap',
        html:`<div class="motion-shell aerial-motion-shell"><div class="aerial-map-marker aerial-${esc(type)} aircraft-shape-${shape.kind}" title="${esc(identity)} — ${esc(v.name)}"><span class="aircraft-silhouette${shape.rotorcraft?' rotorcraft-silhouette':''}" style="transform:rotate(${rotation}deg)">${shape.html}</span><small>${esc(identity)}</small>${priority}</div>${pointer}</div>`,
        iconSize:[62,54],iconAnchor:[31,27]
      });
    }
    if(currentWorkspace==='cool' && cool.score>=45){
      return L.divIcon({
        className:`marker-wrap marker-wrap-${esc(type)} cool-marker-wrap`,
        html:`<div class="motion-shell"><div class="cool-map-marker cool-${esc(cool.theme)}" title="${esc(cool.label)} — ${esc(v.name)}"><span>${esc(cool.icon)}</span><small>${esc(cool.label)}</small></div>${pointer}${speedTag}</div>`,
        iconSize:[58,52],iconAnchor:[29,26]
      });
    }
    const trainTheme=type==='train' ? trainLiveryTheme('',v.operator || v.operator_name || v.toc || v.owner) : null;
    const trainStyle=trainTheme && trainTheme.key!=='reference'
      ? ` style="background:${esc(trainTheme.primary)};border-color:${esc(trainTheme.accent)};color:${esc(trainTheme.ink)};box-shadow:0 4px 13px rgba(0,0,0,.38),0 0 0 3px ${esc(trainTheme.accent)}40"`
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
    const sharedNote=coincidentCount>1 ? ` • ${coincidentCount} trains share this reported point` : '';
    return L.divIcon({
      className: `marker-wrap marker-wrap-${esc(type)}${coincidentCount>1?' train-coincident-wrap':''}`,
      html: `<div class="motion-shell${type==='train'?' train-marker-shell':''}">${trainFan}<div class="marker-bubble marker-${esc(type)} marker-${esc(method)}${eventClass}"${trainStyle} title="${esc((v.position_label||'')+sharedNote)}"><span>${icons[type] || '•'}</span>${trainIndex}</div>${pointer}${speedTag}</div>`,
      iconSize: [42,42], iconAnchor: [21-offsetX,21-offsetY],
      tooltipAnchor: type==='train' ? [offsetX,offsetY-17] : [0,0]
    });
  }

  function placeMarkerIcon(p) {
    const icon = placeIcons[p.place_type] || '📍';
    const isBusStop=p.place_type==='bus_stop';
    const markerContent=isBusStop
      ? '<span class="bus-stop-sign" aria-hidden="true"><i>STOP</i><b></b></span>'
      : esc(icon);
    if(p.place_type==='rail_station'){
      return L.divIcon({
        className: 'place-marker-wrap station-marker-wrap',
        html: `<div class="place-marker place-rail_station station-marker" title="${esc(p.name)}"><i aria-hidden="true"></i></div><span class="station-label">${esc(p.name)}</span>`,
        iconSize:[16,16], iconAnchor:[8,8]
      });
    }
    return L.divIcon({
      className: 'place-marker-wrap',
      html: `<div class="place-marker place-${esc(p.place_type || 'other')}" title="${esc(p.name)}">${markerContent}</div>`,
      iconSize:isBusStop?[27,32]:[27,27], iconAnchor:isBusStop?[13,31]:[13,13]
    });
  }

  function cameraMarkerIcon(camera) {
    return L.divIcon({
      className:'camera-marker-wrap',
      html:`<div class="camera-map-marker${camera.available === false ? ' unavailable' : ''}" title="${esc(camera.name)}">📷</div>`,
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
        marker.bindTooltip(`<div class="activity-tooltip-card"><div class="tooltip-top"><strong>📷 ${esc(camera.name)}</strong><span class="tooltip-live">LIVE CAM</span></div><div class="tooltip-sub">${esc([camera.view,camera.source].filter(Boolean).join(' • '))}</div><div class="tooltip-hint">Click for the source-backed image or video</div></div>`,{direction:'top',offset:[0,-11],opacity:.97,className:'activity-tooltip'});
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
      if(force && !cameras.length)showToast(payload.note || 'No licensed live cameras are mapped in this view.');
    } catch {
      if(requestId!==cameraRequest)return;
      renderCameraMarkers();
      if(force)showToast('The live camera feed is unavailable right now. Existing map data is unaffected.');
    } finally {
      if(requestId===cameraRequest)$('live-webcams')?.removeAttribute('aria-busy');
    }
  }

  function selectCamera(camera, pan) {
    if (!camera) return;
    selectedId=null; selectedPlaceId=null; trackedActivityId=null; selectedDetailSignature=''; sendWatching(null); clearPlaceLink(); clearPhoto(); clearPlaceBoard(); clearActivityIntelligence(); clearCoverage(); clearDiscoveryDetail();
    syncDetailRouteControl(null);
    $('detail-panel')?.classList.add('has-selection'); $('detail-sidebar')?.classList.remove('is-empty');
    $('detail-title').textContent=`📷 ${camera.name || 'Live traffic camera'}`;
    $('detail-subtitle').textContent=[camera.view,camera.source].filter(Boolean).join(' • ') || 'Verified live camera';
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
        marker.bindTooltip(`<div class="activity-tooltip-card"><strong>${placeIcons[p.place_type]||'📍'} ${esc(p.name)}</strong><span>${esc(p.subtitle || p.source || 'Smart place')}</span><small>Click for live board / context</small></div>`,{direction:'top',offset:[0,-11],opacity:.97,className:'activity-tooltip'});
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

  function boardRow(row, kind) {
    const rawClock=row.actual || row.expected || row.clock || row.scheduled || '';
    const clock=rawClock && String(rawClock).includes('T') ? (timeText(rawClock)||rawClock) : rawClock;
    const baseStatus=row.status || (row.minutes!=null ? `${row.minutes} min` : '');
    const status=(kind==='bus' && row.minutes!=null && !String(baseStatus).toLowerCase().includes('live')) ? `${baseStatus} • ${row.minutes}m` : baseStatus;
    const place=kind==='arrival' ? (row.origin || row.operator || '') : (row.destination || row.operator || row.route || '');
    const platform=row.platform ? `Plat ${row.platform}` : '';
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
      status?`data-status="${esc(status)}"`:'',
      `data-activity-type="${kind==='bus'?'bus':'train'}"`,
    ].filter(Boolean).join(' ');
    const liveHint=live?`<em class="board-live-link">View live ${kind==='bus'?'bus':'train'} ›</em>`:(row.train_uid?'<em class="board-live-link">View route ›</em>':(kind!=='bus'&&row.headcode?'<em class="board-live-link">Find train ›</em>':''));
    return `<button type="button" class="board-row board-${kind}${serviceRef?' board-clickable':''}" ${attrs}><span class="board-time">${esc(clock || (row.minutes!=null?`${row.minutes}m`:'—'))}</span><span class="board-main"><strong>${esc(humanTransitLabel(place || row.route || row.name || 'Service'))}</strong><span>${esc([row.headcode,humanTransitLabel(row.route),humanTransitLabel(row.operator)].filter(Boolean).join(' • '))}${liveHint}</span></span><span class="board-status">${esc([status,platform].filter(Boolean).join(' • '))}</span></button>`;
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
    $('detail-title').textContent=`🚆 ${btn.dataset.headcode || 'Scheduled train'}`;
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

  function renderPlaceBoard(place, payload) {
    const box=$('detail-placeboard'); if(!box)return;
    box.classList.remove('rail-departure-board','bus-departure-board');
    const board=payload.board||{}; let html='';
    if(place.place_type==='rail_station') {
      box.classList.add('rail-departure-board');
      const deps=(board.departures||[]).map(r=>boardRow(r,'departure')).join('');
      const arrivalRows=(board.arrivals&&board.arrivals.length ? board.arrivals : (board.recent_arrivals||[]));
      const arr=arrivalRows.map(r=>boardRow(r,'arrival')).join('');
      const arrivalLabel=(board.arrivals&&board.arrivals.length)?'Next arrivals':'Recent actual arrivals';
      const waiting=board.mode==='schedule_sync'?'<p class="board-empty board-waiting">Timetable loading…</p>':'';
      html=`<div class="board-mast"><span>DEPARTURES & ARRIVALS</span><b>${esc(place.name)}</b><em>${esc(place.crs||'')}</em></div>${waiting}<div class="board-tabs"><div><strong>DEPARTURES</strong>${deps||'<p class="board-empty">No departures due.</p>'}</div><div><strong>${esc(arrivalLabel.toUpperCase())}</strong>${arr||'<p class="board-empty">No arrivals due.</p>'}</div></div>`;
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
      const targetBlock=targeted?`<div class="board-secondary"><strong>ALSO APPROACHING</strong>${targeted}</div>`:'';
      html=`<div class="board-mast"><span>BUS TIMES</span><b>${esc(place.name)}</b><em>LIVE</em></div><strong class="board-heading">${esc(mode)}</strong>${rows||'<p class="board-empty">No buses due.</p>'}${targetBlock}`;
    }
    box.classList.remove('hidden');
    const nextHtml=html||'<p class="board-empty">Nothing due right now.</p>';
    box.querySelector('[data-board-refresh-error]')?.remove();
    if(box.innerHTML!==nextHtml){
      const focused=document.activeElement?.closest('.board-row');
      const focusKey=focused&&box.contains(focused)?[focused.dataset.trainUid,focused.dataset.liveId,focused.dataset.headcode,focused.dataset.serviceDate,focused.className].join('|'):null;
      const panel=$('detail-panel'),scrollTop=panel?.scrollTop || 0;
      box.innerHTML=nextHtml;bindBoardLiveLinks(box);
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
    $('detail-title').textContent=`${placeIcons[p.place_type]||'📍'} ${p.name}`;
    $('detail-subtitle').textContent=p.subtitle || ({rail_station:'Rail station',bus_stop:'Bus stop',rain_gauge:'Rain gauge'}[p.place_type]||'Smart place');
    $('detail-fields').innerHTML=[field('Type',(p.place_type||'').replaceAll('_',' ')),field('Code',p.crs||p.atco_code||p.naptan_code),field('Operator',p.operator),field('Source',p.source),field('Latitude',Number(p.lat).toFixed(5)),field('Longitude',Number(p.lon).toFixed(5))].join('');
    $('follow').classList.add('hidden'); $('track-activity')?.classList.add('hidden'); $('share-activity')?.classList.add('hidden');
    const savePlace=$('save-place'); if(savePlace){savePlace.classList.remove('hidden');savePlace.textContent=savedPlaces.has(p.id)?'Saved ✓':'Save place';}
    clearCoverage(); clearDiscoveryDetail();
    for(const id2 of ['detail-insight','detail-photo','detail-related']){const el=$(id2);if(el){el.classList.add('hidden');el.innerHTML='';}}
    const box=$('detail-placeboard'); box.classList.remove('hidden');
    const cached=placeBoardCache.get(id);
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

  function layerTypeMatch(a, type) {
    if (type === 'cool-off') return false;
    if (type === 'all') return true;
    if (type === 'useful') return enabledActivityTypes.has(a.type);
    if (type === 'transport') return ['train','bus'].includes(a.type);
    if (type === 'cool') return a.type !== 'vessel' && coolInfo(a).score >= 45;
    if (type === 'cool-air') return a.type === 'aircraft' && !isRescueContextActivity(a) && (overheadActive || coolInfo(a).score >= 45);
    if (type === 'nearby-aircraft') return overheadActive && nearbyAircraftIds.has(a.id);
    if (type === 'cool-rail') return railSubtypeMatch(a) && coolInfo(a).score >= 45;
    if (type === 'cool-rescue') return isRescueContextActivity(a) && coolInfo(a).score >= 45;
    // Planned roadworks and unplanned incidents remain deliberately separate.
    return a.type === type;
  }

  function replaceActivities(nextActivities) {
    activities = Array.isArray(nextActivities) ? nextActivities : [];
    movementHistory.ingest(activities);
    activitiesById = new Map(activities.map(activity => [activity.id, activity]));
    if(selectedId&&activitiesById.has(selectedId))selectedActivitySnapshot=activitiesById.get(selectedId);
    activityRevision += 1;
    filteredCacheKey = '';
    updateOperatorFilterOptions();
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
      [...enabledActivityTypes].sort().join(','),
    ].join('|');
    if (cacheKey === filteredCacheKey) return filteredCacheRows;
    const source=currentWorkspace==='cool'&&globalCoolActivities.length ? globalCoolRows() : activities;
    filteredCacheRows = source.filter(a => {
      if (!layerTypeMatch(a, selectedType)) return false;
      if (!operatorMatches(a)) return false;
      if (!inNearby(a)) return false;
      if (!searchText) return true;
      return searchable(a).includes(searchText);
    });
    filteredCacheKey = cacheKey;
    return filteredCacheRows;
  }

  function markerPositionSignature(a) {
    return `${Number(a.lat).toFixed(6)}|${Number(a.lon).toFixed(6)}`;
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
        const radius=count===2?20:Math.min(44,Math.max(24,18/Math.sin(Math.PI/count)));
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
    return Math.max(0,Math.min(1,(serverNow-started)/(due-started)));
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

  function markerVisualSignature(a) {
    const zoomBand = map.getZoom() >= 12 ? 2 : map.getZoom() >= 9 ? 1 : 0;
    const appearance = currentWorkspace === 'cool' ? 'cool' : 'standard';
    const cool = appearance === 'cool' ? coolInfo(a) : null;
    const bearing = a.type === 'aircraft' || zoomBand >= 1 ? a.bearing : '';
    const speed = zoomBand >= 1 ? a.speed : '';
    const speedUnit = zoomBand >= 2 ? a.speed_unit : '';
    const movementPosition=['aircraft','sar'].includes(a.type)?`${a.lat}|${a.lon}`:'';
    return [a.type,a.position_method,a.name,a.aircraft_model,a.aircraft_type_code,bearing,movementPosition,speed,speedUnit,zoomBand,appearance,cool?.score,cool?.label,cool?.theme,cool?.icon,a._coincidentCount,a._coincidentSlot,a._coincidentX,a._coincidentY].join('|');
  }

  function markerTooltipSignature(a) {
    const appearance = currentWorkspace === 'cool' ? 'cool' : 'standard';
    return [appearance,a.name,a.type,a.kind,a.origin,a.destination,a.route,a.subtitle,a.position_label,a.speed,a.speed_unit,a.bearing,a.delay_minutes,a.next_location,a.status,a.severity,a.vehicle_ref,a.aircraft_model,a.last_updated,a.cool_score,a.cool_badge].join('|');
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

  function syncMarkerVisual(marker,activity,visualSignature,force=false) {
    if(!force&&marker.__visualSignature===visualSignature&&trainMarkerDomMatches(marker,activity))return false;
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

  function renderMap() {
    const startedAt = performance.now();
    let rows = filteredActivities();
    const routeTrain=$('detail-sidebar')?.classList.contains('route-view')?selectedTrainActivity():null;
    if(routeTrain){
      rows=rows.filter(row=>row.type!=='train'||row.id===selectedId);
      if(!rows.some(row=>row.id===routeTrain.id))rows=[...rows,routeTrain];
    }
    const now=Date.now();
    const presentations=rows.map(row=>activityMapPresentation(row,now));
    liveObjectRenderer?.setActivities(rows,selectedId);
    const coincidentLayouts=trainCoincidentLayouts(presentations);
    const shown = new Set(rows.map(a => a.id));
    let clusterDirty = false;
    for (const [id, marker] of markers.entries()) {
      if (!shown.has(id)||(liveObjectRenderer?.ready&&['train','aircraft','sar'].includes(marker.__activityType))) {
        if ((marker.__activityGroup || markerGroup) === markerGroup) clusterDirty = true;
        (marker.__activityGroup || markerGroup).removeLayer(marker);
        markers.delete(id);
      }
    }
    for (let index=0;index<rows.length;index+=1) {
      const a=rows[index];
      const displayActivity=withTrainCoincidentLayout(presentations[index],coincidentLayouts.get(a.id));
      if(liveObjectRenderer?.ready&&['train','aircraft','sar'].includes(a.type))continue;
      // Discover is a curated, deliberately small set. Never cluster those
      // markers: each find must remain individually visible at every zoom.
      const intendedGroup = currentWorkspace==='cool' || ['aircraft','sar'].includes(a.type) ? aerialMarkerGroup : markerGroup;
      let marker = markers.get(a.id);
      const positionSignature = markerPositionSignature(displayActivity);
      const visualSignature = markerVisualSignature(displayActivity);
      const tooltipSignature = markerTooltipSignature(displayActivity);
      if (!marker) {
        marker = L.marker([displayActivity.lat, displayActivity.lon], { icon: markerIcon(displayActivity), riseOnHover: true });
        marker.bindTooltip(tooltipHtml(displayActivity), { direction: 'top', offset: [0,-13], opacity: 0.97, className: 'activity-tooltip', sticky: true });
        marker.on('click', () => selectActivity(a.id, true));
        marker.__activityGroup = intendedGroup;
        marker.__activityType = a.type;
        marker.__positionSignature = positionSignature;
        marker.__visualSignature = visualSignature;
        marker.__tooltipSignature = tooltipSignature;
        intendedGroup.addLayer(marker);
        if(['train','aircraft','sar'].includes(a.type))marker.getElement?.()?.classList.add('live-object-proxy');
        markers.set(a.id, marker);
        if (intendedGroup === markerGroup) clusterDirty = true;
        diagnostics.markersCreated += 1;
      } else {
        if (marker.__activityGroup !== intendedGroup) {
          if (marker.__activityGroup === markerGroup || intendedGroup === markerGroup) clusterDirty = true;
          marker.__activityGroup?.removeLayer(marker);
          marker.__activityGroup = intendedGroup;
          intendedGroup.addLayer(marker);
        }
        if (marker.__positionSignature !== positionSignature) {
          marker.setLatLng([displayActivity.lat, displayActivity.lon]);
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
        if(['train','aircraft','sar'].includes(a.type))marker.getElement?.()?.classList.add('live-object-proxy');
      }
    }
    if (clusterDirty && typeof markerGroup.refreshClusters === 'function') markerGroup.refreshClusters();
    repairCoincidentTrainMarkerDom(presentations.map((row,index)=>withTrainCoincidentLayout(row,coincidentLayouts.get(rows[index]?.id))));
    requestAnimationFrame(()=>repairCoincidentTrainMarkerDom(
      presentations.map((row,index)=>withTrainCoincidentLayout(row,coincidentLayouts.get(rows[index]?.id)))
    ));
    renderHeatmap();
    diagnostics.lastRenderMs = Math.round((performance.now() - startedAt) * 10) / 10;
    $('map').dataset.activityCount = String(rows.length);
    $('map').dataset.lastRenderMs = String(diagnostics.lastRenderMs);
    if (selectedId && markers.has(selectedId) && focusMarker) {
      map.removeLayer(focusMarker); focusMarker=null;
    }
  }

  function refreshLiveMarkerPositions() {
    if(document.hidden||reduceLiveMotion?.matches)return;
    const now=Date.now();
    const rows=filteredActivities();
    const presentations=rows.map(row=>activityMapPresentation(row,now));
    const coincidentLayouts=trainCoincidentLayouts(presentations);
    for(let index=0;index<rows.length;index+=1){
      const a=rows[index];
      if(a.type!=='train'&&!(overheadActive&&['aircraft','sar'].includes(a.type)))continue;
      const display=withTrainCoincidentLayout(presentations[index],coincidentLayouts.get(a.id));
      if(selectedId===a.id&&a.type==='train')updateSelectedTrainRouteProgress(a,now);
      if(trackedActivityId===a.id)map.panTo([display.lat,display.lon],{animate:false});
      const marker=markers.get(a.id);if(!marker)continue;
      const signature=markerPositionSignature(display);
      const visualSignature=markerVisualSignature(display);
      if(signature!==marker.__positionSignature){
        marker.setLatLng([display.lat,display.lon]);
        marker.__positionSignature=signature;
        diagnostics.markerMoves+=1;
      }
      syncMarkerVisual(marker,display,visualSignature);
    }
  }

  function showFocusMarker(a) {
    if (focusMarker) { map.removeLayer(focusMarker); focusMarker=null; }
    if(liveObjectRenderer?.ready&&['train','aircraft','sar'].includes(a?.type))return;
    if (!a || markers.has(a.id)) return;
    focusMarker=L.marker([a.lat,a.lon],{icon:markerIcon(a),riseOnHover:true,zIndexOffset:1200}).addTo(map);
    focusMarker.bindTooltip(tooltipHtml(a),{direction:'top',offset:[0,-13],opacity:.97,className:'activity-tooltip'});
    focusMarker.on('click',()=>selectActivity(a.id,false));
    focusMarker.openTooltip();
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
    const busShown = Number(streamMeta.buses_streamed || 0);
    if (busTotal && ((currentWorkspace === 'useful' && enabledActivityTypes.has('bus')) || heatMode === 'bus')) {
      messages.push(zoom < busMin ? `🚌 ${fmt(busTotal)} live BODS buses loaded • zoom to ${busMin}+` : `🚌 ${fmt(busShown)} buses in view • ${fmt(busTotal)} active`);
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
      const right=coolMode?'LIVE':((a.tracking_count||0)>0?`${fmt(a.tracking_count)} 👁`:new Date(a.last_updated).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}));
      const sub=info.reasons[0] || (a.origin&&a.destination?`${a.origin} → ${a.destination}`:(a.subtitle||a.position_label));
      const lead=coolMode?coolBadgeHtml(a,true):`<span class="rank-icon">${icons[a.type]||'•'}</span>`;
      const proof=coolMode?`${discoveryIdentity(a).label} • updated ${discoveryFreshness(a)}`:'';
      button.innerHTML=`<span class="rank-number">${i+1}</span>${lead}<span class="rank-copy"><strong>${isNew?'<em class="discovery-new-tag">NEW</em> ':''}${esc(a.name)}${matchesInterest?' <em class="interest-match">MY INTEREST</em>':''}</strong><span>${esc(sub)}</span>${proof?`<small class="rank-proof">${esc(proof)}</small>`:''}</span><span class="rank-count">${esc(right)}</span>`;
      button.addEventListener('click',()=>selectActivity(a.id,true,coolMode?11:null)); list.appendChild(button);
    });
  }

  // Lateness the server works out for signalling-only trains, kept so a
  // live update does not blank the Status cell until the journey reloads.
  const railDelayById = new Map();

  function trainStatusLabel(a) {
    if (a?.restored_snapshot === true) return 'Last known';
    if (a?.terminated_at) return 'Arrived';
    const raw = Number.isFinite(Number(a?.delay_minutes)) && a?.delay_minutes !== null ? Number(a.delay_minutes) : railDelayById.get(a?.id);
    if (raw == null || !Number.isFinite(Number(raw))) return 'Live';
    const delay = Math.round(Number(raw));
    return delay >= 1 ? `${delay} min late` : delay <= -1 ? `${-delay} min early` : 'On time';
  }

  function field(label, value) {
    if (value === undefined || value === null || value === '') return '';
    const technical = new Set(['Source','ICAO / source ref','Latitude','Longitude','Train UID','Headcode','Journey ref','Tracking','Bearing','Code','Type','Registration','MMSI','Identified as','Identity confidence','Position','Operator']);
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

  function renderInsight(a) {
    const box = $('detail-insight');
    if(!box)return;
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
    if(a.type==='train'){box.classList.add('hidden');box.innerHTML='';return;}
    let info;
    if(a.type==='aircraft'){
      const classified=coolInfo(a);
      if(!classified.candidate || classified.score<45 || !classified.reasons.length){box.classList.add('hidden');box.innerHTML='';return;}
      info=classified;
    }else{
      info=currentWorkspace==='cool' ? coolInfo(a) : interestInfo(a);
    }
    if (info.score < (currentWorkspace==='cool'?45:10)) { box.classList.add('hidden'); box.innerHTML=''; return; }
    box.classList.remove('hidden');
    box.innerHTML = `<strong>${currentWorkspace==='cool'?'⚡ Why this is cool':'✨ Why this is interesting now'}</strong><span>${esc(info.reasons.join(' • ') || 'Current live activity')}</span>`;
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
    if (!box) return;
    box.classList.remove('hidden');
    const first=points[0], last=points[points.length-1];
    const distance=first&&last ? distanceKm(first.lat,first.lon,last.lat,last.lon) : 0;
    box.innerHTML = points.length > 1
      ? `<strong>LIVE TRAIL</strong><span>${points.length} observed positions • ${distance.toFixed(1)} km since this map started watching</span><small>Built from this browser’s live feed—not a complete historical flight path.</small>`
      : '<strong>LIVE TRAIL</strong><span>Recording from the next reported position…</span><small>This begins when the aircraft enters your live map stream.</small>';
  }

  function clearActivityIntelligence() {
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
    return tidyCapitals(String(value ?? '').replace(/_s(?=_|$)/gi,"'s").replace(/[_\s]+/g,' ').trim());
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
    const points=callingPoints.map((point,pointIndex)=>{
      const name=providerName(point?.station || point?.location || point?.name);
      const booked=providerClock(point?.scheduled || point?.expected || point?.actual || point?.time);
      const expected=point?.scheduled&&point?.expected ? providerClock(point.expected) : '';
      const lateClock=expected&&expected!==booked ? expected : '';
      const platform=providerName(point?.platform);
      const detail=platform ? `Platform ${platform}` : '';
      if(!name && !booked)return '';
      const state=['passed','current','next','upcoming'].includes(point?.state) ? point.state : '';
      // Between stations the train sits on the line before its next stop.
      const trainHere=state==='next'&&!atStation&&anyPassed
        ? '<li class="train-here" aria-label="Train is here"><i></i><span><small>Train is here</small></span><b></b></li>'
        : '';
      return `${trainHere}<li class="${state}"${state==='current'?' aria-label="Train is at this station"':''}><i></i><span><strong>${esc(name || `Call ${pointIndex+1}`)}</strong>${detail?`<small>${esc(detail)}</small>`:''}</span><b>${esc(booked)}${lateClock?`<em class="stop-late">${esc(lateClock)}</em>`:''}</b></li>`;
    }).filter(Boolean).join('');
    // Only facts a traveller uses: lateness, the stops and, for special
    // workings, what kind of service it is. The route is already the panel
    // subtitle; data-source and inference notes are not shown.
    const facts=points ? [] : [next?`Next: ${next}`:null].filter(Boolean);
    const unknown=!points ? '<p class="transport-empty">Stops not published for this service.</p>' : '';
    const operator=providerName(train.operator || run?.operator || run?.operator_code);
    const operatorTheme=trainLiveryTheme('',operator);
    const operatorStyle=operatorTheme.key!=='reference' ? ` style="--train-primary:${operatorTheme.primary};--train-secondary:${operatorTheme.secondary};--train-accent:${operatorTheme.accent};--train-ink:${operatorTheme.ink}"` : '';
    return `<article class="transport-run-card${operatorStyle?' operator-themed':''}" data-livery-theme="${esc(operatorTheme.key)}"${operatorStyle}>${serviceLabel?`<small class="transport-service-label">${esc(serviceLabel)}</small>`:''}${facts.length?`<div class="transport-facts">${facts.map(value=>`<span>${esc(value)}</span>`).join('')}</div>`:''}${points?`<ol class="transport-stops train-full-route${hasProgress?' has-progress':''}">${points}</ol>`:''}${unknown}</article>`;
  }

  function trainLiveryTheme(livery, operator) {
    const text=`${livery || ''} ${operator || ''}`.toUpperCase();
    const themes=[
      [/INTERCITY|INTER-CITY|SWALLOW/, ['#25272b','#f3ead7','#d7352a','#ffffff','intercity']],
      [/GREAT WESTERN|\bGWR\b/, ['#0b3b2e','#d7c7a0','#b99b5e','#ffffff','gwr']],
      [/LNER|EAST COAST/, ['#b5122b','#f4eee5','#121820','#ffffff','lner']],
      [/AVANTI/, ['#17233f','#7ac943','#ef8354','#ffffff','avanti']],
      [/CALEDONIAN|SLEEPER/, ['#18253f','#c5a45a','#f5f0e4','#ffffff','caledonian']],
      [/SCOTRAIL/, ['#163b73','#f3f7fb','#56a0d3','#ffffff','scotrail']],
      [/NORTHERN/, ['#26205d','#00a7b5','#e85d9e','#ffffff','northern']],
      [/TRANSPENNINE|TPE/, ['#273264','#68c5d1','#eb4b80','#ffffff','transpennine']],
      [/CROSSCOUNTRY/, ['#5a214f','#e6a83e','#d5d8dc','#ffffff','crosscountry']],
      [/SOUTHEASTERN/, ['#243b7b','#63b645','#f2c84b','#ffffff','southeastern']],
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

  function renderRailIntelligence(detail) {
    const runs=Array.isArray(detail?.runs) ? detail.runs : [];
    const terms=detail?.terms_url ? safeUrl(detail.terms_url) : null;
    const cards=runs.slice(0,3).map(renderRailRun).join('');
    const message='This service has no published stops.';
    const stock=detail?.rolling_stock;
    const stockUrl=stock?.source_url ? safeUrl(stock.source_url) : null;
    const livery=stock?.matched ? trainLiveryStyle(stock) : null;
    const stockTitle=stock?.matched ? [stock.train_class?`Class ${stock.train_class}`:null,stock.number,stock.name].filter(Boolean).join(' · ') : '';
    const stockFacts=stock?.matched ? [
      ['Operator',stock.operator],['Owner',stock.owner],['Formation',stock.formation],
      ['Depot',stock.depot],['Built',stock.year_built],['Works no.',stock.works_number],
      ['Pool',stock.pool_code],['Preserved at',stock.preserved_at],['Stored at',stock.stored_at],
    ].filter(([,value])=>value) : [];
    const stockCard=stock?.matched ? `<article class="rolling-stock-card" data-livery-theme="${esc(livery.key)}" style="${livery.css}"><div class="rolling-stock-stripe" aria-hidden="true"><i></i><i></i><i></i></div><small>Rolling stock</small><strong>${esc(stockTitle)}</strong>${stock.livery?`<b class="rolling-stock-livery">${esc(stock.livery)}</b>`:''}${stockFacts.length?`<dl>${stockFacts.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`:''}${stockUrl?`<a target="_blank" rel="noopener" href="${esc(stockUrl)}">${esc(stock.source || 'reference source')} ↗</a>`:''}</article>` : '';
    const liveState=detail?.live_marker===false?'<p class="transport-note">Timetable only · not live on the map</p>':'';
    return `<div class="rail-intelligence-shell${stock?.matched?' has-rolling-stock':''}"${livery?` style="${livery.css}"`:''}>${liveState}${cards || `<div class="transport-unavailable"><strong>Stops not published</strong><span>${esc(message)}</span></div>`}${stockCard}${terms?`<p class="transport-source"><a target="_blank" rel="noopener" href="${esc(terms)}">Data terms ↗</a></p>`:''}</div>`;
  }

  function renderBusIntelligence(detail) {
    const stops=Array.isArray(detail?.stops) ? detail.stops : [];
    const prediction=detail?.live_prediction && typeof detail.live_prediction==='object' ? detail.live_prediction : null;
    const liveTime=providerClock(prediction?.expected || prediction?.predicted || prediction?.time);
    const delay=prediction?.delay_minutes == null ? '' : (Number(prediction.delay_minutes)===0?'On time':`${Number(prediction.delay_minutes)>0?'+':''}${prediction.delay_minutes} min`);
    const route=[detail?.route,detail?.destination?`to ${detail.destination}`:null].filter(Boolean).join(' ');
    const facts=[detail?.operator,detail?.route_long_name,detail?.match_method==='route_time_position'?'Matched by route, time & position':null].filter(Boolean);
    const rows=stops.map((stop,index)=>{
      const state=['passed','next','upcoming'].includes(stop?.state) ? stop.state : 'upcoming';
      const scheduled=providerClock(stop?.clock || stop?.scheduled);
      const isNext=state==='next';
      const displayed=isNext&&liveTime ? liveTime : scheduled;
      const meta=isNext&&liveTime ? ['LIVE PREDICTION',delay].filter(Boolean).join(' • ') : (scheduled?'TIMETABLE':'STOP ORDER');
      return `<li class="${state}"><i></i><span><strong>${esc(stop?.name || `Stop ${index+1}`)}</strong><small>${esc(meta)}</small></span><b>${esc(displayed || '—')}</b></li>`;
    }).join('');
    const heading=humanTransitLabel(route || (detail?.mode==='matched_timetable'?'Matched official trip':'Route detail'));
    return `<small>LIVE BUS JOURNEY</small>${heading?`<div class="transport-route-head"><strong>${esc(heading)}</strong>${facts.length?`<span>${esc(facts.map(humanTransitLabel).join(' • '))}</span>`:''}</div>`:''}${rows?`<ol class="transport-stops bus-stops">${rows}</ol>`:`<div class="transport-unavailable"><strong>${detail?.mode==='timetable_sync'?'Timetable preparing':'Next stop unavailable'}</strong><span>${esc(detail?.note || 'The operator has not linked this live bus to a timetable trip, so its next stop cannot be identified reliably.')}</span></div>`}<p class="transport-source">${esc(detail?.source || 'DfT BODS')}</p>${rows&&detail?.note?`<p class="transport-note">${esc(detail.note)}</p>`:''}`;
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
    if(!force){box.classList.remove('hidden');box.innerHTML=`<small>${a.type==='train'?'LIVE TRAIN JOURNEY':'LIVE BUS JOURNEY'}</small><p class="coverage-loading">Loading route…</p>`;}
    try {
      const response=await fetch(`/api/activity/${encodeURIComponent(a.id)}/${a.type==='train'?'rail':'bus'}`,{cache:'no-store',signal:controller.signal});
      const payload=await response.json().catch(()=>({}));
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
          const cell=document.querySelector('#detail-fields [data-field="Status"] strong');
          if(cell)cell.textContent=trainStatusLabel(activityById(a.id)||a);
        }
      }
      const html=a.type==='train' ? renderRailIntelligence(detail) : renderBusIntelligence(detail);
      if(box.innerHTML!==html){const panel=$('detail-panel'),scrollTop=panel?.scrollTop||0;box.innerHTML=html;if(panel)panel.scrollTop=scrollTop;}
    } catch {
      if(requestId!==intelligenceRequest || selectedId!==a.id)return;
      if(!force)box.innerHTML=`<small>${a.type==='train'?'LIVE TRAIN JOURNEY':'LIVE BUS JOURNEY'}</small><div class="transport-unavailable"><strong>Route details not available yet</strong><span>Live position shown on the map.</span></div>`;
    } finally {
      if(requestId===intelligenceRequest){
        intelligenceController=null;
        if(keepRefreshing&&selectedId===a.id)intelligenceRefreshTimer=setTimeout(()=>{const current=activityById(a.id);if(current)loadActivityIntelligence(current,true);},a.type==='train'?10000:20000);
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
    $('detail-title').innerHTML = `${currentWorkspace==='cool' && detailCool.score>=45 ? coolBadgeHtml(a,true)+' ' : ''}${esc(a.name)}`;
    $('detail-subtitle').textContent = humanTransitLabel(a.kind === 'event' ? (a.subtitle || a.category || a.position_label) : (a.origin && a.destination ? `${a.origin} → ${a.destination}` : (a.subtitle || a.route || a.position_label)));
    renderOperatorIdentity(a);
    renderInsight(a);
    renderDiscoveryDetail(a);
    if (selectionChanged) { clearPhoto(); clearActivityIntelligence(); }
    if (['sar','aircraft'].includes(a.type)) loadAircraftInfo(a);
    else if ((a.type === 'train' && detailCool.candidate) || a.type === 'vessel' || (a.type === 'bus' && a.vehicle_ref)) loadPhoto(a);
    else if (['road','incident'].includes(a.type)) loadNearestCamera(a);
    renderRelated(a);
    loadEnvironmentContext(a);
    loadActivityIntelligence(a);
    if (a.kind === 'event') {
      $('detail-fields').innerHTML = [field('Category', a.category), field('Status', a.status), field('Severity', a.severity), field('Road', a.road), field('Direction', a.direction), field('Lanes', a.lanes_closed), field('Reason', a.reason), field('Started', timeText(a.start_time)), field('Ends', timeText(a.end_time)), field('Location', a.position_label), field('Source', a.source), field('Updated', timeText(a.last_updated))].join('');
      $('follow').classList.remove('hidden');
      $('follow').textContent = followed.has(id) ? 'Saved ✓' : 'Save';
      loadCoverage(a);
    } else {
      const speed = a.speed == null ? null : `${a.speed} ${a.speed_unit || ''}`.trim();
      const delay = a.delay_minutes == null ? null : (a.delay_minutes === 0 ? 'On time' : `${a.delay_minutes > 0 ? '+' : ''}${a.delay_minutes} min`);
      const bearing = a.bearing == null ? null : `${Math.round(a.bearing)}°`;
      const identityLabel = a.type === 'vessel' ? 'MMSI' : (['sar','aircraft'].includes(a.type) ? 'Registration' : 'Vehicle');
      const altitude = a.altitude_ft == null ? null : `${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} ft`;
      const isAircraft=['sar','aircraft'].includes(a.type);
      const aircraftType=isAircraft ? (a.aircraft_model || a.aircraft_type_code || 'Type not identified by live feed') : null;
      const publishedRoute=a.route || (a.origin&&a.destination?`${a.origin} → ${a.destination}`:null);
      if (a.type === 'train') {
        // A traveller's three facts, as in the agreed design. Identifiers and
        // positions stay available behind "More details".
        const age = relativeAge(Math.max(0,(Date.now()-new Date(a.last_updated||0).getTime())/1000));
        $('detail-fields').innerHTML = [
          field('Status', trainStatusLabel(a)), field('Next', humanTransitLabel(a.next_location)), field('Updated', age),
          field('Headcode', a.headcode), field('Train UID', a.train_uid), field('Position', a.position_label),
          field('Source', a.source), field('Latitude', Number.isFinite(Number(a.lat)) ? Number(a.lat).toFixed(5) : null),
          field('Longitude', Number.isFinite(Number(a.lon)) ? Number(a.lon).toFixed(5) : null),
        ].join('');
      } else
      $('detail-fields').innerHTML = [field('Route', publishedRoute || (isAircraft?'Not published by this ADS-B feed':null)), field(identityLabel, a.vehicle_ref), field('Identified as', a.identity_label), field('Identity confidence', a.identity_confidence==='type'?'Type identified':(a.identity_confidence==='exact'?'Exact live identifier':null)), field('Aircraft type', aircraftType), field('ICAO type code', a.aircraft_type_code), field('Altitude', altitude), field('Headcode', a.headcode), field('Train UID', a.train_uid), field('Journey ref', a.journey_ref), field('Status', a.status), field('Position', a.position_label), field('Next report', a.next_location), field('Speed', speed), field('Bearing', bearing), field('Delay', delay), field('Occupancy', a.occupancy), field('Operator', a.operator), field('Source', a.source), field('ICAO / source ref', a.source_id), field('Tracking', fmt(a.tracking_count)), field('Updated', timeText(a.last_updated)), field('Latitude', Number.isFinite(Number(a.lat)) ? Number(a.lat).toFixed(5) : null), field('Longitude', Number.isFinite(Number(a.lon)) ? Number(a.lon).toFixed(5) : null)].join('');
      $('follow').classList.remove('hidden');
      $('follow').textContent = followed.has(id) ? 'Saved ✓' : 'Save';
      clearCoverage();
    }
    renderTrackingControl(a);
    renderSelectedTrail(a);
    if (pan && focusZoom != null) map.setView([a.lat, a.lon], Math.max(map.getZoom(), focusZoom), {animate:true});
    else if (pan || trackedActivityId === id) map.panTo([a.lat, a.lon], { animate: true });
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
        showToast(error?.message || 'This live item has ended.');
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
    network_rail:{label:'TRUST movements',icon:'🚆',group:'RAIL ENGINE'}, td:{label:'TD signalling',icon:'🚦',group:'RAIL ENGINE'}, vstp:{label:'VSTP schedules',icon:'🗓️',group:'RAIL ENGINE'}, schedule:{label:'Full SCHEDULE',icon:'📘',group:'RAIL ENGINE'}, rail_reference:{label:'Rail references',icon:'🧭',group:'RAIL ENGINE'},
    bods:{label:'BODS bus GPS',icon:'🚌',group:'MOVING VEHICLES'}, bods_timetable:{label:'BODS published bus timetable',icon:'🕒',group:'SMART PLACES'}, bods_sx:{label:'Bus disruptions',icon:'🚏',group:'INCIDENTS & WARNINGS'}, adsb_sar:{label:'SAR / special aircraft',icon:'🚁',group:'RESCUE & RESPONSE'}, rnli:{label:'RNLI launches',icon:'🛟',group:'RESCUE & RESPONSE'},
    national_highways:{label:'National Highways roadworks + incidents',icon:'🚧',group:'ROAD INTELLIGENCE'}, national_highways_vms:{label:'Live motorway signs',icon:'▣',group:'ROAD INTELLIGENCE'}, national_highways_speed:{label:'Managed speed limits',icon:'40',group:'ROAD INTELLIGENCE'}, national_highways_features:{label:'Roadside network features',icon:'☎',group:'ROAD INTELLIGENCE'}, traffic_flow:{label:'Live traffic flow',icon:'🚦',group:'ROAD INTELLIGENCE'},
    ea_floods:{label:'EA flood warnings',icon:'🌊',group:'INCIDENTS & WARNINGS'}, met_office:{label:'Weather warnings',icon:'🌦️',group:'INCIDENTS & WARNINGS'}, london_fire:{label:'London Fire incidents',icon:'🔥',group:'RESCUE & RESPONSE'},
    bgs_earthquakes:{label:'BGS earthquakes',icon:'🌐',group:'EARTH & ENVIRONMENT'}, ea_levels:{label:'Tide gauges',icon:'🌊',group:'EARTH & ENVIRONMENT'}, wavenet:{label:'WaveNet buoys',icon:'🌊',group:'EARTH & ENVIRONMENT'}, uk_air:{label:'DEFRA air quality',icon:'💨',group:'EARTH & ENVIRONMENT'},
    carbon_intensity:{label:'Grid carbon intensity',icon:'⚡',group:'ENERGY & SPACE'}, power_outages:{label:'Power outages',icon:'🔌',group:'ENERGY & SPACE'}, space_weather:{label:'Aurora / space weather',icon:'🌌',group:'ENERGY & SPACE'}, iss:{label:'ISS tracker',icon:'🛰️',group:'ENERGY & SPACE'},
    tfl_live:{label:'TfL cycle hire',icon:'🚲',group:'LOCAL MOBILITY'}, gbfs:{label:'GBFS shared mobility',icon:'🛴',group:'LOCAL MOBILITY'}, gritters:{label:'Traffic Scotland gritters',icon:'❄️',group:'LOCAL MOBILITY'}, smart_places:{label:'Clickable stations / stops / terminals',icon:'📍',group:'SMART PLACES'}, demo:{label:'Demo',icon:'🧪',group:'OTHER'}
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
    for (const s of visibleSources) { const meta = sourceMeta[s.name] || {label:(s.name || 'Feed'),icon:'•',group:'OTHER'}; if (!groups.has(meta.group)) groups.set(meta.group, []); groups.get(meta.group).push({s,meta}); }
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
        html.push(`<div class="feed-row feed-${esc(s.name || 'other')}${s.health === 'stale' ? ' is-stale' : ''}"><span class="feed-icon" aria-hidden="true">${meta.icon}</span><div class="feed-copy"><strong><span class="feed-name">${esc(meta.label)}</span><i class="status-led ${cls}"></i><small class="feed-mode">${esc(s.health || s.mode)}</small></strong><p>${esc(s.message)}</p>${freshness}${setup}</div></div>`);
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
    note.textContent = `Nearby mode • ${nearbyState.radiusMiles} miles around ${nearbyState.source === 'device' ? 'your location' : 'map centre'} • ${filteredActivities().length} live matches`;
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

  async function refreshOverheadAircraft({announce=false}={}) {
    if(!overheadActive||nearbyState.lat==null||nearbyState.lon==null)return;
    const requestId=++overheadRefreshRequest;
    try{
      const response=await fetch(`/api/aircraft/nearby?lat=${encodeURIComponent(nearbyState.lat)}&lon=${encodeURIComponent(nearbyState.lon)}&radius_miles=50`,{cache:'no-store'});
      const payload=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(payload.detail||'aircraft lookup unavailable');
      if(requestId!==overheadRefreshRequest||!overheadActive)return;
      nearbyAircraftRows=Array.isArray(payload.activities)?payload.activities:[];
      nearbyAircraftIds=new Set(nearbyAircraftRows.map(row=>row.id));
      replaceActivities([...new Map([...activities,...nearbyAircraftRows].map(row=>[row.id,row])).values()]);
      renderMap();renderStats();renderRanking();renderCoolSummary();
      const checked=Number(payload.checked||0);
      const note=$('overhead-now')?.querySelector('small');
      if(note)note.textContent=`${nearbyAircraftRows.length} live aircraft • refreshing automatically`;
      if(announce)showToast(`Showing ${nearbyAircraftRows.length} positioned aircraft${checked?` from ${checked} feed records`:''} within 50 miles.`);
    }catch(error){
      if(announce)showToast('Nearby aircraft could not refresh; showing the latest retained positions.');
    }finally{
      if(requestId===overheadRefreshRequest&&overheadActive){
        clearTimeout(overheadRefreshTimer);
        overheadRefreshTimer=setTimeout(()=>refreshOverheadAircraft(),8000);
      }
    }
  }

  function clearTransientLocationModes({clearNearestStop=true}={}) {
    stopOverheadRefresh();
    overheadActive=false;
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
    const choices=[['air','▲','Air'],['rail','◆','Rail'],['sea','⚓','Sea']];
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
      return `<article class="field-guide-card${saved?' discovered':' locked'}"><div class="field-guide-card-head"><span class="field-guide-icon ${esc(item.category)}">${esc(item.icon)}</span><div><small>${esc(item.category.toUpperCase())}</small><strong>${esc(item.title)}</strong></div><b>${saved?'FOUND':'TO FIND'}</b></div><p>${esc(item.explanation)}</p>${saved?`<div class="field-guide-proof"><span>${esc(identity.label)}</span>${recordedThisMissionWeek(saved)?'<span>Logged this week</span>':''}${saved.broad_region?`<span>${esc(saved.broad_region)}</span>`:''}${when?`<span>First found ${esc(when)}</span>`:''}</div>${identity.note?`<small class="field-guide-truth">${esc(identity.note)}</small>`:''}`:`<small class="field-guide-hint">Find a live, source-classified example on the map to add it.</small>`}${item.safety_note?`<small class="field-guide-safety">${esc(item.safety_note)}</small>`:''}</article>`;
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
      return `<article class="mission-card${state.complete?' complete':''}"><div class="mission-card-head"><span>${esc(mission.icon||'◇')}</span><div><small>${state.complete?'COMPLETE':'MAP MISSION'}</small><strong>${esc(mission.title||'Weekly mission')}</strong></div><b>${state.count}/${state.total}</b></div><p>${esc(mission.description||'Discover live activity on the map.')}</p><div class="mission-meter"><i style="width:${percent}%"></i></div>${end?`<small class="mission-ends">Available until ${esc(end)}</small>`:''}<small class="mission-safety">${esc(mission.safety_note||'Map-only. Observe safely from lawful public places.')}</small></article>`;
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
      save.classList.remove('hidden');share.classList.remove('hidden');
      save.disabled=discoveryClaimPending;save.textContent=record?'Field Guide ✓':'Save to Field Guide';
      return;
    }
    const plainIdentity=identity.label;
    box.innerHTML=`<div class="discovery-truth-row"><b class="identity-pill ${identity.level}">${esc(plainIdentity)}</b><span>Updated ${esc(freshness)}</span></div>${identity.level==='type'?`<small>${esc(identity.note)}</small>`:''}`;
    save.classList.remove('hidden');share.classList.remove('hidden');
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
    if(!activityIsCollectible(a)){showToast('Rescue and emergency activity stays live context only. It is never collectible.');return;}
    const existing=collectedRecordForActivity(a);
    if(existing){setDiscoveryView('guide');showToast('Already saved in your Field Guide.');return;}
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
      trackProduct('discovery_claimed',discoveryCategoryForActivity(a)||'other');showToast(`${collectibleForActivity(a)?.title||a.name} added to your private Field Guide.`);
    } catch(error) {showToast(error?.message||'Discovery could not be saved right now.');}
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
  function saveFollowed() { try { localStorage.setItem('ukRadarFollowed', JSON.stringify([...followed])); } catch {} }

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
    const options=[['train','🚆','Trains'],['bus','🚌','Buses'],...(maritimeAvailable?[['vessel','🚢','Boats & ships']]:[]),['aircraft','▲','Special aircraft'],['incident','⚠','Incidents'],['weather','🌦','Weather'],['flood','🌊','Floods']];
    badge.textContent=options.filter(([type])=>myRadarInterests.has(type)).length;
    box.innerHTML=options.map(([type,icon,label])=>`<button type="button" data-radar-interest="${type}" class="${myRadarInterests.has(type)?'active':''}" aria-pressed="${myRadarInterests.has(type)}"><b>${icon}</b><span>${label}</span></button>`).join('');
    box.querySelectorAll('[data-radar-interest]').forEach(button=>button.addEventListener('click',()=>{
      const type=button.dataset.radarInterest;
      if(!myRadarInterests.has(type)&&!billingState.plus&&myRadarInterests.size>=3){openPlusDialog();showToast('Free My Radar includes three interests. Radar+ unlocks more.');return;}
      if(myRadarInterests.has(type))myRadarInterests.delete(type);else myRadarInterests.add(type);
      saveMyRadarPreferences();renderMyRadar();trackProduct('my_radar_interest_changed',type);
    }));
  }

  function renderSavedPlaces() {
    const box=$('saved-places-list'),badge=$('saved-places-count');if(!box||!badge)return;
    badge.textContent=savedPlaces.size;
    const rows=[...savedPlaces.values()];
    renderCommuteAlertPlaces();
    if(!rows.length){box.innerHTML='<p class="my-radar-empty">No places saved yet. Add a station or bus stop to get live times and alerts.</p><button id="my-radar-add-place" class="primary-button my-radar-add-place" type="button">＋ Add a station or bus stop</button>';$('my-radar-add-place')?.addEventListener('click',startAddingMyRadarPlace);return;}
    box.innerHTML=`${rows.map(p=>`<div class="saved-place-row"><button type="button" data-saved-place="${esc(p.id)}"><b>${placeIcons[p.place_type]||'📍'}</b><span><strong>${esc(p.name)}</strong><small>${esc((p.place_type||'place').replaceAll('_',' '))}</small></span></button><button type="button" data-remove-place="${esc(p.id)}" aria-label="Remove ${esc(p.name)}">×</button></div>`).join('')}<button id="my-radar-add-place" class="ghost-button my-radar-add-place" type="button">＋ Add another place</button>`;
    box.querySelectorAll('[data-saved-place]').forEach(button=>button.addEventListener('click',async()=>{
      const row=savedPlaces.get(button.dataset.savedPlace);if(!row)return;
      if(!places.some(p=>p.id===row.id))places.push(row);renderPlaces();await selectPlace(row.id,true);if(map.getZoom()<11)map.setZoom(11);
    }));
    box.querySelectorAll('[data-remove-place]').forEach(button=>button.addEventListener('click',()=>{savedPlaces.delete(button.dataset.removePlace);saveMyRadarPreferences();renderMyRadar();}));
    $('my-radar-add-place')?.addEventListener('click',startAddingMyRadarPlace);
  }

  async function startAddingMyRadarPlace() {
    placeMode='all';if($('place-mode'))$('place-mode').value='all';
    if(map.getZoom()<13)map.setZoom(13);
    await fetchPlaces();updateLayerToggleUi();
    showToast('Stations and bus stops are on. Tap one on the map, then choose Save place.');
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

  function renderCommuteAlertPlaces() {
    const select=$('commute-alert-place');if(!select)return;
    const current=select.value||commuteAlertStatus.preferences?.place_id||'';
    const rows=[...savedPlaces.values()];
    select.innerHTML=rows.length?rows.map(place=>`<option value="${esc(place.id)}">${esc(place.name)}</option>`).join(''):'<option value="">Save a place first</option>';
    if(rows.some(place=>place.id===current))select.value=current;
    renderPassingNextPlaces();
  }

  function renderPassingNextPlaces() {
    const select=$('passing-next-place');if(!select)return;
    const current=select.value;
    const rows=[...savedPlaces.values()].filter(place=>['rail_station','bus_stop'].includes(place.place_type));
    select.innerHTML=rows.length?rows.map(place=>`<option value="${esc(place.id)}">${esc(place.name)}</option>`).join(''):'<option value="">Save a station or stop first</option>';
    if(rows.some(place=>place.id===current))select.value=current;
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
      box.innerHTML=rows?`<div class="passing-next-label"><span>${label}</span><b>${esc(place.name)}</b></div>${rows}`:'<p class="my-radar-empty">Nothing due right now.</p>';
      bindBoardLiveLinks(box);
    } catch { box.innerHTML='<p class="my-radar-empty">Live times are unavailable right now.</p>'; }
  }

  function renderCommuteAlertStatus() {
    const state=$('commute-alert-state'),note=$('commute-alert-note'),enable=$('commute-alert-enable'),disable=$('commute-alert-disable');
    if(!state||!note||!enable||!disable)return;
    const active=Boolean(commuteAlertStatus.subscribed);
    state.textContent=active?'ON':'OFF';state.classList.toggle('active',active);
    enable.textContent=active?'Update alerts':'Turn alerts on';
    disable.classList.toggle('hidden',!active);
    if(!savedPlaces.size)note.textContent='Save a station or bus stop to begin.';
    else if(!commuteAlertStatus.configured)note.textContent='Alerts are not available yet.';
    else if(active){const p=commuteAlertStatus.preferences||{};note.textContent=`Active for ${p.mode||'travel'} near ${p.place_name||'your saved place'} • ${p.start_hour}:00–${p.end_hour}:00.`;}
    else note.textContent='Ready when you are. Your browser will ask before notifications are enabled.';
    renderCommuteAlertPlaces();
    const p=commuteAlertStatus.preferences;if(p){if($('commute-alert-mode'))$('commute-alert-mode').value=p.mode||'driving';if($('commute-alert-radius'))$('commute-alert-radius').value=String(p.radius_miles||12);if($('commute-alert-hours'))$('commute-alert-hours').value=`${p.start_hour}:${p.end_hour}`;}
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
    if(!place){showToast('Save a station, stop or place first, then choose it here.');return;}
    if(!commuteAlertStatus.configured){showToast('Alerts are not available yet.');return;}
    if(!('serviceWorker' in navigator)||!('Notification' in window)||!('PushManager' in window)){showToast('This browser does not support travel alerts.');return;}
    try {
      const permission=await Notification.requestPermission();if(permission!=='granted')throw new Error('Notification permission was not granted.');
      const registration=await navigator.serviceWorker.ready;
      let subscription=await registration.pushManager.getSubscription();
      if(!subscription)subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:vapidKeyBytes(commuteAlertStatus.public_key)});
      const [startHour,endHour]=String($('commute-alert-hours')?.value||'5:11').split(':').map(Number);
      const preferences={mode:$('commute-alert-mode')?.value||'driving',place_id:place.id,place_name:place.name,lat:Number(place.lat),lon:Number(place.lon),radius_miles:Number($('commute-alert-radius')?.value||12),start_hour:startHour,end_hour:endHour,activity_types:[...myRadarInterests].filter(type=>['train','bus','vessel','aircraft'].includes(type))};
      const response=await fetch('/api/alerts/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_token:alertDeviceToken(),subscription:subscription.toJSON(),preferences})});
      const payload=await response.json().catch(()=>({}));if(!response.ok)throw new Error(payload.detail||'Alerts could not be enabled.');
      commuteAlertStatus={...commuteAlertStatus,subscribed:true,preferences};renderCommuteAlertStatus();showToast('Travel alerts are active on this device.');trackProduct('commute_alert_enabled',preferences.mode);
    } catch(error){showToast(error?.message||'Travel alerts could not be enabled.');}
  }

  async function disableCommuteAlerts() {
    try {
      await fetch('/api/alerts/unsubscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_token:alertDeviceToken()})});
      const registration=await navigator.serviceWorker.ready;const subscription=await registration.pushManager.getSubscription();await subscription?.unsubscribe();
    } catch {}
    commuteAlertStatus={...commuteAlertStatus,subscribed:false,preferences:null};renderCommuteAlertStatus();showToast('Travel alerts are off on this device.');trackProduct('commute_alert_disabled','alerts');
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
    box.innerHTML=`<div class="my-area-counts"><strong>${matching.length}</strong><span>live near ${anchors.length} saved area${anchors.length===1?'':'s'}</span><b>${interesting.length} relevant</b></div>${top.length?top.map(a=>`<button type="button" data-area-activity="${esc(a.id)}"><span>${icons[a.type]||'•'}</span><div><strong>${esc(a.name)}</strong><small>${esc(a.cool_explanation||a.status||a.subtitle||'Live')}</small></div><b>›</b></button>`).join(''):'<p class="my-radar-empty">Nothing matching your interests is live nearby right now.</p>'}`;
    box.querySelectorAll('[data-area-activity]').forEach(button=>button.addEventListener('click',()=>selectActivity(button.dataset.areaActivity,true,11)));
  }

  function renderMyRadar() {
    const box = $('my-radar-list');
    const badge = $('my-radar-count');
    if (!box || !badge) return;
    renderMyRadarInterests();renderSavedPlaces();renderMyRadarArea();
    badge.textContent = followed.size;
    const live = [...followed].map(activityById).filter(Boolean).sort((a,b)=>new Date(b.last_updated)-new Date(a.last_updated));
    if (!followed.size) { box.innerHTML = '<p class="my-radar-empty">Nothing followed yet. Select a train, incident, vessel or other live activity and tap Follow.</p>'; return; }
    if (!live.length) { box.innerHTML = `<p class="my-radar-empty">${followed.size} saved favourite${followed.size===1?'':'s'} • none are in the current live snapshot.</p>`; return; }
    box.innerHTML = live.slice(0,8).map(a => `<button type="button" class="my-radar-row" data-my-radar="${esc(a.id)}"><b>${icons[a.type] || '•'}</b><span><strong>${esc(a.name)}</strong><span>${esc(a.status || a.subtitle || 'Live')}</span></span><em>${(a.tracking_count||0) ? `${fmt(a.tracking_count)} watching` : 'LIVE'}</em></button>`).join('');
    box.querySelectorAll('[data-my-radar]').forEach(btn=>btn.addEventListener('click',()=>selectActivity(btn.dataset.myRadar,true)));
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
    window.open(url,'_blank','noopener');showToast('The live page has been opened in a new tab.');return true;
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
    } catch(error){if(error?.name!=='AbortError')showToast(error?.message||'Sharing is unavailable right now.');}
  }

  function todayRow(row,label) {
    if(!row)return '';
    return `<button type="button" class="today-row" data-today-activity="${esc(row.activity_id)}"><span>${icons[row.type]||'✦'}</span><div><small>${esc(label)}</small><strong>${esc(row.name)}</strong><p>${esc(row.reason||row.status||row.subtitle||'Verified live activity')}</p></div><b>›</b></button>`;
  }

  async function loadTodayRadar(force=false) {
    const box=$('today-radar');if(!box||(!force&&Date.now()-todayLoadedAt<60000))return;
    todayLoadedAt=Date.now();
    let since='';try{since=localStorage.getItem(TODAY_LAST_VISIT_KEY)||'';}catch{}
    try {
      const response=await fetch(`/api/today${since?`?since=${encodeURIComponent(new Date(Number(since)).toISOString())}`:''}`,{cache:'no-store'});
      if(!response.ok)throw new Error('summary unavailable');const payload=await response.json();
      const fresh=(payload.new_since||[])[0],disruption=(payload.disruptions||[])[0];
      box.innerHTML=[todayRow(payload.best,'BEST NOW'),fresh&&fresh.activity_id!==payload.best?.activity_id?todayRow(fresh,'NEW'):'',disruption?todayRow(disruption,'TRAVEL WATCH'):'' ].filter(Boolean).join('')||'<p class="left-empty">Nothing notable right now.</p>';
      box.querySelectorAll('[data-today-activity]').forEach(button=>button.addEventListener('click',()=>focusActivityById(button.dataset.todayActivity,11)));
      try{localStorage.setItem(TODAY_LAST_VISIT_KEY,String(Date.now()));}catch{}
    } catch { box.innerHTML='<p class="left-empty">Today’s summary is reconnecting. The live map still works below.</p>'; }
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
        if (watchPrimed && !watchSeen.has(key)) showToast(`${icons[a.type] || '•'} ${a.name} entered ${z.name}`);
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
      ['transport','🚆','Transport', rows.filter(a=>['train','bus'].includes(a.type)), 'trains & buses moving nearby'],
      ['road','🚧','Roadworks', rows.filter(a=>a.type==='road'), 'planned works / closures'],
      ['incident','⚠️','Incidents', rows.filter(a=>a.type==='incident'), 'unplanned live incidents'],
      ['weather','🌦️','Weather', rows.filter(a=>['weather','flood'].includes(a.type)), 'warnings in this area']
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
    const moreHtml=more.length?`<div class="cool-find-list"><div class="cool-find-head"><strong>MORE LIVE FINDS</strong><span>${candidates.length} matched live</span></div>${more.map(x=>`<button type="button" class="cool-find-row${isNewDiscoveryActivity(x.a)?' is-new':''}" data-cool-find="${esc(x.a.id)}"><span>${coolBadgeHtml(x.a,true)}</span><span><strong>${isNewDiscoveryActivity(x.a)?'<em class="discovery-new-tag">NEW</em> ':''}${esc(x.a.name)}${activityMatchesInterests(x.a)?' <em class="interest-match">MY INTEREST</em>':''}</strong><small>${esc(x.c.reasons[0]||x.a.subtitle||'Unusual live movement')} • updated ${esc(discoveryFreshness(x.a))}</small></span><b>›</b></button>`).join('')}</div>`:'';
    const previewNote=!globalCoolAvailability.fullAccess&&globalCoolAvailability.total>candidates.length?`<p class="cool-preview-note">Showing a balanced ${candidates.length}-item preview from ${globalCoolAvailability.total} live Discover matches. Radar+ shows the full set.</p>`:'';
    box.innerHTML=`<div class="cool-scoreboard">${counts.map(x=>`<div><span class="cool-mini-icon cool-${x.theme}">${x.theme==='air'?'▲':x.theme==='rail'?'◆':'✚'}</span><strong>${x.n}</strong><small>${x.label}</small></div>`).join('')}</div>${previewNote}${top?`<button class="cool-hero-hit${isNewDiscoveryActivity(top.a)?' is-new':''}" type="button" data-cool-top="${esc(top.a.id)}"><span>${coolBadgeHtml(top.a,false)}</span><div><small>${isNewDiscoveryActivity(top.a)?'NEW • ':''}${activityIsCollectible(top.a)?'BEST LIVE FIND':'LIVE INCIDENT'} • updated ${esc(discoveryFreshness(top.a))}</small><strong>${esc(top.a.name)}${activityMatchesInterests(top.a)?' <em class="interest-match">MY INTEREST</em>':''}</strong><p>${esc(top.c.reasons[0]||top.a.subtitle||'Unusual live movement')}${activityIsCollectible(top.a)?` • ${top.km<1?'<1':Math.round(top.km)} km from map centre`:''}</p></div><b>›</b></button>${moreHtml}`:`<div class="cool-empty">Nothing unusual spotted ${emptyScope} right now.</div>`}`;
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

  function renderAll(sources) {
    renderMap();
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
    let streamed;
    if (isDelta) {
      const merged = new Map(activities.map(row=>[row.id,row]));
      for (const id of payload.activity_delta.remove || []) {
        const current=merged.get(id);
        if(current?.type==='train'){
          if(deferredTrainRemovals.has(id))continue;
          const timer=setTimeout(()=>{
            deferredTrainRemovals.delete(id);
            if(!activitiesById.has(id))return;
            replaceActivities(activities.filter(row=>row.id!==id));
            renderAll(latestSources);
          },90000);
          deferredTrainRemovals.set(id,timer);
        }else merged.delete(id);
      }
      for (const row of payload.activity_delta.upsert || []) if (row?.id) {
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
    const nextActivities = overheadActive
      ? [...new Map([...streamed,...nearbyAircraftRows].map(row=>[row.id,row])).values()]
      : streamed;
    replaceActivities(nextActivities);
    renderAll(latestSources);
  }

  function queueSocketPayload(payload) {
    diagnostics.receivedSnapshots += 1;
    pendingSocketPayload = payload;
    if (socketRenderFrame !== null) return;
    const applyLatest = () => {
      socketRenderFrame = null;
      const latest = pendingSocketPayload;
      pendingSocketPayload = null;
      if (latest) applySocketPayload(latest);
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

  async function runSearch() {
    const raw = $('search').value.trim();
    trackProduct('search_used', 'search');
    if (raw.length < 2) { hideSearchResults(); return; }
    const box = $('search-results'); box.classList.remove('hidden'); box.innerHTML = '<div class="search-empty">Searching live activity and UK roads/places…</div>';
    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(raw)}`); if (!response.ok) throw new Error('search failed');
      const payload = await response.json(); const rows = payload.matches || [];
      const buttons = rows.map(row => {
        const isPlace = row.kind === 'place';
        const attr = isPlace ? `data-place="${esc(row.id)}"` : `data-activity="${esc(row.id)}"`;
        const icon = isPlace ? (placeIcons[row.place_type] || '📍') : (icons[row.type] || '•');
        return `<button type="button" class="search-result" ${attr}><span class="search-result-icon">${icon}</span><span><strong>${esc(row.name)}</strong><span>${esc([row.distance_km != null ? `${row.distance_km} km away` : null,row.subtitle || row.source || (isPlace?'Smart place':'Live activity')].filter(Boolean).join(' • '))}</span></span></button>`;
      });
      if (payload.location) buttons.push(`<button type="button" class="search-result" data-location="1"><span class="search-result-icon">📍</span><span><strong>Go to location</strong><span>${esc(payload.location.name)}</span></span></button>`);
      box.innerHTML = buttons.length ? buttons.join('') : '<div class="search-empty">No matching place or live activity.</div>';
      box.querySelectorAll('[data-activity]').forEach(btn => btn.addEventListener('click',async()=>{ const id=btn.dataset.activity; searchText=''; $('search').value=''; hideSearchResults(); await focusActivityById(id,11); }));
      box.querySelectorAll('[data-place]').forEach(btn => btn.addEventListener('click',()=>{ const id=btn.dataset.place; const row=rows.find(x=>x.id===id); if(row){ if(!places.some(p=>p.id===id)) places.push(row); renderPlaces(); searchText=''; $('search').value=''; map.setView([row.lat,row.lon],row.place_type==='bus_stop'?15:12,{animate:true}); selectPlace(id,false); } hideSearchResults(); }));
      const locationButton=box.querySelector('[data-location]'); if(locationButton && payload.location) locationButton.addEventListener('click',()=>{ addSearchLocation(payload.location); hideSearchResults(); });
    } catch { box.innerHTML = '<div class="search-empty">Place search is temporarily unavailable.</div>'; }
  }

  async function resolveJourneyPlace(query) {
    const raw=String(query || '').trim();
    if(raw.length<2)return null;
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
    if(['RAIL','SUBWAY','TRAM','MONORAIL','FUNICULAR'].includes(key))return '🚆';
    if(['BUS','COACH','TROLLEYBUS'].includes(key))return '🚌';
    if(key==='FERRY')return '⛴️';
    if(['BICYCLE','BIKE','SCOOTER'].includes(key))return '🚲';
    if(key==='CAR')return '🚗';
    return '🚶';
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
    const issueRows=issues.slice(0,5).map(issue=>`<li><span>${issue.type==='incident'?'⚠️':'🚧'}</span><div><strong>${esc(issue.name || issue.reason || 'Road issue')}</strong><small>${esc([issue.road,issue.status,Number.isFinite(Number(issue.distance_km))?`${Number(issue.distance_km).toFixed(1)} km from route`:null].filter(Boolean).join(' • '))}</small></div></li>`).join('');
    const mapButton=geometry.length>1?`<button type="button" class="mini-button journey-show-map" data-journey-option="${index}">Show route on map</button>`:'';
    const directionRows=instructions.slice(0,30).map(step=>`<li><span>${esc(step.message)}</span>${step.road?`<small>${esc(step.road)}</small>`:''}</li>`).join('');
    const directions=directionRows?`<details class="journey-directions"><summary>Driving directions (${instructions.length})</summary><ol>${directionRows}</ol></details>`:'';
    return `<article class="journey-option"><div class="journey-option-head"><div><small>OPTION ${index+1}</small><strong>${esc([duration,changeText].filter(Boolean).join(' • ') || 'Journey option')}</strong></div><b>${esc([depart,arrive].filter(Boolean).join(' → '))}</b></div>${traffic?`<p class="journey-traffic ${trafficDelay>0?'delayed':'clear'}">${esc(traffic)}</p>`:''}${Number.isFinite(walking)&&walking>0?`<p class="journey-walk">Includes ${esc(walking>=1000?(walking/1000).toFixed(1)+' km':Math.round(walking)+' m')} walking</p>`:''}${legs?`<ol class="journey-legs">${legs}</ol>`:'<p class="transport-empty">The router returned an option without readable leg detail.</p>'}${issueRows?`<div class="journey-issues"><strong>Issues near this route</strong><ul>${issueRows}</ul></div>`:''}${directions}${mapButton}</article>`;
  }

  function showJourneyRoute(index) {
    const option=lastJourneyPayload?.itineraries?.[Number(index)];
    const geometry=Array.isArray(option?.geometry)?option.geometry:[];
    const points=geometry.map(point=>[Number(point.lat),Number(point.lon)]).filter(([lat,lon])=>Number.isFinite(lat)&&Number.isFinite(lon));
    if(points.length<2){showToast('This route has no map line to display.');return;}
    clearJourneyRoute();
    journeyRouteOutline=L.polyline(points,{pane:'journeyPane',color:'#071019',weight:13,opacity:.92,interactive:false,className:'journey-route-outline'}).addTo(map);
    journeyRouteLayer=L.polyline(points,{pane:'journeyPane',color:'#77f3ff',weight:7,opacity:1,interactive:false,className:'journey-route-line'}).addTo(map);
    (Array.isArray(option.route_issues)?option.route_issues:[]).forEach(issue=>{
      const lat=Number(issue.lat), lon=Number(issue.lon); if(!Number.isFinite(lat)||!Number.isFinite(lon))return;
      const marker=L.marker([lat,lon],{icon:L.divIcon({className:'journey-issue-icon',html:`<span>${issue.type==='incident'?'⚠':'🚧'}</span>`,iconSize:[30,30],iconAnchor:[15,15]})}).addTo(map);
      marker.bindTooltip?.(`${issue.name || issue.reason || 'Road issue'}${issue.road?` • ${issue.road}`:''}`,{direction:'top'});
      journeyIssueMarkers.push(marker);
    });
    const mapNode=$('map'); if(mapNode)mapNode.dataset.journeyRoutePoints=String(points.length);
    $('journey-route-clear')?.classList.remove('hidden');
    diagnostics.journeyRoutesShown=(diagnostics.journeyRoutesShown||0)+1;
    closeJourneyPlanner();
    map.fitBounds(L.latLngBounds(points),{padding:[45,45],maxZoom:13});
    showToast(`${points.length} route points mapped${journeyIssueMarkers.length?` • ${journeyIssueMarkers.length} issue${journeyIssueMarkers.length===1?'':'s'} marked`:''}.`);
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
      return `<li class="journey-leg"><span class="journey-leg-icon">🚆</span><div><strong>${esc(route)}</strong><span>${esc([from,to].filter(Boolean).join(' → '))}</span></div><b>${esc([legDepart,legArrive].filter(Boolean).join(' – '))}</b></li>`;
    }).join('');
    return `<article class="journey-option rail-fallback"><div class="journey-option-head"><div><small>RAIL OPTION ${index+1}</small><strong>${esc([type,duration].filter(Boolean).join(' • '))}</strong></div><b>${esc([depart,arrive].filter(Boolean).join(' → '))}</b></div>${legs?`<ol class="journey-legs">${legs}</ol>`:'<p class="transport-empty">Route steps are not available for this journey.</p>'}</article>`;
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
    const results=itineraries.length ? itineraries.map(renderOtpItinerary).join('') : railJourneys.map(renderRailJourney).join('');
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
    box.innerHTML=`<div class="journey-result-head"><div><small>LIVE ROUTES</small><strong>${esc(payload?.message || `${itineraries.length || railJourneys.length} journey options`)}</strong></div><span>Updated now</span></div>${resolution}<div class="journey-options">${results}</div><p class="journey-truth">Check with the operator before travelling.</p>`;
    box.querySelectorAll('.journey-show-map').forEach(button=>button.addEventListener('click',()=>showJourneyRoute(button.dataset.journeyOption)));
  }

  async function submitJourney(event) {
    event.preventDefault();
    const originName=$('journey-origin')?.value.trim() || '';
    const destinationName=$('journey-destination')?.value.trim() || '';
    const mode=$('journey-mode')?.value || 'transit';
    const box=$('journey-results'), submit=$('journey-submit');
    if(originName.length<2 || destinationName.length<2)return;
    submit.disabled=true; submit.textContent='Finding…';
    box.innerHTML='<div class="journey-loading"><i></i><strong>Finding routes…</strong></div>';
    try {
      const [origin,destination]=await Promise.all([
        resolveJourneyPlace(originName).catch(()=>null),
        resolveJourneyPlace(destinationName).catch(()=>null),
      ]);
      if(mode!=='transit' && (!origin || !destination)) throw new Error('Both places must resolve to map locations for walking, cycling or driving. Try a town, postcode or station name.');
      const response=await fetch('/api/journeys/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({origin_name:originName,destination_name:destinationName,origin_lat:origin?.lat??null,origin_lon:origin?.lon??null,destination_lat:destination?.lat??null,destination_lon:destination?.lon??null,mode,arrive_by:false,wheelchair:false})});
      const payload=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(journeyErrorText(payload,'Journey planning is unavailable right now.'));
      renderJourneyResults(payload,origin,destination);
      trackProduct('journey_planned',mode);
    } catch(error) {
      box.innerHTML=`<div class="journey-unavailable"><strong>Could not plan this journey</strong><span>${esc(error?.message || 'Journey planning is unavailable right now.')}</span></div>`;
    } finally {
      submit.disabled=false; submit.textContent='Find journeys';
    }
  }

  function openJourneyPlanner() {
    if(!requirePlus('journey_planner','Journey planning is included with Radar+.'))return;
    const dialog=$('journey-dialog'); if(!dialog)return;
    if(typeof dialog.showModal==='function')dialog.showModal(); else dialog.setAttribute('open','');
    setTimeout(()=>$('journey-origin')?.focus(),30);
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
      L.popup({maxWidth:280}).setLatLng(latlng).setContent(`<div class="traffic-popup"><strong>🚦 ${esc(label)} traffic</strong><div>Current: <b>${esc(speed)}</b></div><div>Free-flow: <b>${esc(free)}</b></div><div>Extra delay: <b>${esc(delay)}</b></div><div>Confidence: <b>${esc(d.confidence == null ? '—' : Math.round(Number(d.confidence)*100)+'%')}</b></div>${d.road_closure?'<div><b>Road closure reported</b></div>':''}<a target="_blank" rel="noopener" href="${esc(cameraUrl)}">📷 ${esc(cameraLabel)} ↗</a><small>Live flow segment • TomTom</small></div>`).openOn(map);
    } catch {}
  }

  map.on('click', e => {
    const liveId=liveObjectRenderer?.featureAt(e.containerPoint);
    if(liveId){selectActivity(liveId,true);return;}
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
      map.getContainer().classList.toggle('show-station-labels',map.getZoom()>=11);
      if(effectivePlaceMode()!=='off')fetchPlaces();
      if(webcamsEnabled)fetchCameras(false);
      renderNearbySummary();
    },80);
  });
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => {
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
      if(st?.mode==='setup') showToast(`${sourceMeta[st.name]?.label||st.name}: setup needed — ${st.message}`);
      else if(st?.mode==='error') showToast(`${sourceMeta[st.name]?.label||st.name}: ${st.message}`);
      else if(selectedType==='vessel' && st?.connected) showToast('No ships are visible here. Move closer to the coast.');
      else if(selectedType==='sar' && st?.connected) showToast('No rescue aircraft are visible here right now.');
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
    if (enabledActivityTypes.has(type)) enabledActivityTypes.delete(type); else enabledActivityTypes.add(type);
    // National Highways only reports closures where physical signs/signals are
    // set, so it is intentionally sparse. Pair the Incidents switch with the
    // current TomTom incident overlay to provide genuine local coverage.
    if(type==='incident'){
      if(enabledActivityTypes.has(type)) await setTrafficMode('incidents',true);
      else if(trafficMode==='incidents') await setTrafficMode('off',true);
    }
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
  $('journey-swap')?.addEventListener('click',()=>{ const origin=$('journey-origin'),destination=$('journey-destination'); if(!origin||!destination)return; const old=origin.value; origin.value=destination.value; destination.value=old; });
  $('journey-dialog')?.addEventListener('click',event=>{ if(event.target===$('journey-dialog'))closeJourneyPlanner(); });
  $('station-boards')?.addEventListener('click',async()=>{
    placeMode='rail'; $('place-mode').value='rail'; if(map.getZoom()<9) map.setZoom(10);
    syncBaseMap();
    const c=map.getCenter();
    try {
      const r=await fetch(`/api/places/nearest-rail?lat=${encodeURIComponent(c.lat)}&lon=${encodeURIComponent(c.lng)}`,{cache:'no-store'});
      if(r.ok){ const d=await r.json(); const p=d.place; if(p){ if(!places.some(x=>x.id===p.id))places.push(p); renderPlaces(); await selectPlace(p.id,true); showToast(`Opened ${p.name} live station board.`); return; } }
    } catch {}
    const rows=await fetchPlaces();
    const rail=(rows||places).filter(p=>p.place_type==='rail_station').sort((a,b)=>distanceKm(c.lat,c.lng,a.lat,a.lon)-distanceKm(c.lat,c.lng,b.lat,b.lon));
    if(rail[0]){await selectPlace(rail[0].id,true);showToast(`Opened ${rail[0].name} live station board.`);}else showToast('No rail station metadata is ready yet. If this is a fresh install, configure TomTom or let the station cache finish syncing.');
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
    sendWatching(null);
    syncBaseMap();
    $('track-activity')?.classList.add('hidden'); $('save-place')?.classList.add('hidden'); $('share-activity')?.classList.add('hidden');
    $('detail-sidebar')?.classList.add('is-empty'); $('detail-panel')?.classList.remove('has-selection');
    renderRanking();
  }
  $('detail-close')?.addEventListener('click',closeDetailSelection);
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
    showToast('Map cleared. Choose any layer when you want it.');
  });
  $('near-radius').addEventListener('change', e => { nearbyState.radiusMiles = Number(e.target.value || 10); if (nearbyState.active) { renderNearbyOverlay(); renderMap(); renderStats(); renderRanking(); } });
  function showDeviceLocation(lat,lon) {
    lastKnownDeviceLocation={lat:Number(lat),lon:Number(lon)};
    if(deviceLocationMarker)map.removeLayer(deviceLocationMarker);
    deviceLocationMarker=L.circleMarker([lat,lon],{radius:7,color:'#ffffff',weight:2,fillColor:'#56d6a4',fillOpacity:.95,interactive:false}).addTo(map);
    const button=$('recenter');
    if(button){button.textContent='My location';button.title='Centre on my current location';}
  }

  function centreMapOnDeviceLocation({silent=false,startup=false}={}) {
    if(lastKnownDeviceLocation){
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
      map.setView([lat,lon],12,{animate:!startup});
      sendViewport();
      if(!startup)trackProduct('nearby_used','recenter-location');
    },()=>{
      const button=$('recenter');if(button){button.textContent='Locate me';button.title='Allow location to centre the map near you';}
      if(!silent)showToast('Location permission was not granted. The Britain overview remains available.');
    },{enableHighAccuracy:false,timeout:8000,maximumAge:300000});
  }

  $('near-me').addEventListener('click', () => {
    const button=$('near-me'),note=button?.querySelector('small');
    if (!navigator.geolocation) { showToast('Location is not available in this browser. Search for a place instead.'); return; }
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
        places=[];transientNearestBusPlaceId=null;renderPlaces();showToast(error?.message||'Your nearest bus stop could not load right now.');
      } finally {if(button)button.disabled=false;if(note)note.textContent='Nearest stop and live departures';}
    }, () => {if(button)button.disabled=false;if(note)note.textContent='Nearest stop and live departures';showToast('Location permission was not granted. Search for a stop instead.');}, {enableHighAccuracy:true,timeout:10000,maximumAge:120000});
  });
  $('overhead-now')?.addEventListener('click',()=>{
    const button=$('overhead-now');
    if(overheadActive){
      clearTransientLocationModes();
      selectedType=currentWorkspace==='cool'?'cool-off':'useful';
      renderNearbyOverlay();sendViewport();renderMap();renderStats();renderRanking();showToast('Aircraft above-me view turned off.');return;
    }
    if(!navigator.geolocation){showToast('Location is not available in this browser.');return;}
    if(button){button.disabled=true;button.querySelector('small').textContent='Finding your location…';}
    navigator.geolocation.getCurrentPosition(async pos=>{
      clearTransientLocationModes();
      if($('near-radius'))$('near-radius').value='25';
      overheadActive=true;button?.classList.add('active');
      selectedType='nearby-aircraft';
      if(currentWorkspace==='useful'&&!enabledActivityTypes.has('aircraft')){enabledActivityTypes.add('aircraft');overheadAutoEnabledAircraft=true;updateLayerToggleUi();}
      document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x.dataset.type==='cool-air'));
      setNearby(pos.coords.latitude,pos.coords.longitude,'device');
      map.setView([pos.coords.latitude,pos.coords.longitude],9,{animate:true});
      collapseWorkspaceOnPhone();
      await refreshOverheadAircraft({announce:true});
      sendViewport();
      if(button){button.disabled=false;}
    },()=>{
      showToast('Location permission was not granted.');
      if(button){button.disabled=false;button.querySelector('small').textContent='Aircraft received within 50 miles';}
    },{enableHighAccuracy:false,timeout:7000,maximumAge:120000});
  });
  $('near-centre').addEventListener('click',()=>{ const c=map.getCenter(); setNearby(c.lat,c.lng,'centre'); trackProduct('nearby_used','map-centre'); });
  $('near-clear').addEventListener('click',()=>{ clearTransientLocationModes({clearNearestStop:false}); sendViewport();renderMap();renderStats();renderRanking(); });
  $('add-watch-zone').addEventListener('click',()=>{ if(!billingState.plus&&watchZones.length>=1){openPlusDialog();showToast('Free My Radar includes one watch zone. Radar+ unlocks more.');return;} const c=map.getCenter(); const radius=Number($('near-radius').value || 10); const suggested=`Watch zone ${watchZones.length+1}`; const name=(window.prompt('Name this live watch zone', suggested) || '').trim(); if(!name)return; watchZones.push({id:`zone-${Date.now()}`,name,lat:c.lat,lon:c.lng,radiusMiles:radius}); saveWatchZones(); renderWatchZones();renderMyRadar(); trackProduct('watch_zone_added','watch-zone'); showToast(`${name} saved • ${radius} mile radius`); });
  $('search').addEventListener('input', (e) => { searchText = e.target.value.trim().toLowerCase(); renderMap(); renderStats(); renderRanking(); if (!searchText) hideSearchResults(); });
  $('search').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } });
  $('search-go').addEventListener('click', runSearch);
  $('journey-route-clear')?.addEventListener('click',()=>{clearJourneyRoute();showToast('Route cleared.');});
  document.addEventListener('click', (e) => { if (!e.target.closest('.search-shell')) hideSearchResults(); });
  $('recenter').addEventListener('click', () => { if(searchMarker){map.removeLayer(searchMarker);searchMarker=null;} centreMapOnDeviceLocation(); });
  $('follow').addEventListener('click', () => {
    if (!selectedId) return;
    const wasFollowing = followed.has(selectedId);
    if(!wasFollowing&&!billingState.plus&&followed.size>=3){openPlusDialog();showToast('Free My Radar includes three followed items. Radar+ unlocks more.');return;}
    if (wasFollowing) followed.delete(selectedId); else followed.add(selectedId);
    saveFollowed(); renderMyRadar();
    const selected = activityById(selectedId);
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
    if(savedPlaces.has(place.id)){savedPlaces.delete(place.id);showToast(`${place.name} removed from My Radar.`);}
    else {if(!billingState.plus&&savedPlaces.size>=2){openPlusDialog();showToast('Free My Radar includes two saved places. Radar+ unlocks more.');return;}savedPlaces.set(place.id,{id:place.id,name:place.name,subtitle:place.subtitle,place_type:place.place_type,lat:Number(place.lat),lon:Number(place.lon),crs:place.crs,atco_code:place.atco_code,source:place.source});showToast(`${place.name} saved to My Radar.`);}
    saveMyRadarPreferences();renderMyRadar();$('save-place').textContent=savedPlaces.has(place.id)?'Saved ✓':'Save place';trackProduct('place_saved',place.place_type||'place');
  });
  $('today-refresh')?.addEventListener('click',()=>loadTodayRadar(true));
  $('passing-next-refresh')?.addEventListener('click',loadPassingNext);
  $('passing-next-place')?.addEventListener('change',loadPassingNext);
  $('commute-alert-enable')?.addEventListener('click',enableCommuteAlerts);
  $('commute-alert-disable')?.addEventListener('click',disableCommuteAlerts);

  document.querySelectorAll('.workspace-tab[data-workspace]').forEach(btn=>btn.addEventListener('click',()=>{
    // On a phone a tab is a new screen: put the detail sheet away. A train
    // keeps its route on the map as the compact route bar.
    if(isPhoneLayout()&&!$('detail-sidebar')?.classList.contains('is-empty')){
      if(selectedTrainActivity())setDetailRouteView(true);
      else closeDetailSelection();
    }
    activateWorkspace(btn.dataset.workspace,true);
    if(btn.dataset.workspace==='useful')collapseWorkspaceOnPhone();
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
  $('radar-plus-checkout')?.addEventListener('click',async()=>{const button=$('radar-plus-checkout');try{button.disabled=true;button.textContent='Opening secure checkout…';const payload=await postBilling('/api/billing/checkout');location.assign(payload.checkout_url);}catch(error){showToast(error.message);}finally{button.disabled=false;button.textContent='Get Radar+';}});
  $('radar-plus-portal')?.addEventListener('click',async()=>{try{const payload=await postBilling('/api/billing/portal');location.assign(payload.portal_url);}catch(error){showToast(error.message);}});
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
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/service-worker.js').catch(()=>{});
  initAnalyticsConsent();
  trackProduct('app_open','web');

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
  setInterval(refreshLiveMarkerPositions,1000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshLiveMarkerPositions();});

  // These account/editorial requests do not affect the first map paint or
  // websocket connection. Start them in the browser's first idle slice.
  const loadStartupExtras=()=>{
    diagnostics.startupExtrasAtMs=Math.round(performance.now());
    loadBillingStatus().then(()=>{
      const status=new URLSearchParams(location.search).get('billing');
      const messages={success:'Radar+ is active on this device.',pending:'Payment is still processing.',failed:'Payment could not be confirmed.',unavailable:'Radar+ billing is not configured yet.','portal-return':'Subscription settings updated.'};
      if(status&&messages[status]){showToast(messages[status]);history.replaceState({},'',location.pathname+location.hash);}
    });
    loadCommuteAlertStatus();
    loadTodayRadar(true);
  };
  if('requestIdleCallback' in window) window.requestIdleCallback(loadStartupExtras,{timeout:1200});
  else setTimeout(loadStartupExtras,250);
})();
