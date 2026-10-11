"""Generic ArcGIS Online collector.

Maryland and New York State both publish through ArcGIS Online:
  * MD MDE "Current Conditions" embeds web map item 724261e6ba0746bc9fa501f2d4f21de0
    on maryland.maps.arcgis.com. Legend: Not Recommended / Open / Advisory /
    Algae Bloom / Closed / No Data.
  * NYSDOH Coastal Beach Map is ArcGIS Experience item
    301748017d7d40649bc5082fc1c5365e.

Rather than hard-coding layer URLs (which agencies republish without notice),
this collector walks the public item graph at run time:

    Experience --data--> data sources --> Web Map(s)
    Web Map    --data--> operationalLayers[].url --> FeatureServer/MapServer layers
    Layer      --query?where=1=1&f=geojson--> features

and then maps feature attributes to our schema using an optional explicit
field_map (config/sources.json) with fuzzy matching as a fallback.
All original attributes are retained in raw_json.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from ..models import (CollectorResult, Indicator, Sample, Site, StatusReport,
                      normalize_status)
import logging

from .base import Collector, find_key, parse_date, parse_result, slug

log = logging.getLogger(__name__)

POINT_GEOMETRIES = {"esriGeometryPoint", "esriGeometryMultipoint"}

DEFAULT_FIELDS = {
    "id":     ("beach id", "beachid", "station id", "stationid", "site id", "siteid",
               "globalid", "objectid", "fid"),
    "name":   ("beach name", "beachname", "beach", "site name", "station name", "name", "location"),
    "status": ("beach status", "status", "current status", "condition", "advisory status",
               "advisory", "symbol", "notification"),
    "date":   ("sample date", "last sample date", "last sampled", "date sampled",
               "collection date", "last update", "date"),
    "result": ("enterococcus", "enterococci", "entero", "result", "bacteria", "e coli", "ecoli"),
    "county": ("county", "jurisdiction", "locality"),
    "water":  ("water body", "waterbody", "water type"),
    "url":    ("url", "website", "web link", "link", "more info"),
}


class ArcGISCollector(Collector):
    def __init__(self, source_id: str, state: str, agency: str, portal: str,
                 item_id: Optional[str] = None, layer_urls: Optional[list[str]] = None,
                 field_map: Optional[dict[str, str]] = None, homepage_url: str = "",
                 layer_name_filter: Optional[str] = None, **kw):
        super().__init__(**kw)
        self.source_id, self.state, self.agency = source_id, state, agency
        self.portal = portal.rstrip("/")
        self.item_id = item_id
        self.layer_urls = layer_urls or []
        self.field_map = field_map or {}
        self.homepage_url = homepage_url
        self.layer_name_filter = re.compile(layer_name_filter, re.I) if layer_name_filter else None

    # ------------------------------------------------------ discovery
    def _item(self, item_id: str) -> dict:
        return self.get(f"{self.portal}/sharing/rest/content/items/{item_id}",
                        params={"f": "json"}).json()

    def _item_data(self, item_id: str) -> dict:
        return self.get(f"{self.portal}/sharing/rest/content/items/{item_id}/data",
                        params={"f": "json"}).json()

    def resolve_layers(self, item_id: str, depth: int = 0) -> list[str]:
        if depth > 3:
            return []
        item = self._item(item_id)
        typ = item.get("type", "")
        if typ in ("Feature Service", "Map Service"):
            return self._expand_service(item["url"])
        data = self._item_data(item_id)
        if typ == "Web Map":
            return [u for lyr in iter_webmap_layers(data) for u in self._expand_service(lyr)]
        if typ in ("Web Experience", "Dashboard", "Web Mapping Application", "StoryMap"):
            urls: list[str] = []
            for child in find_item_ids(data):
                if child != item_id:
                    urls += self.resolve_layers(child, depth + 1)
            urls += [u for lyr in find_service_urls(data) for u in self._expand_service(lyr)]
            return list(dict.fromkeys(urls))
        return []

    def _expand_service(self, url: str) -> list[str]:
        """A service root -> its layer URLs; a layer URL -> itself."""
        url = url.rstrip("/")
        if re.search(r"/(Feature|Map)Server/\d+$", url):
            return [url]
        if not re.search(r"/(Feature|Map)Server$", url):
            return []
        meta = self.get(url, params={"f": "json"}).json()
        return [f"{url}/{lyr['id']}" for lyr in meta.get("layers", [])
                if not self.layer_name_filter or self.layer_name_filter.search(lyr.get("name", ""))]

    def query_layer(self, layer_url: str, page: int = 1000, max_pages: int = 50) -> list[dict]:
        feats, offset, seen = [], 0, set()
        for _ in range(max_pages):
            data = self.get(f"{layer_url}/query", params={
                "where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson",
                "resultOffset": offset, "resultRecordCount": page}).json()
            batch = data.get("features", [])
            sig = json.dumps(batch[:1], sort_keys=True, default=str)
            if batch and sig in seen:   # server ignored resultOffset; stop
                break
            seen.add(sig)
            feats += batch
            exceeded = data.get("exceededTransferLimit") or \
                data.get("properties", {}).get("exceededTransferLimit")
            if not exceeded or not batch:
                break
            offset += len(batch)
        return feats

    def collect(self) -> CollectorResult:
        res = CollectorResult()
        urls = list(self.layer_urls)
        if self.item_id:
            urls += self.resolve_layers(self.item_id)
        urls = list(dict.fromkeys(urls))
        if not urls:
            res.warnings.append(f"{self.source_id}: no feature layers discovered")
        log.info("%s: %d layer(s) found", self.source_id, len(urls))
        for u in urls:
            name, geom = "", ""
            try:
                meta = self.get(u, params={"f": "json"}).json()
                name, geom = meta.get("name", ""), meta.get("geometryType", "")
            except Exception:
                pass
            # Only monitoring-site points: never ingest boundaries, basemaps or other polygons/lines.
            if geom not in POINT_GEOMETRIES:
                log.info("%s: skipping non-point layer %r (%s, %s)", self.source_id, name,
                         geom or "no geometry", u)
                continue
            if self.layer_name_filter and not (self.layer_name_filter.search(name)
                                               or self.layer_name_filter.search(u)):
                log.info("%s: skipping layer %r (%s)", self.source_id, name, u)
                continue
            log.info("%s: querying layer %r (%s)", self.source_id, name, u)
            try:
                feats = self.query_layer(u)
                if feats:
                    props = feats[0].get("properties") or {}
                    log.info("%s: %d features; fields: %s", self.source_id, len(feats),
                             ", ".join(f"{k}={str(v)[:30]!r}" for k, v in props.items())[:1500])
                res.extend(parse_features(feats, self.state, self.field_map, layer_url=u,
                                          detail_url=self.homepage_url))
            except Exception as e:
                res.warnings.append(f"{self.source_id} layer {u}: {e}")
        return res


# ------------------------------------------------------------ graph walking
def iter_webmap_layers(webmap: dict) -> Iterable[str]:
    def walk(layers):
        for lyr in layers or []:
            if lyr.get("url"):
                yield lyr["url"]
            yield from walk(lyr.get("layers"))
    yield from walk(webmap.get("operationalLayers"))


def find_item_ids(obj: Any) -> list[str]:
    """All 32-hex itemIds referenced anywhere in an app config."""
    out: list[str] = []
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in ("itemid", "webmap", "mapid") and isinstance(v, str) \
                        and re.fullmatch(r"[0-9a-f]{32}", v):
                    out.append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return list(dict.fromkeys(out))


def find_service_urls(obj: Any) -> list[str]:
    out: list[str] = []
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and re.search(r"/(Feature|Map)Server(/\d+)?/?$", o):
            out.append(o)
    walk(obj)
    return list(dict.fromkeys(out))


# ------------------------------------------------------------- parsing
def _centroid(geom: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    if not geom:
        return None, None
    coords = geom.get("coordinates")
    pts: list[list[float]] = []
    def walk(c):
        if isinstance(c, list) and c and isinstance(c[0], (int, float)):
            pts.append(c)
        elif isinstance(c, list):
            for x in c:
                walk(x)
    walk(coords)
    if not pts:
        return None, None
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def _pick(props: dict, field_map: dict, key: str) -> Optional[str]:
    if field_map.get(key) and field_map[key] in props:
        return field_map[key]
    if field_map.get(key) == "":   # explicitly disabled
        return None
    return find_key(props, *DEFAULT_FIELDS[key])


def parse_features(features: list[dict], state: str, field_map: Optional[dict] = None,
                   layer_url: str = "", detail_url: str = "") -> CollectorResult:
    field_map = field_map or {}
    res = CollectorResult()
    for f in features:
        props = f.get("properties") or {}
        gtype = (f.get("geometry") or {}).get("type")
        if not props or gtype not in ("Point", "MultiPoint"):
            continue   # sites must be points
        id_k, name_k = _pick(props, field_map, "id"), _pick(props, field_map, "name")
        name = str(props.get(name_k) or "").strip() if name_k else ""
        sid = str(props.get(id_k)) if id_k and props.get(id_k) not in (None, "") else slug(name)
        if not sid:
            continue
        lat, lon = _centroid(f.get("geometry"))
        county_k, water_k = _pick(props, field_map, "county"), _pick(props, field_map, "water")
        water_text = str(props.get(water_k) or "") if water_k else ""
        url_k = _pick(props, field_map, "url")
        url = str(props.get(url_k) or "").strip() if url_k else ""
        site_url = url if url.startswith(("http://", "https://")) else detail_url
        res.sites.append(Site(
            source_site_id=sid, name=name or sid, state=state, latitude=lat, longitude=lon,
            county=str(props[county_k]).strip() if county_k and props.get(county_k) else None,
            water_body=water_text or None,
            water_type="fresh" if re.search(r"fresh|lake|pond|river", water_text, re.I)
            else "marine" if water_text else "unknown",
            detail_url=site_url, raw={**props, "_layer": layer_url}))

        status_k = _pick(props, field_map, "status")
        if status_k and props.get(status_k) not in (None, ""):
            txt = str(props[status_k])
            res.statuses.append(StatusReport(sid, normalize_status(txt), txt))

        date_k, result_k = _pick(props, field_map, "date"), _pick(props, field_map, "result")
        d = parse_date(props.get(date_k)) if date_k else None
        if d and result_k and props.get(result_k) not in (None, ""):
            value, qual = parse_result(props[result_k])
            if value is not None or qual:
                ind = Indicator.E_COLI if re.search(r"coli", result_k, re.I) else Indicator.ENTEROCOCCUS
                res.samples.append(Sample(source_site_id=sid, sample_date=d, indicator=ind,
                                          value=value, qualifier=qual, raw=props))
    return res
