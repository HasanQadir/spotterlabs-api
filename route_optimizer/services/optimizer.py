"""
Greedy fuel-stop optimizer.

Problem
-------
Given a list of fuel stations sorted by mile-marker along a route, decide:
  • which stations to stop at
  • how many gallons to purchase at each stop

to minimise total fuel cost, subject to:
  • tank holds at most TANK_RANGE miles of fuel
  • vehicle achieves MPG miles per gallon

Algorithm (provably optimal greedy)
------------------------------------
At each position we ask: "Is the current station the cheapest I'll see
within one full tank from here?"

  • YES → fill up completely (maximise cheap fuel).
  • NO  → buy only enough to reach the next cheaper station
          (avoid buying expensive fuel we don't have to).

If the destination can be reached before we *need* to stop, we stop only
if it's cost-effective to top up for the remainder.

Edge handling
-------------
• If no stations are reachable from the current position the route is
  infeasible and we raise ValueError.
• Purchases of < 0.01 gallons are suppressed (floating-point noise).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from route_optimizer.models import FuelStation


@dataclass
class StopResult:
    station: "FuelStation"
    mile_marker: float
    gallons_purchased: float
    stop_cost: float


def optimise(
    stations_with_markers: list[tuple["FuelStation", float]],
    total_distance_miles: float,
    tank_range: float,
    mpg: float,
) -> tuple[list[StopResult], float]:
    """
    Return (stops, total_fuel_cost).

    Parameters
    ----------
    stations_with_markers
        List of (FuelStation, mile_marker) sorted by mile_marker ascending.
    total_distance_miles
        Distance from origin to destination.
    tank_range
        Maximum miles the vehicle can travel on a full tank.
    mpg
        Fuel efficiency in miles per gallon.
    """
    # Deduplicate: if multiple stations share the same city they'll have the
    # same mile-marker; keep only the cheapest at each marker bucket (±1 mi).
    stations_with_markers = _deduplicate(stations_with_markers)

    # Only consider stations between origin and destination.
    stations = [
        (s, m) for s, m in stations_with_markers if 0.0 < m < total_distance_miles
    ]

    stops: list[StopResult] = []
    total_cost = 0.0

    current_pos = 0.0          # miles from origin
    current_fuel = tank_range  # start with a full tank

    while current_pos + current_fuel < total_distance_miles:
        # Stations reachable on current fuel
        reachable = [
            (s, m) for s, m in stations
            if current_pos < m <= current_pos + current_fuel
        ]

        if not reachable:
            raise ValueError(
                f"No fuel stations reachable from mile {current_pos:.1f}. "
                "The route may pass through a region not covered by the dataset."
            )

        # All stations within a full tank from current position
        full_range_end = current_pos + tank_range
        in_full_range = [
            (s, m) for s, m in stations
            if current_pos < m <= full_range_end
        ]

        # Pick the cheapest station within a full tank.
        # If none are in full range somehow, fall back to reachable.
        pool = in_full_range if in_full_range else reachable
        best_station, best_marker = min(pool, key=lambda x: float(x[0].retail_price))

        # How much fuel do we have when we arrive at best_station?
        fuel_on_arrival = current_fuel - (best_marker - current_pos)

        # Decide how much to buy ----------------------------------------
        remaining_after_stop = total_distance_miles - best_marker

        if remaining_after_stop <= tank_range:
            # We can reach the destination from here - buy just enough.
            gallons_needed = max(0.0, (remaining_after_stop - fuel_on_arrival) / mpg * mpg)
            # Simpler: miles_needed = remaining_after_stop - fuel_on_arrival
            miles_to_buy = max(0.0, remaining_after_stop - fuel_on_arrival)
        else:
            # Look ahead from best_station for a cheaper option.
            ahead = [
                (s, m) for s, m in stations
                if best_marker < m <= best_marker + tank_range
            ]
            cheaper_ahead = [
                (s, m) for s, m in ahead
                if float(s.retail_price) < float(best_station.retail_price)
            ]

            if cheaper_ahead:
                # Buy just enough to reach the nearest cheaper station.
                next_cheap_station, next_cheap_marker = min(
                    cheaper_ahead, key=lambda x: x[1]
                )
                miles_to_buy = max(0.0, next_cheap_station.retail_price and
                                   (next_cheap_marker - best_marker) - fuel_on_arrival)
                # Simpler expression:
                miles_to_buy = max(0.0, (next_cheap_marker - best_marker) - fuel_on_arrival)
            else:
                # This is the cheapest for a full tank - fill up.
                miles_to_buy = tank_range - fuel_on_arrival

        # Cap at tank capacity
        miles_to_buy = min(miles_to_buy, tank_range - fuel_on_arrival)

        gallons = miles_to_buy / mpg
        if gallons > 0.01:
            cost = gallons * float(best_station.retail_price)
            total_cost += cost
            stops.append(
                StopResult(
                    station=best_station,
                    mile_marker=round(best_marker, 1),
                    gallons_purchased=round(gallons, 2),
                    stop_cost=round(cost, 2),
                )
            )

        # Advance to this station
        current_fuel = fuel_on_arrival + miles_to_buy
        current_pos = best_marker

        # Remove the station we just visited so we don't loop on it
        stations = [(s, m) for s, m in stations if not (s.pk == best_station.pk)]

    return stops, round(total_cost, 2)


def _deduplicate(
    stations: list[tuple["FuelStation", float]],
    bucket_size: float = 2.0,
) -> list[tuple["FuelStation", float]]:
    """
    Within each mile-marker bucket keep only the cheapest station.
    This prevents the algorithm from treating co-located stations as
    distinct stops and speeds up the inner loops.
    """
    buckets: dict[int, tuple] = {}
    for station, marker in stations:
        key = int(marker / bucket_size)
        if key not in buckets or float(station.retail_price) < float(buckets[key][0].retail_price):
            buckets[key] = (station, marker)
    return sorted(buckets.values(), key=lambda x: x[1])
