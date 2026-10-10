from app.collectors.environment_agency import EnvironmentAgencyFloodCollector


def test_flood_area_link_is_normalised_to_official_https_host():
    url = "http://environment.data.gov.uk/flood-monitoring/id/floodAreas/123ABC"
    assert EnvironmentAgencyFloodCollector._safe_area_url(url) == (
        "https://environment.data.gov.uk/flood-monitoring/id/floodAreas/123ABC"
    )


def test_flood_area_link_rejects_userinfo_host_bypass():
    malicious = "https://environment.data.gov.uk@attacker.example/flood-monitoring/id/floodAreas/x"
    try:
        EnvironmentAgencyFloodCollector._safe_area_url(malicious)
    except ValueError as exc:
        assert str(exc) == "Unsupported Environment Agency flood-area URL"
    else:
        raise AssertionError("upstream flood-area URL bypass was accepted")
