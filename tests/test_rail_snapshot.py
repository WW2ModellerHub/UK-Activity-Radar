from datetime import datetime, timedelta, timezone

from app.models import Vehicle
from app.rail_snapshot import RailPositionSnapshot


def train(train_id: str, updated: datetime, *, source: str = "Network Rail TRUST") -> Vehicle:
    return Vehicle(
        id=train_id, type="train", name="2S53", headcode="2S53",
        lat=53.235, lon=-1.421, source=source, status="Reported",
        position_method="network_report", last_updated=updated,
    )


def test_recent_network_rail_positions_survive_a_warm_restart(tmp_path):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    cache = RailPositionSnapshot(tmp_path / "rail_positions.json", max_age_minutes=45)

    saved = cache.save([
        train("nr:recent", now - timedelta(minutes=3)),
        train("nr:old", now - timedelta(hours=2)),
        train("demo", now - timedelta(minutes=1), source="demo"),
    ], now=now)
    restored = cache.load(now=now)

    assert saved == 1
    assert [row.id for row in restored] == ["nr:recent"]
    assert restored[0].headcode == "2S53"
    assert restored[0].restored_snapshot is True
    assert restored[0].status == "Last known • railway feed reconnecting"
    assert "awaiting a new live report" in restored[0].position_label


def test_invalid_or_future_snapshot_rows_are_ignored(tmp_path):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    path = tmp_path / "rail_positions.json"
    path.write_text(
        '{"trains":[{"not":"a vehicle"},{"id":"nr:future","type":"train",'
        '"name":"1A01","lat":52,"lon":-1,"source":"Network Rail TD",'
        '"last_updated":"2026-11-03T14:00:00Z"}]}',
        encoding="utf-8",
    )

    assert RailPositionSnapshot(path).load(now=now) == []
