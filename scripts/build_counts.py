"""Rebuild the precomputed table lender_msa_counts (see counts.py).

The loaders and the app rebuild it on their own; run this after editing db/views.sql or
db/census_views.sql by hand:

    python scripts/build_counts.py
"""

import os
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import counts  # noqa: E402

DEFAULT_URL = "postgresql://hmda:hmda@localhost:5432/hmda"


def rebuild(conn: psycopg.Connection) -> None:
    """Rebuild if the HMDA + census views exist; otherwise say why not. Commits."""
    if not counts.source_ready(conn.execute):
        print(f"Skipping {counts.TABLE}: load both HMDA and census data first.")
        return
    print(f"Building {counts.TABLE} ...", flush=True)
    start = time.time()
    counts.build(conn.execute)
    conn.commit()
    n = conn.execute(f"SELECT count(*) FROM {counts.TABLE}").fetchone()[0]
    print(f"  {n:,} lender x MSA x product rows in {time.time() - start:.1f}s")


if __name__ == "__main__":
    with psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_URL)) as conn:
        rebuild(conn)
