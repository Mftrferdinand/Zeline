#!/usr/bin/env python3
"""Location intelligence over free OpenStreetMap services. Python stdlib only.

Commands: search, reverse, nearby, distance, directions, timezone, area, bbox.
Every command prints JSON on stdout; failures print ``{"error": "..."}`` and exit
non-zero so a caller can branch on the exit code instead of parsing prose.

Data sources and the rules they impose:

- **Nominatim** (geocoding) requires a descriptive User-Agent and allows at most
  1 request/second. Both are enforced here, not left to the caller — a skill that
  quietly violates the ToS gets the user's IP blocked.
- **Overpass** (POIs) is frequently overloaded; two mirrors are tried in order.
- **OSRM** (routing) is the demo server: no key, best coverage in Europe/NA.
- **TimeAPI.io** (timezone) needs no key.

No third-party packages: this ships inside a bundled skill and must run on a bare
Termux install with nothing pip-installed.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Zeline-maps-skill/1.0 (+https://github.com/Mftrferdinand/Zeline)"
NOMINATIM = "https://nominatim.openstreetmap.org"
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OSRM = "https://router.project-osrm.org"
TIMEAPI = "https://timeapi.io/api/TimeZone/coordinate"

TIMEOUT = 30
# Nominatim's usage policy: absolute maximum of one request per second.
_MIN_INTERVAL = 1.05
_last_call = 0.0

# OSM tag filters per friendly category. `amenity` covers most, but shops and
# transport live under different keys, so each entry carries its own key.
CATEGORIES: dict[str, tuple[str, str]] = {
    "restaurant": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "bar": ("amenity", "bar"),
    "pub": ("amenity", "pub"),
    "fast_food": ("amenity", "fast_food"),
    "hospital": ("amenity", "hospital"),
    "clinic": ("amenity", "clinic"),
    "pharmacy": ("amenity", "pharmacy"),
    "dentist": ("amenity", "dentist"),
    "doctor": ("amenity", "doctors"),
    "veterinary": ("amenity", "veterinary"),
    "hotel": ("tourism", "hotel"),
    "guest_house": ("tourism", "guest_house"),
    "hostel": ("tourism", "hostel"),
    "camp_site": ("tourism", "camp_site"),
    "museum": ("tourism", "museum"),
    "zoo": ("tourism", "zoo"),
    "supermarket": ("shop", "supermarket"),
    "convenience_store": ("shop", "convenience"),
    "bakery": ("shop", "bakery"),
    "bookshop": ("shop", "books"),
    "laundry": ("shop", "laundry"),
    "car_wash": ("shop", "car_wash"),
    "car_rental": ("amenity", "car_rental"),
    "bicycle_rental": ("amenity", "bicycle_rental"),
    "atm": ("amenity", "atm"),
    "bank": ("amenity", "bank"),
    "gas_station": ("amenity", "fuel"),
    "parking": ("amenity", "parking"),
    "taxi": ("amenity", "taxi"),
    "post_office": ("amenity", "post_office"),
    "police": ("amenity", "police"),
    "fire_station": ("amenity", "fire_station"),
    "library": ("amenity", "library"),
    "school": ("amenity", "school"),
    "university": ("amenity", "university"),
    "park": ("leisure", "park"),
    "playground": ("leisure", "playground"),
    "gym": ("leisure", "fitness_centre"),
    "swimming_pool": ("leisure", "swimming_pool"),
    "stadium": ("leisure", "stadium"),
    "cinema": ("amenity", "cinema"),
    "theatre": ("amenity", "theatre"),
    "nightclub": ("amenity", "nightclub"),
    "church": ("amenity", "place_of_worship"),
    "mosque": ("amenity", "place_of_worship"),
    "synagogue": ("amenity", "place_of_worship"),
    "airport": ("aeroway", "aerodrome"),
    "train_station": ("railway", "station"),
    "bus_stop": ("highway", "bus_stop"),
}

MODES = {"driving": "driving", "walking": "foot", "cycling": "bike"}


class MapsError(Exception):
    """A failure the caller should see as JSON, not a traceback."""


def _throttle() -> None:
    global _last_call
    gap = time.monotonic() - _last_call
    if gap < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - gap)
    _last_call = time.monotonic()


def _get(url: str, *, throttle: bool = True) -> object:
    if throttle:
        _throttle()
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise MapsError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}") from exc
    except urllib.error.URLError as exc:
        raise MapsError(f"network error reaching {urllib.parse.urlsplit(url).netloc}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise MapsError(f"invalid JSON from {urllib.parse.urlsplit(url).netloc}") from exc


def _post_overpass(query: str) -> dict:
    payload = urllib.parse.urlencode({"data": query}).encode()
    last: str = "no mirror tried"
    for mirror in OVERPASS_MIRRORS:
        request = urllib.request.Request(
            mirror, data=payload, headers={"User-Agent": UA, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT + 30) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last = f"{urllib.parse.urlsplit(mirror).netloc}: {exc.__class__.__name__}"
            continue
    raise MapsError(f"all Overpass mirrors failed ({last})")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    radius = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return int(round(2 * radius * math.asin(math.sqrt(a))))


def geocode(place: str) -> dict:
    url = f"{NOMINATIM}/search?" + urllib.parse.urlencode(
        {"q": place, "format": "json", "limit": 1, "addressdetails": 1}
    )
    data = _get(url)
    if not isinstance(data, list) or not data:
        raise MapsError(f"no match for {place!r}")
    hit = data[0]
    return {
        "query": place,
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
        "display_name": hit.get("display_name", ""),
        "type": hit.get("type", ""),
        "class": hit.get("class", ""),
        "importance": hit.get("importance"),
        "boundingbox": [float(value) for value in hit.get("boundingbox", [])] or None,
    }


def _maps_urls(lat: float, lon: float, from_lat: float | None = None, from_lon: float | None = None) -> dict:
    urls = {"maps_url": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"}
    if from_lat is not None and from_lon is not None:
        urls["directions_url"] = (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={from_lat},{from_lon}&destination={lat},{lon}"
        )
    return urls


def cmd_search(args: argparse.Namespace) -> dict:
    return geocode(args.place)


def cmd_reverse(args: argparse.Namespace) -> dict:
    url = f"{NOMINATIM}/reverse?" + urllib.parse.urlencode(
        {"lat": args.lat, "lon": args.lon, "format": "json", "addressdetails": 1}
    )
    data = _get(url)
    if not isinstance(data, dict) or "error" in data:
        raise MapsError(f"no address for {args.lat},{args.lon}")
    address = data.get("address", {})
    return {
        "lat": float(data.get("lat", args.lat)),
        "lon": float(data.get("lon", args.lon)),
        "display_name": data.get("display_name", ""),
        "road": address.get("road"),
        "house_number": address.get("house_number"),
        "suburb": address.get("suburb") or address.get("neighbourhood"),
        "city": address.get("city") or address.get("town") or address.get("village"),
        "state": address.get("state"),
        "postcode": address.get("postcode"),
        "country": address.get("country"),
        "country_code": address.get("country_code"),
        **_maps_urls(float(data.get("lat", args.lat)), float(data.get("lon", args.lon))),
    }


def _resolve_point(args: argparse.Namespace) -> tuple[float, float, str]:
    if args.near:
        hit = geocode(args.near)
        return hit["lat"], hit["lon"], hit["display_name"]
    if args.lat is None or args.lon is None:
        raise MapsError("give LAT LON positionally, or --near \"<place>\"")
    return float(args.lat), float(args.lon), f"{args.lat},{args.lon}"


def _overpass_clauses(categories: list[str], lat: float, lon: float, radius: int) -> str:
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise MapsError(
            f"unknown category {unknown[0]!r}. Known: {', '.join(sorted(CATEGORIES))}"
        )
    parts = []
    for category in categories:
        key, value = CATEGORIES[category]
        for element in ("node", "way"):
            parts.append(f'{element}["{key}"="{value}"](around:{radius},{lat},{lon});')
    return "".join(parts)


def cmd_nearby(args: argparse.Namespace) -> dict:
    lat, lon, label = _resolve_point(args)
    categories = args.category or ([args.positional_category] if args.positional_category else [])
    if not categories:
        raise MapsError("give a category, e.g. `nearby LAT LON restaurant` or --category cafe")
    query = (
        f"[out:json][timeout:60];({_overpass_clauses(categories, lat, lon, args.radius)});"
        "out center tags;"
    )
    payload = _post_overpass(query)
    results = []
    for element in payload.get("elements", []):
        point = element.get("center") or element
        plat, plon = point.get("lat"), point.get("lon")
        if plat is None or plon is None:
            continue
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        street = " ".join(
            value for value in (tags.get("addr:housenumber"), tags.get("addr:street")) if value
        )
        results.append(
            {
                "name": name,
                "address": street or tags.get("addr:full") or None,
                "lat": plat,
                "lon": plon,
                "distance_m": haversine_m(lat, lon, plat, plon),
                "cuisine": tags.get("cuisine"),
                "hours": tags.get("opening_hours"),
                "phone": tags.get("phone") or tags.get("contact:phone"),
                "website": tags.get("website") or tags.get("contact:website"),
                **_maps_urls(plat, plon, lat, lon),
            }
        )
    results.sort(key=lambda item: item["distance_m"])
    return {
        "origin": {"lat": lat, "lon": lon, "label": label},
        "categories": categories,
        "radius_m": args.radius,
        "count": len(results[: args.limit]),
        "results": results[: args.limit],
    }


def _route(from_place: str, to_place: str, mode: str) -> dict:
    if mode not in MODES:
        raise MapsError(f"unknown mode {mode!r}. Use driving, walking, or cycling.")
    start = geocode(from_place)
    end = geocode(to_place)
    url = (
        f"{OSRM}/route/v1/{MODES[mode]}/"
        f"{start['lon']},{start['lat']};{end['lon']},{end['lat']}"
        "?overview=false&steps=true&annotations=false"
    )
    data = _get(url, throttle=False)
    if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
        raise MapsError(f"no {mode} route found ({(data or {}).get('code', 'unknown')})")
    return {"start": start, "end": end, "route": data["routes"][0], "mode": mode}


def cmd_distance(args: argparse.Namespace) -> dict:
    routed = _route(args.origin, args.to, args.mode)
    route = routed["route"]
    start, end = routed["start"], routed["end"]
    straight = haversine_m(start["lat"], start["lon"], end["lat"], end["lon"])
    seconds = int(route["duration"])
    return {
        "from": start["display_name"],
        "to": end["display_name"],
        "mode": args.mode,
        "road_distance_km": round(route["distance"] / 1000, 2),
        "straight_line_km": round(straight / 1000, 2),
        "duration_seconds": seconds,
        "duration_human": f"{seconds // 3600}h {seconds % 3600 // 60}m" if seconds >= 3600 else f"{seconds // 60}m",
    }


def cmd_directions(args: argparse.Namespace) -> dict:
    routed = _route(args.origin, args.to, args.mode)
    route = routed["route"]
    steps = []
    for leg in route.get("legs", []):
        for index, step in enumerate(leg.get("steps", []), start=len(steps) + 1):
            maneuver = step.get("maneuver", {})
            steps.append(
                {
                    "step": index,
                    "instruction": maneuver.get("type", "")
                    + (f" {maneuver['modifier']}" if maneuver.get("modifier") else ""),
                    "road": step.get("name") or None,
                    "distance_m": int(step.get("distance", 0)),
                    "duration_s": int(step.get("duration", 0)),
                    "maneuver": maneuver.get("type"),
                }
            )
    return {
        "from": routed["start"]["display_name"],
        "to": routed["end"]["display_name"],
        "mode": args.mode,
        "total_distance_km": round(route["distance"] / 1000, 2),
        "total_duration_s": int(route["duration"]),
        "steps": steps,
    }


def cmd_timezone(args: argparse.Namespace) -> dict:
    url = f"{TIMEAPI}?" + urllib.parse.urlencode({"latitude": args.lat, "longitude": args.lon})
    data = _get(url, throttle=False)
    if not isinstance(data, dict) or not data.get("timeZone"):
        raise MapsError(f"no timezone for {args.lat},{args.lon}")
    offset = data.get("currentUtcOffset", {}) or {}
    return {
        "lat": args.lat,
        "lon": args.lon,
        "timezone": data.get("timeZone"),
        "utc_offset_seconds": offset.get("seconds"),
        "utc_offset": offset.get("milliseconds") and None or offset.get("seconds") is not None
        and f"{offset['seconds'] // 3600:+03d}:{abs(offset['seconds']) % 3600 // 60:02d}"
        or None,
        "current_local_time": data.get("currentLocalTime"),
        "dst_active": data.get("hasDayLightSaving"),
    }


def cmd_area(args: argparse.Namespace) -> dict:
    hit = geocode(args.place)
    box = hit.get("boundingbox")
    if not box or len(box) != 4:
        raise MapsError(f"no bounding box for {args.place!r}")
    south, north, west, east = box
    height_km = haversine_m(south, west, north, west) / 1000
    width_km = haversine_m(south, west, south, east) / 1000
    return {
        "place": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "bbox": {"south": south, "west": west, "north": north, "east": east},
        "bbox_args": f"{south} {west} {north} {east}",
        "width_km": round(width_km, 2),
        "height_km": round(height_km, 2),
        "approx_area_km2": round(width_km * height_km, 2),
    }


def cmd_bbox(args: argparse.Namespace) -> dict:
    categories = [args.category] if isinstance(args.category, str) else list(args.category or [])
    if not categories:
        raise MapsError("give a category, e.g. `bbox S W N E restaurant`")
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise MapsError(f"unknown category {unknown[0]!r}. Known: {', '.join(sorted(CATEGORIES))}")
    clauses = []
    for category in categories:
        key, value = CATEGORIES[category]
        for element in ("node", "way"):
            clauses.append(
                f'{element}["{key}"="{value}"]({args.south},{args.west},{args.north},{args.east});'
            )
    payload = _post_overpass(f"[out:json][timeout:60];({''.join(clauses)});out center tags;")
    centre_lat = (args.south + args.north) / 2
    centre_lon = (args.west + args.east) / 2
    results = []
    for element in payload.get("elements", []):
        point = element.get("center") or element
        plat, plon = point.get("lat"), point.get("lon")
        tags = element.get("tags", {})
        if plat is None or plon is None or not tags.get("name"):
            continue
        results.append(
            {
                "name": tags["name"],
                "lat": plat,
                "lon": plon,
                "distance_from_centre_m": haversine_m(centre_lat, centre_lon, plat, plon),
                "hours": tags.get("opening_hours"),
                **_maps_urls(plat, plon),
            }
        )
    results.sort(key=lambda item: item["distance_from_centre_m"])
    return {
        "bbox": {"south": args.south, "west": args.west, "north": args.north, "east": args.east},
        "categories": categories,
        "count": len(results[: args.limit]),
        "results": results[: args.limit],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maps_client.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="geocode a place name")
    search.add_argument("place")
    search.set_defaults(func=cmd_search)

    reverse = sub.add_parser("reverse", help="coordinates to address")
    reverse.add_argument("lat", type=float)
    reverse.add_argument("lon", type=float)
    reverse.set_defaults(func=cmd_reverse)

    nearby = sub.add_parser("nearby", help="find POIs by category")
    nearby.add_argument("lat", nargs="?", type=float)
    nearby.add_argument("lon", nargs="?", type=float)
    nearby.add_argument("positional_category", nargs="?")
    nearby.add_argument("--near", help="place name instead of lat/lon (auto-geocoded)")
    nearby.add_argument("--category", action="append", help="repeatable")
    nearby.add_argument("--radius", type=int, default=1000, help="metres (default 1000)")
    nearby.add_argument("--limit", type=int, default=10)
    nearby.set_defaults(func=cmd_nearby)

    distance = sub.add_parser("distance", help="travel distance and time")
    distance.add_argument("origin")
    distance.add_argument("--to", required=True)
    distance.add_argument("--mode", default="driving", choices=sorted(MODES))
    distance.set_defaults(func=cmd_distance)

    directions = sub.add_parser("directions", help="turn-by-turn steps")
    directions.add_argument("origin")
    directions.add_argument("--to", required=True)
    directions.add_argument("--mode", default="driving", choices=sorted(MODES))
    directions.set_defaults(func=cmd_directions)

    timezone = sub.add_parser("timezone", help="timezone for coordinates")
    timezone.add_argument("lat", type=float)
    timezone.add_argument("lon", type=float)
    timezone.set_defaults(func=cmd_timezone)

    area = sub.add_parser("area", help="bounding box and size for a place")
    area.add_argument("place")
    area.set_defaults(func=cmd_area)

    bbox = sub.add_parser("bbox", help="POIs inside a bounding box")
    bbox.add_argument("south", type=float)
    bbox.add_argument("west", type=float)
    bbox.add_argument("north", type=float)
    bbox.add_argument("east", type=float)
    bbox.add_argument("category", nargs="?")
    bbox.add_argument("--limit", type=int, default=20)
    bbox.set_defaults(func=cmd_bbox)

    categories = sub.add_parser("categories", help="list supported POI categories")
    categories.set_defaults(func=lambda _args: {"count": len(CATEGORIES), "categories": sorted(CATEGORIES)})

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(json.dumps(args.func(args), ensure_ascii=False, indent=2))
    except MapsError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
