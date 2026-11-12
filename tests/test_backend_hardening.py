import json
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import app.collectors.bods as bods_module
from app.collectors.bods import BODSCollector
from app.collectors.stomp_client import MultiplexStompFeed, StompFeed, _MultiplexListener
from app.reference_data import merge_station_file


def test_env_migration_searches_downloaded_previous_builds():
    script = Path("migrate_env.ps1").read_text(encoding="utf-8")

    assert "GetFolderPath('UserProfile')" in script
    assert "Join-Path $userProfile 'Downloads'" in script
    assert "Get-ChildItem -LiteralPath $dir.FullName" in script


def test_windows_launcher_restores_and_backs_up_credentials_with_dpapi():
    migration = Path("migrate_env.ps1").read_text(encoding="utf-8")
    backup = Path("backup_credentials.ps1").read_text(encoding="utf-8")
    launcher = Path("run.bat").read_text(encoding="utf-8")

    assert "credentials.v1.json" in migration
    assert "ProtectedData]::Unprotect" in migration
    assert "DataProtectionScope]::CurrentUser" in migration
    assert migration.index("$protectedValues.ContainsKey($key)") < migration.index("foreach ($dir in $previous)", migration.index("foreach ($key in $keys)"))

    assert "ProtectedData]::Protect" in backup
    assert "DataProtectionScope]::CurrentUser" in backup
    assert "SetAccessRuleProtection($true, $false)" in backup
    assert "Move-Item -LiteralPath $temporaryPath" in backup
    assert backup.index("Set-Acl -LiteralPath $temporaryPath") < backup.index("Move-Item -LiteralPath $temporaryPath")

    assert "missingNetworkRailPassword" in migration
    assert 'set "CONFIG_RESULT=%ERRORLEVEL%"' in launcher
    assert launcher.index("repair_env.ps1") < launcher.index("backup_credentials.ps1")


def test_tomtom_setup_hides_key_entry_and_zeros_plaintext_bridge():
    script = Path("configure_traffic.ps1").read_text(encoding="utf-8")

    assert "Read-Host 'TomTom API key' -AsSecureString" in script
    assert "ZeroFreeBSTR" in script
    assert "maps/orbis/traffic/flow/raster/tile" in script
    assert "TomTom-Api-Key" in script
    assert script.index("Invoke-WebRequest") < script.index("Set-DotEnvValue 'TOMTOM_TRAFFIC_API_KEY'")
    assert "backup_credentials.ps1" in script


def test_station_file_merge_preserves_existing_rows_and_cleans_temp_file(tmp_path: Path):
    station_file = tmp_path / "stations.json"
    merge_station_file(station_file, {"00001": {"name": "One"}})
    count = merge_station_file(station_file, {"00002": {"name": "Two"}})

    assert count == 2
    assert set(json.loads(station_file.read_text(encoding="utf-8"))) == {"00001", "00002"}
    assert not station_file.with_suffix(".json.tmp").exists()


def test_stomp_stop_waits_for_worker_shutdown():
    class ControlledFeed(StompFeed):
        def __init__(self):
            super().__init__(SimpleNamespace(), state=None, name="test", topic="test")
            self.started = Event()
            self.finished = Event()

        def _run(self):
            self.started.set()
            self._stop.wait()
            time.sleep(0.05)
            self.finished.set()

    feed = ControlledFeed()
    feed.start()
    assert feed.started.wait(1)
    feed.stop()

    assert feed.finished.is_set()
    assert not feed._thread.is_alive()


def test_multiplex_stomp_dispatches_frames_identified_by_subscription_id():
    received = []
    feed = SimpleNamespace(
        name="td", topic="/topic/TD_ALL_SIG_AREA",
        handle_message=received.append,
    )
    owner = SimpleNamespace(feeds=[feed], last_error=None, terminal_error=None)

    _MultiplexListener(owner).on_message(SimpleNamespace(
        headers={"subscription": "ukradar-td"}, body="rail-frame",
    ))

    assert received == ["rail-frame"]


def test_multiplex_uses_unique_process_session_identity():
    settings = SimpleNamespace(network_rail_client_id="radar")
    first = MultiplexStompFeed(settings, [])
    second = MultiplexStompFeed(settings, [])

    assert first._client_id.startswith("radar-")
    assert second._client_id.startswith("radar-")
    assert first._client_id != second._client_id


def test_successful_bods_snapshot_clears_predictions_removed_upstream(monkeypatch):
    settings = SimpleNamespace(
        bods_api_key="", bods_max_age_seconds=300, bods_poll_seconds=10,
    )

    class State:
        def source(self, name):
            return None

        def set_source(self, status):
            if status.mode == "ready":
                collector._stop.set()

        def upsert(self, vehicle):
            raise AssertionError("empty snapshot should not upsert")

    collector = BODSCollector(settings, State())
    collector._update_predictions([{
        "stop_ref": "STOP1", "expected": "2099-01-01T00:00:00+00:00",
    }])
    collector._fetch = lambda: (b"snapshot", "application/test", "bulk-siri")
    monkeypatch.setattr(bods_module, "parse_bods_payload", lambda payload, content_type: [])
    monkeypatch.setattr(bods_module, "parse_siri_predictions_from_payload", lambda payload, content_type: [])

    collector._run()

    assert collector._stop_predictions == {}
