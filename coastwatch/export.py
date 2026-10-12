"""Export the datastore as static JSON for the website.

    docs/data/sites.geojson       one Point per monitoring site (beach or other coastal water)
    docs/data/sites/<site>.json   merged sample + advisory history for that beach
    docs/data/summary.json        counts by state and status, data freshness

Several sources can describe the same beach (a county site with current
results, MDE's status map, the national portal's delayed history). Export
links such sites into one beach:

  * Candidates come from DIFFERENT sources: within LINK_M metres, or within
    NAME_LINK_M metres with similar names. Pairs are linked nearest-first, and a
    beach never contains two sites from the same source.
  * config/site_links.csv can force ("link") or prevent ("separate") a pairing.
  * The beach's status comes from the highest-priority source that has a
    meaningful status; its history combines results from every linked source.

Source settings (priority, label, delayed, status_only) live in
config/sources.json, so new sources need no website changes.
"""
from __future__ import annotations

import csv
import html
import json
import math
import re
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from . import standards
from .collectors.base import clean_text
from .models import Status
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "config" / "sources.json"
LINKS = ROOT / "config" / "site_links.csv"

STALE_DAYS = 14       # beaches: older results are "no recent data" (in season) or "off season"
STALE_DAYS_WATERWAY = 45   # rivers, bays, harbors are often sampled monthly, year-round
LINK_M = 250          # always link sites from different sources this close
NAME_LINK_M = 1000    # link up to this far if names are similar
NAME_SIM = 0.6
WEAK = {Status.NO_DATA.value, Status.UNKNOWN.value, Status.OFF_SEASON.value}
# an official status from any source outranks one inferred from results
_STOP = {"beach", "beaches", "park", "state", "the", "at", "of", "public", "community",
         "st", "street", "ave", "avenue", "rd", "road", "center", "left", "right", "north",
         "south", "east", "west", "mid", "middle", "end", "and", "club", "inc"}


def derive_status(reported: Optional[str], latest_date: Optional[str],
                  latest_exceeds: Optional[int], today: date, kind: str = "beach") -> str:
    """Publisher-reported status wins; otherwise infer from the newest sample."""
    if reported and reported != Status.UNKNOWN.value:
        return reported
    if not latest_date:
        return Status.NO_DATA.value
    age = (today - date.fromisoformat(latest_date)).days
    if kind == "waterway":
        if age > STALE_DAYS_WATERWAY:
            return Status.NO_DATA.value          # no swim season for year-round monitoring
    elif age > STALE_DAYS:
        in_season = (5, 15) <= (today.month, today.day) <= (9, 20)
        return Status.NO_DATA.value if in_season else Status.OFF_SEASON.value
    if latest_exceeds == 1:
        return Status.EXCEEDED.value    # inferred from a result, not an official advisory
    return Status.OPEN.value


# ------------------------------------------------------------------ helpers
def _fname(site_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", site_id) + ".json"


def load_source_meta(path: Path = SOURCES) -> dict[str, dict]:
    try:
        cfg = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    out = {}
    for sid, c in cfg.items():
        if sid.startswith("_") or not isinstance(c, dict):
            continue
        out[sid] = {"priority": c.get("priority", 10 if c.get("collector") == "wqp" else 50),
                    "label": c.get("label") or c.get("agency") or sid,
                    "delayed": bool(c.get("delayed", c.get("collector") == "wqp")),
                    "status_only": bool(c.get("status_only", False)),
                    "site_kind": c.get("site_kind"),
                    "status_only_note": c.get("status_only_note")}
    return out


def _meta(meta: dict, source_id: str) -> dict:
    return meta.get(source_id) or {"priority": 50, "label": source_id, "delayed": False,
                                   "status_only": False, "status_only_note": None, "site_kind": None}


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(h))


def _norm_name(name: str) -> str:
    name = re.sub(r"\(.*?\)", " ", name.lower())
    toks = [t for t in re.findall(r"[a-z0-9]+", name) if t not in _STOP]
    return " ".join(toks)


def name_similarity(a: str, b: str) -> float:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb)
    return max(jacc, SequenceMatcher(None, na, nb).ratio())


def load_link_overrides(path: Path = LINKS) -> tuple[set, set]:
    force, never = set(), set()
    p = Path(path)
    if not p.exists():
        return force, never
    with p.open() as f:
        for r in csv.DictReader(f):
            a, b = (r.get("site_a") or "").strip(), (r.get("site_b") or "").strip()
            if not a or not b:
                continue
            pair = frozenset((a, b))
            (never if (r.get("action") or "").strip().lower() == "separate" else force).add(pair)
    return force, never


def link_sites(sites: list[dict], force: set = frozenset(), never: set = frozenset()) -> list[list[dict]]:
    """Group sites (dicts with site_id, source_id, name, latitude, longitude) into beaches."""
    idx = {s["site_id"]: i for i, s in enumerate(sites)}
    parent = list(range(len(sites)))
    sources = [{s["source_id"]} for s in sites]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j, forced=False):
        ri, rj = find(i), find(j)
        if ri == rj:
            return
        if not forced and sources[ri] & sources[rj]:
            return                       # never two sites from one source in a beach
        parent[rj] = ri
        sources[ri] |= sources[rj]

    for pair in force:
        a, b = tuple(pair) if len(pair) == 2 else (None, None)
        if a in idx and b in idx:
            union(idx[a], idx[b], forced=True)

    located = [i for i, s in enumerate(sites) if s["latitude"] is not None and s["longitude"] is not None]
    cands = []
    for x in range(len(located)):
        i = located[x]
        si = sites[i]
        for y in range(x + 1, len(located)):
            j = located[y]
            sj = sites[j]
            if si["source_id"] == sj["source_id"]:
                continue
            if abs(si["latitude"] - sj["latitude"]) > 0.01:    # ~1.1 km quick reject
                continue
            d = distance_m((si["latitude"], si["longitude"]), (sj["latitude"], sj["longitude"]))
            approx = si.get("coord_source") == "override_approx" or sj.get("coord_source") == "override_approx"
            similar = d <= NAME_LINK_M and name_similarity(si["name"], sj["name"]) >= NAME_SIM
            # approximate positions can't be trusted on distance alone
            if similar or (d <= LINK_M and not approx):
                if frozenset((si["site_id"], sj["site_id"])) not in never:
                    cands.append((d, i, j))
    for _, i, j in sorted(cands):
        union(i, j)

    groups: dict[int, list[dict]] = {}
    for i, s in enumerate(sites):
        groups.setdefault(find(i), []).append(s)
    return list(groups.values())


def _dedupe_samples(samples: list[dict]) -> list[dict]:
    seen, out = set(), []
    for s in sorted(samples, key=lambda s: (s["sample_date"], s.get("_priority", 0) * -1)):
        key = (s["sample_date"], s["indicator"],
               None if s["value"] is None else round(s["value"], 1), s["qualifier"])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# ------------------------------------------------------------------ export
def export(store: Store, out_dir: str | Path = "docs/data", today: Optional[date] = None,
           sources_path: Path = SOURCES, links_path: Path = LINKS) -> dict:
    today = today or date.today()
    out = Path(out_dir)
    (out / "sites").mkdir(parents=True, exist_ok=True)
    for old in (out / "sites").glob("*.json"):   # drop files for beaches that merged away
        old.unlink()
    meta = load_source_meta(sources_path)
    force, never = load_link_overrides(links_path)

    rows = [dict(r) for r in store.query("""
        SELECT s.*, ls.status AS reported_status, ls.source_status
        FROM sites s LEFT JOIN latest_status ls ON ls.site_id = s.site_id""")]
    for r in rows:
        r["samples"] = [dict(x) for x in store.query(
            """SELECT sample_date, indicator, value, qualifier, units, exceeds_bav
               FROM samples WHERE site_id=? ORDER BY sample_date""", (r["site_id"],))]
        r["advisories"] = [dict(x) for x in store.query(
            """SELECT kind, reason, start_date, end_date, source_text
               FROM advisories WHERE site_id=? ORDER BY start_date DESC""", (r["site_id"],))]
        m = _meta(meta, r["source_id"])
        r["_priority"] = m["priority"]
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except (ValueError, TypeError):
            raw = {}
        raw_kind = raw.get("kind")
        if not raw_kind and raw.get("type"):     # portal stations saved before tagging existed
            raw_kind = "beach" if str(raw["type"]).lower().startswith("beach program site") else "waterway"
        r["_kind"] = raw_kind or m.get("site_kind") or "beach"
        r["_org"] = html.unescape(raw.get("org_name") or "") or None   # portal: who submitted it
        latest = r["samples"][-1] if r["samples"] else None
        r["_status"] = derive_status(r["reported_status"], latest["sample_date"] if latest else None,
                                     latest["exceeds_bav"] if latest else None, today, r["_kind"])

    features, unmapped = [], []
    summary: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "as_of": today.isoformat(), "by_state": {}, "unmapped_sites": 0,
                     "linked_beaches": 0}

    for group in link_sites(rows, force, never):
        members = sorted(group, key=lambda r: (-r["_priority"], r["site_id"]))
        with_data = [m for m in members if m["samples"] or m["reported_status"]]
        primary = (with_data or members)[0]
        status_member = next((m for m in members if m["_status"] not in WEAK), primary)
        status = status_member["_status"]

        merged = []
        for m in members:
            for s in m["samples"]:
                merged.append({**s, "_priority": m["_priority"],
                               "source": m["_org"] or _meta(meta, m["source_id"])["label"]})
        samples = _dedupe_samples(merged)
        for s in samples:
            s.pop("_priority", None)
        advisories = [a for m in members for a in m["advisories"]]
        advisories.sort(key=lambda a: a["start_date"] or "", reverse=True)

        latest = samples[-1] if samples else None
        # Last reading this season (Jan-Apr counts as last year's season), for tinting
        # sites that have no current result. None = no result this season, or no limit to compare.
        season_year = today.year if today.month >= 5 else today.year - 1
        last_result = None
        if latest and int(latest["sample_date"][:4]) == season_year and latest["exceeds_bav"] is not None:
            last_result = "over" if latest["exceeds_bav"] == 1 else "ok"
        ind = latest["indicator"] if latest else "enterococcus"
        ent = [(s["sample_date"], s["value"]) for s in samples
               if s["indicator"] == ind and s["value"] is not None]
        gm = standards.rolling_geomean(ent, latest["sample_date"]) if latest and ent else None
        thr = standards.threshold(primary["state"], ind)

        # Which sources contributed what, for the panel's "Sources" line.
        result_sources = {m["source_id"] for m in members if m["samples"]}
        def label_for(m):
            return f"{m['_org']}, via the Water Quality Portal" if m["_org"] else _meta(meta, m["source_id"])["label"]
        src_list = [{"id": m["source_id"], "label": label_for(m),
                     "delayed": _meta(meta, m["source_id"])["delayed"],
                     "role": ("status and results" if m["source_id"] in result_sources and m is status_member
                              else "status" if m is status_member
                              else "results history" if m["source_id"] in result_sources else "location")}
                    for m in members]
        current_results = any(not _meta(meta, s)["delayed"] for s in result_sources)
        status_meta = _meta(meta, status_member["source_id"])
        loc = next((m for m in members if m["latitude"] is not None and m["coord_source"] != "override_approx"),
                   next((m for m in members if m["latitude"] is not None), primary))

        kind = "beach" if any(m["_kind"] == "beach" for m in members) else "waterway"
        link_url = status_member["detail_url"] or primary["detail_url"]
        portal_link = bool(link_url and "waterqualitydata.us" in link_url)
        props = {
            "site_id": primary["site_id"], "name": primary["name"], "state": primary["state"],
            "kind": kind,
            "link_text": ("View this station on the Water Quality Portal" if portal_link
                          else f"Check the official {status_meta['label']} page"),
            "agency": status_member["_org"] or status_meta["label"],
            "county": next((clean_text(m["county"]) for m in members if clean_text(m["county"])), None),
            "water_body": next((m["water_body"] for m in members if m["water_body"]), None),
            "status": status, "source_status": status_member["source_status"],
            "detail_url": link_url,
            "source_label": status_meta["label"],
            # results exist but all come from delayed sources (e.g. the national portal)
            "delayed": bool(result_sources) and not current_results,
            "status_only": not samples and status_meta["status_only"],
            "status_only_note": status_meta["status_only_note"] if not samples else None,
            "sources": src_list,
            "latest_sample_date": latest["sample_date"] if latest else None,
            "latest_value": latest["value"] if latest else None,
            "latest_qualifier": latest["qualifier"] if latest else None,
            "latest_indicator": latest["indicator"] if latest else None,
            "units": latest["units"] if latest else None,
            "geomean_30d": round(gm, 1) if gm else None,
            "bav": thr["single_sample"] if thr else None,
            "samples_count": len(samples),
            "last_result": last_result,
            "location_approx": loc["coord_source"] == "override_approx",
            "linked_site_ids": [m["site_id"] for m in members if m is not primary],
            "history_file": f"sites/{_fname(primary['site_id'])}",
        }
        if len(members) > 1:
            summary["linked_beaches"] += 1
        (out / "sites" / _fname(primary["site_id"])).write_text(json.dumps(
            {**props, "samples": samples, "advisories": advisories}, indent=1))

        st = summary["by_state"].setdefault(primary["state"], {"sites": 0, "beaches": 0, "waterways": 0,
                                                               "statuses": {}, "newest_sample": None})
        st["sites"] += 1
        st["beaches" if kind == "beach" else "waterways"] += 1
        st["statuses"][status] = st["statuses"].get(status, 0) + 1
        if latest and (not st["newest_sample"] or latest["sample_date"] > st["newest_sample"]):
            st["newest_sample"] = latest["sample_date"]

        if loc["latitude"] is None or loc["longitude"] is None:
            unmapped.append({"site_id": primary["site_id"], "name": primary["name"], "state": primary["state"]})
            continue
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [loc["longitude"], loc["latitude"]]},
                         "properties": props})

    summary["unmapped_sites"] = len(unmapped)
    (out / "sites.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "unmapped_sites.json").write_text(json.dumps(unmapped, indent=1))
    return summary
