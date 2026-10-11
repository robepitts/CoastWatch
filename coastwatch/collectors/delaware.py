"""Delaware DNREC Recreational Waters.

Findings (Oct 2026):
  * Map homepage: https://recwaters.dnrec.delaware.gov/  (beach list loaded by JS)
  * Each beach has a server-rendered page:
        /RecWaterSitePublic.aspx?UnitID=<id>
    containing: beach name, guarded/unguarded + water body, active advisories,
    historical advisories, YTD and 30-day geomeans, and the full season's
    Enterococcus results (date, colonies/100 mL).
  * The page is the richest public source of any state: full sample history.

Site discovery: the beach list comes from a JS call we have not yet pinned
down, so we (1) scan the homepage HTML for UnitID references and embedded
coordinates, and (2) merge with a seed list in config/sources.json.
TODO: open the homepage with browser DevTools > Network, find the request
that returns the beach list (likely JSON with UnitID + lat/lon), and add it
to discover_sites().
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from ..models import (Advisory, CollectorResult, Indicator, Sample, Site, Status,
                      StatusReport, normalize_status)
from .base import Collector, parse_date, parse_result

BASE = "https://recwaters.dnrec.delaware.gov"
SITE_URL = BASE + "/RecWaterSitePublic.aspx?UnitID={uid}"


class DelawareCollector(Collector):
    source_id = "de_dnrec"
    state = "DE"
    agency = "Delaware DNREC, Recreational Water Program"
    homepage_url = BASE + "/"

    def __init__(self, seed_unit_ids: Optional[list[str]] = None, **kw):
        super().__init__(**kw)
        self.seed_unit_ids = [str(u) for u in (seed_unit_ids or [])]

    # ---------------------------------------------------------- network
    def discover_sites(self) -> dict[str, dict]:
        """Return {unit_id: {lat, lon}} from the homepage plus seeds."""
        found: dict[str, dict] = {u: {} for u in self.seed_unit_ids}
        try:
            html = self.get(self.homepage_url).text
            found.update({u: found.get(u, {}) for u in parse_unit_ids(html)})
            for uid, coords in parse_embedded_coords(html).items():
                found.setdefault(uid, {}).update(coords)
        except Exception as e:  # discovery failure shouldn't kill seeded collection
            self._discovery_error = str(e)
        return found

    def collect(self) -> CollectorResult:
        result = CollectorResult()
        sites = self.discover_sites()
        if not sites:
            result.warnings.append("No Delaware UnitIDs discovered or seeded.")
        for uid, coords in sites.items():
            try:
                html = self.get(SITE_URL.format(uid=uid)).text
                r = parse_site_page(html, uid)
                for s in r.sites:
                    s.latitude = s.latitude or coords.get("lat")
                    s.longitude = s.longitude or coords.get("lon")
                result.extend(r)
            except Exception as e:
                result.warnings.append(f"DE UnitID {uid}: {e}")
        return result


# -------------------------------------------------------------- parsing
def parse_unit_ids(html: str) -> set[str]:
    return set(re.findall(r"UnitID[=\"':\s]+(\d+)", html, flags=re.I))


def parse_embedded_coords(html: str) -> dict[str, dict]:
    """Look for JSON-ish objects carrying a UnitID and lat/lon in page scripts."""
    out: dict[str, dict] = {}
    pat = re.compile(
        r"\{[^{}]*?UnitID\"?\s*:\s*\"?(\d+)[^{}]*?"
        r"Lat(?:itude)?\"?\s*:\s*\"?(-?\d+\.\d+)[^{}]*?"
        r"(?:Lon|Lng|Longitude)\"?\s*:\s*\"?(-?\d+\.\d+)", re.I | re.S)
    for uid, lat, lon in pat.findall(html):
        out[uid] = {"lat": float(lat), "lon": float(lon)}
    return out


def _water_body(text: str) -> tuple[Optional[str], Optional[bool]]:
    m = re.search(r"This is an?\s+(Guarded|Unguarded)\s+beach(?:\s+along\s+(?:the\s+)?([^.]+))?",
                  text, re.I)
    if not m:
        return None, None
    wb = m.group(2).strip() if m.group(2) else None
    if wb:
        wb = re.sub(r"^the\s+", "", wb, flags=re.I)
        wb = wb[0].upper() + wb[1:]
    return wb, m.group(1).lower() == "guarded"


def _section_tables(soup: BeautifulSoup):
    """Yield (section_heading_text, table) pairs."""
    for table in soup.find_all("table"):
        heading = table.find_previous(re.compile(r"^h[1-6]$"))
        prev_text = heading.get_text(" ", strip=True) if heading else ""
        yield prev_text, table


def _rows(table) -> list[list[str]]:
    return [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            for tr in table.find_all("tr")]


def _advisory_kind(type_text: str, reason: str) -> str:
    st = normalize_status(f"{type_text} {reason}")
    t = type_text.lower()
    if "lift" in t:
        return "lift"
    if "additional information" in t:
        return "info"
    return {Status.OFF_SEASON: "off_season", Status.RESAMPLING: "resampling",
            Status.CLOSED: "closure", Status.ADVISORY: "advisory"}.get(st, "info")


def parse_site_page(html: str, unit_id: str) -> CollectorResult:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    res = CollectorResult()

    h = soup.find(["h1", "h2"], string=lambda s: s and "delaware" not in s.lower()
                  and "advisory" not in s.lower() and "monitoring" not in s.lower())
    name = h.get_text(strip=True) if h else f"Delaware UnitID {unit_id}"
    water_body, guarded = _water_body(text)
    res.sites.append(Site(
        source_site_id=unit_id, name=name, state="DE", water_body=water_body,
        water_type="marine",  # DNREC reports Enterococcus => marine/estuarine
        detail_url=SITE_URL.format(uid=unit_id),
        raw={"guarded": guarded, "water_body": water_body}))

    active: list[Advisory] = []
    samples_found = False
    for heading, table in _section_tables(soup):
        rows = _rows(table)
        if not rows:
            continue
        header = [c.lower() for c in rows[0]]
        if "type" in header and "reason" in header:
            is_active = "active" in heading.lower()
            for r in rows[1:]:
                if len(r) < 4 or not r[0]:
                    continue
                adv = Advisory(source_site_id=unit_id, kind=_advisory_kind(r[0], r[1]),
                               reason=r[1], start_date=parse_date(r[2]),
                               end_date=parse_date(r[3]), source_text=r[0],
                               raw={"row": r, "active": is_active})
                res.advisories.append(adv)
                if is_active:
                    active.append(adv)
        elif any("sample date" in c for c in header):
            for r in rows[1:]:
                if len(r) >= 2 and parse_date(r[0]):
                    _add_sample(res, unit_id, r[0], r[1])
                    samples_found = True

    if not samples_found:  # fallback for non-<table> markup
        for d, v in re.findall(r"(\d{2}/\d{2}/\d{4})\s*\n?\s*([<>]?\d[\d,]*)", text):
            _add_sample(res, unit_id, d, v)

    for label, key in (("YTD Mean Geometric", "ytd_geomean"),
                       ("Last 30 Days Geometric Mean", "geomean_30d")):
        m = re.search(label + r"[^:]*:\s*(\d+(?:\.\d+)?)", text, re.I)
        if m:
            res.sites[0].raw[key] = float(m.group(1))

    # Status: the publisher's own active advisory wins; otherwise "open".
    if active:
        a = active[0]
        status = {"off_season": Status.OFF_SEASON, "resampling": Status.RESAMPLING,
                  "closure": Status.CLOSED, "advisory": Status.ADVISORY}.get(a.kind, Status.OPEN)
        res.statuses.append(StatusReport(unit_id, status, f"{a.source_text} ({a.reason})"))
    elif re.search(r"No Active Advisories", text, re.I):
        res.statuses.append(StatusReport(unit_id, Status.OPEN, "No Active Advisories"))
    else:
        res.statuses.append(StatusReport(unit_id, Status.UNKNOWN, None))
    return res


def _add_sample(res: CollectorResult, unit_id: str, d: str, v: str) -> None:
    value, qual = parse_result(v)
    res.samples.append(Sample(source_site_id=unit_id, sample_date=parse_date(d),
                              indicator=Indicator.ENTEROCOCCUS, value=value,
                              qualifier=qual, units="CFU/100mL",
                              raw={"date": d, "value": v}))
