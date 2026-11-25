"""Station facilities from the National Rail Knowledgebase, as plain facts."""
from datetime import datetime

from app.seo_pages import station_knowledge_html
from app.station_knowledge import facts, hours_on, plain

STATION = {
    "staffingLevel": "Full Time",
    "stationAccessibility": {"stepFreeCategory": {"category": "A, Compliant step-free access to all platform(s)"},
                             "wheelchairsAvailable": True},
    "ticketBuying": {"ticketOffice": {"available": True, "openingTimes": [
        {"daysOfTheWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], "openingStatus": "Specific Hours",
         "openPeriod": [{"startTime": "05:20:00.000", "endTime": "22:30:00.000"}]},
        {"daysOfTheWeek": ["Sunday"], "openingStatus": "Closed", "openPeriod": []}]}, "ticketMachinesAvailable": True},
    "toiletsAndChanging": {"toilets": {"available": True, "accessibleToiletsAvailable": True, "babyChangingAvailable": True}},
    "lifts": {"available": True, "liftsInfo": [{}, {}, {}]},
    "carParks": {"parkingSpacesAvailable": True, "numberOfSpaces": 458, "numberOfAccessibleSpaces": 19},
    "cycling": {"cycleStorageAvailable": True, "spaces": {"numberOfSpaces": 222}, "sheltered": True},
    "staffAssistance": {"helpline": {"operatorContactDetails": {"note": "<p>08000 11 33 23</p>"}}},
    "transportLinks": {"replacementBus": {"available": True, "notes": "<p>Coaches leave from the Station front.</p>"}},
}


def test_the_facts_a_traveller_asks_come_out_in_plain_words():
    rows = dict(facts(STATION, datetime(2026, 11, 25)))   # a Wednesday
    assert rows["Step-free access"] == "Step-free to all platforms"
    assert rows["Ticket office"] == "Open today 05:20–22:30"
    assert rows["Toilets"] == "Yes, including accessible, baby changing"
    assert rows["Lifts"] == "3 lifts"
    assert rows["Car park"] == "458 spaces (19 accessible)"
    assert rows["Cycle parking"] == "222 spaces, sheltered"
    assert rows["Help line"] == "08000 11 33 23"
    assert dict(facts(STATION, datetime(2026, 11, 29)))["Ticket office"] == "Closed today"   # Sunday


def test_station_page_section_and_html_notes():
    html = station_knowledge_html("Leicester", {"station": STATION})
    assert "At Leicester station" in html and "Coaches leave from the Station front." in html
    assert station_knowledge_html("Nowhere", None) == ""
    assert plain("<p>A&#160;B</p>") == "A B"
    assert hours_on([{"daysOfTheWeek": ["Monday"], "openingStatus": "24 Hours"}], "Monday") == "24 hours"


def test_official_disruption_messages_become_plain_notices_worst_first():
    from app.seo_pages import station_notices_html
    from app.station_knowledge import disruption_items
    payload = {"incidents": [{"crsCode": "BHM", "disruptions": []}], "station_messages": [{"crsCode": "BHM", "stationDisruptions": [
        {"category": "Station", "severity": "Normal", "summary": "<p>The lift serving&nbsp;platform 12 is out of order.</p>"},
        {"category": "Train service", "severity": "Minor", "summary": 'Reduced service between Crewe and Stafford. Latest information can be found in the Disruptions area of the<a href="https://www.nationalrail.co.uk/disruptions/crewe-20261125/"> National Rail website</a>. '},
        {"category": "Station", "severity": "Normal", "summary": "<p>The lift serving&nbsp;platform 12 is out of order.</p>"},
    ]}]}
    notices = disruption_items(payload)
    assert [n["category"] for n in notices] == ["Train service", "Station"]       # train first, duplicate dropped
    assert notices[0]["text"] == "Reduced service between Crewe and Stafford."
    assert notices[0]["link"] == "https://www.nationalrail.co.uk/disruptions/crewe-20261125/"
    assert notices[1]["text"] == "The lift serving platform 12 is out of order."
    assert "Disruption at Birmingham New Street now" in station_notices_html("Birmingham New Street", notices)
    assert disruption_items(None) == [] and station_notices_html("X", []) == ""
