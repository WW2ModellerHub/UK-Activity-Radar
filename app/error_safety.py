from __future__ import annotations

import re
from urllib.parse import quote, quote_plus, urlparse

from requests.exceptions import ProxyError


_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|token|access[_-]?token|password|secret)=)[^&#\s]+"
)
_AUTHORIZATION_VALUE = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^,;\s]+")


def public_error(exc: BaseException, *secrets: str) -> str:
    """Return useful exception context without publishing credentials.

    Requests exceptions commonly include the fully prepared URL, including
    query-string API keys. Source status is returned to browsers, so it must
    never contain those values.
    """

    if isinstance(exc, ProxyError):
        return (
            "ProxyError: the configured outbound proxy could not be reached. "
            "Check HTTP_PROXY/HTTPS_PROXY or restart UK Activity Radar from a normal Windows session."
        )

    message = str(exc) or "upstream request failed"
    message = _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", message)
    message = _AUTHORIZATION_VALUE.sub(r"\1[REDACTED]", message)
    for secret in secrets:
        if not secret:
            continue
        for form in {secret, quote(secret, safe=""), quote_plus(secret, safe="")}:
            if form:
                message = message.replace(form, "[REDACTED]")
    return f"{type(exc).__name__}: {message}"


def same_origin_websocket(origin: str | None, host: str | None) -> bool:
    """Allow browser WebSockets only when their Origin matches the request Host.

    Non-browser clients commonly omit Origin and remain supported. Host is
    supplied by the ASGI server and includes the port when one is present.
    """

    if not origin:
        return True
    if not host:
        return False
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == host.casefold()
