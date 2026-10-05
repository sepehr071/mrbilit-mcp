"""MCP server entry point: registers every read-only MrBilit tool."""

import logging

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, buses, content, flights, hotels, places, trains  # noqa: F401  (imports register the tools)
from .registry import TOOLS

INSTRUCTIONS = """\
Unofficial, read-only access to MrBilit (mrbilit.com), an Iranian online travel agency: domestic and
international flights, trains, intercity buses and private taxis, and Iranian hotels. Nothing here
can log in, hold a seat, book or pay; the user books on mrbilit.com.

Workflow:
1. Codes first: mb_find_place(query, mode) turns a name into the code each search takes. Each mode has
   its own ids: flight IATA codes (Tehran domestic = THR, international usually IKA or THRALL), train
   station ids (Tehran 1, Mashhad 191), bus/taxi 8-digit city ids (Tehran 11320000), hotel city and
   hotel slugs ('mashhad', 'mashhad/enghelab').
2. Flights: mb_flight_price_calendar (cheapest day) -> mb_search_flights (one-way or round trip,
   party, cabin, airline, direct, time filters) -> mb_flight_fare_details (refund penalties,
   confirmed baggage, rules).
3. Trains: mb_train_price_calendar -> mb_search_trains (classes, seats) -> mb_train_price (exact price
   for children, infants, a whole compartment) and mb_train_stops. Sold out or dear:
   mb_alternative_routes (one-change trips, nearby stations, bus cities and airports).
4. Buses: mb_bus_price_calendar -> mb_search_buses -> mb_bus_seats (free seats, women/men seats).
   Private car door to door: mb_search_taxis (price per car).
5. Hotels: mb_search_hotels (available stays with prices) or mb_city_hotels (every hotel, no prices)
   -> mb_hotel (rating, reviews, location) -> mb_hotel_rooms (exact room prices) / mb_hotel_calendar.
6. Context: mb_notices (current site notices for a route), mb_help (official rules, FAQ, help
   guides), mb_travel_guide (route guide, magazine), mb_companies (airline/rail/bus company names).

Conventions: every price is in Toman (the API sends Rial; values are divided by 10), in fields named
*_toman. Flight price_toman is per adult and total_toman the whole party; train price_toman is per
adult; bus price_toman per seat; taxi price_toman per car; hotel price_toman the whole stay for one
room. Dates in and out are Gregorian YYYY-MM-DD; past dates are rejected. Times are local to the
place: a flight time at a foreign airport is that airport's local time, although every flight time
carries the API's +03:30 suffix. Ratings are 0-5 (null = not rated; the site shows hotel ratings x2
out of 10). Ids such as flight_id, class_id and bus_id expire: take them from a fresh search. Train sales open about 18 days ahead, buses about a
month. Results list only what can be bought unless a tool says otherwise; seat counts change fast.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

mcp = MCPServer(
    "mrbilit-mcp",
    title="MrBilit",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/sepehr071/mrbilit-mcp",
)

for fn, title in TOOLS:
    mcp.add_tool(fn, title=title, annotations=READ_ONLY)


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request floods client logs
    mcp.run()


if __name__ == "__main__":
    main()
