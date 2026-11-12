"""Data explorer: denial rates, denial reasons and free-form SQL over the HMDA data.

Every Streamlit "st.something" call adds an element to the page, top to bottom.
Whenever you change a filter, Streamlit simply reruns this whole script.

The charts read small precomputed summary tables (explorer_* in counts.py), with every year x state
x loan purpose combination already worked out, "All" included. Only free-form SQL reads the full
records: PostgreSQL locally, DuckDB when hosted (see data.py).
"""

import pandas as pd
import streamlit as st

import data
from db import require_data

st.set_page_config(page_title="Data explorer", layout="wide")

st.title("Data explorer")

require_data()

totals_all = data.table("explorer_totals")
groups_all = data.table("explorer_groups")
reasons_all = data.table("explorer_reasons")

# --- Filters (sidebar) ---------------------------------------------------------
years = sorted(totals_all["activity_year"].unique())
states = sorted(s for s in totals_all["state_code"].dropna().unique() if s not in ("All", "NA"))
purposes = sorted(p for p in totals_all["loan_purpose"].dropna().unique() if p != "All")

with st.sidebar:
    st.header("Filters")
    year = st.selectbox("Year", years, index=len(years) - 1)
    state = st.selectbox("State", ["All"] + states)
    purpose = st.selectbox("Loan purpose", ["All"] + purposes)


def pick(df: pd.DataFrame) -> pd.DataFrame:
    """Rows for the chosen year, state and loan purpose ("All" rows are precomputed too)."""
    return df[(df["activity_year"] == year) & (df["state_code"] == state) & (df["loan_purpose"] == purpose)]


# --- Headline numbers ----------------------------------------------------------
t = pick(totals_all)
records, decisions, denied = (int(t[c].sum()) for c in ("records", "decisions", "denied"))

c1, c2, c3 = st.columns(3)
c1.metric("Records", f"{records:,}")
c2.metric("Decided applications", f"{decisions:,}")
c3.metric("Denial rate", f"{denied / decisions:.1%}" if decisions else "—")

# --- Denial rate by group --------------------------------------------------------
st.subheader("Denial rate by applicant group")
labels = {"derived_race": "Race", "derived_ethnicity": "Ethnicity", "derived_sex": "Sex", "applicant_age": "Age"}
group_col = st.radio("Group by", list(labels), format_func=labels.__getitem__, horizontal=True)

by_group = pick(groups_all)
by_group = by_group[(by_group["dim"] == group_col) & (by_group["decisions"] >= 30)]
by_group = (
    by_group.assign(denial_rate=by_group["denied"] / by_group["decisions"])
    [["grp", "decisions", "denial_rate", "median_income"]]
    .sort_values("denial_rate", ascending=False)
)

left, right = st.columns([3, 2])
left.bar_chart(by_group, x="grp", y="denial_rate", horizontal=True)
right.dataframe(
    by_group,
    hide_index=True,
    column_config={
        "grp": "Group",
        "decisions": st.column_config.NumberColumn("Decisions", format="localized"),
        "denial_rate": st.column_config.NumberColumn("Denial rate", format="percent"),
        "median_income": st.column_config.NumberColumn("Median income", format="dollar"),
    },
)
st.caption("Groups with fewer than 30 decided applications are hidden.")

# --- Top denial reasons ----------------------------------------------------------
st.subheader("Top denial reasons")
reasons = pick(reasons_all)[["reason", "denials"]].sort_values("denials", ascending=False)
st.bar_chart(reasons, x="reason", y="denials", horizontal=True)

# --- Free-form SQL -----------------------------------------------------------------
with st.expander("Run your own SQL"):
    st.caption(
        f"Query the `hmda_clean` table (readable), `hmda_with_census` (plus census tract columns) or the raw "
        f"`hmda_lar` table (all columns, as text). This copy runs **{data.sql_dialect()}** SQL"
        + (", read-only; queries stop after "
           f"{data.SQL_TIMEOUT_S} seconds." if data.HOSTED else ".")
        + f" At most {data.SQL_MAX_ROWS:,} rows are shown."
    )
    user_sql = st.text_area(
        "SQL",
        "SELECT loan_type, count(*) AS n, avg(interest_rate) AS avg_rate\n"
        "FROM hmda_clean\nWHERE action_taken = '1'\nGROUP BY 1\nORDER BY 2 DESC;",
        height=150,
    )
    if st.button("Run"):
        try:
            result, truncated = data.run_sql(user_sql)
            st.dataframe(result, hide_index=True)
            if truncated:
                st.caption(f"Showing the first {data.SQL_MAX_ROWS:,} rows.")
        except Exception as e:
            st.error(str(e))
