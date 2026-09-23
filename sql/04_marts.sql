-- Stage 05 SQL - marts: dimensions, the area fact, and spray demand.
-- Reads:  staging.stg_crop_production, staging.district_alias (both
--         read-only, from earlier stages).
-- Writes: staging.crop_spray_passes, staging.season_calendar,
--             staging.assumptions - DDL only here; rows are COPYed in by
--             python/05_marts.py between the two halves of this file (see
--             the SEEDS LOADED HERE marker below).
--         staging.chk_spray_crop_mismatch, staging.chk_spray_passes_range,
--             staging.chk_spray_passes_null, staging.chk_spray_missing_source
--             - seed validation; python fails the run if any is non-empty.
--         marts.dim_district, marts.dim_crop, marts.dim_season,
--             marts.dim_year - one row per staging district / seed crop /
--             calendar season / distinct year.
--         marts.fct_crop_area - grain district_key x year_label x season_key
--             x crop_key. One row per staging row with a non-null area_ha.
--         marts.mv_spray_demand - materialised view, grain district_key x
--             year_label x season_key: acres in scope, unsourced, not
--             modelled, plant-protection spray acres at low/base/high, and
--             nutrient spray acres.
--         staging.chk_fact_row_count, staging.chk_fact_area_reconciliation,
--             staging.chk_fact_grain_duplicate, staging.chk_fact_orphan_fk,
--             staging.chk_fact_negative_area, staging.chk_mv_scope_identity,
--             staging.chk_mv_vs_fact_area, staging.chk_mv_pp_base_recompute -
--             quality checks; see python/05_marts.py for the pass/fail gates.
-- Re-run: safe. Every table and the view are dropped and rebuilt; staging
--         and district_alias are never written to.
-- Portable: PostgreSQL 16 only (materialised views; unlike earlier stages,
--         this file is not written to also run on DuckDB).

CREATE SCHEMA IF NOT EXISTS marts;

-- Dropped first, before any seed table: mv_spray_demand's defining query
-- references staging.assumptions directly, so on a re-run, dropping that
-- table before the view depends on it would fail with DependentObjectsStillExist.
DROP MATERIALIZED VIEW IF EXISTS marts.mv_spray_demand;

DROP TABLE IF EXISTS staging.crop_spray_passes;
CREATE TABLE staging.crop_spray_passes (
    crop             text     PRIMARY KEY,
    in_scope         text     NOT NULL CHECK (in_scope IN ('Y', 'N')),
    pp_passes_low    numeric,
    pp_passes_base   numeric,
    pp_passes_high   numeric,
    basis            text     NOT NULL
        CHECK (basis IN ('observed_survey', 'recommended_schedule', 'unsourced', 'not_modelled')),
    source_title     text,
    source_url       text,
    source_grade     text CHECK (source_grade IN ('A', 'B', 'C')),
    needs_check      text     NOT NULL CHECK (needs_check IN ('Y', 'N')),
    note             text
);

DROP TABLE IF EXISTS staging.season_calendar;
CREATE TABLE staging.season_calendar (
    season         text     PRIMARY KEY,
    start_month    int      NOT NULL CHECK (start_month BETWEEN 1 AND 12),
    end_month      int      NOT NULL CHECK (end_month BETWEEN 1 AND 12),
    source_title   text     NOT NULL,
    source_url     text     NOT NULL,
    source_grade   text     NOT NULL,
    note           text
);

DROP TABLE IF EXISTS staging.assumptions;
CREATE TABLE staging.assumptions (
    key     text  PRIMARY KEY,
    value   text  NOT NULL,
    source  text  NOT NULL
);

-- python/05_marts.py splits this file on the marker line below, runs
-- everything above it, COPYs seeds/crop_spray_passes.csv,
-- seeds/season_calendar.csv and seeds/assumptions.csv into the tables above,
-- then runs everything below it.
-- ===== SEEDS LOADED HERE =====

-- Every crop in staging must be in the seed, and every seed crop must be in
-- staging - symmetric, so a crop dropped from the source or added to it
-- fails loudly instead of silently changing what the marts cover.
DROP TABLE IF EXISTS staging.chk_spray_crop_mismatch;
CREATE TABLE staging.chk_spray_crop_mismatch AS
SELECT DISTINCT sp.crop_name AS crop, 'in staging, not in seed' AS problem
FROM staging.stg_crop_production sp
WHERE NOT EXISTS (SELECT 1 FROM staging.crop_spray_passes c WHERE c.crop = sp.crop_name)
UNION ALL
SELECT c.crop, 'in seed, not in staging'
FROM staging.crop_spray_passes c
WHERE NOT EXISTS (SELECT 1 FROM staging.stg_crop_production sp WHERE sp.crop_name = c.crop);

DROP TABLE IF EXISTS staging.chk_spray_passes_range;
CREATE TABLE staging.chk_spray_passes_range AS
SELECT crop, pp_passes_low, pp_passes_base, pp_passes_high
FROM staging.crop_spray_passes
WHERE pp_passes_low IS NOT NULL
  AND NOT (pp_passes_low <= pp_passes_base AND pp_passes_base <= pp_passes_high);

-- A pass count may only be null when the basis says why: no source
-- (unsourced) or out of scope entirely (not_modelled). Any other basis
-- promises a number.
DROP TABLE IF EXISTS staging.chk_spray_passes_null;
CREATE TABLE staging.chk_spray_passes_null AS
SELECT crop, basis
FROM staging.crop_spray_passes
WHERE (pp_passes_low IS NULL OR pp_passes_base IS NULL OR pp_passes_high IS NULL)
  AND basis NOT IN ('unsourced', 'not_modelled');

-- An in-scope row must cite a source unless it is honestly unsourced -
-- "in scope but no source and no admission of that" is the one state the
-- seed must not be allowed to reach.
DROP TABLE IF EXISTS staging.chk_spray_missing_source;
CREATE TABLE staging.chk_spray_missing_source AS
SELECT crop, basis
FROM staging.crop_spray_passes
WHERE in_scope = 'Y'
  AND (source_url IS NULL OR btrim(source_url) = '')
  AND basis <> 'unsourced';

-- ---- dimensions -------------------------------------------------------

DROP TABLE IF EXISTS marts.dim_district;
CREATE TABLE marts.dim_district AS
SELECT
    district_key,
    state_name AS state,
    district_name,
    unit_key,
    unit_label,
    first_year,
    last_year,
    is_coverage_gap
FROM staging.district_alias;
ALTER TABLE marts.dim_district ADD PRIMARY KEY (district_key);

DROP TABLE IF EXISTS marts.dim_crop;
CREATE TABLE marts.dim_crop AS
SELECT
    crop AS crop_key,
    crop,
    (in_scope = 'Y') AS in_scope,
    pp_passes_low,
    pp_passes_base,
    pp_passes_high,
    basis,
    source_grade,
    (needs_check = 'Y') AS needs_check
FROM staging.crop_spray_passes;
ALTER TABLE marts.dim_crop ADD PRIMARY KEY (crop_key);

-- The data's seasons only: Kharif, Rabi, Summer - there is no whole-year
-- season (season_calendar's rows already match this exactly; the fact's
-- orphan-FK check below would catch it if a future season didn't).
DROP TABLE IF EXISTS marts.dim_season;
CREATE TABLE marts.dim_season AS
SELECT
    season AS season_key,
    season,
    start_month,
    end_month
FROM staging.season_calendar;
ALTER TABLE marts.dim_season ADD PRIMARY KEY (season_key);

DROP TABLE IF EXISTS marts.dim_year;
CREATE TABLE marts.dim_year AS
WITH cfg AS (
    SELECT
        (left((SELECT value FROM staging.assumptions WHERE key = 'trend_start_year'), 4))::int AS trend_start,
        (left((SELECT value FROM staging.assumptions WHERE key = 'latest_complete_year'), 4))::int AS latest_complete
)
SELECT DISTINCT
    sp.year_label,
    sp.year_start AS start_year,
    sp.year_start <= cfg.latest_complete AS is_complete,
    sp.year_start BETWEEN cfg.trend_start AND cfg.latest_complete AS in_trend_window
FROM staging.stg_crop_production sp
CROSS JOIN cfg;
ALTER TABLE marts.dim_year ADD PRIMARY KEY (year_label);

-- ---- fact ---------------------------------------------------------------

-- Grain: one row per staging row with a non-null area_ha. Production is not
-- carried here - cotton/jute/mesta/sannhemp report production in bales, not
-- tonnes (seeds/crops.csv), so a production fact would need a per-crop unit
-- column before it could ever be summed; area alone has no such problem.
DROP TABLE IF EXISTS marts.fct_crop_area;
CREATE TABLE marts.fct_crop_area AS
SELECT
    da.district_key,
    sp.year_label,
    sp.season AS season_key,
    sp.crop_name AS crop_key,
    sp.area_ha,
    sp.area_ha * (SELECT value::numeric FROM staging.assumptions WHERE key = 'hectare_to_acre') AS area_acres
FROM staging.stg_crop_production sp
JOIN staging.district_alias da
  ON da.state_name = sp.state_name AND da.district_name = sp.district_name
WHERE sp.area_ha IS NOT NULL;
ALTER TABLE marts.fct_crop_area
    ADD PRIMARY KEY (district_key, year_label, season_key, crop_key);

-- ---- fact quality checks --------------------------------------------------

DROP TABLE IF EXISTS staging.chk_fact_row_count;
CREATE TABLE staging.chk_fact_row_count AS
SELECT
    (SELECT count(*) FROM staging.stg_crop_production WHERE area_ha IS NOT NULL) AS staging_rows_with_area,
    (SELECT count(*) FROM marts.fct_crop_area) AS fact_rows;

-- Area totals by state and year must be identical in staging and in the
-- fact: the join to district_alias must be lossless and the area
-- filter must not have changed anything it shouldn't.
DROP TABLE IF EXISTS staging.chk_fact_area_reconciliation;
CREATE TABLE staging.chk_fact_area_reconciliation AS
WITH by_staging AS (
    SELECT state_name, year_label, sum(area_ha) AS area_ha
    FROM staging.stg_crop_production
    WHERE area_ha IS NOT NULL
    GROUP BY state_name, year_label
),
by_fact AS (
    SELECT da.state AS state_name, f.year_label, sum(f.area_ha) AS area_ha
    FROM marts.fct_crop_area f
    JOIN marts.dim_district da ON da.district_key = f.district_key
    GROUP BY da.state, f.year_label
)
SELECT bs.state_name, bs.year_label, bs.area_ha AS staging_ha, bf.area_ha AS fact_ha,
       bs.area_ha - bf.area_ha AS diff_ha
FROM by_staging bs
JOIN by_fact bf ON bf.state_name = bs.state_name AND bf.year_label = bs.year_label
WHERE bs.area_ha IS DISTINCT FROM bf.area_ha;

DROP TABLE IF EXISTS staging.chk_fact_grain_duplicate;
CREATE TABLE staging.chk_fact_grain_duplicate AS
SELECT district_key, year_label, season_key, crop_key, count(*) AS n
FROM marts.fct_crop_area
GROUP BY district_key, year_label, season_key, crop_key
HAVING count(*) > 1;

DROP TABLE IF EXISTS staging.chk_fact_orphan_fk;
CREATE TABLE staging.chk_fact_orphan_fk AS
SELECT f.district_key, f.year_label, f.season_key, f.crop_key, 'district_key' AS bad_fk
FROM marts.fct_crop_area f
WHERE NOT EXISTS (SELECT 1 FROM marts.dim_district d WHERE d.district_key = f.district_key)
UNION ALL
SELECT f.district_key, f.year_label, f.season_key, f.crop_key, 'year_label'
FROM marts.fct_crop_area f
WHERE NOT EXISTS (SELECT 1 FROM marts.dim_year y WHERE y.year_label = f.year_label)
UNION ALL
SELECT f.district_key, f.year_label, f.season_key, f.crop_key, 'season_key'
FROM marts.fct_crop_area f
WHERE NOT EXISTS (SELECT 1 FROM marts.dim_season s WHERE s.season_key = f.season_key)
UNION ALL
SELECT f.district_key, f.year_label, f.season_key, f.crop_key, 'crop_key'
FROM marts.fct_crop_area f
WHERE NOT EXISTS (SELECT 1 FROM marts.dim_crop c WHERE c.crop_key = f.crop_key);

DROP TABLE IF EXISTS staging.chk_fact_negative_area;
CREATE TABLE staging.chk_fact_negative_area AS
SELECT * FROM marts.fct_crop_area WHERE area_ha < 0;

-- ---- spray demand ---------------------------------------------------------
-- (already dropped at the top of this file, before the seed tables it reads)

CREATE MATERIALIZED VIEW marts.mv_spray_demand AS
WITH agg AS (
    SELECT
        f.district_key,
        f.year_label,
        f.season_key,
        sum(f.area_acres) AS total_acres,
        COALESCE(sum(f.area_acres) FILTER (WHERE c.in_scope), 0) AS in_scope_acres,
        COALESCE(sum(f.area_acres) FILTER (WHERE c.in_scope AND c.pp_passes_base IS NULL), 0) AS unsourced_acres,
        COALESCE(sum(f.area_acres) FILTER (WHERE NOT c.in_scope), 0) AS not_modelled_acres,
        COALESCE(sum(f.area_acres * c.pp_passes_low)  FILTER (WHERE c.pp_passes_low  IS NOT NULL), 0) AS pp_spray_acres_low,
        COALESCE(sum(f.area_acres * c.pp_passes_base) FILTER (WHERE c.pp_passes_base IS NOT NULL), 0) AS pp_spray_acres_base,
        COALESCE(sum(f.area_acres * c.pp_passes_high) FILTER (WHERE c.pp_passes_high IS NOT NULL), 0) AS pp_spray_acres_high
    FROM marts.fct_crop_area f
    JOIN marts.dim_crop c ON c.crop_key = f.crop_key
    GROUP BY f.district_key, f.year_label, f.season_key
)
SELECT
    a.*,
    a.in_scope_acres * (SELECT value::numeric FROM staging.assumptions WHERE key = 'nutrient_passes_per_crop_season')
        AS nutrient_spray_acres
FROM agg a;

-- Every crop is either in scope or not (dim_crop.in_scope is never NULL),
-- so this identity always holds; the check exists to catch a future bug in
-- the view, not because the arithmetic is in doubt.
DROP TABLE IF EXISTS staging.chk_mv_scope_identity;
CREATE TABLE staging.chk_mv_scope_identity AS
SELECT district_key, year_label, season_key, total_acres, in_scope_acres, not_modelled_acres
FROM marts.mv_spray_demand
WHERE total_acres IS DISTINCT FROM in_scope_acres + not_modelled_acres;

DROP TABLE IF EXISTS staging.chk_mv_vs_fact_area;
CREATE TABLE staging.chk_mv_vs_fact_area AS
WITH by_mv AS (
    SELECT da.state, m.year_label, sum(m.total_acres) AS acres
    FROM marts.mv_spray_demand m
    JOIN marts.dim_district da ON da.district_key = m.district_key
    GROUP BY da.state, m.year_label
),
by_fact AS (
    SELECT da.state, f.year_label, sum(f.area_acres) AS acres
    FROM marts.fct_crop_area f
    JOIN marts.dim_district da ON da.district_key = f.district_key
    GROUP BY da.state, f.year_label
)
SELECT bm.state, bm.year_label, bm.acres AS mv_acres, bf.acres AS fact_acres,
       bm.acres - bf.acres AS diff_acres
FROM by_mv bm
JOIN by_fact bf ON bf.state = bm.state AND bf.year_label = bm.year_label
WHERE bm.acres IS DISTINCT FROM bf.acres;

-- Independent recomputation of pp_spray_acres_base straight from the fact
-- and dim_crop, not by reusing the view's own SQL, so a bug shared between
-- the view and this check would still be very unlikely to agree by chance.
DROP TABLE IF EXISTS staging.chk_mv_pp_base_recompute;
CREATE TABLE staging.chk_mv_pp_base_recompute AS
WITH recomputed AS (
    SELECT f.district_key, f.year_label, f.season_key,
           sum(f.area_acres * c.pp_passes_base) AS pp_spray_acres_base
    FROM marts.fct_crop_area f
    JOIN marts.dim_crop c ON c.crop_key = f.crop_key
    WHERE c.pp_passes_base IS NOT NULL
    GROUP BY f.district_key, f.year_label, f.season_key
)
SELECT m.district_key, m.year_label, m.season_key,
       m.pp_spray_acres_base AS view_value, r.pp_spray_acres_base AS recomputed_value,
       m.pp_spray_acres_base - COALESCE(r.pp_spray_acres_base, 0) AS diff
FROM marts.mv_spray_demand m
LEFT JOIN recomputed r
  ON r.district_key = m.district_key AND r.year_label = m.year_label AND r.season_key = m.season_key
WHERE m.pp_spray_acres_base IS DISTINCT FROM COALESCE(r.pp_spray_acres_base, 0);
