(() => {
  const map = L.map('map', { zoomControl: true, preferCanvas: true }).setView([54.1, -2.5], 6);

  const tileSets = {
    standard: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }),
    dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 20,
      subdomains: 'abcd',
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
    }),
    terrain: L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
      maxZoom: 17,
      attribution: 'Map data &copy; OpenStreetMap contributors, SRTM | Map style &copy; OpenTopoMap'
    }),
    rail: L.tileLayer('https://tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png', {
      maxZoom: 19,
      opacity: 0.9,
      attribution: 'Rail overlay &copy; OpenRailwayMap contributors'
    }),
    marine: L.tileLayer('https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png', {
      maxZoom: 18,
      opacity: 0.95,
      attribution: 'Seamarks &copy; OpenSeaMap contributors'
    })
  };
  let currentBase = tileSets.standard.addTo(map);
  let currentOverlay = null;
  let currentMapMode = 'standard';

  map.createPane('trafficPane');
  map.getPane('trafficPane').style.zIndex = '360';
  map.getPane('trafficPane').style.pointerEvents = 'none';
  let trafficLayer = null;
  let trafficIncidentLayer = null;
  let trafficMode = 'off';
  let trafficConfigured = false;
  let trafficTileErrorShown = false;

  map.createPane('weatherRadarPane');
  map.getPane('weatherRadarPane').style.zIndex = '355';
  map.getPane('weatherRadarPane').style.pointerEvents = 'none';
  let weatherRadarLayer = null;
  let weatherRadarEnabled = false;
  let weatherRadarRequest = 0;
  let weatherRadarRefreshTimer = null;

  const icons = { train:'🚆', bus:'🚌', vessel:'🚢', sar:'🚁', aircraft:'✈️', rescue:'🛟', incident:'⚠️', road:'🚧', flood:'🌊', weather:'🌦️', earthquake:'🌐', river:'🏞️', tide:'🌊', wave:'🌊', grid:'⚡', airquality:'💨', bike:'🚲', space:'🌌', satellite:'🛰️', outage:'🔌', gritter:'❄️', camera:'📷', other:'•' };
  const markers = new Map();
  const markerGroup = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({ disableClusteringAtZoom: 10, maxClusterRadius: 48, showCoverageOnHover: false, spiderfyOnMaxZoom: true })
    : L.layerGroup();
  markerGroup.addTo(map);

  const placeIcons = {rail_station:'🚉',bus_stop:'🚏',ferry_terminal:'⛴️',rain_gauge:'🌧️'};
  const placeMarkers = new Map();
  const placeGroup = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({ disableClusteringAtZoom: 14, maxClusterRadius: 40, showCoverageOnHover:false })
    : L.layerGroup();
  placeGroup.addTo(map);
  let places = [];
  let placeMode = 'all';
  let placeFetchSeq = 0;
  let selectedPlaceId = null;
  let placeLinkLine = null;

  let activities = [];
  let selectedId = null;
  let selectedType = 'useful';
  let currentWorkspace = 'useful';
  let searchText = '';
  let socket = null;
  let streamMeta = {};
  let latestSources = [];
  let viewportTimer = null;
  let coverageRequest = 0;
  let coverageLoadedFor = null;
  let mediaRequest = 0;
  let mediaLoadedFor = null;
  let audioRequest = 0;
  let audioLoadedFor = null;
  let searchMarker = null;
  let radioMarker = null;
  let radioRequest = 0;
  let radioRefreshTimer = null;
  let heatLayer = null;
  let heatMode = 'off';
  let nearbyCircle = null;
  let nearbyMarker = null;
  let nearbyState = { active: false, lat: null, lon: null, radiusMiles: 10, source: null };
  let watchZones = [];
  let watchCircles = [];
  let watchPrimed = false;
  const watchSeen = new Set();
  const followed = new Set();
  let deferredInstallPrompt = null;
  let plansPayload = null;
  let lastProductSummaryAt = 0;
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
  const nearbyWebcamsUrl = (item, zoom=11) => {
    const lat = Number(item?.lat), lon = Number(item?.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    // Windy opens the exact tracked/fixed location; its location detail exposes
    // "Webcams in vicinity". Crucially this does NOT search the vessel name,
    // so a ship called GV ACCORD is never interpreted as having an onboard cam.
    const a = lat.toFixed(5), o = lon.toFixed(5);
    return `https://www.windy.com/${a}/${o}?${a},${o},${zoom},d:picker`;
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
  async function trackProduct(eventName, feature='') {
    try {
      await fetch('/api/product/event', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,event_name:eventName,feature})});
    } catch {}
  }

  const searchUrl = (base, query) => `${base}${encodeURIComponent(query)}`;
  const googleImagesUrl = (query) => searchUrl('https://www.google.com/search?tbm=isch&q=', query);
  const commonsUrl = (query) => searchUrl('https://commons.wikimedia.org/w/index.php?search=', query);
  const openRailwayMapUrl = (a) => `https://www.openrailwaymap.org/?style=standard&lat=${Number(a.lat).toFixed(5)}&lon=${Number(a.lon).toFixed(5)}&zoom=13`;
  const openSeaMapUrl = (a) => `https://map.openseamap.org/?zoom=12&lat=${Number(a.lat).toFixed(5)}&lon=${Number(a.lon).toFixed(5)}`;
  const googleMapsUrl = (a) => `https://www.google.com/maps/search/?api=1&query=${Number(a.lat).toFixed(6)}%2C${Number(a.lon).toFixed(6)}`;
  const streetViewUrl = (a) => `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${Number(a.lat).toFixed(6)}%2C${Number(a.lon).toFixed(6)}`;
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
    if (['aircraft','gritter','satellite'].includes(a.type)) { score += 10; reasons.push(a.type === 'aircraft' ? 'military / special aircraft' : a.type === 'gritter' ? 'live winter-service vehicle' : 'ISS currently in map area'); }
    if (a.type === 'airquality' && /high|very high|index [7-9]|index 10/.test(text)) { score += 12; reasons.push('elevated air pollution'); }
    if (a.type === 'train' && /steam|special|charter|heritage|measurement|test train|royal/.test(text)) { score += 18; reasons.push('unusual rail movement'); }
    const delay = Math.abs(Number(a.delay_minutes || 0));
    if (delay >= 15) { score += Math.min(15, delay / 3); reasons.push(`${delay} minute rail delay`); }
    const age = ageMinutes(a);
    if (age <= 5) { score += 8; reasons.push('just updated'); }
    else if (age <= 15) score += 4;
    if (!reasons.length && a.kind !== 'event') reasons.push('live movement');
    return { score, reasons: [...new Set(reasons)].slice(0, 3) };
  }

  function coolInfo(a) {
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
      else if (/RAILTOUR|RAIL TOUR|CHARTER|HERITAGE/.test(text) || /^1Z/.test(head)) { score=94; label='RAILTOUR'; icon='◆'; theme='rail'; reasons.push('Charter / special passenger movement'); }
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
    return {score:Math.max(0,Math.min(104,score)),label,icon,theme,reasons:[...new Set(reasons)].slice(0,3)};
  }

  function coolBadgeHtml(a, compact=false) {
    const c=coolInfo(a); if(!c.label || c.score<45) return '';
    return `<span class="cool-badge cool-${esc(c.theme)}${compact?' compact':''}"><b>${esc(c.icon)}</b><em>${esc(c.label)}</em></span>`;
  }

  function trafficTileStyle() {
    if (trafficMode === 'delays') return 'relative-delay';
    return currentMapMode === 'dark' ? 'relative0-dark' : 'relative0';
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
        showToast('TomTom traffic flow could not load. The server now tries Orbis v2 first, then the legacy traffic tile endpoint. Check Traffic feed status for the exact provider error.');
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
        showToast('TomTom traffic incidents could not load. Check Traffic feed status for the exact TomTom provider error.');
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
    const select = $('traffic-mode');
    if (select) select.value = mode;
    try { localStorage.setItem('ukRadarTrafficMode', mode); } catch {}
    renderTrafficLayer();
    if (!quiet) trackProduct('layer_selected', mode === 'off' ? 'traffic-off' : `traffic-${mode}`);
  }

  async function setWeatherRadar(enabled, quiet=false) {
    weatherRadarEnabled = Boolean(enabled);
    const btn=$('weather-radar');
    btn?.classList.toggle('active',weatherRadarEnabled);
    if (weatherRadarLayer && map.hasLayer(weatherRadarLayer)) map.removeLayer(weatherRadarLayer);
    weatherRadarLayer=null;
    const legend=$('weather-radar-legend');
    if (!weatherRadarEnabled) { if(weatherRadarRefreshTimer){clearTimeout(weatherRadarRefreshTimer);weatherRadarRefreshTimer=null;} legend?.classList.add('hidden'); try{localStorage.setItem('ukRadarWeatherRadar','off')}catch{}; return; }
    const req=++weatherRadarRequest;
    try {
      const r=await fetch('/api/weather/radar',{cache:'no-store'});
      if(!r.ok) throw new Error('radar unavailable');
      const d=await r.json(); if(req!==weatherRadarRequest || !weatherRadarEnabled)return;
      weatherRadarLayer=L.tileLayer(d.tile_template,{pane:'weatherRadarPane',opacity:.60,maxNativeZoom:Number(d.max_native_zoom||7),maxZoom:18,minZoom:2,attribution:esc(d.attribution||'Weather radar © RainViewer')}).addTo(map);
      const stamp=d.frame_time?new Date(d.frame_time*1000):null;
      const label=$('weather-radar-time'); if(label) label.textContent=stamp&&!Number.isNaN(stamp.getTime())?`Frame ${stamp.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}`:'Latest frame';
      legend?.classList.remove('hidden');
      try{localStorage.setItem('ukRadarWeatherRadar','on')}catch{}
      if(weatherRadarRefreshTimer)clearTimeout(weatherRadarRefreshTimer);
      weatherRadarRefreshTimer=setTimeout(()=>{ if(weatherRadarEnabled)setWeatherRadar(true,true); },5*60*1000);
      if(!quiet)showToast('Live precipitation radar on. The displayed frame refreshes automatically every five minutes.');
    } catch {
      weatherRadarEnabled=false; if(weatherRadarRefreshTimer){clearTimeout(weatherRadarRefreshTimer);weatherRadarRefreshTimer=null;} btn?.classList.remove('active'); legend?.classList.add('hidden');
      if(!quiet)showToast('Live weather radar is unavailable right now.');
    }
  }

  function setMapMode(mode) {
    if (!['standard','dark','rail','marine','terrain'].includes(mode)) return;
    if (currentBase && map.hasLayer(currentBase)) map.removeLayer(currentBase);
    if (currentOverlay && map.hasLayer(currentOverlay)) map.removeLayer(currentOverlay);
    currentOverlay = null;
    currentMapMode = mode;
    if (mode === 'dark') currentBase = tileSets.dark;
    else if (mode === 'terrain') currentBase = tileSets.terrain;
    else currentBase = tileSets.standard;
    currentBase.addTo(map);
    if (mode === 'rail') { currentOverlay = tileSets.rail; currentOverlay.addTo(map); }
    if (mode === 'marine') { currentOverlay = tileSets.marine; currentOverlay.addTo(map); }
    $('map').dataset.mapMode = mode;
    document.querySelectorAll('.map-mode').forEach(btn => btn.classList.toggle('active', btn.dataset.mapMode === mode));
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
    if (a.type === 'vessel' && a.vehicle_ref) pills.push(`<span class="tooltip-pill">MMSI ${esc(a.vehicle_ref)}</span>`);
    if (['sar','aircraft'].includes(a.type) && (a.aircraft_model || a.vehicle_ref)) pills.push(`<span class="tooltip-pill">${esc(a.aircraft_model || a.vehicle_ref)}</span>`);
    const badge = currentWorkspace === 'cool' ? coolBadgeHtml(a,true) : '';
    return `<div class="activity-tooltip-card"><div class="tooltip-top"><strong>${icons[a.type] || '•'} ${esc(a.name)}</strong><span class="tooltip-live">${a.kind === 'event' ? 'EVENT' : 'LIVE'}</span></div>${badge}${route ? `<div class="tooltip-sub">${esc(route)}</div>` : ''}<div class="tooltip-pills">${pills.slice(0,4).join('')}</div><div class="tooltip-hint">Updated ${esc(relativeAge(Math.max(0,(Date.now()-new Date(a.last_updated||0).getTime())/1000)))} • click for detail</div></div>`;
  }

  function clearPhoto() {
    const box = $('detail-photo');
    if (!box) return;
    box.classList.add('hidden');
    box.innerHTML = '';
    mediaLoadedFor = null;
  }

  async function loadPhoto(a) {
    const box = $('detail-photo');
    if (!box) return;
    if (mediaLoadedFor === a.id) return;
    mediaLoadedFor = a.id;
    const requestId = ++mediaRequest;
    box.classList.remove('hidden');
    box.innerHTML = '<div class="photo-loading">Checking for a verified Commons image match…</div>';
    try {
      const response = await fetch(`/api/activity/${encodeURIComponent(a.id)}/media`);
      if (!response.ok) throw new Error('Image lookup unavailable');
      const payload = await response.json();
      if (requestId !== mediaRequest || selectedId !== a.id) return;
      if (!payload.ok || !payload.thumbnail_url) {
        // A missing image is cleaner than a plausible-but-wrong historic photo.
        // Manual picture searches remain in the context links below.
        box.classList.add('hidden');
        box.innerHTML = '';
        return;
      }
      const attribution = [payload.artist, payload.license].filter(Boolean).join(' • ');
      box.innerHTML = `<a target="_blank" rel="noopener" href="${esc(safeUrl(payload.page_url || payload.search_url))}"><img src="${esc(safeUrl(payload.thumbnail_url))}" alt="${esc(payload.title || a.name)}" loading="lazy"></a><div class="detail-photo-copy"><strong>${esc(payload.title || a.name)}</strong><span>${esc(attribution || 'Wikimedia Commons')} • <a target="_blank" rel="noopener" href="${esc(safeUrl(payload.page_url || payload.search_url))}">source/licence ↗</a></span></div>`;
    } catch {
      if (requestId !== mediaRequest) return;
      box.classList.add('hidden'); box.innerHTML = '';
    }
  }

  async function loadAircraftInfo(a) {
    const box = $('detail-photo');
    if (!box) return;
    const requestId = ++mediaRequest;
    box.classList.remove('hidden');
    box.innerHTML = '<div class="photo-loading">Resolving exact aircraft identity…</div>';
    try {
      const response = await fetch(`/api/activity/${encodeURIComponent(a.id)}/aircraft`, {cache:'no-store'});
      if (!response.ok) throw new Error('Aircraft lookup unavailable');
      const payload = await response.json();
      if (requestId !== mediaRequest || selectedId !== a.id) return;
      const photo = payload.photo || null;
      const reg = payload.registration || a.vehicle_ref || null;
      const model = payload.model || a.aircraft_model || a.aircraft_type_code || null;
      const profile = safeUrl(payload.profile_url || (reg ? `https://airport-data.com/aircraft/${encodeURIComponent(reg)}` : ''));
      const identity = [reg, model, payload.country].filter(Boolean).join(' • ');
      const image = photo && photo.thumbnail_url ? `<a target="_blank" rel="noopener" href="${esc(safeUrl(photo.page_url || profile))}"><img src="${esc(safeUrl(photo.thumbnail_url))}" alt="${esc(identity || a.name)}" loading="lazy"></a>` : '';
      const credit = photo && photo.photographer ? `Photo © ${photo.photographer} • Airport-Data.com` : 'Aircraft identity • Airport-Data.com';
      const links = [profile ? `<a target="_blank" rel="noopener" href="${esc(profile)}">aircraft profile ↗</a>` : '', a.source_id ? `<a target="_blank" rel="noopener" href="https://www.planespotters.net/hex/${esc(String(a.source_id).toUpperCase())}">Planespotters ↗</a>` : ''].filter(Boolean).join(' • ');
      if (!payload.ok && !model && !reg) { box.classList.add('hidden'); box.innerHTML=''; return; }
      box.innerHTML = `${image}<div class="detail-photo-copy"><strong>${esc(model || a.aircraft_model || a.aircraft_type_code || 'Aircraft type not resolved')}</strong><span>${esc(identity || a.name)}</span><span>${esc(credit)}${links ? ` • ${links}` : ''}</span>${payload.note ? `<small>${esc(payload.note)}</small>` : ''}</div>`;
    } catch {
      if (requestId !== mediaRequest) return;
      box.classList.add('hidden'); box.innerHTML='';
    }
  }

  function regionRoadLinks(a) {
    const lat = Number(a.lat), lon = Number(a.lon);
    const road = String(a.road || '').trim();
    if (lat >= 54.5) return [
      ['Traffic Scotland', 'https://www.traffic.gov.scot/'],
      ['Official traffic cameras', 'https://www.traffic.gov.scot/traffic-cameras'],
      ['Traffic Scotland Radio', 'https://www.traffic.gov.scot/traffic-scotland-radio']
    ];
    if (lat >= 51.2 && lat <= 53.6 && lon <= -2.45) return [
      ['Traffic Wales', 'https://traffic.wales/'],
      ['Official road cameras', 'https://traffic.wales/road-cameras']
    ];
    const cameraQuery = road ? `${road} live motorway camera` : 'UK live motorway cameras';
    return [
      ['National Highways live travel', 'https://nationalhighways.co.uk/travel-updates/'],
      ['Motorway cameras (third-party)', `https://www.google.com/search?q=${encodeURIComponent(`site:motorwaycameras.co.uk ${cameraQuery}`)}`]
    ];
  }

  function clearResources() {
    const box = $('detail-resources');
    if (!box) return;
    box.classList.add('hidden');
    box.innerHTML = '';
  }

  function renderContextResources(a) {
    const box = $('detail-resources');
    if (!box) return;
    const links = [];
    let heading = 'MORE LIVE CONTEXT';
    if (a.kind !== 'event' && a.type === 'train') {
      const photoQuery = [a.operator, a.headcode, a.origin, a.destination, 'train'].filter(Boolean).join(' ');
      links.push(['OpenRailwayMap', openRailwayMapUrl(a)]);
    } else if (a.kind !== 'event' && a.type === 'vessel') {
      const mmsi = String(a.vehicle_ref || a.source_id || '').replace(/\D/g, '');
      const photoQuery = [a.name, mmsi ? `MMSI ${mmsi}` : null, 'ship'].filter(Boolean).join(' ');
      if (mmsi) links.push(['Exact vessel image search', googleImagesUrl(`${a.name || ''} MMSI ${mmsi}`)]);
      links.push(['OpenSeaMap', openSeaMapUrl(a)]);
      const webcams = nearbyWebcamsUrl(a);
      if (webcams) links.push(['Webcams near current vessel position', webcams]);
      if (mmsi) links.push(['VesselFinder profile / photos ↗', `https://www.vesselfinder.com/vessels/details/${encodeURIComponent(mmsi)}`], ['MarineTraffic profile / photos ↗', `https://www.marinetraffic.com/en/ais/details/ships/mmsi:${encodeURIComponent(mmsi)}`]);
    } else if (a.kind !== 'event' && a.type === 'sar') {
      const q = [a.name, a.vehicle_ref, 'SAR aircraft'].filter(Boolean).join(' ');
      if (a.vehicle_ref) links.push(['Exact aircraft image search', googleImagesUrl(q)]);
    }
    if (['road','incident'].includes(a.type)) {
      heading = 'ROAD CONTEXT & CAMERAS';
      links.push(...regionRoadLinks(a));
    }
    if (a.type === 'rescue') {
      links.push(['Street View at lifeboat station', streetViewUrl(a)]);
      const webcams = nearbyWebcamsUrl(a, 12);
      if (webcams) links.push(['Webcams near lifeboat station', webcams]);
    }
    if (['vessel','rescue','sar'].includes(a.type)) {
      links.push(['Maritime safety broadcasts', 'https://www.gov.uk/maritime-safety-weather-and-navigation/maritime-safety-information-broadcasts']);
      links.push(['Shipping Forecast', 'https://www.bbc.co.uk/programmes/b006qfvv']);
    }
    if (a.type === 'earthquake') links.push(['BGS recent earthquakes','https://earthquakes.bgs.ac.uk/earthquakes/recent_uk_events.html']);
    if (a.type === 'tide') links.push(['Environment Agency tide levels','https://environment.data.gov.uk/flood-monitoring/']);
    if (a.type === 'wave') links.push(['Cefas WaveNet','https://wavenet.cefas.co.uk/map']);
    if (a.type === 'grid') links.push(['NESO Carbon Intensity','https://carbonintensity.org.uk/']);
    if (a.type === 'airquality') links.push(['DEFRA UK-AIR','https://uk-air.defra.gov.uk/']);
    if (a.type === 'space') links.push(['NOAA Space Weather','https://www.swpc.noaa.gov/']);
    if (a.type === 'satellite') links.push(['CelesTrak ISS data','https://celestrak.org/']);
    if (a.type === 'outage') links.push(['Powercut 105','https://www.powercut105.com/']);
    if (a.type === 'gritter') links.push(['Traffic Scotland gritter tracker','https://www.traffic.gov.scot/gritter-tracker']);
    if (a.type === 'aircraft') links.push(['ADSB.lol','https://adsb.lol/']);
    if (a.type === 'bike') links.push(['Live shared mobility source', safeUrl(a.official_url || 'https://github.com/MobilityData/gbfs')]);
    links.push(['Open location', googleMapsUrl(a)]);
    if (!links.length) { clearResources(); return; }
    box.classList.remove('hidden');
    box.innerHTML = `<small>${esc(heading)}</small><div class="resource-actions">${links.map(([label,url]) => `<a class="resource-link" target="_blank" rel="noopener" href="${esc(safeUrl(url))}">${esc(label)} ↗</a>`).join('')}</div><p class="resource-note">These are free public links. Camera/radio availability is controlled by the source service.</p>`;
  }

  function renderMapRadio(payload, centre) {
    const list=$('map-radio-list'), label=$('map-radio-location'); if(!list||!label)return;
    label.textContent=`Map centre ${centre.lat.toFixed(3)}, ${centre.lng.toFixed(3)} • specialist public streams first`;
    const stations=payload?.stations||[];
    if(!stations.length){list.innerHTML='<p class="left-empty">No playable public audio found around this map position.</p>';return;}
    const specialist=stations.filter(s=>s.specialism && s.specialism!=='general');
    const general=stations.filter(s=>!s.specialism || s.specialism==='general');
    const renderRows=(rows)=>rows.map(st=>{
      const spec=st.specialism||'general'; const specLabel={aviation:'AVIATION',marine:'MARINE',rail:'RAIL',general:'LOCAL'}[spec]||'LOCAL';
      const specIcon={aviation:'▲',marine:'⚓',rail:'◆',general:'◌'}[spec]||'◌';
      const meta=[st.distance_km!=null?`${st.distance_km} km`:null,st.codec,st.bitrate?`${st.bitrate} kbps`:null].filter(Boolean).join(' • ');
      const stream=safeUrl(st.stream_url||'#'), homepage=safeUrl(st.homepage||'#'); const hasStream=stream!=='#',hasHome=homepage!=='#';
      const hasPos=Number.isFinite(Number(st.lat))&&Number.isFinite(Number(st.lon));
      return `<div class="map-radio-row specialist-${esc(spec)}"><div class="audio-source-copy"><span class="audio-specialism">${specIcon} ${specLabel}</span><strong>${esc(st.name)}</strong><span>${esc(meta||'Public stream')}</span></div><div class="radio-row-actions">${hasStream?`<button type="button" data-radio-stream="${esc(stream)}" data-radio-name="${esc(st.name)}" title="Play inside the radar">▶</button>`:''}${hasHome?`<a href="${esc(homepage)}" target="_blank" rel="noopener" title="Source website">↗</a>`:''}${hasPos?`<button type="button" data-radio-lat="${Number(st.lat)}" data-radio-lon="${Number(st.lon)}" data-radio-name="${esc(st.name)}" title="Show source on map">◎</button>`:''}</div></div>`;
    }).join('');
    list.innerHTML=`${specialist.length?`<div class="audio-group-title">SPECIALIST AUDIO</div>${renderRows(specialist)}`:'<div class="specialist-none"><strong>No specialist public stream found here.</strong><span>Airband/marine/rail audio only appears when an authorised public stream is discoverable.</span></div>'}${general.length?`<details class="general-audio"><summary>Local public radio (${general.length})</summary>${renderRows(general)}</details>`:''}`;
    list.querySelectorAll('[data-radio-stream]').forEach(btn=>btn.addEventListener('click',async()=>{const player=$('radio-player'),now=$('radio-now');if(!player)return;player.src=btn.dataset.radioStream;if(now)now.textContent=`▶ ${btn.dataset.radioName||'Live audio'}`;try{await player.play();}catch{showToast('This public stream would not play in the embedded player. Use its ↗ source link instead.');}}));
    list.querySelectorAll('[data-radio-lat]').forEach(btn=>btn.addEventListener('click',()=>{const lat=Number(btn.dataset.radioLat),lon=Number(btn.dataset.radioLon),name=btn.dataset.radioName||'Audio source';if(radioMarker)map.removeLayer(radioMarker);const icon=L.divIcon({className:'radio-marker-wrap',html:'<div class="radio-pin-icon">◉</div>',iconSize:[30,30],iconAnchor:[15,15]});radioMarker=L.marker([lat,lon],{icon}).addTo(map).bindPopup(`<strong>${esc(name)}</strong><br><small>Public audio source</small>`).openPopup();map.panTo([lat,lon],{animate:true});}));
  }

  async function loadMapRadio(force=false) {
    const list=$('map-radio-list'); if(!list)return;
    const centre=map.getCenter();
    const requestId=++radioRequest;
    if (force) list.innerHTML='<p class="left-empty">Finding nearby public radio…</p>';
    try {
      const r=await fetch(`/api/radio/nearby?lat=${encodeURIComponent(centre.lat)}&lon=${encodeURIComponent(centre.lng)}&limit=6`,{cache:'no-store'});
      if(!r.ok)throw new Error('radio unavailable');
      const payload=await r.json();
      if(requestId!==radioRequest)return;
      renderMapRadio(payload,centre);
    } catch {
      if(requestId!==radioRequest)return;
      $('map-radio-location').textContent=`Map centre ${centre.lat.toFixed(3)}, ${centre.lng.toFixed(3)}`;
      list.innerHTML='<p class="left-empty">Nearby public radio lookup is unavailable right now.</p>';
    }
  }

  function queueMapRadio() {
    clearTimeout(radioRefreshTimer);
    radioRefreshTimer=setTimeout(()=>loadMapRadio(false),700);
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
    const pointer = directional ? `<div class="motion-pointer" style="transform:rotate(${heading}deg)"></div>` : '';
    const speedTag = directional && speed && map.getZoom() >= 12 ? `<span class="motion-speed">${esc(speed)}</span>` : '';
    const cool=coolInfo(v);
    if(currentWorkspace==='cool' && cool.score>=45){
      return L.divIcon({
        className:'marker-wrap cool-marker-wrap',
        html:`<div class="motion-shell"><div class="cool-map-marker cool-${esc(cool.theme)}" title="${esc(cool.label)} — ${esc(v.name)}"><span>${esc(cool.icon)}</span><small>${esc(cool.label)}</small></div>${pointer}${speedTag}</div>`,
        iconSize:[58,52],iconAnchor:[29,26]
      });
    }
    return L.divIcon({
      className: 'marker-wrap',
      html: `<div class="motion-shell"><div class="marker-bubble marker-${esc(type)} marker-${esc(method)}${eventClass}" title="${esc(v.position_label)}">${icons[type] || '•'}</div>${pointer}${speedTag}</div>`,
      iconSize: [42,42], iconAnchor: [21,21]
    });
  }

  function placeMarkerIcon(p) {
    const icon = placeIcons[p.place_type] || '📍';
    return L.divIcon({
      className: 'place-marker-wrap',
      html: `<div class="place-marker place-${esc(p.place_type || 'other')}" title="${esc(p.name)}">${icon}</div>`,
      iconSize:[27,27], iconAnchor:[13,13]
    });
  }

  function renderPlaces() {
    const shown = new Set(places.map(p => p.id));
    for (const [id, marker] of placeMarkers.entries()) {
      if (!shown.has(id)) { placeGroup.removeLayer(marker); placeMarkers.delete(id); }
    }
    for (const p of places) {
      let marker=placeMarkers.get(p.id);
      if (!marker) {
        marker=L.marker([p.lat,p.lon],{icon:placeMarkerIcon(p),riseOnHover:true});
        marker.bindTooltip(`<div class="activity-tooltip-card"><strong>${placeIcons[p.place_type]||'📍'} ${esc(p.name)}</strong><span>${esc(p.subtitle || p.source || 'Smart place')}</span><small>Click for live board / context</small></div>`,{direction:'top',offset:[0,-11],opacity:.97,className:'activity-tooltip'});
        marker.on('click',()=>selectPlace(p.id,true));
        placeGroup.addLayer(marker); placeMarkers.set(p.id,marker);
      } else marker.setLatLng([p.lat,p.lon]);
    }
    if (typeof placeGroup.refreshClusters === 'function') placeGroup.refreshClusters();
  }

  async function fetchPlaces() {
    if (placeMode === 'off') { places=[]; renderPlaces(); return; }
    const b=map.getBounds(), seq=++placeFetchSeq;
    try {
      const u=`/api/places?west=${encodeURIComponent(b.getWest())}&south=${encodeURIComponent(b.getSouth())}&east=${encodeURIComponent(b.getEast())}&north=${encodeURIComponent(b.getNorth())}&zoom=${encodeURIComponent(map.getZoom())}&mode=${encodeURIComponent(placeMode)}`;
      const r=await fetch(u,{cache:'no-store'}); if(!r.ok) throw new Error('places unavailable');
      const payload=await r.json(); if(seq!==placeFetchSeq)return;
      places=payload.places||[]; renderPlaces(); return places;
    } catch { if(seq===placeFetchSeq){places=[];renderPlaces();} return places; }
  }

  function clearPlaceBoard() {
    const box=$('detail-placeboard');
    if(box){box.classList.add('hidden');box.innerHTML='';}
  }

  function boardRow(row, kind) {
    const rawClock=row.expected || row.clock || row.scheduled || '';
    const clock=rawClock && String(rawClock).includes('T') ? (timeText(rawClock)||rawClock) : rawClock;
    const baseStatus=row.status || (row.minutes!=null ? `${row.minutes} min` : '');
    const status=(kind==='bus' && row.minutes!=null && !String(baseStatus).toLowerCase().includes('live')) ? `${baseStatus} • ${row.minutes}m` : baseStatus;
    const place=kind==='arrival' ? (row.origin || row.operator || '') : (row.destination || row.operator || row.route || '');
    const platform=row.platform ? `Plat ${row.platform}` : '';
    const live=row.live_train_id || row.live_vehicle_id;
    return `<button type="button" class="board-row${live?' board-clickable':''}" ${live?`data-live-id="${esc(live)}"`:''}><span class="board-time">${esc(clock || (row.minutes!=null?`${row.minutes}m`:'—'))}</span><span class="board-main"><strong>${esc(place || row.route || row.name || 'Service')}</strong><span>${esc([row.headcode,row.route,row.operator].filter(Boolean).join(' • '))}</span></span><span class="board-status">${esc([status,platform].filter(Boolean).join(' • '))}</span></button>`;
  }

  function bindBoardLiveLinks(box) {
    box.querySelectorAll('[data-live-id]').forEach(btn=>btn.addEventListener('click',()=>{
      const id=btn.dataset.liveId; const a=activities.find(x=>x.id===id);
      if(a) selectActivity(id,true); else showToast('That live vehicle is no longer in the current snapshot.');
    }));
  }

  function clearPlaceLink() {
    if(placeLinkLine && map.hasLayer(placeLinkLine)) map.removeLayer(placeLinkLine);
    placeLinkLine=null;
  }

  function focusNextLive(place, liveId) {
    clearPlaceLink();
    if(!liveId)return;
    const a=activities.find(x=>x.id===liveId); if(!a)return;
    placeLinkLine=L.polyline([[place.lat,place.lon],[a.lat,a.lon]],{weight:2,opacity:.72,dashArray:'5 7',interactive:false,className:'place-link-line'}).addTo(map);
    const marker=markers.get(liveId);
    if(marker){ try{marker.openTooltip(); marker.setZIndexOffset?.(900);}catch{} }
    const bounds=L.latLngBounds([[place.lat,place.lon],[a.lat,a.lon]]);
    if(!map.getBounds().contains([a.lat,a.lon])) map.fitBounds(bounds,{maxZoom:13,padding:[55,55]});
  }

  function renderPlaceBoard(place, payload) {
    const box=$('detail-placeboard'); if(!box)return;
    const board=payload.board||{}; let html='';
    const placeCam = place.place_type === 'ferry_terminal' ? nearbyWebcamsUrl(place, 12) : null;
    const fixedLinks=`<div class="place-streetview"><a class="resource-link" target="_blank" rel="noopener" href="${esc(streetViewUrl(place))}">Street View here ↗</a><a class="resource-link" target="_blank" rel="noopener" href="${esc(googleMapsUrl(place))}">Google Maps ↗</a>${placeCam ? `<a class="resource-link" target="_blank" rel="noopener" href="${esc(placeCam)}">Nearby webcams ↗</a>` : ''}</div>`;
    if(place.place_type==='rail_station') {
      const deps=(board.departures||[]).map(r=>boardRow(r,'departure')).join('');
      const arrivalRows=(board.arrivals&&board.arrivals.length ? board.arrivals : (board.recent_arrivals||[]));
      const arr=arrivalRows.map(r=>boardRow(r,'arrival')).join('');
      const arrivalLabel=(board.arrivals&&board.arrivals.length)?'Next arrivals':'Recent actual arrivals';
      const next=board.next_live_id?'<p class="board-next-label">↗ A board service has an exact live TRUST match; click its row to track it.</p>':'';
      const waiting=board.mode==='schedule_sync'?'<p class="board-empty board-waiting">The Network Rail schedule index is still building. This panel will become the full station board when it is ready.</p>':'';
      html=`<small>LIVE STATION BOARD • ${esc(place.crs||'')}</small>${fixedLinks}${waiting}<div class="board-tabs"><div><strong>Next departures</strong>${deps||'<p class="board-empty">No upcoming departures found.</p>'}</div><div><strong>${esc(arrivalLabel)}</strong>${arr||'<p class="board-empty">No arrival data found.</p>'}</div></div>${next}<p class="resource-note"><strong>${esc(board.source||'Rail data')}</strong> • ${esc(board.note||'Schedule times with exact live overlays where available.')}</p>`;
    } else if(place.place_type==='bus_stop') {
      const rows=(board.rows||[]).map(r=>boardRow(r,'bus')).join('');
      const targeted=(board.targeted_live||[]).map(r=>boardRow(r,'bus')).join('');
      let mode='Bus stop';
      if(board.mode==='live_predictions') mode='Live predicted buses';
      else if(board.mode==='published_timetable') mode='Next published buses';
      else if(board.mode==='live_next_stop') mode='Live bus targeting this stop';
      else if(board.mode==='timetable_sync') mode='Loading BODS timetable…';
      else mode='No due services found';
      const next=board.next_live_id?'<p class="board-next-label">↗ A live bus associated with this stop is highlighted; click a green-linked row where available.</p>':'';
      const targetBlock=targeted?`<div class="board-secondary"><strong>Live buses explicitly reporting this stop</strong>${targeted}</div>`:'';
      html=`<small>BUS STOP</small>${fixedLinks}<strong class="board-heading">${esc(mode)}</strong>${rows||'<p class="board-empty">No stop times are available yet.</p>'}${targetBlock}${next}<p class="resource-note">${esc(board.note||board.source||'DfT BODS')}</p>`;
      if(board.mode==='timetable_sync' && board.retry_seconds){
        const retry=Math.max(3,Math.min(20,Number(board.retry_seconds)||5));
        setTimeout(()=>{ if(selectedPlaceId===place.id)selectPlace(place.id,false); },retry*1000);
      }
    } else if(place.place_type==='ferry_terminal') {
      const rows=(board.rows||[]).map(r=>`<button type="button" class="board-row${r.live_vehicle_id?' board-clickable':''}" ${r.live_vehicle_id?`data-live-id="${esc(r.live_vehicle_id)}"`:''}><span class="board-time">${esc(r.eta_minutes!=null?`~${r.eta_minutes}m`:r.distance_km!=null?`${r.distance_km} km`:'—')}</span><span class="board-main"><strong>${esc(r.name||'Vessel')}</strong><span>${esc(r.vehicle_ref?`MMSI ${r.vehicle_ref}`:'Live AIS')}</span></span><span class="board-status">${esc(r.inbound?'Inbound':r.speed!=null?`${r.speed} ${r.speed_unit||''}`:'Nearby')}</span></button>`).join('');
      const website=board.website?`<a class="resource-link" target="_blank" rel="noopener" href="${esc(safeUrl(board.website))}">Operator / terminal sailings ↗</a>`:'';
      const next=board.next_live_id?'<p class="board-next-label">↗ Best inbound/nearest AIS match is highlighted on the map.</p>':'';
      html=`<small>FERRY TERMINAL</small>${fixedLinks}<strong class="board-heading">${board.mode==='inbound_ais'?'Likely inbound vessels':'Live harbour activity'}</strong>${rows||'<p class="board-empty">No live AIS vessels found around this terminal.</p>'}${next}<div class="resource-actions">${website}</div><p class="resource-note">${esc(board.note||'Live AIS context.')}</p>`;
    }
    box.classList.remove('hidden'); box.innerHTML=html||'<p class="board-empty">No live place context available.</p>'; bindBoardLiveLinks(box);
    focusNextLive(place,board.next_live_id);
  }

  async function selectPlace(id, pan) {
    const p=places.find(x=>x.id===id); if(!p)return;
    selectedPlaceId=id; selectedId=null; sendWatching(null); clearPlaceLink();
    $('detail-panel')?.classList.add('has-selection');
    $('detail-sidebar')?.classList.remove('is-empty');
    $('detail-title').textContent=`${placeIcons[p.place_type]||'📍'} ${p.name}`;
    $('detail-subtitle').textContent=p.subtitle || ({rail_station:'Rail station',bus_stop:'Bus stop',ferry_terminal:'Ferry terminal',rain_gauge:'Rain gauge'}[p.place_type]||'Smart place');
    $('detail-fields').innerHTML=[field('Type',(p.place_type||'').replaceAll('_',' ')),field('Code',p.crs||p.atco_code||p.naptan_code),field('Operator',p.operator),field('Source',p.source),field('Latitude',Number(p.lat).toFixed(5)),field('Longitude',Number(p.lon).toFixed(5))].join('');
    $('follow').classList.add('hidden'); clearCoverage(); clearResources();
    const resources=$('detail-resources'); if(resources){
      const placeCam=p.place_type==='ferry_terminal'?nearbyWebcamsUrl(p,12):null;
      resources.classList.remove('hidden');
      resources.innerHTML=`<small>LOCATION VIEW</small><div class="resource-actions"><a class="resource-link" target="_blank" rel="noopener" href="${esc(streetViewUrl(p))}">Google Street View ↗</a><a class="resource-link" target="_blank" rel="noopener" href="${esc(googleMapsUrl(p))}">Google Maps ↗</a>${placeCam?`<a class="resource-link" target="_blank" rel="noopener" href="${esc(placeCam)}">Nearby webcams ↗</a>`:''}</div><p class="resource-note">Fixed-place views use the actual map location. No generic stock photo or vessel-name webcam search is substituted.</p>`;
    }
    for(const id2 of ['detail-insight','detail-photo','detail-related','detail-audio']){const el=$(id2);if(el){el.classList.add('hidden');el.innerHTML='';}}
    const box=$('detail-placeboard'); box.classList.remove('hidden'); box.innerHTML='<small>SMART PLACE</small><p class="coverage-loading">Loading live board/context…</p>';
    if(pan) map.panTo([p.lat,p.lon],{animate:true});
    try { const r=await fetch(`/api/places/${encodeURIComponent(p.id)}/board?limit=10`,{cache:'no-store'}); if(!r.ok)throw new Error('board unavailable'); const payload=await r.json(); if(selectedPlaceId===id)renderPlaceBoard(p,payload); }
    catch { if(selectedPlaceId===id) box.innerHTML='<small>SMART PLACE</small><p class="board-empty">Live board unavailable right now. The place remains mapped.</p>'; }
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
    return [a.name, a.subtitle, a.operator, a.route, a.origin, a.destination, a.status, a.headcode,
      a.train_uid, a.vehicle_ref, a.journey_ref, a.next_location, a.category, a.road, a.direction,
      a.reason, a.source].filter(Boolean).join(' ').toLowerCase();
  }

  function inNearby(a) {
    if (!nearbyState.active || nearbyState.lat == null || nearbyState.lon == null) return true;
    return distanceKm(nearbyState.lat, nearbyState.lon, Number(a.lat), Number(a.lon)) <= nearbyState.radiusMiles * 1.609344;
  }

  function layerTypeMatch(a, type) {
    if (type === 'all') return true;
    if (type === 'useful') return ['train','bus','road','incident','weather','flood'].includes(a.type);
    if (type === 'transport') return ['train','bus'].includes(a.type);
    if (type === 'cool') return coolInfo(a).score >= 45;
    if (type === 'cool-air') return ['aircraft','sar'].includes(a.type) && coolInfo(a).score >= 45;
    if (type === 'cool-rail') return a.type === 'train' && coolInfo(a).score >= 45;
    if (type === 'cool-sea') return ['vessel','rescue'].includes(a.type) && coolInfo(a).score >= 45;
    if (type === 'cool-rescue') return ['sar','rescue'].includes(a.type) && coolInfo(a).score >= 45;
    // Planned roadworks and unplanned incidents remain deliberately separate.
    return a.type === type;
  }

  function filteredActivities() {
    return activities.filter(a => {
      if (!layerTypeMatch(a, selectedType)) return false;
      if (!inNearby(a)) return false;
      if (!searchText) return true;
      return searchable(a).includes(searchText);
    });
  }

  function renderMap() {
    const shown = new Set(filteredActivities().map(a => a.id));
    for (const [id, marker] of markers.entries()) {
      if (!shown.has(id)) { markerGroup.removeLayer(marker); markers.delete(id); }
    }
    for (const a of filteredActivities()) {
      let marker = markers.get(a.id);
      if (!marker) {
        marker = L.marker([a.lat, a.lon], { icon: markerIcon(a), riseOnHover: true });
        marker.bindTooltip(tooltipHtml(a), { direction: 'top', offset: [0,-13], opacity: 0.97, className: 'activity-tooltip', sticky: true });
        marker.on('click', () => selectActivity(a.id, true));
        markerGroup.addLayer(marker);
        markers.set(a.id, marker);
      } else {
        marker.setLatLng([a.lat, a.lon]);
        marker.setIcon(markerIcon(a));
        if (marker.getTooltip()) marker.setTooltipContent(tooltipHtml(a));
      }
    }
    if (typeof markerGroup.refreshClusters === 'function') markerGroup.refreshClusters();
    renderHeatmap();
  }

  function renderHeatmap() {
    if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
    if (heatMode === 'off' || typeof L.heatLayer !== 'function') return;
    let rows = activities;
    if (heatMode === 'train') rows = rows.filter(a => a.type === 'train');
    else if (heatMode === 'bus') rows = rows.filter(a => a.type === 'bus');
    else if (heatMode === 'road') rows = rows.filter(a => ['road','incident'].includes(a.type));
    else if (heatMode === 'incident') rows = rows.filter(a => a.kind === 'event' && a.type !== 'weather');
    else if (heatMode === 'weather') rows = rows.filter(a => ['weather','flood'].includes(a.type));
    const points = rows.filter(a => Number.isFinite(Number(a.lat)) && Number.isFinite(Number(a.lon))).map(a => {
      const weight = Math.max(0.25, Math.min(1, interestInfo(a).score / 35));
      return [Number(a.lat), Number(a.lon), weight];
    });
    if (!points.length) return;
    heatLayer = L.heatLayer(points, { radius: 24, blur: 20, maxZoom: 11, minOpacity: 0.28 }).addTo(map);
  }

  function renderStats() {
    const shown = filteredActivities();
    $('stat-visible').textContent = fmt(shown.length);
    $('stat-tracking').textContent = fmt(shown.reduce((s,a) => s + (a.tracking_count || 0), 0));
    $('stat-moving').textContent = fmt(shown.filter(a => a.kind !== 'event' && ['train','bus','vessel','sar','aircraft','satellite','gritter'].includes(a.type)).length);
    $('stat-interesting').textContent = fmt(shown.filter(a => currentWorkspace==='cool' ? coolInfo(a).score >= 45 : interestInfo(a).score >= 15).length);
  }

  function renderStreamZoomNote() {
    const note = $('bus-zoom-note');
    if (!note) return;
    const zoom = Number(streamMeta.zoom ?? map.getZoom());
    const messages = [];
    const busTotal = Number(streamMeta.buses_in_memory || 0);
    const busMin = Number(streamMeta.bus_min_zoom || 7);
    const busShown = Number(streamMeta.buses_streamed || 0);
    if (busTotal && (selectedType === 'all' || selectedType === 'useful' || selectedType === 'transport' || selectedType === 'bus' || heatMode === 'bus')) {
      messages.push(zoom < busMin ? `🚌 ${fmt(busTotal)} live BODS buses loaded • zoom to ${busMin}+` : `🚌 ${fmt(busShown)} buses in view • ${fmt(busTotal)} active`);
    }
    const vesselTotal = Number(streamMeta.vessels_in_memory || 0) + Number(streamMeta.sar_in_memory || 0);
    const vesselMin = Number(streamMeta.maritime_min_zoom || 6);
    const vesselShown = Number(streamMeta.vessels_streamed || 0);
    if (vesselTotal && (selectedType === 'all' || selectedType === 'vessel' || selectedType === 'sar' || selectedType === 'cool' || selectedType === 'cool-sea' || selectedType === 'cool-rescue')) {
      messages.push(zoom < vesselMin ? `🚢 ${fmt(vesselTotal)} live AIS positions loaded • zoom to ${vesselMin}+` : `🚢 ${fmt(vesselShown)} AIS positions in view • ${fmt(vesselTotal)} active`);
    }
    if (!messages.length) { note.classList.add('hidden'); return; }
    note.classList.remove('hidden');
    note.textContent = messages.join('   •   ');
  }

  function renderRanking() {
    const list = $('ranking'); if(!list) return;
    list.innerHTML = '';
    const coolMode=currentWorkspace==='cool' || String(selectedType).startsWith('cool');
    let shown = [...filteredActivities()];
    if(coolMode) shown=shown.filter(a=>coolInfo(a).score>=45);
    $('ranking-title').textContent = coolMode ? 'Cool stuff now' : 'Interesting now';
    const ranked = shown.sort((a,b) => {
      const as=coolMode?coolInfo(a).score:interestInfo(a).score, bs=coolMode?coolInfo(b).score:interestInfo(b).score;
      const delta=bs-as; return Math.abs(delta)>0.01?delta:new Date(b.last_updated)-new Date(a.last_updated);
    }).slice(0,12);
    if (!ranked.length) {
      list.innerHTML = `<p style="color:var(--muted);font-size:12px">${coolMode?'Nothing unusual is confidently identified in the current live view yet. Routine movements stay hidden here.':'Waiting for live activity.'}</p>`;
      return;
    }
    ranked.forEach((a, i) => {
      const button = document.createElement('button'); button.type='button'; button.className=`rank-item${selectedId===a.id?' selected':''}${coolMode?' cool-rank-item':''}`;
      const info=coolMode?coolInfo(a):interestInfo(a);
      const right=coolMode?`${Math.round(info.score)}★`:((a.tracking_count||0)>0?`${fmt(a.tracking_count)} 👁`:new Date(a.last_updated).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}));
      const sub=info.reasons[0] || (a.origin&&a.destination?`${a.origin} → ${a.destination}`:(a.subtitle||a.position_label));
      const lead=coolMode?coolBadgeHtml(a,true):`<span class="rank-icon">${icons[a.type]||'•'}</span>`;
      button.innerHTML=`<span class="rank-number">${i+1}</span>${lead}<span class="rank-copy"><strong>${esc(a.name)}</strong><span>${esc(sub)}</span></span><span class="rank-count">${esc(right)}</span>`;
      button.addEventListener('click',()=>selectActivity(a.id,true)); list.appendChild(button);
    });
  }

  function field(label, value) {
    if (value === undefined || value === null || value === '') return '';
    const technical = new Set(['Source','ICAO / source ref','Latitude','Longitude','Train UID','Journey ref','Tracking','Bearing','Code','Type']);
    return `<div class="detail-field${technical.has(label) ? ' is-technical' : ''}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
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
      box.innerHTML = `<small>LIVE STORIES & OFFICIAL LINKS</small><div class="coverage-actions">${links.join('')}</div>${articles ? `<div class="coverage-news">${articles}</div>` : '<p class="coverage-loading">No closely matching GDELT articles found right now. Search links still work.</p>'}`;
    } catch {
      if (requestId !== coverageRequest) return;
      box.innerHTML = '<small>LIVE STORIES & OFFICIAL LINKS</small><p class="coverage-loading">Story lookup unavailable. Official event data is unaffected.</p>';
    }
  }

  function renderInsight(a) {
    const box = $('detail-insight');
    const info = currentWorkspace==='cool' ? coolInfo(a) : interestInfo(a);
    if (!box || info.score < (currentWorkspace==='cool'?45:10)) { if (box) { box.classList.add('hidden'); box.innerHTML=''; } return; }
    box.classList.remove('hidden');
    box.innerHTML = `<strong>${currentWorkspace==='cool'?'⚡ Why this is cool':'✨ Why this is interesting now'}</strong><span>${esc(info.reasons.join(' • ') || 'Current live activity')}</span>`;
  }

  function renderRelated(a) {
    const box = $('detail-related');
    if (!box) return;
    const related = activities.filter(x => x.id !== a.id).map(x => ({x, km: distanceKm(Number(a.lat), Number(a.lon), Number(x.lat), Number(x.lon))})).filter(row => row.km <= 25).sort((m,n)=>m.km-n.km).slice(0,8);
    if (!related.length) { box.classList.add('hidden'); box.innerHTML=''; return; }
    const counts = {};
    related.forEach(({x}) => { counts[x.type] = (counts[x.type] || 0) + 1; });
    const summary = Object.entries(counts).map(([type,count])=>`${icons[type] || '•'} ${count} ${type}`).join(' • ');
    box.classList.remove('hidden');
    box.innerHTML = `<small>NEARBY AT THE SAME TIME</small><p class="related-summary">${esc(summary)} within 25 km. Proximity does not prove the events are connected.</p><div class="related-list">${related.slice(0,5).map(({x,km})=>`<button type="button" data-related-id="${esc(x.id)}"><span>${icons[x.type] || '•'} ${esc(x.name)}</span><small>${km.toFixed(1)} km</small></button>`).join('')}</div>`;
    box.querySelectorAll('[data-related-id]').forEach(btn => btn.addEventListener('click',()=>selectActivity(btn.dataset.relatedId,true)));
  }

  async function loadAudio(a) {
    const box = $('detail-audio');
    if (!box) return;
    const contextual = [];
    if (['road','incident'].includes(a.type) && Number(a.lat) >= 54.5) contextual.push(['Traffic Scotland Radio','https://www.traffic.gov.scot/traffic-scotland-radio']);
    if (['road','incident'].includes(a.type)) contextual.push(['BBC Radio 5 Live','https://www.bbc.co.uk/sounds/play/live:bbc_radio_five_live']);
    if (['vessel','rescue','sar'].includes(a.type)) {
      contextual.push(['MCA maritime safety broadcasts','https://www.gov.uk/maritime-safety-weather-and-navigation/maritime-safety-information-broadcasts']);
      contextual.push(['BBC Shipping Forecast','https://www.bbc.co.uk/programmes/b006qfvv']);
    }
    if (!contextual.length) { box.classList.add('hidden'); box.innerHTML=''; return; }
    box.classList.remove('hidden');
    box.innerHTML = `<small>OFFICIAL / PUBLIC AUDIO CONTEXT</small><div class="audio-links">${contextual.map(([l,u])=>`<a target="_blank" rel="noopener" href="${esc(safeUrl(u))}">${esc(l)} ↗</a>`).join('')}</div><p class="resource-note">Nearby public radio now lives in the left panel and follows the map centre.</p>`;
  }

  function selectActivity(id, pan) {
    selectedPlaceId = null;
    selectedId = id;
    const a = activities.find(x => x.id === id);
    if (!a) return;
    $('detail-panel')?.classList.add('has-selection');
    $('detail-sidebar')?.classList.remove('is-empty');
    sendWatching(id);
    if (pan) trackProduct('activity_selected', a.type);
    const detailCool=coolInfo(a);
    $('detail-title').innerHTML = `${currentWorkspace==='cool' && detailCool.score>=45 ? coolBadgeHtml(a,true)+' ' : ''}${icons[a.type] || '•'} ${esc(a.name)}`;
    $('detail-subtitle').textContent = a.kind === 'event' ? (a.subtitle || a.category || a.position_label) : (a.origin && a.destination ? `${a.origin} → ${a.destination}` : (a.subtitle || a.route || a.position_label));
    renderInsight(a);
    clearPhoto();
    if (['sar','aircraft'].includes(a.type)) loadAircraftInfo(a);
    renderRelated(a);
    loadAudio(a);
    loadEnvironmentContext(a);
    if (a.kind === 'event') {
      $('detail-fields').innerHTML = [field('Category', a.category), field('Status', a.status), field('Severity', a.severity), field('Road', a.road), field('Direction', a.direction), field('Lanes', a.lanes_closed), field('Reason', a.reason), field('Started', timeText(a.start_time)), field('Ends', timeText(a.end_time)), field('Location', a.position_label), field('Source', a.source), field('Updated', timeText(a.last_updated))].join('');
      $('follow').classList.remove('hidden');
      $('follow').textContent = followed.has(id) ? 'Following ✓' : 'Follow incident';
      renderContextResources(a);
      loadCoverage(a);
    } else {
      const speed = a.speed == null ? null : `${a.speed} ${a.speed_unit || ''}`.trim();
      const delay = a.delay_minutes == null ? null : (a.delay_minutes === 0 ? 'On time' : `${a.delay_minutes > 0 ? '+' : ''}${a.delay_minutes} min`);
      const bearing = a.bearing == null ? null : `${Math.round(a.bearing)}°`;
      const identityLabel = a.type === 'vessel' ? 'MMSI' : (['sar','aircraft'].includes(a.type) ? 'Registration' : 'Vehicle');
      const altitude = a.altitude_ft == null ? null : `${Math.round(Number(a.altitude_ft)).toLocaleString('en-GB')} ft`;
      $('detail-fields').innerHTML = [field('Route', a.route), field(identityLabel, a.vehicle_ref), field('Aircraft type', a.aircraft_model), field('ICAO type code', a.aircraft_type_code), field('Altitude', altitude), field('Headcode', a.headcode), field('Train UID', a.train_uid), field('Journey ref', a.journey_ref), field('Status', a.status), field('Position', a.position_label), field('Next report', a.next_location), field('Speed', speed), field('Bearing', bearing), field('Delay', delay), field('Occupancy', a.occupancy), field('Operator', a.operator), field('Source', a.source), field('ICAO / source ref', a.source_id), field('Tracking', fmt(a.tracking_count)), field('Updated', timeText(a.last_updated)), field('Latitude', Number.isFinite(Number(a.lat)) ? Number(a.lat).toFixed(5) : null), field('Longitude', Number.isFinite(Number(a.lon)) ? Number(a.lon).toFixed(5) : null)].join('');
      $('follow').classList.remove('hidden');
      $('follow').textContent = followed.has(id) ? 'Following ✓' : 'Follow activity';
      clearCoverage();
      renderContextResources(a);
    }
    if (pan) map.panTo([a.lat, a.lon], { animate: true });
    renderRanking();
  }

  const sourceMeta = {
    network_rail:{label:'TRUST movements',icon:'🚆',group:'RAIL ENGINE'}, td:{label:'TD signalling',icon:'🚦',group:'RAIL ENGINE'}, vstp:{label:'VSTP schedules',icon:'🗓️',group:'RAIL ENGINE'}, schedule:{label:'Full SCHEDULE',icon:'📘',group:'RAIL ENGINE'}, rail_reference:{label:'Rail references',icon:'🧭',group:'RAIL ENGINE'},
    bods:{label:'BODS bus GPS',icon:'🚌',group:'MOVING VEHICLES'}, bods_timetable:{label:'BODS published bus timetable',icon:'🕒',group:'SMART PLACES'}, bods_sx:{label:'Bus disruptions',icon:'🚏',group:'INCIDENTS & WARNINGS'}, aisstream:{label:'AIS maritime',icon:'🚢',group:'MOVING VEHICLES'}, adsb_sar:{label:'SAR / special aircraft',icon:'🚁',group:'RESCUE & RESPONSE'}, rnli:{label:'RNLI launches',icon:'🛟',group:'RESCUE & RESPONSE'},
    national_highways:{label:'National Highways roadworks + incidents',icon:'🚧',group:'ROAD INTELLIGENCE'}, traffic_flow:{label:'Live traffic flow',icon:'🚦',group:'ROAD INTELLIGENCE'},
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
    useful:['network_rail','bods','national_highways','met_office','ea_floods'], transport:['network_rail','bods'], train:['network_rail'], bus:['bods'], vessel:['aisstream'], rescue:['rnli'], sar:['adsb_sar'],
    cool:['network_rail','aisstream','adsb_sar','rnli'], 'cool-air':['adsb_sar'], 'cool-rail':['network_rail'], 'cool-sea':['aisstream','rnli'], 'cool-rescue':['adsb_sar','rnli'],
    road:['national_highways'], incident:['national_highways','bods_sx','london_fire'],
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
    latestSources = sources || [];
    renderLayerHealth(latestSources);
    const wrap = $('feed-status');
    if (!sources || !sources.length) { wrap.innerHTML = '<div class="feed-row"><div class="feed-copy"><p>Starting data collectors…</p></div></div>'; $('mode-pill').textContent = 'CONNECTING'; renderHealth([]); return; }
    const groups = new Map();
    for (const s of sources) { const meta = sourceMeta[s.name] || {label:(s.name || 'Feed'),icon:'•',group:'OTHER'}; if (!groups.has(meta.group)) groups.set(meta.group, []); groups.get(meta.group).push({s,meta}); }
    const html = [];
    for (const group of groupOrder) {
      const rows = groups.get(group); if (!rows?.length) continue;
      html.push(`<div class="feed-group"><div class="feed-group-title">${esc(group)}</div>`);
      for (const {s,meta} of rows) {
        const cls = s.health === 'stale' ? 'stale' : feedStatusClass(s); let setup = '';
        if (s.name === 'network_rail' && s.mode === 'setup') setup = '<p>Run <strong>configure_network_rail.bat</strong>. v1.2.1 also recovers missing Network Rail credentials from older Radar folders without overwriting AIS/TomTom/BODS keys.</p>';
        else if (s.name === 'national_highways' && s.mode === 'setup') setup = '<p><a class="inline-link" target="_blank" rel="noopener" href="https://developer.data.nationalhighways.co.uk/signup">Get free key ↗</a> then run <strong>configure_roads.bat</strong>.</p>';
        else if (s.name === 'traffic_flow' && s.mode === 'setup') setup = '<p><a class="inline-link" target="_blank" rel="noopener" href="https://developer.tomtom.com/">Get free traffic key ↗</a> then run <strong>configure_traffic.bat</strong>.</p>';
        else if (s.name === 'aisstream' && s.mode === 'setup') setup = '<p><a class="inline-link" target="_blank" rel="noopener" href="https://aisstream.io/account">Create free AISStream key ↗</a> then run <strong>configure_maritime.bat</strong>.</p>';
        else if (s.name === 'bods' && s.mode === 'error') setup = '<p>Run <strong>configure_buses.bat</strong> if your BODS key is missing. The app also repairs legacy BODS URLs automatically.</p>';
        const freshness = `<div class="feed-freshness"><span>${esc(String(s.health || s.mode).toUpperCase())}</span><span>update ${esc(relativeAge(s.age_seconds))}</span><span>${fmt(s.messages_seen || 0)} records/messages</span></div>`;
        html.push(`<div class="feed-row feed-${esc(s.name || 'other')}${s.health === 'stale' ? ' is-stale' : ''}"><span class="feed-icon" aria-hidden="true">${meta.icon}</span><div class="feed-copy"><strong><span class="feed-name">${esc(meta.label)}</span><i class="status-led ${cls}"></i><small class="feed-mode">${esc(s.health || s.mode)}</small></strong><p>${esc(s.message)}</p>${freshness}${setup}</div></div>`);
      }
      html.push('</div>');
    }
    wrap.innerHTML = html.join('');
    const trafficStatus = sources.find(s => s.name === 'traffic_flow');
    trafficConfigured = Boolean(trafficStatus && trafficStatus.enabled && !['setup','off'].includes(trafficStatus.mode));
    const coreNames = ['network_rail','bods','national_highways','traffic_flow','ea_floods','aisstream','rnli','adsb_sar','london_fire','bgs_earthquakes','ea_levels','wavenet','carbon_intensity','uk_air','tfl_live','gbfs','space_weather','iss','power_outages','gritters'];
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
    const issueText = problems.length ? problems.slice(0,3).map(s => `${sourceMeta[s.name]?.label || s.name}: ${s.health}`).join(' • ') : 'No stale/error feeds currently detected.';
    box.innerHTML = `<div class="health-row"><span>Core data feeds</span><strong>${totalOk}/${core.length || 0} ready</strong></div><div class="health-mini">${chips}</div><div class="health-row"><span>${esc(issueText)}</span></div>`;
  }

  function showToast(text) {
    const toast = $('watch-toast');
    if (!toast) return;
    toast.textContent = text;
    toast.classList.remove('hidden');
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(()=>toast.classList.add('hidden'), 6500);
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

  async function loadPlans() {
    if (plansPayload) return plansPayload;
    try { const r=await fetch('/api/product/plans'); plansPayload=await r.json(); } catch { plansPayload={payments_enabled:false,plans:[]}; }
    return plansPayload;
  }
  async function openPlusDialog() {
    trackProduct('radar_plus_opened','pricing');
    const dialog=$('plus-dialog'), cards=$('plan-cards'); if(!dialog||!cards)return;
    const payload=await loadPlans();
    cards.innerHTML=(payload.plans||[]).map(plan=>{
      const price=Number(plan.price_gbp_month||0);
      const actions=plan.id==='free' ? '<div class="plan-current">Current prototype core</div>' : `<button type="button" class="plan-interest" data-plan-interest="${esc(plan.id)}">I’d consider this</button>`;
      return `<article class="plan-card${plan.id==='plus'?' highlight':''}"><h3>${esc(plan.name)}</h3><div class="plan-price">${price===0?'Free':`£${price.toFixed(2)}`} ${price>0?'<span>/ month hypothesis</span>':''}</div><ul>${(plan.features||[]).map(f=>`<li>${esc(f)}</li>`).join('')}</ul>${actions}</article>`;
    }).join('');
    cards.querySelectorAll('[data-plan-interest]').forEach(btn=>btn.addEventListener('click',()=>{ const plan=btn.dataset.planInterest; trackProduct('pricing_interest',plan); btn.textContent='Interest recorded ✓'; btn.disabled=true; refreshProductSummary(true); }));
    if(typeof dialog.showModal==='function') dialog.showModal(); else dialog.setAttribute('open','');
  }
  async function refreshProductSummary(force=false) {
    const box=$('product-summary'); if(!box)return;
    try {
      const r=await fetch('/api/product/summary'); const p=await r.json();
      const events=p.events||{}, interest=p.pricing_interest||{};
      box.innerHTML=`<div class="product-kpis"><div><strong>${fmt(p.anonymous_sessions||0)}</strong><span>anonymous local sessions</span></div><div><strong>${fmt(events.activity_followed||0)}</strong><span>follow actions</span></div><div><strong>${fmt((interest.plus||0)+(interest.enthusiast||0))}</strong><span>paid-plan interest taps</span></div></div><span>No search terms or precise locations are stored by this prototype analytics layer.</span>`;
    } catch { box.textContent='Product analytics unavailable. Live map is unaffected.'; }
  }

  function loadFollowed() {
    try {
      const raw = JSON.parse(localStorage.getItem('ukRadarFollowed') || '[]');
      if (Array.isArray(raw)) raw.forEach(id => typeof id === 'string' && followed.add(id));
    } catch {}
  }
  function saveFollowed() { try { localStorage.setItem('ukRadarFollowed', JSON.stringify([...followed])); } catch {} }
  function renderMyRadar() {
    const box = $('my-radar-list');
    const badge = $('my-radar-count');
    if (!box || !badge) return;
    badge.textContent = followed.size;
    const live = activities.filter(a => followed.has(a.id)).sort((a,b)=>new Date(b.last_updated)-new Date(a.last_updated));
    if (!followed.size) { box.innerHTML = '<p class="my-radar-empty">Nothing followed yet. Select a train, incident, vessel or other live activity and tap Follow.</p>'; return; }
    if (!live.length) { box.innerHTML = `<p class="my-radar-empty">${followed.size} saved favourite${followed.size===1?'':'s'} • none are in the current live snapshot.</p>`; return; }
    box.innerHTML = live.slice(0,8).map(a => `<button type="button" class="my-radar-row" data-my-radar="${esc(a.id)}"><b>${icons[a.type] || '•'}</b><span><strong>${esc(a.name)}</strong><span>${esc(a.status || a.subtitle || 'Live')}</span></span><em>${(a.tracking_count||0) ? `${fmt(a.tracking_count)} watching` : 'LIVE'}</em></button>`).join('');
    box.querySelectorAll('[data-my-radar]').forEach(btn=>btn.addEventListener('click',()=>selectActivity(btn.dataset.myRadar,true)));
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
    box.querySelectorAll('[data-watch-delete]').forEach(btn=>btn.addEventListener('click',()=>{watchZones=watchZones.filter(x=>x.id!==btn.dataset.watchDelete); saveWatchZones(); renderWatchZones(); trackProduct('watch_zone_removed','watch-zone');}));
  }

  function checkWatchZones() {
    if (!watchZones.length) return;
    const matches = [];
    for (const z of watchZones) {
      for (const a of activities) {
        if (interestInfo(a).score < 15) continue;
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
    box.innerHTML = groups.map(([type,icon,label,list,desc]) => `<button type="button" class="nearby-summary-row" data-near-type="${type}"><span class="ns-icon">${icon}</span><span><strong>${label}</strong><span>${list.length ? esc(desc) : 'Nothing current in range'}</span></span><b>${list.length}</b></button>`).join('') || '<div class="nearby-summary-empty">Nothing live is currently mapped around this point.</div>';
    box.querySelectorAll('[data-near-type]').forEach(btn=>btn.addEventListener('click',()=>{
      selectedType=btn.dataset.nearType;
      document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x.dataset.type===selectedType));
      activateWorkspace('useful',false);
      renderMap();renderStats();renderRanking();
    }));
  }

  function renderCoolSummary() {
    const box=$('cool-summary'); if(!box)return;
    const centre=nearbyState.active?{lat:nearbyState.lat,lng:nearbyState.lon}:map.getCenter();
    const radius=(nearbyState.active?nearbyState.radiusMiles:40)*1.609344;
    const candidates=activities.map(a=>({a,c:coolInfo(a),km:distanceKm(centre.lat,centre.lng,Number(a.lat),Number(a.lon))}))
      .filter(x=>x.c.score>=45 && Number.isFinite(x.km) && x.km<=radius).sort((x,y)=>y.c.score-x.c.score || x.km-y.km);
    const cats=[['AIR','air',x=>['aircraft','sar'].includes(x.a.type)],['RAIL','rail',x=>x.a.type==='train'],['SEA','sea',x=>['vessel','rescue'].includes(x.a.type)],['RESCUE','rescue',x=>['sar','rescue'].includes(x.a.type)]];
    const counts=cats.map(([label,theme,fn])=>({label,theme,n:candidates.filter(fn).length}));
    const top=candidates[0];
    box.innerHTML=`<div class="cool-scoreboard">${counts.map(x=>`<div><span class="cool-mini-icon cool-${x.theme}">${x.theme==='air'?'▲':x.theme==='rail'?'◆':x.theme==='sea'?'⚓':'✚'}</span><strong>${x.n}</strong><small>${x.label}</small></div>`).join('')}</div>${top?`<button class="cool-hero-hit" type="button" data-cool-top="${esc(top.a.id)}"><span>${coolBadgeHtml(top.a,false)}</span><div><small>BEST SPOT RIGHT NOW • ${top.km<1?'<1':Math.round(top.km)} km away</small><strong>${esc(top.a.name)}</strong><p>${esc(top.c.reasons[0]||top.a.subtitle||'Unusual live movement')}</p></div><b>›</b></button>`:`<div class="cool-empty">Nothing unusual is confidently identified within ${Math.round(radius/1.609344)} miles right now. The app deliberately hides routine traffic rather than pretending it is special.</div>`}`;
    box.querySelector('[data-cool-top]')?.addEventListener('click',e=>selectActivity(e.currentTarget.dataset.coolTop,true));
  }

  function renderLiveTicker() {
    const host=$('live-ticker'); if(!host)return; const target=host.querySelector('.ticker-items'); if(!target)return;
    const label=host.querySelector('.ticker-label'); const coolMode=currentWorkspace==='cool';
    if(label) label.textContent=coolMode?'COOL':'LIVE';
    let rows=[...activities];
    if(coolMode) rows=rows.filter(a=>coolInfo(a).score>=45).sort((a,b)=>coolInfo(b).score-coolInfo(a).score);
    else rows=rows.filter(a=>layerTypeMatch(a,'useful')).sort((a,b)=>interestInfo(b).score-interestInfo(a).score);
    rows=rows.slice(0,6);
    if(!rows.length){target.textContent=coolMode?'No unusual live activity identified yet.':'Waiting for useful live activity…';return;}
    target.innerHTML=rows.map((a,i)=>`${i?'<span class="ticker-sep">•</span>':''}<button type="button" class="ticker-item" data-ticker-id="${esc(a.id)}">${coolMode?coolBadgeHtml(a,true):(icons[a.type]||'•')} ${esc(a.name)}${a.delay_minutes>0?` +${esc(a.delay_minutes)}m`:''}</button>`).join('');
    target.querySelectorAll('[data-ticker-id]').forEach(btn=>btn.addEventListener('click',()=>selectActivity(btn.dataset.tickerId,true)));
  }

  function activateWorkspace(name, autoSelect=true) {
    if(!['useful','cool','live','settings'].includes(name)) name='useful';
    currentWorkspace=name;
    document.body.dataset.experience=name;
    document.querySelectorAll('.workspace-tab[data-workspace]').forEach(b=>b.classList.toggle('active',b.dataset.workspace===name));
    document.querySelectorAll('[data-workspace-panel]').forEach(p=>p.classList.toggle('active',p.dataset.workspacePanel===name));
    $('workspace-shell')?.classList.remove('collapsed');
    if (autoSelect) {
      const defaults={useful:'useful',cool:'cool'};
      if(defaults[name]){
        selectedType=defaults[name];
        document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x.dataset.type===selectedType));
      }
    }
    renderMap();renderStats();renderRanking();renderNearbySummary();renderCoolSummary();renderLiveTicker();
    try{localStorage.setItem('ukRadarWorkspace',name)}catch{}
    setTimeout(()=>map.invalidateSize(),190);
  }

  function renderAll(sources) {
    renderMap(); renderStats(); renderRanking(); renderFeeds(sources); renderStreamZoomNote(); renderNearbyOverlay(); renderWatchZones(); renderMyRadar(); checkWatchZones(); renderNearbySummary(); renderCoolSummary(); renderLiveTicker();
    if (Date.now() - lastProductSummaryAt > 15000) { lastProductSummaryAt = Date.now(); refreshProductSummary(); }
    if (selectedId) { const selected = activities.find(a => a.id === selectedId); if (selected) selectActivity(selectedId, false); }
  }

  function sendWatching(activityId) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({action:'watching',activity_id:activityId || null}));
  }

  function sendViewport() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const b = map.getBounds();
    socket.send(JSON.stringify({ action: 'viewport', bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], zoom: map.getZoom() }));
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
      box.innerHTML = buttons.length ? buttons.join('') : '<div class="search-empty">No live match or map location found.</div>';
      box.querySelectorAll('[data-activity]').forEach(btn => btn.addEventListener('click',()=>{ const id=btn.dataset.activity; const a=activities.find(x=>x.id===id); if(a){ searchText=''; $('search').value=''; selectActivity(id,true); if(map.getZoom()<10) map.setZoom(10); } hideSearchResults(); }));
      box.querySelectorAll('[data-place]').forEach(btn => btn.addEventListener('click',()=>{ const id=btn.dataset.place; const row=rows.find(x=>x.id===id); if(row){ if(!places.some(p=>p.id===id)) places.push(row); renderPlaces(); searchText=''; $('search').value=''; selectPlace(id,true); if(map.getZoom()<12) map.setZoom(row.place_type==='bus_stop'?15:11); } hideSearchResults(); }));
      const locationButton=box.querySelector('[data-location]'); if(locationButton && payload.location) locationButton.addEventListener('click',()=>{ addSearchLocation(payload.location); hideSearchResults(); });
    } catch { box.innerHTML = '<div class="search-empty">Search service unavailable. Local live filtering still works.</div>'; }
  }

  function connect() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${scheme}://${location.host}/ws`);
    socket.onopen = () => { sendViewport(); sendWatching(selectedId); };
    socket.onmessage = (event) => { const payload = JSON.parse(event.data); activities = payload.activities || [...(payload.vehicles || []), ...(payload.events || [])]; streamMeta = payload.stream || {}; latestSources = payload.sources || []; renderAll(latestSources); };
    socket.onclose = () => { $('mode-pill').textContent = 'RECONNECTING'; setTimeout(connect, 1800); };
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

  map.on('click', e => inspectTrafficAt(e.latlng));
  map.on('moveend zoomend', () => { queueViewport(); fetchPlaces(); queueMapRadio(); renderNearbySummary(); });
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.filter').forEach(x => x.classList.remove('active')); button.classList.add('active'); selectedType = button.dataset.type; trackProduct('layer_selected', selectedType);
    if (selectedType === 'bus' && map.getZoom() < 7) map.setZoom(8); if (['vessel','sar'].includes(selectedType) && map.getZoom() < 6) map.setZoom(7);
    renderMap(); renderStats(); renderRanking(); renderCoolSummary(); renderLiveTicker();
    const count=activities.filter(a=>layerTypeMatch(a,selectedType)).length;
    if(!count && selectedType!=='all') {
      const names=layerSourceNames[selectedType]||[]; const st=latestSources.find(x=>names.includes(x.name));
      if(st?.mode==='setup') showToast(`${sourceMeta[st.name]?.label||st.name}: setup needed — ${st.message}`);
      else if(st?.mode==='error') showToast(`${sourceMeta[st.name]?.label||st.name}: ${st.message}`);
      else if(selectedType==='vessel' && st?.connected) showToast('AIS is connected but there are no maritime positions in the current streamed view. Pan/zoom towards the coast.');
      else if(selectedType==='sar' && st?.connected) showToast('SAR/HEMS feed is healthy but there are no qualifying aircraft in the current snapshot.');
    }
  }));
  document.querySelectorAll('.map-mode').forEach(button => button.addEventListener('click',()=>setMapMode(button.dataset.mapMode)));
  $('heat-mode').addEventListener('change', e => { heatMode = e.target.value; if (heatMode === 'bus' && map.getZoom() < 7) map.setZoom(8); renderHeatmap(); renderStreamZoomNote(); });
  $('bus-heat')?.addEventListener('click',()=>{ heatMode = heatMode === 'bus' ? 'off' : 'bus'; $('heat-mode').value=heatMode; if(heatMode==='bus' && map.getZoom()<7)map.setZoom(8); $('bus-heat').classList.toggle('active',heatMode==='bus'); renderHeatmap(); renderStreamZoomNote(); });
  $('traffic-quick')?.addEventListener('click',()=>{ const next=trafficMode==='off'?'combined':'off'; $('traffic-mode').value=next; setTrafficMode(next); $('traffic-quick').classList.toggle('active',next!=='off'); });
  $('weather-radar')?.addEventListener('click',()=>setWeatherRadar(!weatherRadarEnabled));
  $('live-radar')?.addEventListener('click',()=>setWeatherRadar(!weatherRadarEnabled));
  $('live-webcams')?.addEventListener('click',()=>{ const c=map.getCenter(); window.open(nearbyWebcamsUrl({lat:c.lat,lon:c.lng},10),'_blank','noopener'); });
  $('station-boards')?.addEventListener('click',async()=>{
    placeMode='rail'; $('place-mode').value='rail'; if(map.getZoom()<9) map.setZoom(10);
    const c=map.getCenter();
    try {
      const r=await fetch(`/api/places/nearest-rail?lat=${encodeURIComponent(c.lat)}&lon=${encodeURIComponent(c.lng)}`,{cache:'no-store'});
      if(r.ok){ const d=await r.json(); const p=d.place; if(p){ if(!places.some(x=>x.id===p.id))places.push(p); renderPlaces(); await selectPlace(p.id,true); showToast(`Opened ${p.name} live station board.`); return; } }
    } catch {}
    const rows=await fetchPlaces();
    const rail=(rows||places).filter(p=>p.place_type==='rail_station').sort((a,b)=>distanceKm(c.lat,c.lng,a.lat,a.lon)-distanceKm(c.lat,c.lng,b.lat,b.lon));
    if(rail[0]){await selectPlace(rail[0].id,true);showToast(`Opened ${rail[0].name} live station board.`);}else showToast('No rail station metadata is ready yet. If this is a fresh install, configure TomTom or let the station cache finish syncing.');
  });
  $('map-radio-refresh')?.addEventListener('click',()=>loadMapRadio(true));
  $('detail-close')?.addEventListener('click',()=>{ $('detail-panel')?.classList.remove('show-technical'); const tt=$('detail-tech-toggle'); if(tt)tt.textContent='Technical details'; selectedId=null; selectedPlaceId=null; clearPlaceLink(); sendWatching(null); $('detail-sidebar')?.classList.add('is-empty'); $('detail-panel')?.classList.remove('has-selection'); renderRanking(); });
  $('traffic-mode').addEventListener('change', e => { setTrafficMode(e.target.value); });
  $('place-mode').addEventListener('change', e => { placeMode=e.target.value || 'all'; trackProduct('smart_places_selected', placeMode); fetchPlaces(); });
  $('near-radius').addEventListener('change', e => { nearbyState.radiusMiles = Number(e.target.value || 10); if (nearbyState.active) { renderNearbyOverlay(); renderMap(); renderStats(); renderRanking(); } });
  $('near-me').addEventListener('click', () => {
    if (!navigator.geolocation) { showToast('Location is not available in this browser. Use Map centre instead.'); return; }
    navigator.geolocation.getCurrentPosition(pos => { setNearby(pos.coords.latitude,pos.coords.longitude,'device'); trackProduct('nearby_used','device'); map.setView([pos.coords.latitude,pos.coords.longitude], 10); }, () => showToast('Location permission was not granted. Use Map centre instead.'), {enableHighAccuracy:false,timeout:8000,maximumAge:300000});
  });
  $('near-centre').addEventListener('click',()=>{ const c=map.getCenter(); setNearby(c.lat,c.lng,'centre'); trackProduct('nearby_used','map-centre'); });
  $('near-clear').addEventListener('click',()=>{ nearbyState.active=false; renderNearbyOverlay(); renderMap(); renderStats(); renderRanking(); });
  $('add-watch-zone').addEventListener('click',()=>{ const c=map.getCenter(); const radius=Number($('near-radius').value || 10); const suggested=`Watch zone ${watchZones.length+1}`; const name=(window.prompt('Name this live watch zone', suggested) || '').trim(); if(!name)return; watchZones.push({id:`zone-${Date.now()}`,name,lat:c.lat,lon:c.lng,radiusMiles:radius}); saveWatchZones(); renderWatchZones(); trackProduct('watch_zone_added','watch-zone'); showToast(`${name} saved • ${radius} mile radius`); });
  $('search').addEventListener('input', (e) => { searchText = e.target.value.trim().toLowerCase(); renderMap(); renderStats(); renderRanking(); if (!searchText) hideSearchResults(); });
  $('search').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } });
  $('search-go').addEventListener('click', runSearch);
  document.addEventListener('click', (e) => { if (!e.target.closest('.search-shell')) hideSearchResults(); });
  $('recenter').addEventListener('click', () => { if(searchMarker){map.removeLayer(searchMarker);searchMarker=null;} map.setView([54.1, -2.5], 6); });
  $('follow').addEventListener('click', () => {
    if (!selectedId) return;
    const wasFollowing = followed.has(selectedId);
    if (wasFollowing) followed.delete(selectedId); else followed.add(selectedId);
    saveFollowed(); renderMyRadar();
    const selected = activities.find(a => a.id === selectedId);
    $('follow').textContent = followed.has(selectedId) ? 'Following ✓' : (selected?.kind === 'event' ? 'Follow incident' : 'Follow activity');
    trackProduct(wasFollowing ? 'activity_unfollowed' : 'activity_followed', selected?.type || 'other');
  });

  document.querySelectorAll('.workspace-tab[data-workspace]').forEach(btn=>btn.addEventListener('click',()=>activateWorkspace(btn.dataset.workspace,true)));
  $('workspace-collapse')?.addEventListener('click',()=>{ $('workspace-shell')?.classList.toggle('collapsed'); setTimeout(()=>map.invalidateSize(),190); });
  $('detail-tech-toggle')?.addEventListener('click',()=>{ const panel=$('detail-panel'); if(!panel)return; panel.classList.toggle('show-technical'); $('detail-tech-toggle').textContent=panel.classList.contains('show-technical')?'Hide technical details':'Technical details'; });
  try { activateWorkspace(localStorage.getItem('ukRadarWorkspace') || 'useful', false); } catch { activateWorkspace('useful', false); }

  $('radar-plus')?.addEventListener('click',openPlusDialog);
  $('open-plus-lab')?.addEventListener('click',openPlusDialog);
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferredInstallPrompt=e; $('install-app')?.classList.remove('hidden'); trackProduct('pwa_install_prompted','pwa'); });
  $('install-app')?.addEventListener('click', async()=>{ if(!deferredInstallPrompt)return; deferredInstallPrompt.prompt(); try { await deferredInstallPrompt.userChoice; } catch {} deferredInstallPrompt=null; $('install-app')?.classList.add('hidden'); });
  window.addEventListener('appinstalled',()=>trackProduct('pwa_installed','pwa'));
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/service-worker.js').catch(()=>{});
  trackProduct('app_open','web');
  refreshProductSummary();

  loadFollowed();
  loadWatchZones();
  renderWatchZones();
  try { setMapMode(localStorage.getItem('ukRadarMapMode') || 'standard'); } catch { setMapMode('standard'); }
  try { setTrafficMode(localStorage.getItem('ukRadarTrafficMode') || 'off', true); } catch { setTrafficMode('off', true); }
  try { if(localStorage.getItem('ukRadarWeatherRadar')==='on') setWeatherRadar(true,true); } catch {}
  connect();
  setTimeout(fetchPlaces, 350);
  setTimeout(()=>loadMapRadio(true), 500);
})();
