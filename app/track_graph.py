from __future__ import annotations

import heapq
import json
import math
import threading
from pathlib import Path

from .railway_graph import distance_km


Coordinate = tuple[float, float]  # latitude, longitude
Node = tuple[float, float]  # rounded longitude, latitude


def _node(point: Coordinate) -> Node:
    return round(point[1], 6), round(point[0], 6)


def _append_path(target: list[Coordinate], points: list[Coordinate]) -> None:
    for point in points:
        if not target or point != target[-1]:
            target.append(point)


def _point_line_distance(point: Coordinate, first: Coordinate, second: Coordinate) -> float:
    """Cheap local distance in degrees, suitable for line simplification."""
    lat, lon = point
    first_lat, first_lon = first
    second_lat, second_lon = second
    dx, dy = second_lon - first_lon, second_lat - first_lat
    if not dx and not dy:
        return math.hypot(lon - first_lon, lat - first_lat)
    ratio = max(0.0, min(1.0, ((lon - first_lon) * dx + (lat - first_lat) * dy) / (dx * dx + dy * dy)))
    return math.hypot(lon - (first_lon + ratio * dx), lat - (first_lat + ratio * dy))


def _simplify(points: list[Coordinate], *, tolerance: float = 0.000045, max_points: int = 96) -> list[Coordinate]:
    if len(points) <= 2:
        return points

    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        furthest, distance = None, 0.0
        for index in range(start + 1, end):
            candidate = _point_line_distance(points[index], points[start], points[end])
            if candidate > distance:
                furthest, distance = index, candidate
        if furthest is not None and distance > tolerance:
            keep.add(furthest)
            stack.extend(((start, furthest), (furthest, end)))
    result = [points[index] for index in sorted(keep)]
    if len(result) <= max_points:
        return result
    # Retain a bounded payload for the live websocket while preserving both
    # confirmed endpoints. Full selected routes request a larger limit.
    indexes = {round(index * (len(result) - 1) / (max_points - 1)) for index in range(max_points)}
    return [result[index] for index in sorted(indexes)]


def simplify_path(points: list[list[float]], *, max_points: int = 96) -> list[list[float]]:
    clean = [(float(point[0]), float(point[1])) for point in points if isinstance(point, list) and len(point) >= 2]
    return [[lat, lon] for lat, lon in _simplify(clean, max_points=max_points)]


class PhysicalRailwayGraph:
    """Route between railway timing points over real track polylines.

    The source geometry is a simplified, connected Network Rail reference-line
    model. Timetable locations constrain which branch a service takes; this
    graph supplies the missing curves between those locations. It never
    invents a straight station-to-station chord.
    """

    _cell_size = 0.02

    def __init__(self, geojson_path: Path) -> None:
        self.geojson_path = geojson_path
        self._lock = threading.RLock()
        self._loaded = False
        self._shapes: list[list[Coordinate]] = []
        self._cumulative: list[list[float]] = []
        self._endpoints: list[tuple[Node, Node]] = []
        self._adjacency: dict[Node, list[tuple[Node, float, int]]] = {}
        self._spatial: dict[tuple[int, int], list[tuple[int, int]]] = {}

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return bool(self._shapes)

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                payload = json.loads(self.geojson_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, json.JSONDecodeError):
                return
            for feature in payload.get("features", []):
                geometry = feature.get("geometry") if isinstance(feature, dict) else None
                if not isinstance(geometry, dict):
                    continue
                raw_lines = geometry.get("coordinates") or []
                if geometry.get("type") == "LineString":
                    raw_lines = [raw_lines]
                if geometry.get("type") not in {"LineString", "MultiLineString"}:
                    continue
                for raw_line in raw_lines:
                    shape: list[Coordinate] = []
                    for raw_point in raw_line if isinstance(raw_line, list) else []:
                        if not isinstance(raw_point, list) or len(raw_point) < 2:
                            continue
                        try:
                            point = float(raw_point[1]), float(raw_point[0])
                        except (TypeError, ValueError):
                            continue
                        if not shape or point != shape[-1]:
                            shape.append(point)
                    if len(shape) < 2:
                        continue
                    edge_id = len(self._shapes)
                    cumulative = [0.0]
                    for first, second in zip(shape, shape[1:]):
                        cumulative.append(cumulative[-1] + distance_km(first, second))
                    first_node, last_node = _node(shape[0]), _node(shape[-1])
                    self._shapes.append(shape)
                    self._cumulative.append(cumulative)
                    self._endpoints.append((first_node, last_node))
                    self._adjacency.setdefault(first_node, []).append((last_node, cumulative[-1], edge_id))
                    self._adjacency.setdefault(last_node, []).append((first_node, cumulative[-1], edge_id))
                    for point_index, point in enumerate(shape):
                        cell = self._cell(point)
                        self._spatial.setdefault(cell, []).append((edge_id, point_index))

    def _cell(self, point: Coordinate) -> tuple[int, int]:
        return math.floor(point[1] / self._cell_size), math.floor(point[0] / self._cell_size)

    def _snap_candidates(
        self, point: Coordinate, *, maximum_km: float = 0.8, maximum_edges: int = 8,
    ) -> list[tuple[int, int, float]]:
        self._ensure_loaded()
        base_x, base_y = self._cell(point)
        nearest_by_edge: dict[int, tuple[int, int, float]] = {}
        for x in range(base_x - 3, base_x + 4):
            for y in range(base_y - 3, base_y + 4):
                for edge_id, point_index in self._spatial.get((x, y), []):
                    candidate = distance_km(point, self._shapes[edge_id][point_index])
                    if candidate > maximum_km:
                        continue
                    previous = nearest_by_edge.get(edge_id)
                    if previous is None or candidate < previous[2]:
                        nearest_by_edge[edge_id] = edge_id, point_index, candidate
        return sorted(nearest_by_edge.values(), key=lambda candidate: candidate[2])[:maximum_edges]

    def _snap(self, point: Coordinate, *, maximum_km: float = 0.8) -> tuple[int, int, float] | None:
        candidates = self._snap_candidates(point, maximum_km=maximum_km, maximum_edges=1)
        return candidates[0] if candidates else None

    def _oriented_shape(self, edge_id: int, first: Node, second: Node) -> list[Coordinate]:
        start, end = self._endpoints[edge_id]
        shape = self._shapes[edge_id]
        return shape if (first, second) == (start, end) else list(reversed(shape))

    def _route_pair(self, start: Coordinate, end: Coordinate) -> list[Coordinate]:
        first_snaps = self._snap_candidates(start)
        second_snaps = self._snap_candidates(end)
        if not first_snaps or not second_snaps:
            return []

        candidates: list[tuple[float, list[Coordinate]]] = []
        for first_edge, first_index, first_distance in first_snaps:
            for second_edge, second_index, second_distance in second_snaps:
                if first_edge != second_edge:
                    continue
                shape, cumulative = self._shapes[first_edge], self._cumulative[first_edge]
                low, high = sorted((first_index, second_index))
                same = shape[low:high + 1]
                if first_index > second_index:
                    same = list(reversed(same))
                candidates.append((
                    first_distance + abs(cumulative[first_index] - cumulative[second_index]) + second_distance,
                    same,
                ))

        # Seed the endpoint graph from every nearby track. This matters at
        # stations and junctions where parallel lines are only metres apart:
        # the geometrically nearest line is not always the service's line.
        queue: list[tuple[float, Node]] = []
        best: dict[Node, float] = {}
        previous: dict[Node, tuple[Node, int] | None] = {}
        origins: dict[Node, tuple[int, int, Node]] = {}
        for edge_id, point_index, snap_distance in first_snaps:
            endpoints, cumulative = self._endpoints[edge_id], self._cumulative[edge_id]
            for node, partial in (
                (endpoints[0], cumulative[point_index]),
                (endpoints[1], cumulative[-1] - cumulative[point_index]),
            ):
                cost = snap_distance + partial
                if cost < best.get(node, float("inf")):
                    best[node], previous[node] = cost, None
                    origins[node] = edge_id, point_index, node
                    heapq.heappush(queue, (cost, node))

        destinations: dict[Node, tuple[float, int, int]] = {}
        for edge_id, point_index, snap_distance in second_snaps:
            endpoints, cumulative = self._endpoints[edge_id], self._cumulative[edge_id]
            for node, partial in (
                (endpoints[0], cumulative[point_index]),
                (endpoints[1], cumulative[-1] - cumulative[point_index]),
            ):
                cost = snap_distance + partial
                if node not in destinations or cost < destinations[node][0]:
                    destinations[node] = cost, edge_id, point_index

        best_finish: tuple[float, Node] | None = None
        maximum = max(8.0, distance_km(start, end) * 2.5)
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != best.get(node) or cost > maximum:
                continue
            if best_finish is not None and cost >= best_finish[0]:
                break
            if node in destinations:
                total = cost + destinations[node][0]
                if best_finish is None or total < best_finish[0]:
                    best_finish = total, node
            for neighbour, weight, edge_id in self._adjacency.get(node, []):
                next_cost = cost + weight
                if next_cost < best.get(neighbour, float("inf")) and next_cost <= maximum:
                    best[neighbour] = next_cost
                    previous[neighbour] = node, edge_id
                    heapq.heappush(queue, (next_cost, neighbour))

        if best_finish is not None:
            total, finish_node = best_finish
            nodes: list[Node] = [finish_node]
            edges: list[int] = []
            current = finish_node
            while previous.get(current) is not None:
                prior, edge_id = previous[current]  # type: ignore[misc]
                nodes.append(prior)
                edges.append(edge_id)
                current = prior
            nodes.reverse()
            edges.reverse()
            start_node, end_node = nodes[0], nodes[-1]
            first_edge, first_index, _ = origins[start_node]
            first_nodes, first_shape = self._endpoints[first_edge], self._shapes[first_edge]
            if start_node == first_nodes[0]:
                assembled = list(reversed(first_shape[:first_index + 1]))
            else:
                assembled = first_shape[first_index:]
            for index, edge_id in enumerate(edges):
                _append_path(assembled, self._oriented_shape(edge_id, nodes[index], nodes[index + 1]))
            _, second_edge, second_index = destinations[end_node]
            second_nodes, second_shape = self._endpoints[second_edge], self._shapes[second_edge]
            if end_node == second_nodes[0]:
                _append_path(assembled, second_shape[:second_index + 1])
            else:
                _append_path(assembled, list(reversed(second_shape[second_index:])))
            candidates.append((total, assembled))

        if not candidates:
            return []
        return min(candidates, key=lambda candidate: candidate[0])[1]

    def route(self, timing_points: list[list[float]], *, max_points: int = 96) -> list[list[float]]:
        """Follow the physical railway through every supplied timing point."""
        self._ensure_loaded()
        points: list[Coordinate] = []
        for raw in timing_points:
            if not isinstance(raw, list) or len(raw) < 2:
                continue
            try:
                point = float(raw[0]), float(raw[1])
            except (TypeError, ValueError):
                continue
            if not points or point != points[-1]:
                points.append(point)
        if len(points) < 2:
            return []
        result: list[Coordinate] = []
        for first, second in zip(points, points[1:]):
            pair = self._route_pair(first, second)
            if len(pair) < 2:
                return []
            _append_path(result, pair)
        simplified = _simplify(result, max_points=max(2, max_points))
        return [[lat, lon] for lat, lon in simplified]
