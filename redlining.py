"""Redlining peer analysis: application share in majority-minority census tracts.

For each lender with at least MIN_APPS applications in an MSA (for the chosen product), compare the
share of its applications from the chosen type of tract (majority-minority, majority-Black,
majority-Hispanic, or majority Black + Hispanic) with the pooled share of its peers: other lenders in
the same MSA and product with 50%-200% of its application volume.

2x2 table:                 tract of chosen type   other tracts
            target lender          a                  b
            peers (pooled)         c                  d

Pearson chi-square without continuity correction; Fisher's exact test when any expected
count is below 5.
"""

import pandas as pd
from scipy.stats import chi2_contingency, fisher_exact

MIN_APPS = 100
PEER_LOW, PEER_HIGH = 0.5, 2.0
THRESHOLD = 50  # a tract is "majority X" when X is THRESHOLD % or more of its residents

# code -> (label, description of the population share used, column in hmda_with_census)
TRACT_TYPES = {
    "MMCT": ("Majority-minority", "minority residents (everyone except non-Hispanic White)", "tract_minority_pct"),
    "MBCT": ("Majority-Black", "non-Hispanic Black residents", "tract_black_pct"),
    "MHCT": ("Majority-Hispanic", "Hispanic or Latino residents (any race)", "tract_hispanic_pct"),
    "MBHCT": ("Majority Black + Hispanic", "non-Hispanic Black plus Hispanic residents combined", "tract_black_hispanic_pct"),
}

PRODUCTS = {
    "Mortgage (closed-end)": ["Closed-end (mortgage)"],
    "HELOC (open-end)": ["Open-end (HELOC)"],
    "Mortgage + HELOC combined": ["Closed-end (mortgage)", "Open-end (HELOC)"],
}

BELOW = "Below peers - significant"

# Lender x MSA x product application counts, with the number in each tract type.
# Excluded: purchased loans (action 6), records without census tract data, non-MSA records,
# and records whose open-end flag is "Exempt".
COUNTS_SQL = f"""
    SELECT h.lei,
           coalesce(h.respondent_name, h.lei)                    AS respondent_name,
           coalesce(h.is_depository, false)                      AS is_depository,
           h.msa_md,
           coalesce(n.msa_md_name, h.msa_md)                     AS msa_name,
           h.product,
           count(*)                                              AS apps,
           {", ".join(
               f"count(*) FILTER (WHERE h.{col} >= :threshold) AS {code.lower()}_apps"
               for code, (_, _, col) in TRACT_TYPES.items()
           )}
    FROM hmda_with_census h
    LEFT JOIN msa_md_names n USING (msa_md)
    WHERE h.action_taken <> '6'
      AND h.tract_minority_pct IS NOT NULL
      AND h.tract_black_hispanic_pct IS NOT NULL
      AND h.msa_md <> '99999'
      AND h.product IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6
"""


def combine_products(counts: pd.DataFrame, product: str, tract_type: str) -> pd.DataFrame:
    """Keep the chosen product(s), add them up per lender and MSA, and pick the tract type.

    Returns columns apps (all applications) and tt_apps (applications in the chosen tract type).
    """
    keep = counts[counts["product"].isin(PRODUCTS[product])]
    keys = ["lei", "respondent_name", "is_depository", "msa_md", "msa_name"]
    col = f"{tract_type.lower()}_apps"
    return (
        keep.groupby(keys, as_index=False)[["apps", col]].sum()
        .rename(columns={col: "tt_apps"})
    )


def chi_square_2x2(a: int, b: int, c: int, d: int) -> dict:
    """Test target (a, b) vs peers (c, d). Returns statistic, p-value and method."""
    table = [[a, b], [c, d]]
    if (a + c) == 0 or (b + d) == 0:
        return {"statistic": None, "p_value": None, "method": "No variation (all tracts one type)"}
    stat, p, _, expected = chi2_contingency(table, correction=False)
    if expected.min() < 5:
        _, p = fisher_exact(table)
        return {"statistic": None, "p_value": p, "method": "Fisher's exact"}
    return {"statistic": stat, "p_value": p, "method": "Chi-square"}


def find_peers(grp: pd.DataFrame, lei: str, apps: int, depository_peers_only: bool) -> pd.DataFrame:
    """Peers of one lender within one MSA's counts."""
    peers = grp[(grp["lei"] != lei) & grp["apps"].between(PEER_LOW * apps, PEER_HIGH * apps)]
    if depository_peers_only:
        peers = peers[peers["is_depository"]]
    return peers


def peer_tests(
    counts: pd.DataFrame,
    depository_targets_only: bool = False,
    depository_peers_only: bool = False,
) -> pd.DataFrame:
    """Run the peer test for every lender-MSA with >= MIN_APPS applications.

    counts: output of combine_products (one row per lender and MSA).
    """
    results = []
    for msa, grp in counts.groupby("msa_md"):
        if grp["tt_apps"].sum() == 0:
            continue  # no lender has applications from this tract type here (e.g. an MSA with no MBCTs)
        targets = grp[grp["apps"] >= MIN_APPS]
        if depository_targets_only:
            targets = targets[targets["is_depository"]]
        for t in targets.itertuples():
            peers = find_peers(grp, t.lei, t.apps, depository_peers_only)
            p_apps, p_tt = int(peers["apps"].sum()), int(peers["tt_apps"].sum())
            row = {
                "lei": t.lei,
                "respondent_name": t.respondent_name,
                "is_depository": t.is_depository,
                "msa_md": msa,
                "msa": f"{msa} {t.msa_name}",
                "msa_name": t.msa_name,
                "target_apps": t.apps,
                "target_tt_apps": t.tt_apps,
                "target_share": t.tt_apps / t.apps,
                "peer_count": len(peers),
                "peer_apps": p_apps,
                "peer_tt_apps": p_tt,
                "peer_share": p_tt / p_apps if p_apps else None,
            }
            if p_apps == 0:
                row.update(statistic=None, p_value=None, method="No peers in range")
            else:
                row.update(chi_square_2x2(t.tt_apps, t.apps - t.tt_apps, p_tt, p_apps - p_tt))
            results.append(row)

    out = pd.DataFrame(results)
    if out.empty:
        return out
    out["difference"] = out["target_share"] - out["peer_share"]
    return out.sort_values(["p_value", "msa_md"], na_position="last").reset_index(drop=True)


def flag(df: pd.DataFrame, alpha: float) -> pd.Series:
    """Result label for each row. Redlining risk = significantly *lower* share than peers."""
    sig = df["p_value"].notna() & (df["p_value"] < alpha)
    return pd.Series(
        [
            "Not tested" if pd.isna(p) else
            (BELOW if s and d < 0 else "Above peers - significant" if s else "Not significant")
            for p, s, d in zip(df["p_value"], sig, df["difference"])
        ],
        index=df.index,
    )


def format_p(p: float) -> str:
    return "<0.0001" if p < 0.0001 else f"{p:.4f}"


def lender_summary(results: pd.DataFrame) -> pd.DataFrame:
    """One row per lender: how many MSAs tested, how many are risk areas, and their p-values."""
    risk = results[results["result"] == BELOW].sort_values("p_value")
    risk_list = {
        lei: "; ".join(f"{m} (p {format_p(p)})" for m, p in zip(g["msa_name"], g["p_value"]))
        for lei, g in risk.groupby("lei")
    }
    summary = results.groupby(["lei", "respondent_name", "is_depository"], as_index=False).agg(
        msas_tested=("msa_md", "size"),
        apps_tested=("target_apps", "sum"),
    )
    summary = summary.merge(
        risk.groupby("lei").agg(
            risk_areas=("msa_md", "size"),
            min_p_value=("p_value", "min"),
            largest_shortfall=("difference", "min"),
        ),
        on="lei",
        how="left",
    )
    summary["risk_areas"] = summary["risk_areas"].fillna(0).astype(int)
    summary["risk_msas"] = summary["lei"].map(risk_list).fillna("")
    return summary.sort_values(["risk_areas", "min_p_value"], ascending=[False, True]).reset_index(drop=True)
