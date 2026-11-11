"""Add regulator and depository status to the lender (respondent) file.

Usage:
    python scripts/build_respondents.py

Reads   data/hmda_2025_CA_respondents.csv     (LEI, name, record count)
        data/cfpb/2025_public_ts_csv.csv       (CFPB Transmittal Sheet: name, agency code for every 2025 filer)
        data/cfpb/2023_public_panel_csv.csv    (CFPB Reporter Panel; 2023 is the last year it was produced)
Writes  data/hmda_2025_CA_respondents.csv     (same file, with added columns)

Depository rule:
  1. Lender in the 2023 Reporter Panel: depository if other_lender_code = 0 (the bank / credit union itself).
     Codes 1, 2, 3, 5 are mortgage subsidiaries, independent mortgage companies and affiliates.
     Code -1 (not given) falls through to rule 2.
  2. Otherwise use the 2025 Transmittal Sheet agency code: OCC (1), Federal Reserve (2), FDIC (3) and
     NCUA (5) supervise only banks, thrifts and credit unions -> depository. HUD (7) and CFPB (9) -> not.
"""

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
RESPONDENTS = DATA / "hmda_2025_CA_respondents.csv"
TS = DATA / "cfpb" / "2025_public_ts_csv.csv"
PANEL = DATA / "cfpb" / "2023_public_panel_csv.csv"

AGENCIES = {"1": "OCC", "2": "Federal Reserve", "3": "FDIC", "5": "NCUA", "7": "HUD", "9": "CFPB"}
OTHER_LENDER = {
    "0": "Depository institution",
    "1": "Mortgage banking subsidiary of a state member bank",
    "2": "Mortgage banking subsidiary of a bank holding company",
    "3": "Independent mortgage banking company",
    "5": "Affiliate of a depository institution",
}


def read(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return {r["lei"]: r for r in csv.DictReader(f)}


def main() -> None:
    ts, panel = read(TS), read(PANEL)
    with RESPONDENTS.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        t, p = ts.get(r["lei"]), panel.get(r["lei"])
        if not r["respondent_name"] and t:
            r["respondent_name"], r["name_source"] = t["respondent_name"].strip(), "CFPB Transmittal Sheet"
        agency = t["agency_code"] if t else ""
        r["agency_code"] = agency
        r["agency"] = AGENCIES.get(agency, "")
        r["other_lender_code_2023"] = p["other_lender_code"] if p else ""
        if p and p["other_lender_code"] in OTHER_LENDER:
            r["depository"] = "Y" if p["other_lender_code"] == "0" else "N"
            r["depository_basis"] = f"2023 Reporter Panel: {OTHER_LENDER[p['other_lender_code']]}"
        elif agency:
            r["depository"] = "Y" if agency in {"1", "2", "3", "5"} else "N"
            r["depository_basis"] = f"2025 regulator: {AGENCIES.get(agency, agency)}"
        else:
            r["depository"], r["depository_basis"] = "", "Unknown"

    fields = list(rows[0].keys())
    with RESPONDENTS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    dep = sum(r["depository"] == "Y" for r in rows)
    print(f"{len(rows):,} lenders: {dep:,} depository, {len(rows) - dep:,} not. Wrote {RESPONDENTS.name}")


if __name__ == "__main__":
    main()
