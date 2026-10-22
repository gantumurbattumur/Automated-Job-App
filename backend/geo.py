"""Offline geocoding for the "within N miles of <city>" filter.

Uses GeoNames' free city list (every town over 5,000 people), downloaded once into data/geo/. Job locations come in
many shapes — "San Francisco, CA", "Hybrid - Austin, TX", "Boise, ID - Main Site", "USA - California - San Jose",
"Seattle, San Francisco" — so each one is split into pieces and every piece that names a city becomes a point.
"""
import io
import json
import math
import re
import threading
import zipfile
from functools import lru_cache

from .config import DATA

URL = "https://download.geonames.org/export/dump/cities5000.zip"
CACHE = DATA / "geo" / "cities.json"
_lock = threading.Lock()

US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
             "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
             "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
             "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
             "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
             "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
             "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
             "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
             "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia"}
STATE_CODE = {v.lower(): k for k, v in US_STATES.items()}
COUNTRIES = {"usa": "US", "us": "US", "u.s.": "US", "united states": "US", "united states of america": "US", "canada": "CA",
             "united kingdom": "GB", "uk": "GB", "england": "GB", "india": "IN", "germany": "DE", "france": "FR", "ireland": "IE",
             "netherlands": "NL", "singapore": "SG", "japan": "JP", "china": "CN", "australia": "AU", "mexico": "MX",
             "spain": "ES", "poland": "PL", "israel": "IL", "brazil": "BR", "switzerland": "CH", "sweden": "SE"}
ALIASES = {"la": ("los angeles", "CA"), "nyc": ("new york city", "NY"), "new york": ("new york city", "NY"),
           "sf": ("san francisco", "CA"), "bay area": ("san francisco", "CA"), "sf bay area": ("san francisco", "CA"),
           "silicon valley": ("san jose", "CA"), "south bay": ("san jose", "CA"), "dc": ("washington", "DC"),
           "washington dc": ("washington", "DC"), "washington, dc": ("washington", "DC"), "philly": ("philadelphia", "PA"),
           "nyc metro": ("new york city", "NY"), "manhattan": ("new york city", "NY"), "brooklyn": ("new york city", "NY"),
           "rtp": ("durham", "NC"), "research triangle": ("durham", "NC"), "dfw": ("dallas", "TX")}
NOISE = re.compile(r"\b(hybrid|remote|on-?site|in-?office|main site|campus|hq|headquarters|office|offices|metro(politan)?|"
                   r"greater|area|region|downtown|locations?|multiple|various|flexible|anywhere|and|or)\b", re.I)


def _download() -> dict:
    import httpx
    r = httpx.get(URL, timeout=120, follow_redirects=True)
    r.raise_for_status()
    index: dict[str, list] = {}
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for line in io.TextIOWrapper(z.open("cities5000.txt"), encoding="utf-8"):
            f = line.rstrip("\n").split("\t")
            # name, asciiname, lat, lon, country, admin1, population
            entry = [round(float(f[4]), 4), round(float(f[5]), 4), f[8], f[10], int(f[14] or 0)]
            for name in {f[1].lower(), f[2].lower()}:
                if name:
                    index.setdefault(name, []).append(entry)
    for name in index:  # biggest first
        index[name].sort(key=lambda e: -e[4])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(index, separators=(",", ":")))
    return index


@lru_cache
def index() -> dict[str, list]:
    with _lock:
        return json.loads(CACHE.read_text()) if CACHE.exists() else _download()


def _city(name: str, state: str | None = None, country: str | None = None):
    """(lat, lon, label) for a city name, optionally narrowed by US state code or country code."""
    name = name.strip().lower().replace("st.", "saint").replace("ft.", "fort")
    if name in ALIASES and not (state and state != ALIASES[name][1]):
        name, state = ALIASES[name]
    cands = index().get(name) or index().get(name.replace("saint", "st.")) or []
    if state:
        cands = [c for c in cands if c[2] == "US" and c[3] == state]
    elif country:
        cands = [c for c in cands if c[2] == country]
    if not cands:
        return None
    best = cands[0]
    if not state and not country:  # bare "Cambridge": prefer the US one unless it's tiny next to the biggest match
        us = [c for c in cands if c[2] == "US"]
        if us and us[0][4] >= 0.3 * best[4]:
            best = us[0]
    where = best[3] if best[2] == "US" else best[2]
    return best[0], best[1], f"{name.title()}, {where}"


def _region(token: str) -> tuple[str | None, str | None]:
    t = token.strip().lower().rstrip(".")
    if t.upper() in US_STATES and len(t) == 2:
        return t.upper(), None
    if t in STATE_CODE:
        return STATE_CODE[t], None
    if t in COUNTRIES:
        return None, COUNTRIES[t]
    return None, None


def geocode_piece(piece: str):
    piece = re.sub(r"\([^)]*\)", " ", piece)
    low = re.sub(r"\s+", " ", piece).strip(" ,-–").lower()
    if low in ALIASES:
        return _city(low)
    parts = [p.strip() for p in piece.split(",") if p.strip()]
    if len(parts) >= 2:  # "City, ST" / "City, State, Country" / "City, Country"
        state, country = _region(parts[1])
        if len(parts) >= 3 and not (state or country):
            state, country = _region(parts[2])
        if state or country:
            city = re.sub(r"\s+", " ", NOISE.sub(" ", parts[0])).strip(" -–")
            return _city(city, state, country) if city else None
    clean = re.sub(r"\s+", " ", NOISE.sub(" ", piece)).strip(" ,-–")
    if not clean or _region(clean) != (None, None):  # a bare state/country isn't a point
        return None
    return _city(clean)


@lru_cache(maxsize=50000)
def points(location: str) -> tuple:
    """Every place a job location names, as ((lat, lon, label), ...)."""
    if not location:
        return ()
    out = []
    for seg in re.split(r"[;•|/]|\s+or\s+|\s+[-–]\s+", location):
        p = geocode_piece(seg)
        if p is None and seg.count(",") >= 1:  # "Seattle, San Francisco, New York": a list of bare city names
            subs = [geocode_piece(s) for s in seg.split(",")]
            if all(subs) and len(subs) > 1:
                out += subs
                continue
        if p:
            out.append(p)
    return tuple(dict.fromkeys(out))


def miles(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def center(text: str):
    """The point for what you typed in the "Near" box ("LA", "Los Angeles, CA", "Austin TX", "90210" not supported)."""
    t = text.strip()
    if not t:
        return None
    m = re.match(r"^(.+?)[,\s]+([A-Za-z]{2})$", t)  # "Austin TX" without a comma
    if m and m[2].upper() in US_STATES and "," not in t:
        t = f"{m[1]}, {m[2]}"
    pts = points(t)
    return pts[0] if pts else None


def nearest(location: str, c: tuple) -> float | None:
    pts = points(location)
    return min(miles(c, p) for p in pts) if pts else None


def suggest(prefix: str, n: int = 8) -> list[str]:
    """US cities starting with what you typed, biggest first (for the Near box)."""
    p = prefix.strip().lower()
    if len(p) < 2:
        return []
    hits = []
    for name, cands in index().items():
        if name.startswith(p):
            hits += [(c[4], f"{name.title()}, {c[3]}") for c in cands if c[2] == "US"]
    return [label for _, label in sorted(set(hits), reverse=True)[:n]]
