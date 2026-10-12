"""Export the datastore as static JSON for the website.

    docs/data/sites.geojson       one Point per mapped site, with current status
    docs/data/sites/<site>.json   full sample + advisory history for one site
    docs/data/summary.json        counts by state and status, data freshness

The front end reads only these files, so it can be hosted statically.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from . import standards
from .models import Status
from .collectors.base import clean_text
from .store import Store

STALE_DAYS = 14  # in season, older results are shown as "no recent data"


def derive_status(reported: Optional[str], latest_date: Optional[str],
                  latest_exceeds: Optional[int], today: date) -> str:
    """Publisher-reported status wins; otherwise infer from the newest sample."""
    if reported and reported != Status.UNKNOWN.value:
        return reported
    if not latest_date:
        return Status.NO_DATA.value
    age = (today - date.fromisoformat(latest_date)).days
    if age > STALE_DAYS:
        # Mid-Atlantic swim season is roughly mid-May to mid-September.
        in_season = (5, 15) <= (today.month, today.day) <= (9, 20)
        return Status.NO_DATA.value if in_season else Status.OFF_SEASON.value
    if latest_exceeds == 1:
        return Status.ADVISORY.value
    return Status.OPEN.value


def _fname(site_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", site_id) + ".json"


def export(store: Store, out_dir: str | Path = "docs/data", today: Optional[date] = None) -> dict:
    today = today or date.today()
    out = Path(out_dir)
    (out / "sites").mkdir(parents=True, exist_ok=True)

    sites = store.query("""
        SELECT s.*, ls.status AS reported_status, ls.source_status, ls.observed_at
        FROM sites s LEFT JOIN latest_status ls ON ls.site_id = s.site_id""")
    features, unmapped = [], []
    summary: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "as_of": today.isoformat(),
                     "by_state": {}, "unmapped_sites": 0}

    for s in sites:
        hist = store.query("""SELECT sample_date, indicator, value, qualifier, units, exceeds_bav
                              FROM samples WHERE site_id=? ORDER BY sample_date""", (s["site_id"],))
        advs = store.query("""SELECT kind, reason, start_date, end_date, source_text
                              FROM advisories WHERE site_id=? ORDER BY start_date DESC""",
                           (s["site_id"],))
        latest = hist[-1] if hist else None
        ent = [(h["sample_date"], h["value"]) for h in hist
               if h["indicator"] == "enterococcus" and h["value"] is not None]
        gm = standards.rolling_geomean(ent, latest["sample_date"]) if latest and ent else None
        status = derive_status(s["reported_status"], latest["sample_date"] if latest else None,
                               latest["exceeds_bav"] if latest else None, today)
        thr = standards.threshold(s["state"], latest["indicator"] if latest else "enterococcus")

        props = {
            "site_id": s["site_id"], "name": s["name"], "state": s["state"],
            "county": clean_text(s["county"]), "water_body": s["water_body"], "status": status,
            "source_status": s["source_status"], "detail_url": s["detail_url"],
            "latest_sample_date": latest["sample_date"] if latest else None,
            "latest_value": latest["value"] if latest else None,
            "latest_qualifier": latest["qualifier"] if latest else None,
            "latest_indicator": latest["indicator"] if latest else None,
            "units": latest["units"] if latest else None,
            "geomean_30d": round(gm, 1) if gm else None,
            "bav": thr["single_sample"] if thr else None,
            "samples_count": len(hist),
            "location_approx": s["coord_source"] == "override_approx",
            "history_file": f"sites/{_fname(s['site_id'])}",
        }
        (out / "sites" / _fname(s["site_id"])).write_text(json.dumps({
            **props, "samples": [dict(h) for h in hist], "advisories": [dict(a) for a in advs]},
            indent=1))

        st = summary["by_state"].setdefault(s["state"], {"sites": 0, "statuses": {},
                                                         "newest_sample": None})
        st["sites"] += 1
        st["statuses"][status] = st["statuses"].get(status, 0) + 1
        if latest and (not st["newest_sample"] or latest["sample_date"] > st["newest_sample"]):
            st["newest_sample"] = latest["sample_date"]

        if s["latitude"] is None or s["longitude"] is None:
            unmapped.append({"site_id": s["site_id"], "name": s["name"], "state": s["state"]})
            continue
        features.append({"type": "Feature",
                         "geometry": {"type": "Point",
                                      "coordinates": [s["longitude"], s["latitude"]]},
                         "properties": props})

    summary["unmapped_sites"] = len(unmapped)
    (out / "sites.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "unmapped_sites.json").write_text(json.dumps(unmapped, indent=1))
    return summary
