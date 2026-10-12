# Adding a state or county source

Most beach programs publish results in one of a few formats. CoastWatch has a
collector for each, so adding a source is usually just an entry in
`config/sources.json`. No code changes are needed, and the website picks up the
new source automatically.

| What the source publishes | Collector | Example |
|---|---|---|
| An HTML table on a web page | `table` | a county "weekly beach results" page |
| A CSV or Excel file at a fixed address | `table` | a state's downloadable results sheet |
| A JSON data feed | `json` | the data request behind a modern map page |
| An ArcGIS map or feature service | `arcgis` | Maryland's Healthy Beaches status layer |
| A Socrata open-data dataset | (custom, see `nyc.py`) | NYC Open Data |

PDFs, image-only pages and sites that need a login need custom code.

## 1. Find the data

Open the page in Chrome or Edge, press F12, open the **Network** tab, reload, and
look for a request that returns the results. A JSON feed is the most reliable
choice. If the results are simply in a table on the page, use the page address.

## 2. Add an entry to `config/sources.json`

HTML table example:

```json
"md_aa_county": {
  "enabled": true,
  "collector": "table",
  "state": "MD",
  "agency": "Anne Arundel County Department of Health",
  "label": "Anne Arundel County Health",
  "url": "https://www.example.gov/beach-results",
  "table_match": "Enterococci",
  "columns": {
    "name": "Beach",
    "date": "Sample Date",
    "result": "Enterococci",
    "status": "Status"
  },
  "forward_fill": ["Beach"],
  "priority": 90
}
```

JSON feed example (`records` is the path to the list of records; nested fields
use dots):

```json
"nj_dep_api": {
  "enabled": true,
  "collector": "json",
  "state": "NJ",
  "agency": "NJDEP Cooperative Coastal Monitoring Program",
  "label": "New Jersey DEP",
  "url": "https://www.example.org/api/stations",
  "records": "data.stations",
  "columns": {
    "id": "stationId", "name": "name",
    "lat": "location.lat", "lon": "location.lng",
    "date": "latest.sampleDate", "result": "latest.enterococci",
    "status": "status"
  },
  "priority": 80
}
```

### The `columns` block

| Key | Meaning | Required |
|---|---|---|
| `name` | Beach or station name | yes (or `id`) |
| `id` | Stable station ID; otherwise one is made from the name | no |
| `date` | Sample date | for results |
| `result` | Bacteria result; handles `<10`, `>2,420`, `ND` | for results |
| `status` | Published status ("Open", "Advisory", "Closed"...) | no |
| `lat`, `lon` | Coordinates | no; see below |
| `county`, `water_body`, `url`, `units` | Extra details; `url` links each beach to its own page | no |

Column names are matched loosely, so `"Sample Date"` also finds `SAMPLE_DATE` or
`Date Sampled`. Each value can also be a list of alternatives.

Other options: `format` (`html`, `csv` or `xlsx`, detected automatically),
`sheet` (an Excel sheet name), `forward_fill` (columns left blank under a merged
cell), `indicator` (`enterococcus` or `e_coli`) and `water_type` (`marine` or `fresh`).

### Source settings used by the website

| Key | Meaning |
|---|---|
| `priority` | Which source wins when several describe the same beach. County and state sources ~80 to 90, MDE status 70, national portal 10. |
| `label` | Agency name used in "Check the official … page". |
| `delayed` | Results arrive late (the national portal); the site says so. |
| `status_only` | The source publishes status but no results. |
| `site_kind` | `beach` or `waterway` when every site in the source is one kind (rivers, bays and harbors are `waterway`). |

## 3. Coordinates

If the source has no coordinates, run the workflow once, then add rows to
`config/coordinate_overrides.csv` (`site_id,name,state,latitude,longitude,precision`).
Use `precision` = `approximate` when unsure; the site tells visitors.

## 4. Overlapping sources

When a new source covers beaches that another source already shows, export
links them into one beach automatically: sites from different sources within
250 m, or within 1 km with similar names. The beach takes its status from the
highest-priority source and combines everyone's results in its chart.

If the automatic match is wrong, add a row to `config/site_links.csv`:

```
site_a,site_b,action,note
md_aa_county:bayside-beach,md_mde:{GLOBALID},link,same beach; names differ
wqp_ny:XYZ-12,ny_county:abc,separate,different beaches on the same pier
```

## 5. Test it

Run the workflow and read the **Collect** step log. Each table or JSON source
logs how many rows it found and the column names it saw, which is everything
needed to correct a `columns` block.
