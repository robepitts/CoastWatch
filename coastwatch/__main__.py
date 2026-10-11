"""CoastWatch CLI.

    python -m coastwatch collect [--only nyc_doh de_dnrec]
    python -m coastwatch coords            # apply config/coordinate_overrides.csv
    python -m coastwatch export [--out export]
    python -m coastwatch status            # recent runs + per-state counts
    python -m coastwatch probe [--only ...] # inspect live source structure (no DB writes)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from .store import Store

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "config" / "coordinate_overrides.csv"


def main() -> None:
    ap = argparse.ArgumentParser(prog="coastwatch")
    ap.add_argument("--db", default=str(ROOT / "data" / "coastwatch.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--only", nargs="*")
    sub.add_parser("coords")
    e = sub.add_parser("export")
    e.add_argument("--out", default=str(ROOT / "docs" / "data"))
    sub.add_parser("status")
    pr = sub.add_parser("probe")
    pr.add_argument("--only", nargs="*")
    pr.add_argument("--out", default=str(ROOT / "probe_report.json"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    store = Store(args.db)

    if args.cmd == "collect":
        from .collectors import run
        for s in run(store, only=args.only):
            print(json.dumps(s, default=str), flush=True)
        n = store.apply_coordinate_overrides(OVERRIDES)
        if n:
            print(f"applied {n} coordinate overrides")
    elif args.cmd == "coords":
        n = store.apply_coordinate_overrides(OVERRIDES)
        missing = store.query("SELECT site_id, name, state FROM sites WHERE latitude IS NULL "
                              "ORDER BY state, name")
        existing = set()
        if OVERRIDES.exists():
            with OVERRIDES.open() as f:
                existing = {r["site_id"] for r in csv.DictReader(f)}
        new_rows = [m for m in missing if m["site_id"] not in existing]
        if new_rows:
            write_header = not OVERRIDES.exists()
            with OVERRIDES.open("a", newline="") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(["site_id", "name", "state", "latitude", "longitude"])
                for m in new_rows:
                    w.writerow([m["site_id"], m["name"], m["state"], "", ""])
        print(f"applied {n} overrides; {len(missing)} sites still lack coordinates "
              f"({len(new_rows)} new rows added to {OVERRIDES.name} to fill in)")
    elif args.cmd == "export":
        from .export import export
        print(json.dumps(export(store, args.out), indent=1))
    elif args.cmd == "probe":
        from .probe import probe
        rep = probe(args.only)
        Path(args.out).write_text(json.dumps(rep, indent=1, default=str))
        for k, v in rep.items():
            print(f"{k:10s} {'OK ' if v.get('ok') else 'ERR'} {v.get('error', '')}")
        print(f"full report written to {args.out}")
    elif args.cmd == "status":
        for r in store.query("""SELECT source_id, started_at, ok, sites_seen, samples_new,
                                advisories_new, substr(COALESCE(error,''),1,120) AS err
                                FROM collection_runs ORDER BY run_id DESC LIMIT 12"""):
            print(dict(r))
        for r in store.query("""SELECT state, COUNT(*) n, SUM(latitude IS NOT NULL) mapped
                                FROM sites GROUP BY state"""):
            print(dict(r))


if __name__ == "__main__":
    main()
