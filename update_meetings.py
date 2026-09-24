#!/usr/bin/env python3
"""Refresh the public meeting snapshot. Fail closed: never replace good data with empty/partial data."""
import argparse
import datetime as dt
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'meetings.json'
CACHE = ROOT / 'geocodes.json'
BASE = 'https://nnjaa.org/intergroup/cgi-bin/list_special.php'
SOURCES = {'South Jersey': 'https://aasj.org/locations/', 'Cape Atlantic': 'https://capeatlanticaa.org/locations/'}
DAYS = ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
LOG = logging.getLogger('meetings')
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NJYoungPeopleAAMap/1.0 (public meeting directory; contact repository maintainer)', 'Accept': 'text/html,application/json'})


def text(value):
    return re.sub(r'\s+', ' ', html.unescape(str(value or ''))).strip()


def young(value):
    return bool(re.search(r'\byoung\b', value or '', re.I))


def valid_coords(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
        return 38.7 <= lat <= 41.5 and -75.8 <= lon <= -73.7
    except (TypeError, ValueError):
        return False


def fetch(url, params=None):
    response = SESSION.get(url, params=params, timeout=35)
    response.raise_for_status()
    return response.text


def cells_from_row(row):
    # BeautifulSoup repairs Northern NJ's missing </td> before the Details cell.
    cells = row.find_all(['td', 'th'], recursive=False)
    return [(text(' '.join(str(node) for node in c.contents if getattr(node, 'name', None) not in ('td', 'th')) if c.find(['td', 'th'], recursive=False) else c.get_text(' ', strip=True)), ' '.join(c.get('class', []))) for c in cells]


def north_address(location, town):
    cleaned = re.sub(r'^\s*\(\s*Young[^)]*\)\s*', '', location, flags=re.I)
    cleaned = re.sub(r'\([^)]*\)', ' ', cleaned)
    cleaned = text(cleaned)
    match = re.search(r'\b\d{1,6}(?:-\d{1,6})?\s+.+', cleaned)
    street = match.group(0).strip() if match else cleaned
    return f'{street}, {town}, NJ'


def north_rows(markup):
    soup = BeautifulSoup(markup, 'html.parser')
    found = []
    for row in soup.select('tr'):
        cells = cells_from_row(row)
        by_class = {cls: value for value, classes in cells for cls in classes.split()}
        day = by_class.get('day', '')
        if day not in DAYS:
            continue
        when = by_class.get('time', '')
        match = re.search(r'\b\d{1,2}:\d{2}\s*(?:AM|PM)\b', when, re.I)
        name = by_class.get('town', '')
        location = by_class.get('location', '')
        if not (match and name and location and young(name + ' ' + location)):
            continue
        # Third column (Town) is the Northern NJ title, not the Type column.
        venue = re.sub(r'^\s*\(\s*Young[^)]*\)\s*', '', location, flags=re.I).strip()
        found.append(dict(name=name, day=day, time=text(match.group()).upper(), town=name,
                          location=venue, address=north_address(location, name),
                          types=by_class.get('type', ''), lat=None, lon=None, source='Northern NJ'))
    return found


def other_rows(markup, source):
    # Preserve the V10 ten-column interpretation; reject invalid coordinates rather
    # than pretending a venue has been geocoded. Some sites may change their markup.
    soup = BeautifulSoup(markup, 'html.parser')
    found = []
    for row in soup.select('tr'):
        c = [value for value, _ in cells_from_row(row)]
        if len(c) < 10 or c[0] not in DAYS or not young(c[2]):
            continue
        match = re.search(r'\b\d{1,2}:\d{2}\s*(?:AM|PM)\b', c[1], re.I)
        if not match:
            continue
        try:
            lat, lon = float(c[8]), float(c[9])
        except (ValueError, TypeError):
            lat = lon = None
        found.append(dict(name=c[2], day=c[0], time=text(match.group()).upper(),
                          town=c[6], location=c[3], address=c[4], types=c[7],
                          lat=lat if valid_coords(lat, lon) else None,
                          lon=lon if valid_coords(lat, lon) else None, source=source))
    return found


def key(meeting):
    return tuple(re.sub(r'[^a-z0-9]', '', str(meeting.get(field, '')).lower())
                 for field in ('source', 'name', 'day', 'time', 'address', 'types'))


def geocode(address, town, cache):
    cache_key = text(address).lower()
    if cache_key in cache and valid_coords(cache[cache_key].get('lat'), cache[cache_key].get('lon')):
        return cache[cache_key]
    endpoint = 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates'
    try:
        result = SESSION.get(endpoint, params={'f': 'json', 'SingleLine': address,
            'countryCode': 'USA', 'maxLocations': 5}, timeout=30)
        result.raise_for_status()
        candidates = result.json().get('candidates', [])
        for candidate in candidates:
            point = candidate.get('location') or {}
            lat, lon = point.get('y'), point.get('x')
            matched = candidate.get('address', '')
            if valid_coords(lat, lon) and candidate.get('score', 0) >= 80 and town.lower() in matched.lower():
                entry = {'lat': float(lat), 'lon': float(lon), 'matched': matched, 'score': candidate['score']}
                cache[cache_key] = entry
                time.sleep(0.25)
                return entry
    except (requests.RequestException, ValueError, KeyError) as exc:
        LOG.warning('Geocode failed for %s: %s', address, exc)
    return None


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


def refresh():
    cache = load_json(CACHE, {})
    old = load_json(DATA, {})
    old_meetings = old.get('meetings', [])
    all_meetings = []
    failures = []
    source_counts = {}
    for term in ('Young people', 'Young'):
        try:
            markup = fetch(BASE, {'sortby': 'Time', 'special': term, 'url': ''})
            result = north_rows(markup)
            LOG.info('Northern NJ search %r: %s matches', term, len(result))
            if not result:
                failures.append(f'Northern NJ search {term}: zero parsed meetings')
            all_meetings.extend(result)
        except (requests.RequestException, ValueError) as exc:
            failures.append(f'Northern NJ search {term}: {exc}')
    for source, url in SOURCES.items():
        try:
            result = other_rows(fetch(url), source)
            LOG.info('%s: %s matches', source, len(result))
            if not result:
                failures.append(f'{source}: zero parsed meetings')
            all_meetings.extend(result)
        except (requests.RequestException, ValueError) as exc:
            failures.append(f'{source}: {exc}')
    unique = {key(m): m for m in all_meetings}
    all_meetings = list(unique.values())
    for meeting in all_meetings:
        if meeting['source'] == 'Northern NJ':
            result = geocode(meeting['address'], meeting['town'], cache)
            if result:
                meeting['lat'], meeting['lon'] = result['lat'], result['lon']
        source_counts[meeting['source']] = source_counts.get(meeting['source'], 0) + 1
    # A source failure or unexpectedly low count must not erase the public snapshot.
    # The initial deployment must be seeded from a successful run, not sample meetings.
    if failures or not all_meetings or (old_meetings and len(all_meetings) < len(old_meetings) * 0.65):
        raise RuntimeError('Snapshot NOT replaced: ' + '; '.join(failures or ['meeting count dropped by >35%']))
    all_meetings.sort(key=lambda m: (DAYS.index(m['day']), m['time'], m['name']))
    payload = {'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
               'meetings': all_meetings, 'source_counts': source_counts,
               'unmapped_count': sum(not valid_coords(m['lat'], m['lon']) for m in all_meetings)}
    tmp = DATA.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(DATA)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    LOG.info('Published %d meetings (%d unmapped)', len(all_meetings), payload['unmapped_count'])


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        refresh()
    except Exception as exc:
        LOG.error('%s', exc)
        sys.exit(1)
