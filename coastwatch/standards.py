"""Water quality thresholds and derived metrics (exceedance, geometric mean)."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "standards.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def threshold(state: str, indicator: str) -> Optional[dict]:
    return _load().get(state, {}).get(indicator)


def exceeds_bav(state: str, indicator: str, value: Optional[float]) -> Optional[bool]:
    """True/False vs. the state's single-sample value; None if unknowable."""
    t = threshold(state, indicator)
    if value is None or t is None:
        return None
    return value > t["single_sample"]


def geometric_mean(values: Iterable[float]) -> Optional[float]:
    vals = [max(v, 1.0) for v in values if v is not None]  # guard log(0)
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def rolling_geomean(dated_values: list[tuple[str, float]], as_of: str,
                    window_days: int = 30, min_samples: int = 5) -> Optional[float]:
    """Geomean of samples within `window_days` before `as_of` (ISO dates).
    Returns None when fewer than `min_samples` fall in the window (NJ/DE practice)."""
    end = date.fromisoformat(as_of)
    start = end - timedelta(days=window_days)
    window = [v for d, v in dated_values
              if v is not None and start < date.fromisoformat(d) <= end]
    if len(window) < min_samples:
        return None
    return geometric_mean(window)
