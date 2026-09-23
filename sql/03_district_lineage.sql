-- Stage 04 SQL - district lineage seed, stable units, and district_alias.
-- Reads:  staging.stg_crop_production (read-only).
-- Writes: staging.district_lineage - DDL only here; rows are COPYed in by
--             python/04_district_lineage.py between the two halves of this
--             file (see the SEEDS LOADED HERE marker below).
--         staging._staging_districts - distinct (state, district) names in
--             staging. Internal: reused by every step below, not a contract.
--         staging.chk_lineage_unknown_district - lineage rows whose child or
--             parent name is not in staging for that state; python fails the
--             run if this is non-empty (see the seed's coverage promise).
--         staging._district_component - one row per (state, district),
--             labelled with its connected component's alphabetically first
--             member. Internal.
--         staging._unit_label - one row per component, with the ' + '-joined
--             list of members that are not a split/split_and_rename child
--             (i.e. the unit's pre-split identities), or all of the unit's
--             own members if that list would otherwise be empty. Internal.
--         staging.chk_unit_label_missing - units with a NULL or empty label;
--             python fails the run if this is non-empty.
--         staging.district_alias - the stage output: one row per staging
--             (state, district), with district_key, unit_key, unit_label,
--             first_year, last_year, lineage_event, is_coverage_gap.
--         staging.chk_alias_missing, staging.chk_alias_duplicate - should
--             both be empty; python fails the run otherwise.
--         staging.chk_new_district_lineage - districts that first appear
--             after 2013-14 with no split/split_and_rename/coverage_gap
--             lineage row; python fails the run and names them.
--         staging.chk_area_reconciliation - state-year rows where the area
--             total by district and by unit disagree; python fails the run
--             if this is non-empty.
--         staging.chk_units_per_state - units and names per state, printed
--             (not a pass/fail gate).
-- Re-run: safe. Every table here is dropped and rebuilt; staging.stg_crop_-
--         production is never written to.
-- Portable: plain SQL, including the recursive CTE, runs unchanged on
--         PostgreSQL 16 and DuckDB.

CREATE SCHEMA IF NOT EXISTS staging;

DROP TABLE IF EXISTS staging.district_lineage;
CREATE TABLE staging.district_lineage (
    state            text     NOT NULL,
    child_district   text     NOT NULL,
    parent_district  text     NOT NULL,
    event            text     NOT NULL
        CHECK (event IN ('split', 'split_and_rename', 'parent_transfer',
                          'cross_state_transfer', 'coverage_gap')),
    effective_date   date,               -- NULL only for coverage_gap rows
    transfer_scale   text,               -- NULL only for coverage_gap rows
    cluster_edge     text     NOT NULL CHECK (cluster_edge IN ('Y', 'N')),
    source_title     text     NOT NULL,
    source_url       text,               -- NULL for coverage_gap: the source
                                          -- is our own raw export, not a URL
    source_grade     text     NOT NULL
        CHECK (source_grade IN ('official', 'press', 'data', 'encyclopedia')),
    needs_check      text     NOT NULL CHECK (needs_check IN ('Y', 'N')),
    note             text
);

-- python/04_district_lineage.py splits this file on the marker line below,
-- runs everything above it, COPYs seeds/district_lineage.csv into the table
-- above, then runs everything below it.
-- ===== SEEDS LOADED HERE =====

DROP TABLE IF EXISTS staging._staging_districts;
CREATE TABLE staging._staging_districts AS
SELECT DISTINCT state_name, district_name
FROM staging.stg_crop_production;

-- Every child and parent name must already be a staging district in that
-- state. The one exception is a cross_state_transfer parent: `state` holds
-- the CHILD's state (Khammam's Polavaram mandals moved TO Andhra Pradesh
-- districts, so those rows carry state = 'Andhra Pradesh'), so a
-- cross_state_transfer parent is checked against every state, not just this
-- row's `state` column.
DROP TABLE IF EXISTS staging.chk_lineage_unknown_district;
CREATE TABLE staging.chk_lineage_unknown_district AS
SELECT dl.state, dl.child_district AS district_name, 'child' AS role
FROM staging.district_lineage dl
WHERE NOT EXISTS (
    SELECT 1 FROM staging._staging_districts sd
    WHERE sd.state_name = dl.state AND sd.district_name = dl.child_district)
UNION ALL
SELECT dl.state, dl.parent_district, 'parent'
FROM staging.district_lineage dl
WHERE dl.event <> 'cross_state_transfer'
  AND NOT EXISTS (
    SELECT 1 FROM staging._staging_districts sd
    WHERE sd.state_name = dl.state AND sd.district_name = dl.parent_district)
UNION ALL
SELECT dl.state, dl.parent_district, 'parent (cross-state)'
FROM staging.district_lineage dl
WHERE dl.event = 'cross_state_transfer'
  AND NOT EXISTS (
    SELECT 1 FROM staging._staging_districts sd
    WHERE sd.district_name = dl.parent_district);

-- Stable units: connected components over cluster_edge = 'Y' edges, within a
-- state only (edges never cross states - cross_state_transfer is never 'Y').
-- The recursive step propagates a node's current best label onto its
-- neighbours; UNION (not UNION ALL) discards a label a node already has, so
-- the recursion terminates once every node has seen every label reachable
-- from it, and MIN(label) per node is the component's smallest name.
DROP TABLE IF EXISTS staging._district_component;
CREATE TABLE staging._district_component AS
WITH RECURSIVE
edges AS (
    SELECT state AS state_name, child_district AS a, parent_district AS b
    FROM staging.district_lineage WHERE cluster_edge = 'Y'
    UNION
    SELECT state AS state_name, parent_district AS a, child_district AS b
    FROM staging.district_lineage WHERE cluster_edge = 'Y'
),
propagate AS (
    SELECT state_name, district_name AS node, district_name AS label
    FROM staging._staging_districts
    UNION
    SELECT p.state_name, e.b, p.label
    FROM propagate p
    JOIN edges e ON e.state_name = p.state_name AND e.a = p.node
)
SELECT state_name, node AS district_name, MIN(label) AS unit_seed
FROM propagate
GROUP BY state_name, node;

-- unit_key is deterministic: state + the alphabetically first member
-- (unit_seed). unit_label lists the members that are NOT a split/split_and_-
-- rename child - i.e. the pre-split identities the unit is made of - rather
-- than members reporting in 2013-14: a coverage-gap district (Raigarh) can
-- be absent from 2013-14 yet still be an original district, and a unit can
-- join through a fragment (Sarangarh-Bilaigarh) that itself IS a split
-- child, which must not stand in for the original it was split from.
-- parent_transfer/cross_state_transfer children are kept: those describe a
-- boundary change to a district that already existed (Mahabubnagar, East
-- Godavari, Sri Potti Sriramulu Nellore), never that district's origin.
--
-- Fallback: a unit can end up with NO pre-split member at all - Chennai is a
-- split child of both Thiruvallur and Kancheepuram, but both edges are
-- cluster_edge = 'N' (2026-09-23 materiality review: the transfer is real
-- but immaterial, see Edge rules), so Chennai clusters with nobody and is
-- its own unit, wholly made of a name the primary rule excludes. Rather than
-- leave such a unit unlabelled, fall back to all of its members' own names.
DROP TABLE IF EXISTS staging._unit_label;
CREATE TABLE staging._unit_label AS
SELECT
    dc.state_name,
    dc.unit_seed,
    COALESCE(
        string_agg(dc.district_name, ' + ' ORDER BY dc.district_name) FILTER (
            WHERE NOT EXISTS (
                SELECT 1 FROM staging.district_lineage dl
                WHERE dl.state = dc.state_name AND dl.child_district = dc.district_name
                  AND dl.event IN ('split', 'split_and_rename'))
        ),
        string_agg(dc.district_name, ' + ' ORDER BY dc.district_name)
    ) AS unit_label
FROM staging._district_component dc
GROUP BY dc.state_name, dc.unit_seed;

-- unit_label must never be NULL or empty: every unit needs at least one
-- pre-split member, by construction (a chain of splits always bottoms out
-- at an original district). Empty here means the label rule has a bug.
DROP TABLE IF EXISTS staging.chk_unit_label_missing;
CREATE TABLE staging.chk_unit_label_missing AS
SELECT state_name, unit_seed
FROM staging._unit_label
WHERE unit_label IS NULL OR unit_label = '';

DROP TABLE IF EXISTS staging.district_alias;
CREATE TABLE staging.district_alias AS
SELECT
    sd.state_name,
    sd.district_name,
    sd.state_name || ':' || sd.district_name AS district_key,
    sd.state_name || ':' || dc.unit_seed AS unit_key,
    ul.unit_label,
    yr.first_year,
    yr.last_year,
    -- LIMIT 1 is safe, not arbitrary: a child with several parent rows (for
    -- example Siddipet, carved from three districts) always has the same
    -- event on every one of its rows - checked directly against the seed
    -- before this file was written. parent_transfer and cross_state_transfer
    -- are excluded on purpose: they describe a boundary change to a district
    -- that already existed, never that district's own origin.
    (SELECT dl.event FROM staging.district_lineage dl
     WHERE dl.state = sd.state_name AND dl.child_district = sd.district_name
       AND dl.event IN ('split', 'split_and_rename', 'coverage_gap')
     LIMIT 1) AS lineage_event,
    EXISTS (
        SELECT 1 FROM staging.district_lineage dl
        WHERE dl.state = sd.state_name AND dl.child_district = sd.district_name
          AND dl.event = 'coverage_gap'
    ) AS is_coverage_gap
FROM staging._staging_districts sd
JOIN staging._district_component dc
  ON dc.state_name = sd.state_name AND dc.district_name = sd.district_name
JOIN staging._unit_label ul
  ON ul.state_name = dc.state_name AND ul.unit_seed = dc.unit_seed
JOIN (
    SELECT state_name, district_name,
           MIN(year_start) AS first_year, MAX(year_start) AS last_year
    FROM staging.stg_crop_production
    GROUP BY state_name, district_name
) yr ON yr.state_name = sd.state_name AND yr.district_name = sd.district_name;

-- Every staging (state, district) must appear in district_alias exactly
-- once: both tables should be empty.
DROP TABLE IF EXISTS staging.chk_alias_missing;
CREATE TABLE staging.chk_alias_missing AS
SELECT sd.state_name, sd.district_name
FROM staging._staging_districts sd
WHERE NOT EXISTS (
    SELECT 1 FROM staging.district_alias da
    WHERE da.state_name = sd.state_name AND da.district_name = sd.district_name);

DROP TABLE IF EXISTS staging.chk_alias_duplicate;
CREATE TABLE staging.chk_alias_duplicate AS
SELECT state_name, district_name, count(*) AS n
FROM staging.district_alias
GROUP BY state_name, district_name
HAVING count(*) > 1;

-- A district whose first year is after 2013-14 must have an origin lineage
-- row. If a genuinely new name shows up in a future UPAg export with no
-- matching seed row, it lands here and fails the run.
DROP TABLE IF EXISTS staging.chk_new_district_lineage;
CREATE TABLE staging.chk_new_district_lineage AS
SELECT state_name, district_name, first_year
FROM staging.district_alias
WHERE first_year > 2013 AND lineage_event IS NULL;

-- The area total per state per year must be identical whether summed by
-- district or by unit - grouping into units must not lose or add hectares.
-- Aggregated to the unit first, then to state-year, to match "by unit"
-- literally rather than just re-deriving the by-district total.
DROP TABLE IF EXISTS staging.chk_area_reconciliation;
CREATE TABLE staging.chk_area_reconciliation AS
WITH by_district AS (
    SELECT state_name, year_label, sum(area_ha) AS area_ha
    FROM staging.stg_crop_production
    GROUP BY state_name, year_label
),
by_unit_detail AS (
    SELECT da.state_name, sp.year_label, da.unit_key, sum(sp.area_ha) AS area_ha
    FROM staging.stg_crop_production sp
    JOIN staging.district_alias da
      ON da.state_name = sp.state_name AND da.district_name = sp.district_name
    GROUP BY da.state_name, sp.year_label, da.unit_key
),
by_unit AS (
    SELECT state_name, year_label, sum(area_ha) AS area_ha
    FROM by_unit_detail
    GROUP BY state_name, year_label
)
SELECT bd.state_name, bd.year_label,
       bd.area_ha AS by_district_ha, bu.area_ha AS by_unit_ha,
       bd.area_ha - bu.area_ha AS diff_ha
FROM by_district bd
JOIN by_unit bu ON bu.state_name = bd.state_name AND bu.year_label = bd.year_label
WHERE bd.area_ha IS DISTINCT FROM bu.area_ha;

-- Informational only: printed, never fails the run.
DROP TABLE IF EXISTS staging.chk_units_per_state;
CREATE TABLE staging.chk_units_per_state AS
SELECT state_name, count(DISTINCT unit_key) AS n_units, count(*) AS n_names
FROM staging.district_alias
GROUP BY state_name
ORDER BY state_name;
