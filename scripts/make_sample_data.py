"""Generate SIMULATED data for previewing the front end.

Runs synthetic results through the real Store and export code, so the JSON
the website reads is byte-for-byte the format the live pipeline produces.
Site locations approximate real beaches; every result is invented.

    python scripts/make_sample_data.py            # writes docs/sample-data/
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coastwatch.export import export  # noqa: E402
from coastwatch.models import (Advisory, CollectorResult, Indicator, Sample,  # noqa: E402
                               Site, Status, StatusReport)
from coastwatch.store import Store  # noqa: E402

AS_OF = date(2026, 7, 21)
SEASON_START = date(2026, 5, 18)
RAIN_DAYS = {date(2026, 6, 8), date(2026, 6, 29), date(2026, 7, 13)}

# (source_id, site_key, name, county, water_body, lat, lon, dirtiness)
# dirtiness: 1 = clean ocean beach ... 4 = urban/enclosed bay
SITES = [
    # Virginia (VDH, status derived from results)
    ("va_vdh", "VB-NORTH", "Virginia Beach - 40th Street", "Virginia Beach", "Atlantic Ocean", 36.884, -75.976, 1),
    ("va_vdh", "VB-RESORT", "Virginia Beach - 17th Street", "Virginia Beach", "Atlantic Ocean", 36.846, -75.973, 1.3),
    ("va_vdh", "SANDBRIDGE", "Sandbridge Beach", "Virginia Beach", "Atlantic Ocean", 36.725, -75.934, 1),
    ("va_vdh", "OCEANVIEW", "Ocean View Beach", "Norfolk", "Chesapeake Bay", 36.955, -76.250, 2.6),
    ("va_vdh", "BUCKROE", "Buckroe Beach", "Hampton", "Chesapeake Bay", 37.041, -76.288, 2.4),
    ("va_vdh", "CAPECHARLES", "Cape Charles Beach", "Northampton", "Chesapeake Bay", 37.268, -76.025, 1.8),
    ("va_vdh", "COLONIAL", "Colonial Beach", "Westmoreland", "Potomac River", 38.250, -76.962, 3.4),
    ("va_vdh", "CHINCO", "Assateague (Chincoteague NWR)", "Accomack", "Atlantic Ocean", 37.906, -75.343, 1),
    # Maryland (MDE, reports status)
    ("md_mde", "MD-OC-N", "Ocean City - 120th Street", "Worcester", "Atlantic Ocean", 38.418, -75.054, 1),
    ("md_mde", "MD-OC-S", "Ocean City - Inlet Beach", "Worcester", "Atlantic Ocean", 38.326, -75.083, 1.4),
    ("md_mde", "MD-ASSAT", "Assateague State Park", "Worcester", "Atlantic Ocean", 38.236, -75.140, 1),
    ("md_mde", "MD-SANDY", "Sandy Point State Park", "Anne Arundel", "Chesapeake Bay", 39.012, -76.398, 2.8),
    ("md_mde", "MD-NBEACH", "North Beach", "Calvert", "Chesapeake Bay", 38.707, -76.532, 2.2),
    ("md_mde", "MD-PTLOOK", "Point Lookout State Park", "St. Mary's", "Potomac River", 38.050, -76.322, 2.0),
    ("md_mde", "MD-ELKNECK", "Elk Neck State Park", "Cecil", "Chesapeake Bay", 39.481, -75.982, 3.0),
    ("md_mde", "MD-MATAP", "Matapeake Beach", "Queen Anne's", "Chesapeake Bay", 38.957, -76.355, 2.5),
    # Delaware (DNREC, reports status)
    ("de_dnrec", "293", "Cape Henlopen - North", "Sussex", "Atlantic Ocean", 38.798, -75.093, 1),
    ("de_dnrec", "304", "Rehoboth Beach - Rehoboth Avenue", "Sussex", "Atlantic Ocean", 38.717, -75.076, 1.3),
    ("de_dnrec", "363620", "Dewey Beach - Dagsworthy Street", "Sussex", "Atlantic Ocean", 38.693, -75.073, 1.2),
    ("de_dnrec", "296", "North Indian River Inlet", "Sussex", "Atlantic Ocean", 38.612, -75.062, 1.5),
    ("de_dnrec", "298", "Bethany Beach", "Sussex", "Atlantic Ocean", 38.539, -75.055, 1),
    ("de_dnrec", "299", "Fenwick Island", "Sussex", "Atlantic Ocean", 38.462, -75.051, 1),
    ("de_dnrec", "288", "Broadkill Beach", "Sussex", "Delaware Bay", 38.831, -75.218, 2.6),
    ("de_dnrec", "286", "Slaughter Beach", "Sussex", "Delaware Bay", 38.911, -75.302, 2.4),
    # New Jersey (NJDEP, reports status)
    ("nj_dep", "CM14", "Cape May - Grant Street", "Cape May", "Atlantic Ocean", 38.929, -74.917, 1.2),
    ("nj_dep", "CM31", "Wildwood - Rio Grande Avenue", "Cape May", "Atlantic Ocean", 38.987, -74.808, 1.3),
    ("nj_dep", "CM55", "Ocean City - 34th Street", "Cape May", "Atlantic Ocean", 39.247, -74.592, 1.1),
    ("nj_dep", "AC08", "Atlantic City - Albany Avenue", "Atlantic", "Atlantic Ocean", 39.352, -74.444, 1.8),
    ("nj_dep", "AC21", "Brigantine - 14th Street", "Atlantic", "Atlantic Ocean", 39.408, -74.362, 1.2),
    ("nj_dep", "OC52", "Beach Haven - Centre Street", "Ocean", "Atlantic Ocean", 39.561, -74.241, 1),
    ("nj_dep", "OC14", "Seaside Park - 5th Avenue", "Ocean", "Atlantic Ocean", 39.928, -74.077, 1.1),
    ("nj_dep", "OC02", "Point Pleasant Beach", "Ocean", "Atlantic Ocean", 40.088, -74.035, 1.4),
    ("nj_dep", "OCB7", "Beachwood Beach West", "Ocean", "Toms River", 39.940, -74.191, 3.6),
    ("nj_dep", "MO22", "Asbury Park - 4th Avenue", "Monmouth", "Atlantic Ocean", 40.222, -73.998, 1.6),
    ("nj_dep", "MO12", "Long Branch - Brighton Avenue", "Monmouth", "Atlantic Ocean", 40.296, -73.979, 1.5),
    ("nj_dep", "MO01", "Sandy Hook - Gunnison Beach", "Monmouth", "Atlantic Ocean", 40.448, -73.993, 1.4),
    ("nj_dep", "MOB3", "Keansburg Beach", "Monmouth", "Raritan Bay", 40.452, -74.135, 3.4),
    # New York City (DOHMH, status derived from results)
    ("nyc_doh", "coney-island--center", "Coney Island (Center)", "Kings", "Atlantic Ocean", 40.572, -73.979, 1.7),
    ("nyc_doh", "manhattan-beach--center", "Manhattan Beach (Center)", "Kings", "Sheepshead Bay", 40.577, -73.942, 2.2),
    ("nyc_doh", "rockaway-beach--center", "Rockaway Beach (Center)", "Queens", "Atlantic Ocean", 40.583, -73.816, 1.4),
    ("nyc_doh", "orchard-beach--center", "Orchard Beach (Center)", "Bronx", "Long Island Sound", 40.867, -73.792, 3.2),
    ("nyc_doh", "south-beach--center", "South Beach (Center)", "Richmond", "Lower New York Bay", 40.584, -74.068, 2.6),
    ("nyc_doh", "midland-beach--center", "Midland Beach (Center)", "Richmond", "Lower New York Bay", 40.573, -74.090, 2.7),
    # New York State (NYSDOH, reports status)
    ("ny_doh", "NAS-LB", "Long Beach", "Nassau", "Atlantic Ocean", 40.584, -73.662, 1.3),
    ("ny_doh", "NAS-JB6", "Jones Beach - Field 6", "Nassau", "Atlantic Ocean", 40.594, -73.488, 1),
    ("ny_doh", "SUF-RM", "Robert Moses - Field 2", "Suffolk", "Atlantic Ocean", 40.624, -73.272, 1),
    ("ny_doh", "SUF-WH", "Westhampton Beach", "Suffolk", "Atlantic Ocean", 40.800, -72.640, 1),
    ("ny_doh", "SUF-MON", "Montauk - Ditch Plains", "Suffolk", "Atlantic Ocean", 41.038, -71.918, 1),
    ("ny_doh", "SUF-SB", "Sunken Meadow", "Suffolk", "Long Island Sound", 40.913, -73.259, 2.4),
    ("ny_doh", "NAS-TP", "Tappen Beach", "Nassau", "Hempstead Harbor", 40.839, -73.656, 3.3),
    ("ny_doh", "WES-RYE", "Rye Playland Beach", "Westchester", "Long Island Sound", 40.968, -73.672, 2.9),
]

STATE = {"va_vdh": "VA", "md_mde": "MD", "de_dnrec": "DE", "nj_dep": "NJ",
         "nyc_doh": "NY", "ny_doh": "NY"}
REPORTS_STATUS = {"md_mde", "de_dnrec", "nj_dep", "ny_doh"}

# A few hand-placed situations so every status shows up in the preview.
FORCED_LATEST = {"OCEANVIEW": 260, "MD-SANDY": 189, "OCB7": 520, "MOB3": 2420,
                 "orchard-beach--center": 310, "NAS-TP": 175, "286": 135}
FORCED_STATUS = {
    "OCB7": (Status.CLOSED, "Closed"),
    "MOB3": (Status.ADVISORY, "Advisory"),
    "MD-SANDY": (Status.ADVISORY, "Advisory"),
    "MD-ELKNECK": (Status.ADVISORY, "Algae Bloom"),
    "286": (Status.RESAMPLING, "Resampling Underway"),
    "NAS-TP": (Status.CLOSED, "Closed - bacteria"),
}
STALE = {"COLONIAL", "MD-MATAP"}  # last sampled weeks ago -> "no recent data"


def sample_dates(key: str) -> list[date]:
    d = SEASON_START
    out = []
    twice_weekly = key.startswith("rockaway") or key.startswith("coney")
    while d <= AS_OF:
        out.append(d)
        if twice_weekly:
            out.append(d + timedelta(days=3))
        d += timedelta(days=7)
    out = [x for x in out if x <= AS_OF]
    if key in STALE:
        out = [x for x in out if x <= AS_OF - timedelta(days=24)]
    return out


def simulate(rng: random.Random, dirt: float, d: date) -> int:
    mu = math.log(6) + 0.55 * dirt
    if any(abs((d - r).days) <= 1 for r in RAIN_DAYS):
        mu += 0.9 + 0.35 * dirt
    v = rng.lognormvariate(mu, 0.85)
    return max(10, int(round(v)))  # lab detection limit ~10


def build() -> dict[str, CollectorResult]:
    rng = random.Random(20260721)
    results: dict[str, CollectorResult] = {}
    for src, key, name, county, wb, lat, lon, dirt in SITES:
        res = results.setdefault(src, CollectorResult())
        res.sites.append(Site(source_site_id=key, name=name, state=STATE[src], county=county,
                              water_body=wb, water_type="marine", latitude=lat, longitude=lon,
                              detail_url={"va_vdh": "https://www.vdh.virginia.gov/waterborne-hazards-control/swimming-advisories-and-monitored-beaches-map/",
                                          "md_mde": "https://mde.maryland.gov/programs/water/MHB/Pages/Current-Conditions.aspx",
                                          "de_dnrec": f"https://recwaters.dnrec.delaware.gov/RecWaterSitePublic.aspx?UnitID={key}",
                                          "nj_dep": "https://njbeaches.org/",
                                          "nyc_doh": "https://a816-dohbesp.nyc.gov/IndicatorPublic/Beaches/",
                                          "ny_doh": "https://www.health.ny.gov/environmental/outdoors/swimming/coastalbeaches.htm"}[src],
                              raw={"sample_data": True}))
        dates = sample_dates(key)
        for i, d in enumerate(dates):
            v = simulate(rng, dirt, d)
            if i == len(dates) - 1 and key in FORCED_LATEST:
                v = FORCED_LATEST[key]
            res.samples.append(Sample(source_site_id=key, sample_date=d.isoformat(),
                                      indicator=Indicator.ENTEROCOCCUS, value=float(v),
                                      qualifier="<" if v == 10 else None,
                                      units="MPN/100mL" if src == "nyc_doh" else "CFU/100mL",
                                      source_sample_id=f"{key}-{d:%Y%m%d}"))
            if v > 104 and i < len(dates) - 1:  # past exceedance -> a short advisory
                res.advisories.append(Advisory(source_site_id=key, kind="advisory",
                                               start_date=(d + timedelta(days=1)).isoformat(),
                                               end_date=(d + timedelta(days=3)).isoformat(),
                                               reason="Bacteria", source_text="Swimming advisory"))
        if key in FORCED_STATUS:
            st, text = FORCED_STATUS[key]
            kind = {Status.CLOSED: "closure", Status.RESAMPLING: "resampling"}.get(st, "advisory")
            res.advisories.append(Advisory(source_site_id=key, kind=kind,
                                           start_date=dates[-1].isoformat() if dates else None,
                                           reason="Algae" if "Algae" in text else "Bacteria",
                                           source_text=text))
            res.statuses.append(StatusReport(key, st, text))
        elif src in REPORTS_STATUS and key not in STALE:
            last = res.samples[-1].value
            st = Status.ADVISORY if last > 104 else Status.OPEN
            res.statuses.append(StatusReport(key, st, "Advisory" if last > 104 else "Open"))
    return results


def main(out: Path = ROOT / "docs" / "sample-data") -> None:
    store = Store(":memory:")
    for src, res in build().items():
        store.register_source(src, STATE[src], src)
        store.save(src, STATE[src], res)
    summary = export(store, out, today=AS_OF)
    summary_path = out / "summary.json"
    import json
    s = json.loads(summary_path.read_text())
    s["sample_data"] = True
    s["as_of"] = AS_OF.isoformat()
    summary_path.write_text(json.dumps(s, indent=1))
    print(json.dumps(summary["by_state"], indent=1))


if __name__ == "__main__":
    main()
