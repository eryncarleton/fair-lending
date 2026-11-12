"""Load HMDA loan-level CSV files into PostgreSQL.

Usage (inside the dev container):
    python scripts/load_hmda.py data/your_file.csv [more files...]
    python scripts/load_hmda.py --table hmda_respondents --replace data/hmda_2025_CA_respondents.csv

Options:
    --table NAME   table to load into (default: hmda_lar)
    --replace      empty the table first instead of adding rows

How it works:
  * Every CSV column goes into table `hmda_lar` as TEXT, exactly as published
    (HMDA uses values like "NA" and "Exempt" in numeric fields, so loading as
    text never fails).
  * Then it (re)creates the view `hmda_clean` from db/views.sql, which converts
    the useful columns to numbers and adds readable labels. Query that view.
"""

import argparse
import csv
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from build_counts import rebuild as rebuild_counts

VIEWS_SQL = Path(__file__).resolve().parent.parent / "db" / "views.sql"
CENSUS_VIEWS_SQL = VIEWS_SQL.with_name("census_views.sql")


def clean_column_name(name: str) -> str:
    # "applicant_race-1" -> "applicant_race_1"
    name = name.strip().strip('"').lower()
    return re.sub(r"[^a-z0-9_]", "_", name)


def load_file(conn: psycopg.Connection, path: Path, table: str) -> int:
    with path.open(newline="", encoding="utf-8-sig") as f:
        header = next(csv.reader(f))
    columns = [clean_column_name(c) for c in header]

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {} ()").format(sql.Identifier(table))
        )
        # Different HMDA years/files can have slightly different columns.
        for col in columns:
            cur.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} TEXT").format(
                    sql.Identifier(table), sql.Identifier(col)
                )
            )

        copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true)").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        )
        with path.open("rb") as f, cur.copy(copy_stmt) as copy:
            while chunk := f.read(1024 * 1024):
                copy.write(chunk)
        return cur.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description="Load HMDA CSV files into PostgreSQL.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--table", default="hmda_lar")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    missing = [str(p) for p in args.files if not p.is_file()]
    if missing:
        sys.exit(f"File(s) not found: {', '.join(missing)}")

    with psycopg.connect(os.environ.get("DATABASE_URL", "postgresql://hmda:hmda@localhost:5432/hmda")) as conn:
        if args.replace:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(args.table)))
        for path in args.files:
            print(f"Loading {path} into {args.table} ...", flush=True)
            rows = load_file(conn, path, args.table)
            conn.commit()
            print(f"  {rows:,} rows loaded")

        if conn.execute("SELECT to_regclass('hmda_lar') IS NOT NULL").fetchone()[0]:
            print("Creating view hmda_clean ...")
            conn.execute(VIEWS_SQL.read_text())
            if conn.execute("SELECT to_regclass('ffiec_census_raw') IS NOT NULL").fetchone()[0]:
                print("Creating census views ...")
                conn.execute(CENSUS_VIEWS_SQL.read_text())
            conn.commit()
            rebuild_counts(conn)
        total = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(args.table))
        ).fetchone()[0]
        print(f"Done. {args.table} now has {total:,} rows.")


if __name__ == "__main__":
    main()
