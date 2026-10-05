# Codex Mission — UK Activity Radar v1.2.1

## Objective

Audit UK Activity Radar v1.2.1 as a production codebase. **Do not add product features in this mission.** Preserve all existing user-visible behaviour unless a change is necessary to fix a confirmed defect or security/reliability problem.

## Primary task

1. Inspect the entire frontend and backend codebase before making broad changes.
2. Identify architectural weaknesses, duplicated/dead code, fragile coupling, blocking work, state-management problems, unreliable feed handling, security/secrets risks, and missing integration/browser coverage.
3. Refactor where justified while preserving behaviour.
4. Build automated browser tests for every major user workflow.
5. Ensure all existing tests continue to pass.
6. Produce a concise changelog plus a prioritized list of remaining risks.

## Required audit areas

### Architecture
- FastAPI application boundaries and route organization.
- Collector lifecycle, polling, retry/backoff, caching and failure isolation.
- Shared state ownership and concurrency safety.
- Frontend state management and DOM/event coupling.
- Repeated classification/rendering logic.
- Configuration migration and secrets handling.
- Generated/runtime data separation from source code.

### Feed reliability
Review Network Rail, BODS, National Highways, AISStream, ADS-B, RNLI, Met Office/weather, TomTom and other live-world collectors for:
- timeouts;
- retries/backoff;
- rate limiting;
- stale-data handling;
- partial failures;
- malformed payload handling;
- startup/shutdown behavior;
- cache invalidation;
- one failing source affecting unrelated sources.

Do not require live credentials in automated tests. Use mocks/fixtures.

### Frontend / UX regression protection
Add browser-level tests (prefer Playwright) covering at minimum:
- app loads with the map visible;
- Useful/Cool mode switching;
- left navigation/drawer behavior;
- marker hover and click detail flow;
- station-board flow;
- bus-stop board flow;
- TomTom traffic toggle states;
- weather radar toggle;
- maritime marker selection;
- Live Audio player controls;
- right-side detail panel open/close behavior;
- Cool activity card/badge rendering;
- mobile viewport smoke test.

Tests may use deterministic fixture data rather than external services.

### Security
- No secrets committed to source.
- `.env` must remain ignored.
- `.env.example` must contain placeholders only.
- No API keys leaked to browser code unless the upstream API explicitly requires a public client key and the architecture documents that decision.
- Review URL construction, proxy routes, external fetches and user-controlled query parameters.

### Performance
Measure before changing. Inspect:
- marker rendering volume;
- unnecessary DOM churn;
- repeated network requests;
- synchronous/blocking work on the FastAPI event loop;
- large JSON payloads/websocket updates;
- excessive re-rendering/polling.

Do not rewrite the map stack in this mission unless a measured blocker makes it unavoidable.

## Deliverables

Create/update:
- `AUDIT_REPORT.md` — findings grouped by severity with file/function references.
- `CHANGELOG_CODEX.md` — changes made in this mission.
- browser/integration tests and deterministic fixtures.
- any small architecture notes needed to explain refactors.

## Acceptance criteria

- Existing Python tests pass.
- New browser/integration tests pass.
- Python source compiles.
- Frontend JavaScript passes syntax/lint checks available in the repo.
- No private credentials are introduced.
- Application can run without real third-party credentials using fixtures/demo/mocked paths for tests.
- No intentional product features are removed.
- Any behaviour change is documented with rationale.

## Important product invariants

The product has two distinct experiences:
- **Useful:** everyday transport, roads, weather and operational information.
- **Cool:** unusual/interesting live activity such as steam/railtours, freight/test trains, military/special aircraft, Coastguard/HEMS, naval/lifeboat/cruise/specialist vessels.

Live contains contextual public/authorized audio, webcams and radar. Preserve these concepts and their separation.

## Working style

Prefer small, reviewable commits. Do not perform a huge rewrite. Add characterization tests before risky refactors. When unsure whether odd behaviour is intentional, preserve it and record it in `AUDIT_REPORT.md` rather than silently changing it.
