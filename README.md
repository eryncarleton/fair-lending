# fair-lending

Analyze HMDA (Home Mortgage Disclosure Act) loan data with PostgreSQL and a Streamlit app, all running in Docker.

```
┌──────────── Docker ─────────────┐
│  app (Python 3.12)   db (Postgres 16)
│  ├─ app.py  ──SQL──▶ hmda_lar  (raw CSV columns, text)
│  └─ scripts/          hmda_clean (view: numbers + labels)
└─────────────────────────────────┘
   localhost:8501 → Streamlit     localhost:5432 → Postgres
```

## One-time setup

1. Install **Docker Desktop** and in its settings turn on *Use the WSL 2 based engine*
   (Settings → Resources → WSL integration → enable your distro).
2. Install **VS Code** and the **Dev Containers** extension.
3. Open this folder in VS Code (from WSL: `code .` in the project folder).
4. When prompted, click **Reopen in Container** (or Command Palette → *Dev Containers: Reopen in Container*).
   The first build takes a few minutes. It starts Postgres and installs the Python packages.

## Load HMDA data

1. Go to the HMDA Data Browser: <https://ffiec.cfpb.gov/data-browser/>.
   Pick a year and a state (or a few lenders) and download the CSV.
   Start with one state; the nationwide file is tens of millions of rows.
2. Save the CSV into the `data/` folder of this project.
3. In the VS Code terminal (which runs inside the container):

   ```bash
   python scripts/load_hmda.py data/your_file.csv
   ```

   You can pass several files at once and run the script again later to add more.
   Loading a file twice will duplicate its rows.

### Data already downloaded (2025, California)

| File | Source | Rows |
|---|---|---|
| `data/hmda_2025_CA.csv` | CFPB HMDA Data Browser API (`/v2/data-browser-api/view/csv?states=CA&years=2025`) | 1,161,292 |
| `data/hmda_2025_CA_respondents.csv` | Lenders by LEI: name, regulator, depository flag. Names from the CFPB filers API, plus GLEIF LEI registry for 37 lenders the CFPB list didn't include (see `name_source`) | 1,160 |
| `data/cfpb/2025_public_ts_csv.csv` | CFPB 2025 Transmittal Sheet: every filer's name and regulator (agency code) | 4,782 |
| `data/cfpb/2023_public_panel_csv.csv` | CFPB 2023 Reporter Panel: "other lender code" (depository vs. non-bank). Discontinued after 2023 | 5,113 |
| `data/cfpb/2025_public_msamd_csv.csv` | CFPB MSA/MD names | 418 |

The CFPB files come from `https://files.ffiec.cfpb.gov/static-data/snapshot/<year>/`.

Rebuild the lender file's regulator and depository columns, then reload everything (replaces what's in the tables):

```bash
python scripts/build_respondents.py
python scripts/load_hmda.py --table hmda_lar --replace data/hmda_2025_CA.csv
python scripts/load_hmda.py --table hmda_respondents --replace data/hmda_2025_CA_respondents.csv
python scripts/load_hmda.py --table msa_md_names --replace data/cfpb/2025_public_msamd_csv.csv
```

`hmda_clean` has `respondent_name`, `lender_regulator` and `is_depository` for each record.

**Depository institutions** (673 of 1,160 lenders): banks, savings institutions and credit unions.
A lender in the 2023 Reporter Panel counts as depository when its other lender code is 0.
A lender missing from that panel (62, mostly newer filers) counts as depository when its 2025 regulator is the OCC,
Federal Reserve, FDIC or NCUA. Lenders regulated by HUD or the CFPB don't count.
The regulator code alone isn't enough: the CFPB regulates both large banks and many non-bank lenders,
and some bank-owned mortgage subsidiaries file under their parent bank's regulator.

### FFIEC census tract data

`data/CensusFlatFile2025.csv` was downloaded by hand from <https://www.ffiec.gov/data/census>
(that site blocks automated downloads). It covers every US tract, has 1,212 columns, and has no header row.

```bash
python scripts/load_census.py data/CensusFlatFile2025.csv   # California only; --state all for every state
```

| Name | What it is |
|---|---|
| `ffiec_census_raw` | 9,140 California tracts, columns `c0001`–`c1212` as text, numbered as in the FFIEC data dictionary |
| `ffiec_census_tract` | View with readable names: tract income level (Low/Moderate/Middle/Upper), FFIEC MSA median family income, population, minority %, housing counts |
| `hmda_with_census` | `hmda_clean` + tract columns, joined on `census_tract` |

Seven of the named columns were checked against the census fields in the HMDA file and match for every tract.
The rest come from the standard flat-file layout and are marked "confirm" in `db/census_views.sql`.
To name more columns, download the data dictionary from the same FFIEC page and add them to that file.

Every HMDA record with a census tract matches a census row. The 13,824 records without tract data have no tract in HMDA (`NA`).

## Run the app

```bash
streamlit run app.py
```

VS Code opens <http://localhost:8501>. Edit a page and save, and the page offers to rerun.

| Page | File | What it does |
|---|---|---|
| Redlining peer analysis | `pages/1_Redlining_analysis.py` (logic in `redlining.py`) | Opens on lenders ranked by number of risk areas, with p-values. Click a lender, or type a name in the search box, to open its profile with results for each MSA. Sidebar: tract type (MMCT / MBCT / MHCT / MBHCT), Mortgage / HELOC / both combined, depository institutions only, significance level |
| Data explorer | `pages/2_Data_explorer.py` | Denial rates by group, denial reasons, free-form SQL |

`app.py` only lists the pages for the sidebar. `db.py` has the shared database connection.

Redlining test rules (constants at the top of `redlining.py`):

- Lenders with 100+ applications in an MSA, for the chosen product, are tested in that MSA.
- Peers are other lenders in that MSA with 50–200% of the lender's application count; their applications are pooled.
- The tract type you pick counts tracts where 50% or more of residents are in the group:

  | Code | Tract type | Group | Source column | CA tracts |
  |---|---|---|---|---|
  | MMCT | Majority-minority | Everyone except non-Hispanic White | FFIEC minority % (`c0029`, same as HMDA) | 6,100 |
  | MBCT | Majority-Black | Non-Hispanic Black alone | `c0035` ÷ `c0030` | 45 |
  | MHCT | Majority-Hispanic | Hispanic or Latino, any race | `c0031` ÷ `c0030` | 2,862 |
  | MBHCT | Majority Black + Hispanic | The two groups above added together (they don't overlap) | (`c0031` + `c0035`) ÷ `c0030` | 3,505 |

  The race and ethnicity columns (`c0030`–`c0040`, Census "Hispanic or Latino, and not Hispanic by race")
  weren't in a data dictionary. I identified them because the sub-totals add up exactly for every California tract
  and because county totals match the 2020 Census. See `db/census_views.sql`.
- MSAs where no lender has applications from the chosen tract type are skipped. For MBCT, only Los Angeles and
  Oakland have majority-Black tracts, so MBCT is tested only there.
- Purchased loans, records without census tract data, non-MSA records and "Exempt" open-end flags are excluded.
- Pearson chi-square on the 2×2 table, without continuity correction; Fisher's exact test when any expected count is below 5.
- "Mortgage + HELOC combined" adds both products' applications together before testing.
- "Depository institutions only" tests only depository lenders, and by default their peers are only depository lenders too.
  Tick "Still compare against all lenders as peers" to keep non-depository lenders in the peer groups.

The **Method** section in the app explains this in plain language.

## Query with SQL directly

- **In VS Code:** the SQLTools extension (left sidebar, database icon) has a connection called *HMDA (Postgres)* set up already.
- **In the terminal:** `psql "$DATABASE_URL"`
- **From Windows tools** (DBeaver, pgAdmin, Excel): host `localhost`, port `5432`, database/user/password `hmda`.

Tables:

| Name | What it is |
|---|---|
| `hmda_lar` | Every CSV column exactly as published, stored as text |
| `hmda_clean` | View with numeric columns, readable labels for codes, and `is_decision` / `is_denied` flags |

Example: denial rates by race.

```sql
SELECT derived_race,
       count(*) FILTER (WHERE is_decision)            AS decisions,
       round(avg(is_denied::int) FILTER (WHERE is_decision) * 100, 1) AS denial_pct
FROM hmda_clean
GROUP BY 1
ORDER BY 3 DESC;
```

To add columns to the view, edit `db/views.sql`. The loader re-applies it each time it runs, or you can apply it yourself with `psql "$DATABASE_URL" -f db/views.sql`.

## Starting over

The database lives in a Docker volume, so it survives restarts and rebuilds. To wipe it, run this from WSL in the project folder:

```bash
docker compose -f .devcontainer/docker-compose.yaml down -v
```
