"""Build build/hmda.duckdb: the one data file the hosted app reads (see data.py).

Runs on your computer, against your local PostgreSQL:

    python scripts/export_duckdb.py

Contains:
  * the precomputed tables (counts.PRECOMPUTED), rebuilt first if they're out of date
  * hmda_lar, hmda_clean, ffiec_census_tract, hmda_respondents, msa_md_names, and the
    hmda_with_census view, for free-form SQL
  * _export_meta: the precomputed-table version, so the hosted app can tell a stale file
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import psycopg
import pyarrow as pa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import counts  # noqa: E402
from build_counts import DEFAULT_URL, rebuild  # noqa: E402

OUT = ROOT / "build" / "hmda.duckdb"
CHUNK = 50_000

# Tables and views copied as tables (views are saved as tables, so DuckDB doesn't need
# PostgreSQL's helper functions). hmda_with_census is recreated as a view on top.
SOURCES = ["hmda_lar", "hmda_clean", "ffiec_census_tract", "hmda_respondents", "msa_md_names",
           *counts.PRECOMPUTED]
VIEWS = ["hmda_with_census"]

PG_TO_ARROW = {
    "text": pa.string(), "character varying": pa.string(),
    "smallint": pa.int16(), "integer": pa.int32(), "bigint": pa.int64(),
    "real": pa.float32(), "double precision": pa.float64(),
    "numeric": pa.float64(),  # cast to float8 in the SELECT
    "boolean": pa.bool_(),
}


def columns(pg: psycopg.Connection, name: str) -> list[tuple[str, str]]:
    return pg.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position", (name,)
    ).fetchall()


def copy_table(pg: psycopg.Connection, duck: duckdb.DuckDBPyConnection, name: str) -> int:
    cols = columns(pg, name)
    select, fields = [], []
    for col, pg_type in cols:
        if pg_type == "numeric":
            select.append(f'"{col}"::float8 AS "{col}"')
        elif pg_type not in PG_TO_ARROW:
            select.append(f'"{col}"::text AS "{col}"')
            pg_type = "text"
        else:
            select.append(f'"{col}"')
        fields.append(pa.field(col, PG_TO_ARROW[pg_type]))
    schema = pa.schema(fields)

    n, created = 0, False
    with pg.cursor(name=f"export_{name}") as cur:
        cur.itersize = CHUNK
        cur.execute(f'SELECT {", ".join(select)} FROM "{name}"')
        while rows := cur.fetchmany(CHUNK):
            batch = pa.table([pa.array(col, type=f.type) for col, f in zip(zip(*rows), schema)], schema=schema)
            duck.register("batch", batch)
            duck.execute(f'INSERT INTO "{name}" SELECT * FROM batch' if created
                         else f'CREATE TABLE "{name}" AS SELECT * FROM batch')
            duck.unregister("batch")
            created, n = True, n + len(rows)
    if not created:
        duck.register("batch", schema.empty_table())
        duck.execute(f'CREATE TABLE "{name}" AS SELECT * FROM batch')
        duck.unregister("batch")
    return n


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    start = time.time()

    with psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_URL)) as pg:
        if not counts.source_ready(pg.execute):
            sys.exit("Load the HMDA and census data first.")
        if not counts.is_current(pg.execute):
            rebuild(pg)

        duck = duckdb.connect(str(tmp))
        for name in SOURCES:
            t = time.time()
            n = copy_table(pg, duck, name)
            print(f"  {name:<20} {n:>10,} rows  {time.time() - t:5.1f}s", flush=True)
        for name in VIEWS:
            definition = pg.execute("SELECT pg_get_viewdef(%s::regclass, true)", (name,)).fetchone()[0]
            duck.execute(f'CREATE VIEW "{name}" AS {definition.rstrip().rstrip(";")}')
            print(f"  {name:<20} (view)")
        duck.execute("CREATE TABLE _export_meta AS SELECT ? AS version, ? AS built_at",
                     [counts.version(), datetime.now(timezone.utc).isoformat(timespec="seconds")])
        duck.execute("CHECKPOINT")
        duck.close()

    tmp.replace(OUT)
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:,.0f} MB) in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
