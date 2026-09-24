#!/usr/bin/env python3
"""
NJ Young People AA — GitHub Pages Meeting Updater

Sources:
1. Northern NJ
   - Searches "Young people"
   - Searches "Young"

2. South Jersey
   - First tries https://aasj.org/locations/
   - If GitHub receives 403, automatically falls back to:
     https://aasj.org/meeting-list-print-portrait-2-columns.php

3. Cape Atlantic
   - Uses https://capeatlanticaa.org/locations/

Includes any meeting whose name/designation contains the
complete word "Young".

Fails closed:
A failed source will NOT overwrite the last good meetings.json.
"""

import datetime as dt
import html
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parent

DATA = ROOT / "meetings.json"
CACHE = ROOT / "geocodes.json"


# ============================================================
# SOURCES
# ============================================================

NORTH_BASE = (
    "https://nnjaa.org/intergroup/cgi-bin/list_special.php"
)

SOUTH_LOCATIONS = (
    "https://aasj.org/locations/"
)

SOUTH_PRINT = (
    "https://aasj.org/"
    "meeting-list-print-portrait-2-columns.php"
)

CAPE_URL = (
    "https://capeatlanticaa.org/locations/"
)


# ============================================================
# DAYS
# ============================================================

DAYS = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)


# ============================================================
# LOGGING
# ============================================================

LOG = logging.getLogger("meetings")


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
        "Version/18.0 "
        "Mobile/15E148 "
        "Safari/604.1"
    ),
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
})


# ============================================================
# HELPERS
# ============================================================

def text(value):
    return re.sub(
        r"\s+",
        " ",
        html.unescape(
            str(value or "")
        ),
    ).strip()


def young(value):
    """
    Match Young as a complete word.

    Matches:
    Young People
    Young Men
    Young at Heart

    Does not match:
    Younger
    Youngstown
    """

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
            and
            -75.8 <= lon <= -73.7
        )

    except (
        TypeError,
        ValueError,
    ):
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

def fetch(
    url,
    params=None,
    retries=3,
):

    host = (
        urlparse(url)
        .netloc
        .lower()
    )

    headers = {}

    if host.endswith("aasj.org"):

        headers.update({
            "Referer": "https://aasj.org/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        })

    last_error = None

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                headers=headers,
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
                    "Source returned "
                    "an empty response"
                )

            return response.text

        except (
            requests.RequestException,
            ValueError,
        ) as exc:

            last_error = exc

            if attempt < retries:

                LOG.warning(
                    "Request failed for %s "
                    "(attempt %d/%d): %s",
                    url,
                    attempt,
                    retries,
                    exc,
                )

                time.sleep(
                    2 * attempt
                )

    raise (
        last_error
        or RuntimeError(
            "Unable to download "
            + url
        )
    )


# ============================================================
# TABLE HELPERS
# ============================================================

def cells_from_row(row):

    cells = row.find_all(
        ["td", "th"],
        recursive=False,
    )

    output = []

    for cell in cells:

        value = text(
            cell.get_text(
                " ",
                strip=True,
            )
        )

        classes = " ".join(
            cell.get(
                "class",
                [],
            )
        )

        output.append(
            (
                value,
                classes,
            )
        )

    return output


# ============================================================
# NORTHERN NJ
# ============================================================

def north_address(
    location,
    town,
):

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

    cleaned = text(
        cleaned
    )

    match = re.search(
        r"\b\d{1,6}"
        r"(?:-\d{1,6})?"
        r"\s+.+",
        cleaned,
    )

    if match:
        street = (
            match.group(0)
            .strip()
        )

    else:
        street = cleaned

    return (
        f"{street}, "
        f"{town}, NJ"
    )


def north_rows(markup):

    soup = BeautifulSoup(
        markup,
        "html.parser",
    )

    found = []

    for row in soup.select("tr"):

        cells = cells_from_row(
            row
        )

        by_class = {}

        for value, classes in cells:

            for cls in classes.split():

                by_class[cls] = value

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

        name = by_class.get(
            "town",
            "",
        )

        location = by_class.get(
            "location",
            "",
        )

        types = by_class.get(
            "type",
            "",
        )

        if not (
            day
            and when
            and name
            and location
        ):
            continue

        if not young(
            name + " " + location
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
            "types": types,
            "lat": None,
            "lon": None,
            "source": "Northern NJ",
        })

    return found


# ============================================================
# STANDARD 10-COLUMN LOCATIONS TABLE
#
# Used by:
# - South Jersey /locations/
# - Cape Atlantic /locations/
# ============================================================

def location_rows(
    markup,
    source,
):

    soup = BeautifulSoup(
        markup,
        "html.parser",
    )

    found = []

    for row in soup.select("tr"):

        cells = [
            value
            for value, _ in
            cells_from_row(row)
        ]

        if len(cells) < 10:
            continue

        day = cells[0]

        if day not in DAYS:
            continue

        when = get_time(
            cells[1]
        )

        name = cells[2]

        if not when:
            continue

        if not young(name):
            continue

        try:

            lat = float(
                cells[8]
            )

            lon = float(
                cells[9]
            )

        except (
            TypeError,
            ValueError,
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
            "name": name,
            "day": day,
            "time": when,
            "town": cells[6],
            "location": cells[3],
            "address": cells[4],
            "types": cells[7],
            "lat": lat,
            "lon": lon,
            "source": source,
        })

    return found


# ============================================================
# SOUTH JERSEY PRINTABLE FALLBACK
# ============================================================

def south_print_rows(markup):

    soup = BeautifulSoup(
        markup,
        "html.parser",
    )

    found = []

    current_day = ""

    # The printable page is organized by day.
    #
    # Each physical meeting appears in the order:
    #
    # TIME
    # TYPES
    # TOWN + MEETING NAME [link]
    # VENUE
    # STREET ADDRESS
    #
    # We process the visible page line-by-line.

    page_text = soup.get_text(
        "\n",
        strip=True,
    )

    lines = [
        text(line)
        for line
        in page_text.splitlines()
        if text(line)
    ]

    i = 0

    while i < len(lines):

        line = lines[i]

        # ------------------------------------------
        # DAY
        # ------------------------------------------

        if line in DAYS:

            current_day = line
            i += 1
            continue

        # ------------------------------------------
        # TIME
        # ------------------------------------------

        when = get_time(line)

        if (
            not current_day
            or not when
            or line.upper() != when
        ):

            i += 1
            continue

        # We expect at least:
        #
        # time
        # types
        # meeting line
        # venue
        # address

        if i + 4 >= len(lines):

            i += 1
            continue

        types = lines[
            i + 1
        ]

        meeting_line = lines[
            i + 2
        ]

        venue = lines[
            i + 3
        ]

        street = lines[
            i + 4
        ]

        # Remove printable-page link marker.

        meeting_line = re.sub(
            r"\s*\[link\]\s*$",
            "",
            meeting_line,
            flags=re.I,
        ).strip()

        if not young(
            meeting_line
        ):

            i += 1
            continue

        # ------------------------------------------
        # DETERMINE TOWN
        #
        # The printable list places town before the
        # meeting name.
        #
        # We can derive the actual town from the
        # street/venue using geocoding, so we do
        # NOT maintain a hard-coded list of towns.
        # ------------------------------------------

        town = ""

        # Many meeting names themselves contain a
        # recognizable "of TOWN" ending.

        of_match = re.search(
            r"\bof\s+"
            r"([A-Za-z][A-Za-z .'-]+)$",
            meeting_line,
            re.I,
        )

        if of_match:

            possible_town = text(
                of_match.group(1)
            )

            # If the printable line begins with the
            # same town, separate it.

            if meeting_line.lower().startswith(
                possible_town.lower()
                + " "
            ):

                town = possible_town

                name = meeting_line[
                    len(possible_town):
                ].strip()

            else:

                name = meeting_line

        else:

            name = meeting_line

        # ------------------------------------------
        # ADDRESS
        #
        # Printable page provides the street but
        # not always city/state on the same line.
        #
        # If town wasn't determinable yet, ArcGIS
        # will geocode using venue + street + NJ.
        # ------------------------------------------

        if town:

            address = (
                f"{street}, "
                f"{town}, NJ"
            )

        else:

            address = (
                f"{venue}, "
                f"{street}, NJ"
            )

        found.append({
            "name": name,
            "day": current_day,
            "time": when,
            "town": town,
            "location": venue,
            "address": address,
            "types": types,
            "lat": None,
            "lon": None,
            "source": "South Jersey",
            "_print_name": meeting_line,
        })

        i += 5

    return found


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
        "arcgis/rest/services/"
        "World/GeocodeServer/"
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

        candidates = (
            result.json()
            .get(
                "candidates",
                [],
            )
        )

        for candidate in candidates:

            point = (
                candidate.get(
                    "location"
                )
                or {}
            )

            lat = point.get("y")
            lon = point.get("x")

            matched = str(
                candidate.get(
                    "address",
                    "",
                )
            )

            score = float(
                candidate.get(
                    "score",
                    0,
                )
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

            cache[
                cache_key
            ] = entry

            time.sleep(
                0.25
            )

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
# SOUTH JERSEY FALLBACK CLEANUP
# ============================================================

def finish_south_fallback(
    meetings,
    cache,
):

    output = []

    for meeting in meetings:

        result = geocode(
            meeting["address"],
            meeting["town"],
            cache,
        )

        if not result:

            LOG.warning(
                "South Jersey fallback "
                "could not geocode: %s",
                meeting["address"],
            )

            output.append(
                meeting
            )

            continue

        meeting["lat"] = (
            result["lat"]
        )

        meeting["lon"] = (
            result["lon"]
        )

        matched = result.get(
            "matched",
            "",
        )

        # ------------------------------------------
        # Recover town from ArcGIS if the printable
        # page did not allow us to separate it.
        # ------------------------------------------

        if not meeting["town"]:

            # Typical ArcGIS result:
            #
            # 29 Warwick Rd,
            # Haddonfield,
            # New Jersey, 08033

            parts = [
                text(part)
                for part
                in matched.split(",")
                if text(part)
            ]

            if len(parts) >= 2:

                town = parts[1]

                meeting["town"] = town

                printable = (
                    meeting.get(
                        "_print_name",
                        meeting["name"],
                    )
                )

                # Printable list starts:
                #
                # Haddonfield Cherry Hill Young People
                #
                # Merchantville Young Men of Merchantville

                if printable.lower().startswith(
                    town.lower()
                    + " "
                ):

                    meeting["name"] = (
                        printable[
                            len(town):
                        ]
                        .strip()
                    )

                # Replace temporary geocoding address
                # with a clean user-facing address.

                street_match = re.search(
                    r"(\d[^,]+)",
                    meeting["address"],
                )

                if street_match:

                    meeting["address"] = (
                        street_match
                        .group(1)
                        .strip()
                        + ", "
                        + town
                        + ", NJ"
                    )

        meeting.pop(
            "_print_name",
            None,
        )

        output.append(
            meeting
        )

    return output


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

        key = meeting_key(
            meeting
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        output.append(
            meeting
        )

    return output


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
        r"(\d{1,2}):"
        r"(\d{2})\s*"
        r"(AM|PM)",
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
        match.group(3)
        .upper()
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

    # ========================================================
    # NORTHERN NJ
    # ========================================================

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
                "Northern NJ search %r: "
                "%d matches",
                term,
                len(result),
            )

            if not result:

                failures.append(
                    "Northern NJ "
                    f"{term}: "
                    "zero matches"
                )

            north.extend(
                result
            )

        except Exception as exc:

            failures.append(
                "Northern NJ "
                f"{term}: "
                f"{exc}"
            )

    north = dedupe(
        north
    )

    all_meetings.extend(
        north
    )

    # ========================================================
    # SOUTH JERSEY
    #
    # Try /locations/ first.
    #
    # GitHub Actions currently receives HTTP 403
    # from this endpoint.
    #
    # If it fails, use the official printable
    # AASJ meeting list.
    # ========================================================

    south = []

    try:

        markup = fetch(
            SOUTH_LOCATIONS
        )

        south = location_rows(
            markup,
            "South Jersey",
        )

        LOG.info(
            "South Jersey /locations/: "
            "%d matches",
            len(south),
        )

    except Exception as exc:

        LOG.warning(
            "South Jersey /locations/ "
            "unavailable: %s",
            exc,
        )

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    if not south:

        LOG.info(
            "Using South Jersey "
            "printable-list fallback..."
        )

        try:

            markup = fetch(
                SOUTH_PRINT
            )

            south = south_print_rows(
                markup
            )

            LOG.info(
                "South Jersey printable "
                "raw matches: %d",
                len(south),
            )

            south = (
                finish_south_fallback(
                    south,
                    cache,
                )
            )

            LOG.info(
                "South Jersey printable "
                "processed matches: %d",
                len(south),
            )

        except Exception as exc:

            failures.append(
                "South Jersey fallback: "
                + str(exc)
            )

    if not south:

        failures.append(
            "South Jersey: "
            "zero Young meetings parsed"
        )

    all_meetings.extend(
        south
    )

    # ========================================================
    # CAPE ATLANTIC
    # ========================================================

    try:

        markup = fetch(
            CAPE_URL
        )

        cape = location_rows(
            markup,
            "Cape Atlantic",
        )

        LOG.info(
            "Cape Atlantic: "
            "%d matches",
            len(cape),
        )

        if not cape:

            failures.append(
                "Cape Atlantic: "
                "zero Young meetings parsed"
            )

        all_meetings.extend(
            cape
        )

    except Exception as exc:

        failures.append(
            "Cape Atlantic: "
            + str(exc)
        )

    # ========================================================
    # DEDUPLICATE
    # ========================================================

    all_meetings = dedupe(
        all_meetings
    )

    # ========================================================
    # GEOCODE ANY MISSING COORDINATES
    #
    # This covers:
    # - Northern NJ
    # - South Jersey printable fallback
    # ========================================================

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

    # ========================================================
    # SOURCE COUNTS
    # ========================================================

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

    # ========================================================
    # FAIL CLOSED
    # ========================================================

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
        <
        len(old_meetings) * 0.65
    ):

        raise RuntimeError(
            "Snapshot NOT replaced: "
            "meeting count dropped "
            "by more than 35%"
        )

    # ========================================================
    # SORT
    # ========================================================

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

    # ========================================================
    # REMOVE INTERNAL FIELDS
    # ========================================================

    for meeting in all_meetings:

        for internal in (
            "_print_name",
        ):

            meeting.pop(
                internal,
                None,
            )

    # ========================================================
    # PUBLISH
    # ========================================================

    payload = {

        "updated_at": (
            dt.datetime.now(
                dt.timezone.utc
            ).isoformat()
        ),

        "meetings": (
            all_meetings
        ),

        "source_counts": (
            source_counts
        ),

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
        "Published %d meetings "
        "(%d unmapped)",
        len(all_meetings),
        payload[
            "unmapped_count"
        ],
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
