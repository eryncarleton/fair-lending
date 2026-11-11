"""Data explorer: denial rates, denial reasons and free-form SQL over the HMDA data.

Every Streamlit "st.something" call adds an element to the page, top to bottom.
Whenever you change a filter, Streamlit simply reruns this whole script.
"""

import pandas as pd
import streamlit as st

from db import query, require_data

st.set_page_config(page_title="Data explorer", layout="wide")

st.title("Data explorer")

require_data()

# --- Filters (sidebar) ---------------------------------------------------------
years = query("SELECT DISTINCT activity_year FROM hmda_clean ORDER BY 1")["activity_year"].tolist()
states = query("SELECT DISTINCT state_code FROM hmda_clean WHERE state_code <> 'NA' ORDER BY 1")["state_code"].tolist()
purposes = query("SELECT DISTINCT loan_purpose FROM hmda_clean WHERE loan_purpose IS NOT NULL ORDER BY 1")["loan_purpose"].tolist()

with st.sidebar:
    st.header("Filters")
    year = st.selectbox("Year", years, index=len(years) - 1)
    state = st.selectbox("State", ["All"] + states)
    purpose = st.selectbox("Loan purpose", ["All"] + purposes)

where = """
    WHERE activity_year = :year
      AND (:state = 'All' OR state_code = :state)
      AND (:purpose = 'All' OR loan_purpose = :purpose)
"""
params = dict(year=year, state=state, purpose=purpose)

# --- Headline numbers ----------------------------------------------------------
totals = query(
    f"""
    SELECT count(*)                                         AS applications,
           count(*) FILTER (WHERE is_decision)              AS decisions,
           avg(is_denied::int) FILTER (WHERE is_decision)   AS denial_rate
    FROM hmda_clean {where}
    """,
    **params,
).iloc[0]

c1, c2, c3 = st.columns(3)
c1.metric("Records", f"{int(totals.applications):,}")
c2.metric("Decided applications", f"{int(totals.decisions):,}")
c3.metric("Denial rate", f"{totals.denial_rate:.1%}" if pd.notna(totals.denial_rate) else "—")

# --- Denial rate by group --------------------------------------------------------
st.subheader("Denial rate by applicant group")
group_col = st.radio(
    "Group by",
    {"derived_race": "Race", "derived_ethnicity": "Ethnicity", "derived_sex": "Sex", "applicant_age": "Age"},
    format_func=lambda c: {"derived_race": "Race", "derived_ethnicity": "Ethnicity",
                           "derived_sex": "Sex", "applicant_age": "Age"}[c],
    horizontal=True,
)

by_group = query(
    f"""
    SELECT {group_col}                                   AS grp,
           count(*) FILTER (WHERE is_decision)           AS decisions,
           avg(is_denied::int) FILTER (WHERE is_decision) AS denial_rate,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY income) AS median_income
    FROM hmda_clean {where}
    GROUP BY 1
    HAVING count(*) FILTER (WHERE is_decision) >= 30
    ORDER BY denial_rate DESC
    """,
    **params,
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
reasons = query(
    f"""
    SELECT denial_reason_1 AS reason, count(*) AS denials
    FROM hmda_clean {where} AND is_denied
    GROUP BY 1 ORDER BY 2 DESC
    """,
    **params,
)
st.bar_chart(reasons, x="reason", y="denials", horizontal=True)

# --- Free-form SQL -----------------------------------------------------------------
with st.expander("Run your own SQL"):
    st.caption("Query the `hmda_clean` view (readable) or the raw `hmda_lar` table (all columns, as text).")
    user_sql = st.text_area(
        "SQL",
        "SELECT loan_type, count(*) AS n, avg(interest_rate) AS avg_rate\n"
        "FROM hmda_clean\nWHERE action_taken = '1'\nGROUP BY 1\nORDER BY 2 DESC;",
        height=150,
    )
    if st.button("Run"):
        try:
            st.dataframe(query(user_sql), hide_index=True)
        except Exception as e:
            st.error(str(e))
