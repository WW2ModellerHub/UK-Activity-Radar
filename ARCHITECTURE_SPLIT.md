# Ingestion / web split

## Target

One private ingestion process owns all upstream subscriptions and polling. It
publishes a validated, compressed live-state snapshot to Render Key Value. One
or more public web workers consume that state and own HTTP, WebSockets, billing
and browser-facing filtering.

This removes duplicate provider connections, makes web restarts independent of
feed recovery, and permits horizontal web scaling.

## Migration sequence

1. **Shadow bridge (this change).** `LiveState.export_shared_snapshot`, atomic
   replacement, compression and the Redis-compatible bridge are tested but not
   enabled. The existing `RADAR_PROCESS_ROLE=all` deployment is unchanged.
2. Provision a private Render Key Value instance with `noeviction`; inject its
   internal `connectionString` as `RADAR_SHARED_STATE_URL`.
3. Run the current service as publisher in shadow mode and measure snapshot
   bytes, publish time, Redis memory and freshness for 24 hours.
4. Add a private/background ingest service using `RADAR_PROCESS_ROLE=ingest`.
   Switch the public service to `web` only after its consumer readiness check
   has stayed green during a canary.
5. Scale web instances. Replace full snapshots with changed-ID hashes only if
   shadow metrics show the snapshot transport is the next material bottleneck.

## Baseline measurement

On the development machine, a synthetic snapshot matching the live production
bus count (22,389 records) took 170.7 ms to export and 126.5 ms to JSON-encode
and compress. Its compressed payload was 0.27 MB. A two-second shadow cadence
is therefore a reasonable starting point, but production CPU, latency and
Key Value memory must be measured before the web process is cut over.

## Safety rules

- Web workers retain the last valid snapshot through a short bridge outage.
- A malformed snapshot is rejected before it changes live state.
- The key expires, so a dead ingest process cannot look permanently healthy.
- Provider credentials belong only on the ingest service after cutover.
- Billing, push subscriptions and other durable user data require a shared SQL
  store before more than one web instance is enabled.
