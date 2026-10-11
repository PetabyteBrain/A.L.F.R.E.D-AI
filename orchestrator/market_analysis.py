"""Local market analysis (#53): find businesses in an area that could become clients of the company, and
write a report into the company files.

    spec = build_spec(...)        -> what to search and how (validated; MarketError with a readable message)
    await start(person, spec)     -> {"started": True, ...} (runs in the background, reports on Telegram)

What to search: built-in industries (English and German words mapped to OpenStreetMap tags), explicit
OpenStreetMap tags (e.g. craft=floorer) and/or keywords matched against business names in any language
(e.g. boden, parkett, flooring). How thorough: quick / standard / thorough / complete set how many
businesses are taken and how many web searches look for missing websites (bigger runs need a yes because
of their cost). Report options: focus (no website / weak website / both), score threshold, chains in or
out, shortlist size and order, Markdown and/or Excel, own title and notes.

Steps: geocode the place (Open-Meteo) -> businesses within the radius (Overpass API) -> web searches for
missing websites (Claude + Anthropic web search) -> a quick check of each website (HTTPS, mobile viewport,
speed, copyright year, online booking) -> a fit score with reasons -> report in `Market analysis/` in the
company share. A repeat run of the same search marks what's new.

Rules: only public business listings; no personal data of individuals is looked up; Alfred never contacts
anyone from here (outreach is a human step, Swiss UWG/FADP). Website checks never go to private, loopback,
link-local or tailnet addresses (resolved and checked on every redirect). OpenStreetMap data © its
contributors (ODbL), credited in every report.
"""
from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import logging
import math
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from . import db, usage
from .config import settings
from .identity.registry import Person

log = logging.getLogger(__name__)

# Public Overpass servers, tried in turn (they are busy at times: 429/504 or timeouts move on to the next).
# For places in Switzerland the Swiss OpenStreetMap association's server (Swiss data only) goes first.
OVERPASS_CH = "https://overpass.osm.ch/api/interpreter"
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
FOLDER = "Market analysis"
MAX_RADIUS_KM = 30
SITE_TIMEOUT_S, SITE_MAX_BYTES, SITE_CONCURRENCY = 8, 600_000, 6
RUN_TIMEOUT_S = 25 * 60
LOOKUP_COST_USD = 0.012                 # one web search (~1 ct) + the model's tokens for it
CONFIRM_ABOVE_LOOKUPS = 50              # more web searches than this need the person's yes (cost)
_SEEN_KEY = "market_analysis_seen"
_running: set[int] = set()

# How thorough: how many businesses are taken (closest first) and how many web searches look for websites
# OpenStreetMap doesn't list. "complete" looks up every business without a listed website (up to the cap).
THOROUGHNESS = {
    "quick": {"max_businesses": 150, "lookups": 0, "label": "quick (no web searches)"},
    "standard": {"max_businesses": 300, "lookups": 15, "label": "standard"},
    "thorough": {"max_businesses": 400, "lookups": 50, "label": "thorough"},
    "complete": {"max_businesses": 500, "lookups": 250, "label": "complete (every missing website looked up)"},
}
FOCUS = ("both", "no_website", "weak_website")
SORTS = ("score", "distance", "name")

# Industry words -> OpenStreetMap tags. "*" = any value of that key.
INDUSTRIES: dict[str, list[tuple[str, str]]] = {
    # food and drink
    "restaurant": [("amenity", "restaurant")], "cafe": [("amenity", "cafe")], "bar": [("amenity", "bar"), ("amenity", "pub")],
    "fast food": [("amenity", "fast_food")], "ice cream": [("amenity", "ice_cream")], "bakery": [("shop", "bakery"), ("shop", "pastry")],
    "confectionery": [("shop", "confectionery"), ("shop", "chocolate")], "butcher": [("shop", "butcher")],
    "cheese": [("shop", "cheese"), ("craft", "cheese")], "deli": [("shop", "deli")], "greengrocer": [("shop", "greengrocer")],
    "farm shop": [("shop", "farm")], "beverages": [("shop", "beverages")], "wine": [("shop", "wine"), ("craft", "winery")],
    "brewery": [("craft", "brewery")], "distillery": [("craft", "distillery")], "caterer": [("craft", "caterer")],
    "supermarket": [("shop", "supermarket"), ("shop", "convenience")], "kiosk": [("shop", "kiosk")],
    # personal care and health
    "hairdresser": [("shop", "hairdresser")], "beauty": [("shop", "beauty")], "massage": [("shop", "massage")],
    "tattoo": [("shop", "tattoo")], "spa": [("leisure", "sauna"), ("amenity", "spa")], "dentist": [("amenity", "dentist"), ("healthcare", "dentist")],
    "doctor": [("amenity", "doctors")], "clinic": [("amenity", "clinic")], "physiotherapy": [("healthcare", "physiotherapist")],
    "psychotherapy": [("healthcare", "psychotherapist")], "chiropractor": [("healthcare", "chiropractor")],
    "podiatrist": [("healthcare", "podiatrist")], "alternative medicine": [("healthcare", "alternative")],
    "pharmacy": [("amenity", "pharmacy")], "optician": [("shop", "optician")], "hearing aids": [("shop", "hearing_aids")],
    "medical supply": [("shop", "medical_supply")], "veterinary": [("amenity", "veterinary")], "nursing home": [("amenity", "nursing_home")],
    "fitness": [("leisure", "fitness_centre")], "dance school": [("leisure", "dance"), ("amenity", "dancing_school")],
    # shops
    "clothes": [("shop", "clothes")], "shoes": [("shop", "shoes")], "jewelry": [("shop", "jewelry")], "watches": [("shop", "watches")],
    "books": [("shop", "books")], "stationery": [("shop", "stationery")], "gift": [("shop", "gift")], "toys": [("shop", "toys")],
    "florist": [("shop", "florist")], "garden centre": [("shop", "garden_centre")], "pet shop": [("shop", "pet")],
    "pet grooming": [("shop", "pet_grooming")], "sports": [("shop", "sports"), ("shop", "outdoor")], "bicycle": [("shop", "bicycle")],
    "music instruments": [("shop", "musical_instrument")], "computer": [("shop", "computer")], "mobile phone": [("shop", "mobile_phone")],
    "electronics": [("shop", "electronics")], "furniture": [("shop", "furniture")], "kitchen": [("shop", "kitchen")],
    "bathroom": [("shop", "bathroom_furnishing")], "interior decoration": [("shop", "interior_decoration"), ("shop", "curtain")],
    "flooring": [("shop", "flooring"), ("craft", "floorer"), ("shop", "carpet")], "tiles": [("shop", "tiles"), ("craft", "tiler")],
    "paint": [("shop", "paint")], "hardware": [("shop", "hardware"), ("shop", "doityourself")], "second hand": [("shop", "second_hand")],
    "antiques": [("shop", "antiques")], "art": [("shop", "art"), ("shop", "frame")], "photo": [("shop", "photo")],
    "copy shop": [("shop", "copyshop")], "travel agency": [("shop", "travel_agency")], "dry cleaning": [("shop", "dry_cleaning"), ("shop", "laundry")],
    "funeral": [("shop", "funeral_directors")], "tobacco": [("shop", "tobacco")],
    # vehicles
    "car repair": [("shop", "car_repair")], "car dealer": [("shop", "car")], "tyres": [("shop", "tyres")],
    "car wash": [("amenity", "car_wash")], "motorcycle": [("shop", "motorcycle")], "driving school": [("amenity", "driving_school")],
    # trades
    "plumber": [("craft", "plumber")], "electrician": [("craft", "electrician")], "carpenter": [("craft", "carpenter"), ("craft", "joiner")],
    "painter": [("craft", "painter")], "gardener": [("craft", "gardener")], "roofer": [("craft", "roofer")],
    "builder": [("craft", "builder")], "plasterer": [("craft", "plasterer")], "stonemason": [("craft", "stonemason")],
    "locksmith": [("craft", "locksmith")], "glazier": [("craft", "glaziery")], "window construction": [("craft", "window_construction")],
    "metal construction": [("craft", "metal_construction")], "heating": [("craft", "hvac"), ("craft", "heating_engineer")],
    "upholsterer": [("craft", "upholsterer")], "tailor": [("craft", "tailor"), ("shop", "tailor")], "shoemaker": [("craft", "shoemaker")],
    "watchmaker": [("craft", "watchmaker")], "cleaning": [("craft", "cleaning")], "printer": [("craft", "printer")],
    "sign maker": [("craft", "sign_maker")], "photographer": [("craft", "photographer")],
    # offices and services
    "lawyer": [("office", "lawyer")], "notary": [("office", "notary")], "accountant": [("office", "accountant"), ("office", "tax_advisor")],
    "architect": [("office", "architect")], "real estate": [("office", "estate_agent")], "insurance": [("office", "insurance")],
    "financial advisor": [("office", "financial_advisor")], "consulting": [("office", "consulting")],
    "advertising agency": [("office", "advertising_agency")], "it": [("office", "it")], "engineer": [("office", "engineer")],
    "employment agency": [("office", "employment_agency")], "association": [("office", "association"), ("office", "ngo")],
    "coworking": [("amenity", "coworking_space"), ("office", "coworking")],
    # education and care
    "language school": [("amenity", "language_school")], "music school": [("amenity", "music_school")],
    "kindergarten": [("amenity", "kindergarten"), ("amenity", "childcare")],
    # hospitality and leisure
    "hotel": [("tourism", "hotel")], "guest house": [("tourism", "guest_house"), ("tourism", "apartment")], "hostel": [("tourism", "hostel")],
    "camping": [("tourism", "camp_site")], "event venue": [("amenity", "events_venue")], "museum": [("tourism", "museum")],
    # everything of a kind
    "shop": [("shop", "*")], "office": [("office", "*")], "craft": [("craft", "*")],
}
SYNONYMS = {
    "restaurants": "restaurant", "gastronomy": "restaurant", "gastronomie": "restaurant", "café": "cafe", "coffee": "cafe",
    "pub": "bar", "takeaway": "fast food", "glacé": "ice cream", "gelato": "ice cream", "bäckerei": "bakery", "konditorei": "confectionery",
    "confiserie": "confectionery", "chocolate": "confectionery", "metzgerei": "butcher", "käserei": "cheese", "hofladen": "farm shop",
    "getränke": "beverages", "weinhandlung": "wine", "brauerei": "brewery", "catering": "caterer", "lebensmittel": "supermarket",
    "coiffeur": "hairdresser", "friseur": "hairdresser", "hair salon": "hairdresser", "salon": "hairdresser", "barber": "hairdresser",
    "barbershop": "hairdresser", "kosmetik": "beauty", "nails": "beauty", "nail salon": "beauty", "beauty salon": "beauty",
    "zahnarzt": "dentist", "arzt": "doctor", "praxis": "doctor", "physio": "physiotherapy", "physiotherapist": "physiotherapy",
    "psychotherapeut": "psychotherapy", "naturheilpraxis": "alternative medicine", "apotheke": "pharmacy", "optiker": "optician",
    "hörgeräte": "hearing aids", "tierarzt": "veterinary", "vet": "veterinary", "altersheim": "nursing home", "gym": "fitness",
    "fitness studio": "fitness", "tanzschule": "dance school", "fashion": "clothes", "mode": "clothes", "boutique": "clothes",
    "schuhe": "shoes", "schmuck": "jewelry", "uhren": "watches", "bookshop": "books", "papeterie": "stationery", "geschenke": "gift",
    "spielwaren": "toys", "blumen": "florist", "gartencenter": "garden centre", "tierhandlung": "pet shop", "velo": "bicycle",
    "bike": "bicycle", "musikinstrumente": "music instruments", "möbel": "furniture", "küche": "kitchen", "küchenbau": "kitchen",
    "bad": "bathroom", "einrichtung": "interior decoration", "vorhänge": "interior decoration", "floors": "flooring",
    "floor layer": "flooring", "floor layers": "flooring", "bodenleger": "flooring", "bodenbeläge": "flooring", "parkett": "flooring",
    "teppich": "flooring", "carpet": "flooring", "fliesen": "tiles", "platten": "tiles", "plattenleger": "tiles", "tiler": "tiles",
    "farben": "paint", "eisenwaren": "hardware", "brockenhaus": "second hand", "secondhand": "second hand", "antiquitäten": "antiques",
    "galerie": "art", "gallery": "art", "einrahmung": "art", "druckerei": "printer", "printing": "printer", "reisebüro": "travel agency",
    "reinigung": "cleaning", "wäscherei": "dry cleaning", "bestatter": "funeral", "garage": "car repair", "autogarage": "car repair",
    "mechanic": "car repair", "car": "car dealer", "reifen": "tyres", "autowaschanlage": "car wash", "fahrschule": "driving school",
    "sanitär": "plumber", "spengler": "plumber", "elektriker": "electrician", "schreiner": "carpenter", "zimmermann": "carpenter",
    "maler": "painter", "gärtner": "gardener", "dachdecker": "roofer", "bauunternehmen": "builder", "maurer": "builder",
    "construction": "builder", "gipser": "plasterer", "steinmetz": "stonemason", "schlosser": "metal construction",
    "schlüsseldienst": "locksmith", "glaser": "glazier", "fensterbau": "window construction", "heizung": "heating", "hvac": "heating",
    "polsterer": "upholsterer", "schneiderei": "tailor", "schuhmacher": "shoemaker", "uhrmacher": "watchmaker", "fotograf": "photographer",
    "anwalt": "lawyer", "lawyers": "lawyer", "notar": "notary", "treuhand": "accountant", "accounting": "accountant",
    "architekt": "architect", "immobilien": "real estate", "versicherung": "insurance", "beratung": "consulting",
    "werbeagentur": "advertising agency", "marketing": "advertising agency", "informatik": "it", "ingenieur": "engineer",
    "personalvermittlung": "employment agency", "verein": "association", "sprachschule": "language school",
    "musikschule": "music school", "kita": "kindergarten", "childcare": "kindergarten", "hotels": "hotel", "pension": "guest house",
    "ferienwohnung": "guest house", "jugendherberge": "hostel", "campingplatz": "camping", "eventlocation": "event venue",
    "shops": "shop", "retail": "shop", "laden": "shop", "trades": "craft", "handwerk": "craft", "offices": "office",
}
BOOKING_MATTERS = {"restaurant", "cafe", "bar", "hairdresser", "beauty", "massage", "spa", "tattoo", "hotel", "guest house",
                   "hostel", "dentist", "doctor", "physiotherapy", "psychotherapy", "chiropractor", "podiatrist",
                   "alternative medicine", "veterinary", "fitness", "dance school", "driving school", "music school",
                   "language school", "car repair", "pet grooming"}
SOCIAL = ("facebook.com", "instagram.com", "tiktok.com", "linktr.ee", "business.site", "wixsite.com/")
DIRECTORIES = ("local.ch", "search.ch", "tripadvisor", "google.", "yelp.", "booking.com", "maps.apple")
_TAG = re.compile(r"^(shop|craft|office|amenity|healthcare|tourism|leisure|club|industrial)=([a-z0-9_:-]+|\*)$")
_KEYWORD = re.compile(r"^[\w\- ']{2,30}$")
_NAME_KEYS = "shop|craft|office|amenity|healthcare|tourism|leisure|club"


class MarketError(ValueError):
    """Message is safe to show to the person / the model."""


@dataclass
class Spec:
    area: str
    radius_km: float = 5.0
    industries: list[str] = field(default_factory=list)          # known industry keys
    osm_tags: list[tuple[str, str]] = field(default_factory=list)  # explicit OpenStreetMap tags
    keywords: list[str] = field(default_factory=list)             # matched against business names
    thoroughness: str = "standard"
    max_businesses: int = 300
    lookups: int = 15
    check_websites: bool = True
    focus: str = "both"
    min_score: int = 50
    exclude_chains: bool = True
    shortlist: int = 40
    sort: str = "score"
    formats: tuple[str, ...] = ("markdown", "excel")
    title: str = ""
    notes: str = ""

    @property
    def label(self) -> str:
        parts = list(self.industries) + [f"{k}={v}" for k, v in self.osm_tags]
        if self.keywords:
            parts.append("names with " + " / ".join(self.keywords))
        return ", ".join(parts)

    @property
    def estimate_usd(self) -> float:
        return round(self.lookups * LOOKUP_COST_USD, 2)


def resolve_industries(words: list[str]) -> tuple[list[str], list[str]]:
    known, unknown = [], []
    for w in words or []:
        key = " ".join(str(w).lower().split())
        key = SYNONYMS.get(key, key)
        if key not in INDUSTRIES:
            for cand in (key[:-1] if key.endswith("s") else "", key[:-2] if key.endswith("es") else ""):
                if cand in INDUSTRIES or cand in SYNONYMS:
                    key = SYNONYMS.get(cand, cand)
                    break
        (known if key in INDUSTRIES else unknown).append(key)
    return sorted(set(known)), unknown


def build_spec(area: str, industries: list[str] | None = None, radius_km: float = 5, osm_tags: list[str] | None = None,
               name_keywords: list[str] | None = None, thoroughness: str = "standard", web_lookups: int | None = None,
               check_websites: bool = True, focus: str = "both", min_score: int = 50, exclude_chains: bool = True,
               shortlist: int = 40, sort: str = "score", report_format: str = "both", title: str = "",
               notes: str = "") -> Spec:
    area = " ".join(str(area or "").split())[:80]
    if not area:
        raise MarketError("Which place should the analysis be around?")
    try:
        radius_km = float(radius_km)
    except (TypeError, ValueError):
        raise MarketError("The radius must be a number of kilometres.")
    if not 0.5 <= radius_km <= MAX_RADIUS_KM:
        raise MarketError(f"Pick a radius between 0.5 and {MAX_RADIUS_KM} km.")
    known, unknown = resolve_industries(industries or [])
    tags = []
    for t in osm_tags or []:
        m = _TAG.match(str(t).strip().lower())
        if not m:
            raise MarketError(f"'{t}' isn't an OpenStreetMap tag I can search (use key=value, e.g. craft=floorer).")
        tags.append((m.group(1), m.group(2)))
    keywords = []
    for k in list(name_keywords or []) + unknown:      # unknown industry words become name keywords too
        k = " ".join(str(k).split()).lower()
        if not _KEYWORD.match(k):
            raise MarketError(f"'{k}' can't be used as a name keyword (letters, digits, spaces, '-' only).")
        keywords.append(k)
    keywords = sorted(set(keywords))[:12]
    if len(tags) > 12:
        raise MarketError("At most 12 OpenStreetMap tags per analysis.")
    if not (known or tags or keywords):
        raise MarketError("Which industries should I look at (e.g. restaurants, hairdressers, floor layers)?")
    level = THOROUGHNESS.get(str(thoroughness or "standard").lower())
    if not level:
        raise MarketError(f"Thoroughness must be one of: {', '.join(THOROUGHNESS)}.")
    lookups = level["lookups"] if web_lookups is None else int(web_lookups)
    if not 0 <= lookups <= 250:
        raise MarketError("Web lookups must be between 0 and 250.")
    if focus not in FOCUS:
        raise MarketError(f"Focus must be one of: {', '.join(FOCUS)}.")
    if sort not in SORTS:
        raise MarketError(f"Sort must be one of: {', '.join(SORTS)}.")
    formats = {"both": ("markdown", "excel"), "markdown": ("markdown",), "excel": ("excel",)}.get(report_format)
    if not formats:
        raise MarketError("Report format must be markdown, excel or both.")
    return Spec(area=area, radius_km=radius_km, industries=known, osm_tags=tags, keywords=keywords,
                thoroughness=str(thoroughness).lower(), max_businesses=level["max_businesses"], lookups=lookups,
                check_websites=bool(check_websites), focus=focus, min_score=max(0, min(int(min_score), 95)),
                exclude_chains=bool(exclude_chains), shortlist=max(5, min(int(shortlist), 300)), sort=sort,
                formats=formats, title=" ".join(str(title or "").split())[:120], notes=str(notes or "").strip()[:1500])


# --- data ---------------------------------------------------------------------------------------

def _all_tags(spec: Spec) -> list[tuple[str, str]]:
    return list(dict.fromkeys([t for ind in spec.industries for t in INDUSTRIES[ind]] + list(spec.osm_tags)))


def _overpass_query(spec: Spec, lat: float, lon: float) -> str:
    def sel(k: str, v: str) -> str:
        return f'["{k}"]' if v == "*" else f'["{k}"="{v}"]'
    around = f"(around:{int(spec.radius_km * 1000)},{lat:.5f},{lon:.5f})"
    parts = "".join(f'nwr{sel(k, v)}["name"]{around};' for k, v in _all_tags(spec))
    if spec.keywords:                     # one statement per business key: uses the key index, stays cheap
        pattern = "|".join(re.sub(r"[^\w\- ']", "", k) for k in spec.keywords)
        parts += "".join(f'nwr["{key}"]["name"~"{pattern}",i]{around};' for key in _NAME_KEYS.split("|"))
    return f"[out:json][timeout:90];({parts});out center tags {spec.max_businesses * 3};"


def _distance_km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _norm_url(url: str | None) -> str | None:
    url = (url or "").strip().split(";")[0].strip()
    if not url:
        return None
    if not re.match(r"(?i)^https?://", url):
        url = "https://" + url.lstrip("/")
    return url


def _category(tags: dict, spec: Spec) -> str:
    for ind in spec.industries:
        for k, v in INDUSTRIES[ind]:
            if k in tags and (v == "*" or tags[k] == v):
                return ind if v != "*" else f"{ind}: {tags[k].replace('_', ' ')}"
    for k, v in spec.osm_tags:
        if k in tags and (v == "*" or tags[k] == v):
            return tags[k].replace("_", " ")
    for k in _NAME_KEYS.split("|"):
        if tags.get(k):
            return f"{tags[k].replace('_', ' ')} (name match)"
    return "other"


def parse_businesses(data: dict, spec: Spec, lat: float, lon: float) -> list[dict]:
    out, seen = [], set()
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        la, lo = el.get("lat") or (el.get("center") or {}).get("lat"), el.get("lon") or (el.get("center") or {}).get("lon")
        if not name or la is None:
            continue
        street = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x)
        town = tags.get("addr:city") or tags.get("addr:place") or ""
        key = (name.lower(), street.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "osm_id": f"{el.get('type')}/{el.get('id')}", "name": name, "category": _category(tags, spec),
            "street": street, "postcode": tags.get("addr:postcode", ""), "town": town,
            "distance_km": round(_distance_km(lat, lon, la, lo), 2),
            "website": _norm_url(tags.get("website") or tags.get("contact:website") or tags.get("url")),
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "email": tags.get("email") or tags.get("contact:email") or "",
            "opening_hours": tags.get("opening_hours", ""), "website_source": "OpenStreetMap",
            "chain": bool(tags.get("brand") or tags.get("brand:wikidata")),
        })
    if spec.exclude_chains:
        out = [b for b in out if not b["chain"]]
    out.sort(key=lambda b: b["distance_km"])
    return out[:spec.max_businesses]


async def fetch_businesses(spec: Spec, lat: float, lon: float, swiss: bool = False) -> list[dict]:
    headers = {"User-Agent": f"{settings.assistant_name}-assistant/1.0 (market analysis; {settings.contact_email})"}
    query, problems = _overpass_query(spec, lat, lon), []
    servers = ((OVERPASS_CH,) if swiss else ()) + OVERPASS_URLS
    async with httpx.AsyncClient(timeout=100) as c:
        for attempt, url in enumerate(servers * 2):                # every server, then once more after a pause
            if attempt == len(servers):
                await asyncio.sleep(20)
            try:
                r = await c.post(url, data={"data": query}, headers=headers)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                    usage.record_request("openstreetmap", "overpass")
                    return parse_businesses(r.json(), spec, lat, lon)
                problems.append(f"{urlsplit(url).hostname}: HTTP {r.status_code}")
            except (httpx.HTTPError, ValueError) as e:
                problems.append(f"{urlsplit(url).hostname}: {type(e).__name__}")
    log.warning("Overpass failed everywhere: %s", "; ".join(problems))
    raise MarketError("The OpenStreetMap servers are all busy or down right now. Try again in a while.")


# --- website checks -----------------------------------------------------------------------------

async def _host_problem(host: str) -> str:
    """'' if every address the host resolves to is public (no LAN, loopback, link-local, tailnet/CGNAT)."""
    if not host:
        return "not a public web address"
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return "domain not found"
    ips = {ipaddress.ip_address(i[4][0]) for i in infos}
    return "" if ips and all(ip.is_global for ip in ips) else "not a public web address"


async def _public_host(host: str) -> bool:
    return not await _host_problem(host)


YEAR = re.compile(r"(?:©|&copy;|copyright)\D{0,25}((?:19|20)\d{2})(?:\s*(?:-|–|&ndash;|bis)\s*((?:19|20)\d{2}))?", re.I)
BOOKING = re.compile(r"\b(book(?:ing)?|reserv|termin|buchen|online[- ]?shop|order online|bestellen|calendly|"
                     r"timify|treatwell|opentable|thefork|quandoo)\w*", re.I)


async def check_site(url: str, client: httpx.AsyncClient | None = None) -> dict:
    out = {"reachable": False, "https": False, "mobile": None, "seconds": None, "copyright_year": None,
           "booking": False, "final_url": url, "error": ""}
    own = client is None
    client = client or httpx.AsyncClient(timeout=SITE_TIMEOUT_S, follow_redirects=False,
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; site check)"})
    started = time.monotonic()
    try:
        current = url
        for _ in range(5):                                   # redirects, each one checked
            parts = urlsplit(current)
            problem = "not a public web address" if parts.scheme not in ("http", "https") else                 await _host_problem(parts.hostname or "")
            if problem:
                out["error"] = problem
                return out
            async with client.stream("GET", current) as r:
                if r.is_redirect and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) >= SITE_MAX_BYTES:
                        break
                out.update(final_url=current, reachable=r.status_code < 400, https=current.startswith("https://"))
                if r.status_code >= 400:
                    out["error"] = f"HTTP {r.status_code}"
                    return out
                html = body.decode("utf-8", errors="replace")
                out["mobile"] = bool(re.search(r'<meta[^>]+name=["\']?viewport', html, re.I))
                years = [int(y) for m in YEAR.finditer(html) for y in m.groups() if y]
                out["copyright_year"] = max(years) if years else None
                out["booking"] = bool(BOOKING.search(html))
                return out
        out["error"] = "too many redirects"
    except httpx.HTTPError as e:
        out["error"] = type(e).__name__
    finally:
        out["seconds"] = round(time.monotonic() - started, 1)
        if own:
            await client.aclose()
    return out


async def check_sites(businesses: list[dict]) -> None:
    sem = asyncio.Semaphore(SITE_CONCURRENCY)
    async with httpx.AsyncClient(timeout=SITE_TIMEOUT_S, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 (compatible; site check)"}) as client:
        async def one(b):
            if not b["website"] or _is_social(b["website"]):
                return
            async with sem:
                b["site"] = await check_site(b["website"], client)
        await asyncio.gather(*(one(b) for b in businesses))


def _is_social(url: str) -> bool:
    return any(s in url.lower() for s in SOCIAL)


# --- web search for missing websites ------------------------------------------------------------

async def find_missing_websites(businesses: list[dict], limit: int, person: Person) -> tuple[int, int]:
    """Ask Claude with web search for the official website of businesses OpenStreetMap lists without one."""
    if not settings.web_search_enabled or limit <= 0:
        return 0, 0
    from . import ai_config
    if not ai_config.configured():
        return 0, 0
    from .agent_core import client
    todo = [b for b in businesses if not b["website"]][:limit]
    for b in todo:
        b["looked_up"] = True
    found = 0
    for i in range(0, len(todo), 8):
        batch = todo[i:i + 8]
        listing = [{"id": n, "name": b["name"], "category": b["category"], "address": f"{b['street']}, {b['town']}".strip(", ")}
                   for n, b in enumerate(batch)]
        system = ("For each business, search the web for its own official website. Directory or map listings "
                  "(local.ch, search.ch, Google Maps, TripAdvisor) don't count; a Facebook or Instagram page counts "
                  "only if it is all they have. Reply with JSON only: "
                  '[{"id": 0, "website": "https://..." or null}]')
        try:
            resp = await client().messages.create(
                model=settings.claude_model, max_tokens=1500, system=system,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": len(batch)}],
                messages=[{"role": "user", "content": json.dumps(listing, ensure_ascii=False)}])
        except Exception as e:  # noqa: BLE001 — a failed lookup only means fewer websites found
            log.warning("Market analysis web lookup failed: %s", e)
            continue
        usage.record_claude(getattr(resp, "usage", None), kind="market_analysis", user_id=person.id)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"\[.*\]", text, re.S)
        try:
            rows = json.loads(m.group(0)) if m else []
        except ValueError:
            rows = []
        for row in rows:
            try:
                b = batch[int(row.get("id"))]
            except (TypeError, ValueError, IndexError):
                continue
            url = _norm_url(row.get("website"))
            if url and not any(d in url.lower() for d in DIRECTORIES):
                b["website"], b["website_source"] = url, "web search"
                found += 1
    return len(todo), found


# --- scoring and report -------------------------------------------------------------------------

def score(b: dict, year_now: int) -> tuple[int, list[str]]:
    if not b["website"]:
        if b.get("looked_up"):
            return 90, ["no website found (web search)"]
        return 70, ["no website listed on OpenStreetMap (not looked up)"]
    if _is_social(b["website"]):
        return 80, ["only a social media page"]
    site = b.get("site") or {}
    if not site:
        return 30, ["website not checked"]
    if not site.get("reachable"):
        return 85, [f"website not reachable ({site.get('error') or 'no answer'})"]
    s, why = 20, []
    if not site.get("https"):
        s += 20; why.append("no HTTPS")
    if site.get("mobile") is False:
        s += 30; why.append("not mobile-friendly")
    if (site.get("seconds") or 0) > 3:
        s += 10; why.append(f"slow ({site['seconds']} s)")
    y = site.get("copyright_year")
    if y and y <= year_now - 3:
        s += 20; why.append(f"looks outdated (© {y})")
    if b["category"].split(":")[0].split(" (")[0] in BOOKING_MATTERS and not site.get("booking"):
        s += 10; why.append("no online booking or ordering")
    return min(s, 95), why or ["website looks fine"]


def _web_status(b: dict) -> str:
    if not b["website"]:
        return "none"
    if _is_social(b["website"]):
        return "social media only"
    site = b.get("site") or {}
    if not site:
        return "not checked"
    return "reachable" if site.get("reachable") else "not reachable"


def _matches_focus(b: dict, focus: str) -> bool:
    no_site = not b["website"] or _is_social(b["website"])
    return focus == "both" or (focus == "no_website") == no_site


def _sort_key(spec: Spec):
    return {"score": lambda b: (-b["score"], b["distance_km"]), "distance": lambda b: b["distance_km"],
            "name": lambda b: b["name"].lower()}[spec.sort]


def build_markdown(spec: Spec, run: dict, businesses: list[dict]) -> str:
    fit = sorted((b for b in businesses if b["fit"]), key=_sort_key(spec))
    towns: dict[str, int] = {}
    for b in fit:
        towns[b["town"] or "unknown"] = towns.get(b["town"] or "unknown", 0) + 1
    focus = {"both": "no or a weak website", "no_website": "no website", "weak_website": "a weak website"}[spec.focus]
    title = spec.title or f"Market analysis: {spec.label} within {spec.radius_km:g} km of {run['area']}"
    lines = [f"# {title}", "", f"*{run['date']} · prepared by {settings.assistant_name} for {settings.company_name}*", ""]
    if spec.notes:
        lines += ["## Notes", "", spec.notes, ""]
    lines += ["## Summary", "",
              f"- Searched: {spec.label} within {spec.radius_km:g} km of {run['area']} ({THOROUGHNESS[spec.thoroughness]['label']})",
              f"- Businesses found: **{len(businesses)}** (OpenStreetMap{', chains left out' if spec.exclude_chains else ''})",
              f"- Fitting potential clients ({focus}, score ≥ {spec.min_score}): **{len(fit)}**",
              f"- No website, confirmed by web search: **{sum(1 for b in businesses if not b['website'] and b.get('looked_up'))}**",
              f"- No website listed on OpenStreetMap, not looked up: {sum(1 for b in businesses if not b['website'] and not b.get('looked_up'))}"
              + (" (a more thorough run checks them)" if spec.thoroughness != "complete" else ""),
              f"- Websites found by web search: {run['found_by_search']} (of {run['lookups']} lookups)"]
    if run.get("previous"):
        lines.append(f"- New since the last report for this search: **{sum(1 for b in businesses if b.get('new'))}**")
    lines += ["", "## Shortlist", "", "| # | Business | Category | Town | Contact | Website | Score | Why |",
              "|---|---|---|---|---|---|---|---|"]
    for n, b in enumerate(fit[:spec.shortlist], 1):
        new = " 🆕" if b.get("new") else ""
        contact = b["phone"] or b["email"] or "–"
        lines.append(f"| {n} | {b['name']}{new} | {b['category']} | {b['town'] or '–'} | {contact} | {_web_status(b)} | "
                     f"{b['score']} | {'; '.join(b['reasons'])} |")
    if len(fit) > spec.shortlist:
        lines.append(f"\n…and {len(fit) - spec.shortlist} more in the Excel file." if "excel" in spec.formats
                     else f"\n…and {len(fit) - spec.shortlist} more (ask for a longer shortlist or the Excel list).")
    lines += ["", "## Where they are", "", "| Town | Fitting businesses |", "|---|---|"]
    lines += [f"| {t} | {n} |" for t, n in sorted(towns.items(), key=lambda x: -x[1])]
    lines += ["", "## Method", "",
              f"Businesses matching {spec.label} within {spec.radius_km:g} km of {run['area']} were taken from "
              "OpenStreetMap (name keywords match business names). Listed websites were opened once to check HTTPS, "
              "a mobile layout, loading time, the copyright year and online booking; for businesses without a listed "
              "website, web searches looked for one. Scores: no website (confirmed) 90, unreachable 85, social media "
              "only 80, no website listed but not looked up 70, otherwise 20 plus points per weakness (not "
              "mobile-friendly 30, outdated 20, no HTTPS 20, slow 10, no online booking 10).", "",
              "Only public business listings were used. Contacting businesses is up to you: in Switzerland, "
              "unsolicited advertising must respect the UWG (e.g. the 'no advertising' asterisk in the phone "
              "directory) and the FADP.", "",
              "Data © OpenStreetMap contributors (ODbL), https://www.openstreetmap.org/copyright", ""]
    return "\n".join(lines)


def build_excel(spec: Spec, run: dict, businesses: list[dict]) -> bytes:
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Businesses"
    cols = ["Fit", "Score", "Why", "Name", "Category", "Street", "Postcode", "Town", "Distance km", "Website",
            "Website from", "Website status", "HTTPS", "Mobile", "Load s", "© year", "Online booking", "Phone",
            "Email", "Opening hours", "Chain", "New", "OpenStreetMap"]
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    yes = lambda v: {True: "yes", False: "no"}.get(v, "")  # noqa: E731
    for b in sorted(businesses, key=lambda b: (not b["fit"], _sort_key(spec)(b))):
        s = b.get("site") or {}
        ws.append(["yes" if b["fit"] else "", b["score"], "; ".join(b["reasons"]), b["name"], b["category"], b["street"],
                   b["postcode"], b["town"], b["distance_km"], b["website"] or "", b["website_source"] if b["website"] else "",
                   _web_status(b), yes(s.get("https")), yes(s.get("mobile")), s.get("seconds") or "", s.get("copyright_year") or "",
                   yes(s.get("booking")) if s else "", b["phone"], b["email"], b["opening_hours"], yes(b.get("chain")),
                   "yes" if b.get("new") else "", f"https://www.openstreetmap.org/{b['osm_id']}"])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col, width in zip("ABCDEFGHIJKLMNOPQRSTUVW", (5, 7, 45, 32, 22, 26, 9, 16, 11, 36, 13, 16, 7, 8, 7, 7, 9, 18, 26,
                                                      24, 6, 5, 40)):
        ws.column_dimensions[col].width = width
    info = wb.create_sheet("About")
    for row in (("Title", spec.title or "Market analysis"), ("Area", run["area"]), ("Radius km", spec.radius_km),
                ("Searched", spec.label), ("Thoroughness", spec.thoroughness), ("Focus", spec.focus),
                ("Score threshold", spec.min_score), ("Chains", "left out" if spec.exclude_chains else "included"),
                ("Date", run["date"]), ("Businesses", len(businesses)), ("Fitting", sum(1 for b in businesses if b["fit"])),
                ("Web lookups", run["lookups"]), ("Notes", spec.notes),
                ("Source", "© OpenStreetMap contributors (ODbL); web search for missing websites")):
        info.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- run ----------------------------------------------------------------------------------------

def _run_key(area: str, spec: Spec) -> str:
    return f"{' '.join(area.lower().split())}|{spec.radius_km:g}|{spec.label}"


async def analyse(person: Person, spec: Spec) -> dict:
    """The whole analysis; returns the saved file paths and counts (raises MarketError)."""
    from . import briefing, nas_files
    try:
        place = await briefing.geocode(spec.area)
    except briefing.BriefingError as e:
        raise MarketError(str(e).replace("weather", "analysis"))
    swiss = place["location"].endswith(("Switzerland", "Liechtenstein"))
    businesses = await fetch_businesses(spec, place["lat"], place["lon"], swiss)
    if not businesses:
        raise MarketError(f"OpenStreetMap lists nothing for {spec.label} within {spec.radius_km:g} km of {place['location']}.")
    searched, found = await find_missing_websites(businesses, spec.lookups, person)
    if spec.check_websites:
        await check_sites(businesses)
    year = datetime.now(ZoneInfo(settings.timezone)).year
    for b in businesses:
        b["score"], b["reasons"] = score(b, year)
        b["fit"] = b["score"] >= spec.min_score and _matches_focus(b, spec.focus)
    seen = db.get_setting(_SEEN_KEY, {}) or {}
    key = _run_key(place["location"], spec)
    previous = set(seen.get(key, []))
    for b in businesses:
        b["new"] = bool(previous) and b["osm_id"] not in previous
    seen[key] = [b["osm_id"] for b in businesses]
    db.set_setting(_SEEN_KEY, dict(list(seen.items())[-50:]))
    date = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")
    run = {"area": place["location"], "date": date, "found_by_search": found, "lookups": searched, "previous": bool(previous)}
    what = spec.title or f"{place['location'].split(',')[0]} {spec.radius_km:g}km {' '.join(spec.industries + spec.keywords) or 'tags'}"
    stem = f"{date} {what}"[:120]
    files = []
    if "markdown" in spec.formats:
        md = await asyncio.to_thread(nas_files.upload, person, "company", FOLDER, f"{stem}.md",
                                     build_markdown(spec, run, businesses).encode("utf-8"))
        files.append(md["saved"])
    if "excel" in spec.formats:
        xl = await asyncio.to_thread(nas_files.upload, person, "company", FOLDER, f"{stem}.xlsx", build_excel(spec, run, businesses))
        files.append(xl["saved"])
    fit = sorted((b for b in businesses if b["fit"]), key=_sort_key(spec))
    db.log_activity("system", f"Market analysis by {person.display_name}: {len(businesses)} businesses, {len(fit)} fitting "
                              f"({spec.label}, {spec.radius_km:g} km of {place['location']})"[:300], user_id=person.id)
    return {"area": place["location"], "businesses": len(businesses), "fitting": len(fit),
            "no_website": sum(1 for b in businesses if not b["website"]), "new": sum(1 for b in businesses if b.get("new")),
            "top": [f"{b['name']} ({b['town'] or b['category']}): {'; '.join(b['reasons'])}" for b in fit[:5]],
            "files": files}


async def start(person: Person, spec: Spec, confirmed: bool = False) -> dict:
    from . import nas
    nas.check_scope(person, "company")
    if not nas.enabled():
        raise MarketError("The NAS isn't connected, so there's nowhere to save the report.")
    if spec.lookups > CONFIRM_ABOVE_LOOKUPS and not confirmed:
        return {"started": False, "needs_confirmation": True, "web_lookups": spec.lookups,
                "estimated_cost_usd": spec.estimate_usd,
                "note": f"This run does up to {spec.lookups} web searches (about ${spec.estimate_usd:.2f}). Tell the person "
                        "the estimate and start it with confirmed=true only after they agree in their own message."}
    if person.id in _running:
        raise MarketError("An analysis for you is already running; I'll report when it's done.")
    _running.add(person.id)

    async def job():
        try:
            res = await asyncio.wait_for(analyse(person, spec), RUN_TIMEOUT_S)
            text = (f"📊 Market analysis ready: {res['fitting']} of {res['businesses']} businesses look like fitting "
                    f"clients ({spec.label} within {spec.radius_km:g} km of {res['area']}; {res['no_website']} without "
                    f"a website" + (f", {res['new']} new since last time" if res["new"] else "") + ").\n\n"
                    + "\n".join(f"• {t}" for t in res["top"]) + "\n\nSaved in the company files:\n"
                    + "\n".join(f"• {f}" for f in res["files"]))
        except MarketError as e:
            text = f"The market analysis couldn't finish: {e}"
        except asyncio.TimeoutError:
            text = "The market analysis took too long and was stopped. Try a smaller radius or a quicker run."
        except Exception:  # noqa: BLE001 — reported, never crashes the server
            log.exception("Market analysis failed")
            text = "The market analysis failed unexpectedly; the details are in the server log."
        finally:
            _running.discard(person.id)
        try:
            from .telegram_handler import send_to_user
            await send_to_user(person.id, text, note="market analysis", silent=False)
        except Exception:  # noqa: BLE001
            log.exception("Couldn't send the market analysis result")

    asyncio.get_running_loop().create_task(job())
    minutes = "1-5" if spec.lookups <= 15 else "5-15" if spec.lookups <= 60 else "15-25"
    return {"started": True, "searching": spec.label, "radius_km": spec.radius_km, "area": spec.area,
            "thoroughness": spec.thoroughness, "web_lookups": spec.lookups, "estimated_cost_usd": spec.estimate_usd,
            "note": f"Running in the background (about {minutes} minutes). The report goes to the company files under "
                    f"'{FOLDER}', and the person gets a Telegram message when it's ready. Tell them that briefly."}
