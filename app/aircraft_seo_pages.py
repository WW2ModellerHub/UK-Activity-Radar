"""Server-rendered landing pages for common UK aircraft-tracking searches.

The interactive map is useful once it has loaded, but it cannot by itself
answer the questions that bring somebody to the site.  These pages provide
an honest, readable answer first, then send the visitor to a deliberately
filtered map view.

``aircraft_landing_page`` is intentionally free of FastAPI dependencies so it
can be cached and tested like the other SEO page builders.  The ``view`` query
values exposed by :func:`aircraft_landing_views` form the small integration
contract with the live-map client.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from types import MappingProxyType

from .seo_pages import _breadcrumbs, _shell


@dataclass(frozen=True)
class AircraftLandingPage:
    path: str
    view: str
    title: str
    description: str
    eyebrow: str
    heading: str
    lede: str
    map_label: str
    coverage: tuple[str, ...]
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    related: tuple[str, ...]


_PAGES = MappingProxyType({
    "/military-aircraft-tracker": AircraftLandingPage(
        path="/military-aircraft-tracker",
        view="military-aircraft",
        title="UK Military Aircraft Tracker | Live Map",
        description=(
            "Track publicly visible military aircraft over the UK on a live map. "
            "Check type, callsign, altitude and report age with clear ADS-B limits."
        ),
        eyebrow="Live UK aircraft map",
        heading="UK military aircraft tracker",
        lede=(
            "See military aircraft that are broadcasting a usable public position over "
            "the UK, then check the evidence behind each identification."
        ),
        map_label="Open the live military aircraft map",
        coverage=(
            "Check the “updated” time on every aircraft. It is the time of the last "
            "received report, not a promise that the icon is its exact position now.",
            "No marker does not mean no military aircraft are present. Aircraft may be "
            "too low for nearby receivers, outside coverage, transmitting limited data, "
            "filtered by a provider or not broadcasting a public position at all.",
        ),
        sections=(
            ("What the military map shows", (
                "Where the public feed provides it, a marker can show a callsign, "
                "registration or hex identifier, aircraft type, altitude, ground speed, "
                "direction of travel and the age of the latest report. The map uses the "
                "available type data to distinguish fast jets, trainers, tankers, large "
                "transport aircraft and helicopters.",
                "Positions received a few seconds apart can be animated between reports. "
                "That makes movement easier to follow, but the animation is an estimate; "
                "the received report and its age remain the evidence that matters.",
            )),
            ("How military aircraft are identified", (
                "A military-looking callsign alone is not conclusive. A stronger match "
                "combines the aircraft identifier, registration database, type code, "
                "known operator and flight behaviour. Callsigns can change or be reused, "
                "and database records can be incomplete.",
                "The map therefore describes the type or role supported by the available "
                "metadata instead of guessing a specific mission. Training, transport, "
                "refuelling and maritime-patrol flights can all appear during routine "
                "operations.",
            )),
            ("Why a military flight may disappear", (
                "ADS-B and Mode S reception is based on radio line of sight. Terrain, "
                "buildings, low altitude and receiver availability all affect coverage. "
                "A marker can pause or disappear when reports stop, then return when a "
                "different receiver hears the aircraft.",
                "Use the map as a public-data observation tool, not as an operational or "
                "safety service. Never approach an airfield or restricted site because of "
                "a marker, and follow official instructions around active operations.",
            )),
        ),
        related=(
            "/helicopter-tracker",
            "/what-plane-is-above-me",
            "/guides/how-ads-b-aircraft-tracking-works",
        ),
    ),
    "/helicopter-tracker": AircraftLandingPage(
        path="/helicopter-tracker",
        view="helicopters",
        title="UK Helicopter Tracker | Live Aircraft Map",
        description=(
            "Find publicly visible helicopters flying over the UK. View type, altitude, "
            "direction and report age, and understand gaps in low-level ADS-B coverage."
        ),
        eyebrow="Live UK aircraft map",
        heading="UK helicopter tracker",
        lede=(
            "Find helicopters broadcasting a usable public position near you and across "
            "the UK, with distinct helicopter symbols and report-age information."
        ),
        map_label="Open the live helicopter map",
        coverage=(
            "Check the “updated” time before treating a helicopter marker as current. "
            "Smooth motion between reports is an estimate, and a stale marker can lag "
            "behind the real aircraft.",
            "No marker does not mean the sky is clear. Helicopters often fly below the "
            "radio horizon, where hills, buildings and the spacing of receivers create "
            "larger coverage gaps than for high-altitude airliners.",
        ),
        sections=(
            ("What you can learn from a marker", (
                "A live marker may include the registration or callsign, aircraft model "
                "or ICAO type code, altitude, speed, direction and latest report time. "
                "Those fields can help separate a light private helicopter from a larger "
                "transport, rescue or military type.",
                "When the exact model is unavailable, the honest result is a generic "
                "helicopter. Similar silhouettes, recycled callsigns and incomplete "
                "databases make a specific identification unsafe without supporting data.",
            )),
            ("Why helicopter tracking is harder", (
                "Public flight tracking relies heavily on ground receivers. A helicopter "
                "at a few hundred feet can be hidden from one receiver while an airliner "
                "at cruising altitude is heard across a much wider area. Landing, hovering "
                "or descending behind terrain can end a track abruptly.",
                "Some helicopters do not transmit a public position, and providers can "
                "limit particular records. A track can also resume with a new report after "
                "a gap; that does not mean the aircraft vanished and returned physically.",
            )),
            ("Identify a helicopter above you", (
                "Start with your location, then compare the marker's bearing, direction and "
                "altitude with what you can see. Sound can arrive after the aircraft has "
                "moved, especially in wind, so the nearest map marker is not automatically "
                "the helicopter you heard.",
                "Do not infer a police, medical or rescue mission from circling alone. Open "
                "the aircraft details and look for corroborating identity data, while "
                "respecting emergency scenes and private individuals on the ground.",
            )),
        ),
        related=(
            "/police-helicopter-tracker",
            "/air-ambulance-tracker",
            "/military-aircraft-tracker",
            "/what-plane-is-above-me",
        ),
    ),
    "/police-helicopter-tracker": AircraftLandingPage(
        path="/police-helicopter-tracker",
        view="police-helicopters",
        title="Police Helicopter Tracker UK | Live Map",
        description=(
            "Check publicly visible UK police-helicopter flights on a live aircraft map. "
            "See identity evidence and report age without guessing an operation or incident."
        ),
        eyebrow="Public aircraft data",
        heading="Police helicopter tracker for the UK",
        lede=(
            "Check whether a publicly trackable helicopter near you can be identified as "
            "police-operated, while keeping coverage gaps and uncertainty in view."
        ),
        map_label="Open the police helicopter map",
        coverage=(
            "Check the “updated” time on the aircraft card. A marker may be estimated "
            "between received reports and can remain visible briefly after reception ends.",
            "No marker does not mean no police aircraft are nearby. A police helicopter may "
            "be below receiver coverage, transmit no usable public position, appear without "
            "an identifiable callsign or be limited by the data provider.",
        ),
        sections=(
            ("How a police helicopter is identified", (
                "The strongest public identification uses a registration or aircraft "
                "identifier associated with a police aviation operator, supported by the "
                "type and callsign. Circling, hovering or flying at night is not enough on "
                "its own: air ambulances, survey flights and training aircraft can behave "
                "in similar ways.",
                "If the feed cannot support the role, the map should label the aircraft as "
                "a helicopter rather than present a guess as fact. Callsigns and fleet "
                "assignments can change, so always read the identity fields and freshness "
                "together.",
            )),
            ("What the map can tell you", (
                "Available details can include the aircraft type, registration, altitude, "
                "speed, direction and the time of its last public report. These explain the "
                "flight path on the map; they do not reveal why an aircraft was tasked or "
                "what is happening on the ground.",
                "For information about a local incident, use statements from the relevant "
                "police force or emergency service. Do not travel towards, interfere with "
                "or publish sensitive details about an active operation because of a live "
                "aircraft marker.",
            )),
            ("Why the marker can pause or disappear", (
                "Police helicopters often operate low over towns and varied terrain. Radio "
                "line of sight can be lost behind buildings or hills, and reports can reach "
                "the public feed late. The marker may pause, correct forward or disappear "
                "until another receiver hears it.",
                "This independent tracker is not connected to police dispatch and is not "
                "an emergency service. Call 999 when there is an immediate danger; do not "
                "use a flight-tracking website to make an emergency decision.",
            )),
        ),
        related=(
            "/helicopter-tracker",
            "/air-ambulance-tracker",
            "/what-plane-is-above-me",
            "/guides/how-ads-b-aircraft-tracking-works",
        ),
    ),
    "/air-ambulance-tracker": AircraftLandingPage(
        path="/air-ambulance-tracker",
        view="air-ambulances",
        title="UK Air Ambulance Tracker | Live HEMS Map",
        description=(
            "View publicly visible UK air-ambulance and HEMS helicopters on a live map. "
            "Check type, altitude and report age with clear coverage and safety guidance."
        ),
        eyebrow="Public rescue-aircraft data",
        heading="UK air ambulance tracker",
        lede=(
            "View air-ambulance and HEMS aircraft that can be identified in the public "
            "flight feed, without treating an estimated marker as an emergency report."
        ),
        map_label="Open the live air ambulance map",
        coverage=(
            "Check the “updated” time on each aircraft. Low-level reception can stop during "
            "descent or landing, so the last marker may be stale or estimated between reports.",
            "No marker does not mean no air ambulance is operating nearby. Some aircraft "
            "are outside receiver coverage, do not transmit a usable public position or "
            "cannot be confidently identified from the available data.",
        ),
        sections=(
            ("What HEMS means on the map", (
                "HEMS means Helicopter Emergency Medical Service. An air-ambulance aircraft "
                "can sometimes be matched using its identifier, registration, operator and "
                "type. Where available, the map also shows altitude, speed, direction and "
                "the time of the latest public position report.",
                "A recognised aircraft is not proof of an active medical mission. It may be "
                "repositioning, returning to base, training or undergoing a test flight. The "
                "map deliberately does not identify a patient or explain an incident.",
            )),
            ("Why air ambulances drop off the map", (
                "Medical helicopters frequently descend away from airfields and may land at "
                "unprepared sites. At low altitude, buildings, hills and trees can block the "
                "radio path to ground receivers. The public track may end before landing and "
                "resume after take-off.",
                "Reports can also arrive with delay. Animation can make the last two reports "
                "look smooth, but it does not create a new observation. Use the update age "
                "and track history to judge how current the display is.",
            )),
            ("Safety around medical flights", (
                "Keep well away from a landing or emergency scene. Rotor wash, debris and "
                "access routes create real hazards, and crews need space to work. Never use "
                "the map to approach a patient, landing site, hospital pad or operating base.",
                "For an emergency, call 999 and follow the operator's instructions. This is "
                "an independent view of public aircraft data, not a dispatch system and not "
                "a way to request or monitor medical help.",
            )),
        ),
        related=(
            "/helicopter-tracker",
            "/police-helicopter-tracker",
            "/what-plane-is-above-me",
            "/guides/how-ads-b-aircraft-tracking-works",
        ),
    ),
    "/what-plane-is-above-me": AircraftLandingPage(
        path="/what-plane-is-above-me",
        view="aircraft-near-me",
        title="What Plane Is Above Me? | Live UK Aircraft Map",
        description=(
            "Find the plane or helicopter above you with a live UK aircraft map. Compare "
            "type, altitude, direction and report age, and understand possible coverage gaps."
        ),
        eyebrow="Identify nearby aircraft",
        heading="What plane is above me?",
        lede=(
            "Use your location to find publicly visible aircraft nearby, then compare the "
            "live evidence instead of assuming the closest map marker is the one you saw."
        ),
        map_label="Find aircraft above me",
        coverage=(
            "Check the “updated” time first. The map can animate between received positions, "
            "but the real aircraft may be farther ahead when a report is old.",
            "No marker does not mean no aircraft is overhead. Low aircraft, terrain, receiver "
            "gaps, provider limits and aircraft without a public position can all leave a "
            "flight missing from the map.",
        ),
        sections=(
            ("How to match the aircraft you can see", (
                "Allow location access, then look at aircraft within the nearby view. Compare "
                "the direction from you, the marker's heading and altitude, and whether it is "
                "a plane or helicopter. A high aircraft may look close while being many miles "
                "away horizontally.",
                "Sound is a poor direction finder on its own. It takes time to reach you and "
                "can be carried by wind, so the aircraft may already be beyond the point where "
                "you first heard it. Contrails can also remain after the aircraft has moved on.",
            )),
            ("Read the identification details", (
                "The callsign may be an airline flight number, an operational callsign or a "
                "short code that changes between flights. Registration and the ICAO aircraft "
                "type code are usually better clues to the airframe. The map translates known "
                "type data into helicopter, propeller, airliner, fast-jet and transport icons.",
                "If the type database has no confident match, a generic aircraft symbol is "
                "more accurate than a guessed model. Search the displayed registration or "
                "type only after checking that the report is fresh and the flight path fits.",
            )),
            ("Why some flights are not shown", (
                "Public trackers combine radio reports heard by a network of receivers. High "
                "aircraft are usually heard widely; low aircraft can be screened by the local "
                "landscape. Reception, processing and internet delivery can each add delay.",
                "Some aircraft use surveillance signals that do not contain a public position, "
                "and some records are limited or anonymous. Treat the map as a helpful answer "
                "when a match exists, not a complete inventory of the sky.",
            )),
        ),
        related=(
            "/helicopter-tracker",
            "/military-aircraft-tracker",
            "/police-helicopter-tracker",
            "/air-ambulance-tracker",
            "/guides/how-ads-b-aircraft-tracking-works",
        ),
    ),
})


_RELATED_LABELS = MappingProxyType({
    "/military-aircraft-tracker": ("Military aircraft tracker", "Publicly visible military and special aircraft over the UK."),
    "/helicopter-tracker": ("UK helicopter tracker", "Helicopters near you, with low-level coverage explained."),
    "/police-helicopter-tracker": ("Police helicopter tracker", "Evidence-led police-aircraft identification without guessing an incident."),
    "/air-ambulance-tracker": ("Air ambulance tracker", "Publicly visible HEMS aircraft with clear safety guidance."),
    "/what-plane-is-above-me": ("What plane is above me?", "Match a nearby aircraft using direction, altitude, type and freshness."),
    "/guides/how-ads-b-aircraft-tracking-works": ("How ADS-B tracking works", "Why public aircraft coverage, identity and update speed can vary."),
})


def aircraft_landing_paths() -> tuple[str, ...]:
    """Return canonical paths in stable sitemap order."""
    return tuple(_PAGES)


def aircraft_landing_views() -> dict[str, str]:
    """Return the path-to-map-view contract used by the live-map client."""
    return {path: page.view for path, page in _PAGES.items()}


def aircraft_landing_page(origin: str, path: str) -> str:
    """Render one aircraft landing page, raising ``KeyError`` for unknown paths."""
    canonical_path = "/" + str(path or "").strip("/")
    page = _PAGES[canonical_path]
    clean_origin = str(origin or "").rstrip("/")
    canonical = clean_origin + page.path

    sections = "".join(
        f'<section><h2>{escape(heading)}</h2>'
        + "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
        + "</section>"
        for heading, paragraphs in page.sections
    )
    related = "".join(
        f'<li><a href="{escape(related_path, quote=True)}">'
        f'<strong>{escape(_RELATED_LABELS[related_path][0])}</strong>'
        f'<span>{escape(_RELATED_LABELS[related_path][1])}</span></a></li>'
        for related_path in page.related
    )
    coverage = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in page.coverage)
    map_href = f"/?view={escape(page.view, quote=True)}"
    body = f"""<article class="guide aircraft-landing">
<nav aria-label="Breadcrumb"><a href="/">Home</a> <span aria-hidden="true">/</span> <span aria-current="page">{escape(page.heading)}</span></nav>
<p class="eyebrow">{escape(page.eyebrow)}</p>
<h1>{escape(page.heading)}</h1><p class="lede">{escape(page.lede)}</p>
<p><a class="button" href="{map_href}">{escape(page.map_label)}</a></p>
<section aria-labelledby="live-data-limits"><h2 id="live-data-limits">Live-data limits</h2>{coverage}</section>
{sections}
<section aria-labelledby="related-trackers"><h2 id="related-trackers">Related trackers and guides</h2>
<ul class="guide-list">{related}</ul></section>
<p class="guide-cta"><a class="button" href="{map_href}">{escape(page.map_label)}</a></p>
</article>"""
    return _shell(
        title=page.title,
        description=page.description,
        canonical=canonical,
        body=body,
        json_ld={
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "WebPage",
                    "@id": f"{canonical}#webpage",
                    "url": canonical,
                    "name": page.heading,
                    "description": page.description,
                    "inLanguage": "en-GB",
                    "isPartOf": {
                        "@type": "WebSite",
                        "@id": f"{clean_origin}/#website",
                        "name": "UK Activity Radar",
                        "url": f"{clean_origin}/",
                    },
                    "about": {
                        "@type": "Thing",
                        "name": "Live aircraft tracking in the United Kingdom",
                    },
                },
                _breadcrumbs(
                    clean_origin,
                    canonical,
                    [("Home", "/"), (page.heading, page.path)],
                ),
            ],
        },
    )
