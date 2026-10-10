from pathlib import Path


def test_launcher_uses_env_migration_and_previous_builds():
    text = Path("run.bat").read_text(encoding="utf-8")
    assert "migrate_env.ps1" in text
    assert "uk-activity-radar-v1.1.3" in text
    assert "uk-activity-radar-v1.1.2-live-boards-radar" in text
    assert 'EXPECTED_VERSION=1.2.1' in text


def test_launcher_never_kills_an_unrelated_port_owner():
    text = Path("run.bat").read_text(encoding="utf-8")
    assert "Stop-Process" not in text
    assert "Port 8000 is already in use" in text


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
    assert "Server v1.2.1" in text
    assert "v1.1.3" not in text


def test_useful_cool_live_shell_is_wired():
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'data-workspace="useful"' in html
    assert 'data-workspace="cool"' in html
    assert 'data-workspace="live"' in html
    assert 'data-type="cool-air"' in html
    assert 'function coolInfo(a)' in js
    assert "currentWorkspace==='cool'" in js
