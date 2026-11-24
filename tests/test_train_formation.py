"""A train is drawn from its real coaches (Darwin) or its class's usual length, never guessed."""
import json
from datetime import datetime, timezone

from app.collectors.darwin_kafka import DarwinKafkaFeed
from app.config import Settings
from app.models import Vehicle
from app.state import LiveState
from app.train_formation import describe, train_class


def _train(**changes):
    values = dict(id="nr:t", type="train", name="1M23", lat=52.5, lon=-1.2, train_uid="P19489",
                  service_date="2026-11-24", operator="CrossCountry", power_type="DMU", timing_load="E",
                  headcode="1M23", train_category="XX", last_updated=datetime.now(timezone.utc))
    values.update(changes)
    return Vehicle(**values)


def test_class_comes_from_the_timetable_and_letters_only_where_the_operator_settles_it():
    assert train_class("EMU", "390", "Avanti West Coast") == "390"
    assert train_class("DMU", "E", "CrossCountry") == "170"
    assert train_class("DMU", "E", "East Midlands Railway") is None   # 158 or 170: not guessed
    assert train_class("D", "1600", "DB Cargo UK") is None


def test_without_darwin_the_class_usual_length_is_given_and_marked_as_such():
    drawn = describe(_train(power_type="EMU", timing_load="390", operator="Avanti West Coast"), [])
    assert drawn["name"] == "Pendolino" and drawn["usual"] == [9, 11]
    assert drawn["coaches_known"] is False and drawn["coaches"] == []


def test_freight_and_unknown_trains_are_not_drawn():
    assert describe(_train(headcode="6M23", train_category="EE"), []) is None
    assert describe(_train(power_type="DMU", timing_load="E", operator="East Midlands Railway"), []) is None


def test_darwin_formation_messages_give_this_trains_coaches():
    feed = DarwinKafkaFeed(Settings(), LiveState())
    feed.handle(json.dumps({"uR": {
        "TS": {"rid": "202611247612345", "uid": "P19489", "ssd": "2026-11-24", "Location": []},
        "scheduleFormations": {"rid": "202611247612345", "formation": {"fid": "f1", "coaches": {"coach": [
            {"coachNumber": "A", "coachClass": "First", "toilet": {"status": "InService", "#text": "None"}},
            {"coachNumber": "B", "coachClass": "Standard", "toilet": "Standard"},
            {"coachNumber": "C", "coachClass": "Standard", "toilet": {"status": "NotInService", "#text": "Accessible"}},
        ]}}},
    }}).encode())
    coaches = feed.formation_for("P19489", "2026-11-24")
    assert [c["letter"] for c in coaches] == ["A", "B", "C"]
    drawn = describe(_train(), coaches)
    assert drawn["coaches_known"] and drawn["name"] == "Turbostar"
    assert [(c["letter"], c["first"], c["toilet"]) for c in drawn["coaches"]] == [
        ("A", True, None), ("B", False, "standard"), ("C", False, None)]   # an out-of-service toilet is not shown
    assert feed.report()["formations"] == 1
    assert feed.formation_for("P19489", "2026-11-25") == []
