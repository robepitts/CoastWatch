import io
import json
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from coastwatch.collectors.arcgis import (ArcGISCollector, find_item_ids,
                                          iter_webmap_layers, parse_features)
from coastwatch.collectors.base import parse_date, parse_result
from coastwatch.collectors.delaware import (DelawareCollector, parse_embedded_coords,
                                            parse_site_page, parse_unit_ids)
from coastwatch.collectors.new_jersey import extract_embedded_records, parse_station_records
from coastwatch.collectors.nyc import parse_rows
from coastwatch.collectors.virginia import (find_workbook_links, parse_advisory_xlsx,
                                            parse_monitoring_xlsx)
from coastwatch.export import derive_status, export
from coastwatch.http import Response
from coastwatch.models import Status, normalize_status
from coastwatch.store import Store

FX = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------- helpers
def test_parse_result_variants():
    assert parse_result("52") == (52.0, None)
    assert parse_result("<10") == (10.0, "<")
    assert parse_result(">24,196") == (24196.0, ">")
    assert parse_result("Result below detection limit") == (None, "below_detection")
    assert parse_result(None) == (None, None)


def test_parse_date_variants():
    assert parse_date("09/02/2025") == "2025-09-02"
    assert parse_date("2026-09-29T00:00:00.000") == "2026-09-29"
    assert parse_date(1759363200000) == "2025-10-02"   # ArcGIS epoch ms
    assert parse_date("garbage") is None


@pytest.mark.parametrize("text,expected", [
    ("Not Recommended", Status.ADVISORY), ("Open", Status.OPEN),
    ("Advisory", Status.ADVISORY), ("Algae Bloom", Status.ADVISORY),
    ("Closed", Status.CLOSED), ("No Data", Status.NO_DATA),
    ("Sampling to Resume in Spring", Status.OFF_SEASON),
    ("Resampling Underway", Status.RESAMPLING),
    ("Recreational Water Advisory Lift", Status.OPEN), ("???", Status.UNKNOWN),
])
def test_normalize_status(text, expected):
    assert normalize_status(text) == expected


# ------------------------------------------------------------ Delaware
def test_delaware_open_beach_with_history():
    r = parse_site_page((FX / "de_unit_304.html").read_text(), "304")
    site = r.sites[0]
    assert site.name == "Rehoboth Beach - Rehoboth Avenue"
    assert site.water_body == "Atlantic Ocean" and site.raw["guarded"] is True
    assert site.raw["ytd_geomean"] == 17
    assert len(r.samples) == 5
    big = next(s for s in r.samples if s.sample_date == "2025-07-02")
    assert big.value == 10462
    assert [a.kind for a in r.advisories] == ["lift", "advisory"]
    assert r.statuses[0].status == Status.OPEN


def test_delaware_off_season_active_advisory():
    r = parse_site_page((FX / "de_unit_288.html").read_text(), "288")
    assert r.sites[0].water_body == "Delaware Bay" and r.sites[0].raw["guarded"] is False
    assert r.statuses[0].status == Status.OFF_SEASON
    assert any(a.kind == "off_season" and a.end_date == "2026-05-19" for a in r.advisories)


def test_delaware_discovery_helpers():
    html = ('<a href="RecWaterSitePublic.aspx?UnitID=304">x</a>'
            '<script>var b=[{"UnitID":"296","Name":"N","Latitude":38.61,"Longitude":-75.07}]</script>')
    assert parse_unit_ids(html) == {"304", "296"}
    assert parse_embedded_coords(html) == {"296": {"lat": 38.61, "lon": -75.07}}


# ----------------------------------------------------------------- NYC
def test_nyc_rows():
    r = parse_rows(json.loads((FX / "nyc_rows.json").read_text()))
    assert {s.source_site_id for s in r.sites} == {"manhattan-beach--center", "orchard-beach--right"}
    bdl = next(s for s in r.samples if s.source_site_id.startswith("orchard"))
    assert bdl.value is None and bdl.qualifier == "below_detection"
    assert next(s for s in r.samples if s.sample_date == "2026-09-22").value == 130


# ------------------------------------------------------------ Virginia
def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_virginia_links():
    html = ('<a href="/u/BeachMonitoringData_2026105.xlsx">2026</a>'
            '<a href="/u/BeachActivityReport_2026105.xlsx">2026</a>'
            '<a href="/u/BeachMonitoringData_2025101_508.xlsx">2025</a>')
    links = find_workbook_links(html)
    assert links["monitoring"][2026].endswith("BeachMonitoringData_2026105.xlsx")
    assert links["advisory"][2026].endswith("BeachActivityReport_2026105.xlsx")


def test_virginia_monitoring_with_title_rows_and_positive_longitude():
    data = _xlsx([
        ["Virginia Beach Monitoring Data 2026"], [],
        ["Locality", "Beach Name", "Station ID", "Sample Date", "Enterococci (cfu/100mL)",
         "Latitude", "Longitude"],
        ["Virginia Beach", "Croatan Beach", "VB-01", date(2026, 7, 6), 10, 36.81, 75.96],
        ["Virginia Beach", "Croatan Beach", "VB-01", date(2026, 7, 13), "<10", 36.81, 75.96],
        ["Norfolk", "Ocean View", "NF-03", date(2026, 7, 6), 150, 36.95, -76.25],
    ])
    r = parse_monitoring_xlsx(data)
    assert not r.warnings
    assert {s.source_site_id for s in r.sites} == {"VB-01", "NF-03"}
    vb = next(s for s in r.sites if s.source_site_id == "VB-01")
    assert vb.longitude == -75.96 and vb.county == "Virginia Beach"
    assert len(r.samples) == 3


def test_virginia_advisories():
    data = _xlsx([["Beach Name", "Station ID", "Advisory Type", "Date Issued", "Date Lifted", "Reason"],
                  ["Ocean View", "NF-03", "Swimming Advisory", date(2026, 7, 7), date(2026, 7, 9), "Bacteria"]])
    r = parse_advisory_xlsx(data)
    a = r.advisories[0]
    assert (a.source_site_id, a.kind, a.start_date, a.end_date) == ("NF-03", "advisory", "2026-07-07", "2026-07-09")


# -------------------------------------------------------------- ArcGIS
MD_FEATURES = [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-76.48, 38.97]},
     "properties": {"OBJECTID": 1, "BeachID": "MD-AA-001", "BeachName": "Sandy Point",
                    "County": "Anne Arundel", "Status": "Advisory",
                    "LastSampleDate": 1783382400000, "Enterococcus": 210}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-75.08, 38.33]},
     "properties": {"OBJECTID": 2, "BeachID": "MD-WO-010", "BeachName": "Ocean City 10th St",
                    "County": "Worcester", "Status": "Open", "LastSampleDate": None,
                    "Enterococcus": None}},
]


def test_arcgis_parse_features():
    r = parse_features(MD_FEATURES, "MD")
    assert [s.source_site_id for s in r.sites] == ["MD-AA-001", "MD-WO-010"]
    assert r.sites[0].latitude == 38.97 and r.sites[0].county == "Anne Arundel"
    assert [x.status for x in r.statuses] == [Status.ADVISORY, Status.OPEN]
    assert len(r.samples) == 1 and r.samples[0].value == 210


def test_arcgis_graph_helpers():
    webmap = {"operationalLayers": [
        {"url": "https://x/arcgis/rest/services/Beaches/FeatureServer/0"},
        {"layerType": "GroupLayer", "layers": [{"url": "https://x/FeatureServer/1"}]}]}
    assert list(iter_webmap_layers(webmap)) == [
        "https://x/arcgis/rest/services/Beaches/FeatureServer/0", "https://x/FeatureServer/1"]
    exb = {"dataSources": {"ds1": {"itemId": "a" * 32, "type": "WEB_MAP"}}}
    assert find_item_ids(exb) == ["a" * 32]


class FakeClient:
    """Maps URL prefixes to canned responses; records calls."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None):
        self.calls.append((url, params))
        for key in sorted(self.routes, key=len, reverse=True):
            if url.startswith(key):
                body = self.routes[key]
                if callable(body):
                    body = body(url, params)
                if not isinstance(body, (bytes, str)):
                    body = json.dumps(body)
                if isinstance(body, str):
                    body = body.encode()
                return Response(url, 200, "", body)
        raise RuntimeError(f"no route for {url}")


def test_arcgis_resolves_experience_to_features():
    P = "https://www.arcgis.com/sharing/rest/content/items/"
    exp, wm = "e" * 32, "f" * 32
    svc = "https://services.arcgis.com/abc/arcgis/rest/services/NYBeaches/FeatureServer"
    client = FakeClient({
        P + exp + "/data": {"dataSources": {"d": {"itemId": wm}}},
        P + exp: {"type": "Web Experience"},
        P + wm + "/data": {"operationalLayers": [{"url": svc}]},
        P + wm: {"type": "Web Map"},
        svc + "/0/query": {"type": "FeatureCollection", "features": MD_FEATURES},
        svc + "/0": {"name": "Coastal Beaches", "geometryType": "esriGeometryPoint"},
        svc: {"layers": [{"id": 0, "name": "Coastal Beaches"}, {"id": 1, "name": "Counties"}]},
    })
    col = ArcGISCollector("ny_doh", "NY", "NYSDOH", "https://www.arcgis.com", item_id=exp,
                          layer_name_filter="beach", client=client)
    r = col.collect()
    assert not r.warnings, r.warnings
    assert len(r.sites) == 2
    assert not any(u.endswith("/1/query") for u, _ in client.calls)  # filtered out


# ----------------------------------------------------------- New Jersey
def test_nj_next_data_extraction():
    payload = {"props": {"pageProps": {"stations": [
        {"stationId": "OC14", "name": "Seaside Park - 5th Ave", "lat": 39.93, "lng": -74.07,
         "county": "Ocean", "status": "Open", "sampleDate": "2026-08-31", "enterococci": "<10"},
        {"stationId": "MO22", "name": "Asbury Park - 4th Ave", "lat": 40.22, "lng": -73.99,
         "county": "Monmouth", "status": "Advisory", "sampleDate": "2026-08-31", "enterococci": "210"},
    ]}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
    r = parse_station_records(extract_embedded_records(html))
    assert {s.source_site_id for s in r.sites} == {"OC14", "MO22"}
    assert [x.status for x in r.statuses] == [Status.OPEN, Status.ADVISORY]
    assert sorted(s.value for s in r.samples) == [10.0, 210.0]


# --------------------------------------------------------------- store
def test_store_idempotent_and_exceedance(tmp_path):
    store = Store(tmp_path / "t.db")
    store.register_source("de_dnrec", "DE", "DNREC")
    r = parse_site_page((FX / "de_unit_304.html").read_text(), "304")
    first = store.save("de_dnrec", "DE", r)
    again = store.save("de_dnrec", "DE", r)
    assert first["samples_new"] == 5 and again["samples_new"] == 0
    assert again["advisories_new"] == 0
    exc = store.query("SELECT sample_date FROM samples WHERE exceeds_bav=1")
    assert [e["sample_date"] for e in exc] == ["2025-07-02"]
    # status history accumulates, one observation per run
    assert len(store.query("SELECT * FROM status_observations")) == 2


def test_store_placeholder_site_for_orphan_advisory(tmp_path):
    store = Store(tmp_path / "t.db")
    store.register_source("va_vdh", "VA", "VDH")
    data = _xlsx([["Beach Name", "Station ID", "Advisory Type", "Date Issued"],
                  ["Ghost Beach", "GH-9", "Advisory", date(2026, 7, 1)]])
    store.save("va_vdh", "VA", parse_advisory_xlsx(data))
    assert store.query("SELECT name FROM sites WHERE site_id='va_vdh:GH-9'")[0]["name"] == "GH-9"


def test_coordinates_not_clobbered_and_overrides(tmp_path):
    store = Store(tmp_path / "t.db")
    store.register_source("nyc_doh", "NY", "DOHMH")
    rows = json.loads((FX / "nyc_rows.json").read_text())
    store.save("nyc_doh", "NY", parse_rows(rows))
    csv = tmp_path / "o.csv"
    csv.write_text("site_id,name,state,latitude,longitude\n"
                   "nyc_doh:manhattan-beach--center,Manhattan Beach,NY,40.5770,-73.9420\n")
    assert store.apply_coordinate_overrides(csv) == 1
    store.save("nyc_doh", "NY", parse_rows(rows))      # source has no coords...
    row = store.query("SELECT latitude, coord_source FROM sites "
                      "WHERE site_id='nyc_doh:manhattan-beach--center'")[0]
    assert row["latitude"] == 40.577 and row["coord_source"] == "override"  # ...kept


# -------------------------------------------------------------- export
def test_derive_status():
    t = date(2026, 7, 20)
    assert derive_status("closed", "2026-07-19", 0, t) == "closed"
    assert derive_status(None, "2026-07-19", 1, t) == "advisory"
    assert derive_status(None, "2026-07-19", 0, t) == "open"
    assert derive_status(None, "2026-06-01", 0, t) == "no_data"
    assert derive_status(None, "2026-09-01", 0, date(2026, 10, 11)) == "off_season"
    assert derive_status(None, None, None, t) == "no_data"


def test_export_files(tmp_path):
    store = Store(tmp_path / "t.db")
    store.register_source("md_mde", "MD", "MDE")
    store.save("md_mde", "MD", parse_features(MD_FEATURES, "MD"))
    summary = export(store, tmp_path / "out", today=date(2026, 7, 20))
    gj = json.loads((tmp_path / "out" / "sites.geojson").read_text())
    assert len(gj["features"]) == 2
    props = {f["properties"]["name"]: f["properties"] for f in gj["features"]}
    assert props["Sandy Point"]["status"] == "advisory"
    assert props["Sandy Point"]["bav"] == 104
    hist = json.loads((tmp_path / "out" / props["Sandy Point"]["history_file"]).read_text())
    assert hist["samples"][0]["value"] == 210
    assert summary["by_state"]["MD"]["sites"] == 2


# --------------------------------------------------------- end to end
def test_end_to_end_delaware_with_fake_network(tmp_path):
    base = "https://recwaters.dnrec.delaware.gov"
    client = FakeClient({
        base + "/RecWaterSitePublic.aspx?UnitID=304": (FX / "de_unit_304.html").read_text(),
        base + "/RecWaterSitePublic.aspx?UnitID=288": (FX / "de_unit_288.html").read_text(),
        base + "/": '<a href="RecWaterSitePublic.aspx?UnitID=288">Broadkill</a>',
    })
    store = Store(tmp_path / "t.db")
    col = DelawareCollector(seed_unit_ids=["304"], client=client, store=store)
    r = col.collect()
    counts = store.save("de_dnrec", "DE", r)
    assert counts["sites_seen"] == 2 and counts["samples_new"] == 7
    assert len(store.query("SELECT * FROM raw_snapshots")) == 3  # every payload archived


def test_probe_with_fake_network(monkeypatch):
    import coastwatch.probe as probe_mod
    base = "https://recwaters.dnrec.delaware.gov"
    fake = FakeClient({base + "/": '<a href="X.aspx?UnitID=304">a</a><script src="/app.js"></script>',
                       "https://njbeaches.org/": "<html><script>self.__next_f.push([1,\"x\"])</script></html>"})
    monkeypatch.setattr(probe_mod, "PoliteClient", lambda **kw: fake)
    rep = probe_mod.probe(["de_dnrec", "nj_dep"])
    assert rep["de_dnrec"]["discovered_unit_ids"] == ["304"]
    assert rep["de_dnrec"]["script_srcs"] == ["/app.js"]
    assert rep["nj_dep"]["ok"] and rep["nj_dep"]["has_rsc_payload"]


# ------------------------------------------------- regressions from first live run
def test_http_does_not_retry_404(monkeypatch):
    from coastwatch import http as h
    calls = []
    class R:
        status_code, url, headers, content = 404, "https://x/y", {}, b""
    client = h.PoliteClient(min_interval_s=0, respect_robots=False)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: calls.append(1) or R())
    with pytest.raises(h.ClientError):
        client.get("https://x/y")
    assert len(calls) == 1


def test_delaware_stops_after_first_404(tmp_path):
    from coastwatch.http import ClientError
    class NotFound(FakeClient):
        def get(self, url, params=None):
            self.calls.append((url, params))
            if "RecWaterSitePublic" in url:
                raise ClientError(url, 404)
            return Response(url, 200, "", b"<html></html>")
    client = NotFound({})
    col = DelawareCollector(seed_unit_ids=["288", "292", "296", "298"], client=client)
    r = col.collect()
    assert sum("RecWaterSitePublic" in u for u, _ in client.calls) == 1
    assert any("redesigned" in w for w in r.warnings)


def test_arcgis_stops_when_server_ignores_offset():
    page = {"type": "FeatureCollection", "exceededTransferLimit": True, "features": MD_FEATURES}
    client = FakeClient({"https://x/FeatureServer/0/query": page})
    col = ArcGISCollector("md_mde", "MD", "MDE", "https://p", client=client)
    feats = col.query_layer("https://x/FeatureServer/0")
    assert len(feats) == 2 and len(client.calls) == 2


def test_arcgis_name_filter_applies_to_webmap_layers():
    P = "https://www.arcgis.com/sharing/rest/content/items/"
    wm = "b" * 32
    beach = "https://x/arcgis/rest/services/Health/FeatureServer/3"
    chem = "https://y/arcgis/rest/services/parquet_portal_chemistry/FeatureServer/12"
    client = FakeClient({
        P + wm + "/data": {"operationalLayers": [{"url": beach}, {"url": chem}]},
        P + wm: {"type": "Web Map"},
        beach + "/query": {"type": "FeatureCollection", "features": MD_FEATURES},
        beach: {"name": "Coastal Beaches", "geometryType": "esriGeometryPoint"},
        chem: {"name": "Chemistry Results", "geometryType": "esriGeometryPoint"},
    })
    col = ArcGISCollector("ny_doh", "NY", "NYSDOH", "https://www.arcgis.com", item_id=wm,
                          layer_name_filter="beach", client=client)
    r = col.collect()
    assert len(r.sites) == 2
    assert not any(u.startswith(chem + "/query") for u, _ in client.calls)



def test_arcgis_skips_polygon_layers_even_if_named_beach():
    P = "https://www.arcgis.com/sharing/rest/content/items/"
    wm = "c" * 32
    pts = "https://x/arcgis/rest/services/Beaches/FeatureServer/0"
    poly = "https://x/arcgis/rest/services/Beaches/FeatureServer/1"
    client = FakeClient({
        P + wm + "/data": {"operationalLayers": [{"url": pts}, {"url": poly}]},
        P + wm: {"type": "Web Map"},
        pts + "/query": {"type": "FeatureCollection", "features": MD_FEATURES},
        pts: {"name": "Beach Sites", "geometryType": "esriGeometryPoint"},
        poly: {"name": "Beach Areas", "geometryType": "esriGeometryPolygon"},
    })
    r = ArcGISCollector("md_mde", "MD", "MDE", "https://www.arcgis.com", item_id=wm,
                        layer_name_filter="beach", client=client).collect()
    assert len(r.sites) == 2
    assert not any(u.startswith(poly + "/query") for u, _ in client.calls)


def test_parse_features_ignores_non_point_geometry():
    poly = {"type": "Feature", "properties": {"BeachName": "Town line"},
            "geometry": {"type": "Polygon", "coordinates": [[[-75, 38], [-75, 39], [-74, 39], [-75, 38]]]}}
    assert parse_features([poly] + MD_FEATURES, "MD").sites.__len__() == 2



def test_arcgis_real_maryland_fields():
    """Field layout observed on MDE's WSA_Healthy_Beaches_Alerts layer (Oct 2026)."""
    f = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-76.51099, 38.989919]},
         "properties": {"OBJECTID": 1, "Unique_ID": 222, "County": "Anne Arundel County",
                        "Beach_Name": "Admiral Heights", "Description": "Private access community beach",
                        "URL": "https://mde.geodata.md.gov/Beaches/222", "Latitude": 38.989919,
                        "Longitude": -76.51099, "Status": "No Data", "Update_Datetime": 1787740104000,
                        "GlobalID": "{6B85AA79-BACB-40AC-BD96-1DA60}"}}
    r = parse_features([f], "MD", detail_url="https://mde.maryland.gov/x")
    s = r.sites[0]
    assert s.name == "Admiral Heights" and s.county == "Anne Arundel County"
    assert s.detail_url == "https://mde.geodata.md.gov/Beaches/222"
    assert r.statuses[0].status == Status.NO_DATA and not r.samples



def test_nice_name_and_clean_text():
    from coastwatch.collectors.base import clean_text, nice_name
    assert nice_name("ASSATEAGUE ISLAND, TOM'S COVE NORTH") == "Assateague Island, Tom's Cove North"
    assert nice_name("10TH VIEW, BEHIND QUALITY INN,1010 W OCEAN VIEW AVE") == \
        "10th View, Behind Quality Inn, 1010 W Ocean View Ave"
    assert nice_name("WOLFE'S POND BEACH") == "Wolfe's Pond Beach"
    assert nice_name("CONEY ISLAND WEST 28TH - WEST 37TH") == "Coney Island West 28th - West 37th"
    assert nice_name("Sandy Point State Park") == "Sandy Point State Park"
    assert clean_text("none") is None and clean_text(" N/A ") is None and clean_text("Norfolk") == "Norfolk"
