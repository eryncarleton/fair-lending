"""Underwriting disparity by applicant group: denial rates, adverse impact ratio (AIR), significance.

For each lender (or peer) in an MSA and product, each protected group is compared with its
comparison group, using decided applications (originated, approved not accepted, denied):

    denial rate = denied / decided
    AIR         = protected approval rate / comparison approval rate
                = (1 - protected denial rate) / (1 - comparison denial rate)

An AIR below 0.80 (the "four-fifths rule") is the usual screening threshold. Significance uses the
same 2x2 test as the redlining analysis (protected vs. comparison x denied vs. approved): Pearson
chi-square, or Fisher's exact test when an expected count is below 5.

The "median peer" is a hypothetical lender whose every figure is the median of that figure across
the peer group. Each figure is a separate median, so its AIR is not computed from its denial rates.
"""

import numpy as np
import pandas as pd

from redlining import chi_square_2x2

AIR_THRESHOLD = 0.80
MIN_DECISIONS = 5  # below this many protected-group decisions, a lender's rate isn't reported

# code -> (label, SQL condition on hmda_with_census h)
CONTROLS = {
    "nhw": ("Non-Hispanic White",
            "h.derived_race = 'White' AND h.derived_ethnicity = 'Not Hispanic or Latino'"),
    "male": ("Male", "h.derived_sex = 'Male'"),
    "nonsenior": ("Under 62", "h.age_group = 'Under 62'"),
}

# code -> (label, SQL condition, comparison group code)
GROUPS = {
    "aian": ("American Indian", "h.derived_race = 'American Indian or Alaska Native'", "nhw"),
    "black": ("Black", "h.derived_race = 'Black or African American'", "nhw"),
    "nhpi": ("Pacific Islander", "h.derived_race = 'Native Hawaiian or Other Pacific Islander'", "nhw"),
    "hisp": ("Hispanic", "h.derived_ethnicity = 'Hispanic or Latino'", "nhw"),
    "female": ("Female", "h.derived_sex = 'Female'", "male"),
    "senior": ("Senior", "h.age_group = 'Senior'", "nonsenior"),
}

SIG_AIR = "Significant, AIR < 0.80"
SIG = "Significant disparity"
AIR_ONLY = "AIR < 0.80, not significant"
NONE = "No disparity flagged"
TOO_FEW = f"Too few decisions (<{MIN_DECISIONS})"
ADVERSE = (SIG_AIR, SIG)


def all_conditions() -> dict[str, str]:
    """Every group and comparison group: code -> SQL condition (used to build the counts table)."""
    return {code: cond for code, (_, cond, _) in GROUPS.items()} | {
        code: cond for code, (_, cond) in CONTROLS.items()
    }


def count_columns() -> list[str]:
    return [f"{code}_{kind}" for code in all_conditions() for kind in ("dec", "den")]


def long_metrics(df: pd.DataFrame, alpha: float, id_cols: list[str]) -> pd.DataFrame:
    """One row per entity (row of df) and group, with rates, AIR, p-value and finding."""
    frames = []
    for g, (label, _, ctl) in GROUPS.items():
        out = df[id_cols].copy()
        out["group"] = g
        out["group_label"] = label
        out["control_label"] = CONTROLS[ctl][0]
        out["dec"], out["den"] = df[f"{g}_dec"].astype(int), df[f"{g}_den"].astype(int)
        out["ctl_dec"], out["ctl_den"] = df[f"{ctl}_dec"].astype(int), df[f"{ctl}_den"].astype(int)
        frames.append(out)
    m = pd.concat(frames, ignore_index=True)

    enough = (m["dec"] >= MIN_DECISIONS) & (m["ctl_dec"] > 0)
    m["rate"] = np.where(enough, m["den"] / m["dec"].where(m["dec"] > 0), np.nan)
    m["ctl_rate"] = np.where(m["ctl_dec"] > 0, m["ctl_den"] / m["ctl_dec"].where(m["ctl_dec"] > 0), np.nan)
    ctl_approval = (1 - m["ctl_rate"]).where(lambda s: s > 0)
    m["air"] = (1 - m["rate"]) / ctl_approval

    tests = [
        chi_square_2x2(r.den, r.dec - r.den, r.ctl_den, r.ctl_dec - r.ctl_den) if ok
        else {"statistic": None, "p_value": None, "method": "Not tested"}
        for r, ok in zip(m.itertuples(), enough)
    ]
    m["p_value"] = pd.to_numeric([t["p_value"] for t in tests], errors="coerce")
    m["method"] = [t["method"] for t in tests]
    m["finding"] = finding(m, alpha)
    return m


def finding(m: pd.DataFrame, alpha: float) -> pd.Series:
    sig = (m["p_value"] < alpha) & (m["rate"] > m["ctl_rate"])
    low = m["air"] < AIR_THRESHOLD
    return pd.Series(
        np.select(
            [m["rate"].isna(), sig & low, sig, low],
            [TOO_FEW, SIG_AIR, SIG, AIR_ONLY],
            default=NONE,
        ),
        index=m.index,
    )


def median_peer(peer_metrics: pd.DataFrame) -> pd.DataFrame:
    """Hypothetical median peer: median of each figure across peers, one row per group.

    Peers with too few decisions for a group are left out of that group's medians.
    """
    usable = peer_metrics[peer_metrics["rate"].notna()]
    med = usable.groupby("group")[["dec", "rate", "ctl_rate", "air"]].median()
    med["peers_used"] = usable.groupby("group").size()
    return med.reindex(list(GROUPS)).assign(peers_used=lambda d: d["peers_used"].fillna(0).astype(int))
