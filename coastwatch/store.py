"""SQLite-backed datastore. All writes are idempotent."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import standards
from .models import CollectorResult

SCHEMA = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: str | Path = "data/coastwatch.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA.read_text())

    # ---- sources & runs -------------------------------------------------
    def register_source(self, source_id: str, state: str, agency: str,
                        homepage_url: str = "", notes: str = "") -> None:
        self.conn.execute(
            """INSERT INTO sources VALUES (?,?,?,?,?)
               ON CONFLICT(source_id) DO UPDATE SET state=excluded.state,
               agency=excluded.agency, homepage_url=excluded.homepage_url,
               notes=excluded.notes""",
            (source_id, state, agency, homepage_url, notes))
        self.conn.commit()

    def start_run(self, source_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO collection_runs (source_id, started_at) VALUES (?,?)",
            (source_id, now_iso()))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, ok: bool, error: Optional[str] = None,
                   **counts: int) -> None:
        self.conn.execute(
            """UPDATE collection_runs SET finished_at=?, ok=?, error=?,
               sites_seen=?, samples_new=?, advisories_new=? WHERE run_id=?""",
            (now_iso(), int(ok), error, counts.get("sites_seen", 0),
             counts.get("samples_new", 0), counts.get("advisories_new", 0), run_id))
        self.conn.commit()

    def archive_raw(self, source_id: str, url: str, body: bytes,
                    content_type: str = "") -> str:
        digest = hashlib.sha256(body).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO raw_snapshots VALUES (?,?,?,?,?,?)",
            (digest, source_id, url, now_iso(), content_type, gzip.compress(body)))
        self.conn.commit()
        return digest

    # ---- the main write path -------------------------------------------
    def save(self, source_id: str, state: str, result: CollectorResult,
             run_id: Optional[int] = None) -> dict[str, int]:
        ts = now_iso()
        c = self.conn
        sid = lambda src_site: f"{source_id}:{src_site}"  # noqa: E731
        counts = {"sites_seen": 0, "samples_new": 0, "advisories_new": 0}
        site_states: dict[str, str] = {}

        with c:  # one transaction
            c.execute("INSERT OR IGNORE INTO sources (source_id, state, agency) VALUES (?,?,?)",
                      (source_id, state, source_id))
            for s in result.sites:
                site_states[s.source_site_id] = s.state
                c.execute(
                    """INSERT INTO sites (site_id, source_id, source_site_id, name, state,
                         county, water_body, water_type, latitude, longitude, coord_source,
                         detail_url, raw_json, first_seen_at, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(site_id) DO UPDATE SET
                         name=excluded.name, county=COALESCE(excluded.county, sites.county),
                         water_body=COALESCE(excluded.water_body, sites.water_body),
                         water_type=CASE WHEN excluded.water_type='unknown'
                                         THEN sites.water_type ELSE excluded.water_type END,
                         -- never let a source without coordinates wipe out known ones
                         latitude=COALESCE(excluded.latitude, sites.latitude),
                         longitude=COALESCE(excluded.longitude, sites.longitude),
                         coord_source=CASE WHEN excluded.latitude IS NOT NULL
                                           THEN excluded.coord_source ELSE sites.coord_source END,
                         detail_url=COALESCE(excluded.detail_url, sites.detail_url),
                         raw_json=excluded.raw_json, last_seen_at=excluded.last_seen_at""",
                    (sid(s.source_site_id), source_id, s.source_site_id, s.name, s.state,
                     s.county, s.water_body, s.water_type, s.latitude, s.longitude,
                     "source" if s.latitude is not None else None, s.detail_url,
                     json.dumps(s.raw, default=str), ts, ts))
                counts["sites_seen"] += 1

            # Placeholder rows for sites referenced only by samples/advisories
            # (e.g. a VA advisory sheet naming a beach absent from the results sheet).
            referenced = ({x.source_site_id for x in result.samples}
                          | {a.source_site_id for a in result.advisories}
                          | {r.source_site_id for r in result.statuses})
            for key in referenced - set(site_states):
                c.execute(
                    """INSERT OR IGNORE INTO sites (site_id, source_id, source_site_id, name,
                         state, water_type, raw_json, first_seen_at, last_seen_at)
                       VALUES (?,?,?,?,?,'unknown','{}',?,?)""",
                    (sid(key), source_id, key, key, state, ts, ts))

            for x in result.samples:
                st = site_states.get(x.source_site_id, state)
                exc = standards.exceeds_bav(st, x.indicator.value, x.value)
                cur = c.execute(
                    """INSERT OR IGNORE INTO samples (site_id, sample_date, sample_time,
                         indicator, value, qualifier, units, exceeds_bav, source_sample_id,
                         raw_json, collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid(x.source_site_id), x.sample_date, x.sample_time, x.indicator.value,
                     x.value, x.qualifier, x.units, None if exc is None else int(exc),
                     x.source_sample_id, json.dumps(x.raw, default=str), ts))
                counts["samples_new"] += cur.rowcount

            for a in result.advisories:
                cur = c.execute(
                    """INSERT OR IGNORE INTO advisories (site_id, kind, reason, start_date,
                         end_date, source_text, raw_json, collected_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (sid(a.source_site_id), a.kind, a.reason, a.start_date, a.end_date,
                     a.source_text, json.dumps(a.raw, default=str), ts))
                counts["advisories_new"] += cur.rowcount

            for r in result.statuses:
                c.execute(
                    """INSERT INTO status_observations (site_id, observed_at, status,
                         source_status, run_id) VALUES (?,?,?,?,?)""",
                    (sid(r.source_site_id), ts, r.status.value, r.source_status, run_id))
        return counts

    # ---- coordinate overrides -------------------------------------------
    def apply_coordinate_overrides(self, csv_path: str | Path) -> int:
        """Fill/replace coordinates from a CSV with columns site_id,latitude,longitude.
        Used for sources (NYC, DE) that publish results without coordinates."""
        import csv
        n = 0
        p = Path(csv_path)
        if not p.exists():
            return 0
        with p.open() as f, self.conn:
            for row in csv.DictReader(f):
                if not row.get("latitude") or not row.get("longitude"):
                    continue
                cur = self.conn.execute(
                    """UPDATE sites SET latitude=?, longitude=?, coord_source='override'
                       WHERE site_id=?""",
                    (float(row["latitude"]), float(row["longitude"]), row["site_id"]))
                n += cur.rowcount
        return n

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))
