# agri-drone-target-districts

> Work in progress. The finding will lead this page once the analysis stage is
> built. So far the pipeline goes as far as staging and district lineage.

Which Indian districts should a one-drone, one-pilot spraying operator serve?
This repo answers that with SQL over public district crop statistics. Sown area
alone ranks them wrongly, because spray passes per crop and the length of each
season's spray window matter more than raw acreage.

**Source:** UPAg (Unified Portal for Agricultural Statistics, Ministry of
Agriculture & Farmers Welfare), Complete APY Dataset (District Level),
2013-14 to 2024-25, exported 2026-09-23. 2024-25 is incomplete (several states
have not reported it), so the latest complete year is 2023-24.

## Getting the data

The report is a Dash app with no public API, so the data is exported by hand
and not redistributed in this repo.

1. Open UPAg: Reports → Area, Production & Yield → Complete APY Dataset
   (District Level),
   <https://upag.gov.in/dash-reports/desdistrictwisecompletedatasetreport>.
2. Export settings: UOM = Actual; Crop Category = Food Grains, Commercial Crops
   and Oilseeds; Crop = All; From Year = earliest offered; To Year = 2024-25;
   Metric = Area, Production, Yield; Apply; CSV.
3. The report caps each export at three years (From to To), so export four
   files: 2013-14 to 2015-16, 2016-17 to 2018-19, 2019-20 to 2021-22,
   2022-23 to 2024-25. Keep the file names UPAg gives them
   (`DES-District-Data-For-<from>-to-<to>.csv`) and put all four in `data/raw/`.

## Reproducing

Requires Python 3.11+ and either Docker or nothing else (DuckDB fallback).

```bash
docker compose up -d
make load
```

Without Docker, the same SQL runs on a local DuckDB file:

```bash
make load DB_BACKEND=duckdb
```

| Stage | Script | Reads | Writes |
|---|---|---|---|
| 01 | `python/01_download.py` | `data/raw/*.csv` | `data/raw/MANIFEST.json` (checksums, sizes, row counts; fails if files changed) |
| 02 | `python/02_load.py`, `sql/01_load_raw.sql` | `data/raw/*.csv` | `raw.crop_production_import`, all text, unaltered |
| 03 | `python/03_stage.py`, `sql/02_stage.sql` | `raw.crop_production_import`, `seeds/*.csv` | `staging.stg_crop_production` (long format, one row per state x district x crop x season x year), plus `staging.chk_season_total` / `staging.chk_crop_group` / `staging.rej_empty_year` for the rows taken out |
| 04 | `python/04_district_lineage.py`, `sql/03_district_lineage.sql` | `staging.stg_crop_production`, `seeds/district_lineage.csv` | `staging.district_alias` (one row per state x district, with a stable `unit_key` for districts joined by a split) - see `docs/district_alias_notes.md` for why a district-name series isn't safe to trend on its own |

`make stage` runs `make load` first, then `python/03_stage.py`.
`make alias` runs `make stage` first, then `python/04_district_lineage.py`.
`make demo` runs `make alias` first, then `python/run_split_artefact_demo.py`, which
writes `output/split_artefact_demo.txt` - before/after evidence that a stable
unit removes the fake year-on-year collapse a raw district name shows at a
split.

## Licence

MIT, see `LICENSE`. The source data belongs to its publisher and is not
included.
