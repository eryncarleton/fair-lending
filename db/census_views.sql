-- Readable names for FFIEC Census flat file columns (ffiec_census_raw.cNNNN = column NNNN
-- in the FFIEC data dictionary). Add more columns here once you have the dictionary.
--
-- "Checked" = values match the matching field in the 2025 California HMDA file for every
-- tract (8,987 of 8,987). Others are from the standard flat-file layout; confirm them
-- against the dictionary.
DROP VIEW IF EXISTS hmda_with_census;
DROP VIEW IF EXISTS ffiec_census_tract;

CREATE VIEW ffiec_census_tract AS
SELECT
    census_tract,
    to_num(c0001)::INT AS census_year,          -- collection year
    c0002              AS msa_md,               -- MSA/MD code
    c0003              AS state_code,           -- FIPS
    c0004              AS county_code,          -- FIPS
    c0005              AS tract_code,
    c0006              AS principal_city_flag,  -- layout; confirm
    c0010              AS urban_rural_flag,     -- layout; confirm (U/R/M)

    to_num(c0014)      AS ffiec_msa_median_family_income,   -- checked
    to_num(c0013)      AS tract_to_msa_income_pct,          -- checked (HMDA rounds it)
    to_num(c0015)::INT AS tract_income_level,                -- 1 low, 2 moderate, 3 middle, 4 upper; consistent with c0013 for 98% of tracts
    CASE c0015 WHEN '1' THEN 'Low' WHEN '2' THEN 'Moderate'
               WHEN '3' THEN 'Middle' WHEN '4' THEN 'Upper' END AS tract_income_level_label,

    to_num(c0084)      AS tract_population,                  -- checked
    to_num(c0029)      AS tract_minority_pct,                -- checked

    -- Race / ethnicity counts (Census "Hispanic or Latino, and not Hispanic by race" table).
    -- Checked for all 9,140 CA tracts: c0030 = population; c0031 + c0032 = c0030;
    -- c0034..c0039 sum to c0033; c0033 + c0040 = c0032; 100 - c0034/c0030 = c0029 (minority %).
    -- County totals match the 2020 Census (e.g. Imperial 85% Hispanic, Los Angeles 7.6% Black).
    to_num(c0031)      AS hispanic_pop,                      -- Hispanic or Latino, any race
    to_num(c0034)      AS nh_white_pop,                      -- not Hispanic, White alone
    to_num(c0035)      AS nh_black_pop,                      -- not Hispanic, Black alone
    round(100 * to_num(c0035) / nullif(to_num(c0030), 0), 2) AS tract_black_pct,
    round(100 * to_num(c0031) / nullif(to_num(c0030), 0), 2) AS tract_hispanic_pct,
    round(100 * (to_num(c0031) + to_num(c0035)) / nullif(to_num(c0030), 0), 2) AS tract_black_hispanic_pct,
    to_num(c1185)      AS owner_occupied_units,              -- checked
    to_num(c0900)      AS one_to_four_family_homes,          -- checked
    to_num(c1058)      AS median_age_housing_units           -- checked (8,963 of 8,987)
FROM ffiec_census_raw;

CREATE VIEW hmda_with_census AS
SELECT h.*,
       c.tract_black_pct,
       c.tract_hispanic_pct,
       c.tract_black_hispanic_pct,
       c.tract_income_level,
       c.tract_income_level_label,
       c.ffiec_msa_median_family_income,
       c.urban_rural_flag,
       c.principal_city_flag,
       c.owner_occupied_units,
       c.one_to_four_family_homes,
       c.median_age_housing_units
FROM hmda_clean h
LEFT JOIN ffiec_census_tract c USING (census_tract);
