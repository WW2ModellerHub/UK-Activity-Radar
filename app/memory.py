"""Keep the server's memory footprint down on a 2 GB instance.

Two glibc settings matter for a threaded Python server:

* Arenas: by default glibc gives busy threads their own malloc arena (up to
  eight per CPU). With ~40 worker threads each arena keeps its own freed
  pages, so resident memory grows far beyond what Python is actually using.
  Two arenas is the usual setting for threaded Python services.
* Trimming: freed memory at the top of an arena is only returned to the OS on
  some frees. ``malloc_trim(0)`` hands back everything free, so a burst (a
  large page build or a feed refresh) does not leave the process permanently
  bigger.

Both are no-ops off glibc (macOS, Windows, musl), where the calls are absent.
"""
from __future__ import annotations

import ctypes
import ctypes.util

_M_ARENA_MAX = -8


def _libc():
    try:
        name = ctypes.util.find_library("c")
        return ctypes.CDLL(name) if name else None
    except OSError:
        return None


_LIBC = _libc()


def limit_malloc_arenas(arenas: int = 2) -> bool:
    """Cap glibc malloc arenas. Call before worker threads start."""
    mallopt = getattr(_LIBC, "mallopt", None)
    if mallopt is None:
        return False
    try:
        return bool(mallopt(_M_ARENA_MAX, int(arenas)))
    except (OSError, ValueError, TypeError):
        return False


def trim() -> bool:
    """Return free heap memory to the operating system."""
    malloc_trim = getattr(_LIBC, "malloc_trim", None)
    if malloc_trim is None:
        return False
    try:
        return bool(malloc_trim(0))
    except (OSError, ValueError, TypeError):
        return False
