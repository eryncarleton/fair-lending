"""Where the pages get their data: PostgreSQL locally, or a DuckDB file when hosted.

Local (default): tables come from PostgreSQL (DATABASE_URL). The precomputed tables are built there
on first use, or whenever their definition in counts.py changes.

Hosted (FAIR_LENDING_DUCKDB=/path/to/hmda.duckdb): everything comes from one read-only DuckDB file
built on your computer by scripts/export_duckdb.py: the precomputed tables for the pages, and the
full records for free-form SQL only. Nothing is ever built on the server, and there is no database
server: DuckDB runs inside the app, with file and network access switched off.
"""

import os
import threading

import pandas as pd
import streamlit as st

import counts

DUCKDB_PATH = os.environ.get("FAIR_LENDING_DUCKDB")
HOSTED = bool(DUCKDB_PATH)

SQL_TIMEOUT_S = 60        # free-form SQL: stop a query after this long
SQL_MAX_ROWS = 10_000     # free-form SQL: return at most this many rows


# --- Hosted: DuckDB ------------------------------------------------------------------------

@st.cache_resource
def _duck():
    import duckdb

    return duckdb.connect(DUCKDB_PATH, read_only=True, config={
        # Locked down for free-form SQL: no reading or writing other files, no network,
        # no extensions, no changing these settings afterwards.
        "enable_external_access": False,
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "allow_community_extensions": False,
        "memory_limit": os.environ.get("FAIR_LENDING_DUCKDB_MEMORY", "1GB"),
        "threads": 2,
        "lock_configuration": True,
    })


def _duck_df(sql: str) -> pd.DataFrame:
    return _duck().cursor().execute(sql).df()


def _hosted_check() -> None:
    try:
        built = _duck_df("SELECT version FROM _export_meta")["version"].iloc[0]
    except Exception:
        st.error(f"The data file {DUCKDB_PATH} is missing or unreadable.")
        st.stop()
    if built != counts.version():
        st.error(
            "The data file was built by an older version of the app. Rebuild it on your computer "
            "and deploy again: `bash deploy/cloudrun.sh --export`."
        )
        st.stop()


# --- Local: PostgreSQL ------------------------------------------------------------------------

def _ensure_built() -> None:
    from db import get_engine

    with get_engine().begin() as conn:
        if not counts.source_ready(conn.exec_driver_sql):
            st.warning("Census data isn't loaded yet. Run `python scripts/load_census.py data/CensusFlatFile2025.csv`.")
            st.stop()
        if not counts.is_current(conn.exec_driver_sql):
            with st.spinner("Building the summary tables (one time, about 30 seconds)…"):
                counts.build(conn.exec_driver_sql)


# --- What the pages call ----------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Loading…")
def table(name: str) -> pd.DataFrame:
    """One of the precomputed tables (counts.PRECOMPUTED), as a DataFrame."""
    assert name in counts.PRECOMPUTED, name
    if HOSTED:
        _hosted_check()
        return _duck_df(f"SELECT * FROM {name}")
    from db import query

    _ensure_built()
    return query(f"SELECT * FROM {name}")


def has_data() -> bool:
    if HOSTED:
        return bool(DUCKDB_PATH and os.path.exists(DUCKDB_PATH))
    from db import query

    return bool(query("SELECT to_regclass('hmda_clean') IS NOT NULL AS ok")["ok"].iloc[0])


def run_sql(sql: str) -> tuple[pd.DataFrame, bool]:
    """Free-form SQL from the Data explorer. Returns (rows, truncated).

    Hosted: DuckDB, read-only, stopped after SQL_TIMEOUT_S, at most SQL_MAX_ROWS rows.
    Local: PostgreSQL, as before.
    """
    if not HOSTED:
        from db import query

        df = query(sql)
        return df.head(SQL_MAX_ROWS), len(df) > SQL_MAX_ROWS

    cur = _duck().cursor()
    # One read-only statement only: SELECT (including WITH, DESCRIBE, SHOW, SUMMARIZE, FROM ...) or
    # EXPLAIN. The file is read-only anyway; this also rules out in-memory temp tables.
    import duckdb

    statements = cur.extract_statements(sql)
    if len(statements) != 1:
        raise ValueError("Run one statement at a time.")
    if statements[0].type not in (duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN):
        raise ValueError("Only queries that read data (SELECT, WITH, EXPLAIN, DESCRIBE, SHOW) can run here.")

    timer = threading.Timer(SQL_TIMEOUT_S, cur.interrupt)
    timer.start()
    try:
        rel = cur.sql(sql)                   # runs lazily: rows stream out as they're read
        if rel is None:                      # a statement with no result set
            return pd.DataFrame(), False
        reader = rel.to_arrow_reader(2_048)
        batches, n = [], 0
        for batch in reader:
            batches.append(batch)
            n += batch.num_rows
            if n > SQL_MAX_ROWS:
                break
    except Exception as e:
        if "interrupt" in str(e).lower():
            raise RuntimeError(f"Query stopped after {SQL_TIMEOUT_S} seconds.") from None
        raise
    finally:
        timer.cancel()
    import pyarrow as pa

    df = pa.Table.from_batches(batches, schema=reader.schema).to_pandas() if batches else pd.DataFrame(
        columns=[f.name for f in reader.schema])
    return df.head(SQL_MAX_ROWS), n > SQL_MAX_ROWS


def sql_dialect() -> str:
    return "DuckDB" if HOSTED else "PostgreSQL"
