from pathlib import Path

from app.product import ProductAnalytics
from app.config import Settings, _commercial_source


ROOT = Path(__file__).resolve().parents[1]


def test_render_blueprint_uses_single_worker_port_health_and_persistent_data():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "plan: 1c-2g" in blueprint
    assert "numInstances: 1" in blueprint
    assert "--host 0.0.0.0" in blueprint
    assert "--port $PORT" in blueprint
    assert "--workers 1" in blueprint
    assert "healthCheckPath: /api/health" in blueprint
    assert "mountPath: /opt/render/project/src/data" in blueprint
    assert "sizeGB: 10" in blueprint
    assert 'key: RADAR_DATA_DIR\n        value: "/opt/render/project/src/data"' in blueprint
    assert "python -m compileall -q app" in blueprint
    assert "--timeout-keep-alive 10" in blueprint
    assert 'key: PUBLIC_DEPLOYMENT\n        value: "true"' in blueprint
    assert 'key: COMMERCIAL_MODE\n        value: "true"' in blueprint


def test_render_blueprint_prompts_for_secrets_instead_of_storing_values():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    secret_names = {
        "NETWORK_RAIL_USERNAME",
        "NETWORK_RAIL_PASSWORD",
        "BODS_API_KEY",
        "NATIONAL_HIGHWAYS_API_KEY",
        "TOMTOM_TRAFFIC_API_KEY",
        "MAPBOX_PUBLIC_TOKEN",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PLUS_PRICE_ID",
        "BILLING_TOKEN_PEPPER",
        "SELLER_LEGAL_NAME",
        "SUPPORT_EMAIL",
    }

    for name in secret_names:
        assert f"- key: {name}\n        sync: false" in blueprint


def test_render_blueprint_arms_radar_plus_with_public_price():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert 'key: BILLING_ENABLED\n        value: "false"' in blueprint
    assert 'key: PUBLIC_BASE_URL\n        value: "https://uk-activity-radar.onrender.com"' in blueprint
    assert 'key: RADAR_PLUS_PRICE_DISPLAY\n        value: "£3.99/month"' in blueprint


def test_render_commercial_profile_disables_unlicensed_or_unclear_sources():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    disabled = {
        "AISSTREAM_ENABLED", "RNLI_ENABLED", "LONDON_FIRE_ENABLED",
        "MET_OFFICE_ENABLED", "NOMINATIM_ENABLED", "COVERAGE_ENABLED",
        "BGS_EARTHQUAKES_ENABLED", "WAVENET_ENABLED", "UK_AIR_ENABLED", "TFL_LIVE_ENABLED",
        "GBFS_ENABLED", "ISS_ENABLED", "POWER_OUTAGES_ENABLED",
        "GRITTER_ENABLED",
    }
    for name in disabled:
        assert f'- key: {name}\n        value: "false"' in blueprint


def test_commercial_source_guard_cannot_be_bypassed_by_an_enabled_feed_flag(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_MODE", "true")
    monkeypatch.setenv("BGS_EARTHQUAKES_ENABLED", "true")
    assert _commercial_source("BGS_EARTHQUAKES_ENABLED") is False


def test_render_python_version_matches_ci_major_minor():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.13"


def test_disabled_product_analytics_is_a_no_write_noop(tmp_path: Path):
    database = tmp_path / "nested" / "product.sqlite"
    store = ProductAnalytics(database, enabled=False)

    assert store.record("browser-session", "app_open", "web") is True
    assert store.record("browser-session", "unsupported_event", "web") is False
    assert store.summary() == {
        "enabled": False,
        "anonymous_sessions": 0,
        "events": {},
        "pricing_interest": {},
        "note": "Anonymous product analytics are disabled for this deployment.",
    }
    assert not database.exists()
    assert not database.parent.exists()


def test_product_analytics_is_opt_in_by_default(monkeypatch):
    monkeypatch.delenv("PRODUCT_ANALYTICS_ENABLED", raising=False)
    assert Settings().product_analytics_enabled is False


def test_google_analytics_is_consent_gated_and_opt_in_by_default(monkeypatch):
    monkeypatch.delenv("GA4_ENABLED", raising=False)
    assert Settings().ga4_enabled is False
    source = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "loadGoogleAnalytics" in source
    assert "analyticsChoice()==='granted'" in source
    assert "googletagmanager.com/gtag/js" in source


def test_public_deployment_mode_is_opt_in_for_local_installs(monkeypatch):
    monkeypatch.delenv("PUBLIC_DEPLOYMENT", raising=False)
    assert Settings().public_deployment is False


def test_basic_train_route_details_are_not_hidden_behind_subscription():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    activity_route = source[source.index("def activity_rail("):source.index("def rail_schedule(")]
    schedule_route = source[source.index("def rail_schedule("):source.index("def locate_rail_service(")]
    assert '_require_plus(request, "advanced_transport")' not in activity_route
    assert '_require_plus(request, "advanced_transport")' not in schedule_route
    locate_route = source[source.index("def locate_rail_service("):source.index("def activity_bus(")]
    assert '_require_plus(request, "advanced_transport")' not in locate_route
    bus_route = source[source.index("def activity_bus("):source.index("def activity_detail(")]
    assert '_require_plus(request, "advanced_transport")' not in bus_route


def test_paid_launch_requires_public_seller_name_and_support_email():
    assert Settings(seller_legal_name="", support_email="").seller_details_ready is False
    assert Settings(seller_legal_name="Example Operator", support_email="not-an-email").seller_details_ready is False
    assert Settings(seller_legal_name="Example Operator", support_email="support@example.com").seller_details_ready is True


def test_main_uses_configurable_persistent_data_directory():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'os.getenv("RADAR_DATA_DIR"' in source
    assert "GZipMiddleware" in source
    assert 'response.headers.setdefault("Cache-Control", "private, no-store")' in source
    assert '"Content-Security-Policy"' in source
    assert '"Cross-Origin-Opener-Policy", "same-origin"' in source


def test_public_readiness_has_no_credential_rotation_warning_or_blocker():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "credential_rotation" not in source
    launch_checks = source[source.index("launch_checks = {"):source.index("launch_blockers =")]
    assert "credential_rotation" not in launch_checks
    assert '"tomtom_base_map": bool(settings.tomtom_traffic_api_key)' in source


def test_commercial_public_pages_and_footer_are_present():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    terms = (ROOT / "app" / "legal" / "terms.html").read_text(encoding="utf-8")
    privacy = (ROOT / "app" / "legal" / "privacy.html").read_text(encoding="utf-8")

    assert '@app.get("/terms"' in main
    assert '@app.get("/privacy"' in main
    assert 'page.replace("{{SELLER_LEGAL_NAME}}"' in main
    assert 'href="/terms"' in index
    assert 'href="/privacy"' in index
    assert 'href="/data-licences"' in index
    assert 'issues/new?template=bug_report.yml' in index
    assert 'Report a bug' in index
    assert (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").exists()
    assert "not an official transport, navigation, emergency or safety service" in terms
    assert "£3.99 per month" in terms
    assert "{{SELLER_LEGAL_NAME}}" in terms
    assert "Radar+ subscriptions and Stripe" in privacy
    assert "Full card numbers" in privacy
    assert "UK Activity Radar v1.5.0" in privacy


def test_hosted_smoke_monitor_is_opt_in_and_uses_real_https_site():
    workflow = (ROOT / ".github" / "workflows" / "hosted-smoke.yml").read_text(encoding="utf-8")
    config = (ROOT / "playwright.hosted.config.js").read_text(encoding="utf-8")
    spec = (ROOT / "e2e" / "hosted-smoke.spec.js").read_text(encoding="utf-8")

    assert "vars.RADAR_BASE_URL != ''" in workflow
    assert "npm run test:hosted" in workflow
    assert "RADAR_BASE_URL must be the deployed HTTPS origin" in config
    assert "readiness.launch.ready" in spec
    assert "health.storage_healthy" in spec


def test_public_profile_hides_bulk_and_diagnostic_routes():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "def _require_local_debug_route()" in source
    for function in ("diagnostics", "vehicles", "events", "activities", "unmapped"):
        marker = f"def {function}("
        start = source.index(marker)
        assert "_require_local_debug_route()" in source[start:start + 260]


def test_long_lived_service_rechecks_schedule_and_reference_data():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "async def _schedule_sync_loop()" in source
    assert "await asyncio.sleep(6 * 3600)" in source
    assert "asyncio.create_task(_schedule_sync_loop())" in source
    assert "async def _reference_sync_loop()" in source
    assert "await asyncio.sleep(24 * 3600)" in source
    assert "asyncio.create_task(_reference_sync_loop())" in source


def test_versioned_shell_assets_do_not_leave_mobile_clients_on_stale_css():
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    worker = (ROOT / "app" / "static" / "service-worker.js").read_text(encoding="utf-8")
    assert "/static/app.css?v=16070" in index
    assert "/static/map-markers.css?v=16020" in index
    assert "/static/app.js?v=16071" in index
    assert "/static/motion-engine.js?v=15161" in index
    assert "/static/live-object-renderer.js?v=16020" in index
    # The v1.5 stylesheets were replaced by the flat redesign.
    for retired in ("styles.css", "refinements.css", "movement-history.css"):
        assert f"/static/{retired}" not in index
        assert f"/static/{retired}" not in worker
        assert not (ROOT / "app" / "static" / retired).exists()
    assert "NETWORK_FIRST_ASSETS" in worker
    assert "/static/app.css?v=16070" in worker
    assert "/static/map-markers.css?v=16020" in worker
    assert "/static/app.js?v=16071" in worker
    assert "/static/motion-engine.js?v=15161" in worker
    assert "/static/live-object-renderer.js?v=16020" in worker
    assert "uk-activity-radar-shell-v1.5.0-r79" in worker


def test_sparse_reporting_interval_does_not_grey_live_trains():
    app = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert app.count("staleTrain=a.type==='train'&&a.restored_snapshot===true") == 2
    assert "restored_snapshot===true||ageMinutes" not in app
    assert "background:#53606b" not in app
    assert "marker-stale" not in app


def test_paid_transport_detail_is_labelled_as_radar_plus():
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "response.status===402" in app_js
    assert "Available with Radar+" in app_js


def test_map_provider_configuration_cannot_pin_the_old_fallback_in_cache():
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    map_route = main[main.index("def map_config("):main.index("def map_tile(")]

    assert "fetch('/api/map/config',{cache:'no-store'})" in app_js
    assert '"Cache-Control": "no-store"' in map_route
    assert '"base_map_provider": "Mapbox" if settings.mapbox_public_token' in main
    # One pale base map (Mapbox Light), the same with or without trains.
    assert "mapbox/light-v11" in map_route
    assert "streets-v12" not in map_route
    assert '"rail_tile_url": None' in map_route


def test_optional_traffic_control_does_not_mislabel_the_basemap_as_tomtom():
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

    assert "<strong>Live traffic</strong>" in index
    assert "<strong>TomTom traffic</strong>" not in index


def test_map_has_a_max_zoom_before_marker_clusters_are_attached():
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    map_setup = app_js[:app_js.index("const markerGroup")]
    assert "maxZoom: 20" in map_setup
