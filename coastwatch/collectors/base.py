"""Collector contract: fetch upstream data -> parse -> CollectorResult.

fetch() and parse() are separate on purpose: parse() is pure (no network),
so every parser is unit-tested against saved fixtures, and archived raw
snapshots can be re-parsed after a bug fix.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from typing import Any, Optional

from ..http import PoliteClient
from ..models import CollectorResult
from ..store import Store

log = logging.getLogger(__name__)


class Collector(ABC):
    source_id: str = ""
    state: str = ""
    agency: str = ""
    homepage_url: str = ""
    notes: str = ""

    def __init__(self, client: Optional[PoliteClient] = None, store: Optional[Store] = None):
        self.client = client or PoliteClient()
        self.store = store

    def get(self, url: str, params=None, timeout: float | None = None):
        resp = (self.client.get(url, params=params, timeout=timeout) if timeout
                else self.client.get(url, params=params))
        if self.store:
            self.store.archive_raw(self.source_id, resp.url, resp.body, resp.content_type)
        return resp

    @abstractmethod
    def collect(self) -> CollectorResult:
        """Fetch from upstream and return normalized results."""


# ---------------------------------------------------------------- helpers
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S",
                 "%Y-%m-%dT%H:%M:%S.%f", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y")


def parse_date(v: Any) -> Optional[str]:
    """Return ISO YYYY-MM-DD from many representations, or None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)):
        # ArcGIS uses epoch milliseconds
        if v > 1e11:
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date().isoformat()
        return None
    s = str(v).strip()
    try:  # ISO 8601 incl. '2026-09-29T00:00:00.000'
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def parse_result(v: Any) -> tuple[Optional[float], Optional[str]]:
    """Parse lab results like '52', '<10', '>24196', '10,462', 'ND'.
    Returns (value, qualifier)."""
    if v is None:
        return None, None
    if isinstance(v, (int, float)):
        return float(v), None
    s = str(v).strip().replace(",", "")
    if not s:
        return None, None
    if s.upper() in {"ND", "BDL", "NON-DETECT"} or "below detection" in s.lower():
        return None, "below_detection"
    m = re.match(r"^([<>]=?)?\s*(\d+(?:\.\d+)?)", s)
    if not m:
        return None, s[:40]
    return float(m.group(2)), (m.group(1) or None)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def find_key(d: dict, *candidates: str) -> Optional[str]:
    """Find the first key in d whose normalized name matches/contains a candidate.
    Lets parsers survive column renames ('Beach Name' vs 'BEACH_NAME' vs 'Beach')."""
    norm = {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in d}
    for c in candidates:
        c2 = re.sub(r"[^a-z0-9]", "", c.lower())
        if c2 in norm:
            return norm[c2]
    for c in candidates:
        c2 = re.sub(r"[^a-z0-9]", "", c.lower())
        for nk, k in norm.items():
            if c2 and c2 in nk:
                return k
    return None


_PLACEHOLDERS = {"", "none", "null", "nan", "n/a", "na", "-", "unknown"}


def clean_text(v) -> Optional[str]:
    """None for empty/placeholder values such as 'none' or 'N/A'."""
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return None if s.lower() in _PLACEHOLDERS else s


def nice_name(v: str) -> str:
    """'TOM'S COVE, 10TH ST' -> "Tom's Cove, 10th St" (no '10Th' or 'Tom'S')."""
    s = re.sub(r"\s+", " ", str(v)).strip()
    if not s or (s != s.upper() and s != s.title()):
        return s                       # already mixed case: leave as published
    s = s.lower()
    s = re.sub(r"(?<![0-9a-z'])[a-z][a-z']*", lambda m: m.group(0)[0].upper() + m.group(0)[1:], s)
    s = re.sub(r",(?=\S)", ", ", s)    # 'INN,1010' -> 'Inn, 1010'
    return s
