from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests


class TomTomRoutingClient:
    """Small server-side adapter for road, walking and cycle journeys."""

    _MODES = {"car": "car", "walk": "pedestrian", "bike": "bicycle"}

    def __init__(self, api_key: str, base_url: str = "https://api.tomtom.com", timeout_seconds: float = 12.0) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "https://api.tomtom.com").rstrip("/")
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 30.0))
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.5.0 journey-planner"})

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def plan(
        self,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        mode: str,
        *,
        from_name: str = "",
        to_name: str = "",
    ) -> dict[str, Any]:
        travel_mode = self._MODES.get(str(mode or "").casefold())
        if not self.configured or travel_mode is None:
            raise ValueError("TomTom routing is not configured for that travel mode")
        for lat, lon in ((origin_lat, origin_lon), (destination_lat, destination_lon)):
            if not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
                raise ValueError("Journey coordinates are invalid")

        route_points = f"{float(origin_lat):.6f},{float(origin_lon):.6f}:{float(destination_lat):.6f},{float(destination_lon):.6f}"
        response = self._session.get(
            f"{self.base_url}/routing/1/calculateRoute/{route_points}/json",
            params={
                "key": self.api_key,
                "travelMode": travel_mode,
                "traffic": "true" if travel_mode == "car" else "false",
                "routeRepresentation": "polyline",
                "instructionsType": "text",
                "computeTravelTimeFor": "all" if travel_mode == "car" else "none",
                "sectionType": "traffic" if travel_mode == "car" else None,
                "language": "en-GB",
                "maxAlternatives": 2,
            },
            timeout=(3.0, self.timeout_seconds),
        )
        response.raise_for_status()
        routes = response.json().get("routes") or []
        itineraries = [self._normalise_route(row, mode, from_name, to_name) for row in routes[:3]]
        itineraries = [row for row in itineraries if row is not None]
        return {
            "status": "ok" if itineraries else "no_route",
            "available": True,
            "provider": "TomTom Routing",
            "message": f"Found {len(itineraries)} traffic-aware route option(s)." if itineraries else "TomTom found no route for those places.",
            "itineraries": itineraries,
            "routing_errors": [],
        }

    @staticmethod
    def _normalise_route(row: dict[str, Any], mode: str, from_name: str, to_name: str) -> dict[str, Any] | None:
        summary = row.get("summary") or {}
        seconds = summary.get("travelTimeInSeconds")
        distance = summary.get("lengthInMeters")
        if seconds is None or distance is None:
            return None
        points: list[dict[str, float]] = []
        for leg in row.get("legs") or []:
            for point in leg.get("points") or []:
                try:
                    points.append({"lat": float(point["latitude"]), "lon": float(point["longitude"])})
                except (KeyError, TypeError, ValueError):
                    continue
        depart_at = summary.get("departureTime") or datetime.now(timezone.utc).isoformat()
        arrive_at = summary.get("arrivalTime")
        delay = summary.get("trafficDelayInSeconds")
        no_traffic = summary.get("noTrafficTravelTimeInSeconds")
        traffic_sections: list[dict[str, Any]] = []
        for section in row.get("sections") or []:
            if str(section.get("sectionType") or "").casefold() != "traffic":
                continue
            start_index = section.get("startPointIndex")
            end_index = section.get("endPointIndex")
            try:
                start_point = points[int(start_index)] if start_index is not None else None
                end_point = points[int(end_index)] if end_index is not None else None
            except (IndexError, TypeError, ValueError):
                start_point = end_point = None
            traffic_sections.append({
                "category": str(section.get("simpleCategory") or "traffic").replace("_", " "),
                "delay_seconds": int(section.get("delayInSeconds") or 0),
                "effective_speed_kmh": int(section.get("effectiveSpeedInKmh") or 0) or None,
                "magnitude": section.get("magnitudeOfDelay"),
                "start": start_point,
                "end": end_point,
            })
        instructions: list[dict[str, Any]] = []
        for instruction in (row.get("guidance") or {}).get("instructions") or []:
            if not isinstance(instruction, dict):
                continue
            message = str(instruction.get("message") or instruction.get("instructionType") or "").strip()
            if not message:
                continue
            point = instruction.get("point") or {}
            try:
                instruction_point = {"lat": float(point["latitude"]), "lon": float(point["longitude"])}
            except (KeyError, TypeError, ValueError):
                instruction_point = None
            instructions.append({
                "message": message,
                "road": str(instruction.get("street") or "").strip() or None,
                "road_numbers": [str(value) for value in (instruction.get("roadNumbers") or []) if value],
                "exit_number": str(instruction.get("exitNumber") or "").strip() or None,
                "route_offset_meters": int(instruction.get("routeOffsetInMeters") or 0),
                "travel_time_seconds": int(instruction.get("travelTimeInSeconds") or 0),
                "point": instruction_point,
            })
        return {
            "duration_seconds": int(seconds),
            "distance_meters": int(distance),
            "depart_at": depart_at,
            "arrive_at": arrive_at,
            "transfers": 0,
            "walking_distance_meters": int(distance) if mode == "walk" else 0,
            "traffic_delay_seconds": int(delay or 0),
            "no_traffic_duration_seconds": int(no_traffic) if no_traffic is not None else None,
            "traffic_sections": traffic_sections,
            "instructions": instructions,
            "geometry": points,
            "legs": [{
                "mode": mode,
                "from": from_name or "Origin",
                "to": to_name or "Destination",
                "duration_seconds": int(seconds),
                "distance_m": int(distance),
                "distance_meters": int(distance),
                "depart_at": depart_at,
                "arrive_at": arrive_at,
                "traffic_delay_seconds": int(delay or 0),
            }],
        }
