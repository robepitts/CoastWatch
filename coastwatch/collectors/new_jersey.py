"""New Jersey DEP Cooperative Coastal Monitoring Program (CCMP).  EXPERIMENTAL.

Findings (Oct 2026):
  * https://njbeaches.org/ is a Next.js app hosted by Rutgers for NJDEP
    (a dev build lives at njbeaches.njdep.rutgers.edu). ~195 ocean + 25 bay
    stations, sampled Mondays mid-May to mid-September; results posted
    Tuesday afternoon. Standard: 104 CFU/100 mL single sample, 30 geomean.
  * No documented public API. The page shell contains no data in static
    HTML text, so data is either embedded as Next.js page props
    (__NEXT_DATA__ / RSC payload) or fetched from an internal JSON API.

Strategy here: pull the page, extract any embedded JSON, and walk it for
arrays of station-like objects (have lat/lon + a name). This works if the
data is server-rendered into the page.

TODO (highest-value next step for NJ): open njbeaches.org with DevTools >
Network > Fetch/XHR, click a station, and record the JSON endpoint(s).
Then set `api_url` in config/sources.json; parse_station_records() already
handles flat station/sample objects.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from ..models import (CollectorResult, Indicator, Sample, Site, StatusReport,
                      normalize_status)
from .base import Collector, find_key, parse_date, parse_result, slug

HOME = "https://njbeaches.org/"


class NewJerseyCollector(Collector):
    source_id = "nj_dep"
    state = "NJ"
    agency = "NJDEP Cooperative Coastal Monitoring Program"
    homepage_url = HOME

    def __init__(self, api_url: Optional[str] = None, **kw):
        super().__init__(**kw)
        self.api_url = api_url

    def collect(self) -> CollectorResult:
        if self.api_url:
            return parse_station_records(_records_from_json(self.get(self.api_url).json()))
        records = extract_embedded_records(self.get(HOME).text)
        res = parse_station_records(records)
        if not res.sites:
            res.warnings.append(
                "NJ: no station data embedded in page; discover the JSON API "
                "(see module docstring) and set api_url in config/sources.json")
        return res


def extract_embedded_records(html: str) -> list[dict]:
    blobs: list[Any] = []
    soup = BeautifulSoup(html, "lxml")
    nd = soup.find("script", id="__NEXT_DATA__")
    if nd and nd.string:
        try:
            blobs.append(json.loads(nd.string))
        except json.JSONDecodeError:
            pass
    for s in soup.find_all("script", type="application/json"):
        if s is nd:
            continue
        try:
            blobs.append(json.loads(s.string or ""))
        except json.JSONDecodeError:
            pass
    # App Router RSC payloads: self.__next_f.push([1,"...json..."])
    for chunk in re.findall(r'self\.__next_f\.push\(\[\d+,"(.*?)"\]\)', html, re.S):
        try:
            text = json.loads(f'"{chunk}"')
        except json.JSONDecodeError:
            continue
        for m in re.finditer(r"\[\{.*?\}\]", text, re.S):
            try:
                blobs.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                continue
    # Keep the single largest station list (the same data is often embedded twice).
    return max((_records_from_json(b) for b in blobs), key=len, default=[])


def _is_station(d: dict) -> bool:
    return bool(find_key(d, "latitude", "lat") and find_key(d, "longitude", "lng", "lon")
                and find_key(d, "name", "beach", "station", "site"))


def _records_from_json(obj: Any) -> list[dict]:
    """Find the largest list of station-like dicts anywhere in a JSON tree."""
    best: list[dict] = []
    def walk(o):
        nonlocal best
        if isinstance(o, list):
            dicts = [x for x in o if isinstance(x, dict)]
            if dicts and sum(_is_station(x) for x in dicts) >= max(1, len(dicts) // 2):
                if len(dicts) > len(best):
                    best = dicts
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
    walk(obj)
    return best


def parse_station_records(records: list[dict]) -> CollectorResult:
    res = CollectorResult()
    for r in records:
        id_k = find_key(r, "station id", "stationid", "station code", "site id", "id")
        name_k = find_key(r, "beach name", "station name", "name", "beach", "site")
        lat_k, lon_k = find_key(r, "latitude", "lat"), find_key(r, "longitude", "lng", "lon")
        name = str(r.get(name_k) or "").strip()
        sid = str(r.get(id_k)) if id_k and r.get(id_k) not in (None, "") else slug(name)
        if not sid:
            continue
        county_k = find_key(r, "county", "municipality")
        try:
            lat, lon = float(r[lat_k]), float(r[lon_k])
        except (TypeError, ValueError, KeyError):
            lat = lon = None
        res.sites.append(Site(source_site_id=sid, name=name or sid, state="NJ",
                              latitude=lat, longitude=lon, water_type="marine",
                              county=str(r[county_k]) if county_k and r.get(county_k) else None,
                              detail_url=HOME, raw=r))
        st_k = find_key(r, "status", "beach status", "condition", "advisory")
        if st_k and r.get(st_k):
            res.statuses.append(StatusReport(sid, normalize_status(str(r[st_k])), str(r[st_k])))
        d_k = find_key(r, "sample date", "last sample", "collection date", "date")
        v_k = find_key(r, "enterococci", "enterococcus", "result", "value", "count")
        d = parse_date(r.get(d_k)) if d_k else None
        if d and v_k and r.get(v_k) not in (None, ""):
            value, qual = parse_result(r[v_k])
            res.samples.append(Sample(source_site_id=sid, sample_date=d,
                                      indicator=Indicator.ENTEROCOCCUS, value=value,
                                      qualifier=qual, raw=r))
    return res
