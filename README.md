# CoastWatch: Mid-Atlantic Coastal Water Quality Watcher

Collects fecal-bacteria results (Enterococcus and E. coli) and advisories for **beaches and other coastal waters** (bays, harbors, sounds and tidal tributaries) in Virginia, Maryland, Delaware, New Jersey and New York. It normalizes them into one datastore and exports static JSON for a map-based website. Each site is tagged as a designated swimming **beach** or another **waterway** monitoring station, and the website can filter by either.

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

The collector identifies itself to every site with a link to this repository (`USER_AGENT` in `coastwatch/http.py`), so agencies can see who is collecting and why.

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

`open`, `advisory`, `exceeded`, `closed`, `resampling`, `off_season`, `no_data`, `unknown`. `advisory` and `closed` are only ever official statuses published by an agency; when the site infers status from a result over the limit it uses `exceeded` ("Over the limit"), because states apply their own rules before issuing advisories. Publisher wording is mapped in `coastwatch/models.py` (`STATUS_PHRASES`). When a source reports no status, export infers one from the newest sample: exceeds threshold → `exceeded`. Beaches: older than 14 days → `no_data` in season or `off_season` outside it. Other waterways (often sampled monthly, year-round): older than 45 days → `no_data`.

### Export files

- `docs/data/sites.geojson`: one Point per beach (linked sites merged) with status, latest result, 30-day geomean, threshold and contributing sources
- `docs/data/sites/<site_id>.json`: full sample and advisory history for charts
- `docs/data/summary.json`: counts by state and status, newest sample per state
- `docs/data/unmapped_sites.json`: sites still missing coordinates

## The website

`site/index.template.html` is a single self-contained page; `scripts/build_site.py` turns it into `docs/index.html`. No build tools or frameworks are needed. It loads Leaflet and Chart.js from jsDelivr and Public Sans from Google Fonts.

- **Map:** OpenStreetMap tiles at every zoom level, credited as OSM requires. (The all-in-one preview build can't load remote images, so it draws U.S. Census state outlines instead.) Dark mode dims the tiles slightly.
- **Markers** are solid shapes with a thin outline in a darker shade of their own color, sized to the zoom level (smaller when zoomed out): green circle meets standard, amber triangle over the limit, orange-red triangle advisory, dark-red square closed, purple diamond resampling. Colors differ in brightness as well as hue for color-blind readers. Sites without a current result show their last result this season faded, or a grey circle if there is no result this season. Problem sites are always drawn on top: grey, then faded green, green, faded amber, amber and purple, advisory, closed; the selected or hovered site comes to the very top.
- **Side panel:** with nothing selected, it lists beaches in the current map view, most urgent first, with a status breakdown and a per-state table (click a state to zoom to it). Selecting a beach shows its status in plain language, the latest result against the state limit, the 30-day geometric mean, a season chart, recent advisories and a link to the official page.
- **Starting view and Zoom to Mid-Atlantic:** the map opens fitted to every site in the Mid-Atlantic coastal region (`CONFIG.region`), so the northernmost and southernmost coastal sites are both in view. Outliers such as New York's Great Lakes beaches and western Maryland's lakes are one zoom-out away. A button under the zoom controls returns to this view.
- **Zoom to a beach:** selecting a beach in the list or on the map zooms to it; "All beaches in view" returns to the previous view.
- **Map limits:** panning stays near the monitored coast, and the furthest zoom-out still shows every site. The map re-measures itself when its size changes, so tiles always fill it.
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

## Adding sources

See [ADDING_SOURCES.md](ADDING_SOURCES.md). HTML tables, CSV/Excel files and JSON feeds can be added with configuration only (`table` and `json` collectors). When sources overlap, export links their sites into one beach: status from the highest-priority source, history combined from all. Overrides go in `config/site_links.csv`.

## Sources (investigated October 2026)

| Source ID | State | Method | Status |
|---|---|---|---|
| `nyc_doh` | NY (NYC) | NYC Open Data Socrata API, dataset `2xir-kwzz` | **Working**: NYC beaches, current |
| `va_vdh` | VA | Yearly Excel workbooks on VDH Beach Data page | **Working**: VA beaches, current |
| `md_mde` | MD | ArcGIS layer `WSA_Healthy_Beaches_Alerts` (MDE) | **Working**: MD beach status (no results) |
| `wqp_nj` | NJ | Water Quality Portal, coastal stations | **Working**: NJDEP beaches (2025 season) plus bays, rivers, harbors |
| `wqp_ny` | NY | Water Quality Portal, coastal stations; NYC beaches skipped | **Working**: NYSDOH beaches (2025 season) plus harbor and Sound stations |
| `wqp_de` | DE | Water Quality Portal, coastal stations | **Working**: DNREC beaches (current within ~2 weeks) plus tidal waters |
| `wqp_md` | MD | Water Quality Portal, coastal stations | **New**: MDE beach history (merges with MDE status) plus Chesapeake tributaries |
| `wqp_va` | VA | Water Quality Portal, coastal stations; VA beaches skipped | **New**: tidal rivers and bays |
| `ny_doh` | NY | ArcGIS item `3017…365e` | **Disabled**: NYSDEC stream and lake portal, not a beach map |
| `de_dnrec` | DE | HTML of `RecWaterSitePublic.aspx?UnitID=<id>` | **Disabled**: site redesigned Oct 2026; pages now 404 |
| `nj_dep` | NJ | Data embedded in njbeaches.org (Next.js) | **Disabled**: no embedded data; needs JSON endpoint |

Notes per source:

- **NYC and Virginia** publish results without coordinates. Positions come from `config/coordinate_overrides.csv`, generated by `scripts/make_coordinate_overrides.py` from known beach locations. Each row is marked `good` or `approximate`; the site tells visitors when a position is approximate. Replace with official station coordinates (EPA's beach database) when available. NYC status is derived from results. "Result below detection limit" is stored as `qualifier='below_detection'` with a null value.
- **Delaware** pages contain the full season's results, geomeans, and active/historical advisories. Six UnitIDs are seeded; the homepage loads its full beach list via JavaScript, which `discover_sites()` only partly captures.
- **Virginia**'s map is a Tableau dashboard, but the same data is published as `BeachMonitoringData_<stamp>.xlsx` and `BeachActivityReport_<stamp>.xlsx`. File names change on each update, so the collector finds the link labeled with the current year. Columns are matched by fuzzy name.
- **Maryland and NY State** use the generic ArcGIS collector, which walks app → web map → feature layers at run time instead of hard-coding layer URLs. Only **point** layers are ever collected: boundaries, basemaps and other polygon or line layers are skipped regardless of name, and non-point features are discarded. `layer_name_filter` then keeps only layers whose name or address matches (default `beach`). The run log lists skipped layers and the field names of each collected layer. If guessed fields are wrong, set `field_map` in `config/sources.json`, for example `{"name": "BEACH_NAME", "status": "STATUS", "date": "SAMPLE_DT"}`.
- **New Jersey** has no documented API. The collector looks for station data embedded in the page. If `probe` reports `embedded_records: 0`, the data is fetched by JavaScript and its endpoint needs to be found (below).
- **Water Quality Portal** (`coastwatch/collectors/wqp.py`) queries the national portal by state, site type (Ocean, Estuary, which includes tidal tributaries) and bacteria (Enterococcus, E. coli). Stations with an EPA "BEACH Program Site" location type are tagged beaches; all others are waterways and link to their own portal page. Each station is credited to the agency that submitted it. Beach stations already covered by a current direct source are skipped (`exclude_beach_counties` for NYC, `exclude_beach_sites` for Virginia). Bare station codes are shown with their description or as "Station 1403a". `prune_unseen` removes stations a run no longer returns. Non-tidal inland streams are not included; adding `River/Stream` to `site_types` with a coastal-county limit would bring them in. Stations come with official coordinates. Results are delayed because agencies submit periodically; the website labels these sources as latest reported results, shows month and year in the list, and status falls to "no recent data" or "off season" rather than implying current conditions. Agencies already covered directly (NYC Health) are excluded with `exclude_org_pattern`.
- **Considered, not used:** NJDEP continuous sensors, NYSDEC's stream and lake portal, HRECOS (Hudson River sensors) and USGS real-time stations measure physical conditions (temperature, oxygen, chlorophyll), not beach bacteria. USGS rainfall and water temperature, and Riverkeeper's community Enterococcus sampling, are candidates for later add-ons.
- **Next:** attach Water Quality Portal history to Maryland's existing beaches (match stations to MDE beaches by name and distance); repoint New York State to the NYSDOH coastal beach map when it reactivates in season.
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
