#!/usr/bin/env python3
"""
Extract location signals from a Gmail .mbox archive
====================================================

Streams a Google Takeout mbox and looks for travel/location signals in
each email — hotel confirmations, airline tickets, rideshare receipts,
delivery addresses, restaurant reservations, and explicit city/state
mentions in transactional emails. Outputs a date->locations map for
downstream geocoding/merge.

Hard filters applied BEFORE extraction (cuts marketing-blast noise):
  1. Sender-level: skip pure-marketing senders ("newsletter@", "marketing@",
     "promo@", "deals@", "news@", etc.). We deliberately do NOT inherit
     01_parse_mbox.py's full SKIP_SENDERS list because it blocks every
     "noreply@" address, and legitimate confirmations almost always come
     from noreply senders.
  2. Subject-level: skip if the subject contains promotional language
     ("% off", "sale", "Final Day", "discount", "deal", "Announcing",
     "Earn miles", "limited time", "introducing", "flash sale", etc.).
  3. Body-level: require at least one confirmation phrase in subject or
     body ("reservation confirmed", "boarding pass", "your itinerary",
     "your trip", "your ride", "out for delivery", etc.). No phrase ->
     no extraction.

Heuristics (in rough order of confidence):
  high   — explicit "City, ST" in a confirmation-context email
  high   — airport-code PAIR ("IAH -> LAX", "IAH-LAX", "IAH to LAX") in
           an airline confirmation. Bare 3-letter mentions are NOT used,
           since marketing emails list every destination as a 3-letter
           code and false-positive cities by the dozen.
  medium — bare city name resolved by an unambiguous default
           (Chicago -> IL; or user-specific Houston -> TX)
  medium — international city name in an explicit travel context

No network calls. No external datasets. Standard library only
(plus tqdm for the progress bar, same as 01_parse_mbox.py).

Usage:
  python scripts/08_extract_locations.py "D:\\path\\to\\All mail.mbox"
  python scripts/08_extract_locations.py "...mbox" --limit 1000
  python scripts/08_extract_locations.py "...mbox" \\
      --start-date 2018-06-01 --end-date 2018-09-30

Output:
  data/locations.json — dict keyed by ISO date, each value a list of
                        {city, state, country, confidence, source,
                         email_subject} entries.
"""

from __future__ import annotations

import argparse
import email
import email.header
import email.policy
import email.utils
import json
import re
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm not installed. Run: pip install tqdm", file=sys.stderr)
    sys.exit(1)


DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_OUTPUT = DATA_DIR / "locations.json"


# ─── Pattern bank ────────────────────────────────────────────────────────────

# Sender-domain -> category. Matches exact domain and any subdomain.
HOTEL_DOMAINS = {
    "marriott.com", "marriott-email.com",
    "hilton.com", "res.hilton.com",
    "hyatt.com", "world.hyatt.com",
    "ihg.com", "ihgmail.com",          # Holiday Inn, Crowne Plaza, etc.
    "bestwestern.com",
    "airbnb.com", "airbnbemail.com",
    "vrbo.com", "homeaway.com",
    "hotels.com", "email.hotels.com",
    "booking.com",
    "expedia.com", "reply.expedia.com",
    "priceline.com",
    "orbitz.com",
    "travelocity.com",
    "kayak.com",
    "agoda.com",
    "trip.com", "ctrip.com",
}

AIRLINE_DOMAINS = {
    "southwest.com", "swabiz.com",
    "united.com", "unitedmileageplus.com",
    "delta.com", "email.delta.com",
    "aa.com", "americanairlines.com",
    "jetblue.com",
    "alaskaair.com",
    "spirit.com",
    "frontier.com",
    "allegiant.com",
    "emirates.com",
    "etihad.com",
    "lufthansa.com",
    "britishairways.com",
    "airfrance.com",
    "klm.com",
    "singaporeair.com",
    "qantas.com.au",
    "airindia.in", "airindia.com",
    "indigo.in", "goindigo.in",
    "spicejet.com",
}

RIDESHARE_DOMAINS = {
    "uber.com", "uber.us",
    "lyft.com",
}

DELIVERY_DOMAINS = {
    "doordash.com",
    "ubereats.com",
    "grubhub.com",
    "postmates.com",
    "instacart.com",
    "shipt.com",
    "caviar.com",
    "seamless.com",
}

SHIPPING_DOMAINS = {
    "amazon.com", "amazonses.com",
    "usps.com",
    "fedex.com",
    "ups.com",
    "shopify.com",
}

RESTAURANT_DOMAINS = {
    "yelp.com",
    "opentable.com",
    "resy.com",
    "tock.com",
}


# ─── Promotional / confirmation gates ────────────────────────────────────────

# Sender tokens that indicate a pure marketing list. Confirmation senders
# almost always use "noreply@" / "notifications@" / "support@", so those are
# intentionally NOT in this list (cf. 01_parse_mbox.py SKIP_SENDERS, which
# is fine for sampling human email but would zero out our hit rate here).
PROMO_SENDER_PATTERNS = (
    "newsletter", "marketing", "promo", "promotions",
    "deals@", "offers@", "news@", "newsletter@",
    "campaigns@", "blast@", "broadcast@",
)

# Subject phrases that mark an email as a marketing blast. Matched as
# case-insensitive substrings against the decoded subject. Tuned for the
# kinds of false positives the v1 script produced (Southwest "40% off",
# "Announcing Sip and Ship", etc.).
PROMO_SUBJECT_PATTERNS = (
    "% off", "$ off", " off!", " off ",
    "sale", "final day", "final hours",
    "discount", "discounts",
    "announcing", "earn miles", "miles bonus",
    "deal of", " deals", "today's deal",
    "promotion", "limited time", "exclusive offer",
    "save up to", "save big", "save on",
    "new route", "new routes", "new destination",
    "introducing", "anniversary sale", "flash sale", "blowout",
    "free shipping on", "today only",
    "last chance", "don't miss", "ends today", "ends tonight",
    "special offer", "back by popular demand",
    "rewards", "double points", "bonus points",
)

# At least one of these phrases must appear in subject + first 5K of body
# for the email to be treated as a real confirmation. Without it, we skip
# extraction entirely. Phrases are matched case-insensitively as substrings.
CONFIRMATION_PATTERNS = (
    # hotels
    "reservation confirmed", "reservation confirmation",
    "booking confirmed", "booking confirmation",
    "your reservation", "your booking", "your stay",
    "thank you for your reservation", "thank you for booking",
    "we look forward to your stay", "check-in details",
    # airlines
    "your trip", "your itinerary", "itinerary for",
    "flight is confirmed", "flight confirmed",
    "flight confirmation", "boarding pass", "e-ticket", "eticket",
    "check-in for your flight", "checked in for your flight",
    "your e-ticket", "passenger itinerary",
    # rideshare
    "trip with uber", "your uber", "your lyft",
    "lyft ride", "your ride", "your trip receipt",
    "thanks for riding",
    # delivery / shipping
    "order delivered", "out for delivery",
    "shipping to", "shipped to", "delivery to",
    "your order has shipped", "package delivered",
    "your delivery", "your order is on the way",
    # restaurant
    "reservation reminder", "your table",
    "see you on", "you're confirmed for",
)


def _domain_matches(domain: str, domain_set: set[str]) -> bool:
    """True if domain is in the set or is a subdomain of any entry."""
    if domain in domain_set:
        return True
    for d in domain_set:
        if domain.endswith("." + d):
            return True
    return False


def categorize(domain: str) -> str | None:
    if _domain_matches(domain, HOTEL_DOMAINS):
        return "hotel"
    if _domain_matches(domain, AIRLINE_DOMAINS):
        return "airline"
    if _domain_matches(domain, RIDESHARE_DOMAINS):
        return "rideshare"
    if _domain_matches(domain, DELIVERY_DOMAINS):
        return "delivery"
    if _domain_matches(domain, SHIPPING_DOMAINS):
        return "shipping"
    if _domain_matches(domain, RESTAURANT_DOMAINS):
        return "restaurant"
    return None


def is_promotional_sender(from_full: str) -> bool:
    """from_full is the raw From header ('Foo <bar@x.com>')."""
    a = from_full.lower()
    return any(p in a for p in PROMO_SENDER_PATTERNS)


def is_promotional_subject(subject: str) -> bool:
    s = subject.lower()
    return any(p in s for p in PROMO_SUBJECT_PATTERNS)


def is_confirmation(subject: str, body: str) -> bool:
    """Real confirmations contain at least one structural phrase."""
    text = (subject + "\n" + body[:5000]).lower()
    return any(p in text for p in CONFIRMATION_PATTERNS)


# US states: full name -> 2-letter, plus a set of valid abbreviations.
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
US_STATE_ABBREVS = set(US_STATES.values())

# Cities with one overwhelming common interpretation — emit at medium
# confidence without requiring a state, per the user spec.
DEFAULT_CITY_STATE = {
    "chicago": "IL", "san francisco": "CA", "seattle": "WA",
    "new york": "NY", "los angeles": "CA", "boston": "MA",
    "miami": "FL", "denver": "CO", "atlanta": "GA", "phoenix": "AZ",
    "las vegas": "NV", "philadelphia": "PA", "minneapolis": "MN",
    "detroit": "MI", "dallas": "TX", "san diego": "CA",
    "austin": "TX", "orlando": "FL", "tampa": "FL", "nashville": "TN",
    "pittsburgh": "PA", "baltimore": "MD", "milwaukee": "WI",
    "san jose": "CA", "oakland": "CA",
    "new orleans": "LA", "indianapolis": "IN",
}

# User-specific defaults (per spec): Houston -> TX (medium confidence).
USER_DEFAULT_CITY_STATE = {
    "houston": "TX",
}

# Cities the user explicitly called out as too ambiguous to default-resolve.
# Skipped unless a state appears in the same context.
AMBIGUOUS_CITIES = {
    "memphis", "springfield", "portland", "salem", "auburn",
    "athens", "burlington", "kansas city", "cleveland", "columbus",
    "lexington", "lancaster", "jackson", "manchester", "rochester",
    "richmond", "oxford", "washington",  # WA state vs DC
}

# Major international cities — captured when explicit, country code attached.
INTERNATIONAL_CITIES = {
    "london":    ("London",    None, "UK"),
    "tokyo":     ("Tokyo",     None, "JP"),
    "mumbai":    ("Mumbai",    None, "IN"),
    "delhi":     ("Delhi",     None, "IN"),
    "new delhi": ("New Delhi", None, "IN"),
    "bangalore": ("Bangalore", None, "IN"),
    "bengaluru": ("Bengaluru", None, "IN"),
    "chennai":   ("Chennai",   None, "IN"),
    "kolkata":   ("Kolkata",   None, "IN"),
    "hyderabad": ("Hyderabad", None, "IN"),
    "pune":      ("Pune",      None, "IN"),
    "goa":       ("Goa",       None, "IN"),
    "dubai":     ("Dubai",     None, "AE"),
    "singapore": ("Singapore", None, "SG"),
    "hong kong": ("Hong Kong", None, "HK"),
    "sydney":    ("Sydney",    None, "AU"),
    "melbourne": ("Melbourne", None, "AU"),
    "toronto":   ("Toronto",   None, "CA"),
    "vancouver": ("Vancouver", None, "CA"),
    "amsterdam": ("Amsterdam", None, "NL"),
    "berlin":    ("Berlin",    None, "DE"),
    "rome":      ("Rome",      None, "IT"),
    "madrid":    ("Madrid",    None, "ES"),
    "barcelona": ("Barcelona", None, "ES"),
    "paris":     ("Paris",     None, "FR"),
}

# Common airport codes -> (city, state). state=None for international.
AIRPORT_CODES = {
    # US — major hubs
    "IAH": ("Houston", "TX"),     "HOU": ("Houston", "TX"),
    "LAX": ("Los Angeles", "CA"), "BUR": ("Burbank", "CA"),
    "SNA": ("Santa Ana", "CA"),   "ONT": ("Ontario", "CA"),
    "LGB": ("Long Beach", "CA"),
    "SFO": ("San Francisco", "CA"), "OAK": ("Oakland", "CA"),
    "SJC": ("San Jose", "CA"),
    "JFK": ("New York", "NY"),    "LGA": ("New York", "NY"),
    "EWR": ("Newark", "NJ"),
    "ORD": ("Chicago", "IL"),     "MDW": ("Chicago", "IL"),
    "ATL": ("Atlanta", "GA"),
    "DFW": ("Dallas", "TX"),      "DAL": ("Dallas", "TX"),
    "SEA": ("Seattle", "WA"),
    "DEN": ("Denver", "CO"),
    "MIA": ("Miami", "FL"),       "FLL": ("Fort Lauderdale", "FL"),
    "BOS": ("Boston", "MA"),
    "LAS": ("Las Vegas", "NV"),
    "PHX": ("Phoenix", "AZ"),
    "PHL": ("Philadelphia", "PA"),
    "DCA": ("Washington", "DC"),  "IAD": ("Washington", "DC"),
    "BWI": ("Baltimore", "MD"),
    "AUS": ("Austin", "TX"),
    "BNA": ("Nashville", "TN"),
    "MSP": ("Minneapolis", "MN"),
    "MCO": ("Orlando", "FL"),
    "TPA": ("Tampa", "FL"),
    "SAN": ("San Diego", "CA"),
    "PDX": ("Portland", "OR"),
    "SLC": ("Salt Lake City", "UT"),
    "DTW": ("Detroit", "MI"),
    "HNL": ("Honolulu", "HI"),
    "STL": ("St. Louis", "MO"),
    "CLT": ("Charlotte", "NC"),
    "RDU": ("Raleigh", "NC"),
    "PIT": ("Pittsburgh", "PA"),
    "CMH": ("Columbus", "OH"),
    "CLE": ("Cleveland", "OH"),
    "CVG": ("Cincinnati", "OH"),
    "IND": ("Indianapolis", "IN"),
    "MKE": ("Milwaukee", "WI"),
    "MEM": ("Memphis", "TN"),
    "MSY": ("New Orleans", "LA"),
    "JAX": ("Jacksonville", "FL"),
    "SAT": ("San Antonio", "TX"),
    "OKC": ("Oklahoma City", "OK"),
    "TUL": ("Tulsa", "OK"),
    "ABQ": ("Albuquerque", "NM"),
    "BOI": ("Boise", "ID"),
    "SMF": ("Sacramento", "CA"),
    "RIC": ("Richmond", "VA"),
    "ORF": ("Norfolk", "VA"),
    "BUF": ("Buffalo", "NY"),
    "ALB": ("Albany", "NY"),
    "ROC": ("Rochester", "NY"),
    "PVD": ("Providence", "RI"),
    # International
    "LHR": ("London", None),       "LGW": ("London", None),
    "CDG": ("Paris", None),        "ORY": ("Paris", None),
    "BOM": ("Mumbai", None),
    "DEL": ("Delhi", None),
    "BLR": ("Bangalore", None),
    "MAA": ("Chennai", None),
    "HYD": ("Hyderabad", None),
    "DXB": ("Dubai", None),
    "SIN": ("Singapore", None),
    "HKG": ("Hong Kong", None),
    "SYD": ("Sydney", None),
    "YYZ": ("Toronto", None),
    "YVR": ("Vancouver", None),
    "NRT": ("Tokyo", None),        "HND": ("Tokyo", None),
    "AMS": ("Amsterdam", None),
    "FRA": ("Frankfurt", None),
    "MUC": ("Munich", None),
    "FCO": ("Rome", None),
    "MAD": ("Madrid", None),
    "BCN": ("Barcelona", None),
}

# Words that look like cities but never are — common email-closing words.
CITY_SKIP_WORDS = {
    "sincerely", "thanks", "thank", "best", "regards", "cheers", "love",
    "hello", "hi", "hey", "dear", "team", "support", "attention",
    "subject", "from", "to", "name", "address", "phone", "email",
    "date", "time", "morning", "afternoon", "evening", "tonight",
    "tomorrow", "today", "yesterday", "yes", "no", "maybe",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "view", "click", "open", "close", "reply", "forward",
    "thank you", "best regards", "kind regards",
}


# ─── Regexes ─────────────────────────────────────────────────────────────────

# "City, ST" or "City, Full State Name" — city is 1–3 capitalized words.
# State token alternates against the actual list of US states so we never
# match a non-state word and have to skip-and-advance (which silently ate
# valid matches in v1: "Market St, San Francisco, CA" gobbled "San
# Francisco" as a fake state and missed the real "San Francisco, CA").
def _build_city_state_re() -> "re.Pattern[str]":
    full_names = sorted({name.title() for name in US_STATES.keys()},
                        key=len, reverse=True)
    abbrevs = sorted(US_STATE_ABBREVS)
    state_alt = "|".join(re.escape(s) for s in full_names + abbrevs)
    return re.compile(
        r"\b([A-Z][a-zA-Z]+(?:[\s\-][A-Z][a-zA-Z]+){0,2}),\s+"
        r"(" + state_alt + r")\b"
    )


CITY_STATE_RE = _build_city_state_re()

# Pair-separator characters between airport codes ("→", "✈", em/en dash,
# ASCII hyphen, ">"). The pair finder also accepts the literal word "to".
AIRPORT_PAIR_SEP_CHARS = "→✈➜>–—"

# Hotel/travel framing phrases: "welcome to X", "your stay in X", etc.
WELCOME_RE = re.compile(
    r"(?:welcome to|"
    r"your (?:trip|stay|reservation|booking|visit) (?:in|at|to)|"
    r"reservation (?:in|at|for)|"
    r"booking (?:in|for|at)|"
    r"flying to|arriving in|arrival in|destination[:\s]+)\s+"
    r"([A-Z][a-zA-Z]+(?:[\s\-][A-Z][a-zA-Z]+){0,2})",
    re.IGNORECASE,
)


# ─── HTML stripper (matches 01_parse_mbox.py) ────────────────────────────────

class HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)

    def get_text(self) -> str:
        return " ".join(self.text)


def strip_html(html_text: str) -> str:
    s = HTMLStripper()
    try:
        s.feed(html_text)
        return s.get_text()
    except Exception:
        return html_text


# ─── Email helpers ───────────────────────────────────────────────────────────

def _decode_header(raw: str) -> str:
    if not raw:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(raw))).strip()
    except Exception:
        return raw.strip()


def get_sender_domain(msg) -> str:
    raw = msg.get("From", "") or ""
    _, addr = email.utils.parseaddr(raw)
    if "@" not in addr:
        return ""
    return addr.split("@", 1)[1].lower().strip().rstrip(">")


def get_subject(msg) -> str:
    return _decode_header(msg.get("Subject", ""))


def get_date_iso(msg) -> str | None:
    raw = msg.get("Date", "")
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def get_body_text(msg) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if ctype == "text/html":
                    text = strip_html(text)
                parts.append(text)
            except Exception:
                continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    text = strip_html(text)
                parts.append(text)
        except Exception:
            pass
    return " ".join(parts)


# ─── Stream MBOX (matches 01_parse_mbox.py — never load whole file) ──────────

def stream_mbox(mbox_path: Path):
    """Yield (count, raw_message_bytes) tuples without holding the file in RAM."""
    current: list[bytes] = []
    n = 0
    with open(mbox_path, "rb") as f:
        for line in f:
            if line.startswith(b"From ") and current:
                n += 1
                yield n, b"".join(current)
                current = []
            else:
                current.append(line)
        if current:
            n += 1
            yield n, b"".join(current)


# ─── Extraction ──────────────────────────────────────────────────────────────

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def find_city_state(text: str):
    """Yield (city, state) for explicit 'City, ST' or 'City, State Name' matches."""
    seen = set()
    for m in CITY_STATE_RE.finditer(text):
        city = m.group(1).strip()
        if city.lower() in CITY_SKIP_WORDS:
            continue
        if len(city) < 3:
            continue
        token = m.group(2)
        state = token if token in US_STATE_ABBREVS else US_STATES[token.lower()]
        key = (city.lower(), state)
        if key in seen:
            continue
        seen.add(key)
        yield city, state


_CODE_RE = re.compile(r"\b([A-Z]{3})\b")
_TO_RE = re.compile(r"\bto\b", re.IGNORECASE)


def find_airport_pairs(text: str):
    """Yield (city, state, country) for airport codes appearing in
    structured origin->destination pair patterns. Bare 3-letter mentions
    are intentionally ignored — marketing emails list every destination
    as a 3-letter code, which produced dozens of false positives in v1.

    A "pair" is two known airport codes within 80 characters of each
    other, with a separator (arrow, en/em dash, hyphen-only span, or the
    word "to") between them. This handles the common formats:
      IAH -> LAX
      IAH-LAX
      IAH to LAX
      Houston (IAH) -> Los Angeles (LAX)
    """
    code_matches = [
        (m.start(), m.end(), m.group(1))
        for m in _CODE_RE.finditer(text)
        if m.group(1) in AIRPORT_CODES
    ]
    seen = set()
    for i in range(len(code_matches) - 1):
        _, e1, c1 = code_matches[i]
        s2, _, c2 = code_matches[i + 1]
        if s2 - e1 > 80:
            continue
        between = text[e1:s2]
        has_arrow = any(ch in between for ch in AIRPORT_PAIR_SEP_CHARS)
        has_dash_only = bool(re.fullmatch(r"\s*-+\s*", between))
        has_to = _TO_RE.search(between) is not None
        if not (has_arrow or has_dash_only or has_to):
            continue
        for code in (c1, c2):
            city, state = AIRPORT_CODES[code]
            key = (city, state)
            if key in seen:
                continue
            seen.add(key)
            country = "US" if state else None
            yield city, state, country


def find_welcome_cities(text: str):
    """Yield bare city names from 'welcome to X' / 'your trip to X' phrasings."""
    seen = set()
    for m in WELCOME_RE.finditer(text):
        city = m.group(1).strip()
        if len(city) < 3 or city.lower() in CITY_SKIP_WORDS:
            continue
        if city.lower() in seen:
            continue
        seen.add(city.lower())
        yield city


def resolve_bare_city(city_lower: str):
    """
    For a bare city name with no state context, return
    (city, state, country, confidence) or None to skip.
    """
    if city_lower in USER_DEFAULT_CITY_STATE:
        return city_lower.title(), USER_DEFAULT_CITY_STATE[city_lower], "US", "medium"
    if city_lower in DEFAULT_CITY_STATE:
        return city_lower.title(), DEFAULT_CITY_STATE[city_lower], "US", "medium"
    if city_lower in INTERNATIONAL_CITIES:
        c, s, country = INTERNATIONAL_CITIES[city_lower]
        return c, s, country, "medium"
    if city_lower in AMBIGUOUS_CITIES:
        return None
    # Fall through: not in any list — skip rather than guess wildly.
    return None


def extract_locations(category: str, subject: str, body: str):
    """Return a list of location dicts found in the email."""
    text = (subject + "\n" + body)[:20_000]
    found: list[dict] = []

    # 1) Explicit "City, ST" — high confidence in any travel-category email.
    for city, state in find_city_state(text):
        found.append({
            "city": city,
            "state": state,
            "country": "US",
            "confidence": "high",
            "source": f"{category} email — explicit city/state",
        })

    # 2) Airport-code PAIRS — only meaningful in airline emails, and only
    # when origin/destination appear together (filters marketing blasts
    # that list every airport).
    if category == "airline":
        for city, state, country in find_airport_pairs(text):
            found.append({
                "city": city,
                "state": state,
                "country": country,
                "confidence": "high",
                "source": "airline email — airport-code pair",
            })

    # 3) "Welcome to X" / "Your trip to X" patterns — promote to high in
    # travel-confirmation contexts since the framing phrase is itself a signal.
    if category in ("hotel", "airline", "rideshare"):
        for raw_city in find_welcome_cities(text):
            resolved = resolve_bare_city(raw_city.lower())
            if resolved is None:
                continue
            c, s, country, conf = resolved
            promoted = "high" if category in ("hotel", "airline") else conf
            found.append({
                "city": c,
                "state": s,
                "country": country,
                "confidence": promoted,
                "source": f"{category} email — welcome/reservation phrase",
            })

    # Dedup by (city, state); keep the highest-confidence entry.
    best: dict[tuple, dict] = {}
    for loc in found:
        key = (loc["city"].lower(), loc.get("state"))
        prior = best.get(key)
        if prior is None or CONFIDENCE_RANK[loc["confidence"]] > CONFIDENCE_RANK[prior["confidence"]]:
            best[key] = loc
    return list(best.values())


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract location signals from a Gmail .mbox archive.",
    )
    parser.add_argument(
        "mbox_path",
        help='Path to the .mbox file (e.g. "D:\\path\\to\\All mail.mbox")',
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after processing N emails (test mode).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path (default: data/locations.json).",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Only process emails dated >= this ISO date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Only process emails dated <= this ISO date (YYYY-MM-DD).",
    )
    args = parser.parse_args()

    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for label, val in (("--start-date", args.start_date), ("--end-date", args.end_date)):
        if val and not iso_re.match(val):
            print(f"Error: {label} must be YYYY-MM-DD, got {val!r}", file=sys.stderr)
            return 1

    mbox_path = Path(args.mbox_path)
    if not mbox_path.exists():
        print(f"Error: mbox file not found at {mbox_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    file_size = mbox_path.stat().st_size
    print(f"Streaming {mbox_path.name} ({file_size / 1e9:.1f} GB)")
    print(f"Limit:    {args.limit or 'none — full scan'}")
    print(f"Date:     {args.start_date or '...'} -> {args.end_date or '...'}")
    print(f"Output:   {output_path}")
    print("─" * 60)

    locations: dict[str, list[dict]] = defaultdict(list)
    cat_hits: Counter = Counter()
    confidence_hits: Counter = Counter()
    cities_seen: Counter = Counter()
    total = 0
    matched = 0
    skipped_promo_sender = 0
    skipped_promo_subject = 0
    skipped_no_confirm = 0
    skipped_out_of_range = 0
    last_progress_at = 0

    parser_policy = email.policy.compat32
    start_date = args.start_date
    end_date = args.end_date

    pbar = tqdm(stream_mbox(mbox_path), unit=" emails", smoothing=0.05)
    for n, raw in pbar:
        total = n
        if args.limit and total > args.limit:
            break

        if total - last_progress_at >= 1000:
            pbar.set_postfix(matched=matched, dates=len(locations))
            last_progress_at = total

        try:
            msg = email.message_from_bytes(raw, policy=parser_policy)
        except Exception:
            continue

        domain = get_sender_domain(msg)
        category = categorize(domain)
        if category is None:
            continue

        # Sender-level promo gate. Inexpensive — runs before body parse.
        raw_from = msg.get("From", "") or ""
        if is_promotional_sender(raw_from):
            skipped_promo_sender += 1
            continue

        date = get_date_iso(msg)
        if not date:
            continue
        if start_date and date < start_date:
            skipped_out_of_range += 1
            continue
        if end_date and date > end_date:
            skipped_out_of_range += 1
            continue

        # Subject-level promo gate. Also inexpensive.
        subject = get_subject(msg)
        if is_promotional_subject(subject):
            skipped_promo_subject += 1
            continue

        # Body parse + confirmation-phrase gate.
        body = get_body_text(msg)
        if not is_confirmation(subject, body):
            skipped_no_confirm += 1
            continue

        results = extract_locations(category, subject, body)
        if not results:
            continue

        matched += 1
        cat_hits[category] += 1
        for loc in results:
            loc["email_subject"] = subject[:200]
            locations[date].append(loc)
            confidence_hits[loc["confidence"]] += 1
            cities_seen[(loc["city"], loc.get("state"))] += 1

    pbar.close()

    # Write output
    sorted_locations = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locations, f, indent=2, ensure_ascii=False)

    # Summary
    total_loc_entries = sum(len(v) for v in locations.values())
    print()
    print("─" * 60)
    print(f"Scanned:           {total:>8,} emails")
    print(f"Matched:           {matched:>8,} emails")
    print(f"Dated locations:   {total_loc_entries:>8,}")
    print(f"Unique dates:      {len(locations):>8,}")
    print()
    print("Skipped (filters):")
    print(f"  promo sender     {skipped_promo_sender:>8,}")
    print(f"  promo subject    {skipped_promo_subject:>8,}")
    print(f"  no confirm phr.  {skipped_no_confirm:>8,}")
    print(f"  out of date rng  {skipped_out_of_range:>8,}")
    print()
    print("By category:")
    for cat, n in cat_hits.most_common():
        print(f"  {cat:12s} {n:>8,}")
    print()
    print("By confidence:")
    for conf in ("high", "medium", "low"):
        if confidence_hits[conf]:
            print(f"  {conf:12s} {confidence_hits[conf]:>8,}")
    print()
    print("Top 30 cities:")
    for (c, s), n in cities_seen.most_common(30):
        print(f"  {c}, {s or '?':<3s}  {n:>5,}")
    print()
    print(f"Wrote {output_path} ({total_loc_entries} entries across {len(locations)} dates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
