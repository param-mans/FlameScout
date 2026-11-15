"""House catalog (data/raw/catalog.csv, scripts/build_house_catalog.mjs): an extra source for the address locator.
Plain functions, no LLM.

One metadata row per scanned house that passed the plan quality check (listing_id, matterport_model_id, address, city,
state, zip, floors, bedrooms, bathrooms, sqft, year_built, room_types, exterior_doors, stairs, quality). Loaded once and
cached (reloaded only when the file changes). Missing file = empty catalog: everything works as before.

match() scores a spoken address against every row, deterministically:
- normalized: lowercase, no punctuation, street suffixes and directions in one short form (Lane/Ln -> ln, North -> n);
- digits said as words become the house number ("nine six six oh" -> 9660, "ninety six sixty" -> 9660; oh / zero = 0);
- the street name must match; a house number that was said must match exactly (a wrong number is no candidate);
- without a number every house on that street is a candidate at a low score (the dispatcher picks);
- a city that was said must be the house's city, and adds to the score; a matching suffix adds, a different one subtracts.
The auto-confirm rule (services/api/service.py: auto_confirm_pick) decides what to do with the scores.
"""
from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CATALOG_PATH: Optional[Path] = None   # None: <providers.RAW_ROOT>/catalog.csv (tests redirect RAW_ROOT)

SUFFIXES = {"street": "st", "st": "st", "lane": "ln", "ln": "ln", "court": "ct", "ct": "ct", "drive": "dr", "dr": "dr",
            "road": "rd", "rd": "rd", "boulevard": "blvd", "blvd": "blvd", "avenue": "ave", "ave": "ave", "av": "ave",
            "place": "pl", "pl": "pl", "circle": "cir", "cir": "cir", "trail": "trl", "trl": "trl", "way": "way",
            "parkway": "pkwy", "pkwy": "pkwy", "terrace": "ter", "ter": "ter", "highway": "hwy", "hwy": "hwy"}
DIRECTIONS = {"north": "n", "n": "n", "south": "s", "s": "s", "east": "e", "e": "e", "west": "w", "w": "w"}
_DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
           "seven": "7", "eight": "8", "nine": "9"}
_TEENS = {"ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
          "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19"}
_TENS = {"twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6", "seventy": "7", "eighty": "8",
         "ninety": "9"}
_STOP = {"the", "at", "on", "in", "of", "a", "an", "its", "it", "is", "house", "home", "address", "fire", "were",
         "we", "im", "i", "my", "our", "near", "by", "unit", "apt", "please", "hurry", "and", "texas", "tx",
         "california", "ca", "usa"}

SCORE_NUMBER = 0.85        # house number + street name
SCORE_NAME_ONLY = 0.5      # street name, no number said: every house on that street, the dispatcher picks
SCORE_PARTIAL_NAME = 0.7   # house number + only part of the street name
BONUS_SUFFIX, MALUS_SUFFIX, BONUS_CITY = 0.05, 0.15, 0.1


def words_to_digits(tokens: list[str]) -> list[str]:
    """Runs of number words -> one digit string: "nine six six oh" -> "9660", "eighteen one oh seven" -> "18107".
    A run needs two tokens or more, and "oh" alone is never a number."""
    out, run = [], []

    def flush():
        if len(run) >= 2 and any(t != "oh" for t in run):
            digits, i = "", 0
            while i < len(run):
                t = run[i]
                if t in _TENS:
                    if i + 1 < len(run) and run[i + 1] in _DIGITS and run[i + 1] not in ("zero", "oh"):
                        digits += _TENS[t] + _DIGITS[run[i + 1]]
                        i += 2
                        continue
                    digits += _TENS[t] + "0"
                else:
                    digits += _TEENS.get(t) or _DIGITS[t]
                i += 1
            out.append(digits)
        else:
            out.extend(run)
        run.clear()

    for t in tokens:
        if t in _DIGITS or t in _TEENS or t in _TENS:
            run.append(t)
        else:
            flush()
            out.append(t)
    flush()
    return out


def normalize(text: str) -> list[str]:
    """Lowercase tokens, no punctuation, number words as digits, suffixes and directions in short form."""
    raw = re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", ""))
    return [SUFFIXES.get(t) or DIRECTIONS.get(t) or t for t in words_to_digits(raw)]


@dataclass(frozen=True)
class Address:
    number: Optional[str]
    name: tuple[str, ...]          # street name tokens, without suffix and direction
    suffix: Optional[str]
    direction: Optional[str]


def parse_street(tokens: list[str]) -> Address:
    """A catalog street ("9660 Fairdale Lane", "1234 N Braeswood Boulevard") in normalized tokens."""
    number = tokens[0] if tokens and tokens[0].isdigit() else None
    rest = tokens[1:] if number else tokens
    direction = rest[0] if len(rest) > 1 and rest[0] in DIRECTIONS.values() else None
    rest = rest[1:] if direction else rest
    suffix = rest[-1] if len(rest) > 1 and rest[-1] in SUFFIXES.values() else None
    rest = rest[:-1] if suffix else rest
    return Address(number, tuple(rest), suffix, direction)


@dataclass
class Row:
    listing_id: str
    data: dict
    street: Address
    city: str


@dataclass
class Catalog:
    rows: list[Row]
    by_id: dict[str, dict]
    cities: list[tuple[str, ...]]  # normalized city names, longest first


_lock = threading.Lock()
_cache: dict = {"key": None, "catalog": Catalog([], {}, [])}


def catalog_path() -> Path:
    if CATALOG_PATH is not None:
        return Path(CATALOG_PATH)
    from .providers import RAW_ROOT  # read at call time: tests redirect it
    return Path(RAW_ROOT) / "catalog.csv"


def load() -> Catalog:
    """The catalog, read once and cached; re-read only when the file changes. Missing or unreadable: empty."""
    path = catalog_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(path), None, None)
    with _lock:
        if _cache["key"] == key:
            return _cache["catalog"]
        rows: list[Row] = []
        if key[1] is not None:
            try:
                with path.open(newline="", encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        if r.get("listing_id") and r.get("matterport_model_id") and r.get("address"):
                            rows.append(Row(r["listing_id"], r, parse_street(normalize(r["address"])),
                                            " ".join(normalize(r.get("city", "")))))
            except (OSError, csv.Error, UnicodeDecodeError):
                rows = []
        cities = sorted({tuple(r.city.split()) for r in rows if r.city}, key=len, reverse=True)
        cat = Catalog(rows, {r.listing_id: r.data for r in rows}, cities)
        _cache.update(key=key, catalog=cat)
        return cat


def get(listing_id: str) -> Optional[dict]:
    """The catalog row of a listing id, or None."""
    return load().by_id.get(str(listing_id or ""))


def model_id(listing_id: str) -> Optional[str]:
    row = get(listing_id)
    return row["matterport_model_id"] if row else None


def full_address(row: dict) -> str:
    """ "9660 Fairdale Lane, Houston, TX 77063" """
    tail = " ".join(x for x in (row.get("state"), row.get("zip")) if x)
    return ", ".join(x for x in (row.get("address"), row.get("city"), tail) if x)


@dataclass
class Query:
    number: Optional[str]
    street: list[str]               # tokens that may be the street (name, suffix, direction)
    city: Optional[str]


def parse_query(text: str, cities: list[tuple[str, ...]]) -> Query:
    toks = normalize(text)
    city = None
    for c in cities:                # longest city first ("los angeles" before a one-word city)
        n = len(c)
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == c:
                city = " ".join(c)
                toks = toks[:i] + toks[i + n:]
                break
        if city:
            break
    number, street = None, []
    for i, t in enumerate(toks):
        if t.isdigit() and number is None:
            number = t
            street = toks[i + 1:]
            break
    if number is None:
        street = toks
    # the street ends at its suffix (what follows is city / state / zip / chatter)
    for i, t in enumerate(street):
        if t in SUFFIXES.values() and i > 0:
            street = street[:i + 1]
            break
    street = [t for t in street if t not in _STOP and not t.isdigit()]
    return Query(number, street, city)


@dataclass
class Match:
    listing_id: str
    row: dict
    score: float
    reasons: list[str]


def match(text: str, limit: int = 5) -> list[Match]:
    """Catalog houses for a spoken address, best first (see the module docstring for the scoring)."""
    cat = load()
    if not cat.rows or not (text or "").strip():
        return []
    q = parse_query(text, cat.cities)
    said = set(q.street)
    name_words = said - set(SUFFIXES.values()) - set(DIRECTIONS.values())
    dirs = said & set(DIRECTIONS.values())
    if not name_words:
        return []
    out: list[Match] = []
    for r in cat.rows:
        a = r.street
        if not a.name:
            continue
        name = set(a.name)
        if q.city and r.city != q.city:
            continue
        if a.direction and dirs and a.direction not in dirs:
            continue
        reasons = []
        if name == name_words:
            full = True
        elif q.number and name & name_words and (name <= name_words or name_words <= name):
            full = False
        else:
            continue
        if q.number:
            if a.number != q.number:
                continue          # a house number that was said must be this house's
            score = SCORE_NUMBER if full else SCORE_PARTIAL_NAME
            reasons.append(f"catalog: house number {a.number} and street "
                           + ("name" if full else "name partly") + f" match ({r.data['address']})")
        else:
            score = SCORE_NAME_ONLY
            reasons.append(f"catalog: street name matches, no house number said ({r.data['address']})")
        if a.suffix and a.suffix in said:
            score += BONUS_SUFFIX
        elif a.suffix and said & set(SUFFIXES.values()):
            score -= MALUS_SUFFIX
            reasons.append("catalog: street type differs")
        if q.city:
            score += BONUS_CITY
            reasons.append(f"catalog: city {r.data.get('city')} matches")
        out.append(Match(r.listing_id, r.data, round(min(1.0, max(0.0, score)), 2), reasons))
    out.sort(key=lambda m: (-m.score, m.listing_id))
    return out[:limit]
