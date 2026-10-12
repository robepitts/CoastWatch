"""Build collectors from config/sources.json and run them into the Store."""
from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

from ..http import PoliteClient
from ..store import Store
from .arcgis import ArcGISCollector
from .delaware import DelawareCollector
from .new_jersey import NewJerseyCollector
from .nyc import NYCCollector
from .virginia import VirginiaCollector
from .wqp import WQPCollector

log = logging.getLogger(__name__)
CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.json"


def build(source_id: str, cfg: dict, client: PoliteClient, store: Store):
    kind = cfg["collector"]
    if cfg.get("respect_robots") is False:
        # Only for documented public data APIs whose robots.txt targets crawlers
        # (see the note on that source in config/sources.json). Still throttled.
        client = PoliteClient(min_interval_s=client.min_interval_s, timeout_s=client.timeout_s,
                              retries=client.retries, respect_robots=False)
    kw = {"client": client, "store": store}
    if kind == "nyc":
        return NYCCollector(lookback_days=cfg.get("lookback_days", 120), **kw)
    if kind == "delaware":
        return DelawareCollector(seed_unit_ids=cfg.get("seed_unit_ids"), **kw)
    if kind == "virginia":
        return VirginiaCollector(year=cfg.get("year"), **kw)
    if kind == "new_jersey":
        return NewJerseyCollector(api_url=cfg.get("api_url"), **kw)
    if kind == "arcgis":
        return ArcGISCollector(source_id=source_id, state=cfg["state"], agency=cfg["agency"],
                               portal=cfg["portal"], item_id=cfg.get("item_id"),
                               layer_urls=cfg.get("layer_urls"), field_map=cfg.get("field_map"),
                               homepage_url=cfg.get("homepage_url", ""),
                               layer_name_filter=cfg.get("layer_name_filter"), **kw)
    if kind == "wqp":
        return WQPCollector(source_id=source_id, state=cfg["state"], agency=cfg["agency"],
                            homepage_url=cfg.get("homepage_url", ""),
                            site_types=cfg.get("site_types"), characteristics=cfg.get("characteristics"),
                            years_back=cfg.get("years_back", 2),
                            exclude_org_pattern=cfg.get("exclude_org_pattern"), **kw)
    raise ValueError(f"unknown collector type {kind!r}")


def run(store: Store, only: list[str] | None = None, client: PoliteClient | None = None) -> list[dict]:
    cfg_all = json.loads(CONFIG.read_text())
    client = client or PoliteClient()
    summaries = []
    for source_id, cfg in cfg_all.items():
        if not cfg.get("enabled", True) or (only and source_id not in only):
            continue
        col = build(source_id, cfg, client, store)
        log.info("collecting %s ...", source_id)
        store.register_source(source_id, col.state, col.agency, col.homepage_url)
        run_id = store.start_run(source_id)
        try:
            result = col.collect()
            counts = store.save(source_id, col.state, result, run_id=run_id)
            err = "; ".join(result.warnings)[:2000] or None
            store.finish_run(run_id, ok=bool(result.sites), error=err, **counts)
            summaries.append({"source": source_id, "ok": bool(result.sites), **counts,
                              "warnings": result.warnings})
            log.info("finished %s: %d sites, %d new samples, %d warnings", source_id,
                     counts["sites_seen"], counts["samples_new"], len(result.warnings))
        except Exception as e:
            log.exception("collector %s failed", source_id)
            store.finish_run(run_id, ok=False, error=f"{e}\n{traceback.format_exc()}"[:4000])
            summaries.append({"source": source_id, "ok": False, "error": str(e)})
    return summaries
