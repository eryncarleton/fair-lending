"""Lender analysis: pick a lender, see its MSA risk areas, then compare it with its peers in one MSA.

For the chosen MSA the page shows the redlining comparison (share of applications from the chosen
tract type) and underwriting outcomes by applicant group (denial rates, adverse impact ratio,
significance), each against every peer and against a hypothetical "median peer".

Test logic: redlining.py (peer test) and disparity.py (denial rates / AIR). This file builds the page.
"""

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import disparity as dp
import redlining as rl
import ui
from db import require_data

st.set_page_config(page_title="Lender analysis", layout="wide")
require_data()
s = ui.sidebar()
results = ui.redlining_results(s)

st.title("Lender analysis")
st.caption(s.scope)
if results.empty:
    st.info(f"No lender has {rl.MIN_APPS}+ applications in any MSA with these settings.")
    st.stop()

summary = rl.lender_summary(results)
names = dict(zip(summary["lei"], summary["respondent_name"]))
options = sorted(names, key=lambda x: names[x].lower())

# Keep the chosen lender when switching pages; drop it if the new settings don't test it.
# Remember the lender picked last ("la_wanted"), so it comes back after a setting that briefly
# leaves it untested (e.g. a loan-type filter) is undone.
chosen = st.session_state.get("la_lender") or st.session_state.get("la_wanted")
st.session_state["la_lender"] = chosen if chosen in names else None
if st.session_state["la_lender"]:
    st.session_state["la_wanted"] = st.session_state["la_lender"]
lei = st.selectbox(
    "Lender",
    options,
    placeholder="Type a lender name…",
    format_func=lambda x: f"{names[x]}  ·  {x}",
    key="la_lender",
)
if lei is None:
    st.info(
        f"Pick a lender to see its MSA risk areas and its peers. {len(names):,} lenders have "
        f"{rl.MIN_APPS}+ applications in at least one MSA with these settings."
    )
    st.stop()

info = summary[summary["lei"] == lei].iloc[0]
mine = results[results["lei"] == lei].copy()

# Denial-rate metrics for this lender in every MSA it's tested in.
own_counts = ui.lender_rows(s, lei)
own_counts = own_counts[own_counts["msa_md"].isin(mine["msa_md"])]
own_metrics = dp.long_metrics(own_counts, s.alpha, ["msa_md"])


def flag_text(m: pd.DataFrame) -> str:
    hits = m[m["finding"].isin(dp.ADVERSE)]
    return "; ".join(f"{g} (AIR {a:.2f})" for g, a in zip(hits["group_label"], hits["air"]))


group_flags = own_metrics.groupby("msa_md").apply(flag_text, include_groups=False)
mine["denial_flags"] = mine["msa_md"].map(group_flags).fillna("")
mine["is_risk"] = mine["result"] == rl.BELOW
mine = mine.sort_values(["is_risk", "p_value"], ascending=[False, True], na_position="last")

st.header(info.respondent_name)
st.caption(f"LEI {lei} · {'Depository institution' if info.is_depository else 'Non-depository lender'}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("MSAs tested", int(info.msas_tested))
c2.metric("Risk areas", int(info.risk_areas),
          help=f"MSAs where the lender's {s.tract_type} share is significantly below its peers'")
c3.metric("Denial disparity MSAs", int((mine["denial_flags"] != "").sum()),
          help="MSAs where at least one applicant group has a significantly higher denial rate "
          "than its comparison group")
c4.metric("Apps in tested MSAs", f"{int(info.apps_tested):,}")

# =========================================================================================
# MSA risk areas
# =========================================================================================
st.subheader("MSA risk areas")
risk = mine[mine["is_risk"]]
if risk.empty:
    st.success(f"No redlining risk areas: in no MSA is this lender's {s.tract_type} share "
               f"significantly below its peers' (p < {s.alpha}). All tested MSAs are listed.")
show_all = st.toggle("Show all tested MSAs", value=risk.empty, disabled=risk.empty)
msa_table = mine if show_all else risk

msa_columns = {
    "msa": "MSA",
    "target_apps": st.column_config.Column("Lender apps"),
    "target_share": st.column_config.Column(f"Lender {s.tract_type} share"),
    "peer_share": st.column_config.Column("Peers pooled"),
    "median_peer_share": st.column_config.Column(
        "Median peer",
        help=f"Median of the peers' own {s.tract_type} shares"),
    "difference": st.column_config.Column("Difference vs pooled"),
    "peer_count": "Peers",
    "p_value": st.column_config.Column("p-value"),
    "result": "Redlining result",
    "denial_flags": st.column_config.TextColumn(
        "Significant denial disparities", width="large",
        help=f"Groups with a significantly higher denial rate than their comparison group (p < {s.alpha})"),
}


def risk_rows(row):
    return [ui.RED if row["result"] == rl.BELOW else ""] * len(row)


st.dataframe(ui.styled(msa_table[list(msa_columns)], ints=["target_apps", "peer_count"],
                       pct=["target_share", "peer_share", "median_peer_share", "difference"],
                       pval=["p_value"]).apply(risk_rows, axis=1), hide_index=True,
             width="stretch", column_config=msa_columns)

# =========================================================================================
# Peers in one MSA
# =========================================================================================
msa_names = dict(zip(mine["msa_md"], mine["msa"]))
msa_key = f"la_msa_{lei}"
if st.session_state.get(msa_key) not in msa_names:  # e.g. new settings: this MSA no longer tested
    st.session_state.pop(msa_key, None)
msa = st.selectbox("MSA to compare with peers", mine["msa_md"].tolist(), format_func=msa_names.__getitem__,
                   key=msa_key)
r = mine[mine["msa_md"] == msa].iloc[0]
peers = ui.peers_of(s, lei, msa, r.target_apps)
own = own_counts[own_counts["msa_md"] == msa]

st.subheader(f"Peers in {r.msa_name}")
st.caption(
    f"{len(peers)} peers: lenders with {int(rl.PEER_LOW * r.target_apps):,}–{int(rl.PEER_HIGH * r.target_apps):,} "
    f"{rl.PRODUCT_PHRASE[s.product]} applications in this MSA (the lender has {int(r.target_apps):,})"
    + (f" of loan types {', '.join(s.loan_types)}" if set(s.loan_types) != set(rl.LOAN_TYPES) else "")
    + (", depository institutions only" if s.depository_peers else "") + "."
)
if peers.empty:
    st.info("No peers in range in this MSA, so there is nothing to compare with.")
    st.stop()

LENDER, MEDIAN, POOLED = "Lender", "Median peer", "Pooled peers"


def role_rows(row):
    role = row.get("role", "")
    if role == LENDER:
        return ["background-color: rgba(37, 99, 235, 0.14)"] * len(row)
    if role in (MEDIAN, POOLED):
        return ["background-color: rgba(100, 116, 139, 0.14)"] * len(row)
    return [""] * len(row)


# --- Redlining: share of applications from the chosen tract type -----------------------------
st.markdown(f"**Applications from {s.tt_lower} tracts ({s.tract_type})**")
rl_table = pd.concat([
    pd.DataFrame([
        {"role": LENDER, "name": info.respondent_name, "apps": r.target_apps,
         "tt_apps": r.target_tt_apps, "tt_share": r.target_share},
        {"role": MEDIAN, "name": f"hypothetical: median of {len(peers)} peers", "apps": peers["apps"].median(),
         "tt_apps": peers["tt_apps"].median(), "tt_share": r.median_peer_share},
        {"role": POOLED, "name": f"{len(peers)} peers", "apps": r.peer_apps,
         "tt_apps": r.peer_tt_apps, "tt_share": r.peer_share},
    ]),
    peers.rename(columns={"respondent_name": "name"}).assign(role="Peer")[
        ["role", "name", "apps", "tt_apps", "tt_share"]],
], ignore_index=True)
st.dataframe(
    ui.styled(rl_table, ints=["apps", "tt_apps"], pct=["tt_share"]).apply(role_rows, axis=1), hide_index=True, width="stretch",
    column_config={
        "role": "", "name": "Lender",
        "apps": st.column_config.Column("Apps"),
        "tt_apps": st.column_config.Column(f"{s.tract_type} apps"),
        "tt_share": st.column_config.Column(f"{s.tract_type} share"),
    },
)
p_text = ("p < 0.0001" if r.p_value < 0.0001 else f"p = {r.p_value:.4f}") if pd.notna(r.p_value) else "no test"
st.caption(f"Lender vs. pooled peers: {r.method}, {p_text} → **{r.result}**.")

# --- Product and loan-type mix: the lender vs. one peer, the median peer or pooled peers -----
st.markdown("**Product and loan-type mix**")
peer_names = dict(zip(peers["lei"], peers["respondent_name"]))
mix_options = [MEDIAN, POOLED, *peers["lei"]]
if st.session_state.get("la_mix_peer") not in mix_options:
    st.session_state["la_mix_peer"] = MEDIAN
compare = st.selectbox(
    "Compare the lender with", mix_options, key="la_mix_peer",
    format_func=lambda x: {MEDIAN: f"{MEDIAN} (hypothetical)", POOLED: f"{POOLED} ({len(peers)})"}.get(
        x, peer_names.get(x, x)),
)
compare_label = peer_names.get(compare, compare)
MIX_ORDER = {"product": ["Closed-end (mortgage)", "Open-end (HELOC)"], "loan_type": [*rl.LOAN_TYPES, "Unknown"]}


def mix_shares(by: str) -> pd.DataFrame:
    """Long table: category, who (lender / comparison), share of all applications in this MSA."""
    n = ui.msa_mix(msa, by)
    cats = [c for c in MIX_ORDER[by] if c in n.columns] + [c for c in n.columns if c not in MIX_ORDER[by]]
    n = n.reindex(columns=cats, fill_value=0)
    shares = n.div(n.sum(axis=1).where(lambda t: t > 0), axis=0)
    peer_shares = shares.reindex(peers["lei"]).dropna(how="all")
    lender_share = shares.loc[lei] if lei in shares.index else pd.Series(0.0, index=cats)
    if compare == MEDIAN:
        other = peer_shares.median()  # median of each category on its own, so these needn't add to 100%
    elif compare == POOLED:
        pooled = n.reindex(peers["lei"]).sum()
        other = pooled / pooled.sum()
    else:
        other = peer_shares.loc[compare] if compare in peer_shares.index else pd.Series(0.0, index=cats)
    return pd.concat([
        pd.DataFrame({"category": cats, "who": info.respondent_name, "share": lender_share.values}),
        pd.DataFrame({"category": cats, "who": compare_label, "share": other.reindex(cats).values}),
    ])


left, right = st.columns(2)
for col, by, title in [(left, "product", "Product"), (right, "loan_type", "Loan type")]:
    with col:
        data = mix_shares(by)
        st.caption(title)
        chart = alt.Chart(data).mark_bar().encode(
            x=alt.X("share:Q", title="Share of applications", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("category:N", title=None, sort=list(dict.fromkeys(data["category"])),
                    axis=alt.Axis(labelLimit=220)),
            yOffset=alt.YOffset("who:N", sort=[info.respondent_name, compare_label]),
            color=alt.Color("who:N", title=None, legend=alt.Legend(orient="bottom", labelLimit=400),
                            scale=alt.Scale(domain=[info.respondent_name, compare_label],
                                            range=["#2563eb", "#94a3b8"])),
            tooltip=[alt.Tooltip("who:N", title="Lender"), alt.Tooltip("category:N", title=title),
                     alt.Tooltip("share:Q", title="Share", format=".1%")],
        ).properties(height=70 + 50 * data["category"].nunique())
        st.altair_chart(chart, use_container_width=True)
st.caption(
    "Shares of all the lender's applications in this MSA, whatever the product and loan-type settings in the "
    "sidebar (the peers themselves are chosen using those settings). The median peer's shares are each a "
    "separate median across the peers, so they need not add up to 100%."
)

# --- Underwriting: denial rates and AIR by applicant group ----------------------------------
st.markdown("**Denial rates and adverse impact ratios by applicant group**")
lender_m = dp.long_metrics(own, s.alpha, ["msa_md"]).set_index("group")
peer_m = dp.long_metrics(peers, s.alpha, ["lei", "respondent_name"])
median_m = dp.median_peer(peer_m)

group_table = pd.DataFrame({
    "group_label": lender_m["group_label"],
    "control_label": lender_m["control_label"],
    "dec": lender_m["dec"],
    "rate": lender_m["rate"],
    "ctl_rate": lender_m["ctl_rate"],
    "air": lender_m["air"],
    "p_value": lender_m["p_value"],
    "finding": lender_m["finding"],
    "med_rate": median_m["rate"],
    "med_ctl_rate": median_m["ctl_rate"],
    "med_air": median_m["air"],
    "peers_used": median_m["peers_used"],
}).reset_index(drop=True)


def air_color(v):
    return "color: rgb(220, 38, 38); font-weight: 600" if ui.is_low_air(v) else ""


st.dataframe(
    ui.styled(group_table, ints=["dec", "peers_used"], pct=["rate", "ctl_rate", "med_rate", "med_ctl_rate"],
              ratio=["air", "med_air"], pval=["p_value"])
    .map(ui.finding_color, subset=["finding"])
    .map(air_color, subset=["air", "med_air"]),
    hide_index=True, width="stretch",
    column_config={
        "group_label": "Group",
        "control_label": "Compared with",
        "dec": st.column_config.Column("Lender decisions",
                                             help="Decided applications from this group"),
        "rate": st.column_config.Column("Lender denial rate"),
        "ctl_rate": st.column_config.Column("Lender comparison denial rate"),
        "air": st.column_config.Column("Lender AIR"),
        "p_value": st.column_config.Column("p-value"),
        "finding": st.column_config.TextColumn("Finding", width="medium"),
        "med_rate": st.column_config.Column("Median peer denial rate"),
        "med_ctl_rate": st.column_config.Column("Median peer comparison denial rate"),
        "med_air": st.column_config.Column("Median peer AIR"),
        "peers_used": st.column_config.Column(
            "Peers in median", help=f"Peers with at least {dp.MIN_DECISIONS} decisions for this group"),
    },
)

# --- Peer-by-peer detail for one group ------------------------------------------------------
group = st.segmented_control(
    "Peer detail for group", list(dp.GROUPS), format_func=lambda g: dp.GROUPS[g][0],
    default="black", key="la_group",
) or "black"
lg = lender_m.loc[group]
mg = median_m.loc[group]
pg = peer_m[peer_m["group"] == group].sort_values("dec", ascending=False)
detail = pd.concat([
    pd.DataFrame([
        {"role": LENDER, "name": info.respondent_name, "dec": lg.dec, "den": lg.den, "rate": lg.rate,
         "ctl_dec": lg.ctl_dec, "ctl_rate": lg.ctl_rate, "air": lg.air, "p_value": lg.p_value,
         "finding": lg.finding},
        {"role": MEDIAN, "name": f"hypothetical: median of {int(mg.peers_used)} peers", "dec": mg.dec, "den": np.nan,
         "rate": mg.rate, "ctl_dec": np.nan, "ctl_rate": mg.ctl_rate, "air": mg.air, "p_value": np.nan,
         "finding": ""},
    ]),
    pg.rename(columns={"respondent_name": "name"}).assign(role="Peer")[
        ["role", "name", "dec", "den", "rate", "ctl_dec", "ctl_rate", "air", "p_value", "finding"]],
], ignore_index=True)
ctl_label = dp.CONTROLS[dp.GROUPS[group][2]][0]
st.dataframe(
    ui.styled(detail, ints=["dec", "den", "ctl_dec"], pct=["rate", "ctl_rate"], ratio=["air"],
              pval=["p_value"]).apply(role_rows, axis=1)
    .map(ui.finding_color, subset=["finding"])
    .map(air_color, subset=["air"]),
    hide_index=True, width="stretch",
    column_config={
        "role": "", "name": "Lender",
        "dec": st.column_config.Column(f"{dp.GROUPS[group][0]} decisions"),
        "den": st.column_config.Column("Denied"),
        "rate": st.column_config.Column("Denial rate"),
        "ctl_dec": st.column_config.Column(f"{ctl_label} decisions"),
        "ctl_rate": st.column_config.Column(f"{ctl_label} denial rate"),
        "air": st.column_config.Column("AIR"),
        "p_value": st.column_config.Column("p-value"),
        "finding": st.column_config.TextColumn("Finding", width="medium"),
    },
)

with st.expander("Method"):
    st.markdown(f"""
**MSA risk areas.** The redlining peer test from the *MSA analysis* tab: an MSA is a risk area when
the lender's share of applications from {s.tt_lower} tracts is significantly below that of its pooled peers
(p < {s.alpha}). See that page's Method section for the full rules.

**Peers.** Other lenders in the same MSA and product with {rl.PEER_LOW:.0%}–{rl.PEER_HIGH:.0%} of the lender's
application count there. The same peers are used for every comparison on this page.

**Median peer.** A hypothetical lender whose every figure is the median of that figure across the peers. Each
figure is a separate median, so the median peer's AIR is the median of the peers' AIRs, not a ratio of its
denial rates. For a group, only peers with at least {dp.MIN_DECISIONS} decisions from that group are included.

**Applicant groups** (HMDA derived fields). Each is compared with the group in brackets:

- **American Indian** (American Indian or Alaska Native), **Black**, **Pacific Islander** (Native Hawaiian or
  Other Pacific Islander): HMDA derived race, any ethnicity [non-Hispanic White].
- **Hispanic**: derived ethnicity Hispanic or Latino, any race [non-Hispanic White].
- **Female**: a female applicant with no co-applicant, or two female applicants [the same for male].
- **Senior**: applicant aged 62 or over, and the co-applicant too if there is one [both under 62].
  Pairs with one applicant over and one under 62 are left out.

Applications with joint or unreported race, ethnicity or sex fall in neither group of that comparison.

**Decisions.** Originated, approved but not accepted, and denied applications (action taken 1–3) for the selected
product in the MSA. Withdrawn, incomplete, preapproval and purchased records are excluded. Unlike the redlining
test, records without census tract data are included.

**Denial rate** = denied ÷ decisions. **Adverse impact ratio (AIR)** = the group's approval rate ÷ the comparison
group's approval rate. Values below {dp.AIR_THRESHOLD:.2f} (the four-fifths rule) are shown in red.

**Significance.** 2×2 table (group vs. comparison group × denied vs. approved): Pearson chi-square without
continuity correction, or Fisher's exact test when any expected count is below 5. A disparity is flagged when
p < {s.alpha} and the group's denial rate is the higher one. Rates are not reported when the group has fewer than
{dp.MIN_DECISIONS} decisions. These are raw comparisons: they don't control for credit score, income, loan-to-value
or other underwriting factors.
""")
