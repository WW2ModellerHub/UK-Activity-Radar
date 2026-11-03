import json
from pathlib import Path


def test_launcher_uses_env_migration_and_previous_builds():
    text = Path("run.bat").read_text(encoding="utf-8")
    assert "migrate_env.ps1" in text
    assert "uk-activity-radar-v1.1.3" in text
    assert "uk-activity-radar-v1.1.2-live-boards-radar" in text
    assert 'EXPECTED_VERSION=1.5.0' in text
    assert 'UK-Activity-Radar-v1.3.2' in text


def test_launcher_never_kills_an_unrelated_port_owner():
    text = Path("run.bat").read_text(encoding="utf-8")
    assert "Stop-Process" not in text
    assert "Port 8000 is already in use" in text


def test_launchers_refuse_the_known_discard_proxy_without_clearing_proxies():
    launcher = Path("run.bat").read_text(encoding="utf-8")
    server = Path("server.bat").read_text(encoding="utf-8")
    preflight = Path("app/network_environment.py").read_text(encoding="utf-8")

    assert "network_environment.py" in launcher
    assert "network_environment.py" in server
    assert "outbound_proxy_blocked" in launcher
    assert "double-click run.bat in File Explorer" in launcher
    assert "DISCARD_PROXY_PORT = 9" in preflight
    assert "SetEnvironmentVariable" not in preflight
    assert "trust_env = False" not in preflight


def test_network_rail_configurator_merges_instead_of_overwriting_env():
    text = Path("configure_live.ps1").read_text(encoding="utf-8")
    assert "Set-DotEnvValue 'NETWORK_RAIL_USERNAME'" in text
    assert "WriteAllLines" in text
    assert "WriteAllText((Join-Path $PSScriptRoot '.env'), $content" not in text


def test_maritime_and_roads_write_utf8_without_bom():
    for filename in ("configure_maritime.ps1", "configure_roads.ps1"):
        text = Path(filename).read_text(encoding="utf-8")
        assert "System.Text.UTF8Encoding($false)" in text
        assert "Set-Content -Path $envPath -Value $content -Encoding UTF8" not in text


def test_server_batch_version_matches_release():
    text = Path("server.bat").read_text(encoding="utf-8")
    assert "Server v1.5.0" in text
    assert "v1.1.3" not in text


def test_release_version_is_consistent_across_runtime_entry_points():
    package = json.loads(Path("package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(Path("package-lock.json").read_text(encoding="utf-8"))
    version = package["version"]

    assert version == "1.5.0"
    assert package_lock["version"] == version
    assert package_lock["packages"][""]["version"] == version
    assert f'APP_VERSION = "{version}"' in Path("app/main.py").read_text(encoding="utf-8")
    assert f"EXPECTED_VERSION={version}" in Path("run.bat").read_text(encoding="utf-8")
    assert f"Server v{version}" in Path("server.bat").read_text(encoding="utf-8")
    assert f"shell-v{version}" in Path("app/static/service-worker.js").read_text(encoding="utf-8")
    assert f"UK Activity Radar v{version}" in Path("app/static/index.html").read_text(encoding="utf-8")

    outbound_sources = (
        Path("app/traffic_flow.py"),
        Path("app/coverage.py"),
        Path("app/cameras.py"),
    )
    assert all(f"UKActivityRadar/{version}" in path.read_text(encoding="utf-8") for path in outbound_sources)
    assert all("UKActivityRadar/1.2" not in path.read_text(encoding="utf-8") for path in outbound_sources)


def test_useful_cool_live_shell_is_wired():
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'data-workspace="useful"' in html
    assert 'data-workspace="cool"' in html
    assert 'data-workspace="live"' in html
    assert 'data-type="cool-air"' in html
    assert 'function coolInfo(a)' in js
    assert "currentWorkspace==='cool'" in js
