"""Load the FFIEC Census flat file (no header row) into PostgreSQL.

Usage (inside the dev container):
    python scripts/load_census.py data/CensusFlatFile2025.csv            # California only (default)
    python scripts/load_census.py data/CensusFlatFile2025.csv --state 36 # another state, by FIPS code
    python scripts/load_census.py data/CensusFlatFile2025.csv --state all

What it creates:
  * ffiec_census_raw    - one row per tract, every column as text: c0001, c0002, ... c1212
                          (numbered in the order of the FFIEC data dictionary), plus census_tract
  * ffiec_census_tract  - view with readable names for the columns we've identified (db/census_views.sql)
  * hmda_with_census    - hmda_clean joined to ffiec_census_tract on census_tract

Each run replaces ffiec_census_raw.
"""

import argparse
import csv
import io
import os
from pathlib import Path

import psycopg
from psycopg import sql

TABLE = "ffiec_census_raw"
VIEWS_SQL = Path(__file__).resolve().parent.parent / "db" / "census_views.sql"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--state", default="06", help="2-digit state FIPS code, or 'all' (default: 06 = California)")
    args = parser.parse_args()

    # Read the rows we want; the file has no header, so number the columns.
    rows = []
    with args.file.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if args.state == "all" or row[2] == args.state:
                rows.append(row)
    if not rows:
        raise SystemExit(f"No rows found for state {args.state}")
    width = len(rows[0])
    cols = [f"c{i:04d}" for i in range(1, width + 1)]
    print(f"{len(rows):,} tracts, {width} columns")

    # census_tract = state (2) + county (3) + tract (6) = 11 digits, same format as HMDA
    buf = io.StringIO()
    w = csv.writer(buf)
    for row in rows:
        w.writerow([row[2] + row[3] + row[4], *row])
    buf.seek(0)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(TABLE)))
        conn.execute(
            sql.SQL("CREATE TABLE {} (census_tract TEXT PRIMARY KEY, {})").format(
                sql.Identifier(TABLE),
                sql.SQL(", ").join(sql.SQL("{} TEXT").format(sql.Identifier(c)) for c in cols),
            )
        )
        copy_stmt = sql.SQL("COPY {} FROM STDIN WITH (FORMAT csv)").format(sql.Identifier(TABLE))
        with conn.cursor() as cur, cur.copy(copy_stmt) as copy:
            copy.write(buf.read())
        conn.execute(VIEWS_SQL.read_text())
        conn.commit()
        n = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        print(f"Done. {TABLE} has {n:,} tracts; views ffiec_census_tract and hmda_with_census created.")


if __name__ == "__main__":
    main()
