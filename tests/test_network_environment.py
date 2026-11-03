from app.network_environment import blocked_proxy_variables


def test_discard_proxy_is_detected_without_exposing_its_value():
    environment = {
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "localhost:9",
        "ALL_PROXY": "socks5://[::1]:9",
    }

    assert blocked_proxy_variables(environment) == (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    )


def test_legitimate_proxy_and_unrelated_environment_are_preserved():
    environment = {
        "HTTPS_PROXY": "https://proxy.example:8443",
        "NO_PROXY": "localhost,127.0.0.1",
        "TOMTOM_TRAFFIC_API_KEY": "not-inspected",
    }

    assert blocked_proxy_variables(environment) == ()
