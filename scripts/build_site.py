"""Build the website.

    python scripts/build_site.py              # docs/index.html + docs/basemap.json
    python scripts/build_site.py --preview    # also dist/preview.html (all data embedded)

docs/ is what GitHub Pages serves. The page loads docs/data/ (written daily by
the collector) and falls back to docs/sample-data/ until real data exists.
The preview build embeds everything in one file, for sharing a demo.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE, DOCS = ROOT / "site", ROOT / "docs"


def render() -> str:
    html = (SITE / "index.template.html").read_text()
    return html.replace("/*LEAFLET_CSS*/", (SITE / "vendor" / "leaflet.css").read_text())


def embed(html: str, data_dir: Path) -> str:
    sites = json.loads((data_dir / "sites.geojson").read_text())
    payload = {
        "sites": sites,
        "summary": json.loads((data_dir / "summary.json").read_text()),
        "histories": {f["properties"]["history_file"]:
                      json.loads((data_dir / f["properties"]["history_file"]).read_text())
                      for f in sites["features"]},
        "basemap": json.loads((SITE / "basemap.json").read_text()),
    }
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    marker = '<script id="cw-embed" type="application/json">null</script>'
    assert marker in html
    return html.replace(marker, f'<script id="cw-embed" type="application/json">{blob}</script>')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--data", default=str(DOCS / "sample-data"))
    args = ap.parse_args()

    DOCS.mkdir(exist_ok=True)
    html = render()
    (DOCS / "index.html").write_text(html)
    shutil.copy(SITE / "basemap.json", DOCS / "basemap.json")
    (DOCS / ".nojekyll").write_text("")  # serve files as-is on GitHub Pages
    print("wrote docs/index.html")
    if args.preview:
        out = ROOT / "dist" / "preview.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(embed(html, Path(args.data)))
        print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
