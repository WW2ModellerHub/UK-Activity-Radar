from __future__ import annotations


def rainviewer_payload(body: dict) -> dict:
    """Turn RainViewer weather-maps metadata into one current radar layer."""
    host = str(body.get("host") or "").rstrip("/")
    past = ((body.get("radar") or {}).get("past") or [])
    if not host or not past:
        raise ValueError("RainViewer returned no radar frame")
    frame = past[-1]
    path = str(frame.get("path") or "")
    stamp = frame.get("time")
    if not path:
        raise ValueError("RainViewer radar frame has no tile path")
    return {
        "source": "RainViewer",
        "frame_time": stamp,
        "generated": body.get("generated"),
        "max_native_zoom": 7,
        "tile_template": f"{host}{path}/256/{{z}}/{{x}}/{{y}}/2/1_1.png",
        "attribution": "Weather radar © RainViewer",
    }
