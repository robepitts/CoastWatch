"""Water Quality Portal (waterqualitydata.us) collector.

The national portal holds monitoring results that agencies submit to EPA's
Water Quality Exchange (WQX), including BEACH Act beach monitoring. It has a
public, documented download service, so it can fill gaps where a state's own
website can't be read (NJ, DE) or publishes nothing off-season (NY State).

IMPORTANT: portal data is DELAYED. Most agencies submit beach results
periodically, often annually, so results may be months old. The website labels
these sources as "latest reported results", not current conditions.

Scope: every coastal monitoring station (site types Ocean and Estuary, which
include bays, harbors, sounds and tidal tributaries) with fecal-bacteria results
(Enterococcus or E. coli), whichever agency submitted them. Each station is
tagged "beach" (EPA BEACH Program location types) or "waterway". Beach stations
already covered by a direct, current source can be skipped by county
(exclude_beach_counties) or entirely (exclude_beach_sites).
"""
from __future__ import annotations

import csv
import html
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
    "MD": {"003": "Anne Arundel", "005": "Baltimore", "009": "Calvert", "011": "Caroline", "015": "Cecil",
           "017": "Charles", "019": "Dorchester", "025": "Harford", "029": "Kent", "033": "Prince George's",
           "035": "Queen Anne's", "037": "St. Mary's", "039": "Somerset", "041": "Talbot", "045": "Wicomico",
           "047": "Worcester", "510": "Baltimore City"},
    "VA": {"001": "Accomack", "073": "Gloucester", "093": "Isle of Wight", "095": "James City",
           "099": "King George", "103": "Lancaster", "115": "Mathews", "119": "Middlesex",
           "131": "Northampton", "133": "Northumberland", "159": "Richmond County", "193": "Westmoreland",
           "199": "York", "550": "Chesapeake", "650": "Hampton", "700": "Newport News", "710": "Norfolk",
           "735": "Poquoson", "740": "Portsmouth", "800": "Suffolk", "810": "Virginia Beach"},
}


class WQPCollector(Collector):
    def __init__(self, source_id: str, state: str, agency: str, homepage_url: str = "",
                 site_types: Optional[list[str]] = None, characteristics: Optional[list[str]] = None,
                 years_back: int = 2, exclude_org_pattern: Optional[str] = None,
                 location_types: Optional[list[str]] = None, exclude_counties: Optional[list[str]] = None,
                 exclude_beach_counties: Optional[list[str]] = None, exclude_beach_sites: bool = False,
                 timeout_s: float = 300, **kw):
        super().__init__(**kw)
        self.source_id, self.state, self.agency, self.homepage_url = source_id, state, agency, homepage_url
        self.site_types = site_types or ["Ocean", "Estuary"]
        self.characteristics = characteristics or ["Enterococcus", "Enterococci", "Escherichia coli"]
        self.years_back = years_back
        self.exclude = re.compile(exclude_org_pattern, re.I) if exclude_org_pattern else None
        # Optional: restrict to location types (e.g. ["BEACH Program Site"]); default keeps all.
        self.location_types = location_types or []
        self.exclude_counties = {str(c).zfill(3) for c in (exclude_counties or [])}
        self.exclude_beach_counties = {str(c).zfill(3) for c in (exclude_beach_counties or [])}
        self.exclude_beach_sites = exclude_beach_sites
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
        res = parse_wqp(stations, results, self.state, self.exclude, self.homepage_url,
                        location_types=self.location_types, exclude_counties=self.exclude_counties,
                        exclude_beach_counties=self.exclude_beach_counties,
                        exclude_beach_sites=self.exclude_beach_sites)
        orgs = Counter(s.raw.get("org_name") or s.raw.get("org") for s in res.sites)
        log.info("%s: %d stations kept from %d agencies: %s", self.source_id, len(res.sites),
                 len(orgs), "; ".join(f"{k} ({v})" for k, v in orgs.most_common(8)))
        if not res.sites:
            res.warnings.append(f"{self.source_id}: no beach stations with Enterococcus results found")
        return res


def parse_wqp(stations: list[dict], results: list[dict], state: str,
              exclude: Optional[re.Pattern] = None, detail_url: str = "",
              location_types: Optional[list[str]] = None,
              exclude_counties: Optional[set[str]] = None,
              exclude_beach_counties: Optional[set[str]] = None,
              exclude_beach_sites: bool = False) -> CollectorResult:
    res = CollectorResult()
    kept: dict[str, str] = {}   # station id -> org
    prefixes = [t.lower() for t in (location_types or [])]
    for st in stations:
        id_k = find_key(st, "MonitoringLocationIdentifier")
        sid = clean_text(st.get(id_k)) if id_k else None
        if not sid:
            continue
        org = clean_text(st.get(find_key(st, "OrganizationIdentifier") or "")) or ""
        org_name = html.unescape(clean_text(st.get(find_key(st, "OrganizationFormalName") or "")) or "")
        if exclude and (exclude.search(org) or exclude.search(org_name)):
            continue
        try:
            lat = float(st.get(find_key(st, "LatitudeMeasure") or "") or "nan")
            lon = float(st.get(find_key(st, "LongitudeMeasure") or "") or "nan")
        except ValueError:
            lat = lon = float("nan")
        if lat != lat or lon != lon:   # NaN: unusable location
            continue
        typ = clean_text(st.get(find_key(st, "MonitoringLocationTypeName") or "")) or ""
        if prefixes and not any(typ.lower().startswith(pf) for pf in prefixes):
            continue
        name = clean_text(st.get(find_key(st, "MonitoringLocationName") or "")) or sid
        desc = clean_text(st.get(find_key(st, "MonitoringLocationDescriptionText") or ""))
        ccode = str(st.get(find_key(st, "CountyCode") or "") or "").strip().zfill(3)
        is_beach = typ.lower().startswith("beach program site")
        if exclude_counties and ccode in exclude_counties:
            continue
        if is_beach and (exclude_beach_sites or (exclude_beach_counties and ccode in exclude_beach_counties)):
            continue   # beach already covered by a direct, current source
        county = COUNTY.get(state, {}).get(ccode)
        provider = clean_text(st.get(find_key(st, "ProviderName") or "")) or "STORET"
        station_url = f"https://www.waterqualitydata.us/provider/{provider}/{org}/{sid}/" if org else None
        display = nice_name(name)
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,10}", name or ""):      # bare station code like "1403a"
            display = f"{nice_name(desc)} ({name})" if desc and len(desc) <= 80 else f"Station {name}"
        res.sites.append(Site(
            source_site_id=sid, name=display, state=state, latitude=lat, longitude=lon,
            county=county, water_body=_water_body(typ),
            water_type="fresh" if re.search(r"lake|river|stream", typ, re.I) else "marine",
            # beaches link to the state's beach page; other waterways to their portal station page
            detail_url=(detail_url or station_url) if is_beach else (station_url or detail_url),
            raw={"org": org, "org_name": org_name, "type": typ, "description": desc,
                 "kind": "beach" if is_beach else "waterway", "county_code": ccode}))
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
        char = (clean_text(r.get(find_key(r, "CharacteristicName") or "")) or "").lower()
        indicator = Indicator.E_COLI if ("coli" in char) else Indicator.ENTEROCOCCUS
        res.samples.append(Sample(
            source_site_id=sid, sample_date=d, indicator=indicator, value=value,
            qualifier=qual, units=units, sample_time=time[:5] if time else None,
            source_sample_id=(clean_text(r.get(find_key(r, "ActivityIdentifier") or "")) or "") + (":ec" if indicator == Indicator.E_COLI else ""),
            raw={"org": kept[sid], "unit": unit, "condition": cond}))
    return res


def _water_body(typ: str) -> Optional[str]:
    t = typ.lower()
    for key, label in (("great lake", "Great Lakes"), ("lake", "Lake"), ("river", "River"),
                       ("stream", "River"), ("estuar", "Estuary or bay"), ("ocean", "Ocean")):
        if key in t:
            return label
    return None
