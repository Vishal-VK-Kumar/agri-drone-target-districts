-- Stage 03 SQL - long-format staging table for the UPAg district APY exports.
-- Reads:  raw.crop_production_import (read-only).
-- Writes: staging.seed_crop_groups, staging.seed_crops - DDL only here; rows
--             are COPYed in by python/03_stage.py between the two halves of
--             this file (see the SEEDS LOADED HERE marker below).
--         staging._crop_production_long - unpivoted, cleaned, one row per
--             state x district x crop x season x year. Internal: not part of
--             the staging contract, kept only so the checks below can query it.
--         staging.chk_season_total   - rows removed because Season = 'Total'.
--         staging.chk_crop_group     - rows removed because the crop is a
--             group total (seeds/crop_group_rows.csv), not an individual crop.
--         staging.chk_unknown_crop   - crop names that are in neither seed
--             file; python/03_stage.py fails the run if this is non-empty.
--         staging.rej_empty_year     - rows removed because area, production
--             and yield are all NULL for that year.
--         staging.chk_total_reconciliation - one row per district-crop-year
--             comparing the Total row to the season-row sum, status one of
--             matched/mismatched/both_null/one_null; python/03_stage.py fails
--             the run on any mismatched row, and on any one_null row whose
--             state+year is not in its documented allow-list.
--         staging.stg_crop_production - the kept rows: the staging output.
-- Re-run: safe. Every table here is dropped and rebuilt; raw and the seed
--         CSVs are never modified.
-- Portable: plain SQL that runs unchanged on PostgreSQL 16 and DuckDB.

CREATE SCHEMA IF NOT EXISTS staging;

DROP TABLE IF EXISTS staging.seed_crop_groups;
CREATE TABLE staging.seed_crop_groups (
    crop_name  text  PRIMARY KEY
);

DROP TABLE IF EXISTS staging.seed_crops;
CREATE TABLE staging.seed_crops (
    crop_name        text     PRIMARY KEY,
    production_unit  text     NOT NULL,  -- 'tonnes' or 'bales'
    unit_weight_kg   numeric  NOT NULL,  -- kg represented by one unit of production_unit
    source           text     NOT NULL
);

-- python/03_stage.py splits this file on the marker line below, runs
-- everything above it, COPYs seeds/crop_group_rows.csv and seeds/crops.csv
-- into the two tables above, then runs everything below it.
-- ===== SEEDS LOADED HERE =====

-- The exports are wide (one column per metric-year); unpivoting to one row
-- per year is the only reshape staging does. Trimming and whitespace
-- collapse are defensive - the current exports already have none - so a
-- future export with stray spacing does not silently change grain or join
-- against the seed tables on the wrong crop name. Empty strings become NULL
-- (never 0): a missing figure and a true zero are different facts, and only
-- one of them is in this data (see staging.rej_empty_year below).
DROP TABLE IF EXISTS staging._crop_production_long;
CREATE TABLE staging._crop_production_long AS
WITH cleaned AS (
    SELECT
        source_file,
        source_row,
        btrim(regexp_replace(state,    '\s+', ' ', 'g'))  AS state_name,
        btrim(regexp_replace(district, '\s+', ' ', 'g'))  AS district_name,
        btrim(regexp_replace(crop,     '\s+', ' ', 'g'))  AS crop_name,
        btrim(regexp_replace(season,   '\s+', ' ', 'g'))  AS season,
        year_1, year_2, year_3,
        NULLIF(btrim(area_year_1), '')        AS area_year_1,
        NULLIF(btrim(area_year_2), '')        AS area_year_2,
        NULLIF(btrim(area_year_3), '')        AS area_year_3,
        NULLIF(btrim(production_year_1), '')  AS production_year_1,
        NULLIF(btrim(production_year_2), '')  AS production_year_2,
        NULLIF(btrim(production_year_3), '')  AS production_year_3,
        NULLIF(btrim(yield_year_1), '')       AS yield_year_1,
        NULLIF(btrim(yield_year_2), '')       AS yield_year_2,
        NULLIF(btrim(yield_year_3), '')       AS yield_year_3
    FROM raw.crop_production_import
)
SELECT source_file, source_row, state_name, district_name, crop_name, season,
       year_1 AS year_label, (left(year_1, 4))::int AS year_start,
       area_year_1::numeric AS area_ha, production_year_1::numeric AS production,
       yield_year_1::numeric AS yield
FROM cleaned
UNION ALL
SELECT source_file, source_row, state_name, district_name, crop_name, season,
       year_2 AS year_label, (left(year_2, 4))::int AS year_start,
       area_year_2::numeric AS area_ha, production_year_2::numeric AS production,
       yield_year_2::numeric AS yield
FROM cleaned
UNION ALL
SELECT source_file, source_row, state_name, district_name, crop_name, season,
       year_3 AS year_label, (left(year_3, 4))::int AS year_start,
       area_year_3::numeric AS area_ha, production_year_3::numeric AS production,
       yield_year_3::numeric AS yield
FROM cleaned;

-- Kind 1: Season totals. Kept aside (not dropped) because they are what the
-- reconciliation check below compares the season rows against.
DROP TABLE IF EXISTS staging.chk_season_total;
CREATE TABLE staging.chk_season_total AS
SELECT *
FROM staging._crop_production_long
WHERE season = 'Total';

-- Kind 2: Crop-group rows (Cereals, Total Food Grains, ...). These double-count
-- against their member crops, so they never belong in stg_crop_production.
DROP TABLE IF EXISTS staging.chk_crop_group;
CREATE TABLE staging.chk_crop_group AS
SELECT l.*
FROM staging._crop_production_long l
JOIN staging.seed_crop_groups g ON g.crop_name = l.crop_name
WHERE l.season <> 'Total';

-- Every crop name reaching this point must be in exactly one seed file. A
-- name in neither means the source added a crop that was never classified -
-- python/03_stage.py fails the run if this table is non-empty.
DROP TABLE IF EXISTS staging.chk_unknown_crop;
CREATE TABLE staging.chk_unknown_crop AS
SELECT l.crop_name, count(*) AS n_rows
FROM staging._crop_production_long l
WHERE l.season <> 'Total'
  AND NOT EXISTS (SELECT 1 FROM staging.seed_crop_groups g WHERE g.crop_name = l.crop_name)
  AND NOT EXISTS (SELECT 1 FROM staging.seed_crops c WHERE c.crop_name = l.crop_name)
GROUP BY l.crop_name;

-- Kind 3: a year with no figures at all for that district-crop-season. UPAg
-- leaves these blank rather than omitting the row.
DROP TABLE IF EXISTS staging.rej_empty_year;
CREATE TABLE staging.rej_empty_year AS
SELECT l.*
FROM staging._crop_production_long l
WHERE l.season <> 'Total'
  AND NOT EXISTS (SELECT 1 FROM staging.seed_crop_groups g WHERE g.crop_name = l.crop_name)
  AND l.area_ha IS NULL AND l.production IS NULL AND l.yield IS NULL;

-- The staging output: individual crops, individual seasons, at least one
-- figure present. production_unit comes from seeds/crops.csv per crop -
-- production is never summed across crops, so a mixed tonnes/bales unit
-- column is safe to carry forward as-is.
DROP TABLE IF EXISTS staging.stg_crop_production;
CREATE TABLE staging.stg_crop_production AS
SELECT
    l.state_name,
    l.district_name,
    l.crop_name,
    l.season,
    l.year_label,
    l.year_start,
    l.area_ha,
    l.production,
    c.production_unit,
    l.yield,
    l.source_file,
    l.source_row
FROM staging._crop_production_long l
JOIN staging.seed_crops c ON c.crop_name = l.crop_name
WHERE l.season <> 'Total'
  AND NOT EXISTS (SELECT 1 FROM staging.seed_crop_groups g WHERE g.crop_name = l.crop_name)
  AND NOT (l.area_ha IS NULL AND l.production IS NULL AND l.yield IS NULL);

-- Quality check: for every district-crop-year, the Total row's area and the
-- sum of its season rows must agree within 1 ha. Applies to every crop name
-- (individual and group alike), so it is built off staging._crop_production_-
-- long directly rather than off stg_crop_production. A FULL OUTER JOIN, not
-- an inner one: a key present on only one side (or NULL on one side) is a
-- real fact about that key, not a row to silently drop. status is one of:
--   'matched'     - both sides present, agree within 1 ha
--   'mismatched'  - both sides present, disagree by more than 1 ha
--   'both_null'   - neither side has a usable figure (nothing to compare;
--                   expected wherever UPAg has not reported the year yet)
--   'one_null'    - one side has a figure and the other does not - a real
--                   discrepancy (aggregate without a breakdown, or vice
--                   versa), not a skip
DROP TABLE IF EXISTS staging.chk_total_reconciliation;
CREATE TABLE staging.chk_total_reconciliation AS
WITH totals AS (
    SELECT state_name, district_name, crop_name, year_label, area_ha AS total_area_ha
    FROM staging.chk_season_total
),
seasons AS (
    SELECT state_name, district_name, crop_name, year_label, SUM(area_ha) AS season_area_ha
    FROM staging._crop_production_long
    WHERE season <> 'Total'
    GROUP BY state_name, district_name, crop_name, year_label
)
SELECT
    state_name, district_name, crop_name, year_label,
    t.total_area_ha, s.season_area_ha,
    abs(t.total_area_ha - s.season_area_ha) AS diff_ha,
    CASE
        WHEN t.total_area_ha IS NULL AND s.season_area_ha IS NULL THEN 'both_null'
        WHEN t.total_area_ha IS NULL OR  s.season_area_ha IS NULL THEN 'one_null'
        WHEN abs(t.total_area_ha - s.season_area_ha) <= 1.0        THEN 'matched'
        ELSE 'mismatched'
    END AS status
FROM totals t
FULL OUTER JOIN seasons s USING (state_name, district_name, crop_name, year_label);
