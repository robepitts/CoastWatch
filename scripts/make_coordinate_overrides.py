"""Write config/coordinate_overrides.csv for sources that publish no coordinates.

Positions were assigned from known beach locations (October 2026), not from the
agencies' own station coordinates. "good" = a well-known public beach placed on
the correct stretch of shore; "approximate" = a private club or a name whose
exact location is uncertain. Replace with official station coordinates (e.g.
from EPA's beach database) when available.

    python scripts/make_coordinate_overrides.py
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "coordinate_overrides.csv"

G, A = "good", "approximate"

# ---- Virginia (VDH station keys) ------------------------------------------
VA = {
    # Eastern Shore
    "assateague-island-tom-s-cove-north": (37.8930, -75.3380, A),
    "assateague-island-tom-s-cove-south": (37.8800, -75.3450, A),
    "guard-shore": (37.8450, -75.6920, A),
    "kiptopeke-state-park": (37.1680, -75.9860, G),
    "town-of-cape-charles-public-beach": (37.2650, -76.0220, G),
    # Hampton
    "buckroe-north": (37.0520, -76.2880, G),
    "buckroe-mid": (37.0440, -76.2860, G),
    "buckroe-south": (37.0360, -76.2850, G),
    "fort-monroe": (37.0100, -76.3010, G),
    "grandview": (37.0850, -76.2830, A),
    "salt-ponds": (37.0660, -76.2900, A),
    # Norfolk, Ocean View (west to east)
    "21st-bay-st-north-end-behind-ship-s-captain-restaurant": (36.9640, -76.3000, A),
    "5th-bay-st-north-end": (36.9570, -76.2870, A),
    "13th-view-north-end": (36.9510, -76.2680, A),
    "10th-view-behind-quality-inn-1010-w-ocean-view-ave": (36.9505, -76.2640, A),
    "ocean-view-park-east-side-of-parking-lot": (36.9550, -76.2500, G),
    "north-community-beach": (36.9500, -76.2400, A),
    "sarah-constant-park-east-end": (36.9518, -76.2315, G),
    "capeview-ave-north-end": (36.9280, -76.1880, A),
    "captains-quarters": (36.9260, -76.1770, A),
    "east-community-beach-end-of-east-ocean-view-ave": (36.9210, -76.1700, A),
    # Newport News, York, Gloucester, Mathews
    "anderson-s-beach": (37.0000, -76.4400, A),
    "hilton-beach": (37.0255, -76.4610, G),
    "huntington-beach": (36.9940, -76.4420, G),
    "king-lincoln-park": (36.9620, -76.4100, G),
    "yorktown-beach": (37.2360, -76.5080, G),
    "gloucester-point-beach": (37.2480, -76.4990, G),
    "festival-beach": (37.4940, -76.2830, A),
    # Virginia Beach: Chesapeake Bay side (west to east)
    "chesapeake-beach": (36.9160, -76.1100, G),
    "lesner-bridge-east": (36.9110, -76.0900, G),
    "chick-s-beach": (36.9130, -76.0800, G),
    "first-landing-state-park": (36.9190, -76.0500, G),
    "fort-story-south": (36.9150, -76.0050, A),
    # Virginia Beach: oceanfront (north to south)
    "78th-street": (36.9020, -75.9870, G),
    "63rd-street": (36.8890, -75.9830, G),
    "45th-street": (36.8730, -75.9790, G),
    "28th-street": (36.8580, -75.9750, G),
    "15th-street": (36.8460, -75.9730, G),
    "croatan": (36.8220, -75.9680, G),
    "camp-pendleton": (36.8070, -75.9630, G),
    "dam-neck-north": (36.7900, -75.9570, A),
    "dam-neck-middle": (36.7780, -75.9540, A),
    "dam-neck-south": (36.7660, -75.9500, A),
    "sea-gate": (36.7440, -75.9380, A),
    "sandbridge-north": (36.7350, -75.9350, G),
    "sandbridge-south": (36.7150, -75.9300, G),
    "little-island-beach-north": (36.6980, -75.9220, G),
    "little-island-beach-south": (36.6920, -75.9200, G),
    "back-bay-beach": (36.6720, -75.9120, G),
}

# ---- NYC (DOHMH beach keys; Left/Center/Right are offset along the shore) ----
NYC = {
    "orchard-beach": (40.8670, -73.7920, G),
    "manhattan-beach": (40.5770, -73.9400, G),
    "kingsborough-community-college": (40.5780, -73.9340, G),
    "gerritsen-kiddie-beach": (40.5850, -73.9150, G),
    "south-beach": (40.5835, -74.0665, G),
    "midland-beach": (40.5730, -74.0860, G),
    "cedar-grove": (40.5670, -74.0990, G),
    "wolfe-s-pond-beach": (40.5170, -74.1900, G),
    "coney-island-br-15th-6th": (40.5770, -73.9570, G),
    "coney-island-br-6th-ocean-pkwy": (40.5745, -73.9650, G),
    "coney-island-ocean-pkwy-west-8th": (40.5730, -73.9720, G),
    "coney-island-west-8th-pier": (40.5725, -73.9770, G),
    "coney-island-west-16th-west-27th": (40.5725, -73.9880, G),
    "coney-island-west-28th-west-37th": (40.5735, -73.9980, G),
    "sea-gate-42nd": (40.5770, -74.0050, A),
    "sea-gate-beach-club": (40.5775, -74.0100, A),
    "rockaway-beach-9th-13th": (40.5945, -73.7520, G),
    "rockaway-beach-15th-22nd": (40.5940, -73.7580, G),
    "rockaway-beach-23rd-59th": (40.5890, -73.7760, G),
    "rockaway-beach-59th-80th": (40.5860, -73.8000, G),
    "rockaway-beach-80th-95th": (40.5830, -73.8150, G),
    "rockaway-beach-95th-116th": (40.5800, -73.8300, G),
    "rockaway-beach-116th-126th": (40.5770, -73.8450, G),
    "rockaway-beach-126th-149th": (40.5730, -73.8580, G),
    "breezy-point-reid-ave": (40.5580, -73.9050, A),
    "breezy-point-219th-street": (40.5530, -73.9300, A),
    # Private beach clubs on Long Island Sound / Little Neck Bay
    "douglaston-homeowners-association": (40.7720, -73.7510, A),
    "whitestone-booster-civic-association": (40.7970, -73.8170, A),
    "morris-yacht-and-beach-club": (40.8360, -73.7840, A),
    "schuyler-hill-civic-association": (40.8180, -73.8070, A),
    "danish-american": (40.8170, -73.8000, A),
    "american-turners": (40.8060, -73.7960, A),
    "trinity-danish": (40.8120, -73.7930, A),
    "white-cross-fishing-club": (40.8100, -73.7900, A),
    "west-fordham-street-association": (40.8080, -73.8020, A),
    "658-sunrise-club-inc": (40.8040, -73.7990, A),
}
OFFSET = {"left": -0.0010, "center": 0.0, "right": 0.0010}


def rows():
    for key, (lat, lon, q) in VA.items():
        yield f"va_vdh:{key}", key.replace("-", " ").title(), "VA", lat, lon, q
    for key, (lat, lon, q) in NYC.items():
        for pos, d in OFFSET.items():
            yield (f"nyc_doh:{key}--{pos}", f"{key.replace('-', ' ').title()} ({pos})", "NY",
                   round(lat, 5), round(lon + d, 5), q)


def main() -> None:
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["site_id", "name", "state", "latitude", "longitude", "precision"])
        for r in rows():
            w.writerow(r)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
