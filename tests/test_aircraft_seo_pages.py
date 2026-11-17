"""Focused coverage for the high-intent aircraft landing pages."""
import json
import re

import pytest
from bs4 import BeautifulSoup

from app.aircraft_seo_pages import (
    aircraft_landing_page,
    aircraft_landing_paths,
    aircraft_landing_views,
)


EXPECTED = {
    "/military-aircraft-tracker": "military-aircraft",
    "/helicopter-tracker": "helicopters",
    "/police-helicopter-tracker": "police-helicopters",
    "/air-ambulance-tracker": "air-ambulances",
    "/what-plane-is-above-me": "aircraft-near-me",
}


def _structured_data(html: str) -> dict:
    encoded = re.search(r'<script type="application/ld\+json">(.*?)</script>', html)
    assert encoded
    return json.loads(encoded.group(1))


def test_registry_exposes_five_stable_paths_and_map_views():
    assert aircraft_landing_paths() == tuple(EXPECTED)
    assert aircraft_landing_views() == EXPECTED
    # Callers cannot accidentally mutate the module's registry through the
    # returned integration mapping.
    changed = aircraft_landing_views()
    changed.clear()
    assert aircraft_landing_views() == EXPECTED


@pytest.mark.parametrize(("path", "view"), EXPECTED.items())
def test_each_page_is_server_rendered_canonical_and_actionable(path, view):
    html = aircraft_landing_page("https://radar.example/", path)
    page = BeautifulSoup(html, "html.parser")

    assert page.html["lang"] == "en-GB"
    assert page.find("meta", attrs={"name": "viewport"})
    assert page.find("meta", attrs={"name": "robots"})["content"].startswith("index,follow")
    assert page.find("link", rel="canonical")["href"] == f"https://radar.example{path}"
    assert page.title.string.endswith(("Live Map", "Aircraft Map", "HEMS Map"))
    assert 110 <= len(page.find("meta", attrs={"name": "description"})["content"]) <= 165
    assert len(page.find_all("h1")) == 1
    assert page.find("nav", attrs={"aria-label": "Breadcrumb"})

    calls_to_action = page.select(f'a.button[href="/?view={view}"]')
    assert len(calls_to_action) == 2
    assert all(link.get_text(" ", strip=True) for link in calls_to_action)
    assert "Check the “updated” time" in page.get_text(" ", strip=True)
    assert "No marker does not mean" in page.get_text(" ", strip=True)
    assert len(page.select("section h2")) >= 5
    assert len(page.get_text(" ", strip=True).split()) >= 260


@pytest.mark.parametrize("path", EXPECTED)
def test_each_page_has_valid_webpage_and_breadcrumb_structured_data(path):
    structured = _structured_data(aircraft_landing_page("https://radar.example", path))
    graph = structured["@graph"]
    assert {node["@type"] for node in graph} == {"WebPage", "BreadcrumbList"}
    webpage = next(node for node in graph if node["@type"] == "WebPage")
    breadcrumb = next(node for node in graph if node["@type"] == "BreadcrumbList")
    assert webpage["url"] == f"https://radar.example{path}"
    assert webpage["inLanguage"] == "en-GB"
    assert webpage["isPartOf"]["url"] == "https://radar.example/"
    assert [item["position"] for item in breadcrumb["itemListElement"]] == [1, 2]
    assert breadcrumb["itemListElement"][-1]["item"] == webpage["url"]


def test_pages_have_distinct_search_intent_and_crawlable_related_links():
    titles = set()
    descriptions = set()
    all_paths = set(aircraft_landing_paths())
    for path in aircraft_landing_paths():
        page = BeautifulSoup(aircraft_landing_page("https://radar.example", path), "html.parser")
        titles.add(page.title.string)
        descriptions.add(page.find("meta", attrs={"name": "description"})["content"])
        related = {link["href"] for link in page.select(".guide-list a")}
        assert related
        assert related <= all_paths | {"/guides/how-ads-b-aircraft-tracking-works"}
        assert related & all_paths
    assert len(titles) == len(EXPECTED)
    assert len(descriptions) == len(EXPECTED)


def test_specialist_pages_do_not_overclaim_identity_or_safety():
    police = aircraft_landing_page("https://radar.example", "/police-helicopter-tracker")
    assert "Circling, hovering or flying at night is not enough" in police
    assert "not connected to police dispatch" in police
    medical = aircraft_landing_page("https://radar.example", "/air-ambulance-tracker")
    assert "not proof of an active medical mission" in medical
    assert "call 999" in medical.lower()
    military = aircraft_landing_page("https://radar.example", "/military-aircraft-tracker")
    assert "No marker does not mean no military aircraft are present" in military
    above = aircraft_landing_page("https://radar.example", "/what-plane-is-above-me")
    assert "the closest map marker is the one you saw" in above


def test_path_normalisation_and_unknown_pages():
    assert aircraft_landing_page("https://radar.example/", "helicopter-tracker").count(
        'rel="canonical" href="https://radar.example/helicopter-tracker"'
    ) == 1
    with pytest.raises(KeyError):
        aircraft_landing_page("https://radar.example", "/not-a-real-tracker")
