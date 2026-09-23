-- Stage 02 DDL - raw landing table for the UPAg district APY exports.
-- Reads:  nothing; python/02_load.py runs this, then bulk-loads the CSVs.
-- Writes: raw.crop_production_import (dropped and rebuilt).
-- Re-run: safe. Raw is rebuilt from the untouched files in data/raw/ and is
--         read-only to every later stage.
-- Portable: plain SQL that runs unchanged on PostgreSQL 16 and DuckDB.

CREATE SCHEMA IF NOT EXISTS raw;

DROP TABLE IF EXISTS raw.crop_production_import;

-- The exports are wide, with the year in the column name (Area-2013-14 ...),
-- and each file covers a different three years. To union them into one table
-- without reshaping the data, each file's three year labels are stored as
-- values (year_1..year_3) and the measures go into positional slots. Unpivoting
-- to one row per year is a transformation, so it waits for staging.
-- Everything from the file is text, exactly as exported: names untrimmed,
-- missing years kept as empty strings, not NULL.
CREATE TABLE raw.crop_production_import (
    source_file        text    NOT NULL,
    source_row         integer NOT NULL,  -- data row number within the file, header excluded
    year_1             text    NOT NULL,
    year_2             text    NOT NULL,
    year_3             text    NOT NULL,
    state              text    NOT NULL,
    district           text    NOT NULL,
    crop               text    NOT NULL,
    season             text    NOT NULL,
    area_year_1        text    NOT NULL,
    area_year_2        text    NOT NULL,
    area_year_3        text    NOT NULL,
    production_year_1  text    NOT NULL,
    production_year_2  text    NOT NULL,
    production_year_3  text    NOT NULL,
    yield_year_1       text    NOT NULL,
    yield_year_2       text    NOT NULL,
    yield_year_3       text    NOT NULL,
    PRIMARY KEY (source_file, source_row)
);
