"""Redlining risk: lender vs. peer application share in majority-minority (or majority-Black,
majority-Hispanic, majority Black + Hispanic) census tracts.

Run inside the dev container:
    streamlit run app.py

The test logic lives in redlining.py; this file only builds the page.
Opens on a summary of lenders ranked by number of risk areas. Click a row (or pick a lender in the
search box) to open that lender's profile.
"""

import pandas as pd
import streamlit as st

import redlining as rl
from db import query, require_data

st.set_page_config(page_title="Redlining Peer Analysis", layout="wide")
require_data()

# --- Page state ------------------------------------------------------------------------
st.session_state.setdefault("lender", None)       # LEI of the open profile, or None for the summary
st.session_state.setdefault("table_version", 0)   # bumped to clear the summary table's row selection

# --- Sidebar -----------------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    tract_type = st.radio(
        "Tract type",
        list(rl.TRACT_TYPES),
        format_func=lambda code: f"{code} · {rl.TRACT_TYPES[code][0]}",
        help=f"A tract counts when {rl.THRESHOLD}% or more of its residents are in the group.",
    )
    product = st.radio("Product", list(rl.PRODUCTS))
    depository_only = st.checkbox(
        "Depository institutions only",
        help="Banks, savings institutions and credit unions. Non-bank mortgage companies and "
        "bank-owned mortgage subsidiaries are excluded.",
    )
    peers_all = False
    if depository_only:
        peers_all = st.checkbox(
            "Still compare against all lenders as peers",
            help="When ticked, only depository institutions are tested, but their peer groups "
            "include non-depository lenders too.",
        )
    alpha = st.selectbox("Significance level", [0.05, 0.01, 0.10], format_func=lambda a: f"{a:.2f}")

tt_label, tt_desc, _ = rl.TRACT_TYPES[tract_type]
tt_lower = tt_label[0].lower() + tt_label[1:]  # "majority-Black": keeps group names capitalized


@st.cache_data(ttl=600)
def load_counts() -> pd.DataFrame:
    return query(rl.COUNTS_SQL, threshold=rl.THRESHOLD)


@st.cache_data(ttl=600)
def run_tests(tract_type: str, product: str, depository_targets: bool, depository_peers: bool) -> pd.DataFrame:
    counts = rl.combine_products(load_counts(), product, tract_type)
    return rl.peer_tests(counts, depository_targets, depository_peers)


results = run_tests(tract_type, product, depository_only, depository_only and not peers_all)
if results.empty:
    st.title("Redlining peer analysis")
    st.info(f"No lender has {rl.MIN_APPS}+ applications in any MSA with these settings.")
    st.stop()
results["result"] = rl.flag(results, alpha)
summary = rl.lender_summary(results)

scope = f"{tract_type} ({tt_lower} tracts) · {product}"
if depository_only:
    scope += " · depository institutions only" + (" (peers: all lenders)" if peers_all else " (peers: depository only)")

# --- Lender search box (shared by both views) ---------------------------------------------
names = dict(zip(summary["lei"], summary["respondent_name"]))
options = sorted(names, key=lambda x: names[x].lower())
if st.session_state.lender not in names:   # e.g. settings changed and this lender is no longer tested
    st.session_state.lender = None


def back_to_summary():
    st.session_state.lender = None
    st.session_state.table_version += 1


def open_from_table():
    selection = st.session_state[f"summary_{st.session_state.table_version}"].selection
    # Opens on a click anywhere in a row: the row checkbox (rows) or any cell (cells = [(row, column)])
    rows = list(selection.get("rows", [])) + [cell[0] for cell in selection.get("cells", [])]
    if rows:
        st.session_state.lender = st.session_state.summary_leis[rows[0]]


# --- Column formats -----------------------------------------------------------------------
result_columns = {
    "msa": "MSA",
    "respondent_name": "Lender",
    "target_apps": st.column_config.NumberColumn("Lender apps", format="localized"),
    "target_share": st.column_config.NumberColumn(f"Lender {tract_type} share", format="percent"),
    "peer_count": "Peers",
    "peer_apps": st.column_config.NumberColumn("Peer apps", format="localized"),
    "peer_share": st.column_config.NumberColumn(f"Peer {tract_type} share", format="percent"),
    "difference": st.column_config.NumberColumn("Difference", format="percent"),
    "statistic": st.column_config.NumberColumn("Chi-square", format="%.2f"),
    "p_value": st.column_config.NumberColumn("p-value", format="%.4g"),
    "method": "Test",
    "result": "Result",
}


def highlight(row):
    color = "background-color: rgba(220, 38, 38, 0.18)" if row["result"] == rl.BELOW else ""
    return [color] * len(row)


METHOD = f"""
**Question.** Does a lender take a significantly different share of its applications from a type of census
tract than comparable lenders in the same market? A significantly *lower* share is a redlining risk indicator.

**Tract types.** A tract counts when {rl.THRESHOLD}% or more of its residents are in the group:

- **MMCT, majority-minority:** minority residents, meaning everyone except non-Hispanic White (FFIEC minority %, the same figure HMDA reports).
- **MBCT, majority-Black:** non-Hispanic residents who are Black alone.
- **MHCT, majority-Hispanic:** Hispanic or Latino residents of any race.
- **MBHCT, majority Black + Hispanic:** the two groups above added together. They don't overlap, so nobody is counted twice.

Population counts come from the FFIEC 2025 Census flat file (2020 Census race and ethnicity by tract).
California has 6,100 MMCTs, 45 MBCTs, 2,862 MHCTs and 3,505 MBHCTs. With so few MBCTs, most MBCT tests
rest on very small counts.

**Products.** Mortgage = closed-end; HELOC = open-end line of credit; combined = both added together.

**Applications counted.** HMDA records for the selected product, excluding purchased loans (action taken 6),
records with no census tract data, records outside an MSA (code 99999), and records where the open-end flag is "Exempt".

**Target lenders.** Every lender with at least {rl.MIN_APPS} applications in an MSA (or metropolitan division)
for the product. Each MSA is tested separately. MSAs where no lender has any applications from the chosen
tract type are skipped, since there is nothing to compare there. This matters most for MBCT, because
majority-Black tracts exist in only a few California MSAs.

**Peers.** All other lenders in the same MSA and product with {rl.PEER_LOW:.0%}–{rl.PEER_HIGH:.0%} of the
target's application count there. Their applications are pooled.

**Depository institutions.** Banks, savings institutions and credit unions: lenders the CFPB's 2023 Reporter Panel
lists as a depository institution (other lender code 0). Lenders not in that panel are classified by their 2025
regulator (OCC, Federal Reserve, FDIC or NCUA = depository; HUD or CFPB = not). The Reporter Panel was discontinued
after 2023.

**Test.** 2×2 table (lender vs. pooled peers × tract of the chosen type vs. other tracts), Pearson chi-square
with 1 degree of freedom and no continuity correction. When any expected cell count is below 5, Fisher's exact
test is used instead. Results are flagged at the significance level chosen in the sidebar. No adjustment is made
for running many tests at once, so expect some significant results by chance.
"""

# =========================================================================================
# Summary view
# =========================================================================================
if st.session_state.lender is None:
    st.title("Redlining peer analysis")
    st.caption(scope)

    st.selectbox(
        "Find a lender",
        options,
        placeholder="Type a lender name…",
        format_func=lambda x: f"{names[x]}  ·  {x}",
        key="lender",
    )

    flagged = summary[summary["risk_areas"] > 0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Lenders tested", f"{len(summary):,}")
    c2.metric("Lenders with a risk area", f"{len(flagged):,}")
    c3.metric("Risk areas (lender-MSA)", f"{int(summary['risk_areas'].sum()):,}")

    st.subheader("Lenders with the most risk areas")
    st.caption(
        f"A risk area is an MSA where the lender's share of applications from {tt_lower} tracts "
        f"({tract_type}s) is significantly below its peers' (p < {alpha}). **Click a lender to open its profile.**"
    )
    show_all = st.toggle("Include lenders with no risk areas", value=False)
    table = (summary if show_all else flagged).reset_index(drop=True)
    st.session_state.summary_leis = table["lei"].tolist()
    st.dataframe(
        table[["respondent_name", "is_depository", "risk_areas", "msas_tested", "min_p_value",
               "largest_shortfall", "risk_msas"]],
        hide_index=True,
        width="stretch",
        height=560,
        on_select=open_from_table,
        selection_mode=["single-row", "single-cell"],
        key=f"summary_{st.session_state.table_version}",
        column_config={
            "respondent_name": st.column_config.TextColumn("Lender", width="medium"),
            "is_depository": st.column_config.CheckboxColumn("Depository"),
            "risk_areas": st.column_config.NumberColumn("Risk areas"),
            "msas_tested": st.column_config.NumberColumn("MSAs tested"),
            "min_p_value": st.column_config.NumberColumn("Smallest p-value", format="%.2e"),
            "largest_shortfall": st.column_config.NumberColumn(
                "Largest shortfall", format="percent",
                help=f"Biggest gap (in percentage points) between the lender's {tract_type} share and its "
                "peers', among its risk areas"),
            "risk_msas": st.column_config.TextColumn("Risk MSAs (p-value)", width="large"),
        },
    )

    with st.expander("All lender-MSA results"):
        only_flags = st.toggle("Only show risk areas", value=True)
        view = results[results["result"] == rl.BELOW] if only_flags else results
        st.dataframe(view[list(result_columns)].style.apply(highlight, axis=1),
                     hide_index=True, width="stretch", column_config=result_columns)
        st.download_button(
            "Download as CSV",
            view.drop(columns=["msa_md"]).assign(tract_type=tract_type, product=product).to_csv(index=False),
            file_name=f"redlining_peer_tests_{tract_type.lower()}.csv",
            mime="text/csv",
        )

    with st.expander("Method"):
        st.markdown(METHOD)
    st.stop()

# =========================================================================================
# Lender profile view
# =========================================================================================
lei = st.session_state.lender
mine = results[results["lei"] == lei].sort_values("target_apps", ascending=False)
info = summary[summary["lei"] == lei].iloc[0]

st.button("← All lenders", on_click=back_to_summary)
st.selectbox(
    "Lender",
    options,
    placeholder="Type a lender name…",
    format_func=lambda x: f"{names[x]}  ·  {x}",
    key="lender",
)
st.title(info.respondent_name)
st.caption(f"LEI {lei} · {'Depository institution' if info.is_depository else 'Non-depository lender'} · {scope}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("MSAs tested", int(info.msas_tested))
c2.metric("Risk areas", int(info.risk_areas))
c3.metric("Smallest p-value", rl.format_p(info.min_p_value) if pd.notna(info.min_p_value) else "—")
c4.metric("Apps in tested MSAs", f"{int(info.apps_tested):,}")

shown = [c for c in result_columns if c != "respondent_name"]
st.dataframe(mine[shown].style.apply(highlight, axis=1), hide_index=True, width="stretch",
             column_config=result_columns)

chart = mine.melt(
    id_vars="msa", value_vars=["target_share", "peer_share"], var_name="group", value_name="share"
).replace({"target_share": "Lender", "peer_share": "Peers"})
st.bar_chart(chart, x="msa", y="share", color="group", stack=False, horizontal=True,
             height=max(250, 45 * len(mine)),
             x_label=f"Share of applications in {tt_lower} tracts ({tract_type})", y_label="")

with st.expander("Details for one MSA: 2×2 table and peer list"):
    msa = st.selectbox("MSA", mine["msa_md"].tolist(),
                       format_func=dict(zip(mine["msa_md"], mine["msa"])).__getitem__)
    r = mine[mine["msa_md"] == msa].iloc[0]
    st.dataframe(
        pd.DataFrame(
            {
                f"{tt_label} tracts": [r.target_tt_apps, r.peer_tt_apps],
                "Other tracts": [r.target_apps - r.target_tt_apps, r.peer_apps - r.peer_tt_apps],
                "Total": [r.target_apps, r.peer_apps],
            },
            index=["Lender", f"Peers ({r.peer_count})"],
        )
    )
    counts = rl.combine_products(load_counts(), product, tract_type)
    peers = rl.find_peers(counts[counts["msa_md"] == msa], lei, r.target_apps,
                          depository_only and not peers_all)
    peers = peers.assign(tt_share=peers["tt_apps"] / peers["apps"]).sort_values("apps", ascending=False)
    st.caption(
        f"Peers: lenders with {int(rl.PEER_LOW * r.target_apps):,}–{int(rl.PEER_HIGH * r.target_apps):,} "
        "applications in this MSA."
    )
    st.dataframe(
        peers[["respondent_name", "is_depository", "lei", "apps", "tt_apps", "tt_share"]],
        hide_index=True,
        width="stretch",
        column_config={
            "respondent_name": "Peer", "is_depository": st.column_config.CheckboxColumn("Depository"),
            "lei": "LEI", "apps": "Apps", "tt_apps": f"{tract_type} apps",
            "tt_share": st.column_config.NumberColumn(f"{tract_type} share", format="percent"),
        },
    )

with st.expander("Method"):
    st.markdown(METHOD)
