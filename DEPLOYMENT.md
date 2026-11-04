# Public deployment

UK Activity Radar is a dynamic FastAPI service. GitHub stores the clean source;
GitHub Pages cannot run its collectors, WebSocket, SQLite caches, secret-key
proxies or Stripe webhooks. `render.yaml` defines a single-instance Render web
service with a persistent disk and `/api/health` health check.

## First launch

1. Push only clean source. Confirm `.env`, `data/`, `.venv`, `node_modules` and
   test output are absent from the commit.
2. Create a Render Blueprint from the GitHub repository and review the displayed
   service and disk price before applying it.
3. Enter requested secrets in Render; never put values in `render.yaml`.
   Enter the public seller name and support email when prompted; paid launch is
   blocked until both are present and the email is structurally valid.
4. Keep `PUBLIC_DEPLOYMENT=true`, `COMMERCIAL_MODE=true`, one instance and one
   Uvicorn worker.
5. Deploy, then verify `/api/health`, `/api/readiness`, the browser WebSocket and
   each enabled provider under **Network → Data health**.
   Do not launch until `/api/readiness` reports `launch.ready: true` and an empty
   `launch.blockers` list.
6. Prove the generated HTTPS `onrender.com` URL before attaching a custom domain.

Run the same production browser check used by monitoring:

```powershell
$env:RADAR_BASE_URL = "https://YOUR-DOMAIN"
npm run test:hosted
```

The hosted test deliberately uses the real deployment rather than recorded
fixtures. It checks the map shell, live connection, commercial profile,
Radar+ visibility, legal pages, storage headroom, request budgets and every
launch-readiness gate.

The Blueprint pins `RADAR_DATA_DIR` to the persistent disk, compresses sizeable
HTTP responses and serves browser assets with short revalidation caches. Do not
move the data directory back to the ephemeral filesystem: rail schedules and
regional BODS timetable indexes must survive deploys and restarts.

Render references: [Blueprints](https://render.com/docs/blueprint-spec),
[FastAPI](https://render.com/docs/deploy-fastapi),
[persistent disks](https://render.com/docs/disks), and
[current pricing](https://render.com/pricing).

## Live-data secrets

| Variable | Purpose |
| --- | --- |
| `NETWORK_RAIL_USERNAME` | Network Rail Open Data login |
| `NETWORK_RAIL_PASSWORD` | Network Rail Open Data password |
| `BODS_API_KEY` | DfT Bus Open Data Service |
| `NATIONAL_HIGHWAYS_API_KEY` | National Highways road feed |
| `TOMTOM_TRAFFIC_API_KEY` | TomTom maps, traffic and search |

The commercial Blueprint deliberately disables sources without a sufficiently
clear commercial-display basis. Do not re-enable one merely because a free key
exists.

## Radar+ activation

The first production Blueprint launches with `BILLING_ENABLED=false` and keeps
the intended public price at `£3.99/month`. This allows the real HTTPS service
and webhook origin to be proven before Stripe is configured. Billing remains
fail-closed: change the flag to `true` only after every required secret below is
present and valid.

Before enabling the paid profile:

1. Create one recurring Stripe Price for Radar+.
2. Set `PUBLIC_BASE_URL` to the final HTTPS origin.
3. Generate a long random `BILLING_TOKEN_PEPPER` and keep it only in Render.
4. Set `STRIPE_SECRET_KEY` and `STRIPE_PLUS_PRICE_ID`.
5. Register `https://YOUR-DOMAIN/api/billing/webhook` in Stripe, subscribe to
   the checkout/subscription events used by the app, and set
   `STRIPE_WEBHOOK_SECRET`.
6. Deploy with Stripe test-mode values first, complete checkout and verify
   status, portal access, cancellation and entitlement removal. Replace the
   three Stripe values together when moving to live mode; mixed test/live
   webhook events are rejected.
7. Publish seller identity, contact, pricing, cancellation/refund and privacy
   information before accepting live payments.

The Blueprint prompts for `SELLER_LEGAL_NAME` and `SUPPORT_EMAIL`; these are
rendered into `/terms` and `/privacy`. They are also readiness gates, so a paid
deployment cannot report launch-ready while either still says pending.

The browser never grants itself access. Paid routes and the WebSocket enforce
entitlements server-side; checkout and portal destinations are restricted to
Stripe-hosted URLs.

## Proactive commute alerts

The alert engine is present but the Blueprint defaults
`PUSH_ALERTS_ENABLED=false`. To activate it after the final HTTPS domain works:

1. Generate one VAPID keypair, for example with
   `npx web-push generate-vapid-keys --json`. Generate it once only; rotating it
   invalidates every existing browser subscription.
2. Store the returned values as Render secrets `VAPID_PUBLIC_KEY` and
   `VAPID_PRIVATE_KEY`.
3. Set `VAPID_SUBJECT` to a genuine operator contact such as
   `mailto:alerts@your-domain` and ensure `PUBLIC_BASE_URL` is the final HTTPS
   origin.
4. Set `PUSH_ALERTS_ENABLED=true`, redeploy, save a place in My Radar and enable
   one test commute from an installed browser.
5. Verify a test driving incident and rail disruption. The scheduler sends at
   most one notification per live activity and stores subscriptions only on the
   persistent private disk.

Browsers require a direct user gesture before notification permission is
requested. Never pre-prompt or enable alerts by default.

## Optional routing and Street Manager

`OTP_GRAPHQL_URL` may point to a separately operated OpenTripPlanner 2 service.
Driving, walking and cycling use the existing TomTom key through the server-side
Routing API when `TOMTOM_ROUTING_ENABLED=true`; public transport still requires
the independently hosted OpenTripPlanner graph.
That router needs its own suitably licensed OSM and GTFS/NeTEx graph and may need
substantially more memory than this map service.

OpenStreetMap data may be used commercially under the ODbL with attribution,
but the OpenStreetMap Foundation's community tile servers and public demo
routers are not production infrastructure. The hosted build therefore keeps
TomTom Routing as its supported car router. A future provider-independent
deployment can self-host OSRM/openrouteservice from an OSM extract, but must
budget separate compute, graph updates, monitoring and ODbL attribution.

Street Manager public open data is implemented as a signed AWS SNS receiver.
Enable it only after DfT registration of the public HTTPS callback and preserve
the single-process SQLite ordering authority.

## Architecture constraints

- Keep one worker and one instance until feed collection and live state move to
  external services. Extra processes duplicate subscriptions and split viewers.
- Keep the persistent disk mounted at `data/`; full rail caches are too large for
  an ephemeral/free deployment and refreshes temporarily need extra space.
- Regional BODS databases include route and service-day indexes. The first use
  of a region may still need to download and build its official GTFS bundle;
  subsequent vehicle and stop lookups use the persistent indexed copy.
- Keep public request budgets enabled for TomTom tiles and click-driven lookups.
- Monitor memory, disk, restarts, stale feeds and provider 401/403/429 responses.
- Keep the pinned, self-hosted Leaflet assets and tested security headers in the
  release bundle; do not replace them with unpinned CDN dependencies.

## Monitoring and launch alerts

1. Set the GitHub Actions repository variable `RADAR_BASE_URL` to the final
   HTTPS origin. `.github/workflows/hosted-smoke.yml` then runs twice an hour
   and can also be started manually. Enable GitHub Actions failure
   notifications for the repository operator.
2. Configure an external uptime check against `/api/health` every minute.
3. Configure a JSON/content check against `/api/readiness` and alert when
   `launch.ready` is false, `overall` is `degraded`, storage is unhealthy, or a
   request budget has less than 20% remaining.
4. In Render, alert on restarts, sustained high memory/CPU and disk use above
   80%. The application independently blocks readiness below 1 GB free.
5. Enable Stripe webhook delivery failure emails and test the webhook after
   changing domains, keys or endpoint configuration.

Readiness exposes only aggregate quota counters, disk capacity and redacted
source health. It never returns client addresses, API credentials, billing
tokens or Stripe secrets.

## Google Search and consent-gated analytics

The public service exposes `/robots.txt`, `/sitemap.xml`, canonical links,
social metadata, structured data and a crawlable `/about` page. After the final
domain is stable:

1. Add the HTTPS URL-prefix property in Google Search Console.
2. Choose HTML-tag verification and copy only the token from the tag into the
   Render environment variable `GOOGLE_SITE_VERIFICATION`, then redeploy and
   click Verify.
3. Submit `https://your-domain/sitemap.xml` in Search Console and inspect `/`
   and `/about`. A sitemap helps discovery but does not guarantee rankings.
4. In Google Analytics, create a GA4 web stream. Put its public `G-...` ID in
   `GA4_MEASUREMENT_ID`, set `GA4_ENABLED=true`, and redeploy.
5. Test both choices in a private browser window. No request to
   `googletagmanager.com` may occur before **Allow analytics** is clicked.
   Visitors can change the choice under Network → Site analytics.

Search Console and GA4 identifiers are public integration identifiers, not API
secrets, but they remain deployment configuration so one checkout cannot
silently send measurement to another operator's property. Do not enable GA4
without preserving the consent control and current privacy notice.

## Commercial-source policy

The live `/data-licences` register is the operational source of attributions.
Trainiac, RainViewer radar, RNLI editorial content, embedded public radio,
Airport-Data photos, community tile servers, OpenRailwayMap hosted tiles and
other unclear or non-commercial sources are absent from the commercial
experience. AISStream and other optional local collectors remain forced off by
`COMMERCIAL_MODE` unless a future reviewed agreement justifies a code change.

Provider terms, quotas and prices can change. Recheck them before launch and at
regular intervals; an API key proves authentication, not redistribution rights.
Charging users is not the test for commercial use: advertising, sponsorship,
affiliate revenue and a paid tier all make this a commercial deployment.
