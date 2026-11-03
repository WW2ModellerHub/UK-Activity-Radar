from __future__ import annotations

from typing import Any


# Network Rail/Rail Delivery Group two-character timetable operator codes.
# Unknown codes are displayed, not guessed.
ATOC_OPERATOR_NAMES = {
    "AW": "Transport for Wales", "CC": "c2c", "CH": "Chiltern Railways",
    "CS": "Caledonian Sleeper", "EM": "East Midlands Railway", "ES": "Eurostar",
    "GC": "Grand Central", "GN": "Great Northern", "GR": "LNER",
    "GW": "Great Western Railway", "GX": "Gatwick Express", "HT": "Hull Trains",
    "HX": "Heathrow Express", "IL": "Island Line", "LE": "Greater Anglia",
    "LM": "West Midlands Trains", "LO": "London Overground", "LT": "London Underground",
    "ME": "Merseyrail", "NT": "Northern", "SE": "Southeastern", "SN": "Southern",
    "SR": "ScotRail", "SW": "South Western Railway", "TL": "Thameslink",
    "TP": "TransPennine Express", "VT": "Avanti West Coast", "XC": "CrossCountry",
    "XR": "Elizabeth line",
}

# Numeric TRUST sector codes are distinct from the two-character timetable
# (ATOC) codes above. Keep this deliberately conservative: an unknown sector
# is described generically instead of exposing an internal code to users.
TRUST_SECTOR_OPERATOR_NAMES = {
    "02": "JSD Rail Research & Development",
    "04": "DB Cargo Charters",
    "05": "DB Cargo UK",
    "06": "Eurostar",
    "07": "Rail Operations Group",
    "08": "DB Cargo International",
    "10": "Serco Rail Operations",
    "11": "Freightliner Heavy Haul",
    "13": "Europorte",
    "15": "Network Rail On-Track Machines",
    "16": "Loram",
    "17": "Hanson & Hall Rail Services",
    "18": "Swanage Railway",
    "19": "South Yorkshire Supertram",
    "20": "TransPennine Express",
    "21": "Greater Anglia",
    "22": "Grand Central",
    "23": "Northern Trains",
    "25": "Great Western Railway",
    "27": "CrossCountry",
    "28": "East Midlands Railway",
    "29": "West Midlands Trains",
    "30": "London Overground",
    "31": "Network Rail",
    "33": "Elizabeth line",
    "34": "DC Rail",
    "35": "Caledonian Sleeper",
    "36": "Vintage Trains",
    "39": "Harsco Rail",
    "40": "Balfour Beatty Rail",
    "42": "Colas Rail",
    "43": "Amey Fleet Services",
    "45": "Lumo",
    "46": "Swietelsky Babcock Rail",
    "49": "VolkerRail",
    "50": "West Coast Railways",
    "51": "North Yorkshire Moors Railway",
    "52": "Pre Metro Operations",
    "54": "GB Railfreight",
    "55": "Hull Trains",
    "56": "Tyne and Wear Metro",
    "60": "ScotRail",
    "61": "LNER",
    "64": "Merseyrail",
    "65": "Avanti West Coast",
    "66": "Freightliner Intermodal",
    "71": "Transport for Wales",
    "74": "Chiltern Railways",
    "79": "c2c",
    "80": "Southeastern",
    "84": "South Western Railway",
    "85": "Island Line",
    "86": "Heathrow Express",
    "88": "Govia Thameslink Railway",
    "89": "Locomotive Services",
    "95": "Varamis Rail",
    "97": "Direct Rail Services",
}

POWER_TYPE_NAMES = {
    "D": "Diesel locomotive", "DEM": "Diesel-electric multiple unit",
    "DMU": "Diesel multiple unit", "E": "Electric locomotive",
    "ED": "Electro-diesel locomotive", "EMU": "Electric multiple unit",
    "HST": "High Speed Train",
}


def operator_name(code: str | None, fallback: str | None = None) -> str | None:
    normalised = str(code or "").strip().upper()
    if not normalised:
        return fallback
    return ATOC_OPERATOR_NAMES.get(normalised) or fallback or "Rail operator"


def trust_operator_name(code: str | int | None, fallback: str | None = None) -> str | None:
    normalised = str(code or "").strip()
    if not normalised:
        return fallback
    if normalised.isdigit():
        normalised = normalised.zfill(2)
    return TRUST_SECTOR_OPERATOR_NAMES.get(normalised) or fallback


def power_type_name(code: str | None) -> str | None:
    normalised = str(code or "").strip().upper()
    if not normalised:
        return None
    return POWER_TYPE_NAMES.get(normalised) or normalised


def infer_train_family(*, operator_code: str | None, power_type: str | None,
                       origin: str | None = None, destination: str | None = None) -> dict[str, Any] | None:
    """Return a transparent family-level inference, never an exact allocation."""
    operator = str(operator_code or "").strip().upper()
    power = str(power_type or "").strip().upper()
    route = f"{origin or ''} {destination or ''}".upper()
    if operator == "XC" and power in {"DMU", "DEM"}:
        long_distance = any(name in route for name in (
            "EDINBURGH", "DARLINGTON", "NEWCASTLE", "YORK", "BIRMINGHAM",
            "BRISTOL", "PLYMOUTH", "READING", "BOURNEMOUTH",
        ))
        return {
            "label": "Class 220/221 Voyager family" if long_distance else "CrossCountry diesel multiple unit",
            "confidence": "likely" if long_distance else "possible",
            "explanation": "Inferred from the CrossCountry operator, diesel timing type and published route; the exact unit is not in the timetable feed.",
        }
    if operator == "CS":
        return {
            "label": "Caledonian Sleeper locomotive-hauled train", "confidence": "likely",
            "explanation": "Inferred from the timetable operator; the exact locomotive and coaches are not confirmed by the live feed.",
        }
    return None


def is_charter_operator(code: str | None) -> bool:
    # A 1Z headcode alone means special passenger working, not a railtour.
    return str(code or "").strip().upper() in {"LS", "WR"}
