-- Stage 07 SQL - export: one SELECT per marts object, for python/07_export.py
-- to write to output/marts/ as Parquet (and CSV for the dims and the
-- ranking).
-- Reads:  marts.dim_crop, marts.dim_crop_state_passes, marts.dim_district,
--         marts.dim_season, marts.dim_year, marts.fct_crop_area,
--         marts.mv_spray_demand, marts.rpt_crop_base,
--         marts.rpt_state_rollup, marts.rpt_top_districts,
--         marts.rpt_unit_trend - every table and materialised view in the
--         marts schema at the time this file was written. Recomputes
--         nothing: every SELECT reads straight from an existing marts
--         object, in the same column values it already holds.
-- Writes: nothing in the database. python/07_export.py splits this file on
--         the "-- ===== EXPORT: <name> =====" markers below and runs each
--         block as its own query.
-- Re-run: safe. Every query is a plain read.
-- Portable: reads marts.mv_spray_demand, a materialised view that only
--         exists on the Postgres backend, so this stage is Postgres only,
--         same as the marts and analysis stages before it.
--
-- Every NUMERIC measure is cast to double precision here: Postgres NUMERIC
-- has no fixed precision, and pyarrow turns an uncast NUMERIC into a
-- Decimal128 column, which Power BI imports as text-backed and slow. Keys
-- and counts (text, boolean, integer, bigint) are already fixed-width and
-- are exported unchanged. Column order and ORDER BY follow each object's
-- own primary key (or, for the one materialised view and the one rollup
-- table, its natural grain), so every export is byte-for-byte the same
-- across runs on the same data.

-- ===== EXPORT: dim_crop =====
SELECT
    crop_key,
    crop,
    in_scope,
    pp_passes_low::double precision  AS pp_passes_low,
    pp_passes_base::double precision AS pp_passes_base,
    pp_passes_high::double precision AS pp_passes_high,
    basis,
    source_grade,
    needs_check
FROM marts.dim_crop
ORDER BY crop_key;

-- ===== EXPORT: dim_crop_state_passes =====
SELECT
    state,
    crop_key,
    pp_passes_low::double precision  AS pp_passes_low,
    pp_passes_base::double precision AS pp_passes_base,
    pp_passes_high::double precision AS pp_passes_high,
    n_farmers,
    source_grade
FROM marts.dim_crop_state_passes
ORDER BY state, crop_key;

-- ===== EXPORT: dim_district =====
SELECT
    district_key,
    state,
    district_name,
    unit_key,
    unit_label,
    first_year,
    last_year,
    is_coverage_gap
FROM marts.dim_district
ORDER BY district_key;

-- ===== EXPORT: dim_season =====
SELECT
    season_key,
    season,
    start_month,
    end_month
FROM marts.dim_season
ORDER BY season_key;

-- ===== EXPORT: dim_year =====
SELECT
    year_label,
    start_year,
    is_complete,
    in_trend_window
FROM marts.dim_year
ORDER BY year_label;

-- ===== EXPORT: fct_crop_area =====
SELECT
    district_key,
    year_label,
    season_key,
    crop_key,
    area_ha::double precision    AS area_ha,
    area_acres::double precision AS area_acres
FROM marts.fct_crop_area
ORDER BY district_key, year_label, season_key, crop_key;

-- ===== EXPORT: mv_spray_demand =====
SELECT
    district_key,
    year_label,
    season_key,
    total_acres::double precision          AS total_acres,
    in_scope_acres::double precision       AS in_scope_acres,
    unsourced_acres::double precision      AS unsourced_acres,
    not_modelled_acres::double precision   AS not_modelled_acres,
    pp_spray_acres_low::double precision   AS pp_spray_acres_low,
    pp_spray_acres_base::double precision  AS pp_spray_acres_base,
    pp_spray_acres_high::double precision  AS pp_spray_acres_high,
    nutrient_spray_acres::double precision AS nutrient_spray_acres
FROM marts.mv_spray_demand
ORDER BY district_key, year_label, season_key;

-- rpt_crop_base, rpt_state_rollup and rpt_unit_trend are not in the seven
-- marts objects the handoff named; they exist (built by sql/05_analysis.sql)
-- and are exported here for the same reason as the rest: the Power BI
-- report opens from committed files, not a live database, so anything a
-- report page might join against has to be one of them.

-- ===== EXPORT: rpt_crop_base =====
SELECT
    district_key,
    season,
    crop_key,
    needs_check,
    crop_base_acres::double precision AS crop_base_acres
FROM marts.rpt_crop_base
ORDER BY district_key, season, crop_key;

-- ===== EXPORT: rpt_state_rollup =====
SELECT
    level,
    state,
    district_name,
    pp_spray_acres_base::double precision AS pp_spray_acres_base,
    n_district_seasons,
    n_decile_1
FROM marts.rpt_state_rollup
ORDER BY level, state, district_name;

-- ===== EXPORT: rpt_unit_trend =====
SELECT
    unit_key,
    year_label,
    start_year,
    unit_in_scope_acres::double precision AS unit_in_scope_acres,
    yoy_change_pct::double precision      AS yoy_change_pct,
    ma3_acres::double precision           AS ma3_acres
FROM marts.rpt_unit_trend
ORDER BY unit_key, year_label;

-- The ranking behind output/top_districts.csv, straight from
-- marts.rpt_top_districts (built by sql/05_analysis.sql) - the ranking
-- itself (DENSE_RANK, NTILE, the LATERAL top-crop join) is not
-- re-implemented here, only read back.

-- ===== EXPORT: top_districts =====
SELECT
    district_key,
    district_name,
    state,
    season,
    sprayable_acres::double precision          AS sprayable_acres,
    pp_spray_acres_low::double precision       AS pp_spray_acres_low,
    pp_spray_acres_base::double precision      AS pp_spray_acres_base,
    pp_spray_acres_high::double precision      AS pp_spray_acres_high,
    rank_national,
    rank_national_low,
    rank_national_high,
    rank_in_state,
    decile,
    top_crop_1,
    top_crop_1_share_pct::double precision     AS top_crop_1_share_pct,
    top_crop_2,
    top_crop_2_share_pct::double precision     AS top_crop_2_share_pct,
    top_crop_3,
    top_crop_3_share_pct::double precision     AS top_crop_3_share_pct,
    open_crop_share_pct::double precision      AS open_crop_share_pct,
    unit_key,
    unit_district_count,
    unit_yoy_area_change_pct::double precision AS unit_yoy_area_change_pct,
    unit_area_ma3_acres::double precision      AS unit_area_ma3_acres
FROM marts.rpt_top_districts
ORDER BY district_key, season;
