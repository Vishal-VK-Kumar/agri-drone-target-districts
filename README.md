# agri-drone-target-districts

With one agricultural drone and one spray season, which Indian districts would you serve
first? This repo ranks 1,886 district-seasons by spraying demand, built in PostgreSQL from
government crop statistics.

**In 2023-24, half of India's modelled crop-spraying demand sat in one tenth of
district-seasons. The top 50 are all Kharif, and most of them are soybean and cotton
districts in Maharashtra and western Madhya Pradesh.**

| | Share of national demand | Share of district-seasons |
|---|---|---|
| Top 10 | 6.2% | 0.5% |
| Top 50 | 21.9% | 2.7% |
| Top decile (189) | 49.6% | 10.0% |

Spray demand is the sown acres of each in-scope crop multiplied by how many times that
crop is sprayed in a season, summed by district and season (acre-passes).

**Source:** UPAg (Unified Portal for Agricultural Statistics, Ministry of Agriculture &
Farmers Welfare), Complete APY Dataset (District Level). The series runs 2013-14 to
2024-25; the latest complete year is 2023-24, since 2024-25 is missing several states'
reports. Downloaded 2026-09-23. Field crops only; horticulture is not covered.

The full finding, with the crop mix, the stability check and what the ranking does not
answer, is in [`output/finding.md`](output/finding.md).

## Reproduce

Prerequisites: Docker, Python 3.11+, and make.

1. Export the data by hand; the report has no public API.
   - Open UPAg: Reports → Area, Production & Yield → Complete APY Dataset (District
     Level), <https://upag.gov.in/dash-reports/desdistrictwisecompletedatasetreport>.
   - Export settings: UOM = Actual; Crop Category = Food Grains, Commercial Crops and
     Oilseeds; Crop = All; From Year = earliest offered; To Year = 2024-25; Metric =
     Area, Production, Yield; Apply; CSV.
   - The report caps each export at three years, so export four files: 2013-14 to
     2015-16, 2016-17 to 2018-19, 2019-20 to 2021-22, 2022-23 to 2024-25. Keep the file
     names UPAg gives them (`DES-District-Data-For-<from>-to-<to>.csv`) and put all
     four in `data/raw/`.
2. Run the pipeline:

   ```bash
   make up
   make analyse
   make export
   ```

Each make target runs the stage before it (manifest, load, stage, alias, marts,
analyse), so `make analyse` rebuilds everything from the four raw CSVs and writes
`output/top_districts.csv` and `output/state_rollup.csv`. `make up` starts PostgreSQL
16 in Docker. Quality checks written in SQL run inside the build and stop it on any
mismatch in row counts, keys or totals.

`make export` writes the marts to `output/marts/` as Parquet (CSV as well for the
dimension tables and the ranking). It fails if any file's row count differs from its
source table.

## Method

**Stable units.** 752 raw district names collapse to 629 stable units by joining
districts that split from a common parent (`seeds/district_lineage.csv`). Ranking uses
current district names; trend measures (year-on-year change, the moving average) use
the stable unit, so a split district is never mistaken for a collapse. See
`docs/district_alias_notes.md`.

**Seasons.** The source relabels seasons before 2023-24 (eastern winter rice moves from
Rabi to Kharif in several states), so year-on-year and moving-average measures run on
all-season totals only. Season is used within the 2023-24 ranking year, where the
labels match the agronomy. See `docs/spray_passes_notes.md`, Seasons.

**Spray passes.** Each in-scope crop carries a low, base and high spray-pass count with
a source grade (`seeds/crop_spray_passes.csv`, documented row by row in
`docs/spray_passes_notes.md`). Rice uses state-level figures for the eight CSISA survey
states (`seeds/crop_state_spray_passes.csv`, 65% of national rice area) and the
national figure elsewhere.

## Open items

- Spray-pass counts for maize, gram and moong are open (`needs_check = 'Y'`). No
  Indian survey with a spray count was found for any of the three; reasons are in
  `docs/spray_passes_notes.md`.
- The eight Rajasthan districts and Mauganj first report in 2024-25, so their lineage
  is unresolved. They touch neither the 2023-24 ranking year nor the trend window; no
  analysis in this repo uses 2024-25. See `docs/district_alias_notes.md`, Open items.
- Two Telangana districts (Jayashankar Bhupalapally, Hanumakonda) have documentation-
  only open lineage rows: no effect on units, since the districts are already joined
  through other edges.
- A 2025-26 export will fail the lineage check by design, once Andhra Pradesh's new
  districts appear in the data.

## Next

A Power BI model that asks whether one drone pays in a top-ranked district. It reads
`output/marts/`, so it opens from a clean clone with no database running. Not built yet.

## Repo layout

- `config/`: assumptions every marts number reads, instead of a literal in a query.
- `data/`: `data/raw/` holds the manually exported UPAg CSVs (not committed) and their
  manifest.
- `docs/`: the reasoning behind district lineage and spray passes, row by row.
- `output/`: pipeline results. `finding.md`, `split_artefact_demo.txt` and `marts/` (the
  Parquet interface to the Power BI report) are committed; everything else is
  regenerated.
- `python/`: one script per pipeline stage, run through the Makefile.
- `seeds/`: reference data joined against the source: crop names, spray-pass counts,
  the season calendar, district lineage.
- `sql/`: one file per pipeline stage, run by its matching Python script.

## Licence

MIT, see `LICENSE`. The source data belongs to its publisher and is not included.
