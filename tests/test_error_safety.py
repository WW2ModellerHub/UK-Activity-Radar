import requests

from app.error_safety import public_error, same_origin_websocket


def test_public_error_redacts_sensitive_query_values_and_explicit_secrets():
    secret = "private key/+value"
    error = requests.HTTPError(
        "500 Server Error for url: https://provider.test/feed?api_key=private+key%2F%2Bvalue&mode=live"
    )

    result = public_error(error, secret)

    assert "private" not in result
    assert "api_key=[REDACTED]" in result
    assert "mode=live" in result


def test_public_error_redacts_generic_key_and_authorization_values():
    error = RuntimeError(
        "GET https://provider.test/tile?key=top-secret Authorization: Bearer bearer-secret"
    )

    result = public_error(error)

    assert "top-secret" not in result
    assert "bearer-secret" not in result
    assert result.startswith("RuntimeError:")


def test_websocket_origin_must_match_request_host():
    assert same_origin_websocket("http://127.0.0.1:8000", "127.0.0.1:8000")
    assert same_origin_websocket("https://radar.example", "radar.example")
    assert same_origin_websocket(None, "127.0.0.1:8000")
    assert not same_origin_websocket("https://attacker.example", "127.0.0.1:8000")
    assert not same_origin_websocket("file://local", "127.0.0.1:8000")
