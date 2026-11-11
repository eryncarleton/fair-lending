"""Pieces shared by the analysis pages: sidebar settings, cached data and test results."""

from dataclasses import dataclass

import pandas as pd
import streamlit as st

import counts
import disparity as dp
import redlining as rl
from db import get_engine, query

SETTING_KEYS = ["tract_type", "product", "loan_types", "depository_only", "peers_all", "alpha"]
DEFAULT_PRODUCT = "Mortgage + HELOC combined"


@dataclass
class Settings:
    tract_type: str
    product: str
    loan_types: tuple[str, ...]
    depository_only: bool
    peers_all: bool
    alpha: float

    @property
    def depository_peers(self) -> bool:
        return self.depository_only and not self.peers_all

    @property
    def tt_label(self) -> str:
        return rl.TRACT_TYPES[self.tract_type][0]

    @property
    def tt_lower(self) -> str:  # "majority-Black": keeps group names capitalized
        return self.tt_label[0].lower() + self.tt_label[1:]

    @property
    def scope(self) -> str:
        s = f"{self.tract_type} ({self.tt_lower} tracts) · {self.product}"
        if set(self.loan_types) != set(rl.LOAN_TYPES):
            s += " · " + ", ".join(self.loan_types)
        if self.depository_only:
            s += " · depository institutions only" + (
                " (peers: all lenders)" if self.peers_all else " (peers: depository only)")
        return s


def sidebar() -> Settings:
    # Streamlit forgets a widget's value when you switch to a page that doesn't draw it.
    # Re-saving the values here keeps the settings when moving between pages.
    for k in SETTING_KEYS:
        if k in st.session_state:
            st.session_state[k] = st.session_state[k]
    st.session_state.setdefault("product", DEFAULT_PRODUCT)
    st.session_state.setdefault("loan_types", list(rl.LOAN_TYPES))

    with st.sidebar:
        st.header("Settings")
        tract_type = st.radio(
            "Tract type",
            list(rl.TRACT_TYPES),
            format_func=lambda code: f"{code} · {rl.TRACT_TYPES[code][0]}",
            help=f"A tract counts when {rl.THRESHOLD}% or more of its residents are in the group.",
            key="tract_type",
        )
        product = st.radio("Product", list(rl.PRODUCTS), key="product")
        loan_types = st.multiselect(
            "Loan type", rl.LOAN_TYPES, key="loan_types", placeholder="Pick at least one…",
            help="Applications of these loan types are counted everywhere: peer groups, the redlining "
            "test and denial rates. (The product and loan-type mix chart always shows them all.)",
        )
        depository_only = st.checkbox(
            "Depository institutions only",
            help="Banks, savings institutions and credit unions. Non-bank mortgage companies and "
            "bank-owned mortgage subsidiaries are excluded.",
            key="depository_only",
        )
        peers_all = False
        if depository_only:
            peers_all = st.checkbox(
                "Still compare against all lenders as peers",
                help="When ticked, only depository institutions are tested, but their peer groups "
                "include non-depository lenders too.",
                key="peers_all",
            )
        alpha = st.selectbox("Significance level", [0.05, 0.01, 0.10],
                             format_func=lambda a: f"{a:.2f}", key="alpha")
    if not loan_types:
        st.warning("Pick at least one loan type in the sidebar.")
        st.stop()
    return Settings(tract_type, product, tuple(loan_types), depository_only, peers_all, alpha)


@st.cache_data(ttl=600, show_spinner="Loading lender counts…")
def load_counts() -> pd.DataFrame:
    """The precomputed lender x MSA x product table, (re)built first if missing or out of date."""
    with get_engine().begin() as conn:
        if not counts.source_ready(conn.exec_driver_sql):
            st.warning("Census data isn't loaded yet. Run `python scripts/load_census.py data/CensusFlatFile2025.csv`.")
            st.stop()
        if not counts.is_current(conn.exec_driver_sql):
            with st.spinner("Building the lender summary table (one time, about 15 seconds)…"):
                counts.build(conn.exec_driver_sql)
    return query(f"SELECT * FROM {counts.TABLE}")


@st.cache_data(ttl=600)
def combined(product: str, tract_type: str, loan_types: tuple[str, ...]) -> pd.DataFrame:
    return rl.combine_products(load_counts(), product, tract_type, loan_types)


def combined_for(s: Settings) -> pd.DataFrame:
    return combined(s.product, s.tract_type, s.loan_types)


@st.cache_data(ttl=600, show_spinner="Running peer tests…")
def _redlining_results(product: str, tract_type: str, loan_types: tuple[str, ...], dep_targets: bool, dep_peers: bool,
                       alpha: float) -> pd.DataFrame:
    results = rl.peer_tests(combined(product, tract_type, loan_types), dep_targets, dep_peers)
    if not results.empty:
        results["result"] = rl.flag(results, alpha)
    return results


def redlining_results(s: Settings) -> pd.DataFrame:
    return _redlining_results(s.product, s.tract_type, s.loan_types, s.depository_only, s.depository_peers, s.alpha)


def peers_of(s: Settings, lei: str, msa: str, target_apps: int) -> pd.DataFrame:
    c = combined_for(s)
    peers = rl.find_peers(c[c["msa_md"] == msa], lei, target_apps, s.depository_peers)
    return peers.assign(tt_share=peers["tt_apps"] / peers["apps"]).sort_values("apps", ascending=False)


def lender_rows(s: Settings, lei: str) -> pd.DataFrame:
    c = combined_for(s)
    return c[c["lei"] == lei]


def msa_mix(msa: str, by: str) -> pd.DataFrame:
    """Every lender's application counts by product or loan type in one MSA (all applications)."""
    c = load_counts()
    return rl.mix(c[c["msa_md"] == msa], by)


@st.cache_data(ttl=600)
def msa_states() -> pd.DataFrame:
    load_counts()  # makes sure the table exists and is current
    return query(f"SELECT msa_md, state_code FROM {counts.STATES_TABLE}")


@st.cache_data(ttl=600, show_spinner="Testing denial disparities…")
def _denial_flags(product: str, tract_type: str, loan_types: tuple[str, ...], dep_targets: bool, dep_peers: bool,
                  alpha: float) -> pd.Series:
    """For every tested lender-MSA: the groups with a significant denial disparity, as text."""
    results = _redlining_results(product, tract_type, loan_types, dep_targets, dep_peers, alpha)
    if results.empty:
        return pd.Series(dtype=str)
    c = combined(product, tract_type, loan_types)
    tested = c.merge(results[["lei", "msa_md"]], on=["lei", "msa_md"])
    m = dp.long_metrics(tested, alpha, ["lei", "msa_md"])
    hits = m[m["finding"].isin(dp.ADVERSE)]
    return hits.groupby(["lei", "msa_md"]).apply(
        lambda g: "; ".join(f"{x} (AIR {a:.2f})" for x, a in zip(g["group_label"], g["air"])),
        include_groups=False,
    )


def denial_flags(s: Settings) -> pd.Series:
    """Index (lei, msa_md) -> e.g. "Black (AIR 0.75); Hispanic (AIR 0.70)". Missing = none flagged."""
    return _denial_flags(s.product, s.tract_type, s.loan_types, s.depository_only, s.depository_peers, s.alpha)


def open_in_lender_analysis(s: Settings, lei: str, msa: str) -> None:
    """Jump to the Lender analysis tab with this lender and MSA already chosen."""
    st.session_state["la_lender"] = lei
    st.session_state[f"la_msa_{lei}"] = msa
    st.switch_page("app_pages/0_Lender_analysis.py")


# --- Shared formatting ------------------------------------------------------------------------

RED = "background-color: rgba(220, 38, 38, 0.18)"
AMBER = "background-color: rgba(217, 119, 6, 0.16)"


MISSING = "—"


def format_p(p) -> str:
    if isinstance(p, str):
        return p
    return "<0.0001" if p < 0.0001 else f"{p:.4f}"


def _fmt(spec: str):
    return lambda v: v if isinstance(v, str) else spec.format(v)


def styled(df: pd.DataFrame, pct=(), ratio=(), pval=(), ints=()):
    """Format numbers for display. Missing values show as a dash: st.dataframe always draws a
    null as "None", so a column with any missing values is turned into its display text
    (sorting that column is then alphabetical; columns without gaps stay numeric)."""
    fmt = ({c: _fmt("{:.1%}") for c in pct} | {c: _fmt("{:.2f}") for c in ratio}
           | {c: format_p for c in pval} | {c: _fmt("{:,.0f}") for c in ints})
    d = df.copy()
    for c, f in fmt.items():
        if d[c].isna().any():
            d[c] = [MISSING if pd.isna(v) else f(v) for v in d[c]]
    return d.style.format({c: f for c, f in fmt.items() if d[c].dtype != object})


def is_low_air(v) -> bool:
    return isinstance(v, (int, float)) and not pd.isna(v) and v < dp.AIR_THRESHOLD


def finding_color(value: str) -> str:
    if value in dp.ADVERSE:
        return RED
    if value == dp.AIR_ONLY:
        return AMBER
    return ""


def clicked_rows(selection) -> list[int]:
    """Rows picked in a table, by its row checkbox or by clicking any cell (cells = [(row, column)])."""
    return list(selection.get("rows", [])) + [cell[0] for cell in selection.get("cells", [])]
