#!/usr/bin/env python3
"""Refresh the public NJ Young People AA meeting snapshot.

Northern NJ and Cape Atlantic are refreshed from their live sources.
South Jersey is hardcoded because AASJ blocks GitHub Actions.

Fail closed: never replace good data with empty/partial live-source data.
"""

import datetime as dt
import html
import json
import logging
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# PATHS / SOURCES
# ============================================================

ROOT = Path(__file__).resolve().parent

DATA = ROOT / "meetings.json"
CACHE = ROOT / "geocodes.json"

NORTH_BASE = (
    "https://nnjaa.org/intergroup/cgi-bin/list_special.php"
)

CAPE_URL = (
    "https://capeatlanticaa.org/locations/"
)

DAYS = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)

LOG = logging.getLogger("meetings")


# ============================================================
# HARDCODED SOUTH JERSEY MEETINGS
# ============================================================

SOUTH_JERSEY_MEETINGS = [
    {
        "name": "Cherry Hill Young People",
        "day": "Wednesday",
        "time": "8:00 PM",
        "town": "Haddonfield",
        "location": "Haddonfield United Methodist Church",
        "address": "29 Warwick Rd, Haddonfield, NJ 08033, USA",
        "types": "Big Book, Discussion, Open, Wheelchair Access",
        "lat": 39.894687,
        "lon": -75.0369955,
        "source": "South Jersey",
    },
    {
        "name": "Young Men of Merchantville",
        "day": "Thursday",
        "time": "8:00 PM",
        "town": "Merchantville",
        "location": "Grace Episcopal Church (Merchantville)",
        "address": "7 E Maple Ave, Merchantville, NJ 08109, USA",
        "types": "Big Book, Men, Open",
        "lat": 39.950836,
        "lon": -75.0483033,
        "source": "South Jersey",
    },  
    {
        "name": "Sober Savages",
        "day": "Friday",
        "time": "8:30 PM",
        "town": "Clementon",
        "location": "400 Club",
        "address": "42 Berlin Road Clementon, NJ 08021, USA",
        "types": "Discussion, Newcomer, Step Meeting",
        "lat": 39.8053,
        "lon": -74.9890,
        "source": "South Jersey",
    },
    {
        "name": "Sober Savages Big Book",
        "day": "Saturday",
        "time": "7:30 PM",
        "town": "Clementon",
        "location": "400 Club",
        "address": "42 Berlin Road Clementon, NJ 08021, USA",
        "types": "Big Book, Newcomer, Speaker",
        "lat": 39.8053,
        "lon": -74.9890,
        "source": "South Jersey",
    },
    {
        "name": "Union Hill Friday Night YP",
        "day": "Friday",
        "time": "10:30 PM",
        "town": "Denville",
        "location": "Union Hill Presbyterian Church",
        "address": "427 Franklin rd Denville, NJ",
        "types": "Discussion, Open",
        "lat": 40.86812,
        "lon": -74.52293,
        "source": "South Jersey",
    },
    {
        "name": "Franklin Monday Nite YP",
        "day": "Monday",
        "time": "7:30 PM",
        "town": "Denville",
        "location": "Immaculate Conception Church",
        "address": "75 Church St Franklin, NJ 07416",
        "types": "Closed Discussion",
        "lat": 41.1167,
        "lon": -74.5919,
        "source": "South Jersey",
    },
]


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})


# ============================================================
# HELPERS
# ============================================================

def text(value):
    return re.sub(
        r"\s+",
        " ",
        html.unescape(str(value or "")),
    ).strip()


def young(value):
    return bool(
        re.search(
            r"\byoung\b",
            str(value or ""),
            re.I,
        )
    )


def valid_coords(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)

        return (
            38.7 <= lat <= 41.5
            and -75.8 <= lon <= -73.7
        )

    except (TypeError, ValueError):
        return False


def get_time(value):
    match = re.search(
        r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b",
        str(value or ""),
        re.I,
    )

    if not match:
        return ""

    return text(
        match.group()
    ).upper()


# ============================================================
# DOWNLOAD
# ============================================================

def fetch(url, params=None):
    last_error = None

    for attempt in range(1, 4):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=35,
                allow_redirects=True,
            )

            LOG.info(
                "HTTP %s %s",
                response.status_code,
                response.url,
            )

            response.raise_for_status()

            if not response.text.strip():
                raise ValueError(
                    "Source returned an empty response"
                )

            return response.text

        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            last_error = exc

            if attempt < 3:
                LOG.warning(
                    "Request failed for %s "
                    "(attempt %d/3): %s",
                    url,
                    attempt,
                    exc,
                )

                time.sleep(2 * attempt)

    raise last_error or RuntimeError(
        "Unable to download " + url
    )


# ============================================================
# TABLE HELPERS
# ============================================================

def cells_from_row(row):
    """
    Preserve handling for Northern NJ's malformed HTML.
    """

    cells = row.find_all(
        ["td", "th"],
        recursive=False,
    )

    output = []

    for cell in cells:
        nested = cell.find(
            ["td", "th"],
            recursive=False,
        )

        if nested:
            value = " ".join(
                str(node)
                for node in cell.contents
                if getattr(
                    node,
                    "name",
                    None,
                ) not in ("td", "th")
            )

            value = text(value)

        else:
            value = text(
                cell.get_text(
                    " ",
                    strip=True,
                )
            )

        classes = " ".join(
            cell.get("class", [])
        )

        output.append(
            (value, classes)
        )

    return output


# ============================================================
# NORTHERN NJ
# ============================================================

def north_address(location, town):
    cleaned = re.sub(
        r"^\s*\(\s*Young[^)]*\)\s*",
        "",
        location,
        flags=re.I,
    )

    cleaned = re.sub(
        r"\([^)]*\)",
        " ",
        cleaned,
    )

    cleaned = text(cleaned)

    match = re.search(
        r"\b\d{1,6}(?:-\d{1,6})?\s+.+",
        cleaned,
    )

    street = (
        match.group(0).strip()
        if match
        else cleaned
    )

    return f"{street}, {town}, NJ"


def north_rows(markup):
    soup = BeautifulSoup(
        markup,
        "html.parser",
    )

    found = []

    for row in soup.select("tr"):
        cells = cells_from_row(row)

        by_class = {
            cls: value
            for value, classes in cells
            for cls in classes.split()
        }

        day = by_class.get(
            "day",
            "",
        )

        if day not in DAYS:
            continue

        when = get_time(
            by_class.get(
                "time",
                "",
            )
        )

        # Northern NJ third column / town is
        # intentionally used as the meeting title.
        name = by_class.get(
            "town",
            "",
        )

        location = by_class.get(
            "location",
            "",
        )

        if not (
            when
            and name
            and location
            and young(
                name + " " + location
            )
        ):
            continue

        venue = re.sub(
            r"^\s*\(\s*Young[^)]*\)\s*",
            "",
            location,
            flags=re.I,
        ).strip()

        found.append({
            "name": name,
            "day": day,
            "time": when,
            "town": name,
            "location": venue,
            "address": north_address(
                location,
                name,
            ),
            "types": by_class.get(
                "type",
                "",
            ),
            "lat": None,
            "lon": None,
            "source": "Northern NJ",
        })

    return found


# ============================================================
# CAPE ATLANTIC
# ============================================================

def cape_rows(markup):
    soup = BeautifulSoup(
        markup,
        "html.parser",
    )

    found = []

    for row in soup.select("tr"):
        c = [
            value
            for value, _ in cells_from_row(row)
        ]

        if len(c) < 10:
            continue

        if c[0] not in DAYS:
            continue

        if not young(c[2]):
            continue

        when = get_time(c[1])

        if not when:
            continue

        try:
            lat = float(c[8])
            lon = float(c[9])

        except (
            ValueError,
            TypeError,
        ):
            lat = None
            lon = None

        if not valid_coords(
            lat,
            lon,
        ):
            lat = None
            lon = None

        found.append({
            "name": c[2],
            "day": c[0],
            "time": when,
            "town": c[6],
            "location": c[3],
            "address": c[4],
            "types": c[7],
            "lat": lat,
            "lon": lon,
            "source": "Cape Atlantic",
        })

    return found


# ============================================================
# DEDUPLICATION
# ============================================================

def meeting_key(meeting):
    return tuple(
        re.sub(
            r"[^a-z0-9]",
            "",
            str(
                meeting.get(
                    field,
                    "",
                )
            ).lower(),
        )
        for field in (
            "source",
            "name",
            "day",
            "time",
            "address",
            "types",
        )
    )


def dedupe(meetings):
    seen = set()
    output = []

    for meeting in meetings:
        key = meeting_key(meeting)

        if key in seen:
            continue

        seen.add(key)
        output.append(meeting)

    return output


# ============================================================
# GEOCODING
# ============================================================

def geocode(
    address,
    town,
    cache,
):
    cache_key = text(
        address
    ).lower()

    cached = cache.get(
        cache_key
    )

    if (
        cached
        and valid_coords(
            cached.get("lat"),
            cached.get("lon"),
        )
    ):
        return cached

    endpoint = (
        "https://geocode.arcgis.com/"
        "arcgis/rest/services/World/"
        "GeocodeServer/"
        "findAddressCandidates"
    )

    try:
        result = SESSION.get(
            endpoint,
            params={
                "f": "json",
                "SingleLine": address,
                "countryCode": "USA",
                "maxLocations": 5,
            },
            timeout=30,
        )

        result.raise_for_status()

        candidates = result.json().get(
            "candidates",
            [],
        )

        for candidate in candidates:
            point = (
                candidate.get("location")
                or {}
            )

            lat = point.get("y")
            lon = point.get("x")

            matched = candidate.get(
                "address",
                "",
            )

            score = candidate.get(
                "score",
                0,
            )

            if not valid_coords(
                lat,
                lon,
            ):
                continue

            if score < 80:
                continue

            if (
                town
                and town.lower()
                not in matched.lower()
            ):
                continue

            entry = {
                "lat": float(lat),
                "lon": float(lon),
                "matched": matched,
                "score": score,
            }

            cache[cache_key] = entry

            time.sleep(0.25)

            return entry

    except (
        requests.RequestException,
        ValueError,
        KeyError,
    ) as exc:
        LOG.warning(
            "Geocode failed for %s: %s",
            address,
            exc,
        )

    return None


# ============================================================
# JSON
# ============================================================

def load_json(
    path,
    fallback,
):
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        ValueError,
    ):
        return fallback


# ============================================================
# TIME SORT
# ============================================================

def sort_time(value):
    match = re.search(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        value,
        re.I,
    )

    if not match:
        return 9999

    hour = int(
        match.group(1)
    )

    minute = int(
        match.group(2)
    )

    am_pm = (
        match.group(3).upper()
    )

    if (
        am_pm == "AM"
        and hour == 12
    ):
        hour = 0

    if (
        am_pm == "PM"
        and hour != 12
    ):
        hour += 12

    return (
        hour * 60
        + minute
    )


# ============================================================
# REFRESH
# ============================================================

def refresh():
    cache = load_json(
        CACHE,
        {},
    )

    old = load_json(
        DATA,
        {},
    )

    old_meetings = old.get(
        "meetings",
        [],
    )

    all_meetings = []
    failures = []


    # --------------------------------------------------------
    # NORTHERN NJ
    # --------------------------------------------------------

    north = []

    for term in (
        "Young people",
        "Young",
    ):
        try:
            markup = fetch(
                NORTH_BASE,
                {
                    "sortby": "Time",
                    "special": term,
                    "url": "",
                },
            )

            result = north_rows(
                markup
            )

            LOG.info(
                "Northern NJ search %r: %d matches",
                term,
                len(result),
            )

            if not result:
                failures.append(
                    "Northern NJ search "
                    f"{term}: zero parsed meetings"
                )

            north.extend(
                result
            )

        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            failures.append(
                "Northern NJ search "
                f"{term}: {exc}"
            )

    north = dedupe(north)

    LOG.info(
        "Northern NJ after deduplication: %d meetings",
        len(north),
    )

    all_meetings.extend(
        north
    )


    # --------------------------------------------------------
    # SOUTH JERSEY — HARDCODED
    # --------------------------------------------------------

    south = [
        dict(meeting)
        for meeting
        in SOUTH_JERSEY_MEETINGS
    ]

    LOG.info(
        "South Jersey: %d hardcoded meetings",
        len(south),
    )

    all_meetings.extend(
        south
    )


    # --------------------------------------------------------
    # CAPE ATLANTIC
    # --------------------------------------------------------

    try:
        markup = fetch(
            CAPE_URL
        )

        cape = cape_rows(
            markup
        )

        LOG.info(
            "Cape Atlantic: %d matches",
            len(cape),
        )

        if not cape:
            failures.append(
                "Cape Atlantic: "
                "zero parsed meetings"
            )

        all_meetings.extend(
            cape
        )

    except (
        requests.RequestException,
        ValueError,
    ) as exc:
        failures.append(
            f"Cape Atlantic: {exc}"
        )


    # --------------------------------------------------------
    # DEDUPLICATE
    # --------------------------------------------------------

    all_meetings = dedupe(
        all_meetings
    )


    # --------------------------------------------------------
    # GEOCODE MISSING COORDINATES
    #
    # South Jersey already has hardcoded coordinates.
    # Northern NJ still uses ArcGIS.
    # --------------------------------------------------------

    for meeting in all_meetings:
        if valid_coords(
            meeting.get("lat"),
            meeting.get("lon"),
        ):
            continue

        address = meeting.get(
            "address",
            "",
        )

        if not address:
            continue

        result = geocode(
            address,
            meeting.get(
                "town",
                "",
            ),
            cache,
        )

        if result:
            meeting["lat"] = (
                result["lat"]
            )

            meeting["lon"] = (
                result["lon"]
            )


    # --------------------------------------------------------
    # SOURCE COUNTS
    # --------------------------------------------------------

    source_counts = {}

    for meeting in all_meetings:
        source = meeting[
            "source"
        ]

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

    LOG.info(
        "Source counts: %s",
        source_counts,
    )


    # --------------------------------------------------------
    # FAIL CLOSED
    # --------------------------------------------------------

    if failures:
        raise RuntimeError(
            "Snapshot NOT replaced: "
            + "; ".join(
                failures
            )
        )

    if not all_meetings:
        raise RuntimeError(
            "Snapshot NOT replaced: "
            "no meetings found"
        )

    if (
        old_meetings
        and len(all_meetings)
        < len(old_meetings) * 0.65
    ):
        raise RuntimeError(
            "Snapshot NOT replaced: "
            "meeting count dropped "
            "by more than 35%"
        )


    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    all_meetings.sort(
        key=lambda m: (
            DAYS.index(
                m["day"]
            ),
            sort_time(
                m["time"]
            ),
            m["name"].lower(),
        )
    )


    # --------------------------------------------------------
    # PUBLISH
    # --------------------------------------------------------

    payload = {
        "updated_at": (
            dt.datetime.now(
                dt.timezone.utc
            ).isoformat()
        ),
        "meetings": all_meetings,
        "source_counts": source_counts,
        "unmapped_count": sum(
            not valid_coords(
                meeting.get("lat"),
                meeting.get("lon"),
            )
            for meeting
            in all_meetings
        ),
    }

    temporary = DATA.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(
        DATA
    )

    CACHE.write_text(
        json.dumps(
            cache,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    LOG.info(
        "Published %d meetings (%d unmapped)",
        len(all_meetings),
        payload["unmapped_count"],
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(levelname)s "
            "%(message)s"
        ),
    )

    try:
        refresh()

    except Exception as exc:
        LOG.error(
            "%s",
            exc,
        )

        sys.exit(1)
