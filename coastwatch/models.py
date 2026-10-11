"""Normalized data model shared by all collectors.

Every collector, whatever its upstream format, must produce these objects.
That contract is what lets the website treat five states as one dataset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    OPEN = "open"               # sampled, meets standard
    ADVISORY = "advisory"       # swimming not recommended / standard exceeded
    CLOSED = "closed"
    RESAMPLING = "resampling"   # first exceedance, confirmation sample pending
    OFF_SEASON = "off_season"   # not sampled until next season
    NO_DATA = "no_data"         # in season but no recent result
    UNKNOWN = "unknown"         # publisher wording we couldn't map


class Indicator(str, Enum):
    ENTEROCOCCUS = "enterococcus"
    E_COLI = "e_coli"


# Publisher wording -> normalized status. Checked as lowercase substrings,
# in order, so more specific phrases must come first.
STATUS_PHRASES: list[tuple[str, Status]] = [
    ("not recommended", Status.ADVISORY),
    ("advisory lift", Status.OPEN),
    ("resampl", Status.RESAMPLING),
    ("sampling to resume", Status.OFF_SEASON),
    ("end of sampling season", Status.OFF_SEASON),
    ("season has ended", Status.OFF_SEASON),
    ("season ended", Status.OFF_SEASON),
    ("off season", Status.OFF_SEASON),
    ("off-season", Status.OFF_SEASON),
    ("closed", Status.CLOSED),
    ("closure", Status.CLOSED),
    ("advisory", Status.ADVISORY),
    ("warning", Status.ADVISORY),
    ("exceed", Status.ADVISORY),
    ("algae", Status.ADVISORY),
    ("no data", Status.NO_DATA),
    ("not sampled", Status.NO_DATA),
    ("no sample", Status.NO_DATA),
    ("no active advisories", Status.OPEN),
    ("within", Status.OPEN),
    ("safe", Status.OPEN),
    ("open", Status.OPEN),
    ("meets", Status.OPEN),
]


def normalize_status(text: Optional[str]) -> Status:
    if not text:
        return Status.UNKNOWN
    t = text.strip().lower()
    for phrase, status in STATUS_PHRASES:
        if phrase in t:
            return status
    return Status.UNKNOWN


@dataclass
class Site:
    source_site_id: str
    name: str
    state: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    county: Optional[str] = None
    water_body: Optional[str] = None
    water_type: str = "unknown"          # marine | fresh | unknown
    detail_url: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Sample:
    source_site_id: str
    sample_date: str                     # YYYY-MM-DD
    indicator: Indicator
    value: Optional[float]
    units: Optional[str] = "CFU/100mL"
    qualifier: Optional[str] = None
    sample_time: Optional[str] = None
    source_sample_id: str = ""           # "" (not NULL) so UNIQUE constraint works
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Advisory:
    source_site_id: str
    kind: str                            # advisory | closure | resampling | off_season | info | lift
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    reason: Optional[str] = None
    source_text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusReport:
    source_site_id: str
    status: Status
    source_status: Optional[str] = None


@dataclass
class CollectorResult:
    sites: list[Site] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    advisories: list[Advisory] = field(default_factory=list)
    statuses: list[StatusReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: "CollectorResult") -> None:
        self.sites += other.sites
        self.samples += other.samples
        self.advisories += other.advisories
        self.statuses += other.statuses
        self.warnings += other.warnings
