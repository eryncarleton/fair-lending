# fair-lending

Analyze HMDA (Home Mortgage Disclosure Act) loan data with PostgreSQL and a Streamlit app.
Run it fully in Docker, or run the Python side directly in WSL.

```
┌──────────── Docker ─────────────┐
│  app (Python 3.12)   db (Postgres 16)
│  ├─ app.py  ──SQL──▶ hmda_lar  (raw CSV columns, text)
│  └─ scripts/          hmda_clean (view: numbers + labels)
└─────────────────────────────────┘
   localhost:8501 → Streamlit     localhost:5432 → Postgres
```

## Environments

There are three ways to run the project. All of them share one Compose project (`docker-compose.yaml`,
named `fair-lending`) and one Postgres data volume, so data you load in one is there in the others.

| | Python runs in | Postgres | Start with |
|---|---|---|---|
| **Docker** | `app` container | `db` container | `docker compose up -d` |
| **WSL local** | WSL venv (`~/.venvs/fair-lending`) | `db` container, or Postgres installed in WSL | `docker compose up -d db`, then `streamlit run app.py` |
| **VS Code dev container** | `dev` container | `db` container | *Reopen in Container* |

Every mode serves the app on <http://localhost:8501> and Postgres on `localhost:5432`
(user, password and database all `hmda`). Run one app at a time: they all want port 8501.

First, for all modes: install **Docker Desktop** and turn on its WSL integration
(Settings → Resources → WSL integration → enable your distro). Run the commands below from a WSL terminal
in the project folder (`cd /mnt/c/Users/<you>/Projects/fair-lending`).

### Docker

```bash
docker compose up -d          # builds the app image the first time
docker compose logs -f app    # watch Streamlit start; Ctrl+C stops watching, not the app
```

Run the loader scripts inside the container, e.g. `docker compose exec app python scripts/load_hmda.py data/your_file.csv`.
If port 8501 is busy (VS Code forwards it while the dev container is open), pick another: `APP_PORT=8510 docker compose up -d`.
Code edits apply without a rebuild (the folder is mounted). After changing `requirements.txt`: `docker compose up -d --build`.
Stop with `docker compose stop`.

### WSL local

One-time setup (creates the venv and installs the Python packages; Ubuntu's Python is fine):

```bash
bash scripts/setup_wsl.sh
```

Then, in each new terminal:

```bash
docker compose up -d db
source ~/.venvs/fair-lending/bin/activate
streamlit run app.py
```

Scripts and the app connect to `localhost:5432` when `DATABASE_URL` isn't set, so the loader commands below work as written.

**No Docker at all:** `bash scripts/setup_wsl.sh --native-db` also installs Postgres inside WSL, on port **5433**
so it can't clash with the Docker database, and sets `DATABASE_URL` in the venv's activate script.
That database starts empty: load the data with the commands in the next section.

### VS Code dev container

1. Install **VS Code** and the **Dev Containers** extension.
2. Open this folder in VS Code (from WSL: `code .` in the project folder).
3. When prompted, click **Reopen in Container** (or Command Palette → *Dev Containers: Reopen in Container*).
   The first build takes a few minutes. It starts Postgres and installs the Python packages.

Postgres keeps running when you close VS Code (so WSL-local mode can still use it). Stop it with `docker compose stop`.

## Load HMDA data

1. Go to the HMDA Data Browser: <https://ffiec.cfpb.gov/data-browser/>.
   Pick a year and a state (or a few lenders) and download the CSV.
   Start with one state; the nationwide file is tens of millions of rows.
2. Save the CSV into the `data/` folder of this project.
3. In the VS Code terminal (dev container) or your activated WSL venv
   (Docker mode: prefix with `docker compose exec app`):

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

(In Docker mode it's already running.) Open <http://localhost:8501>. Edit a page and save, and the page offers to rerun.

| Page | File | What it does |
|---|---|---|
| Lender analysis (first tab) | `app_pages/0_Lender_analysis.py` (logic in `redlining.py`, `disparity.py`) | Pick a lender: its MSA risk areas (redlining test, with any significant denial disparities per MSA), then, for one MSA, its peers. A peer picker (any peer, the median peer or pooled peers) charts the lender's product and loan-type mix against it. Peer tables show the lender, a hypothetical **median peer** (median of each figure across the peers), pooled peers and every peer. Denial rates, adverse impact ratio (AIR) and significance for American Indian, Black, Pacific Islander, Hispanic, Female and Senior applicants vs. their comparison groups |
| MSA analysis | `app_pages/1_MSA_analysis.py` (logic in `redlining.py`, `disparity.py`) | Pick a state, then an MSA. With only a state picked: its MSAs with applications, the share from the chosen tract type, lenders tested, risk areas and lenders with a denial disparity (click an MSA to open it). With an MSA picked: every tested lender there with its redlining peer-test result (vs. pooled peers and the median peer) and its significant denial disparities. Click a lender to open it in Lender analysis with that MSA. Lenders under 100 applications are listed separately, untested |
| Data explorer | `app_pages/2_Data_explorer.py` | Denial rates by group, denial reasons (from the precomputed `explorer_*` tables), free-form SQL |

The two analysis pages share the sidebar settings (`ui.py`): tract type (MMCT / MBCT / MHCT / MBHCT), product (Mortgage / HELOC / both combined, the default), loan type (Conventional / FHA / VA / USDA, any combination; all by default), depository institutions only, significance level. The loan-type filter applies everywhere: peer groups, redlining tests and denial rates. They stay put when you switch pages.

`app.py` only lists the pages for the sidebar. `db.py` has the shared database connection.

**Speed.** The analysis pages read the precomputed table `lender_msa_counts` (one row per lender × MSA × product, built by `counts.py`)
instead of aggregating 1.2M records each time. The loaders rebuild it after every load, and the app rebuilds it (about 15 seconds)
if it is missing or if the definitions in `counts.py`, `redlining.py` or `disparity.py` change.
After editing `db/views.sql` or `db/census_views.sql` by hand, run `python scripts/build_counts.py`.

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
| `hmda_clean` | View with numeric columns, readable labels for codes, and `is_decision` / `is_denied` flags, and `age_group` (Senior / Under 62) |
| `lender_msa_counts` | Precomputed counts per lender, MSA and product: applications by tract type, and decisions / denials by applicant group |
| `msa_states`, `explorer_totals`, `explorer_groups`, `explorer_reasons` | Precomputed: which states each MSA is in, and the Data explorer's summaries for every year × state × loan purpose (plus "All") |

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

## Hosting on Google Cloud (Cloud Run)

The hosted copy runs on [Cloud Run](https://cloud.google.com/run) with **no database server**. Everything it shows
comes from one read-only data file, `build/hmda.duckdb`, built on your computer from your local PostgreSQL by
`scripts/export_duckdb.py` (about 80 seconds, 140 MB). Nothing is computed on the server.

| | Local | Hosted |
|---|---|---|
| Data | PostgreSQL (`DATABASE_URL`) | `build/hmda.duckdb`, baked into the image (`FAIR_LENDING_DUCKDB`) |
| Precomputed tables | Built on first use, or when their definition changes | Built locally; the app says so if the file is older than the code |
| Free-form SQL | PostgreSQL, as the database owner | [DuckDB](https://duckdb.org/), read-only queries only, 60-second limit, 10,000 rows shown |

The data file holds the precomputed tables for every page (`lender_msa_counts`, `msa_states`,
`explorer_totals` / `explorer_groups` / `explorer_reasons`), plus the full records for the SQL box: `hmda_lar`,
`hmda_clean`, `ffiec_census_tract`, `hmda_respondents`, `msa_md_names` and the `hmda_with_census` view.

**Free-form SQL when hosted** runs as DuckDB SQL, which is close to PostgreSQL. Only single read-only statements run
(`SELECT`, `WITH`, `EXPLAIN`, `DESCRIBE`, `SHOW`); DuckDB has file, network and extension access switched off and its
settings locked, so a query can't read server files, reach the internet or change anything.
Use `DESCRIBE hmda_lar` to list a table's columns.

**Password.** The site is open to the internet, and the app asks for one shared password before showing anything
(`auth.py`). Only a PBKDF2 hash of it is stored, in Secret Manager (`fair-lending-password-hash`). If that secret is
missing, the hosted app shows nothing at all. Signing in lasts for the browser tab: reloading the page asks again.
Locally there is no password.

**Deploy** from **Git Bash** in the project folder, with the [gcloud CLI](https://cloud.google.com/sdk/docs/install)
signed in (`gcloud auth login`, then `gcloud config set project <project-id>`):

```bash
bash deploy/set_password.sh        # once, and whenever you change it: prompts, stores only the hash
bash deploy/cloudrun.sh --export   # rebuild the data file from local PostgreSQL, then deploy
bash deploy/cloudrun.sh            # later: deploy code changes with the existing data file
```

`cloudrun.sh` builds the image with Cloud Build (uploading the code and the data file, not `data/`), stores it in
Artifact Registry and deploys service `fair-lending` in `us-west1`, running as its own service account whose only
permission is reading the password secret. At most 3 instances run, which caps the cost.

**Address: https://ecarleton.com/redlining.** The app runs with Streamlit's base path set to `/redlining`, and a free
Cloudflare Worker (`deploy/cloudflare-worker.js`) forwards `ecarleton.com/redlining*` to the Cloud Run service;
Cloudflare provides HTTPS, and the rest of ecarleton.com is untouched. Set up once in the Cloudflare dashboard:

1. **Workers & Pages → Create → Worker**, name it `fair-lending-redlining`, paste `deploy/cloudflare-worker.js`, **Deploy**.
2. In the Worker: **Settings → Domains & Routes → Add → Route**: `ecarleton.com/redlining*`, zone `ecarleton.com`.
3. **DNS → Records**: if `ecarleton.com` has no proxied record yet, add `AAAA` `@` `100::` with **Proxied** on
   (a placeholder Cloudflare uses for Worker-only names). Leave the Proton Mail TXT record as it is.

The Cloud Run address also works directly: `https://fair-lending-542044103226.us-west1.run.app/redlining/`.

Files: `auth.py`, `deploy/Dockerfile.cloudrun`, `deploy/cloudbuild.yaml`, `deploy/cloudrun.sh`, `deploy/set_password.sh`,
`deploy/cloudflare-worker.js`, `scripts/hash_password.py`, `.gcloudignore`.

## Starting over

The database lives in the Docker volume `fair-lending_devcontainer_pgdata` (its name predates the shared setup),
so it survives restarts and rebuilds. To wipe it, run this from WSL in the project folder:

```bash
docker compose down -v
```
