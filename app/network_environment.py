"""Small startup checks for an unusable inherited network environment.

The application respects legitimate HTTP(S) proxies.  Some restricted
development runners deliberately inject a discard proxy on localhost port 9,
however.  Starting the full collector set in that environment makes every
independent provider look broken even though the credentials and endpoints are
fine.  Detect that exact sentinel so launchers can fail with one useful message;
never clear or bypass a configured proxy here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlparse


PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_DISCARD_PROXY_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DISCARD_PROXY_PORT = 9


def blocked_proxy_variables(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return proxy variable names that point at the known discard sentinel."""

    values = os.environ if environ is None else environ
    blocked: list[str] = []
    for name in PROXY_VARIABLES:
        raw = str(values.get(name, "") or "").strip()
        if not raw:
            continue
        candidate = raw if "://" in raw else f"http://{raw}"
        try:
            parsed = urlparse(candidate)
            port = parsed.port
        except ValueError:
            continue
        if (parsed.hostname or "").casefold() in _DISCARD_PROXY_HOSTS and port == _DISCARD_PROXY_PORT:
            blocked.append(name)
    return tuple(blocked)


def main() -> int:
    blocked = blocked_proxy_variables()
    if not blocked:
        return 0
    print("UK Activity Radar cannot reach live feeds from this restricted shell.")
    print("Close this server and double-click run.bat in File Explorer instead.")
    print("No API keys need to be changed.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
