"""MSA analysis: pick a state, then an MSA, and review every lender there.

With a state picked and no MSA yet, the page lists the state's MSAs with their counts of tested
lenders and risk areas (click one to open it). With an MSA picked, it lists the lenders there with
their redlining peer-test result and any significant denial disparities. Click a lender to open it
in the Lender analysis tab.

Test logic: redlining.py and disparity.py. This file only builds the page.
"""

import pandas as pd
import streamlit as st

import redlining as rl
import ui
from db import require_data

st.set_page_config(page_title="MSA analysis", layout="wide")
require_data()

s = ui.sidebar()
tract_type, alpha, tt_lower = s.tract_type, s.alpha, s.tt_lower

st.title("MSA analysis")
st.caption(s.scope)

results = ui.redlining_results(s)
if results.empty:
    st.info(f"No lender has {rl.MIN_APPS}+ applications in any MSA with these settings.")
    st.stop()
flags = ui.denial_flags(s)
results = results.merge(flags.rename("denial_flags"), left_on=["lei", "msa_md"], right_index=True, how="left")
results["denial_flags"] = results["denial_flags"].fillna("")

combined = ui.combined_for(s)
states = ui.msa_states()

# --- MSA-level totals (every lender, tested or not) ----------------------------------------
msa_totals = combined.groupby(["msa_md", "msa_name"], as_index=False)[["apps", "tt_apps"]].sum()
msa_totals["tt_share"] = msa_totals["tt_apps"] / msa_totals["apps"].where(msa_totals["apps"] > 0)
per_msa = results.groupby("msa_md").agg(
    lenders_tested=("lei", "size"),
    risk_lenders=("result", lambda r: int((r == rl.BELOW).sum())),
    denial_lenders=("denial_flags", lambda f: int((f != "").sum())),
)
msa_totals = msa_totals.merge(per_msa, left_on="msa_md", right_index=True, how="left").fillna(
    {"lenders_tested": 0, "risk_lenders": 0, "denial_lenders": 0})
msa_totals["label"] = msa_totals["msa_name"] + " (" + msa_totals["msa_md"] + ")"
labels = dict(zip(msa_totals["msa_md"], msa_totals["label"]))

# --- State and MSA pickers -------------------------------------------------------------------
state_options = sorted(states.loc[states["msa_md"].isin(msa_totals["msa_md"]), "state_code"].unique())
if st.session_state.get("ma_state") not in state_options:
    st.session_state["ma_state"] = state_options[0] if len(state_options) == 1 else None
if "ma_msa" not in st.session_state:
    st.session_state["ma_msa"] = None


def clear_msa():
    st.session_state["ma_msa"] = None


c1, c2 = st.columns([1, 3])
state = c1.selectbox("State", state_options, placeholder="Pick a state…", key="ma_state", on_change=clear_msa)
if state is None:
    st.info("Pick a state to see its MSAs.")
    st.stop()

in_state = msa_totals[msa_totals["msa_md"].isin(states.loc[states["state_code"] == state, "msa_md"])]
in_state = in_state.sort_values("apps", ascending=False)
msa_options = in_state["msa_md"].tolist()
if st.session_state["ma_msa"] not in msa_options:
    st.session_state["ma_msa"] = None
msa = c2.selectbox("MSA", msa_options, format_func=labels.__getitem__, placeholder="Pick an MSA…",
                   key="ma_msa")

# =========================================================================================
# State overview: the state's MSAs
# =========================================================================================
if msa is None:
    st.subheader(f"MSAs in {state}")
    c1, c2, c3 = st.columns(3)
    c1.metric("MSAs", len(in_state))
    c2.metric("Lender-MSAs tested", f"{int(in_state['lenders_tested'].sum()):,}")
    c3.metric("Redlining risk areas", f"{int(in_state['risk_lenders'].sum()):,}")
    st.caption(
        f"Click an MSA to review its lenders. Risk areas: tested lenders whose share of applications from "
        f"{tt_lower} tracts ({tract_type}s) is significantly below their peers' (p < {alpha})."
    )

    def open_msa():
        rows = ui.clicked_rows(st.session_state["ma_state_table"].selection)
        if rows:
            st.session_state["ma_msa"] = st.session_state["ma_state_msas"][rows[0]]

    table = in_state.reset_index(drop=True)
    st.session_state["ma_state_msas"] = table["msa_md"].tolist()
    st.dataframe(
        ui.styled(table[["label", "apps", "tt_share", "lenders_tested", "risk_lenders", "denial_lenders"]],
                  ints=["apps", "lenders_tested", "risk_lenders", "denial_lenders"], pct=["tt_share"]),
        hide_index=True, width="stretch", height=min(700, 38 + 35 * len(table)),
        on_select=open_msa, selection_mode=["single-row", "single-cell"], key="ma_state_table",
        column_config={
            "label": st.column_config.TextColumn("MSA", width="large"),
            "apps": st.column_config.Column("Applications", help="All lenders, with census tract data"),
            "tt_share": st.column_config.Column(f"{tract_type} share",
                                                help=f"Share of all applications from {tt_lower} tracts"),
            "lenders_tested": st.column_config.Column("Lenders tested",
                                                      help=f"Lenders with {rl.MIN_APPS}+ applications"),
            "risk_lenders": st.column_config.Column("Risk areas", help="Tested lenders significantly below peers"),
            "denial_lenders": st.column_config.Column(
                "Lenders with a denial disparity",
                help="Tested lenders with a significantly higher denial rate for at least one applicant group"),
        },
    )
    st.stop()

# =========================================================================================
# One MSA: its lenders
# =========================================================================================
st.button(f"← All MSAs in {state}", on_click=clear_msa)
m = in_state[in_state["msa_md"] == msa].iloc[0]
here = results[results["msa_md"] == msa].copy()
here["is_risk"] = here["result"] == rl.BELOW
here = here.sort_values(["is_risk", "p_value", "target_apps"], ascending=[False, True, False],
                        na_position="last")

st.subheader(m.label)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Applications", f"{int(m.apps):,}", help="All lenders, with census tract data")
c2.metric(f"{tract_type} share", f"{m.tt_share:.1%}" if pd.notna(m.tt_share) else "—",
          help=f"Share of all applications in the MSA from {tt_lower} tracts")
c3.metric("Lenders tested", int(m.lenders_tested), help=f"Lenders with {rl.MIN_APPS}+ applications")
c4.metric("Risk areas", int(m.risk_lenders),
          help=f"Tested lenders whose {tract_type} share is significantly below their peers' (p < {alpha})")

if here.empty:
    st.info(f"No lender has {rl.MIN_APPS}+ applications here with these settings, or the MSA has no "
            f"{tt_lower} tracts.")
else:
    st.caption("Click a lender to open it in **Lender analysis**, with this MSA's peers.")
    only_risk = st.toggle("Only lenders that are risk areas or have a denial disparity", value=False)
    view = here[here["is_risk"] | (here["denial_flags"] != "")] if only_risk else here
    view = view.reset_index(drop=True)

    def risk_rows(row):
        return [ui.RED if row["result"] == rl.BELOW else ""] * len(row)

    cols = ["respondent_name", "is_depository", "target_apps", "target_share", "peer_share",
            "median_peer_share", "difference", "peer_count", "p_value", "result", "denial_flags"]
    event = st.dataframe(
        ui.styled(view[cols], ints=["target_apps", "peer_count"],
                  pct=["target_share", "peer_share", "median_peer_share", "difference"],
                  pval=["p_value"]).apply(risk_rows, axis=1),
        hide_index=True, width="stretch", on_select="rerun", selection_mode=["single-row", "single-cell"],
        key=f"ma_lenders_{msa}_{only_risk}",
        column_config={
            "respondent_name": st.column_config.TextColumn("Lender", width="medium"),
            "is_depository": st.column_config.CheckboxColumn("Depository"),
            "target_apps": "Apps",
            "target_share": f"{tract_type} share",
            "peer_share": st.column_config.Column("Peers pooled", help="Pooled share of the lender's peers"),
            "median_peer_share": st.column_config.Column(
                "Median peer", help=f"Hypothetical median peer: median of the peers' own {tract_type} shares"),
            "difference": st.column_config.Column("Difference vs pooled"),
            "peer_count": "Peers",
            "p_value": "p-value",
            "result": "Redlining result",
            "denial_flags": st.column_config.TextColumn(
                "Significant denial disparities", width="large",
                help=f"Applicant groups with a significantly higher denial rate than their comparison "
                f"group (p < {alpha}), with the AIR"),
        },
    )
    if rows := ui.clicked_rows(event.selection):
        ui.open_in_lender_analysis(s, view.loc[rows[0], "lei"], msa)

    st.download_button(
        "Download as CSV",
        here.drop(columns=["is_risk"]).assign(tract_type=tract_type, product=s.product).to_csv(index=False),
        file_name=f"msa_{msa}_{tract_type.lower()}.csv", mime="text/csv",
    )

# Lenders below the testing threshold: shown for completeness, not tested.
small = combined[(combined["msa_md"] == msa) & (combined["apps"] > 0) & (combined["apps"] < rl.MIN_APPS)]
if s.depository_only:
    small = small[small["is_depository"]]
with st.expander(f"Lenders with fewer than {rl.MIN_APPS} applications here ({len(small)}, not tested)"):
    small = small.assign(tt_share=small["tt_apps"] / small["apps"]).sort_values("apps", ascending=False)
    st.dataframe(
        ui.styled(small[["respondent_name", "is_depository", "apps", "tt_apps", "tt_share"]],
                  ints=["apps", "tt_apps"], pct=["tt_share"]),
        hide_index=True, width="stretch",
        column_config={
            "respondent_name": "Lender", "is_depository": st.column_config.CheckboxColumn("Depository"),
            "apps": "Apps", "tt_apps": f"{tract_type} apps", "tt_share": f"{tract_type} share",
        },
    )


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
with st.expander("Method"):
    st.markdown(METHOD)
