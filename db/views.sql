-- Helper: turn HMDA text values into numbers; "NA", "Exempt", blanks -> NULL.
CREATE OR REPLACE FUNCTION to_num(v TEXT) RETURNS NUMERIC
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN v ~ '^-?[0-9]+(\.[0-9]+)?$' THEN v::NUMERIC END
$$;

-- Lender names (LEI -> name). Loaded with:
--   python scripts/load_hmda.py --table hmda_respondents --replace data/hmda_2025_CA_respondents.csv
CREATE TABLE IF NOT EXISTS hmda_respondents (lei TEXT, respondent_name TEXT, activity_year TEXT);
ALTER TABLE hmda_respondents ADD COLUMN IF NOT EXISTS agency TEXT;
ALTER TABLE hmda_respondents ADD COLUMN IF NOT EXISTS depository TEXT;   -- Y / N, see scripts/build_respondents.py

-- MSA/MD names from the CFPB's MSA/MD description file. Loaded with:
--   python scripts/load_hmda.py --table msa_md_names --replace data/cfpb/2025_public_msamd_csv.csv
CREATE TABLE IF NOT EXISTS msa_md_names (msa_md TEXT, msa_md_name TEXT, state TEXT);

-- Analysis-friendly view over the raw table. Codes follow the CFPB HMDA
-- data dictionary (2018 onward): https://ffiec.cfpb.gov/documentation/
DROP VIEW IF EXISTS hmda_clean CASCADE;  -- load_hmda.py recreates the census views afterwards
CREATE VIEW hmda_clean AS
SELECT
    to_num(l.activity_year)::INT            AS activity_year,
    l.lei,
    r.respondent_name,
    r.agency                                AS lender_regulator,
    r.depository = 'Y'                      AS is_depository,
    derived_msa_md                          AS msa_md,       -- '99999' = not in an MSA
    CASE open_end_line_of_credit
        WHEN '2' THEN 'Closed-end (mortgage)'
        WHEN '1' THEN 'Open-end (HELOC)'
    END                                     AS product,      -- NULL when 'Exempt' (1111)
    state_code,
    county_code,
    census_tract,

    derived_race,
    derived_ethnicity,
    derived_sex,
    applicant_age,

    action_taken,
    CASE action_taken
        WHEN '1' THEN 'Loan originated'
        WHEN '2' THEN 'Approved, not accepted'
        WHEN '3' THEN 'Application denied'
        WHEN '4' THEN 'Application withdrawn'
        WHEN '5' THEN 'File closed for incompleteness'
        WHEN '6' THEN 'Purchased loan'
        WHEN '7' THEN 'Preapproval request denied'
        WHEN '8' THEN 'Preapproval approved, not accepted'
    END                                     AS action_taken_label,
    -- Decided applications (originated / approved / denied) - the usual denominator
    action_taken IN ('1', '2', '3')         AS is_decision,
    action_taken = '3'                      AS is_denied,

    CASE loan_type
        WHEN '1' THEN 'Conventional'
        WHEN '2' THEN 'FHA'
        WHEN '3' THEN 'VA'
        WHEN '4' THEN 'USDA / RHS'
    END                                     AS loan_type,
    CASE loan_purpose
        WHEN '1'  THEN 'Home purchase'
        WHEN '2'  THEN 'Home improvement'
        WHEN '31' THEN 'Refinancing'
        WHEN '32' THEN 'Cash-out refinancing'
        WHEN '4'  THEN 'Other purpose'
        WHEN '5'  THEN 'Not applicable'
    END                                     AS loan_purpose,
    CASE occupancy_type
        WHEN '1' THEN 'Principal residence'
        WHEN '2' THEN 'Second residence'
        WHEN '3' THEN 'Investment property'
    END                                     AS occupancy_type,

    to_num(loan_amount)                     AS loan_amount,
    to_num(income) * 1000                   AS income,          -- HMDA reports income in $1,000s
    to_num(property_value)                  AS property_value,
    to_num(loan_to_value_ratio)             AS loan_to_value_ratio,
    to_num(interest_rate)                   AS interest_rate,
    to_num(rate_spread)                     AS rate_spread,
    to_num(total_loan_costs)                AS total_loan_costs,
    debt_to_income_ratio,                   -- kept as text: mostly ranges like '30%-<36%'

    CASE denial_reason_1
        WHEN '1'  THEN 'Debt-to-income ratio'
        WHEN '2'  THEN 'Employment history'
        WHEN '3'  THEN 'Credit history'
        WHEN '4'  THEN 'Collateral'
        WHEN '5'  THEN 'Insufficient cash'
        WHEN '6'  THEN 'Unverifiable information'
        WHEN '7'  THEN 'Credit application incomplete'
        WHEN '8'  THEN 'Mortgage insurance denied'
        WHEN '9'  THEN 'Other'
        WHEN '10' THEN 'Not applicable'
    END                                     AS denial_reason_1,

    to_num(tract_minority_population_percent) AS tract_minority_pct,
    to_num(tract_to_msa_income_percentage)    AS tract_to_msa_income_pct
FROM hmda_lar AS l
LEFT JOIN (
    SELECT DISTINCT ON (lei, activity_year) lei, activity_year, respondent_name, agency, depository
    FROM hmda_respondents
    ORDER BY lei, activity_year
) AS r ON r.lei = l.lei AND r.activity_year = l.activity_year;
