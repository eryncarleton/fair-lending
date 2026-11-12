"""Precomputed table `lender_msa_counts`: one row per lender x MSA x product x loan type.

Every number the Streamlit pages need comes from this table (plus msa_states and the explorer_*
summary tables below), so they load in well under a second
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


# Data explorer summaries, for every year x state x loan purpose, plus "All" for state and/or purpose
# (CUBE). Precomputing every combination keeps medians exact: they can't be added up afterwards.
# NULL loan purposes stay NULL (they count toward "All" but aren't offered as a filter).
_CUBE_KEYS = """activity_year,
       CASE WHEN grouping(state_code) = 1 THEN 'All' ELSE state_code END     AS state_code,
       CASE WHEN grouping(loan_purpose) = 1 THEN 'All' ELSE loan_purpose END AS loan_purpose"""
EXPLORER_DIMS = ["derived_race", "derived_ethnicity", "derived_sex", "applicant_age"]
EXPLORER_SQL = {
    "explorer_totals": f"""
        SELECT {_CUBE_KEYS},
               count(*)                              AS records,
               count(*) FILTER (WHERE is_decision)   AS decisions,
               count(*) FILTER (WHERE is_denied)     AS denied
        FROM hmda_clean
        GROUP BY activity_year, CUBE (state_code, loan_purpose)""",
    "explorer_groups": "\n        UNION ALL\n".join(f"""
        SELECT {_CUBE_KEYS},
               '{dim}'                               AS dim,
               {dim}                                 AS grp,
               count(*) FILTER (WHERE is_decision)   AS decisions,
               count(*) FILTER (WHERE is_denied)     AS denied,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY income) AS median_income
        FROM hmda_clean
        GROUP BY activity_year, {dim}, CUBE (state_code, loan_purpose)""" for dim in EXPLORER_DIMS),
    "explorer_reasons": f"""
        SELECT {_CUBE_KEYS},
               denial_reason_1                       AS reason,
               count(*)                              AS denials
        FROM hmda_clean
        WHERE is_denied
        GROUP BY activity_year, denial_reason_1, CUBE (state_code, loan_purpose)""",
}

# Every precomputed table, in the order the hosted export copies them.
PRECOMPUTED = [TABLE, STATES_TABLE, *EXPLORER_SQL]


def version() -> str:
    defs = _select_sql() + STATES_SQL + "".join(EXPLORER_SQL.values())
    return "v:" + hashlib.sha1(defs.encode()).hexdigest()[:12]


def statements() -> list[str]:
    stmts = [
        f"DROP TABLE IF EXISTS {TABLE}",
        f"CREATE TABLE {TABLE} AS {_select_sql()}",
        f"CREATE INDEX ON {TABLE} (msa_md, product)",
        f"CREATE INDEX ON {TABLE} (lei)",
        f"DROP TABLE IF EXISTS {STATES_TABLE}",
        f"CREATE TABLE {STATES_TABLE} AS {STATES_SQL}",
    ]
    for name, sql in EXPLORER_SQL.items():
        stmts += [f"DROP TABLE IF EXISTS {name}", f"CREATE TABLE {name} AS {sql}"]
    # Written last, so an interrupted build is never mistaken for a current one.
    return stmts + [f"COMMENT ON TABLE {TABLE} IS '{version()}'"]


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
