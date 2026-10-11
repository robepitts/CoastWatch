-- CoastWatch datastore schema (SQLite; portable to Postgres/PostGIS with minor type changes)
--
-- Design principles:
--   * Every row traces back to its source (source_id + source_site_id).
--   * Writes are idempotent: re-running a collector never duplicates data.
--   * Nothing is thrown away: original attributes are kept in raw_json, and
--     the untouched payload of every fetch is archived in raw_snapshots.

PRAGMA foreign_keys = ON;

-- One row per upstream publisher (e.g. "de_dnrec", "nyc_doh").
CREATE TABLE IF NOT EXISTS sources (
    source_id       TEXT PRIMARY KEY,
    state           TEXT NOT NULL,            -- VA, MD, DE, NJ, NY
    agency          TEXT NOT NULL,
    homepage_url    TEXT,
    notes           TEXT
);

-- A monitoring location (a beach, or a station on a beach).
CREATE TABLE IF NOT EXISTS sites (
    site_id         TEXT PRIMARY KEY,         -- our ID: "<source_id>:<source_site_id>"
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    source_site_id  TEXT NOT NULL,            -- the publisher's own ID
    name            TEXT NOT NULL,
    state           TEXT NOT NULL,
    county          TEXT,
    water_body      TEXT,                     -- "Atlantic Ocean", "Delaware Bay"...
    water_type      TEXT,                     -- marine | fresh | unknown
    latitude        REAL,                     -- WGS84; NULL until geocoded
    longitude       REAL,
    coord_source    TEXT,                     -- "source", "override", "epa_rad"...
    detail_url      TEXT,                     -- link back to the official page
    raw_json        TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    UNIQUE (source_id, source_site_id)
);

-- One laboratory result.
CREATE TABLE IF NOT EXISTS samples (
    sample_pk       INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id         TEXT NOT NULL REFERENCES sites(site_id),
    sample_date     TEXT NOT NULL,            -- ISO date (YYYY-MM-DD)
    sample_time     TEXT,                     -- HH:MM when known
    indicator       TEXT NOT NULL,            -- enterococcus | e_coli | ...
    value           REAL,                     -- NULL when only a qualifier is known
    qualifier       TEXT,                     -- "<", ">", "below_detection"...
    units           TEXT,                     -- "CFU/100mL", "MPN/100mL"
    exceeds_bav     INTEGER,                  -- 1/0 vs. state's single-sample value; NULL unknown
    source_sample_id TEXT,
    raw_json        TEXT,
    collected_at    TEXT NOT NULL,            -- when WE first stored it
    UNIQUE (site_id, sample_date, indicator, source_sample_id)
);

-- Advisories / closures / notices as published.
CREATE TABLE IF NOT EXISTS advisories (
    advisory_pk     INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id         TEXT NOT NULL REFERENCES sites(site_id),
    kind            TEXT NOT NULL,            -- advisory | closure | resampling | off_season | info | lift
    reason          TEXT,
    start_date      TEXT,
    end_date        TEXT,
    source_text     TEXT,                     -- publisher's own wording
    raw_json        TEXT,
    collected_at    TEXT NOT NULL,
    UNIQUE (site_id, kind, start_date, source_text)
);

-- The status a source reported for a site at the moment we looked.
-- Appending (rather than overwriting) gives us a status history even for
-- sources that only ever publish "current conditions".
CREATE TABLE IF NOT EXISTS status_observations (
    obs_pk          INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id         TEXT NOT NULL REFERENCES sites(site_id),
    observed_at     TEXT NOT NULL,
    status          TEXT NOT NULL,            -- normalized: open | advisory | closed | resampling | off_season | no_data | unknown
    source_status   TEXT,                     -- publisher's own wording
    run_id          INTEGER REFERENCES collection_runs(run_id)
);

-- One row per collector execution, for monitoring and debugging.
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    ok              INTEGER,
    sites_seen      INTEGER DEFAULT 0,
    samples_new     INTEGER DEFAULT 0,
    advisories_new  INTEGER DEFAULT 0,
    error           TEXT
);

-- Untouched upstream payloads (gzip), de-duplicated by content hash.
-- Lets us re-parse history if a parser bug is found later.
CREATE TABLE IF NOT EXISTS raw_snapshots (
    sha256          TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    url             TEXT,
    fetched_at      TEXT NOT NULL,
    content_type    TEXT,
    body_gz         BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_samples_site_date ON samples(site_id, sample_date);
CREATE INDEX IF NOT EXISTS idx_status_site_time  ON status_observations(site_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_sites_state       ON sites(state);

-- Convenience view: the newest sample per site/indicator (drives map popups).
CREATE VIEW IF NOT EXISTS latest_samples AS
SELECT s.* FROM samples s
JOIN (
    SELECT site_id, indicator, MAX(sample_date) AS max_date
    FROM samples GROUP BY site_id, indicator
) m ON s.site_id = m.site_id AND s.indicator = m.indicator AND s.sample_date = m.max_date;

-- Convenience view: the newest status observation per site.
CREATE VIEW IF NOT EXISTS latest_status AS
SELECT o.* FROM status_observations o
JOIN (
    SELECT site_id, MAX(obs_pk) AS max_pk FROM status_observations GROUP BY site_id
) m ON o.obs_pk = m.max_pk;
