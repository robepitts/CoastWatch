# CoastWatch: Mid-Atlantic Beach Water Quality Collectors

Collects beach water quality results and advisories from five state programs (VA, MD, DE, NJ, NY), normalizes them into one datastore, and exports static JSON for a map-based website.

> Unofficial aggregator. Always link users back to the official state source, and display a disclaimer that conditions can change faster than sampling.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                      # 30 tests, all offline

python -m coastwatch probe               # inspect live sources, writes probe_report.json
python -m coastwatch collect             # fetch everything into data/coastwatch.db
python -m coastwatch collect --only nyc_doh de_dnrec
python -m coastwatch coords              # list sites lacking coordinates in config/coordinate_overrides.csv
python -m coastwatch export              # write docs/data/ for the website
python -m coastwatch status              # recent runs and per-state counts

python scripts/build_site.py             # build docs/index.html
python scripts/build_site.py --preview   # also dist/preview.html with sample data embedded
python scripts/make_sample_data.py       # regenerate docs/sample-data/
```

To view the site locally: `python -m http.server -d docs 8000`, then open http://localhost:8000.

Before your first real run, edit the contact address in `USER_AGENT` in `coastwatch/http.py`. Agencies are much more forgiving of automated traffic they can identify and contact.

## How it fits together

```
 state websites ──► collectors ──► Store (SQLite) ──► docs/data/ (static JSON) ──► docs/index.html
                     │  fetch()       │ idempotent writes
                     │  parse() pure  │ raw payloads archived
                     ▼                ▼
               config/sources.json  config/standards.json
```

Each collector separates fetching from parsing. Parsers are pure functions tested against fixtures, so a site redesign shows up as a failing test or a logged warning rather than silently bad data. Every raw payload is archived (gzipped, deduplicated by hash) in `raw_snapshots`, so history can be re-parsed after fixing a parser.

### Datastore tables

| Table | Purpose |
|---|---|
| `sources` | One row per publisher |
| `sites` | Beaches/stations with coordinates and a link to the official page |
| `samples` | Individual lab results; `exceeds_bav` computed against the state threshold |
| `advisories` | Advisories, closures, lifts, off-season notices as published |
| `status_observations` | Status reported at each run, appended rather than overwritten, so status history builds up even for "current conditions only" sources |
| `collection_runs` | Success/failure, counts and errors for every run |
| `raw_snapshots` | Untouched upstream payloads |

Views `latest_samples` and `latest_status` drive the map popups. The schema is plain SQL and ports to Postgres/PostGIS with minor type changes.

### Normalized statuses

`open`, `advisory`, `closed`, `resampling`, `off_season`, `no_data`, `unknown`. Publisher wording is mapped in `coastwatch/models.py` (`STATUS_PHRASES`). When a source reports no status, export infers one from the newest sample: exceeds threshold → `advisory`; older than 14 days → `no_data` in season or `off_season` outside it.

### Export files

- `docs/data/sites.geojson`: one Point per mapped site with status, latest result, 30-day geomean and threshold
- `docs/data/sites/<site_id>.json`: full sample and advisory history for charts
- `docs/data/summary.json`: counts by state and status, newest sample per state
- `docs/data/unmapped_sites.json`: sites still missing coordinates

## The website

`site/index.template.html` is a single self-contained page; `scripts/build_site.py` turns it into `docs/index.html`. No build tools or frameworks are needed. It loads Leaflet and Chart.js from jsDelivr and Public Sans from Google Fonts.

- **Map:** OpenStreetMap tiles at every zoom level, credited as OSM requires. (The all-in-one preview build can't load remote images, so it draws U.S. Census state outlines instead.) Dark mode dims the tiles slightly.
- **Markers** differ in shape as well as color (circle meets standard, triangle advisory, square closed, diamond resampling, hollow ring off season or no recent data), so status is readable without relying on color.
- **Side panel:** with nothing selected, it lists beaches in the current map view, most urgent first, with a status breakdown and a per-state table (click a state to zoom to it). Selecting a beach shows its status in plain language, the latest result against the state limit, the 30-day geometric mean, a season chart, recent advisories and a link to the official page.
- **Filters:** states, name search, and "only advisories and closures".
- **Shareable links:** selecting a beach updates the URL (`#site=de_dnrec%3A304`).
- **Data:** the page reads `docs/data/`. Until the first successful collection it falls back to `docs/sample-data/`, which is clearly labeled as simulated. Add `?sample=1` to the URL to view the sample set at any time.
- Light and dark mode follow the device setting; the layout stacks on phones.

## Hosting on GitHub (free)

1. Create a **public** repository and upload this project. If the hidden `.github` folder doesn't upload, use "Add file" → "Create new file", name it `.github/workflows/collect.yml`, and paste the contents.
2. Settings → Actions → General → Workflow permissions: choose **Read and write permissions**.
3. Settings → Pages: "Deploy from a branch", branch `main`, folder **/docs**. The site appears at `https://<username>.github.io/<repo>/`.
4. Actions tab → **collect** → **Run workflow** to do the first collection now instead of waiting for the daily run. Each run's log shows which sources succeeded.

The workflow runs once a day at 22:10 UTC (evening Eastern time), commits the database and exports, and Pages republishes automatically. GitHub pauses scheduled workflows in repositories with no activity for 60 days; the daily data commits normally prevent that, but check the Actions tab occasionally. If a state's website blocks GitHub's servers, that source will fail in the logs while the others continue.

## Sources (investigated October 2026)

| Source ID | State | Method | Confidence |
|---|---|---|---|
| `nyc_doh` | NY (NYC) | NYC Open Data Socrata API, dataset `2xir-kwzz` | **High**: documented public API, schema confirmed |
| `de_dnrec` | DE | HTML of `RecWaterSitePublic.aspx?UnitID=<id>` | **High** for parsing; site discovery partial |
| `va_vdh` | VA | Yearly Excel workbooks on VDH Beach Data page | **Medium**: links confirmed, column names unverified |
| `md_mde` | MD | ArcGIS web map `724261e6ba0746bc9fa501f2d4f21de0` | **Medium**: item confirmed, field names unverified |
| `ny_doh` | NY (state) | ArcGIS Experience `301748017d7d40649bc5082fc1c5365e` | **Medium**: item confirmed, field names unverified |
| `nj_dep` | NJ | Data embedded in njbeaches.org (Next.js) | **Low/experimental**: no public API found |

Notes per source:

- **NYC** publishes enterococci results with no coordinates and no status. Coordinates come from `config/coordinate_overrides.csv`; status is derived from results. "Result below detection limit" is stored as `qualifier='below_detection'` with a null value.
- **Delaware** pages contain the full season's results, geomeans, and active/historical advisories. Six UnitIDs are seeded; the homepage loads its full beach list via JavaScript, which `discover_sites()` only partly captures.
- **Virginia**'s map is a Tableau dashboard, but the same data is published as `BeachMonitoringData_<stamp>.xlsx` and `BeachActivityReport_<stamp>.xlsx`. File names change on each update, so the collector finds the link labeled with the current year. Columns are matched by fuzzy name.
- **Maryland and NY State** use the generic ArcGIS collector, which walks app → web map → feature layers at run time instead of hard-coding layer URLs. Only **point** layers are ever collected: boundaries, basemaps and other polygon or line layers are skipped regardless of name, and non-point features are discarded. `layer_name_filter` then keeps only layers whose name or address matches (default `beach`). The run log lists skipped layers and the field names of each collected layer. If guessed fields are wrong, set `field_map` in `config/sources.json`, for example `{"name": "BEACH_NAME", "status": "STATUS", "date": "SAMPLE_DT"}`.
- **New Jersey** has no documented API. The collector looks for station data embedded in the page. If `probe` reports `embedded_records: 0`, the data is fetched by JavaScript and its endpoint needs to be found (below).
- **Also available, not yet added:** NYS Parks beach results (separate ArcGIS map), and EPA's Water Quality Portal for multi-year historical backfill.
- `ny.healthinspections.us` disallows automated access in robots.txt and is deliberately not used.

## Thresholds

`config/standards.json` holds single-sample and geomean values per state and indicator. DE (104 / 35) and NJ (104 / 30) were confirmed against official sources. MD, VA and NY are marked `"status": "verify"`: confirm with each program before launch.

## Next steps

1. **Run `python -m coastwatch probe`** from a normal internet connection and share `probe_report.json`. That shows the real MD/NY field names and VA column headers so mappings can be pinned down.
2. **Find the NJ data endpoint.** Open https://njbeaches.org in Chrome, press F12, open *Network*, filter to *Fetch/XHR*, reload and click a station. Copy any request URL returning JSON and set it as `api_url` for `nj_dep` in `config/sources.json`.
3. **Find the Delaware beach list** the same way on https://recwaters.dnrec.delaware.gov (look for a request returning beach names with UnitIDs and coordinates).
4. **Fill coordinates** for NYC and DE sites: run `coords`, then complete `config/coordinate_overrides.csv` (or source them from EPA beach location data).
5. **Consider an off-season schedule.** From October to April, daily runs mostly re-fetch unchanged pages; changing the cron line to weekly for those months is optional.

## Project layout

```
coastwatch/
  __main__.py          CLI
  models.py            normalized data model + status vocabulary
  store.py             SQLite datastore
  schema.sql           tables and views
  standards.py         thresholds, exceedance, rolling geomean
  http.py              polite HTTP client (robots.txt, throttling, retries)
  export.py            static JSON export for the website
  probe.py             live-source inspection
  collectors/          one module per source + generic ArcGIS
site/
  index.template.html  the website (HTML, CSS, JS in one file)
  basemap.json         state outlines (preview build only)
  vendor/leaflet.css
scripts/
  build_site.py        builds docs/ (and an all-in-one preview)
  make_sample_data.py  simulated data through the real pipeline
docs/                  what GitHub Pages serves
  index.html, data/ (daily), sample-data/
config/
  sources.json         per-source settings
  standards.json       state thresholds
  coordinate_overrides.csv
tests/                 offline tests and fixtures
.github/workflows/collect.yml
```
