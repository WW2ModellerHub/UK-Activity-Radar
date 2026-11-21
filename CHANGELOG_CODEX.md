# Codex stabilization changelog

## 2026-11-21 — town pages

- /towns and /town/{name} for 52 towns and cities (Hinckley, Nuneaton,
  Coventry, Leicester … Plymouth), each anchored on its main station: next
  trains with lateness, buses moving within 5 km and their routes,
  roadworks and incidents within 10 km, the named railway lines through it,
  stations within 8 km, the nearest airport, and a link that opens the map
  there with trains on. Cached 2 minutes. Sitemap, header and Browse links.

## 2026-11-21 — line pages on real track; train panels link their line; league table

- Each named line is routed once over the physical railway graph between
  its stations (straight hop only where a pair cannot be routed), warmed in
  the background 90 s after start, with a grid index. A train counts as on
  the line only within 300 m of that track, in order along it.
- Train panels add "On the West Coast Main Line now." with a link, from the
  new /api/activity/{id}/lines (only lines already routed, so a tap is
  never slowed).
- /operators/league-table: every operator ranked by the latest official
  punctuality, with within-3-minutes, cancellations and the annual average;
  linked from /operators and in the sitemap.

## 2026-11-21 — station board: timetable first

- The departures/arrivals list is now at the top of a station panel, with
  the live snapshot (counts, punctuality bar, destinations) below it.
- "0% on time" from a single train: the percentage now needs at least three
  trains with a known time; the counts stay.
- Station titles are tidied ("HINCKLEY" → "Hinckley").

## 2026-11-21 — airports open in the app; links keep their place

- Tapping an airport no longer sends you to a separate page: the map
  centres on it with the planes around it (the "above me" lookup, centred on
  the airport, refreshing every 12 s), and the panel lists them as arriving,
  leaving, on the ground and overhead, each opening its full panel. The web
  page stays for search engines; its map button opens /?airport=BHX, the
  same in-app view.
- Links that place the map (?airport, ?lat/lon, ?place, ?open) are no longer
  undone a moment later by the startup "centre on my location".

## 2026-11-21 — aircraft photos fall back; airports on the map; links to pages

- Aircraft photos: an exact Planespotters photo first; with none, an
  example of the type (airline colours where known, labelled as an
  example) instead of nothing; and if the exact photo fails to load, the
  example is fetched. The app sends type, callsign and airline again.
- Airports: with the Aircraft layer on, the 13 airports with pages show as
  code tags (EMA, BHX…); tapping one opens a panel linking to its live
  page. New /api/airports.
- Station panels link to the station page; More has a Browse section for
  stations, railway lines, airports, operators and daily finds.

## 2026-11-21 — the radar keeps a daily record

- New history store (DATA_DIR/history.sqlite, kept 400 days), sampled every
  5 minutes: one row per train per day (latest and worst lateness,
  cancelled) and one per unusual find per day. Only what was observed.
- Train panel: "Usually: on time on 9 of the last 12 days we saw it, late on
  2 (about 4 min late on average)" once a service has 3+ earlier days.
- Discover archive: /discover-archive and /discover/YYYY-MM-DD, each day's
  steam and test trains, light engines and military and rescue aircraft,
  grouped, with the place and times seen. Days with 3+ finds go in the
  sitemap.

## 2026-11-21 — airport pages

- /airports and /airport/{name}-{iata} for 13 UK airports: aircraft
  arriving, leaving, on the ground and passing within 12 nm right now, with
  routes, types and heights, aircraft types seen, runways, and a link that
  opens the live map there with planes on. Built from live ADS-B only: a
  flight counts as arriving or leaving when its published route names the
  airport, or when it is low and descending or climbing close by. The page
  says it is not an official arrivals board. Cached 60 s per airport.
- Map links: /?lat=..&lon=..&zoom=..&layer=aircraft opens the map there with
  that layer on.

## 2026-11-21 — official ORR statistics

- Station pages: "How busy is X?" with official yearly passenger entries and
  exits for the last ten years (ORR table 1415, to March 2026), national
  rank, change on the year before, level against 2019-20, interchanges, and
  a bar chart. 2,595 stations.
- Operator pages: "Official punctuality" for the latest four-week railway
  period (on time to the minute, within 3 minutes, cancellations), against
  Great Britain overall, the annual average and a 13-period trend (ORR
  tables 3138 and 3124). Thameslink, Southern, Great Northern and Gatwick
  Express show Govia Thameslink Railway's figures and say so.
- Bundled as JSON under app/assets; refresh scripts in scripts/orr.
  Credited on the data licences page (Open Government Licence v3.0).

## 2026-11-21 — railway line pages

- New /railway-lines and /railway-line/{name} pages for 12 named lines
  (West Coast, East Coast, Midland, Great Western, Settle–Carlisle,
  Chiltern, Brighton, South Western, Birmingham–Leicester,
  Coventry–Nuneaton, Cross City, Cross Country): stations in order linked to
  station pages, live trains within 1 km of the line in line order with
  delays, a line health label, operators seen, and a link to the live map.
  Station names are matched to the real directory (all 128 checked against
  the live site); unknown names are left out, never guessed. Added to the
  sitemap and the page header.

## 2026-11-17 — aircraft overhead provider and quota repair

- Found the production failure behind the empty “aircraft above you” view:
  the anonymous ADSB.lol allowance was being exhausted by a 12-second
  background poll, after which Render received HTTP 429 for every nearby
  lookup.
- Added keyed FlyItalyADSB support as the primary hosted point feed, with
  ADSB.lol (and its optional key) as a fallback. Credentials stay server-side;
  the map and data-licence page show the required provider attribution.
- Identical nearby lookups are now coalesced, successful results are shared for
  30 seconds, and ordinary aircraft from the rotating UK snapshots are retained
  for a degraded response instead of disappearing when a provider is slow.
- Anonymous background polling now defaults to a quota-safe six minutes; keyed
  providers get a faster cadence. Explicit configuration still overrides it.

## 2026-11-17 — bus photos off; M&S at the source

- Bus photos are switched off (tester: "they don't work"). Commons rarely
  has the right bus; tapping a bus asks for no photo, the media routes
  refuse buses, and bus photos are no longer looked up in advance.
  Trains, planes and ships keep theirs.
- "Clivesway → M's" was made on the server: the bus feed writes "&" as "_",
  and the feed tidier turned M_S into "M's". One letter either side of "_"
  now reads as "&" there too (M&S, B&Q); St_Margaret_s is still St
  Margaret's.

## 2026-11-17 — no more atlas pages as bus photos; M&S

- Bus LC14 (Arriva Midlands 3149) showed a page of a 15th-century atlas
  PDF as its photo: the exact-vehicle search accepted any file whose text
  held "3149". It now searches photos only (filetype:bitmap), rejects PDFs,
  scans and video, and a bus or train photo must say bus/train and carry the
  fleet number as a number on its own.
- "Clivesway → M's": the bus feed writes "&" as "_", so M_S became "M's".
  One letter either side of "_" now reads as "&" (M&S, B&Q).

## 2026-11-17 — neater bus stops

- Bus stops were big green "STOP" signs on pins that piled up over each
  other. They are now small white dots with a green ring and a bus symbol,
  centred on the stop (28px tap area), clearly different from live buses
  (solid green with a route number).

## 2026-11-17 — free Radar+ codes; the map keeps its styles through a restart

- Stripe Checkout now offers a promotion-code box, so a 100%-off code made
  in Stripe gives friends, testers or reviewers Radar+ free.
- A phone that loaded the app while the server restarted drew bus labels as
  loose overlapping text: the new map-marker stylesheet returned 502 and the
  offline cache had nothing to fall back on. Fixed in the service worker:
  - Copies are taken before the response is handed to the page; taken
    after, the body was already used, so network-first files were never
    saved at all.
  - A failed (502) stylesheet or script falls back to this version's saved
    copy, then the last version's.
  - Install saves each file separately (one failure no longer saves
    nothing), and activate keeps an older copy of anything the new version
    could not fetch before dropping old caches.
  - An error page is never saved as the offline home page.
- Bus destinations show from zoom 15 (was 14) and long ones end in "…", so
  busy town centres are not a pile of overlapping words.

## 2026-11-17 — postcodes go to the postcode

- "CV11 4" went to "Vape Station CV11", a shop: the business geocoder read
  it as a name. Full postcodes and postcode districts now come from
  postcodes.io (ONS open data, cached a week), then the usual geocoder.

## 2026-11-17 — bus numbers on the map; search that finds places and routes

- Buses show their route number under the icon, and from street level
  (zoom 14) where they are going ("7 · Nuneaton"), so the right bus is found
  without tapping every icon.
- Search runs as you type (after a half-second pause), not only on Enter.
- A route number ("5", "X5", "bus 18A") finds buses on that route, nearest
  to where the map is looking; one character is enough.
- Place names: the town, street or postcode comes back first and flies the
  map there. Live matches need every word to start a word ("Market
  Bosworth" no longer finds buses at Beastmarket Hill), duplicates are
  dropped, and only useful fields are searched (it used to text-dump every
  field of up to 20,000 vehicles per search).
- Typing in search no longer hides every map marker that lacks the letters.
- Quick 502s: the app closed idle connections after 10 s while Render's
  proxy reuses them for longer; keep-alive is now 120 s. Render's health
  check uses /api/health/runtime, which needs no worker thread.
## 2026-11-17 — Planes overhead stays useful when the point feed is slow

- Fixed the production 500 behind “Nearby aircraft could not refresh” and
  removed a global request lock that made one slow lookup queue every visitor.
- Nearby aircraft now appears immediately from the live map, keeps a recent
  successful point lookup for brief outages, and falls back to retained UK
  aircraft while the precise lookup retries. The control reports progress and
  never wipes already-visible aircraft because one refresh failed.

- Added five crawlable, evidence-led aircraft pages (military, helicopters,
  police helicopters, air ambulances and “what plane is above me?”), connected
  them to real filtered map views and published them in the sitemap.
- Versioned static assets now receive an immutable one-year browser/CDN cache;
  unversioned live application assets keep their short refresh policy.

## 2026-11-16 — Radar+ box fits on computers

- With "Already have Radar+?" open, the code box and Turn on button were
  wider than the Radar+ card's column on computer screens, pushing the card
  100px past the box edge with a sideways scrollbar. Cards and the code row
  now shrink to fit.

## 2026-11-16 — cancelling Radar+ always possible; train alerts near stations

- A failed Radar+ check (the server busy for a moment) reset the visit to
  "no Radar+, payments off": every button vanished, Manage included, with no
  explanation. It now keeps what it knew, says "Couldn't check Radar+ just
  now. Trying again…" and retries every 15 seconds.
- New STRIPE_PORTAL_LOGIN_URL (Stripe's customer-portal sign-in link): when
  set, anyone without Radar+ on this device sees "Manage or cancel a
  subscription (sign in with your email)", so a subscriber can always
  cancel, from any device. Only a billing.stripe.com/p/login/ link is used.
- Train delay alerts listed bus stops as places ("Train near Atkins
  Building"); with Train chosen, only stations and general places show.

## 2026-11-16 — Hawks in Discover; each unusual train once

- Military trainers were all left out of Discover as routine. The Hawk is a
  fast jet (and the Red Arrows' aircraft), so it now has its own "Jet
  trainer" type: in Discover, the Field Guide and military spotter alerts,
  ranked below combat jets. Propeller trainers (Tutor, Prefect, Juno) stay
  out.
- One train reported by the main feed and by several signalling areas
  filled Discover three times over (3Q56 x3, 1Q15 x3, six of the ten free
  finds). Discover now keeps one per unusual headcode, the main feed's
  record first, then the freshest.

## 2026-11-16 — military aircraft say what they are; server health detail

- An aircraft with no airline (military, private) lost its type and
  registration, which only appear on the airline line: a Hawk showed as
  "JAVLN14" alone. They now sit under the callsign ("BAE Systems Hawk •
  ZB136").
- Photo searches need a word for the type; names with no number (Hawk, Puma,
  Lynx) used to give none, so no photo. Short names now count when there is
  no number.
- The live site gave 502s for about five minutes. /api/health now reports
  uptime, memory, busy worker threads and event-loop delay, and the new
  /api/health/runtime answers even when every worker thread is busy.
- Photo look-ahead runs on its own single worker, never in front of a photo
  someone has tapped for, and a second asker waits at most 3 seconds.

## 2026-11-16 — Radar+ ready for paying customers

- Radar+ on another device: a subscriber taps "Use Radar+ on another
  device" for a one-time 8-character code (10 minutes, once only, only while
  the subscription is active, stored hashed). The other device types it
  under "Already have Radar+?". This matters most on iPhone, where the Home
  Screen app (needed for alerts) does not share Safari's sign-in. Code
  guesses are limited to 10 a minute per visitor.
- Paid features carry a small "Radar+" label for free visitors once Radar+
  can be bought: Live traffic, Traffic cameras, Plan a journey, Follow on
  map, spotter alerts. Subscribers and payment-off sites see none.
- Discover over the live connection gave free visitors 3 finds while the
  list gave 10; both now give 10.
- Traffic cameras moved from the top of More to the Map tools beside Live
  traffic, and appear as a chip when Roadworks or Incidents are on.
- Terms say how Radar+ works across devices.

## 2026-11-16 — Google Tag Manager

- New `GTM_CONTAINER_ID` setting (render.yaml: GTM-N2JVR3QZ). Like GA4 it
  does nothing until `GA4_ENABLED` is true, and the container loads only
  after the visitor taps "Allow analytics".
- Consent defaults deny advertising storage, ad user data and
  personalisation; the app's own events (Radar+ checkout and the like) are
  pushed to the data layer for Custom Event triggers.
- With a container set, GA4 is not also loaded directly, so visits are not
  counted twice: put the GA4 tag inside Tag Manager.
- The security policy allows Google's tag hosts only while analytics are on,
  and still blocks inline scripts (so Tag Manager "Custom HTML" tags will not
  run).

## 2026-11-16 — faster plane photos

- Everyday planes are not kept on the server, so a tapped plane asked for
  its photo, got "not found", then asked again for an example. The phone now
  sends the type, callsign and airline in the first request and gets the
  example back in one trip.
- When the "planes near me" list is sent, the server starts finding photos
  for those types in the background, so a tapped plane's photo is usually
  ready. A tap during that search waits for it instead of searching again.
- Twice as many common airline and type pairs are looked up at start-up.
- Commons is asked only for the picture details we read (licence, author,
  description), not every camera and GPS field, for smaller, quicker replies.
- When an example photo is ready, the search for the exact vehicle may hold
  it up for 1.2 seconds at most; a slow search carries on and is remembered.

## 2026-11-16 — faster photos

- Photos came from Wikimedia at 960 px: it rounds non-standard widths (we
  asked for 520 and 640) up to its next standard size. They now use the
  standard 500 px, about a quarter of the data for a phone panel.
- Example photos for every train operator, the bus groups and common
  airline types are looked up in the background after start-up, and found
  examples are kept on disk, so deploys and restarts no longer wipe them.
  First lookups took 1.4 to 3.4 seconds; these are now ready before anyone
  asks.
- The exact-vehicle search and the example search run side by side instead
  of one after the other.
- The page opens a connection to Wikimedia's image servers as it loads;
  photos already fetched this visit are reused when a vehicle is reopened;
  panel images load at full priority instead of lazily.

## 2026-11-16 — choose what spotter alerts are about

- Tick boxes on the spotter card: steam and railtours, test and measurement
  trains, locos on their own and engineering trains, military aircraft. All
  ticked by default; changes take effect at once. Ordinary freight never
  alerts, nor do live rescues. Alerts saved before ("rail", "air") still work.

## 2026-11-16 — Discover shows everything until Radar+ is on sale

- Discover showed 3 finds to everyone (the free preview) while 50 were live,
  though Radar+ cannot be bought yet. While payments are off it now shows
  them all. Once they are on, free shows 10 (one of each kind first) with a
  "+N more finds right now with Radar+" button; Radar+ shows them all.

## 2026-11-16 — station pages can't overload the server

- The live site started failing (Render 502s, restarting) the day after
  2,677 station and operator pages went into the sitemap. Each station page
  builds a live board (a few seconds of work); crawlers fetching many at once
  can starve the single server. Now only one page is built at a time; others
  get the last copy or, if there is none, a light page without the board
  (it says departures are on the live map). Station pages are kept for 10
  minutes instead of 90 seconds.

## 2026-11-16 — alerts on iPhone need the Home Screen app, said up front

- iPhones and iPads only deliver web alerts to a site added to the Home
  Screen and opened from there. That was only said after tapping "turn on".
  Now, on an iPhone or iPad in Safari, every place that offers alerts says
  so first with the steps (Share, Add to Home Screen, open from the icon):
  the Saved tab's alerts card, the spotter card in Discover, "When should I
  leave?", journey watch, and the Radar+ window. Android and computers,
  where browser alerts work, see nothing extra.

## 2026-11-16 — Radar+: yearly, free trial, alerts at the centre

- **Yearly price.** Optional `STRIPE_PLUS_YEARLY_PRICE_ID` adds a yearly
  option next to monthly (`RADAR_PLUS_YEARLY_PRICE_DISPLAY`, default
  "£24.99/year"). Checkout takes `?period=month|year`; the chosen price is
  bound to the checkout state as before, so a session cannot be swapped
  between them. Webhooks accept either price as Radar+.
- **Free trial.** `RADAR_PLUS_TRIAL_DAYS` (default 7, 0 to turn off) is passed
  to Stripe as `trial_period_days`; "trialing" subscriptions already count
  as active. The Radar+ window says the trial and its terms up front.
- **What Radar+ is.** New `alerts_pro` feature: alerts for every saved
  train, when-to-leave, journey watch, spotter and nearby-place alerts. Free
  keeps phone alerts for one saved train. Enforced on the server when
  alerts are saved, and only once payments are switched on: while they are
  off, nothing is limited. Plan wording in the app and `/api/product/plans`
  rewritten around this.
- **Tracking.** `plus_checkout_started` (with month/year), `plus_subscribed`
  and `plus_limit_reached` (with which feature) for analytics.
- Terms updated for yearly billing and the trial (effective 16 November).

## 2026-11-15 — link previews and pages for search

- **Link previews.** A shared link (WhatsApp, iMessage, X, Facebook) now
  previews with its own picture and title: a 1200 × 630 card drawn on the
  server (`app/share_card.py`, Inter font bundled under the OFL) showing the
  train, bus or plane, where it is going, roughly where it is and whether it
  is late. Never coordinates. The rest of the site previews with a general
  card instead of the app icon. `/live/` pages stay out of search results by
  their own noindex, so robots.txt no longer blocks them (preview crawlers
  need to read them).
- **Station pages** (`/station/rugby-rug`, about 2,500): live departures,
  platforms (including "usually 6"), delays and cancellations in a readable
  table, a one-line summary, the operators there with today's status, nearby
  stations, schema.org TrainStation data and a button that opens the station
  in the live map (`/?place=rail-station:RUG`).
- **Operator pages** (`/operator/avanti-west-coast`): today's status, the
  share of trains late, the most delayed trains.
- Indexes at `/stations` and `/operators`; all are in the sitemap. Pages are
  built on the server and cached for 90 seconds, so crawlers are cheap.

## 2026-11-15 — photo and shared page tidy-ups

- Example train photos skip engines, spare power units and parts (a DRS
  freight showed a spare Class 68 engine on a pallet).
- Shared pages no longer repeat the journey under "What it is", or "Late"
  under Status when Running already says it.

## 2026-11-15 — shared pages and alerts open the thing itself

- "Open UK Activity Radar" on a shared page went to the home page, and phone
  alerts opened the shared page, so neither reached the train. The app now
  takes `/?open=<id>`, waits for the live feed, turns on that layer and
  opens the item; shared pages ("Show it on the live map") and alerts link
  there.
- Shared pages say more, in plain words: last seen, on time or late, near
  which station (trains and buses only; rescues keep the broad area), next
  stop, operator, what a loco move or special is, what the headcode means,
  and how it was seen. Feed wording such as "next berth unresolved" is gone.

## 2026-11-15 — bus photos: the right region, a real route number

- Bus 8 showed a London Routemaster on route 159: "8 December 2005" in its
  title was read as route 8. A route number now has to read as a route
  ("route 8", "service 8", "the 8 to …").
- Regional companies (Arriva Midlands, North West, North East, Yorkshire)
  are searched by their own name first, then the group's. London buses are
  red whoever runs them, so London photos are never used for buses outside
  London.

## 2026-11-15 — rain radar

- The Weather layer shows rain radar from the EUMETNET OPERA composite, which
  joins Europe's national weather radars (15 in the UK, 2 in Ireland). It is
  open under CC BY 4.0, so allowed in the commercial build with credit.
- `app/rain_radar.py` fetches the newest 15-minute frames through the Open
  Radar Data API every 5 minutes, keeps two hours, cuts each to the UK and
  Ireland, reprojects the 2 km Lambert equal-area grid to Web Mercator and
  colours it by rain rate (transparent where dry or out of range).
  `/api/weather/radar` lists frames; `/api/weather/radar/{time}.png` serves one.
- On the map: Weather → "Rain radar" (on by default) and "Play last 2
  hours"; a key shows the time, the colour scale and the credit. Credited on
  the data licences page. New dependency: h5py (radar files are ODIM HDF5).

## 2026-11-15 — alerts once, camera first, honest bus photos

- **The same closure alerted again and again.** TomTom incident ids carry a
  model version that changes every few minutes, so each refresh looked new.
  Ids now keep only the incident part, and nearby road alerts are keyed by
  what, road and place (to about a kilometre) for the day, so one closure
  listed in both directions is one alert. At most three nearby road alerts a
  day per phone, most serious first.
- **"Watch out for Closed: Closed"** now reads "Road closed near Hinckley" with
  where, impact and until when. Nearby alerts need an active closure, crash,
  lane closure or long delay; plain roadworks no longer notify.
- **Camera view waited for a location** and gave up after 8 seconds, so it
  only worked after "Above me" had found one. It now opens at once and finds
  the location while running. A "What's that?" row at the top of the Map
  panel opens it on trains, buses and aircraft without choosing a layer.
- **Bus 158 showed a photo of a 436.** A photo of the same route is looked
  for first; otherwise the operator's colours with "it may show another
  route". Photo captions drop file names and catalogue numbers.

## 2026-11-15 — phone alerts could never be switched on

- The worker was registered from `/static/`, so it only covered that folder,
  never the app page at `/`. Turning alerts on waits for a worker covering
  the page, so the button did nothing, for everyone, since alerts were built.
  The worker is now served and registered at `/service-worker.js` with
  scope `/`; the old registration is removed.
- Waiting for the worker gives up after 8 seconds with a plain reason, and
  the button shows "Turning on…" while it works.
- New test with a real worker on a local server; the other browser tests
  fake the worker, which is how this went unnoticed.

## 2026-11-15 — tab bar and the iPhone home bar

- The tab bar grows by the height of the iPhone home bar, so its icons and
  labels are neither squashed (16210) nor covered by the bar (before).
  Everything placed above it moves up with it.
- The locate button sits above the map credits instead of on top of them.

## 2026-11-15 — fixes from a morning on a real phone

- **Camera view Close** was hard to hit: labels were rebuilt on every frame,
  which swallowed taps. Labels are now moved, redraws are capped at about 15
  a second, and Close is a larger button that acts on first touch.
- **Pinching the interface zoomed the whole page** on an iPhone and was hard
  to undo. The page no longer pinch-zooms; the map still does.
- **A saved plane showed as "none running"**. Everything saved is now listed
  with what it was; things not on the map say "Not in range right now" or
  "Not running right now", and each has a remove button.
- **Alerts on or off was unclear.** The Saved tab now states "Alerts are on
  for this phone" with the list of what will be sent, or "Alerts are off.
  Saving a station does not turn them on". The button names the place it
  acts on. If turning on fails, the reason stays on the page (blocked
  notifications with where to allow them, or the server's refusal).
- **Tapping a saved place** now chooses it for the departures and alerts
  below instead of opening a separate timetable; a Map button opens it on
  the map. Station names are no longer in capitals.
- The "Aircraft within 25 miles" bubble no longer covers the options row.

## 2026-11-15 — camera view shows what you opened it from

- Opened from Trains it shows trains, from Buses buses, from Aircraft
  aircraft (it showed everything). Three switches in the view add or remove
  the others; aircraft are only looked up when wanted.

## 2026-11-15 — camera view, point at sky, platform predictor

- **Camera view** (in the Trains, Buses and Aircraft options): the phone held
  upright shows the camera with a label over each aircraft, train (within
  3 km) and bus (within 1 km) in that direction; tap a label to open it.
  Labels are placed from live positions, the phone's location, compass and
  tilt. Nothing is recognised from the picture. New `/api/around`; the
  Permissions-Policy header now allows the camera for this site only.
- **Point at sky** (Aircraft options): no camera needed; point the top of the
  phone at an aircraft and it is named, or you are told where the nearest is.
- **Platform predictor**: every platform reported by the live feed is
  remembered per train and station (`app/platform_history.py`, on the data
  disk, 120 days). Once three or more days mostly agree, boards and "Yours"
  say "usually platform 6 (8 of last 10)" until the real one is announced.
- Fix: the line status and delay caches returned nothing for the first
  seconds after the server started.

## 2026-11-14 — journey watch and Leave now

- **Journey watch**: after planning a drive, "Watch this drive" with a
  leaving time. On weekdays, in the 45 minutes before that time, the phone
  is alerted if a crash, closure or long delay is on the route. Silence
  means it looks clear. Live incidents are fetched along the route for this.
- **Leave now**: in a train's panel, "When should I leave?" — choose the stop
  you board at and the minutes to get there. One alert about five minutes
  before you need to set off, moved later if the train is running late, with
  the platform when known. The train is saved too, so a cancellation or
  platform change also reaches the phone.
- Line status grading recalibrated against a normal evening: "severe" now
  needs half the trains late (or 30% and 20+ minutes).

## 2026-11-14 — line status and spotter alerts

- **Line status** (Trains → Line status): every passenger operator graded
  good, minor or severe from the live trains (share running 5+ min late,
  how late, cancellations), worst first. Tap one for its latest trains.
  Operators with fewer than four trains reporting are shown as unknown.
- **Spotter alerts** (top of Discover): a phone alert when a Discover find
  (steam, charter, military aircraft) is within 5, 10 or 25 miles of where it
  was switched on, 7am to 10pm. Live rescues never alert.

## 2026-11-14 — opens on your own things

- New "Yours" section at the top of the Map panel: each saved station or
  stop with its next departure (time, destination, platform, on time /
  expected / cancelled, and the one after), and each saved train with its
  state (late, cancelled, platform change). Refreshes every minute; tap a
  row to open it.
- On a phone the app used to open on a bare map with the panel closed. With
  saved things to show, it now opens with the panel up and "Yours" first.

## 2026-11-14 — Discover is for unusual things

- A 1Z train run by an everyday passenger operator (Southeastern, GWR…) is an
  extra or relief service: it is labelled "Extra" and no longer ranks as a
  Discover find. 1Z trains from charter firms or unknown operators still do.

## 2026-11-14 — why a train is "Special"

- A train labelled Special now says why: a Z in the headcode (1Z64) marks a
  one-off working not in the regular timetable, usually an extra or relief
  train on a normal operator's route, sometimes a charter. Loco moves explain
  themselves too.
- The train panel showed two identical Share buttons; there is one now.

## 2026-11-14 — train badges sit upright

- The live map layer turned every icon to its direction of travel, so the
  square train badges sat at odd angles. Only aircraft turn now.

## 2026-11-14 — phone alerts actually switch on

- Alerts were built but dormant on the hosted site: they waited for Web Push
  keys to be pasted into the host settings, so the app said "Alerts are not
  available yet". The server now makes its own key pair on first start and
  keeps it on the data disk (`app/push_keys.py`); `render.yaml` turns
  `PUSH_ALERTS_ENABLED` on. Supplied `VAPID_*` values still win.
- Saving a train turns phone alerts on for it in the same tap (after the
  browser's permission prompt): late, cancelled, platform change. No place or
  hours to choose first.
- "Send a test alert" proves a device receives them; an expired registration
  is switched off and says so.
- iPhone: says to add the app to the Home Screen first, which iOS requires.

## 2026-11-14 — photos for trains, buses and planes

- Trains only looked for a photo when the exact unit number was known (almost
  never), buses only by fleet number, and everyday planes not at all. Now, when
  there is no photo of the exact vehicle, the panel shows an example in the
  operator's colours: a GWR Class 800, a Stagecoach bus, a Ryanair 737.
  Each is labelled "Example of … — not this exact one".
- A photo must mention both the operator and the vehicle type, and have a
  licence that allows commercial use; an unknown operator gets no guess.
  Steam and railtour specials never get an everyday train photo.
- New `/api/media/reference` serves planes the server does not keep.
- Example photos show the outside of the vehicle (no interiors, cabs or
  logos), and a Commons rate limit is retried once after a short wait.

## 2026-11-14 — options under each layer

- A second chip row appears under the layer chips with options for the layer
  switched on last: Trains → Delays, Late only, Near me, Nearest station;
  Buses → Next bus, Bus stops, Disruptions; Aircraft → Above me; Roadworks and
  Incidents → My journey, Live traffic. Delays moved here from the main row.
- "Late only" keeps trains 5 min or more late and cancellations on the map.
- "Disruptions" lists bus stop closures and diversions in view.

## 2026-11-14 — layers on the map, answers in the Map panel

- The chip row under search is layer switches only again (Trains, Buses,
  Aircraft…). The four everyday answers moved to the top of the Map panel as
  "Near you": Planes above you, Trains near you, Your next bus, My journey.
- On a phone the Map tab always closed the panel, and with the bar gone
  nothing opened it. Now: arriving from another tab shows the map; tapping
  Map while on the map opens its panel; tapping again closes it.

## 2026-11-14 — one control row

- Too many controls on the phone map (search, layer chips, answer bar, tabs
  and two round buttons). The four answers (Planes, Trains near, Bus,
  Journey) now lead the chip row under search, followed by a divider and the
  layer switches; the separate answer bar and the round layers button are
  gone, and the locate button sits just above the tabs. All four answers fit
  a 390 px phone without scrolling; chips are 40 px tall on phones.

## 2026-11-14 — search shows roads in plain words

- Search results for roadworks and incidents used the raw feed title
  ("M40 — road Maintenance … planned"). They now use the same plain title and
  facts as the panel ("Lane closure · M40 — M40 southbound between J11 and
  J10 · 2 lanes closed · Until 06:00"), and only current ones are listed.

## 2026-11-14 — live incidents (the Incidents layer was empty)

- The Incidents layer showed nothing: National Highways' free closures feed
  has planned works and rare emergency closures but no crashes, breakdowns
  or queues, and the bus-stop closures that used to fill the layer had been
  moved to buses.
- New `app/traffic_incidents.py`: TomTom Incident Details for the area on
  screen when Incidents or Roadworks is on, in half-degree cells cached for
  two minutes, at most 12 cells per view (zoom in from a national view), and
  a daily request cap (`TOMTOM_INCIDENT_DAILY_LIMIT`, default 2000). Each
  incident carries its type (crash, queue, broken-down vehicle, closure),
  delay, queue length, stretch ("J14 Milton Keynes to J15 Northampton") and
  description, so the panel says e.g. "Queueing traffic · M1 — Delays of
  about 15 min · Queue of 3.2 km — Started 30 min ago".
- The Incidents switch no longer turns on TomTom's picture-only incident
  tiles (not clickable, Radar+ only); it hints to zoom in when needed.

## 2026-11-14 — road titles from live data

- Checked live National Highways records after deploy: real works times
  ("From 23:00 to tomorrow 05:00"), lane counts and public notes come
  through. Records with lanes shut but no management type were titled
  "Roadworks · M6"; they are now "Lane closure · M6" with the works
  ("Repairs", "Construction work") as the reason.

## 2026-11-14 — incidents and roadworks overhaul; aircraft and bus fixes

**Incidents and roadworks** now say what they mean for a driver:
- New `app/road_impact.py` turns each event into plain facts: what ("Lane
  closure", "Crash", "Temporary traffic lights"), where ("M1 southbound
  between J22 and J21A"), impact ("2 lanes closed · Delays of about 20 min ·
  Queue of 2.5 km"), when ("From 20:00 to tomorrow 06:00", "Started 25 min
  ago"), why/who ("Gas works by Cadent Gas") and the operator's public
  comment. The panel and hover card lead with these; feed fields sit under
  More details; the road camera comes after.
- National Highways: `_first` took whichever field came first in the
  document, so works showed the record's creation time as their start. It
  now honours priority order. Delay, delay band, queue length, lanes
  restricted (never the total lane count), management, cause, validity
  windows and the public comment are captured.
- Street Manager works keep the promoter, location description and category.
- Only events that matter are shown: happening now or starting within 12
  hours, and (where the feed says) actually restricting traffic. Works
  running to 2027 with nothing closed, and closures weeks away, are gone.
- Bus disruptions (stop closures) are bus events, not road incidents.
- "Right now" picks the most serious current disruption within 50 km of you
  (or the map), by impact; it never picks a bus-stop closure.

**Aircraft**
- "Planes above me" stored every nearby aircraft on the server, so everyday
  light aircraft filled everyone's Aircraft layer and went stale. Only
  military, rescue and notable aircraft are shared now; flight routes for the
  others are looked up by callsign and position.
- Aircraft not heard from for 10 minutes leave the map.
- Civil helicopter types (Robinson R22/R44/R66, Airbus/Eurocopter, Leonardo,
  Bell, Sikorsky, Enstrom, Guimbal and more) get the helicopter icon.

**Buses and panels**
- A selected bus draws its route on the map (done in grey, ahead in blue);
  stop codes such as "leidtgwt" are no longer shown.
- Quick-answer lists clear any previous station board, photo or journey.
- The operator filter (it listed raw feed codes such as "Amid") is removed.

## 2026-11-14 — aircraft type matching

- A Cessna 172 (type code C172) was labelled "C-17" with the heavy military
  transport icon: the app's type rules matched "C-?17" anywhere, including
  the start of "C172". The same loose matching could turn a Reims Cessna F150
  into a fast jet (F-?15), a Hawker business jet into a Hawk, and parts of
  registrations into AWACS (E-?3). All type codes are now matched as whole
  words in the marker label, marker shape and fast-renderer icon. The server
  rules already used whole words.

## 2026-11-14 — quick answers: one tap for the everyday jobs

Walked the five things a visitor comes to do and cut the taps:

| Job | Before | Now |
|---|---|---|
| Planes above me | open sheet, scroll, Planes overhead; result was a toast | 1 tap: a list, nearest first, with routes |
| Trains going past | Trains chip, find yourself, find a train | 1 tap: trains within 5 km, nearest first, with lateness |
| Next bus | open sheet, Buses near me | 1 tap |
| Why is my train late | find it, open it; only "N min late" | the train panel says where the delay built up and whether trains nearby are late too |
| Roadworks on my journey | sheet, scroll, planner, type both places, find, read each option | 1 tap once a journey is remembered; the answer is the first line |

- **Quick answers bar** (Planes above, Trains near, Next bus, My journey) sits above
  the tabs on phones and floats at the bottom of the map on desktop. The
  Map panel's own location shortcuts are hidden; the bar replaces them.
- `/api/trains/near` lists trains near a point at their moving position.
- **Lateness history.** Each train keeps its last 12 lateness readings
  (`delay_history`); the panel's "Why is it late?" names the stretch where
  most time was lost and notes other late trains nearby. Network Rail's open
  feeds do not publish delay reasons, so the panel states what the data shows.
- **My journey** remembers from, to and mode, accepts "My location", runs
  again in one tap, and leads with "N roadworks and N incidents on this
  route" or "No roadworks or incidents on this route".

## 2026-11-14 — stabled trains, Discover rows, aircraft subtitle

- **Trains in sidings.** Six trains sat fanned out on one spot by a line:
  matched services that had finished without a terminating report stayed
  until the 45-minute cut-off. Any train with no report for 20 minutes and
  no timetable leg it is still running now leaves the map; trains between
  sparse reports on a current leg stay.
- **Discover.** Every find is one plain row: category icon, name, one-line
  description, and "New · updated 19s ago". The big upper-case category
  badges, the "NEW • LIVE INCIDENT • updated" kicker, the different-looking
  first row and the "More finds" heading are gone.
- **Aircraft subtitle.** Without a known route it is empty instead of
  repeating the registration, type and operator shown on the line below.

## 2026-11-14 — aircraft panel and stale tabs

- Aircraft panels use the compact chip row (altitude, speed, updated); the
  type is in the heading, the route is the subtitle (no repeat as a chip),
  and identifiers (registration, type code, source) sit under More details.
  The photo caption no longer repeats type and registration.
- The "LIVE TRAIL — N observed positions…" box is gone; the dashed trail on
  the map remains.
- A tab left open across a deploy kept running the old app (a user saw
  "Not published by this ADS-B feed" after it was removed). The page now
  checks the published app version when it comes back into view and every
  10 minutes, and reloads once per new version (never loops).

## 2026-11-14 — flight routes: second source and reasons

- Live check after deploy: `/api/activity/{id}/flight-route` returned
  `found: false` for airline flights near Heathrow (VIR3N, RYR10RY, AFR375,
  EXS1N), with no way to tell "no route" from "lookup failed".
- adsbdb.com (`/v0/callsign/{cs}`) is now tried when adsb.lol fails or has no
  route; its route is used only if the aircraft is within 30% + 100 km of
  the direct line between the airports.
- A not-found reply now says why (`reason`: no_published_route /
  lookup_failed / not_an_airline_callsign, plus the error for failures).

## 2026-11-14 — panel and wording clean-up

- **Removed from the panels** (code kept dormant): "Look around another place"
  (map-centre radius filter), "What's around the map centre?" (a box of
  "Nothing current in range 0"), watch zones (named with a browser prompt),
  roadside equipment layer, density overlay, the Right now Refresh button and
  every small upper-case kicker above headings.
- **Saved tab** shows only how to add a place until one is saved; next
  departures, nearby activity and travel alerts appear after. Saved trains
  are a plain section, not hidden in "Following and watch zones". Count
  badges hide at zero; the "N live near N saved areas / N relevant" counter
  is gone.
- **Plain wording** across the Map, Discover, Saved and More tabs, the Radar+
  and journey dialogs and ~35 messages (no more "My Radar", "positioned
  aircraft from feed records", "setup needed — <feed message>", "configure
  TomTom", "BEST NOW", "TRAVEL WATCH", "5 matched live", "balanced N-item
  preview"). "Aircraft above me" said 50 miles while filtering 25: it is now
  "Planes overhead — within 25 miles of you".
- **Discover descriptions** rewritten without "Identified … / movement /
  excluded from Best Spot" (e.g. "Military fast jet.", "Steam locomotive.").
- Layer switches use the line icons instead of emoji.

## 2026-11-14 — compact facts, stop lists, bus lateness, flight routes

- **Compact facts.** Train and bus panels show status, next stop and update
  time as one row of small chips (status coloured by lateness) instead of
  large boxes. "Last known" is not repeated under the heading that says it.
- **Stop lists open where the vehicle is.** Trains and buses share one stop
  list that starts at the stop before the vehicle; earlier stops sit behind
  "Show N earlier stops". Buses gain the "Bus is here" marker and late times.
- **Bus lateness.** The operator's BODS prediction is used when published;
  otherwise, when the GPS feed names the stop the bus is heading for and its
  timetable time has passed, the bus is "About N min late". Expected times
  are added to the stops ahead. The full route is now sent (trimmed to 20
  stops before the bus on very long trips).
- **Flight routes.** Airline flights show departure and arrival airports
  ("London Stansted (STN) → Dublin (DUB)") from adsb.lol's route database,
  only when it marks the route plausible for the aircraft's position;
  cached per callsign. `/api/activity/{id}/flight-route`.

## 2026-11-14 — pale base map

- Back to the pale Mapbox Light base map, now used everywhere (with or
  without trains). The app's own dark rail line layer stands out on it.

## 2026-11-14 — nearby limit pill

- "Aircraft above me" / nearby mode showed a large box mid-map ("Nearby mode
  • 25 miles around your location • 0 live matches") with no way to close it
  from the map; its off switch was in the side panel. It is now a small pill
  under the map chips ("Aircraft within 25 miles of you ×"); × turns the
  mode off and shows everything again.

## 2026-11-14 — smoothness and alignment pass

- **Live layer lined up with the map.** The fast trains/aircraft layer
  (MapLibre) was given Leaflet's zoom number, but MapLibre zooms are for
  512px tiles: every object was drawn twice as far from the screen centre as
  its true place (324 px off in the regression test), and taps on what you
  saw missed. Now one zoom level lower, aligned to within a pixel at all
  zooms; it follows drags in the same frame and runs Leaflet's zoom animation
  instead of jumping when the zoom ends. Test uses the real libraries.
- **Aircraft glide.** Every aircraft marker was rebuilt on every position
  update (its position was in the redraw signature), cancelling the glide.
  Heading now turns in place; trains no longer redraw on heading or speed
  changes they do not show.
- **Long jumps snap** (after a reconnect or returning to the tab) instead of
  sliding across the map; places and incidents no longer have a slide
  animation; "Follow on map" pans smoothly with the train.
- **Panel layout.** The close button always sits at the right edge (a hidden
  back button defeated the title's flex rule); train facts fill one row with
  no empty cell; "Live" is no longer shown as a status; the rolling stock card
  keeps traveller facts only (the dataset stays credited on Data licences);
  map chips fade at the edge when they scroll; lists share one row rhythm; the
  Map tab no longer repeats its heading (kept for screen readers).

## 2026-11-14 — disruption, line view, saved-train alerts, aircraft icons

- **Cancellations.** TRUST cancellations (and reinstatements) are now kept per
  service, including trains that never started. Boards show "Cancelled" with
  the time struck out (a part-cancelled train still arrives where it was cut
  short); the train panel shows "Cancelled from …"; a cancelled train on the
  map turns grey with a strike line for 10 minutes, then leaves.
- **Platform changes.** The platform TRUST reports is compared with the
  booked one. Boards and calling points show "Platform 5 (was 2)".
- **Saved-train alerts.** Saving a train asks for notification permission and
  alerts once per change: cancelled, late (5/15/30/60 min bands) or moved
  platform. `/api/trains/status` serves the app; with travel alerts on, the
  same alerts are pushed to the phone while the app is closed.
- **Line view.** Tapping a railway line lists the trains on that stretch
  (junction to junction, named after the stations at its ends) in order,
  with lateness. `/api/rail/line`.
- **Delays.** A "Delays" chip (shown with Trains) highlights stretches where
  trains run late and lists them; tapping one zooms there and opens its line
  view. `/api/rail/delays`.
- **Aircraft icons.** One flat set of top-down silhouettes (airliner, heavy,
  fast jet, light aircraft, helicopter, Apache, Chinook) on a white disc,
  coloured by kind, shared by the map markers and the fast renderer; the old
  dark-theme aircraft CSS overrides were removed.

## 2026-11-13 — one panel per tap

- Touch screens no longer show the floating map card when a train, bus, station or camera is tapped; the detail panel is the only view. Mouse users keep the hover card.
- The selected item no longer opens its map card automatically.
- Train hover cards show "On time" / "N min late" instead of internal signalling text such as "TD live • next berth unresolved".

## 2026-11-13 — station board, Discover clean-up, punctuality

- **Station board.** Rebuilt as one list with Departures / Arrivals tabs,
  replacing two squeezed columns. Each row shows the booked time (HH:MM, not
  "13 Nov at 21:31"), the full destination in normal case, "Platform N ·
  Operator", and one status: the expected time in red when late, "On time",
  "Cancelled", or "Live". The whole row is tappable; "View live train ›" and
  "View route ›" are gone. The server now sends the operator's full name and
  no longer passes internal TD status text to the board.
- **Discover.** One All / Air / Rail / Rescue switch with "Map on" beside it,
  then one list of finds. No longer shown: the "NEW / 0/3 Field Guide"
  chips, the AIR/RAIL/RESCUE scoreboard, the Visible/Moving/Cool now/Tracking
  box, the duplicate "Cool stuff now" ranking, the preview note and the
  category counts.
- **Removed:** the scrolling desktop ticker (it repeated names already in the
  lists). "More details" only appears when there is something more to show.
- **Punctuality.** Train markers get a corner dot: green within 1 minute,
  amber up to 5 minutes late, red beyond. There is no dot when lateness is
  unknown. Signalling-only trains now get lateness from each timetabled berth
  report (`schedule_store._lateness_minutes`), so the dot, the train panel's
  Status and station boards work beyond TRUST-reported trains.

Tests: board tabs and status, punctuality dots and signalling lateness.
Python 352 passed; browser 75 passed.

## 2026-11-13 — one base map

The map used Mapbox Light normally and switched to Mapbox Streets when trains
were on, to make railways visible. Railways are now drawn by the app's own
rail line layer, so Streets is used everywhere and the map no longer changes
style when the train layer is toggled. `rail_tile_url` is kept as `null` for
pages cached before the change.

## 2026-11-13 — calmer train map

- **Markers:** 26 px coloured squares with a thin white edge and a white
  icon. Train markers use only the operator colour: no accent border or
  glow. Heading sticks and speed tags are hidden.
- **Trains at one reporting point:** they sit 14 px apart side by side. The
  numbered badges, connector stalks and origin dot are no longer shown.
- **Stations:** 12 px navy squares. Names are in normal case, at 12 px,
  centred beneath the square, from zoom 12 (was 11), so trains standing at a
  station do not cover the name.

## 2026-11-13 — railway network on the map, one icon set

- **Railway lines.** With trains on, the national railway network is drawn
  as navy lines with a white outline, above the base map and below train
  routes and markers. It uses the bundled Network Rail track geometry that
  already routes live trains (`app/rail_lines.py`), simplified to three
  levels:
  - nationally, one ~140 KB response (`/api/rail/lines/national`);
  - zoomed in, half-degree cells (`/api/rail/lines/{level}/{cx}/{cy}`),
    cacheable for a day.

  Outlines and lines are drawn on separate canvases so junctions join
  cleanly. The layer is removed when trains are turned off.
- **Icons.** Emoji and typographic symbols are replaced by one line-icon set
  (`ICON_PATHS` in `app.js`): map markers (white icon on a coloured square),
  tooltips, lists, search results, Discover badges and markers, Field Guide,
  interests, feed status, journey planner and the panel buttons. Discover
  icons follow the server's category (rail / air / sea / rescue). Emoji
  inside sentences (toasts, the camera title, the bus zoom note, "Why this is
  cool") are removed.

Tests: `tests/test_rail_lines.py` and a browser test for the rail layer.
Browser locators that found markers by emoji now use the marker type class.
Python 351 passed; browser 74 passed.

## 2026-11-13 — cleaner detail panel

The train panel now follows the agreed design. Order: title and route,
operator and headcode, then a Status / Next / Updated row, then
Share · Follow on map · Save, then the stop timeline with the train marked.

Removed from the panel:
- the "Published train route … cyan is locked to physical track…" box;
- the inferred-unit block ("POSSIBLE … inferred from…");
- the technical pills (TD status text, power type, booked speed);
- "Stops" under each station, and the duplicated route line;
- the "JOURNEY" and "LIVE NOW" labels, the emoji in the title, and the
  data-source line;
- the "Also nearby … within 25 km" list.

Identifiers and positions stay under "More details".

Other changes:
- Text that arrives entirely in capitals is title-cased: "LEICESTER →
  GLOUCESTER" becomes "Leicester → Gloucester". Headcodes and acronyms such
  as LNER, GWR and RAF are kept.
- The train marker always appears in the timeline: at the first stop before
  departure, between stops while running, at the last stop on arrival.
- Status uses the server's lateness for signalling-only trains.
- Buttons use plain words: Follow on map, Share, Save, Save place, Save to
  Field Guide.

Tests: browser assertions that pinned the removed wording now check the
hidden route box, the timeline and the Status cell. Python 346 passed;
browser 73 passed.

## 2026-11-13 — live train timeline, finished trains, stations with trains

- **Where the train is.** `/api/activity/{id}/rail` now places the train in
  its calling pattern (`smart_places._train_progress`). Each stop has a
  `state` (passed / current / next / upcoming), its `platform`, and an
  `expected` time while the train runs late. The run gains `delay_minutes`
  (signed; negative = early) and `delay_source`. Lateness uses TRUST when
  TRUST reported last; otherwise it is the signalling report time minus the
  booked time at the current timing point. The detail panel draws this as a
  timeline: a train marker between stops (or at a station), booked times with
  red expected times, platforms, and an "N min late / early / On time" label.
- **TRUST lateness sign.** `timetable_variation` is unsigned; it is now stored
  negative when `variation_status` is EARLY.
- **Finished trains leave the map.** New `Vehicle.terminated_at`, set by TRUST
  termination or by a timetable-matched train reporting at its destination.
  Later berth steps no longer bring a terminated service back, because the
  old check relied on status text that TD rewrites. A Train Describer
  headcode that never matched a timetable leaves after 20 minutes idle
  (was 45). Map, counts, summary and Discover use the same rule
  (`state._train_finished`). Finished services are not projected onwards.
- **Stations follow the train layer.** With trains on, rail stations are
  shown in addition to any "Stations and stops" choice. They are off again
  with trains off. Stations are drawn as a flat square, with names from zoom
  11.

Tests: `tests/test_train_progress.py`, `tests/test_train_lifecycle.py` and two
browser tests (timeline, stations). Python 346 passed; browser 73 passed.

## 2026-11-13 — flat, light interface for phone and desktop

A full redesign of the application chrome, agreed from mockups. Product
features are unchanged; only layout and appearance moved.

- **New stylesheets.** `styles.css`, `refinements.css` and
  `movement-history.css` (~145 KB, dark, ~800 hard-coded colours) are
  replaced by `app.css` (flat light theme, one accent, system font) and
  `map-markers.css`, which carries the existing marker, aircraft silhouette
  and route-line rules over unchanged. It is generated by
  `scripts/extract_map_css.py`.
- **Phones (≤760 px).** A full-screen map with search and layer chips at the
  top, and a "Nearby now" bar that summarises what is on the map and opens the
  Map panel. A bottom tab bar (**Map, Discover, Saved, More**) replaces the
  side rail; Discover, Saved and More open as full screens. Details are a
  full-width sheet capped at 55% of the screen, above the tab bar. Tapping a
  tab puts the sheet away; a selected train keeps its route bar.
- **Desktop.** A left column holds search, tabs and the active panel; the
  detail panel opens on the right so a list and a selected item are visible
  together. Collapsing the column floats search and tabs as cards over a
  full-width map.
- **Renamed tabs:** Live → Map, My Radar → Saved, Network → More (Discover
  unchanged). Legal links moved from the hidden footer into More.
- **Not changed:** Google Fonts are not used, because the CSP only allows
  same-origin fonts and stylesheets. The Mapbox base map was already the light
  style.

Tests updated: three mobile browser tests pinned the old side rail (≤52 px
wide), the detail panel's 60 px left offset and the drawer height; they now
assert the bottom tab bar and full-width sheet. Three new browser tests cover
the layer chips, the "Nearby now" bar and tabs dismissing the sheet. The
deployment test now checks the new asset names and that the retired
stylesheets are gone. Python 338 passed; browser 71 passed.

Root cause of two long-standing intermittent browser failures: map moves
and layer changes send a debounced viewport request, and the test fixture
answers each with its standard snapshot. "Trains sharing one station
coordinate" and "live stream deltas" injected their own snapshot before
that answer arrived, so a late answer could overwrite it. Slower machines,
including CI's runner with its wider fallback font, lost the race every
time. Both tests now wait for viewport requests to settle first.

## 2026-11-13 — live rail feed could not keep up and was dropped by the broker

Observed on the hosted service: the Network Rail session connected, ran for
about eight minutes while handling ~20 Train Describer messages a second,
was ended by the broker, and the reconnect was refused with `AMQ339009`,
starting the 15-minute cool-down. Trains on the map were warm-start snapshot
rows up to 45+ minutes old.

Root cause: message handling ran on the STOMP socket-reader thread and cost
tens of milliseconds per berth step, on a single-CPU instance that was also
deep-copying every train once a second. The client fell behind real time
until the broker treated it as a slow consumer.

- **Reader/worker split** (`stomp_client.py`): the listener now only queues
  frames; a worker thread processes them. If Train Describer processing still
  falls behind, its oldest (superseded) frames are discarded; TRUST and VSTP
  frames are not. After an established session ends the client waits 30 s
  before reconnecting instead of 10 s.
- **No national headcode search per step** (`td.py` `_known_schedule`,
  `schedule_store.py` `schedule_calling_at`): a train already matched to a
  schedule keeps that identity while its new location is on that schedule's
  route. Only new or off-route trains run the full search.
- **Cheap readiness and lookups** (`schedule_store.py`): `ready()` no longer
  counts ~730,000 rows per message; STANOX→TIPLOC lookups are cached;
  unroutable legs are remembered for two minutes instead of being re-searched
  on every report and every maintenance pass.
- **Maintenance loop** (`td.py` `advance_live_estimates`,
  `state.py` `trains_where`): copies only trains whose leg has elapsed,
  re-checks geometry every five minutes rather than every second, and retries
  a leg with no onward timetable leg every 30 s rather than every second.
- **State reads** (`state.py`): headcode index instead of scanning every bus
  and train; shallow copies in `all()`; the estimate path is walked only when
  the confirmed point is off-screen.
- **Stale ghosts**: a train restored from the warm-start snapshot is hidden
  after 15 minutes if no live report has replaced it (was 45–90 minutes).

Measured locally (730,817-schedule database, 15,000 buses, 3,300 trains):

| Path | Before | After |
| --- | --- | --- |
| Train Describer steps handled per second | 35 | 387 |
| All-trains read (was run every second) | 359 ms | 28 ms |
| Viewport read, 800 trains (per viewer, per second) | 137 ms | 13 ms |
| Headcode lookup | 2.0 ms | 0.17 ms |

Tests: `tests/test_rail_ingest_throughput.py` (14 new).

Not changed: the 15-minute `AMQ339009` cool-down itself, and the hosted
instance size (one CPU).

## 2026-11-13 — trains no longer blink off the map or jump between reports

Root causes found and fixed:

- **Stream cap churn** (`app/state.py` `LiveState.all`, `app/main.py`
  `/ws`): when more trains were eligible than the zoom-dependent cap
  (800 below zoom 9, 1400 below zoom 10), selection was purely "most recent
  report first". Every TD berth step reshuffled the cut, so trains were
  removed from the browser and re-added moments later. Trains already on a
  viewer's map, plus the watched train, now keep their place while eligible.
- **Identity flip-flop** (`LiveState.upsert`): TD and TRUST could hold two
  records for the same dated service (UID + service date). The visible one
  alternated with whichever reported last, which the browser saw as a remove
  plus an add. A second record for a dated service is now folded into the
  first identity seen, carrying the newest report. A late (older) duplicate
  is discarded. `test_duplicate_sources_collapse_only_within_the_same_dated_service`
  was updated: it pinned the old "newest id wins" behaviour.
- **Device clock skew** (`motion-engine.js`, `app.js`): estimate windows are
  stamped on the server clock but were evaluated against `Date.now()`, so a
  fast or slow device ran trains ahead or behind, then yanked them back on the
  next report. Each websocket payload now carries `server_time_ms` and the
  motion engine applies a smoothed offset (route progress bar too).
- **Teleports when an estimate ends** (`motion-engine.js`
  `trainPresentation`): a report without a track leg made the marker jump
  straight from its animated position to the report point, and the motion
  state went stale. Short (≤1.5 km) off-path corrections now ease into place
  in under ~1.2 s; larger gaps are still placed directly so trains are never
  slid across countryside.
- **Backward jitter**: a new leg that puts the train slightly behind its drawn
  position (≤1.5 km along the track, ordinary reporting noise) now holds the
  marker until the estimate catches up instead of reversing it. Along-track
  corrections up to 30 km still ease along the railway.

Tests: `tests/test_live_state_fast_paths.py` (3 new),
`tests/test_websocket_train_stream.py` (new, websocket-level cap stability
and server clock), `e2e/motion-engine.spec.js` (3 new: clock skew, glide,
hold). Asset versions bumped (`app.js`/`motion-engine.js` v15161, shell r62).

Previously noted as timing-flaky, now fixed (see the interface redesign
entry): `e2e/radar.spec.js` tests "trains sharing one station coordinate…"
and "live stream deltas…".

## 2026-11-12 — resilient train continuity during short feed outages

- Keep a timetable-matched train visible for up to 90 minutes when its last
  confirmed TRUST/TD report passes the ordinary 45-minute live cutoff, but
  only while it still has a current, multi-point railway projection.
- Preserve the original railway timestamp and explicit estimated-position
  label, so continuity during an upstream outage is never presented as GPS or
  a fresh Network Rail report.
- Extend the durable warm-start snapshot to the same bounded horizon and roll
  elapsed timetable legs every second, eliminating the avoidable pause at a
  timing point before the next verified schedule leg appears.
- Added regressions proving a current railway projection survives a short
  broker outage while an old or completed projection still expires.

## 2026-11-12 — path-locked, high-frame-rate live movement

- Replaced the fixed 12.5-fps live-object loop with an adaptive 60/30/20-fps
  MapLibre renderer, keeping a selected train at 60 fps and coalescing map
  synchronisation to one animation frame during pan and zoom.
- Keep railway corrections on the server-validated route: a new report is
  projected onto its mapped path and eased along that path, never across a
  field or town. Unmapped trains remain honestly fixed at their confirmed
  railway report instead of receiving invented straight-line motion.
- Smooth incoming ADS-B corrections and freeze aircraft at the end of the safe
  dead-reckoning window instead of snapping them back to an old coordinate.
- Repair Leaflet train icons after marker-cluster redraws when their outer
  coincident wrapper and inner numbered badge fall out of sync, preventing
  overlapping services from becoming visually indistinguishable.
- Make the multiplexed Network Rail topics truly non-durable by omitting the
  broker client identity, with bounded exponential recovery from stale broker
  sessions, and send the required Network Rail hostname in the STOMP 1.1 host
  header instead of the invalid generic `/` virtual host. Added deterministic
  backend and browser regressions. A stale-session rejection now cools down for
  15 minutes and then exponentially up to two hours, because Network Rail's
  firewall can extend an account block when a failed client reconnects rapidly.

## 2026-11-11 — keep selected train routes visible on mobile

- Split the mobile train-detail actions into a reversible route view and a true
  close, so tapping the map no longer destroys the selected railway path.
- Replace the tall train sheet with a compact, accessible route bar that can
  restore details, refit the whole published route or close the selection.
- Add a dedicated Route control to fit the full path, with mobile-safe spacing,
  44-pixel controls and reduced-motion support.
- Added browser regression coverage proving minimize/restore preserves the
  route and that only an explicit close removes it.

## 2026-11-11 — align train routes to railway timing geography

- Fill passenger-station gaps with the cached national TIPLOC geography so a
  selected service follows its published junctions and timing points instead
  of drawing a few long station-to-station chords across the map.
- Use the same richer path for live between-report movement, keeping the marker
  on the railway-shaped timetable corridor rather than interpolating directly
  across towns and fields.
- Recover missing TD routes from the headcode, reporting location and service
  time when a train has not yet received its TRUST UID or schedule identity.
- Report mapped timing-point coverage in the selected-route panel and retain an
  honest current-leg fallback where no full timetable can be verified.

## 2026-11-11 — rail-focused map and persistent published train routes

- Switch train and station contexts from the pale general Mapbox Light map to
  the commercial Mapbox Streets style so railway infrastructure has stronger
  visual context without introducing a separate unlicensed tile provider.
- Return mapped geometry for the verified full published train schedule,
  including exact live schedule matches that have not received a public UID.
- Keep the full route highlighted in cyan whenever a train is selected, then
  overlay the current live estimated leg and progress in white and gold.
- Preserve the route when the live feed omits an estimate path, eliminating the
  detached “floating train” state for services with a verified timetable.
- Added backend and browser regressions for exact-schedule route recovery,
  rail-map context switching and persistent full-route highlighting.

## 2026-11-11 — continue live trains through railway reporting gaps

- Hand confirmed station arrivals back to the published onward schedule after
  the booked dwell, so a service no longer remains pinned at a station after it
  has departed merely because its next TRUST report is delayed.
- Carry mapped Train Describer services across short unresolved SMART berth
  gaps using the identified schedule, while retaining explicit estimate labels
  and replacing the estimate as soon as a verified railway report arrives.
- Keep temporarily omitted trains for 90 seconds to survive feed hand-offs,
  update their presentation every second, and shorten visual easing so markers
  no longer lag several seconds behind their calculated position.
- Fan trains reported at the same station or berth into individually tappable
  markers, with numbered markers and leader lines back to the true rail point.
- Added schedule, TRUST, TD and browser regressions for arrival dwell hand-off,
  unresolved berth continuity, same-position separation and marker stability.

## 2026-11-11 — anchor selected trains to their live rail leg

- Added a high-contrast selected-train rail leg beneath the marker so an
  estimated position no longer appears to float over the pale basemap.
- Separated the completed portion from the remaining leg, and added directional
  arrows aligned to the published path without obscuring train markers.
- Labelled the line as an estimate between railway reports, kept its progress in
  step with the one-second marker presentation, and removed it with the detail
  selection.
- Added browser coverage for route visibility, direction arrows, progress and
  clean-up.

## 2026-11-11 — correct signed rail progress and prevent visible dropouts

- Applied Network Rail SMART arrival/departure semantics and signed berth offsets
  to rail estimates, so a departure report starts from its corrected event time
  instead of restarting the train behind the signal report.
- Kept arrivals on their approach leg until the corrected arrival time, then
  switched departures to the following leg without preserving the wrong path.
- Made rail viewport streaming use the same moving estimate as the browser, so
  an on-screen train is not removed merely because its last confirmed reporting
  point is outside the map bounds.
- Added regressions for the Hinckley/Narborough offset pattern, arrival approach
  direction and visible-estimate streaming continuity.

## 2026-11-11 — persistent and smoother live trains

- Unified TRUST and Train Describer updates for the same scheduled service so a
  TD-first train keeps one stable map identity when TRUST activates it later.
- Converted mapped TD berth reports into continuous, explicitly estimated
  movement along the published timetable path to the next mapped timing point.
- Retained the last mapped position across unresolved signalling steps, added a
  buffered rail viewport, and delayed removal after one missing stream delta so
  transient feed gaps no longer make a marker blink off and back on.
- Added the required Network Rail Infrastructure Limited licence wording and
  linked “Powered by National Rail Enquiries” attribution.
- Added backend and Playwright regressions for TD/TRUST identity hand-off,
  unresolved berth continuity, path estimates and transient missing updates.

## 2026-11-10 — live aircraft and railway movement repair

- Changed Aircraft above me from a one-off snapshot to an automatic eight-second
  refresh, with short bounded between-report motion based on the latest ADS-B
  speed and bearing.
- Added smooth, clearly labelled train estimates between confirmed TRUST reports
  using mapped timetable timing points instead of straight lines across the map.
- Show newly activated services while their first movement report is pending and
  retain an honest last-mapped estimate when a TD berth transition is incomplete,
  preventing live trains from silently disappearing.
- Added backend and browser regressions for route-shaped rail movement, activation
  visibility, incomplete berth mapping and repeat nearby-aircraft requests.

## 2026-11-05 — preserve later passenger stops

- Applied the 40-stop journey limit after removing non-stopping railway timing
  points, preventing long services from being truncated before later calls.
- Added a regression case with 45 pass-through locations before the final
  passenger stops so routes such as Leicester to London remain complete.

## 2026-11-05 — mobile double-tap zoom

- Added predictable one-level map zoom on double tap, centred on the point the
  visitor tapped, while preserving single-tap panel dismissal.

## 2026-11-05 — tap the mobile map to dismiss panels

- Made a tap on bare map space close an open activity, station or timetable
  card and collapse the navigation panel on mobile.
- Kept the detail header and close control visible while long train timetables
  scroll, providing a second quick exit route.

## 2026-11-05 — passenger stops only in train timetables

- Removed non-stopping junctions, signals and railway timing points from train
  journey cards; the displayed list now contains passenger stops only.
- Added a browser-side guard so upstream route data explicitly labelled as a
  pass is also hidden rather than leaking back into the public timetable.

## 2026-11-05 — prevent false train journeys

- Stopped TD-only trains being joined to a national timetable using a reused
  four-character headcode alone.
- Required the live signalling location and a plausible passage time before a
  TD report can inherit a timetable identity, route or calling points.
- Show a clear unavailable explanation when no timetable identity is verified
  instead of displaying a confident but unrelated journey.

## 2026-11-05 — location-first map and smoother live trains

- Centred the first map view on the visitor's current location when browser
  permission is available, with the UK overview retained as the safe fallback.
- Replaced the old Britain reset with a persistent My location control and a
  visible current-position dot.
- Added clearly labelled three-second train progress estimates between the last
  confirmed Network Rail position and its next timed reporting point, while
  keeping confirmed source positions unchanged in the live data and details.

## 2026-11-05 — faster zoom and balanced Discover preview

- Classified Lynx, Wildcat and other named military rotorcraft as helicopters
  and replaced the generic disc with recognisable single-rotor, Apache and
  tandem-rotor Chinook map silhouettes.
- Stopped long live-position interpolation from overriding Leaflet during zoom,
  so markers now remain attached to their map coordinates during the animation.
- Balanced the three-item free Discover preview across aircraft, rail and rescue
  whenever those categories are live, while showing the real national category
  totals and explaining that Radar+ unlocks the full result set.

## 2026-11-04 — public bug reporting

- Added a prominent Report a bug action to the mobile-accessible Network menu,
  the desktop footer and public information pages.
- Added a structured GitHub issue form that requests reproduction steps, device,
  browser and optional screenshots while warning visitors not to publish private
  locations, credentials or payment information.
- Advanced the offline shell cache so existing installed clients receive the
  new support route.

## 2026-11-04 — technical SEO and consent-gated site analytics

- Added canonical, description, social-card and WebSite/WebApplication
  structured metadata to the public radar.
- Added a crawlable About page, XML sitemap and robots policy while keeping
  private API, billing and temporary shared-live URLs out of search results.
- Added environment-driven Search Console verification.
- Added optional GA4 measurement that remains completely unloaded until a
  visitor explicitly opts in, with a persistent decline choice and a control
  to revisit the decision from Network settings.
- Updated the privacy notice and deployment guide for Search Console, sitemap
  submission, GA4 configuration and consent verification.

## 2026-11-04 — distinct aircraft and bus-stop map symbols

- Replaced the shared aircraft outline with recognisable top-down airliner,
  propeller, fast-jet, heavy-transport and helicopter silhouettes, including
  visible engines, propellers, intakes and rotor details at map scale.
- Gave each aircraft class its own colour while retaining the aircraft type
  label beneath the symbol.
- Replaced the moving-bus emoji on fixed bus stops with a dedicated blue
  roadside stop sign and pole, preventing stops being mistaken for vehicles.
- Added browser assertions for bus-stop signage, airliner engines and propeller
  detail, and advanced the mobile asset cache to release 15133.

## 2026-11-03 — safe first Render deployment

- Staged Radar+ behind `BILLING_ENABLED=false` for the first hosted launch so
  missing Stripe credentials cannot abort application startup.
- Pinned the confirmed first-party origin to
  `https://uk-activity-radar.onrender.com`; Stripe remains fail-closed and is
  enabled only after the hosted URL, product, price and webhook are tested.

## 2026-11-03 — mobile location and My Places launch flow

- Removed weekly Missions from the public Discover interface and stopped the
  browser requesting mission data; discoveries remain in the private Field Guide.
- Replaced the generic location control with large `Buses near me` and
  `Aircraft above me` actions designed for first-time phone visitors.
- Added a bounded nearest-NaPTAN-stop lookup so `Buses near me` enables the bus
  layer, centres the map and opens the nearest stop's live departure board.
- Simplified My Radar into saved places, live times, nearby activity and one
  optional travel-alert card; detailed alert choices are now progressive.
- Raised the important mobile controls to 44px or larger and added a 390px
  first-visit browser journey covering location, stop saving and alerts.

## 2026-11-03 — public rail labels, current routes and bounded live state

- Replaced raw TRUST sector and unknown ATOC labels with public operator names
  or the neutral “Rail operator” fallback; numeric sector 29 now resolves to
  West Midlands Trains.
- Removed the unsupported Steam live filter and renamed the reliable 1Z view
  “Special passenger”; freight and measurement filters remain source-backed.
- Prevented a reused train UID from attaching an expired or non-running route:
  live and station-board details now use a schedule valid for today and its
  advertised day of operation.
- Replaced TRUST/SCHEDULE/VSTP jargon in customer-facing rail panels with plain
  descriptions while retaining precise source names in diagnostics.
- Corrected the nearby-aircraft promise to “aircraft received” and reports the
  number of positioned aircraft returned from the feed rather than implying
  universal radar coverage.
- Added five-minute source-aware stale-vehicle cleanup to keep long-running
  bus, aircraft, vessel and rail state bounded without changing map freshness.
- Rechecked the commercial profile: sources without a documented display basis
  remain forced off by `COMMERCIAL_MODE` and the Render Blueprint.

## 2026-11-03 — complete live train routes and warm starts

- Recent confirmed Network Rail positions now warm-start from the persistent
  disk instead of rebuilding the train map one movement at a time after every
  service restart.
- TD-only train describers are joined to today's closest location/time-matched
  SCHEDULE or VSTP run, providing operator, origin, destination and calling
  points when the authoritative timetable can identify the service.

## 2026-11-03 — pre-launch operations and payment disclosure

- Added final Radar+ price, renewal, cancellation, refund-request and Stripe
  processing disclosures to the service terms.
- Expanded privacy disclosure for billing email, customer/subscription IDs,
  secure checkout/access cookies and payment-record retention.
- Added seller name and support email as Render prompts and hard launch-readiness
  gates; legal pages render the configured public values without storing them
  as application secrets.
- Added disk-headroom, enabled-feed health and aggregate request-budget gates to
  readiness, without exposing visitor identifiers or credentials.
- Added a real-deployment Playwright smoke suite and optional twice-hourly
  GitHub Actions monitor driven by the `RADAR_BASE_URL` repository variable.
- Documented uptime, readiness, Render resource and Stripe webhook alerts.

## 2026-11-03 — Radar+ launch activation

- Armed Radar+ in the production Render Blueprint at £3.99/month.
- Kept the five deployment-specific billing values as secret Render prompts;
  no Stripe credential, site URL or token pepper is stored in source.
- Added deployment coverage that prevents billing being silently disabled or
  its required secrets being replaced with committed values.
- Updated the test-to-live Stripe checklist while retaining fail-closed startup
  and test/live webhook-mode validation.

## 2026-11-03 — production performance pass

- Deferred billing, push-alert status and Today-card requests until the
  browser's first idle slice, keeping the cold-start critical path for the map
  and live websocket.
- Stopped source-health-only websocket deltas from forcing a full marker,
  filter and heat-layer render.
- Increased the TomTom proxy's reusable HTTP connection pool and coalesced
  identical cold base-map tile requests after a server restart.
- Added browser diagnostics for deferred startup and skipped unchanged
  snapshots, plus a concurrent tile regression test.

## 2026-11-03 — visual-first live detail and personal radar cleanup

- Moved licensed aircraft/train imagery to the top of live detail, reduced the
  visible fact grid and replaced duplicate identity prose with a compact
  expandable details section.
- Made aircraft imagery a single bounded lookup and added immediate
  type-specific silhouettes, including clearer directional Apache, Chinook and
  helicopter map symbols when a suitable commercial-use photo is unavailable.
- Repaired “What’s above me?” so every returned nearby aircraft remains an
  individual visible marker until the user turns the mode off.
- Extended operator colours across train detail cards, facts and journey
  intelligence instead of limiting them to one header strip.
- Reordered My Radar around places, live relevance, passing services,
  interests and optional alerts; moved following/watch-zone controls into a
  quieter secondary section.
- Simplified Network into map options, a plain live-data summary and optional
  technical diagnostics. Disabled feeds no longer clutter the list, and the
  commercial build continues to hide maritime because AISStream is disabled.
- Reduced initial/return rendering work with smaller Leaflet tile buffering and
  cache-first static assets in the service worker.

## 2026-11-02 — commercial launch hardening

- Changed TomTom basemap caching to honour the provider's current cache
  directive instead of unconditionally advertising a 24-hour lifetime.
- Isolated bounded basemap caching from frequently refreshed traffic tiles and
  exposed non-secret cache hit/miss statistics in provider status.
- Added production Content Security Policy, opener isolation and legacy
  cross-domain policy headers without allowing remote script execution.
- Added explicit launch-readiness checks for the commercial profile, required
  provider configuration and disabled-safe billing/push configuration.
- Added public-beta service terms, linked Privacy, Terms and Data licences in
  the footer, and refreshed the offline application shell.

## 2026-11-02 — operator filters and wider rail liveries

- Added a live Operator filter for train, bus and aircraft layers, with **All
  operators** as the behaviour-preserving default.
- Added a normal Aircraft map switch so airline filters can show individual
  live aircraft without entering Cool mode.
- Expanded colour treatments for 22 passenger, charter and freight railway
  operators, including EMR, Thameslink, TfW, GBRf, DRS and West Coast
  Railways. These are original colour-led treatments and do not copy logos.
- Clear map now also resets the operator filter, preventing a hidden filter
  from making a newly enabled layer appear empty.

## 2026-11-02 — operator identity and licensed imagery

- Added colour-led identity cards for British Airways, Ryanair, easyJet, Jet2,
  TUI, Virgin Atlantic, Loganair, Wizz Air, Emirates and Lufthansa aircraft.
- Added matching treatments for Arriva, Stagecoach, First Bus, National
  Express, Go-Ahead and Transdev vehicles.
- Operator cards are derived from published operator names or standard flight
  callsigns and intentionally do not copy company logos.
- Buses with an exact vehicle reference can now use the existing conservative
  Wikimedia media lookup, with photographer and licence displayed beside the
  image.
- Exact-airframe aircraft photos and type-reference images retain visibly
  different truth labels; weak or non-commercially licensed matches remain
  hidden.

## 2026-11-02 — UI simplification and focus-map pass

- Rewrote the most visible image, discovery, search, journey and alert messages
  in passenger-facing language while preserving source and licence detail.
- Simplified Discover and Network headings, removed duplicate visible Cool
  counters and shortened Radar+ benefit copy.
- Added a one-click **Focus map** mode. It hides both panels without changing
  layer state and restores them with the same button or Escape.
- Kept provider failures and feed status in the collapsed Network view instead
  of exposing implementation terminology in normal map messages.
- Updated the offline shell revision so installed copies receive the new UI.
- Added browser regression coverage for Focus map and the revised truthful
  image and live-incident labels.

## 2026-11-02 — Cool, movement and cold-start usability pass

- Rebuilt Cool controls around four compact categories, a clear on/off switch and a one-tap “What’s above me right now?” aircraft view.
- Bundled the pinned Leaflet, marker-cluster and heat-map distributions locally with their licences, removing three render-blocking CDN dependencies from cold start.
- Reduced the polite four-region ADS-B rotation from roughly two minutes to about 48 seconds while retaining Retry-After rate-limit backoff; ordinary websocket updates now wait at most two seconds.
- Aligned aircraft symbols to their observed movement trail, removed the competing direction pointer and added smooth marker-position transitions.
- Made station-board live rows fetch and focus their exact vehicle immediately; train journeys now expose up to 40 scrollable calling points with substantially shorter, plainer wording.
- Raised journey routes above TomTom traffic, added a high-contrast route casing and a persistent Clear route control.
- Fixed search-result navigation so off-stream activities and geocoded locations focus the map directly.

## 2026-11-02 — Aircraft trails and exact train identity

- Added bounded, browser-local trails for selected aircraft and SAR movements using positions already present in the authorised viewport stream.
- Isolated movement history as renderer-neutral GeoJSON-ready state, establishing the seam needed for a later measured MapLibre/WebGL layer migration without replacing live-feed logic.
- Bundled the MIT-licensed UK Train Dataset and added strict exact-number rolling-stock enrichment. Headcodes and train UIDs are never treated as vehicle numbers.
- Added visible data provenance, licence retention, cache revisioning and automated reference tests.

## 2026-11-01 — v1.5.0 Radar Now and authentic live boards

- Upgraded the live briefing to **Radar Now**, with concise best, new and
  travel-watch cards derived from current activity.
- Added **Passing Next** for saved railway stations and bus stops. Its rows use
  the existing board data and open matched live vehicles.
- Clarified My Radar as **Smart Place Alerts** without changing its explicit
  browser-permission and safe geofencing behaviour.
- Restyled railway and bus boards as recognisable passenger displays without
  copying an operator's branding or altering source data.
- Added an isolated National Highways Digital VMS collector and parser. Only
  actual sign messages receive the motorway-sign treatment.
- Added the official Speed Managed Areas feed. Cleared, suspended, zero-speed,
  expired and future records are excluded; live metric DATEX values are shown
  as their authentic UK mph roundel.
- Added emergency telephones and strategic-road bus stops as an optional
  **Roadside network** layer under Network. The static feed polls hourly and
  never clutters the default map or masquerades as an incident.
- Isolated closures, motorway signs, managed speed and roadside features into
  four independently monitored National Highways sources.
- Shortened passenger-facing setup and provider copy; diagnostics remain under
  Network and Technical details.
- Verified with 201 Python tests, JavaScript syntax checks and 34 Chromium
  workflows.

## 2026-11-01 — boards, incident freshness and sharing

- Made exact live train and bus matches on station/stop boards visibly
  clickable. Selecting one automatically enables the required map layer,
  waits for its viewport snapshot and opens that live vehicle.
- Removed machine-style underscores from BODS origin/destination names and
  recognised `DatedVehicleJourneyRef` where operators use it to link a live
  bus to its published trip.
- Replaced GTFS-oriented failure copy with a plain explanation when an
  operator has not supplied enough data to identify a bus's next stop.
- Limited London Fire Brigade archive cards to reports from the previous 18
  hours so multi-day-old fires cannot remain presented as live incidents.
- Enabled the National Highways planned-closures feed in both the clean default
  and this local configuration; unplanned incidents remain a separate layer.
- Repaired **Share facts** and **Share live page** by keeping native sharing
  inside the original click gesture and adding clipboard/new-tab fallbacks.
- Simplified aircraft-image wording and removed the internal identity-service
  explanation from the public live-detail card.
- Current verification: 195 Python tests and all 33 Chromium browser workflows
  pass.

## 2026-11-01 — live-detail, map and routing refinement

- Reduced the visible secondary-panel refresh interval to 1.5 seconds and the
  national Cool refresh window to eight seconds, while retaining incremental
  marker updates so unchanged live objects are not rebuilt.
- Added a complete **Cool map off** state and an immediate focus marker for a
  national Cool result that has not yet arrived in the current viewport stream.
- Normalised drawer typography and rebuilt Cool ranking rows so badges, names,
  explanations and status no longer overlap at narrow widths.
- Made aircraft type explicit in live detail, including an honest unavailable
  state when the source has not identified the airframe.
- Restricted automatic train imagery to an identified vehicle or class and
  restricted vessel imagery to an exact distinctive name or MMSI match. Generic
  archive pictures are no longer substituted for unknown live objects.
- Enriched Network Rail special movements with source-backed origin,
  destination, operator, service code and calling points. Marketed railtour
  names are shown only when an authoritative source supplies one.
- Turned My Radar interests into radius-entry alerts for trains, buses, planes,
  helicopters and vessels, and made **Choose a place** automatically expose
  nearby stations and stops.
- Added traffic-aware TomTom routing for driving and TomTom routing for walking
  and cycling using the existing server-side key. Public-transport planning
  remains on the independently hosted OpenTripPlanner integration.
- Current verification: 193 Python tests and all 32 Chromium browser workflows
  pass, including a 2,500-object incremental refresh scenario.

## 2026-11-01 — engagement layer

- Added opt-in Web Push commute alerts that can wake the installed site while it
  is closed. A user chooses a saved place, driving/train mode, radius and alert
  hours; the server sends only matching, deduplicated live disruption.
- Added a persistent subscription store, strict browser-push endpoint allowlist,
  VAPID configuration, service-worker notification handling and privacy copy.
- Added **Today on the Radar**, a compact editorial briefing built only from
  current verified state: best live find, new activity and major disruption.
- Upgraded **My Radar 2.0** with saved stations/stops, saved activity interests,
  a live area summary and clear free-versus-Radar+ capacity limits.
- Added stable `/live/{activity}` public pages with identity, freshness,
  confidence and source facts. Share responses never include exact coordinates;
  rescue/SAR activity receives an additional safety warning.
- Stopped unchanged place snapshots from moving markers or refreshing clusters,
  reducing needless map work.
- Hardened the Windows launcher against two simultaneous starts: it now waits
  for a warming v1.4 server, or continues when a transient port-8000 listener
  disappears, instead of reporting a false permanent port conflict.
- Kept traini.ac out of the commercial build after verifying its repository terms;
  retained independently hosted OpenTripPlanner as the commercial routing path.
- Current verification: 190 Python tests and all 31 Chromium browser workflows pass.

## 2026-11-01 — commercial launch foundation + Radar+

- Reframed the product around **Live**, **Discover**, **My Radar** and
  **Network**, keeping the map empty by default and removing duplicate controls.
- Added secure Stripe Checkout, webhook, portal and opaque device-access-token
  infrastructure with server-side route and WebSocket entitlement checks.
- Kept core live data free while gating full Discover, live tracking, faster
  updates, journey planning, cameras, traffic and richer intelligence as Radar+.
- Added a fail-closed commercial profile and public data-licence register.
- Replaced community base-map tiles with server-proxied TomTom display tiles.
- Removed Trainiac, RainViewer radar, embedded radio, Airport-Data imagery and
  the abandoned YouTube/broadcast surface from the commercial application.
- Limited automatic Wikimedia imagery to files whose metadata explicitly permits
  commercial reuse, retaining direct creator, source and licence links.
- Added a free My Radar allowance (three follows and one watch zone) with expanded
  capacity under Radar+.
- Verification at that milestone: 183 Python tests and all 27 Chromium browser workflows
  pass.

## 2026-10-25 — transport intelligence integrations

- Added a defensive client for traini.ac's current `/v1` API to enrich
  click-driven station/train details and rail-only journeys without replacing
  Network Rail TRUST/SCHEDULE. Requests are cached, locally budgeted and honour
  provider backoff; public deployment keeps this non-commercial service off by
  default.
- Added an optional OpenTripPlanner 2 GTFS GraphQL client for real multimodal
  itineraries. An external OTP service and OSM plus GTFS/NeTEx graph are
  required; an unconfigured service returns an explicit unavailable state.
- Upgraded live-bus intelligence by retaining aimed and expected BODS times,
  calculated early/late status and prediction confidence, and by joining a
  clicked live vehicle's GTFS trip ID to its official regional stop sequence.
- Added the actual Street Manager public-open-data path: a signed AWS SNS
  callback, official topic/URL validation, British National Grid conversion,
  replay-resistant version ordering, cancellation tombstones and a durable
  SQLite snapshot. It remains disabled until the public HTTPS callback is
  registered with DfT. The V6 organisation-scoped Reporting adapter is kept
  separate and is never represented as the national public feed.
- Kept the existing National Highways Road & Lane Closures v2.0 collector as
  the deployable road source. Documented that NTIS real-time DATEX requires an
  onboarded SOAP push receiver, so the legacy URL placeholder is not exposed as
  a pretend working integration.
- Kept the OpenRailwayMap infrastructure layer visibly attributed and documented
  the hosted tile service's small-scale, non-commercial constraint.

## 2026-10-24 — local live-feed proxy recovery

- Diagnosed the simultaneous ADS-B, TomTom, BODS, AIS and public-feed failures as one inherited restricted-shell proxy, not missing or invalid API credentials.
- Added a fail-fast launcher preflight for the exact localhost discard proxy so a restricted development process can no longer masquerade as nineteen independent provider failures.
- Kept legitimate configured proxies supported; the application does not clear proxy variables or bypass them with direct retries.
- Added a non-secret health flag so `run.bat` refuses to reopen an already-running restricted instance.
- Sanitized ADSB.lol proxy errors into actionable copy without publishing proxy URLs or credentials.

## 2026-10-24 — v1.4.0 Find & Collect

- Added a trust-first discovery layer to Cool with Now, Missions and Field Guide views.
- Added a server-owned catalogue of 19 genuinely Cool collectibles, stable weekly mission definitions and validated live discovery claims.
- Claims reject stale, simulated, replayed, routine, non-collectible and active rescue/emergency activity; no coordinates are persisted in a discovery record.
- Added a private browser Field Guide with interests, new-since-last-visit context, exact-versus-type identity labels and factual share/copy summaries.
- Kept Field Guide unlocks permanently on the device while resetting mission credit each week; a previously found type must pass live server validation again before it counts in the new week.
- Kept the MVP map-only: no accounts, GPS proof, public profiles, photo uploads, leaderboards or punitive streaks.
- Kept active rescue/RNLI/SAR records in a dedicated, non-collectible context filter instead of allowing them to dominate Best Live Find, rankings or the Cool ticker.
- Added a separate OBS-ready `/broadcast` programme with rotating national,
  Best Spot, air, rail, sea, traffic and weather scenes, plus 16:9 and 9:16
  layouts and an explicit connection-watchdog slate.
- Added a read-only broadcast adapter that emits hashed display keys, sanitized
  text and deliberately coarse 12–50 km positions. Rescue/SAR, synthetic,
  stale and failed-source highlights are suppressed before reaching the browser.
- Raised undersized discovery microcopy and fixed the mobile/tablet map so it fills the viewport beneath a wrapping header.
- Added a production-oriented Render Blueprint with a single-worker 2 GB web
  service, 10 GB persistent cache disk, health check, Python version pin and prompted
  secret environment variables; no credential values are stored in source.
- Added a public-deployment analytics switch. The Render profile accepts the
  existing coarse events as no-ops and creates no analytics database until a
  consent flow and protected operator report exist.
- Documented measured first-boot memory/storage needs, full-live hosting cost,
  provider quota/licensing gates, single-process constraints and launch checks
  in `DEPLOYMENT.md`.
- Added public-mode request budgets, disabled bulk/debug routes, recurring rail
  cache refreshes, age-based BODS timetable refresh and resilient Windows
  SQLite handoff for the national schedule build.
- Verified the promoted national rail index with SQLite quick-check and 721,875
  schedules; the restarted live service reports the schedule cache ready.
- Verification: 139 Python tests and 34 Chromium browser workflows pass.

## 2026-10-16 — v1.3.2 Visuals + Reliability

- Repaired every density mode by including its required activity types in the viewport subscription; All activity requests the complete pool and Off returns to the user's selected Useful layers.
- Added loading, live-point-count and no-data feedback for density overlays, plus deterministic browser coverage for rendering, switching and clearing heat layers.
- Added conservative reference imagery for server-classified steam, railtour/special and measurement trains, without treating a reused headcode as locomotive identity.
- Added explicit exact-vehicle versus representative-image labelling and retained Wikimedia creator, licence and source attribution.
- Enabled the click-driven Wikimedia lookup by default while keeping routine unidentified trains image-free.
- Updated CI to include the root release-wiring checks that normal `pytest.ini` discovery omits.
- Verification: 108 Python tests and 24 Chromium browser workflows pass.

## 2026-10-14 — v1.3.1 Clear Map + Reliable Boards

- Made the default Useful map empty and converted the six practical feeds into independent, explicit on/off cards; WebSocket snapshots are now scoped to the requested types.
- Removed duplicate filters and duplicate density, radar, traffic and Radar+ controls; added one Clear map action for every optional overlay.
- Repaired empty station boards by falling back when the local schedule cache is ready but incomplete.
- Prevented a small VSTP-only database from being mistaken for the complete national timetable cache.
- Limited ferry context to credible inbound AIS targets within 20 km or harbour activity within 5 km, with no claim that AIS is a ferry timetable.
- Added friendly military aircraft type resolution, distinct aircraft/helicopter silhouettes and clearly labelled exact-airframe versus type-reference imagery.
- Expanded browser coverage for the clear start, one-control design and clear-map behaviour.
- Verification: 96 Python tests and 21 Chromium browser workflows pass.

## 2026-10-14 — v1.3 performance and live-feed hardening

- Reworked the live-state hot path so summary counts no longer deep-copy and reclassify the entire national bus snapshot on every browser update.
- Reduced live map delivery to one compact activity array, skipped unchanged snapshots, increased the useful browser cadence from 1.5s to 1.0s, and added zoom-aware limits for routine trains, bikes and dense environmental layers.
- Changed map rendering to update only markers whose position, icon or tooltip actually changed; a 2,500-object unchanged snapshot now causes zero marker/cluster churn.
- Added a small national Cool priority API so Best Spot, rankings and the ticker remain UK-wide while map markers stay viewport-scoped.
- Expanded real radio choices to 12 in the interface / 16 in the backend, coalesced directory requests and retained last-known verified stations through short provider outages.
- Corrected ADSB.lol identity parsing so real aircraft descriptions/operators reach the Cool classifier, and shortened the polite four-region UK refresh from about four minutes to about two.
- Hardened AISStream subscription confirmation, compression, ping/queue handling, retry backoff, malformed frames and shutdown. A replacement key is now validated before it can be saved; the currently stored key is rejected before confirmation and still needs replacing.
- Verification: 89 Python tests and 19 Chromium browser tests pass.

## 2026-10-13 — v1.3.0 Stabilise + Intelligence

- Added the server-owned Cool classifier with explainable category, subtype, score, badge and Best Spot eligibility; Eurofighters and other identified high-value aircraft outrank routine trainers.
- Added Cool rail subtype filters, Best Spot zoom, individual aircraft icons, live-object map tracking and independent practical layer switches.
- Replaced the external webcam shortcut with source-backed on-map TfL JamCam images/video and nearest-camera context for incidents where licensed camera data exists.
- Made RNLI media conservative: only date/place/rescue-matched coverage is presented and generic historic imagery is no longer substituted.
- Made passenger stations immediately available from the cached rail reference, preserving the last good cache through transient Windows locks.
- Bounded and cached aircraft enrichment, removed repeated refresh lookups and retained feed-provided identity while enrichment is unavailable.
- Kept bus density in More, removed the CARTO basemap dependency, and expanded deterministic browser coverage for the new interactions.

## 2026-10-12 — protected local credential persistence

- Added a Windows DPAPI-encrypted, per-user credential backup so new local checkouts restore Network Rail and other configured feeds automatically without exposing secrets to Git.
- Changed TomTom setup to hide key entry and clear its temporary plaintext bridge immediately after use.

## 2026-10-10 — production hosting preparation

- Removed the CARTO dark basemap and its external tile dependency.
- Existing browsers that saved the retired dark-map preference now fall back to the standard map.
- Added browser regression coverage confirming that no CARTO request is made.
- Extended Windows credential migration to recover missing values from previous extracted builds in Downloads, including the extra directory created by ZIP extraction.
- TomTom setup now validates a replacement key against the official Orbis Traffic service before saving it and immediately refreshes the encrypted Windows credential backup.

## 2026-10-10 — v1.2.1 production stabilization

This mission intentionally adds no product features and preserves the Useful, Cool, and Live experiences.

### Repository and automation

- Imported the sanitized v1.2.1 source as the repository baseline; no populated `.env`, credentials, Python caches, or runtime data were included.
- Expanded `.gitignore` so downloaded/generated `data/`, Node dependencies, Playwright output, pytest output, and local work directories cannot be committed accidentally.
- Added reproducible development dependencies in `requirements-dev.txt`.
- Added GitHub Actions checks for Python tests/compilation and deterministic Chromium tests/JavaScript syntax.

### Security

- Added public-error sanitization for credentials embedded in query strings, authorization values, and known secret values.
- Applied sanitization to BODS, BODS SIRI-SX, and TomTom traffic source status.
- Replaced prefix-only Environment Agency URL checks with parsed scheme/host/port/path validation for browser-supplied measure URLs and upstream flood-area links.
- Restricted browser WebSocket connections to the request's own Origin while retaining clients that do not send an Origin header.
- Changed the Windows launcher to report a port-8000 conflict instead of force-terminating the process that owns the port.

### Reliability and concurrency

- Made `LiveState` own and return detached model snapshots so callers cannot mutate shared state outside its lock.
- Made Environment Agency and National Highways collector starts idempotent and shutdown joins bounded.
- Made STOMP retry waits interruptible and worker shutdown bounded.
- Serialized and atomically replaced station-reference merges that can run concurrently at startup.
- Clear BODS stop predictions after a successful empty snapshot so removed/cancelled journeys do not remain stale.
- Preserve outage state on total provider failure by surfacing an error instead of treating the result as a verified empty snapshot.

### Browser regression coverage

- Added a Playwright/Chromium harness with deterministic recorded application, place, station-board, bus-board, radio, weather, traffic, rail, and maritime data.
- Covered map load, Useful/Cool switching, Cool badges, left drawer behavior, marker hover/click, detail open/close, station boards, bus timetables, TomTom toggles, weather radar, maritime selection, Live Audio, and mobile layout.
- Stubbed external tiles/CDN/media interfaces so tests are credential-free and do not depend on live providers.

### Verification

- Python: 58 tests passed.
- Browser: 10 Playwright tests passed in Chromium.
- Python source and tests compile successfully.
- Frontend application and service-worker JavaScript pass Node syntax checks.
- The staged source contains no populated `.env` or detected credential literal.

### Remaining work

See `AUDIT_REPORT.md` for the prioritized remaining risks. The highest priorities are a shared outbound URL policy, protection for quota-bearing proxy routes, structured partial-failure semantics, source-aware state expiry, and backend recorded-feed replay tests. The Cool Engine described here was subsequently delivered in the v1.3.0 work above.
## 2026-11-02 — Exact rolling-stock livery panels

- Turned exact UK Train Dataset number matches into livery-themed train cards.
- Added class, individual number, name, operator/owner, formation, depot, build year, works number, pool and preservation/storage details when present.
- Kept TRUST headcodes and train UIDs out of rolling-stock matching so a service such as `1Z66` cannot be mistaken for a locomotive number.
# 2026-11-03 — Mobile map controls and toast repair

- Replaced the tall phone navigation rail with a compact bottom-sheet layout.
- Collapsed location actions after use on phones so aircraft and bus results are
  immediately visible on the map, with a small one-tap control to reopen it.
- Fixed status toasts stretching between top and bottom edges in mobile Safari.
- Shortened route-detail failure copy and removed the misleading validity claim.
# 2026-11-03 — Hosted mobile cache, routes and aircraft polish

- Versioned the main browser assets and made live CSS/JavaScript network-first
  so deployed phone clients cannot remain stuck on an obsolete layout.
- Made basic train routes and calling points part of the free live detail while
  keeping advanced locating, tracking and the remaining Radar+ paywall intact.
- Put the phone logo, search and Go action on one compact row, kept workspace
  buttons visible, and moved Leaflet zoom controls clear of the menu.
- Replaced the generic helicopter blocks with distinct top-down Chinook,
  Apache and conventional-helicopter silhouettes.
- Kept the four workspace destinations visible in the collapsed phone bar so
  location actions reveal the map without making My Radar unreachable.
