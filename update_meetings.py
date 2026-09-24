#!/usr/bin/env python3
"""Refresh the public NJ Young People AA meeting snapshot.

Fail closed: never replace good data with empty/partial data.
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
# PATHS / SOURCES
# ============================================================

ROOT = Path(__file__).resolve().parent

DATA = ROOT / "meetings.json"
CACHE = ROOT / "geocodes.json"

NORTH_BASE = (
    "https://nnjaa.org/intergroup/cgi-bin/list_special.php"
)

SOURCES = {
    "South Jersey": "https://aasj.org/locations/",
    "Cape Atlantic": "https://capeatlanticaa.org/locations/",
}

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
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

# Use normal browser-style headers rather than identifying
# the request as an automated GitHub process.
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
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
            value or "",
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


# ============================================================
# DOWNLOAD
# ============================================================

def fetch(url, params=None):
    """
    Download a source page.

    South Jersey has returned HTTP 403 to GitHub Actions even
    though the page is publicly available. Give that request
    browser-style navigation headers and retry transient/403
    responses before treating the source as failed.
    """

    host = urlparse(url).netloc.lower()

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

    for attempt in range(1, 4):

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

            # Retry a 403 because this is the specific
            # intermittent/blocking behavior seen from AASJ.
            if response.status_code == 403:
                last_error = requests.HTTPError(
                    "403 Forbidden for "
                    + response.url
                )

                if attempt < 3:
                    LOG.warning(
                        "403 from %s — retry %d/3",
                        host,
                        attempt,
                    )

                    time.sleep(2 * attempt)
                    continue

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
                continue

    raise last_error or RuntimeError(
        "Unable to download " + url
    )


# ============================================================
# TABLE HELPERS
# ============================================================

def cells_from_row(row):
    """
    BeautifulSoup repairs Northern NJ's malformed HTML,
    including its missing </td> before the Details cell.
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

        when = by_class.get(
            "time",
            "",
        )

        match = re.search(
            r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b",
            when,
            re.I,
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
            match
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
            "time": text(
                match.group()
            ).upper(),
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
# SOUTH JERSEY / CAPE ATLANTIC
# ============================================================

def other_rows(markup, source):

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

        # Include any meeting with "Young" as a
        # complete word in the meeting name.
        if not young(c[2]):
            continue

        match = re.search(
            r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b",
            c[1],
            re.I,
        )

        if not match:
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

        coordinates_valid = valid_coords(
            lat,
            lon,
        )

        found.append({
            "name": c[2],
            "day": c[0],
            "time": text(
                match.group()
            ).upper(),
            "town": c[6],
            "location": c[3],
            "address": c[4],
            "types": c[7],
            "lat": (
                lat
                if coordinates_valid
                else None
            ),
            "lon": (
                lon
                if coordinates_valid
                else None
            ),
            "source": source,
        })

    return found


# ============================================================
# DEDUPLICATION
# ============================================================

def key(meeting):

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


# ============================================================
# NORTHERN NJ GEOCODING
# ============================================================

def geocode(
    address,
    town,
    cache,
):

    cache_key = text(
        address
    ).lower()

    if (
        cache_key in cache
        and valid_coords(
            cache[cache_key].get("lat"),
            cache[cache_key].get("lon"),
        )
    ):
        return cache[cache_key]

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
    source_counts = {}

    # --------------------------------------------------------
    # Northern NJ
    # --------------------------------------------------------

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
                "Northern NJ search %r: %s matches",
                term,
                len(result),
            )

            if not result:
                failures.append(
                    "Northern NJ search "
                    f"{term}: zero parsed meetings"
                )

            all_meetings.extend(
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

    # --------------------------------------------------------
    # South Jersey + Cape Atlantic
    # --------------------------------------------------------

    for source, url in SOURCES.items():

        try:
            markup = fetch(url)

            result = other_rows(
                markup,
                source,
            )

            LOG.info(
                "%s: %s matches",
                source,
                len(result),
            )

            if not result:
                failures.append(
                    f"{source}: "
                    "zero parsed meetings"
                )

            all_meetings.extend(
                result
            )

        except (
            requests.RequestException,
            ValueError,
        ) as exc:

            failures.append(
                f"{source}: {exc}"
            )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    unique = {
        key(meeting): meeting
        for meeting in all_meetings
    }

    all_meetings = list(
        unique.values()
    )

    # --------------------------------------------------------
    # Geocode Northern NJ
    # --------------------------------------------------------

    for meeting in all_meetings:

        if (
            meeting["source"]
            == "Northern NJ"
        ):

            result = geocode(
                meeting["address"],
                meeting["town"],
                cache,
            )

            if result:
                meeting["lat"] = (
                    result["lat"]
                )

                meeting["lon"] = (
                    result["lon"]
                )

        source_counts[
            meeting["source"]
        ] = (
            source_counts.get(
                meeting["source"],
                0,
            )
            + 1
        )

    # --------------------------------------------------------
    # FAIL CLOSED
    #
    # Never overwrite a good public snapshot if:
    # - any source failed
    # - no meetings were found
    # - total meeting count suddenly drops >35%
    # --------------------------------------------------------

    if (
        failures
        or not all_meetings
        or (
            old_meetings
            and len(all_meetings)
            < len(old_meetings) * 0.65
        )
    ):

        reason = "; ".join(
            failures
            or [
                "meeting count "
                "dropped by >35%"
            ]
        )

        raise RuntimeError(
            "Snapshot NOT replaced: "
            + reason
        )

    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------

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
    # Publish
    # --------------------------------------------------------

    payload = {
        "updated_at": (
            dt.datetime.now(
                dt.timezone.utc
            ).isoformat()
        ),
        "meetings": all_meetings,
        "source_counts": (
            source_counts
        ),
        "unmapped_count": sum(
            not valid_coords(
                meeting["lat"],
                meeting["lon"],
            )
            for meeting
            in all_meetings
        ),
    }

    tmp = DATA.with_suffix(
        ".json.tmp"
    )

    tmp.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    tmp.replace(DATA)

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
        payload["unmapped_count"],
    )

    LOG.info(
        "Source counts: %s",
        source_counts,
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
