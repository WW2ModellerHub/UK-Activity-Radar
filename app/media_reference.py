"""What a vehicle of this kind looks like, when the exact one has no photo.

Live feeds rarely say which train unit or bus fleet number is running, and
Commons rarely has a photo of one particular bus. What people want to see is
the operator's colour scheme and the kind of vehicle: a GWR Class 800, an
Arriva bus, a Ryanair 737. This builds a Commons search for that, plus the
words a candidate photo must mention so a search hit for another operator or
type is never shown, and the plain label shown under the photo.
"""
from __future__ import annotations

import re

# Train operator (as named on the map) -> (names a photo may use, the fleet
# most often seen, the words for that fleet). One typical fleet per operator:
# the photo is labelled as an example of the operator's trains, not this one.
TRAIN_FLEETS: dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]] = {
    "great western railway": (("gwr", "great western railway"), "Class 800", ("class 800", "class 802", "iet")),
    "avanti west coast": (("avanti",), "Class 390 Pendolino", ("class 390", "pendolino")),
    "lner": (("lner",), "Azuma", ("azuma", "class 800", "class 801")),
    "crosscountry": (("crosscountry", "cross country"), "Class 220 Voyager", ("class 220", "class 221", "voyager")),
    "east midlands railway": (("east midlands railway", "emr"), "Class 222 Meridian", ("class 222", "meridian", "class 810", "class 158", "class 170", "class 360")),
    "northern": (("northern",), "Class 195", ("class 195", "class 331", "class 158", "class 150", "class 156")),
    "northern trains": (("northern",), "Class 195", ("class 195", "class 331", "class 158", "class 150", "class 156")),
    "transpennine express": (("transpennine", "tpe"), "Class 802 Nova 1", ("class 802", "nova", "class 185", "class 397")),
    "southern": (("southern",), "Class 377 Electrostar", ("class 377", "electrostar")),
    "thameslink": (("thameslink",), "Class 700", ("class 700", "desiro city")),
    "great northern": (("great northern",), "Class 717", ("class 717", "class 387", "class 700")),
    "southeastern": (("southeastern",), "Class 375 Electrostar", ("class 375", "class 465", "class 395", "class 707", "javelin")),
    "south western railway": (("south western railway", "swr"), "Class 444 Desiro", ("class 444", "class 450", "class 701", "class 159", "desiro")),
    "greater anglia": (("greater anglia",), "Class 745", ("class 745", "class 720", "class 755", "aventra", "flirt")),
    "c2c": (("c2c",), "Class 357 Electrostar", ("class 357", "class 720")),
    "chiltern railways": (("chiltern",), "Class 168 Clubman", ("class 168", "class 165", "class 68", "mark 3")),
    "scotrail": (("scotrail",), "Class 385", ("class 385", "class 380", "class 170", "class 43", "class 156", "class 158")),
    "transport for wales": (("transport for wales", "tfw", "trafnidiaeth"), "Class 197", ("class 197", "class 231", "class 756", "class 158", "class 175")),
    "west midlands trains": (("west midlands railway", "london northwestern", "west midlands trains"), "Class 350 Desiro", ("class 350", "class 196", "class 730", "class 172")),
    "london overground": (("london overground", "overground"), "Class 710 Aventra", ("class 710", "class 378", "aventra")),
    "elizabeth line": (("elizabeth line",), "Class 345 Aventra", ("class 345", "aventra")),
    "merseyrail": (("merseyrail",), "Class 777", ("class 777", "class 507", "class 508")),
    "grand central": (("grand central",), "Class 180 Adelante", ("class 180", "adelante")),
    "hull trains": (("hull trains",), "Class 802 Paragon", ("class 802", "paragon")),
    "lumo": (("lumo",), "Class 803", ("class 803",)),
    "caledonian sleeper": (("caledonian sleeper",), "Mark 5 sleeper", ("mark 5", "class 92", "class 73")),
    "heathrow express": (("heathrow express",), "Class 387", ("class 387", "class 332")),
    "gatwick express": (("gatwick express",), "Class 387", ("class 387",)),
    "eurostar": (("eurostar",), "e320", ("e320", "class 374", "class 373")),
    "db cargo uk": (("db cargo", "db schenker", "ews"), "Class 66", ("class 66", "class 60", "class 67")),
    "freightliner": (("freightliner",), "Class 66", ("class 66", "class 70", "class 90")),
    "freightliner heavy haul": (("freightliner",), "Class 66", ("class 66", "class 70")),
    "gb railfreight": (("gb railfreight", "gbrf"), "Class 66", ("class 66", "class 69", "class 73")),
    "direct rail services": (("direct rail services", "drs"), "Class 68", ("class 68", "class 66", "class 88", "class 37")),
    "colas rail": (("colas",), "Class 70", ("class 70", "class 66", "class 56", "class 37")),
    "network rail": (("network rail",), "test train", ("test train", "new measurement train", "class 43", "class 37", "class 97")),
}

# Bus operator NOC code -> group. Only codes and prefixes that are certain;
# an unknown operator gets no livery photo rather than another firm's.
BUS_GROUP_CODES = {
    "ARBB": "Arriva", "AMID": "Arriva", "ANWE": "Arriva", "ANEA": "Arriva", "AKSS": "Arriva",
    "ARHE": "Arriva", "AMSY": "Arriva", "ANUM": "Arriva", "AYKS": "Arriva",
    "FBRI": "First", "FMAN": "First", "FLDS": "First", "FGLA": "First", "FECS": "First",
    "FSYO": "First", "FYOR": "First", "FCYM": "First", "FPOT": "First", "FABD": "First",
    "FESX": "First", "FBRA": "First", "FHAL": "First", "FHUD": "First", "FKIR": "First",
    "GNEL": "Go North East", "BHBC": "Brighton & Hove", "OXBC": "Oxford Bus Company",
    "LOTH": "Lothian Buses", "TNXB": "National Express", "TCVW": "National Express",
    "WMSA": "National Express",
}
BUS_GROUP_PREFIXES = (("SC", "Stagecoach"),)
BUS_GROUP_NAMES = {
    "arriva": "Arriva", "stagecoach": "Stagecoach", "first": "First", "go-ahead": "Go-Ahead",
    "go north east": "Go North East", "national express": "National Express",
    "brighton & hove": "Brighton & Hove", "lothian": "Lothian Buses", "metroline": "Metroline",
    "transport uk": "Transport UK", "rotala": "Rotala", "oxford bus": "Oxford Bus Company",
}
BUS_GROUP_ALIASES = {
    "First": ("first bus", "firstbus", "first group", "firstgroup", "first "),
    "Arriva": ("arriva",), "Stagecoach": ("stagecoach",),
    "Go North East": ("go north east",), "Brighton & Hove": ("brighton & hove", "brighton and hove"),
    "Oxford Bus Company": ("oxford bus",), "Lothian Buses": ("lothian",),
    "National Express": ("national express",), "Go-Ahead": ("go-ahead", "go ahead"),
    "Metroline": ("metroline",), "Transport UK": ("transport uk", "abellio"), "Rotala": ("rotala", "diamond bus"),
}

AIRLINE_ALIASES = {
    "Ryanair": ("ryanair",), "easyJet": ("easyjet",), "British Airways": ("british airways",),
    "Loganair": ("loganair",), "Jet2": ("jet2",), "TUI Airways": ("tui",), "Virgin Atlantic": ("virgin atlantic",),
    "Wizz Air": ("wizz",), "Aer Lingus": ("aer lingus",), "KLM": ("klm",), "Air France": ("air france",),
    "Lufthansa": ("lufthansa",), "United Airlines": ("united airlines",), "American Airlines": ("american airlines",),
}
AIRLINE_CALLSIGNS = {
    "RYR": "Ryanair", "RUK": "Ryanair", "EZY": "easyJet", "EJU": "easyJet", "BAW": "British Airways",
    "SHT": "British Airways", "EFW": "British Airways", "LOG": "Loganair", "EXS": "Jet2", "TOM": "TUI Airways",
    "VIR": "Virgin Atlantic", "WZZ": "Wizz Air", "WUK": "Wizz Air", "EIN": "Aer Lingus", "EAI": "Aer Lingus",
    "KLM": "KLM", "AFR": "Air France", "DLH": "Lufthansa", "UAL": "United Airlines", "AAL": "American Airlines",
}


def bus_group(operator: str | None) -> str | None:
    text = str(operator or "").strip()
    if not text:
        return None
    code = text.upper()
    if code in BUS_GROUP_CODES:
        return BUS_GROUP_CODES[code]
    if re.fullmatch(r"[A-Z]{4}", code):
        for prefix, group in BUS_GROUP_PREFIXES:
            if code.startswith(prefix):
                return group
        return None
    lower = text.casefold()
    for key, group in BUS_GROUP_NAMES.items():
        if lower.startswith(key) or f" {key}" in f" {lower}":
            return group
    return None


def airline_for(callsign: str | None, operator: str | None = None) -> str | None:
    name = str(operator or "").strip()
    for airline in AIRLINE_ALIASES:
        if name and name.casefold() == airline.casefold():
            return airline
    prefix = re.sub(r"[^A-Z]", "", str(callsign or "").upper())[:3]
    if re.fullmatch(r"[A-Z]{3}", prefix) and re.match(r"^[A-Z]{3}\d", re.sub(r"\s+", "", str(callsign or "").upper())):
        return AIRLINE_CALLSIGNS.get(prefix)
    return None


def _model_keys(model: str) -> tuple[str, ...]:
    """Words a photo of this aircraft type must mention: 737, a320, c-130, chinook."""
    lower = model.casefold()
    keys: list[str] = []
    for word in re.findall(r"[a-z]*-?\d+[a-z0-9]*", lower):
        word = word.strip("-")
        base = re.match(r"([a-z]*-?\d+)", word)
        if base:
            keys.append(base.group(1))
            keys.append(base.group(1).replace("-", ""))
    for word in re.findall(r"[a-z]{5,}", lower):
        if word not in {"boeing", "airbus", "lockheed", "martin", "embraer", "bombardier", "leonardo",
                        "systems", "canada", "havilland", "sikorsky", "agustawestland", "eurocopter", "cessna", "piper"}:
            keys.append(word)
    return tuple(dict.fromkeys(k for k in keys if len(k) >= 3))


def reference_for(kind: str, operator: str | None = None, model: str | None = None,
                  callsign: str | None = None) -> dict | None:
    """Search plan for an example photo: query, required words and label.

    ``require`` is a list of groups; a photo must mention at least one word
    from every group (the operator, and the vehicle type).
    """
    kind = str(kind or "").lower()
    if kind == "train":
        entry = TRAIN_FLEETS.get(str(operator or "").strip().casefold())
        if not entry:
            return None
        aliases, fleet, fleet_keys = entry
        name = str(operator).strip()
        return {
            "query": f"{aliases[0].upper() if len(aliases[0]) <= 4 else aliases[0].title()} {fleet}",
            "require": [aliases, fleet_keys],
            "label": f"{name} — example of their trains, not this exact one",
        }
    if kind == "bus":
        group = bus_group(operator)
        if not group:
            return None
        return {
            "query": f"{group} bus",
            "require": [BUS_GROUP_ALIASES.get(group, (group.casefold(),)), ("bus", "double-decker", "double decker", "single-decker")],
            "label": f"{group} — example of their buses, not this exact one",
        }
    if kind in {"aircraft", "sar"}:
        model = str(model or "").strip()
        if not model:
            return None
        keys = _model_keys(model)
        if not keys:
            return None
        airline = airline_for(callsign, operator)
        if airline:
            return {
                "query": f"{airline} {model}",
                "require": [AIRLINE_ALIASES[airline], keys],
                "label": f"{airline} {model} — example of this type, not this exact aircraft",
                "fallback": {"query": f"{model} aircraft", "require": [keys],
                             "label": f"{model} — example of this type, not this exact aircraft"},
            }
        return {
            "query": f"{model} aircraft",
            "require": [keys],
            "label": f"{model} — example of this type, not this exact aircraft",
        }
    return None


def mentions_all(text: str, require: list) -> bool:
    hay = text.casefold()
    flat = hay.replace("-", "")
    return all(any(word in hay or word.replace("-", "") in flat for word in group) for group in require)


# A picture of the outside of the vehicle shows its colours; these do not.
NOT_THE_OUTSIDE = re.compile(
    r"\b(interior|inside|seats?|seating|cab|cockpit|flight deck|toilet|table|luggage|window|"
    r"logo|sign|signage|livery diagram|model|toy|scale|cabin|galley|wheel|bogie|engine detail|plaque)\b", re.I)


def shows_the_outside(text: str) -> bool:
    return not NOT_THE_OUTSIDE.search(text)
