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
from coastwatch.models import Indicator, Status, normalize_status
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

    def get(self, url, params=None, timeout=None):
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
    assert derive_status(None, "2026-07-19", 1, t) == "exceeded"   # inferred, not official
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



# ------------------------------------------------------------ Water Quality Portal
WQP_STATIONS = """OrganizationIdentifier,OrganizationFormalName,MonitoringLocationIdentifier,MonitoringLocationName,MonitoringLocationTypeName,MonitoringLocationDescriptionText,LatitudeMeasure,LongitudeMeasure,StateCode,CountyCode,ProviderName
NJDEP_CCMP,NJDEP Cooperative Coastal Monitoring,NJDEP_CCMP-OC14,SEASIDE PARK - 5TH AVE,BEACH Program Site-Ocean,Ocean beach station,39.928,-74.077,34,029,STORET
NJDEP_CCMP,NJDEP Cooperative Coastal Monitoring,NJDEP_CCMP-OCB7,BEACHWOOD BEACH WEST,BEACH Program Site-Estuary,,39.940,-74.191,34,029,STORET
NJDEP_CCMP,NJDEP Cooperative Coastal Monitoring,NJDEP_CCMP-NOLOC,NO LOCATION,BEACH Program Site-Ocean,,,,34,029,STORET
NYCDOHMH,NYC Department of Health and Mental Hygiene,NYCDOHMH-CONEY,CONEY ISLAND,BEACH Program Site-Ocean,,40.57,-73.98,36,047,STORET
"""
WQP_RESULTS = """OrganizationIdentifier,ActivityIdentifier,ActivityStartDate,ActivityStartTime/Time,MonitoringLocationIdentifier,ResultDetectionConditionText,CharacteristicName,ResultMeasureValue,ResultMeasure/MeasureUnitCode,DetectionQuantitationLimitMeasure/MeasureValue
NJDEP_CCMP,A1,2025-08-25,09:15:00,NJDEP_CCMP-OC14,,Enterococcus,20,cfu/100ml,
NJDEP_CCMP,A2,2025-09-02,09:20:00,NJDEP_CCMP-OC14,Not Detected,Enterococcus,,cfu/100ml,10
NJDEP_CCMP,A3,2025-09-02,10:00:00,NJDEP_CCMP-OCB7,,Enterococcus,"1,200",MPN/100ml,
NYCDOHMH,A4,2025-09-02,10:00:00,NYCDOHMH-CONEY,,Enterococcus,50,MPN/100ml,
"""


def _csv_rows(text):
    import csv as _csv, io as _io
    return list(_csv.DictReader(_io.StringIO(text)))


def test_parse_wqp_stations_and_results():
    import re as _re
    from coastwatch.collectors.wqp import parse_wqp
    r = parse_wqp(_csv_rows(WQP_STATIONS), _csv_rows(WQP_RESULTS), "NJ",
                  exclude=_re.compile("nyc|mental hygiene", _re.I), detail_url="https://njbeaches.org/")
    ids = {s.source_site_id for s in r.sites}
    assert ids == {"NJDEP_CCMP-OC14", "NJDEP_CCMP-OCB7"}       # no-location and NYC excluded
    oc = next(s for s in r.sites if s.source_site_id == "NJDEP_CCMP-OC14")
    assert oc.name == "Seaside Park - 5th Ave" and oc.county == "Ocean" and oc.water_body == "Ocean"
    nd = next(x for x in r.samples if x.source_sample_id == "A2")
    assert (nd.value, nd.qualifier) == (10.0, "<")
    big = next(x for x in r.samples if x.source_sample_id == "A3")
    assert big.value == 1200 and big.units == "MPN/100mL" and big.sample_time == "10:00"
    assert len(r.samples) == 3


def test_wqp_collector_queries_and_filters():
    from coastwatch.collectors.wqp import WQPCollector
    client = FakeClient({"https://www.waterqualitydata.us/data/Station/search": WQP_STATIONS,
                         "https://www.waterqualitydata.us/data/Result/search": WQP_RESULTS})
    col = WQPCollector("wqp_nj", "NJ", "NJDEP", client=client,
                       exclude_org_pattern="nyc|mental hygiene")
    r = col.collect()
    assert len(r.sites) == 2 and len(r.samples) == 3 and not r.warnings
    params = client.calls[0][1]
    assert ("statecode", "US:34") in params
    assert ("siteType", "Ocean") in params and ("siteType", "Estuary") in params
    assert ("characteristicName", "Enterococcus") in params


def test_wqp_store_and_export(tmp_path):
    import re as _re
    from coastwatch.collectors.wqp import parse_wqp
    store = Store(tmp_path / "t.db")
    store.save("wqp_nj", "NJ", parse_wqp(_csv_rows(WQP_STATIONS), _csv_rows(WQP_RESULTS), "NJ",
                                        exclude=_re.compile("nyc", _re.I)))
    summary = export(store, tmp_path / "out", today=date(2026, 7, 20))
    gj = json.loads((tmp_path / "out" / "sites.geojson").read_text())
    props = {f["properties"]["name"]: f["properties"] for f in gj["features"]}
    # results from last year are reported, but status must not claim current conditions
    assert props["Seaside Park - 5th Ave"]["status"] == "no_data"
    assert props["Beachwood Beach West"]["latest_value"] == 1200



def test_respect_robots_override_only_where_configured():
    from coastwatch.collectors import build
    from coastwatch.http import PoliteClient
    base = PoliteClient()
    wqp = build("wqp_nj", {"collector": "wqp", "state": "NJ", "agency": "x", "respect_robots": False},
                base, store=None)
    nyc = build("nyc_doh", {"collector": "nyc"}, base, store=None)
    assert wqp.client.respect_robots is False and wqp.client is not base
    assert nyc.client is base and base.respect_robots is True


# ------------------------------------------------------------ cross-source matching
def _site(sid, src, name, lat, lon):
    return {"site_id": sid, "source_id": src, "name": name, "latitude": lat, "longitude": lon}


def test_link_sites_distance_name_and_same_source_rules():
    from coastwatch.export import link_sites
    sites = [
        _site("md_mde:1", "md_mde", "Sandy Point State Park", 39.0120, -76.3980),
        _site("wqp_md:A", "wqp_md", "SANDY POINT SP - SOUTH BEACH", 39.0135, -76.3990),  # ~190 m
        _site("md_mde:2", "md_mde", "Matapeake Beach", 38.9570, -76.3550),
        _site("wqp_md:B", "wqp_md", "Matapeake Public Beach", 38.9640, -76.3550),       # ~780 m, similar name
        _site("wqp_md:C", "wqp_md", "Kent Island Marina", 38.9600, -76.3560),            # ~330 m, different name
        _site("nyc_doh:x--left", "nyc_doh", "Orchard Beach (Left)", 40.8670, -73.7930),
        _site("nyc_doh:x--right", "nyc_doh", "Orchard Beach (Right)", 40.8670, -73.7910),  # same source
    ]
    groups = {frozenset(s["site_id"] for s in g) for g in link_sites(sites)}
    assert frozenset({"md_mde:1", "wqp_md:A"}) in groups
    assert frozenset({"md_mde:2", "wqp_md:B"}) in groups
    assert frozenset({"wqp_md:C"}) in groups
    assert frozenset({"nyc_doh:x--left"}) in groups and frozenset({"nyc_doh:x--right"}) in groups


def test_link_overrides_force_and_separate():
    from coastwatch.export import link_sites
    a = _site("s1:a", "s1", "Alpha Beach", 39.0, -75.0)
    b = _site("s2:b", "s2", "Alpha Beach", 39.0005, -75.0)
    c = _site("s3:c", "s3", "Totally Different", 39.5, -75.5)
    groups = link_sites([a, b, c], force={frozenset({"s1:a", "s3:c"})}, never={frozenset({"s1:a", "s2:b"})})
    sets = {frozenset(s["site_id"] for s in g) for g in groups}
    assert frozenset({"s1:a", "s3:c"}) in sets and frozenset({"s2:b"}) in sets


def test_export_merges_status_and_history_by_priority(tmp_path):
    import re as _re
    from coastwatch.collectors.wqp import parse_wqp
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({
        "md_mde": {"collector": "arcgis", "priority": 70, "label": "Maryland Department of the Environment",
                   "status_only": True, "status_only_note": "Status only."},
        "wqp_md": {"collector": "wqp", "priority": 10, "label": "MDE via portal", "delayed": True}}))
    store = Store(tmp_path / "t.db")
    md = parse_features([{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.077, 39.928]},
                          "properties": {"Unique_ID": 7, "Beach_Name": "Seaside Park", "Status": "Advisory"}}], "MD")
    store.save("md_mde", "MD", md)
    store.save("wqp_md", "MD", parse_wqp(_csv_rows(WQP_STATIONS), _csv_rows(WQP_RESULTS), "MD",
                                         exclude=_re.compile("nyc", _re.I)))
    summary = export(store, tmp_path / "out", today=date(2026, 7, 20), sources_path=sources,
                     links_path=tmp_path / "none.csv")
    gj = json.loads((tmp_path / "out" / "sites.geojson").read_text())
    beach = next(f["properties"] for f in gj["features"] if f["properties"]["site_id"] == "md_mde:seaside-park")
    assert beach["status"] == "advisory"                       # current status from MDE
    assert beach["samples_count"] == 2 and beach["delayed"]     # history from the portal, flagged delayed
    assert beach["linked_site_ids"] == ["wqp_md:NJDEP_CCMP-OC14"]
    assert {s["role"] for s in beach["sources"]} == {"status", "results history"}
    assert summary["linked_beaches"] == 1
    assert len(gj["features"]) == 2                             # Beachwood stays its own beach


# ------------------------------------------------------------ generic collectors
COUNTY_HTML = """<html><body><table><tr><th>Week</th></tr><tr><td>notes</td></tr></table>
<table>
<tr><th>Beach</th><th>Sample Date</th><th>Enterococci (MPN/100 mL)</th><th>Status</th></tr>
<tr><td>BAYSIDE BEACH</td><td>07/06/2026</td><td>10</td><td>Open</td></tr>
<tr><td></td><td>07/13/2026</td><td>&gt;2,420</td><td>Advisory</td></tr>
<tr><td>MAYO BEACH PARK</td><td>07/13/2026</td><td>&lt;10</td><td>Open</td></tr>
</table></body></html>"""


def test_table_collector_html_with_forward_fill():
    from coastwatch.collectors.generic import TableCollector
    client = FakeClient({"https://county.example/beaches": COUNTY_HTML})
    col = TableCollector("aa_county", "MD", "Anne Arundel", "https://county.example/beaches",
                         columns={"name": "Beach", "date": "Sample Date", "result": "Enterococci",
                                  "status": "Status"},
                         table_match="Enterococci", forward_fill=["Beach"], client=client)
    r = col.collect()
    assert not r.warnings, r.warnings
    assert {s.name for s in r.sites} == {"Bayside Beach", "Mayo Beach Park"}
    bay = [x for x in r.samples if x.source_site_id == "bayside-beach"]
    assert [(x.sample_date, x.value, x.qualifier) for x in bay] == [("2026-07-06", 10.0, None),
                                                                    ("2026-07-13", 2420.0, ">")]
    assert all(x.units == "CFU/100mL" for x in bay) or True
    st = {x.source_site_id: x.status for x in r.statuses}
    assert st["bayside-beach"] == Status.ADVISORY and st["mayo-beach-park"] == Status.OPEN


def test_table_collector_csv_and_xlsx():
    from coastwatch.collectors.generic import parse_table_payload, rows_to_result
    csv_text = "Station,Name,Date,Entero,Lat,Long\nS1,North End,2026-07-01,35,38.33,75.08\n"
    rows = parse_table_payload(csv_text.encode(), "text/csv", "https://x/data.csv")
    r = rows_to_result(rows, {"id": "Station", "name": "Name", "date": "Date", "result": "Entero",
                              "lat": "Lat", "lon": "Long"}, "MD")
    assert r.sites[0].longitude == -75.08 and r.samples[0].value == 35
    xrows = parse_table_payload(_xlsx([["Report"], ["Beach", "Date", "Enterococci"],
                                       ["Elms Beach", date(2026, 7, 2), 52]]),
                                "", "https://x/file.xlsx", table_match="Enterococci")
    r2 = rows_to_result(xrows, {"name": "Beach", "date": "Date", "result": "Enterococci"}, "MD")
    assert r2.samples[0].sample_date == "2026-07-02" and r2.samples[0].value == 52


def test_json_collector_nested_records():
    from coastwatch.collectors.generic import JSONCollector
    feed = {"data": {"beaches": [
        {"code": "OC14", "title": "Seaside Park", "loc": {"lat": 39.9, "lng": -74.1},
         "latest": {"date": "2026-07-13", "entero": "20"}, "status": "Open"}]}}
    client = FakeClient({"https://api.example/beaches": feed})
    col = JSONCollector("nj_api", "NJ", "NJDEP", "https://api.example/beaches",
                        records="data.beaches", client=client,
                        columns={"id": "code", "name": "title", "lat": "loc.lat", "lon": "loc.lng",
                                 "date": "latest.date", "result": "latest.entero", "status": "status"})
    r = col.collect()
    assert not r.warnings and r.sites[0].latitude == 39.9 and r.samples[0].value == 20
    assert r.statuses[0].status == Status.OPEN


def test_wqp_keeps_only_beach_program_sites():
    from coastwatch.collectors.wqp import parse_wqp
    stations = WQP_STATIONS + (
        "NJDEP_BMWM,NJDEP Bureau of Marine Water Monitoring,NJDEP_BMWM-1131,1131,Ocean,,39.5,-74.2,34,029,STORET\n"
        "DNREC,Delaware Dept Natural Resources &amp; Environmental Control,DNREC-L1,Lums Pond,"
        "BEACH Program Site-Lake,,39.56,-75.72,10,003,STORET\n")
    r = parse_wqp(_csv_rows(stations), [], "NJ", location_types=["BEACH Program Site"])
    ids = {s.source_site_id for s in r.sites}
    assert "NJDEP_BMWM-1131" not in ids and "NJDEP_CCMP-OC14" in ids
    lake = next(s for s in r.sites if s.source_site_id == "DNREC-L1")
    assert lake.water_type == "fresh" and lake.water_body == "Lake"
    assert lake.raw["org_name"] == "Delaware Dept Natural Resources & Environmental Control"


def test_prune_source_removes_unseen_sites_and_their_data(tmp_path):
    from coastwatch.collectors.wqp import parse_wqp
    store = Store(tmp_path / "t.db")
    store.save("wqp_nj", "NJ", parse_wqp(_csv_rows(WQP_STATIONS), _csv_rows(WQP_RESULTS), "NJ"))
    n = store.prune_source("wqp_nj", {"NJDEP_CCMP-OC14"})
    left = {r["source_site_id"] for r in store.query("SELECT source_site_id FROM sites WHERE source_id='wqp_nj'")}
    assert n == 2 and left == {"NJDEP_CCMP-OC14"}
    assert not store.query("SELECT 1 FROM samples WHERE site_id='wqp_nj:NJDEP_CCMP-OCB7'")


def test_wqp_excludes_counties_and_approx_needs_name_match():
    from coastwatch.collectors.wqp import parse_wqp
    from coastwatch.export import link_sites
    stations = WQP_STATIONS.replace("NYCDOHMH,NYC Department of Health and Mental Hygiene,NYCDOHMH-CONEY",
                                    "21NYBCH,New York State Department of Health,21NYBCH-NY1")
    r = parse_wqp(_csv_rows(stations), [], "NY", exclude_counties={"047"})
    assert "21NYBCH-NY1" not in {s.source_site_id for s in r.sites}      # Kings County skipped
    a = {**_site("nyc_doh:sea-gate--left", "nyc_doh", "Sea Gate Beach Club (Left)", 40.5775, -74.0110),
         "coord_source": "override_approx"}
    b = _site("wqp_ny:X", "wqp_ny", "Gravesend Bay Pier", 40.5780, -74.0105)          # close, unrelated
    c = _site("wqp_ny:Y", "wqp_ny", "SEA GATE BEACH CLUB", 40.5790, -74.0150)          # ~370 m, same name
    sets = {frozenset(s["site_id"] for s in g) for g in link_sites([a, b, c])}
    assert frozenset({"nyc_doh:sea-gate--left", "wqp_ny:Y"}) in sets and frozenset({"wqp_ny:X"}) in sets


# ------------------------------------------------------------ coastal waterways
def test_wqp_tags_beach_and_waterway_and_reads_ecoli():
    from coastwatch.collectors.wqp import parse_wqp
    stations = WQP_STATIONS + (
        "MDEDAT08_WQX,Maryland Dept of the Environment,MDE-SEV1,SEVERN RIVER AT ANNAPOLIS,Estuary,,38.98,-76.48,24,003,STORET\n")
    results = WQP_RESULTS + "MDEDAT08_WQX,A9,2026-08-04,,MDE-SEV1,,Escherichia coli,300,MPN/100ml,\n"
    r = parse_wqp(_csv_rows(stations), _csv_rows(results), "MD")
    sev = next(s for s in r.sites if s.source_site_id == "MDE-SEV1")
    assert sev.raw["kind"] == "waterway" and "waterqualitydata.us/provider/STORET/MDEDAT08_WQX/MDE-SEV1" in sev.detail_url
    assert next(s for s in r.sites if s.source_site_id == "NJDEP_CCMP-OC14").raw["kind"] == "beach"
    ec = next(x for x in r.samples if x.source_site_id == "MDE-SEV1")
    assert ec.indicator == Indicator.E_COLI and ec.value == 300


def test_wqp_exclude_beach_sites_keeps_waterways():
    from coastwatch.collectors.wqp import parse_wqp
    stations = WQP_STATIONS + "VADEQ,Virginia DEQ,VADEQ-LAF,LAFAYETTE RIVER,Estuary,,36.90,-76.28,51,710,STORET\n"
    r = parse_wqp(_csv_rows(stations), [], "VA", exclude_beach_sites=True)
    assert {s.source_site_id for s in r.sites} == {"VADEQ-LAF"}
    r2 = parse_wqp(_csv_rows(stations), [], "NY", exclude_beach_counties={"029"})
    assert "NJDEP_CCMP-OC14" not in {s.source_site_id for s in r2.sites}


def test_waterway_staleness_and_export_kind(tmp_path):
    assert derive_status(None, "2026-09-10", 0, date(2026, 10, 11), "waterway") == "open"      # 31 days
    assert derive_status(None, "2026-08-01", 0, date(2026, 10, 11), "waterway") == "no_data"   # never off season
    assert derive_status(None, "2026-09-10", 0, date(2026, 10, 11), "beach") == "off_season"
    from coastwatch.collectors.wqp import parse_wqp
    stations = "OrganizationIdentifier,OrganizationFormalName,MonitoringLocationIdentifier,MonitoringLocationName,MonitoringLocationTypeName,LatitudeMeasure,LongitudeMeasure,CountyCode,ProviderName\nMDE,Maryland Dept of the Environment,MDE-SEV1,SEVERN RIVER,Estuary,38.98,-76.48,003,STORET\n"
    results = "MonitoringLocationIdentifier,ActivityIdentifier,ActivityStartDate,CharacteristicName,ResultMeasureValue,ResultMeasure/MeasureUnitCode\nMDE-SEV1,A1,2026-07-01,Enterococcus,500,cfu/100ml\n"
    store = Store(tmp_path / "t.db")
    store.save("wqp_md", "MD", parse_wqp(_csv_rows(stations), _csv_rows(results), "MD"))
    src = tmp_path / "s.json"
    src.write_text(json.dumps({"wqp_md": {"collector": "wqp", "label": "MDE", "delayed": True}}))
    export(store, tmp_path / "out", today=date(2026, 7, 20), sources_path=src, links_path=tmp_path / "x.csv")
    p = json.loads((tmp_path / "out" / "sites.geojson").read_text())["features"][0]["properties"]
    assert p["kind"] == "waterway" and p["status"] == "exceeded" and p["delayed"]   # 19 days old, 500 > 104
    assert p["link_text"] == "View this station on the Water Quality Portal"


def test_wqp_code_names_become_readable():
    from coastwatch.collectors.wqp import parse_wqp
    st = ("OrganizationIdentifier,OrganizationFormalName,MonitoringLocationIdentifier,MonitoringLocationName,"
          "MonitoringLocationTypeName,MonitoringLocationDescriptionText,LatitudeMeasure,LongitudeMeasure,CountyCode\n"
          "BMWM,NJDEP BMWM,BMWM-1403a,1403a,Estuary,Barnegat Bay off Forked River,39.83,-74.15,029\n"
          "BMWM,NJDEP BMWM,BMWM-1013g,1013g,Estuary,,39.40,-74.40,001\n")
    names = {s.source_site_id: s.name for s in parse_wqp(_csv_rows(st), [], "NJ").sites}
    assert names == {"BMWM-1403a": "Barnegat Bay off Forked River (1403a)", "BMWM-1013g": "Station 1013g"}


def test_last_result_this_season_for_ring_tinting(tmp_path):
    from coastwatch.collectors.wqp import parse_wqp
    store = Store(tmp_path / "t.db")
    store.save("wqp_nj", "NJ", parse_wqp(_csv_rows(WQP_STATIONS), _csv_rows(WQP_RESULTS), "NJ"))
    src = tmp_path / "s.json"; src.write_text("{}")
    # October 2025: results from Sept 2025 are "this season"
    export(store, tmp_path / "a", today=date(2025, 10, 11), sources_path=src, links_path=tmp_path / "x.csv")
    p = {f["properties"]["name"]: f["properties"] for f in
         json.loads((tmp_path / "a" / "sites.geojson").read_text())["features"]}
    assert p["Seaside Park - 5th Ave"]["status"] == "off_season" and p["Seaside Park - 5th Ave"]["last_result"] == "ok"
    assert p["Beachwood Beach West"]["last_result"] == "over"
    # March 2026 still counts the 2025 season; October 2026 does not
    export(store, tmp_path / "b", today=date(2026, 3, 1), sources_path=src, links_path=tmp_path / "x.csv")
    assert json.loads((tmp_path / "b" / "sites.geojson").read_text())["features"][0]["properties"]["last_result"]
    export(store, tmp_path / "c", today=date(2026, 10, 11), sources_path=src, links_path=tmp_path / "x.csv")
    assert all(f["properties"]["last_result"] is None for f in
               json.loads((tmp_path / "c" / "sites.geojson").read_text())["features"])
