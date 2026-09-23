-- Before/after demonstration for step 5 (district lineage).
-- Reads:  staging.stg_crop_production, staging.district_alias (both
--         read-only). Writes nothing - this is a report, not a stage.
-- Shows year-on-year area change for three districts whose 2016-2022
-- boundary changes collapse their raw name in one year, and shows the same
-- years by stable unit, where the collapse disappears:
--   Warangal (Telangana), Ballari (Karnataka), Visakhapatnam (Andhra Pradesh)
-- Run by python/run_split_artefact_demo.py, which formats and saves this.

WITH cases(state_name, district_name) AS (
    VALUES ('Telangana', 'Warangal'),
           ('Karnataka', 'Ballari'),
           ('Andhra Pradesh', 'Visakhapatnam')
),
by_district AS (
    SELECT c.state_name, c.district_name, sp.year_label, sp.year_start,
           sum(sp.area_ha) AS area_ha
    FROM cases c
    JOIN staging.stg_crop_production sp
      ON sp.state_name = c.state_name AND sp.district_name = c.district_name
    GROUP BY c.state_name, c.district_name, sp.year_label, sp.year_start
),
by_district_yoy AS (
    SELECT state_name, district_name, year_label, year_start, area_ha,
           area_ha - lag(area_ha) OVER w AS yoy_change_ha
    FROM by_district
    WINDOW w AS (PARTITION BY state_name, district_name ORDER BY year_start)
),
unit_of_case AS (
    SELECT c.state_name, c.district_name AS case_district_name,
           da.unit_key, da.unit_label
    FROM cases c
    JOIN staging.district_alias da
      ON da.state_name = c.state_name AND da.district_name = c.district_name
),
by_unit AS (
    SELECT u.state_name, u.case_district_name, u.unit_key, u.unit_label,
           sp.year_label, sp.year_start, sum(sp.area_ha) AS area_ha
    FROM unit_of_case u
    JOIN staging.district_alias member
      ON member.state_name = u.state_name AND member.unit_key = u.unit_key
    JOIN staging.stg_crop_production sp
      ON sp.state_name = member.state_name AND sp.district_name = member.district_name
    GROUP BY u.state_name, u.case_district_name, u.unit_key, u.unit_label,
             sp.year_label, sp.year_start
),
by_unit_yoy AS (
    SELECT state_name, case_district_name, unit_label, year_label, year_start, area_ha,
           area_ha - lag(area_ha) OVER w AS yoy_change_ha
    FROM by_unit
    WINDOW w AS (PARTITION BY state_name, unit_key ORDER BY year_start)
)
SELECT 'by_district' AS grouping, state_name, district_name AS case_district_name,
       district_name AS label, year_label, year_start, area_ha, yoy_change_ha
FROM by_district_yoy
UNION ALL
SELECT 'by_unit' AS grouping, state_name, case_district_name,
       unit_label AS label, year_label, year_start, area_ha, yoy_change_ha
FROM by_unit_yoy
ORDER BY state_name, case_district_name, grouping DESC, year_start;
