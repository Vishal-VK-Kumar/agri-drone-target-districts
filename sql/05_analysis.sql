-- Stage 06 SQL - analysis: which districts to serve first with one drone.
-- Reads:  marts.dim_district, marts.dim_crop, marts.dim_crop_state_passes,
--         marts.dim_year, marts.fct_crop_area, marts.mv_spray_demand - marts
--         only, nothing in staging - this stage is a read model over the
--         marts, not a new ingestion of source data.
-- Writes: marts.rpt_crop_base - district x season x crop base acre-passes
--             for the ranking year only (feeds the top-crops LATERAL below
--             and the crop-share diagnostics in python/06_analysis.py).
--         marts.rpt_unit_trend - unit x year all-season in-scope acres,
--             year-on-year change and a 3-year moving average, for every
--             year in the trend window (not just the ranking year), so the
--             window functions see the whole series they are computed over.
--         marts.rpt_top_districts - one row per district-season in the
--             ranking year with pp_spray_acres_base > 0: national and
--             in-state rank at base (DENSE_RANK), national rank at low and
--             high (DENSE_RANK), national decile (NTILE(10), 1 = highest
--             demand), each district-season's top three crops and their
--             share of its base acre-passes (LATERAL), the share from
--             crops still needs_check = 'Y' ("open" crops), and its unit's
--             size, year-on-year change and 3-year moving average.
--         marts.rpt_state_rollup - district, state and national rows in one
--             pass (GROUPING SETS) over rpt_top_districts: base acre-passes,
--             district-season count, count in decile 1.
--         staging.chk_analysis_base_total, staging.chk_analysis_duplicate,
--             staging.chk_analysis_decile_range,
--             staging.chk_analysis_crop_share_sum - quality checks; see
--             python/06_analysis.py for the pass/fail gates.
-- Re-run: safe. Every table here is dropped and rebuilt; nothing it reads
--         is written to.
-- Portable: PostgreSQL 16 only (reads marts.mv_spray_demand, a materialised
--         view that only exists on the Postgres backend).
--
-- Ranking year and trend window come from marts.dim_year (is_complete,
-- in_trend_window), never a literal year, so a future year's data moves
-- this stage without an edit.
--
-- Seasons (docs/spray_passes_notes.md, "Seasons"): the source relabels
-- seasons before 2023-24, so season is used only inside the ranking year,
-- for the top_districts grain and the top-crop mix. Year-on-year change and
-- the moving average run on unit-level ALL-SEASON totals only
-- (rpt_unit_trend sums in_scope_acres across season_key before the window
-- functions run), never split by season.
--
-- Trends run on stable units, ranking on current districts
-- (docs/district_alias_notes.md, "Stable units"): rpt_unit_trend is grain
-- unit_key x year; rpt_top_districts carries its own district_key and the
-- unit's trend columns side by side, plus unit_district_count, so a
-- multi-district unit is never mistaken for a single district's own trend.

-- ---- district x season x crop base acre-passes, ranking year only -------
DROP TABLE IF EXISTS marts.rpt_crop_base;
CREATE TABLE marts.rpt_crop_base AS
WITH ranking_year AS (
    SELECT year_label FROM marts.dim_year WHERE is_complete ORDER BY start_year DESC LIMIT 1
)
SELECT
    f.district_key,
    f.season_key AS season,
    c.crop_key,
    c.needs_check,
    sum(f.area_acres * COALESCE(csp.pp_passes_base, c.pp_passes_base)) AS crop_base_acres
FROM marts.fct_crop_area f
JOIN marts.dim_district da ON da.district_key = f.district_key
JOIN marts.dim_crop c ON c.crop_key = f.crop_key AND c.in_scope
JOIN ranking_year ry ON ry.year_label = f.year_label
LEFT JOIN marts.dim_crop_state_passes csp
  ON csp.state = da.state AND csp.crop_key = f.crop_key
GROUP BY f.district_key, f.season_key, c.crop_key, c.needs_check;
ALTER TABLE marts.rpt_crop_base ADD PRIMARY KEY (district_key, season, crop_key);

-- ---- unit-level all-season in-scope acres, LAG and MA3 over the trend
--      window (every trend-window year, not just the ranking year) --------
DROP TABLE IF EXISTS marts.rpt_unit_trend;
CREATE TABLE marts.rpt_unit_trend AS
WITH unit_year AS (
    SELECT
        d.unit_key,
        m.year_label,
        y.start_year,
        sum(m.in_scope_acres) AS unit_in_scope_acres
    FROM marts.mv_spray_demand m
    JOIN marts.dim_district d ON d.district_key = m.district_key
    JOIN marts.dim_year y ON y.year_label = m.year_label
    WHERE y.in_trend_window
    GROUP BY d.unit_key, m.year_label, y.start_year
)
SELECT
    unit_key,
    year_label,
    start_year,
    unit_in_scope_acres,
    (unit_in_scope_acres - lag(unit_in_scope_acres) OVER w)
        / NULLIF(lag(unit_in_scope_acres) OVER w, 0) * 100 AS yoy_change_pct,
    avg(unit_in_scope_acres) OVER (
        PARTITION BY unit_key ORDER BY start_year
        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
    ) AS ma3_acres
FROM unit_year
WINDOW w AS (PARTITION BY unit_key ORDER BY start_year);
ALTER TABLE marts.rpt_unit_trend ADD PRIMARY KEY (unit_key, year_label);

-- ---- one row per district-season in the ranking year ---------------------
DROP TABLE IF EXISTS marts.rpt_top_districts;
CREATE TABLE marts.rpt_top_districts AS
WITH ranking_year AS (
    SELECT year_label FROM marts.dim_year WHERE is_complete ORDER BY start_year DESC LIMIT 1
),
base AS (
    SELECT
        m.district_key,
        d.district_name,
        d.state,
        m.season_key AS season,
        d.unit_key,
        m.in_scope_acres AS sprayable_acres,
        m.pp_spray_acres_low,
        m.pp_spray_acres_base,
        m.pp_spray_acres_high
    FROM marts.mv_spray_demand m
    JOIN marts.dim_district d ON d.district_key = m.district_key
    JOIN ranking_year ry ON ry.year_label = m.year_label
    WHERE m.pp_spray_acres_base > 0
),
-- DENSE_RANK's ORDER BY carries no tie-break column: two district-seasons
-- with the same acre-passes must get the same rank. NTILE below adds the
-- tie-break instead, since a decile boundary needs a deterministic side to
-- land tied rows on.
ranked AS (
    SELECT
        base.*,
        dense_rank() OVER (ORDER BY pp_spray_acres_base DESC) AS rank_national,
        dense_rank() OVER (ORDER BY pp_spray_acres_low  DESC) AS rank_national_low,
        dense_rank() OVER (ORDER BY pp_spray_acres_high DESC) AS rank_national_high,
        dense_rank() OVER (PARTITION BY state ORDER BY pp_spray_acres_base DESC) AS rank_in_state,
        ntile(10) OVER (ORDER BY pp_spray_acres_base DESC, state, district_name, season) AS decile
    FROM base
),
crop_totals AS (
    SELECT
        district_key,
        season,
        sum(crop_base_acres) AS total_base_acres,
        sum(crop_base_acres) FILTER (WHERE needs_check) AS open_crop_base_acres
    FROM marts.rpt_crop_base
    GROUP BY district_key, season
),
-- LATERAL: for each ranked district-season, its top three crops by base
-- acre-passes. The row_number() rank is computed inside the LATERAL
-- subquery (not in the outer WHERE, which can't see a window function
-- result), then pivoted into three column pairs by the FILTER below -
-- this replaces the nearest-neighbour route-density measure originally
-- planned for M4: there are no district coordinates in this data, and the
-- crop mix explains the ranking better than a synthetic distance would.
top_crops AS (
    SELECT
        r.district_key,
        r.season,
        max(rc.crop_key)        FILTER (WHERE rc.rn = 1) AS top_crop_1,
        max(rc.crop_base_acres) FILTER (WHERE rc.rn = 1) AS top_crop_1_acres,
        max(rc.crop_key)        FILTER (WHERE rc.rn = 2) AS top_crop_2,
        max(rc.crop_base_acres) FILTER (WHERE rc.rn = 2) AS top_crop_2_acres,
        max(rc.crop_key)        FILTER (WHERE rc.rn = 3) AS top_crop_3,
        max(rc.crop_base_acres) FILTER (WHERE rc.rn = 3) AS top_crop_3_acres
    FROM ranked r
    LEFT JOIN LATERAL (
        SELECT
            crop_key,
            crop_base_acres,
            row_number() OVER (ORDER BY crop_base_acres DESC, crop_key) AS rn
        FROM marts.rpt_crop_base cb
        WHERE cb.district_key = r.district_key AND cb.season = r.season
    ) rc ON rc.rn <= 3
    GROUP BY r.district_key, r.season
),
unit_size AS (
    SELECT unit_key, count(DISTINCT district_key) AS unit_district_count
    FROM marts.dim_district
    GROUP BY unit_key
)
SELECT
    r.district_key,
    r.district_name,
    r.state,
    r.season,
    r.sprayable_acres,
    r.pp_spray_acres_low,
    r.pp_spray_acres_base,
    r.pp_spray_acres_high,
    r.rank_national,
    r.rank_national_low,
    r.rank_national_high,
    r.rank_in_state,
    r.decile,
    tc.top_crop_1,
    100.0 * tc.top_crop_1_acres / NULLIF(ct.total_base_acres, 0) AS top_crop_1_share_pct,
    tc.top_crop_2,
    100.0 * tc.top_crop_2_acres / NULLIF(ct.total_base_acres, 0) AS top_crop_2_share_pct,
    tc.top_crop_3,
    100.0 * tc.top_crop_3_acres / NULLIF(ct.total_base_acres, 0) AS top_crop_3_share_pct,
    100.0 * ct.open_crop_base_acres / NULLIF(ct.total_base_acres, 0) AS open_crop_share_pct,
    r.unit_key,
    us.unit_district_count,
    ut.yoy_change_pct AS unit_yoy_area_change_pct,
    ut.ma3_acres AS unit_area_ma3_acres
FROM ranked r
JOIN crop_totals ct ON ct.district_key = r.district_key AND ct.season = r.season
JOIN top_crops tc ON tc.district_key = r.district_key AND tc.season = r.season
JOIN unit_size us ON us.unit_key = r.unit_key
CROSS JOIN ranking_year ry
LEFT JOIN marts.rpt_unit_trend ut ON ut.unit_key = r.unit_key AND ut.year_label = ry.year_label
ORDER BY r.rank_national, r.state, r.district_name, r.season;
ALTER TABLE marts.rpt_top_districts ADD PRIMARY KEY (district_key, season);

-- ---- district, state and national rows in one pass ------------------------
DROP TABLE IF EXISTS marts.rpt_state_rollup;
CREATE TABLE marts.rpt_state_rollup AS
SELECT
    CASE
        WHEN grouping(state) = 1 THEN 'national'
        WHEN grouping(district_name) = 1 THEN 'state'
        ELSE 'district'
    END AS level,
    state,
    district_name,
    sum(pp_spray_acres_base) AS pp_spray_acres_base,
    count(*) AS n_district_seasons,
    count(*) FILTER (WHERE decile = 1) AS n_decile_1
FROM marts.rpt_top_districts
GROUP BY GROUPING SETS ((state, district_name), (state), ())
ORDER BY level, pp_spray_acres_base DESC;

-- ---- quality checks --------------------------------------------------------

-- rpt_top_districts only keeps pp_spray_acres_base > 0 rows, but every row
-- the view drops is exactly a $0 contribution, so the two totals must match
-- to the last digit of the numeric type - no tolerance needed.
DROP TABLE IF EXISTS staging.chk_analysis_base_total;
CREATE TABLE staging.chk_analysis_base_total AS
WITH ranking_year AS (
    SELECT year_label FROM marts.dim_year WHERE is_complete ORDER BY start_year DESC LIMIT 1
)
SELECT
    (SELECT sum(pp_spray_acres_base) FROM marts.rpt_top_districts) AS top_districts_total,
    (SELECT sum(m.pp_spray_acres_base)
     FROM marts.mv_spray_demand m
     JOIN ranking_year ry ON ry.year_label = m.year_label) AS view_total;

DROP TABLE IF EXISTS staging.chk_analysis_duplicate;
CREATE TABLE staging.chk_analysis_duplicate AS
SELECT district_key, season, count(*) AS n
FROM marts.rpt_top_districts
GROUP BY district_key, season
HAVING count(*) > 1;

DROP TABLE IF EXISTS staging.chk_analysis_decile_range;
CREATE TABLE staging.chk_analysis_decile_range AS
SELECT district_key, season, decile
FROM marts.rpt_top_districts
WHERE decile IS NULL OR decile NOT BETWEEN 1 AND 10;

-- A district-season with three or fewer crops has its shares sum to exactly
-- 100, but NUMERIC division of three separate ratios can each round in the
-- same direction, so the sum can clear 100 by less than 1e-9 with no crop
-- left unaccounted for. The tolerance below absorbs that division rounding,
-- not a real overshoot.
DROP TABLE IF EXISTS staging.chk_analysis_crop_share_sum;
CREATE TABLE staging.chk_analysis_crop_share_sum AS
SELECT
    district_key,
    season,
    COALESCE(top_crop_1_share_pct, 0) + COALESCE(top_crop_2_share_pct, 0)
        + COALESCE(top_crop_3_share_pct, 0) AS share_sum
FROM marts.rpt_top_districts
WHERE COALESCE(top_crop_1_share_pct, 0) + COALESCE(top_crop_2_share_pct, 0)
          + COALESCE(top_crop_3_share_pct, 0) > 100 + 1e-6;
