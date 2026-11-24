"""Load census tract boundaries (Census TIGER/Line shapefile) into PostgreSQL, for the lender maps.

Usage:
    python scripts/load_tracts.py                                  # downloads California 2025 if needed
    python scripts/load_tracts.py data/tiger/tl_2025_06_tract.zip  # a file you already have

Source: https://www2.census.gov/geo/tiger/TIGER2025/TRACT/tl_2025_06_tract.zip (06 = California).
TIGER/Line tracts include their water area, so coastal and bay tracts can be mostly water.

What it creates:
  * tract_shapes - one row per tract: census_tract (11 digits, as in HMDA), land and water area (m²),
                   water_pct, and the boundary as GeoJSON, simplified to about 30 m so a whole MSA
                   is light enough to draw in a browser.

Each run replaces tract_shapes.
"""

import argparse
import json
import os
import urllib.request
from pathlib import Path

import psycopg
import shapefile  # pyshp
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "data" / "tiger" / "tl_2025_06_tract.zip"
DEFAULT_URL = "https://www2.census.gov/geo/tiger/TIGER2025/TRACT/tl_2025_06_tract.zip"
TABLE = "tract_shapes"
SIMPLIFY_DEG = 0.0003   # about 30 m: invisible at MSA zoom, keeps a large MSA's map to a few MB
DIGITS = 4              # coordinate decimals (about 10 m)


def _round(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], DIGITS), round(coords[1], DIGITS)]
    return [_round(c) for c in coords]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, nargs="?", default=DEFAULT_FILE)
    args = parser.parse_args()

    if not args.file.exists():
        if args.file != DEFAULT_FILE:
            raise SystemExit(f"{args.file} not found")
        print(f"Downloading {DEFAULT_URL} ...", flush=True)
        args.file.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(DEFAULT_URL, args.file)

    rows = []
    with shapefile.Reader(str(args.file)) as sf:
        for rec in sf.iterShapeRecords():
            r = rec.record.as_dict()
            geom = shape(rec.shape.__geo_interface__).simplify(SIMPLIFY_DEG, preserve_topology=True)
            g = mapping(geom)
            gj = json.dumps({"type": g["type"], "coordinates": _round(json.loads(json.dumps(g["coordinates"])))},
                            separators=(",", ":"))
            aland, awater = int(r["ALAND"]), int(r["AWATER"])
            total = aland + awater
            rows.append((r["GEOID"], aland, awater, round(100 * awater / total, 2) if total else None, gj))
    print(f"{len(rows):,} tracts, {sum(len(r[4]) for r in rows) / 1e6:.1f} MB of GeoJSON")

    with psycopg.connect(os.environ.get("DATABASE_URL", "postgresql://hmda:hmda@localhost:5432/hmda")) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        conn.execute(f"""CREATE TABLE {TABLE} (
            census_tract TEXT PRIMARY KEY, aland BIGINT, awater BIGINT, water_pct DOUBLE PRECISION, geojson TEXT)""")
        with conn.cursor().copy(f"COPY {TABLE} FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        n_water = conn.execute(f"SELECT count(*) FROM {TABLE} WHERE water_pct >= 90").fetchone()[0]
        conn.commit()
    print(f"Loaded {TABLE}: {len(rows):,} tracts, {n_water} of them at least 90% water")


if __name__ == "__main__":
    main()
