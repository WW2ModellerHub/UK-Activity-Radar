from __future__ import annotations

import heapq
import math
from collections import defaultdict
from collections.abc import Iterable


def distance_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    radius = 6371.0
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    d_lat, d_lon = lat2 - lat1, lon2 - lon1
    value = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


class RailwayRoutingGraph:
    """A conservative routing graph inferred from published train schedules.

    Edges represent consecutive TIPLOCs which national timetable services
    actually traverse. It is not a source of live positions: it only supplies
    plausible railway geometry between confirmed reporting points.
    """

    def __init__(self, edges: Iterable[tuple[str, str, int]], coordinates: dict[str, tuple[float, float]]) -> None:
        self.coordinates = coordinates
        self.adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for raw_first, raw_second, raw_count in edges:
            first, second = str(raw_first).upper(), str(raw_second).upper()
            if first == second or first not in coordinates or second not in coordinates:
                continue
            # Frequently published links are slightly preferred when two
            # railway paths have nearly the same geographic length.
            weight = distance_km(coordinates[first], coordinates[second]) + 1 / max(1, int(raw_count or 1))
            self.adjacency[first].append((second, weight))
            self.adjacency[second].append((first, weight))

    def route(
        self, start: str, end: str, *, max_nodes: int = 48,
        max_distance_km: float = 80, avoid_direct_edge: bool = False,
    ) -> list[str]:
        start, end = str(start).upper(), str(end).upper()
        if start == end:
            return [start]
        if start not in self.adjacency or end not in self.adjacency:
            return []
        queue: list[tuple[float, str, tuple[str, ...]]] = [(0.0, start, (start,))]
        best = {start: 0.0}
        while queue:
            cost, node, path = heapq.heappop(queue)
            if node == end:
                return list(path)
            if cost > best.get(node, float("inf")) or len(path) >= max_nodes or cost > max_distance_km:
                continue
            for neighbour, weight in self.adjacency.get(node, []):
                if avoid_direct_edge and node == start and neighbour == end:
                    continue
                if neighbour in path:
                    continue
                next_cost = cost + weight
                if next_cost < best.get(neighbour, float("inf")) and next_cost <= max_distance_km:
                    best[neighbour] = next_cost
                    heapq.heappush(queue, (next_cost, neighbour, (*path, neighbour)))
        return []
