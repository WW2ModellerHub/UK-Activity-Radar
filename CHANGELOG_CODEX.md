# Codex stabilization changelog

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

See `AUDIT_REPORT.md` for the prioritized remaining risks. The highest priorities are a shared outbound URL policy, protection for quota-bearing proxy routes, structured partial-failure semantics, source-aware state expiry, and backend recorded-feed replay tests. The Cool Engine remains a separate v1.3 mission.
