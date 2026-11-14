"""The server's own Web Push signing keys.

Web Push needs one VAPID key pair per site. Rather than wait for someone to
generate one and paste it into the host's settings, the hosted service makes
its own the first time it starts and keeps it on its data disk. The private
key never leaves that disk and is never in source control; changing it would
silently break every existing subscription, so it is created once and reused.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

FILE_NAME = "vapid_keys.json"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate() -> tuple[str, str]:
    """(public, private) as unpadded base64url: the forms browsers and pywebpush take."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(public), _b64(private)


def ensure(data_dir: Path) -> tuple[str, str]:
    """Load the stored key pair, creating it on first use."""
    path = Path(data_dir) / FILE_NAME
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        public, private = str(stored["public"]), str(stored["private"])
        if len(public) >= 80 and len(private) >= 40:
            return public, private
    except (OSError, ValueError, KeyError, TypeError):
        pass
    public, private = generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"public": public, "private": private}, handle)
    os.replace(temporary, path)
    return public, private
