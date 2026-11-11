"""Precomputed table `lender_msa_counts`: one row per lender x MSA x product x loan type.

Every number the Streamlit pages need comes from this table, so they load in well under a second
instead of re-aggregating ~1.2M HMDA records through three views each time.

Columns:
  total_apps                     all applications (product / loan-type mix)
  apps, <tract type>_apps        applications with tract data, total and in each tract type (redlining test)
  <group>_dec, <group>_den       decided applications and denials for each applicant group
                                 and its comparison group (denial rates / AIR)

Built automatically:
  * by scripts/load_hmda.py and scripts/load_census.py after they load data
  * by the app on first use, or whenever the definitions below change (a version hash of the SQL
    is stored as the table comment)
  * by hand: python scripts/build_counts.py

This module has no Streamlit import, so the loader scripts can use it.
"""

import hashlib

import disparity as dp
import redlining as rl

TABLE = "lender_msa_counts"

# A record has usable census tract data when both tract-share columns are present.
TRACT_OK = "h.tract_minority_pct IS NOT NULL AND h.tract_black_hispanic_pct IS NOT NULL"


def _select_sql() -> str:
    tract_cols = [
        f"count(*) FILTER (WHERE {TRACT_OK} AND h.{col} >= {rl.THRESHOLD}) AS {code.lower()}_apps"
        for code, (_, _, col) in rl.TRACT_TYPES.items()
    ]
    group_cols = []
    for code, cond in dp.all_conditions().items():
        group_cols.append(f"count(*) FILTER (WHERE h.is_decision AND ({cond})) AS {code}_dec")
        group_cols.append(f"count(*) FILTER (WHERE h.is_denied AND ({cond})) AS {code}_den")
    cols = ",\n           ".join(tract_cols + group_cols)
    # Excluded everywhere: purchased loans (action 6), non-MSA records, "Exempt" open-end flags.
    # Records without tract data still count toward denial rates, but not toward `apps`
    # (the redlining test needs a tract for every application it counts).
    return f"""
    SELECT h.lei,
           coalesce(h.respondent_name, h.lei)      AS respondent_name,
           coalesce(h.is_depository, false)        AS is_depository,
           h.msa_md,
           coalesce(n.msa_md_name, h.msa_md)       AS msa_name,
           h.product,
           coalesce(h.loan_type, 'Unknown')      AS loan_type,
           count(*)                                AS total_apps,
           count(*) FILTER (WHERE {TRACT_OK})      AS apps,
           {cols}
    FROM hmda_with_census h
    LEFT JOIN msa_md_names n USING (msa_md)
    WHERE h.action_taken <> '6'
      AND h.msa_md <> '99999'
      AND h.product IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6, 7
    """


# Which states each MSA has applications in (an MSA can cross state lines, so it is listed under
# every state it has records in, not just its principal state). Used by the MSA analysis page.
STATES_TABLE = "msa_states"
STATES_SQL = """
    SELECT DISTINCT msa_md, state_code
    FROM hmda_clean
    WHERE msa_md <> '99999' AND state_code ~ '^[A-Z]{2}$'
"""


def version() -> str:
    return "v:" + hashlib.sha1((_select_sql() + STATES_SQL).encode()).hexdigest()[:12]


def statements() -> list[str]:
    return [
        f"DROP TABLE IF EXISTS {TABLE}",
        f"CREATE TABLE {TABLE} AS {_select_sql()}",
        f"CREATE INDEX ON {TABLE} (msa_md, product)",
        f"CREATE INDEX ON {TABLE} (lei)",
        f"DROP TABLE IF EXISTS {STATES_TABLE}",
        f"CREATE TABLE {STATES_TABLE} AS {STATES_SQL}",
        # Written last, so an interrupted build is never mistaken for a current one.
        f"COMMENT ON TABLE {TABLE} IS '{version()}'",
    ]


# `execute` is any function that runs one SQL string and returns something with .fetchone():
# psycopg's conn.execute, or SQLAlchemy's conn.exec_driver_sql.

def source_ready(execute) -> bool:
    return execute("SELECT to_regclass('hmda_with_census') IS NOT NULL").fetchone()[0]


def is_current(execute) -> bool:
    row = execute(f"SELECT obj_description(to_regclass('{TABLE}'), 'pg_class')").fetchone()
    return row[0] == version()


def build(execute) -> None:
    for sql in statements():
        execute(sql)
