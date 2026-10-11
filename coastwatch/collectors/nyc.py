"""New York City DOHMH beach samples via NYC Open Data (Socrata).

Findings (Oct 2026):
  * Dataset "Beach Water Samples", id 2xir-kwzz, ~31k rows since 2005,
    last updated 2026-09-21, newest sample 2026-09-29.
  * Columns: sample_id, sample_date, beach_name, sample_location
    (Left/Center/Right), enterococci_results, units_or_notes
    ("MPN/100 ml" or "Result below detection limit").
  * No coordinates and no advisory status. Coordinates come from
    config/coordinate_overrides.csv; status is derived from results.

This is a proper public API, so it is the most reliable feed in the set.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..models import CollectorResult, Indicator, Sample, Site
from .base import Collector, parse_date, parse_result, slug

API = "https://data.cityofnewyork.us/resource/2xir-kwzz.json"


class NYCCollector(Collector):
    source_id = "nyc_doh"
    state = "NY"
    agency = "NYC Dept. of Health and Mental Hygiene"
    homepage_url = "https://data.cityofnewyork.us/d/2xir-kwzz"

    def __init__(self, lookback_days: int = 120, **kw):
        super().__init__(**kw)
        self.lookback_days = lookback_days

    def collect(self) -> CollectorResult:
        since = (date.today() - timedelta(days=self.lookback_days)).isoformat()
        rows, offset, page = [], 0, 5000
        while True:
            resp = self.get(API, params={
                "$where": f"sample_date >= '{since}T00:00:00'",
                "$order": "sample_date DESC", "$limit": page, "$offset": offset})
            batch = resp.json()
            rows += batch
            if len(batch) < page:
                break
            offset += page
        return parse_rows(rows)


def parse_rows(rows: list[dict]) -> CollectorResult:
    res = CollectorResult()
    seen: set[str] = set()
    for r in rows:
        beach = (r.get("beach_name") or "").strip()
        if not beach:
            continue
        loc = (r.get("sample_location") or "").strip().title().replace("Rigth", "Right")
        site_key = slug(beach) + (f"--{slug(loc)}" if loc and loc != "Null" else "")
        if site_key not in seen:
            seen.add(site_key)
            res.sites.append(Site(
                source_site_id=site_key,
                name=beach.title() + (f" ({loc})" if loc and loc != "Null" else ""),
                state="NY", county=None, water_type="marine",
                detail_url="https://a816-dohbesp.nyc.gov/IndicatorPublic/Beaches/",
                raw={"beach_name": beach, "sample_location": loc}))
        notes = (r.get("units_or_notes") or "").strip()
        value, qual = parse_result(r.get("enterococci_results"))
        if "below detection" in notes.lower():
            qual = "below_detection"
        d = parse_date(r.get("sample_date"))
        if not d:
            continue
        res.samples.append(Sample(
            source_site_id=site_key, sample_date=d, indicator=Indicator.ENTEROCOCCUS,
            value=value, qualifier=qual,
            units="MPN/100mL" if "mpn" in notes.lower() else None,
            source_sample_id=r.get("sample_id", ""), raw=r))
    return res
