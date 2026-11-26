"""What a passenger train looks like: its class and its coaches.

Two sources, never mixed up:

* The coaches themselves (letters, first class, toilets) come from Darwin's
  formation messages when the operator publishes them. That is this train.
* The class comes from the timetable: an electric unit's timing load *is* its
  class ("390"), a diesel unit's is a letter for a group of classes, narrowed
  by operator where only one fits. Without Darwin, the diagram shows the
  class's usual length and says so; the exact unit is never guessed.
"""
from __future__ import annotations

# class: (name, usual coaches per unit, traction)
CLASSES: dict[str, tuple[str | None, tuple[int, ...], str]] = {
    "139": ("Parry People Mover", (1,), "diesel"),
    "142": ("Pacer", (2,), "diesel"), "143": ("Pacer", (2,), "diesel"), "144": ("Pacer", (2,), "diesel"),
    "150": ("Sprinter", (2,), "diesel"), "153": ("Super Sprinter", (1,), "diesel"),
    "155": ("Super Sprinter", (2,), "diesel"), "156": ("Super Sprinter", (2,), "diesel"),
    "158": ("Express Sprinter", (2, 3), "diesel"), "159": ("South Western Turbo", (3,), "diesel"),
    "165": ("Networker Turbo", (2, 3), "diesel"), "166": ("Networker Turbo Express", (3,), "diesel"),
    "168": ("Clubman", (2, 3, 4), "diesel"), "170": ("Turbostar", (2, 3), "diesel"),
    "171": ("Turbostar", (2, 4), "diesel"), "172": ("Turbostar", (2, 3), "diesel"),
    "175": ("Coradia", (2, 3), "diesel"), "180": ("Adelante", (5,), "diesel"),
    "185": ("Desiro", (3,), "diesel"), "195": ("Civity", (2, 3), "diesel"),
    "196": ("Civity", (2, 4), "diesel"), "197": ("Civity", (2, 3), "diesel"),
    "220": ("Voyager", (4,), "diesel"), "221": ("Super Voyager", (5,), "diesel"),
    "222": ("Meridian", (5, 7), "diesel"), "230": (None, (3,), "diesel"),
    "231": ("FLIRT", (4,), "diesel"), "318": (None, (3,), "electric"), "320": (None, (3,), "electric"),
    "321": (None, (4,), "electric"), "323": (None, (3,), "electric"), "331": ("Civity", (3, 4), "electric"),
    "333": (None, (4,), "electric"), "334": ("Juniper", (3,), "electric"), "345": ("Aventra", (9,), "electric"),
    "350": ("Desiro", (4,), "electric"), "357": ("Electrostar", (4,), "electric"),
    "360": ("Desiro", (4, 5), "electric"), "365": ("Networker Express", (4,), "electric"),
    "375": ("Electrostar", (3, 4), "electric"), "376": ("Electrostar", (5,), "electric"), "377": ("Electrostar", (3, 4, 5), "electric"),
    "378": ("Capitalstar", (5,), "electric"), "379": ("Electrostar", (4,), "electric"),
    "380": ("Desiro", (3, 4), "electric"), "385": ("AT200", (3, 4), "electric"),
    "387": ("Electrostar", (4,), "electric"), "390": ("Pendolino", (9, 11), "electric"),
    "395": ("Javelin", (6,), "electric"), "397": ("Nova 2", (5,), "electric"),
    "398": ("Citylink", (3,), "electric"), "399": ("Citylink tram-train", (3,), "electric"),
    "444": ("Desiro", (5,), "electric"), "450": ("Desiro", (4,), "electric"),
    "455": (None, (4,), "electric"), "456": (None, (2,), "electric"), "458": ("Juniper", (5,), "electric"), "465": ("Networker", (4,), "electric"),
    "466": ("Networker", (2,), "electric"), "484": (None, (2,), "electric"), "507": (None, (3,), "electric"),
    "508": (None, (3,), "electric"), "700": ("Desiro City", (8, 12), "electric"),
    "701": ("Aventra", (5, 10), "electric"), "707": ("Desiro City", (5,), "electric"),
    "710": ("Aventra", (4, 5), "electric"), "717": ("Desiro City", (6,), "electric"),
    "720": ("Aventra", (5,), "electric"), "730": ("Aventra", (3, 5), "electric"),
    "745": ("FLIRT", (12,), "electric"), "755": ("FLIRT", (3, 4), "bi-mode"),
    "756": ("FLIRT", (3, 4), "bi-mode"), "769": ("Flex", (4,), "bi-mode"), "777": (None, (4,), "electric"),
    "800": ("Azuma", (5, 9), "bi-mode"), "801": ("Azuma", (5, 9), "electric"),
    "802": ("Intercity Express Train", (5, 9), "bi-mode"), "803": (None, (5,), "electric"), "805": ("Evero", (5,), "bi-mode"),
    "807": ("Evero", (7,), "electric"), "810": ("Aurora", (5,), "bi-mode"),
}
# Operator-specific names for the same build (GWR and Hull Trains call their 800/802 "IET").
_NAMES = {("800", "Great Western Railway"): "Intercity Express Train",
          ("800", "LNER"): "Azuma", ("802", "TransPennine Express"): "Nova 1",
          ("802", "Hull Trains"): "Paragon", ("802", "Great Western Railway"): "Intercity Express Train"}
# CIF diesel-unit timing loads are letters; only where the operator settles the class.
_DMU_BY_OPERATOR = {
    ("E", "CrossCountry"): "170", ("E", "Northern"): "158", ("E", "West Midlands Trains"): "170",
    ("E", "ScotRail"): "170", ("E", "Transport for Wales"): "158", ("E", "Chiltern Railways"): "168",
    ("E", "TransPennine Express"): "185",
    ("V", "CrossCountry"): "220", ("X", "South Western Railway"): "159",
    ("S", "Northern"): "150", ("S", "Great Western Railway"): "150", ("S", "Transport for Wales"): "150",
    ("T", "Great Western Railway"): "165", ("T", "Chiltern Railways"): "165",
    # Only one class in each fleet for these codes (seen live).
    # (EMR's "E" may be a 158 or 170 and its "S" a 153 or 156: left unnamed.)
    ("S", "ScotRail"): "156", ("E", "Great Western Railway"): "158",
}
# The feeds spell some operators two ways ("Northern" and "Northern Trains").
_OPERATOR_ALIASES = {"northern trains": "Northern", "london northwestern railway": "West Midlands Trains",
                     "west midlands railway": "West Midlands Trains", "scotrail trains": "ScotRail",
                     "gwr": "Great Western Railway", "emr": "East Midlands Railway",
                     "cross country": "CrossCountry", "transpennine": "TransPennine Express",
                     "transpennine trains": "TransPennine Express", "tpe": "TransPennine Express"}


# Facilities which are not present in Darwin's formation messages.  Entries
# are intentionally narrow: a class can have different interiors at different
# operators, and even within an operator's sub-fleets.  Add a profile only when
# its source supports every advertised facility.  Live Darwin coach class and
# toilet data always takes precedence over these normal/unit-profile values.
_FACILITY_PROFILES: dict[tuple[str, str], dict] = {
    ("170", "CrossCountry"): {
        "facilities": {
            "wifi": True,
            "bicycle_spaces": True,
            "wheelchair_spaces": True,
            "toilets": True,
        },
        # CrossCountry 170/1 units are three-car sets; 170/5 units are two-car.
        # Locations are deliberately not asserted here because they vary by
        # orientation.  The operator's current seating plan identifies Coach C
        # for the accessible area and Coach A for the other toilet.
        "unit_lengths": {"1701": 3, "1705": 2},
        "coach_letters": {2: ["A", "C"], 3: ["A", "B", "C"]},
        "coach_facilities": {
            "A": {"toilet": "standard"},
            "C": {"toilet": "accessible", "bicycle": True, "wheelchair_space": True},
        },
        "source_name": "CrossCountry fleet information",
        "source_url": "https://www.crosscountrytrains.co.uk/travel-information/on-board/about-our-trains",
    },
}


def canonical_operator(operator: str | None) -> str:
    name = str(operator or "").strip()
    return _OPERATOR_ALIASES.get(name.casefold(), name)


_POWER_TRACTION = {"DMU": "diesel", "DEM": "diesel", "HST": "diesel", "D": "diesel",
                   "EMU": "electric", "EML": "electric", "E": "electric", "ED": "electro-diesel"}


def train_class(power_type: str | None, timing_load: str | None, operator: str | None) -> str | None:
    power = str(power_type or "").strip().upper()
    load = str(timing_load or "").strip().upper()
    if power == "DMU" and load and not load.isdigit():
        return _DMU_BY_OPERATOR.get((load[:1], canonical_operator(operator)))
    digits = load[:3]
    if power in {"EMU", "DMU", "DEM", "EML"} and digits in CLASSES:
        return digits
    return None


def _speed(train) -> int:
    try:
        return int(str(getattr(train, "booked_speed", "") or "0").strip() or 0)
    except ValueError:
        return 0


def _coach(raw: dict) -> dict:
    toilet = str(raw.get("toilet") or "").strip().lower()
    return {
        "letter": str(raw.get("letter") or "").strip()[:3] or None,
        "first": str(raw.get("class") or "").strip().lower() == "first",
        "toilet": "accessible" if toilet == "accessible" else "standard" if toilet == "standard" else None,
    }


def _allocated_facilities(code: str | None, operator: str | None, units: list[str]) -> dict | None:
    """Return a conservative operator profile, refined by an allocation.

    Without an allocation only class-wide facilities are returned: coach
    letters and locations remain unknown and the API labels the basis clearly.
    """
    if not code or not operator:
        return None
    profile = _FACILITY_PROFILES.get((code, operator))
    if not profile:
        return None
    allocated = next((str(unit).strip() for unit in units if str(unit).strip()), "")
    length = next((count for prefix, count in profile.get("unit_lengths", {}).items()
                   if allocated.startswith(prefix)), None)
    letters = profile.get("coach_letters", {}).get(length) if length else None
    letters = letters or ([chr(ord("A") + index) for index in range(length)] if length else [])
    coach_facilities = profile.get("coach_facilities", {})
    return {
        "facilities": dict(profile.get("facilities") or {}),
        "coaches": [{"letter": letter, "first": False, "toilet": None,
                     **coach_facilities.get(letter, {})} for letter in letters],
        "basis": "allocated_unit" if allocated and length else "operator_class",
        "source_name": profile.get("source_name"),
        "source_url": profile.get("source_url"),
    }


def describe(train, coaches: list[dict] | None) -> dict | None:
    """The diagram for a passenger train, or None when there is nothing honest to draw."""
    category = str(getattr(train, "train_category", "") or "").upper()
    if category[:1] in {"E", "J", "P"} or str(getattr(train, "headcode", "") or "")[:1] in {"0", "3", "4", "5", "6", "7", "8"}:
        return None   # freight, empty stock and engineering trains: no coaches to show
    operator = canonical_operator(getattr(train, "operator", None)) or None
    code = train_class(getattr(train, "power_type", None), getattr(train, "timing_load", None), operator)
    name, usual, traction = CLASSES.get(code, (None, (), None)) if code else (None, (), None)
    name = _NAMES.get((code, operator), name)
    known = [_coach(row) for row in (coaches or []) if isinstance(row, dict)]
    units = [str(unit) for unit in (getattr(train, "formation", None) or []) if str(unit).strip()][:4]
    allocated = _allocated_facilities(code, operator, units)
    power = str(getattr(train, "power_type", "") or "").strip().upper()
    hauled = power in {"D", "E", "ED"}
    inferred = False
    inference_reason = None
    if not code and power in {"DMU", "DEM"} and operator == "East Midlands Railway" and _speed(train) >= 110:
        # EMR's 125 mph diesel units with no class in the timetable are its
        # Meridians: its Aurora (810) workings carry "810".
        code, inferred = "222", True
        inference_reason = "operator, diesel unit and 125 mph timing"
        name, usual, traction = CLASSES[code]
    if not code and power in {"DMU", "DEM"} and operator == "TransPennine Express" and 0 < _speed(train) <= 110:
        # TPE's diesel-only passenger fleet is Class 185. Nova 1 Class 802
        # workings are bi-mode and normally carry their numeric timing load.
        code, inferred = "185", True
        inference_reason = "operator, diesel unit and 100 mph timing"
        name, usual, traction = CLASSES[code]
    if not code:
        # Class not settled by the timetable: still drawn, as a generic train of its traction.
        traction = _POWER_TRACTION.get(power)
        name = "High Speed Train" if power == "HST" else None
    published = bool(known)
    if not known and allocated:
        known = allocated["coaches"]
    return {
        "class": code, "name": name, "traction": traction, "operator": operator,
        "hauled": hauled, "generic": not code, "inferred": inferred,
        "inference_reason": inference_reason,
        "coaches": known,
        # Unit numbers from the allocation feed (e.g. "170636"), when it has them.
        "units": units,
        "coaches_known": bool(known),
        "coaches_published": published,
        "facilities": allocated["facilities"] if allocated else {},
        "facilities_source": allocated["source_name"] if allocated else None,
        "facilities_source_url": allocated["source_url"] if allocated else None,
        "facilities_typical": bool(allocated),
        "facilities_basis": allocated["basis"] if allocated else None,
        # Without Darwin: the class's usual unit lengths ("2 or 3"), never a claim about this train.
        "usual": list(usual),
        "source": "Darwin (National Rail)" if published else "Allocated unit profile" if allocated and allocated["basis"] == "allocated_unit" else "Operator class profile" if allocated else "Timetable",
    }
