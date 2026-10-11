"""Water Quality Portal (waterqualitydata.us) collector.

The national portal holds monitoring results that agencies submit to EPA's
Water Quality Exchange (WQX), including BEACH Act beach monitoring. It has a
public, documented download service, so it can fill gaps where a state's own
website can't be read (NJ, DE) or publishes nothing off-season (NY State).

IMPORTANT: portal data is DELAYED. Most agencies submit beach results
periodically, often annually, so results may be months old. The website labels
these sources as "latest reported results", not current conditions.

Queries are filtered by state, site type (Ocean, Estuary) and Enterococcus, so
beach stations are found whichever agency account submitted them. Agencies
already covered by a direct source (e.g. NYC Health) are excluded by name.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from collections import Counter
from datetime import date
from typing import Optional

from ..models import CollectorResult, Indicator, Sample, Site
from .base import Collector, clean_text, find_key, nice_name, parse_date, parse_result

log = logging.getLogger(__name__)

BASE = "https://www.waterqualitydata.us/data"
FIPS = {"NJ": "US:34", "NY": "US:36", "DE": "US:10", "MD": "US:24", "VA": "US:51"}
COUNTY = {  # coastal counties only; others fall back to blank
    "NJ": {"001": "Atlantic", "005": "Burlington", "009": "Cape May", "011": "Cumberland",
           "015": "Gloucester", "017": "Hudson", "023": "Middlesex", "025": "Monmouth",
           "029": "Ocean", "033": "Salem", "039": "Union"},
    "NY": {"005": "Bronx", "047": "Kings", "059": "Nassau", "061": "New York", "081": "Queens",
           "085": "Richmond", "087": "Rockland", "103": "Suffolk", "119": "Westchester"},
    "DE": {"001": "Kent", "003": "New Castle", "005": "Sussex"},
}


class WQPCollector(Collector):
    def __init__(self, source_id: str, state: str, agency: str, homepage_url: str = "",
                 site_types: Optional[list[str]] = None, characteristics: Optional[list[str]] = None,
                 years_back: int = 2, exclude_org_pattern: Optional[str] = None,
                 timeout_s: float = 300, **kw):
        super().__init__(**kw)
        self.source_id, self.state, self.agency, self.homepage_url = source_id, state, agency, homepage_url
        self.site_types = site_types or ["Ocean", "Estuary"]
        self.characteristics = characteristics or ["Enterococcus", "Enterococci"]
        self.years_back = years_back
        self.exclude = re.compile(exclude_org_pattern, re.I) if exclude_org_pattern else None
        self.timeout_s = timeout_s

    def _params(self) -> list[tuple[str, str]]:
        start = date(date.today().year - self.years_back + 1, 1, 1)
        p = [("statecode", FIPS[self.state]), ("startDateLo", start.strftime("%m-%d-%Y")),
             ("mimeType", "csv"), ("zip", "no")]
        p += [("siteType", t) for t in self.site_types]
        p += [("characteristicName", c) for c in self.characteristics]
        return p

    def _csv(self, endpoint: str) -> list[dict]:
        url = f"{BASE}/{endpoint}/search"
        log.info("%s: downloading %s (this can take a few minutes)", self.source_id, endpoint)
        resp = self.get(url, params=self._params(), timeout=self.timeout_s)
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        log.info("%s: %s returned %d rows", self.source_id, endpoint, len(rows))
        return rows

    def collect(self) -> CollectorResult:
        stations = self._csv("Station")
        results = self._csv("Result") if stations else []
        res = parse_wqp(stations, results, self.state, self.exclude, self.homepage_url)
        orgs = Counter(s.raw.get("org_name") or s.raw.get("org") for s in res.sites)
        log.info("%s: %d stations kept from %d agencies: %s", self.source_id, len(res.sites),
                 len(orgs), "; ".join(f"{k} ({v})" for k, v in orgs.most_common(8)))
        if not res.sites:
            res.warnings.append(f"{self.source_id}: no beach stations with Enterococcus results found")
        return res


def parse_wqp(stations: list[dict], results: list[dict], state: str,
              exclude: Optional[re.Pattern] = None, detail_url: str = "") -> CollectorResult:
    res = CollectorResult()
    kept: dict[str, str] = {}   # station id -> org
    for st in stations:
        id_k = find_key(st, "MonitoringLocationIdentifier")
        sid = clean_text(st.get(id_k)) if id_k else None
        if not sid:
            continue
        org = clean_text(st.get(find_key(st, "OrganizationIdentifier") or "")) or ""
        org_name = clean_text(st.get(find_key(st, "OrganizationFormalName") or "")) or ""
        if exclude and (exclude.search(org) or exclude.search(org_name)):
            continue
        try:
            lat = float(st.get(find_key(st, "LatitudeMeasure") or "") or "nan")
            lon = float(st.get(find_key(st, "LongitudeMeasure") or "") or "nan")
        except ValueError:
            lat = lon = float("nan")
        if lat != lat or lon != lon:   # NaN: unusable location
            continue
        name = clean_text(st.get(find_key(st, "MonitoringLocationName") or "")) or sid
        desc = clean_text(st.get(find_key(st, "MonitoringLocationDescriptionText") or ""))
        typ = clean_text(st.get(find_key(st, "MonitoringLocationTypeName") or "")) or ""
        county = COUNTY.get(state, {}).get(str(st.get(find_key(st, "CountyCode") or "") or "").zfill(3))
        res.sites.append(Site(
            source_site_id=sid, name=nice_name(name), state=state, latitude=lat, longitude=lon,
            county=county, water_body="Estuary or bay" if "estuar" in typ.lower() else
            ("Ocean" if "ocean" in typ.lower() else None),
            water_type="marine", detail_url=detail_url or None,
            raw={"org": org, "org_name": org_name, "type": typ, "description": desc}))
        kept[sid] = org

    for r in results:
        sid = clean_text(r.get(find_key(r, "MonitoringLocationIdentifier") or ""))
        if sid not in kept:
            continue
        d = parse_date(r.get(find_key(r, "ActivityStartDate") or ""))
        if not d:
            continue
        cond = (clean_text(r.get(find_key(r, "ResultDetectionConditionText") or "")) or "").lower()
        value, qual = parse_result(r.get(find_key(r, "ResultMeasureValue") or ""))
        if value is None and ("not detected" in cond or "below" in cond):
            lim, _ = parse_result(r.get(find_key(r, "DetectionQuantitationLimitMeasure/MeasureValue",
                                                  "DetectionQuantitationLimitMeasureValue") or ""))
            value, qual = (lim, "<") if lim is not None else (None, "below_detection")
        if value is None and not qual:
            continue
        unit = (clean_text(r.get(find_key(r, "ResultMeasure/MeasureUnitCode",
                                          "ResultMeasureMeasureUnitCode") or "")) or "").lower()
        units = "MPN/100mL" if "mpn" in unit else "CFU/100mL"
        time = clean_text(r.get(find_key(r, "ActivityStartTime/Time", "ActivityStartTimeTime") or ""))
        res.samples.append(Sample(
            source_site_id=sid, sample_date=d, indicator=Indicator.ENTEROCOCCUS, value=value,
            qualifier=qual, units=units, sample_time=time[:5] if time else None,
            source_sample_id=clean_text(r.get(find_key(r, "ActivityIdentifier") or "")) or "",
            raw={"org": kept[sid], "unit": unit, "condition": cond}))
    return res
