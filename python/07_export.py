"""
Stage 07 - export: the marts as files, for a Power BI report on a machine
with no database.

What:   Runs every query in sql/06_export.sql (one per marts object, split
        on its "-- ===== EXPORT: <name> =====" markers) and writes each
        result to output/marts/<name>.parquet with an explicit pyarrow
        schema (snappy compression), plus output/marts/<name>.csv for the
        five dimensions and the ranking (top_districts). Everything is
        written to a staging directory first; only once every check in the
        module docstring's Checks section passes are the files moved into
        output/marts/, so a failed run leaves the committed export
        untouched.
Checks: every Parquet file's row count, read back from disk, equals
        `SELECT count(*)` on its source marts object (and its CSV row
        count, where one exists); for fct_crop_area and mv_spray_demand,
        every acre-named column's sum in the Parquet file matches the same
        sum in SQL within a relative tolerance of 1e-9; top_districts.parquet
        has 1,886 rows, a pp_spray_acres_base total that rounds to
        683,825,836, and its rank_national = 1 row is Yavatmal, Kharif; no
        file exceeds 50 MB.
Reads:  marts.* (see sql/06_export.sql), sql/06_export.sql
Writes: output/marts/*.parquet, output/marts/*.csv (dims and top_districts
        only)
Re-run: safe. Deterministic: the same marts data produces byte-identical
        files (fixed column order, ORDER BY the grain key in every query,
        explicit schema), so a re-run with no marts change leaves
        output/marts/ unchanged on disk.
"""

import argparse
import csv
import math
import re
import shutil
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from db import connect

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = REPO_ROOT / "sql" / "06_export.sql"
MARTS_DIR = REPO_ROOT / "output" / "marts"
STAGING_DIR = REPO_ROOT / "output" / ".marts_staging"
EXPORT_MARKER_RE = re.compile(r"-- ===== EXPORT: (\w+) =====\n")
MAX_FILE_BYTES = 50 * 1024 * 1024
ACRE_SUM_REL_TOL = 1e-9

# (export name, source object to check row counts/sums against, write a CSV, schema)
EXPORTS = [
    ("dim_crop", "marts.dim_crop", True, pa.schema([
        ("crop_key", pa.string()), ("crop", pa.string()), ("in_scope", pa.bool_()),
        ("pp_passes_low", pa.float64()), ("pp_passes_base", pa.float64()),
        ("pp_passes_high", pa.float64()), ("basis", pa.string()),
        ("source_grade", pa.string()), ("needs_check", pa.bool_()),
    ])),
    ("dim_crop_state_passes", "marts.dim_crop_state_passes", True, pa.schema([
        ("state", pa.string()), ("crop_key", pa.string()),
        ("pp_passes_low", pa.float64()), ("pp_passes_base", pa.float64()),
        ("pp_passes_high", pa.float64()), ("n_farmers", pa.int32()),
        ("source_grade", pa.string()),
    ])),
    ("dim_district", "marts.dim_district", True, pa.schema([
        ("district_key", pa.string()), ("state", pa.string()),
        ("district_name", pa.string()), ("unit_key", pa.string()),
        ("unit_label", pa.string()), ("first_year", pa.int32()),
        ("last_year", pa.int32()), ("is_coverage_gap", pa.bool_()),
    ])),
    ("dim_season", "marts.dim_season", True, pa.schema([
        ("season_key", pa.string()), ("season", pa.string()),
        ("start_month", pa.int32()), ("end_month", pa.int32()),
    ])),
    ("dim_year", "marts.dim_year", True, pa.schema([
        ("year_label", pa.string()), ("start_year", pa.int32()),
        ("is_complete", pa.bool_()), ("in_trend_window", pa.bool_()),
    ])),
    ("fct_crop_area", "marts.fct_crop_area", False, pa.schema([
        ("district_key", pa.string()), ("year_label", pa.string()),
        ("season_key", pa.string()), ("crop_key", pa.string()),
        ("area_ha", pa.float64()), ("area_acres", pa.float64()),
    ])),
    ("mv_spray_demand", "marts.mv_spray_demand", False, pa.schema([
        ("district_key", pa.string()), ("year_label", pa.string()),
        ("season_key", pa.string()), ("total_acres", pa.float64()),
        ("in_scope_acres", pa.float64()), ("unsourced_acres", pa.float64()),
        ("not_modelled_acres", pa.float64()), ("pp_spray_acres_low", pa.float64()),
        ("pp_spray_acres_base", pa.float64()), ("pp_spray_acres_high", pa.float64()),
        ("nutrient_spray_acres", pa.float64()),
    ])),
    ("rpt_crop_base", "marts.rpt_crop_base", False, pa.schema([
        ("district_key", pa.string()), ("season", pa.string()),
        ("crop_key", pa.string()), ("needs_check", pa.bool_()),
        ("crop_base_acres", pa.float64()),
    ])),
    ("rpt_state_rollup", "marts.rpt_state_rollup", False, pa.schema([
        ("level", pa.string()), ("state", pa.string()), ("district_name", pa.string()),
        ("pp_spray_acres_base", pa.float64()), ("n_district_seasons", pa.int64()),
        ("n_decile_1", pa.int64()),
    ])),
    ("rpt_unit_trend", "marts.rpt_unit_trend", False, pa.schema([
        ("unit_key", pa.string()), ("year_label", pa.string()), ("start_year", pa.int32()),
        ("unit_in_scope_acres", pa.float64()), ("yoy_change_pct", pa.float64()),
        ("ma3_acres", pa.float64()),
    ])),
    ("top_districts", "marts.rpt_top_districts", True, pa.schema([
        ("district_key", pa.string()), ("district_name", pa.string()),
        ("state", pa.string()), ("season", pa.string()),
        ("sprayable_acres", pa.float64()), ("pp_spray_acres_low", pa.float64()),
        ("pp_spray_acres_base", pa.float64()), ("pp_spray_acres_high", pa.float64()),
        ("rank_national", pa.int64()), ("rank_national_low", pa.int64()),
        ("rank_national_high", pa.int64()), ("rank_in_state", pa.int64()),
        ("decile", pa.int32()), ("top_crop_1", pa.string()),
        ("top_crop_1_share_pct", pa.float64()), ("top_crop_2", pa.string()),
        ("top_crop_2_share_pct", pa.float64()), ("top_crop_3", pa.string()),
        ("top_crop_3_share_pct", pa.float64()), ("open_crop_share_pct", pa.float64()),
        ("unit_key", pa.string()), ("unit_district_count", pa.int64()),
        ("unit_yoy_area_change_pct", pa.float64()), ("unit_area_ma3_acres", pa.float64()),
    ])),
]
# Exact target for the ranking, independent of the marts row-count/sum checks
TOP_DISTRICTS_EXPECTED_ROWS = 1_886
TOP_DISTRICTS_EXPECTED_BASE_TOTAL = 683_825_836
TOP_DISTRICTS_EXPECTED_RANK_1 = ("Yavatmal", "Kharif")
# -----------------------------------------------------------------------------


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args()


def load_export_queries(sql_text: str) -> dict:
    parts = EXPORT_MARKER_RE.split(sql_text)
    # split() on a capturing group returns [preamble, name1, sql1, name2, sql2, ...]
    if len(parts) < 3:
        fail(f"{SQL_PATH.relative_to(REPO_ROOT)} has no '-- ===== EXPORT: <name> =====' markers")
    queries = {}
    for name, sql in zip(parts[1::2], parts[2::2]):
        queries[name] = sql.strip().rstrip(";")
    return queries


def rows_to_table(rows: list, schema: "pa.Schema") -> "pa.Table":
    columns = list(zip(*rows)) if rows else [[] for _ in schema]
    arrays = [pa.array(col, type=field.type) for col, field in zip(columns, schema)]
    return pa.table(arrays, schema=schema)


def write_csv(path: Path, schema: "pa.Schema", rows: list) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(schema.names)
        writer.writerows(rows)


def main() -> None:
    parse_args()

    if not SQL_PATH.exists():
        fail(f"{SQL_PATH.relative_to(REPO_ROOT)} is missing")

    queries = load_export_queries(SQL_PATH.read_text(encoding="utf-8"))
    missing = [name for name, *_ in EXPORTS if name not in queries]
    if missing:
        fail(f"{SQL_PATH.relative_to(REPO_ROOT)} has no EXPORT block for: {missing}")

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True)

    con = connect()
    cur = con.cursor()

    # ---- run every export query and write to the staging directory ----
    tables = {}
    for name, _source, want_csv, schema in EXPORTS:
        cur.execute(queries[name])
        rows = cur.fetchall()
        table = rows_to_table(rows, schema)
        tables[name] = table
        pq.write_table(table, STAGING_DIR / f"{name}.parquet", compression="snappy")
        if want_csv:
            write_csv(STAGING_DIR / f"{name}.csv", schema, rows)

    # ---- checks ----
    report_rows = []
    for name, source, want_csv, _schema in EXPORTS:
        cur.execute(f"SELECT count(*) FROM {source}")
        marts_rows = cur.fetchone()[0]
        parquet_path = STAGING_DIR / f"{name}.parquet"
        parquet_rows = pq.ParquetFile(parquet_path).metadata.num_rows
        parquet_bytes = parquet_path.stat().st_size
        csv_rows = None
        csv_bytes = 0
        if want_csv:
            csv_path = STAGING_DIR / f"{name}.csv"
            with csv_path.open(encoding="utf-8") as fh:
                csv_rows = sum(1 for _ in fh) - 1
            csv_bytes = csv_path.stat().st_size

        ok = marts_rows == parquet_rows and (csv_rows is None or csv_rows == marts_rows)
        status = "OK" if ok else "MISMATCH"
        report_rows.append((name, marts_rows, parquet_rows, csv_rows, parquet_bytes + csv_bytes, status))
        if not ok:
            fail(f"{name}: marts rows {marts_rows}, parquet rows {parquet_rows}, csv rows {csv_rows}")

        for path, size in ((parquet_path, parquet_bytes),
                           (STAGING_DIR / f"{name}.csv" if want_csv else None, csv_bytes)):
            if path is not None and size > MAX_FILE_BYTES:
                fail(f"{path.name} is {size:,} bytes, over the {MAX_FILE_BYTES:,} byte limit")

    print(f"{'object':<24}{'marts rows':>12}{'parquet rows':>14}{'csv rows':>10}{'bytes':>12}  status")
    for name, marts_rows, parquet_rows, csv_rows, total_bytes, status in report_rows:
        csv_display = f"{csv_rows:,}" if csv_rows is not None else "-"
        print(f"{name:<24}{marts_rows:>12,}{parquet_rows:>14,}{csv_display:>10}{total_bytes:>12,}  {status}")

    # ---- acre-column sum checks: fct_crop_area, mv_spray_demand ----
    print("\nAcre-column sum checks (Parquet vs SQL, relative tolerance 1e-9):")
    for name, source, _want_csv, schema in EXPORTS:
        if name not in ("fct_crop_area", "mv_spray_demand"):
            continue
        acre_cols = [f.name for f in schema if "acres" in f.name]
        cur.execute(f"SELECT {', '.join(f'sum({c})' for c in acre_cols)} FROM {source}")
        sql_sums = cur.fetchone()
        table = tables[name]
        for col, sql_sum in zip(acre_cols, sql_sums):
            parquet_sum = pc.sum(table.column(col)).as_py()
            close = math.isclose(float(sql_sum), parquet_sum, rel_tol=ACRE_SUM_REL_TOL)
            print(f"  {name}.{col:<24}sql {float(sql_sum):>18,.2f}  parquet {parquet_sum:>18,.2f}  "
                  f"{'OK' if close else 'MISMATCH'}")
            if not close:
                fail(f"{name}.{col} sum disagrees beyond {ACRE_SUM_REL_TOL} relative tolerance: "
                     f"sql {sql_sum} vs parquet {parquet_sum}")

    # ---- top_districts.parquet target checks ----
    top = tables["top_districts"]
    n_top = top.num_rows
    base_total = round(pc.sum(top.column("pp_spray_acres_base")).as_py())
    rank1 = top.filter(pc.equal(top.column("rank_national"), 1))
    rank1_district = (rank1.column("district_name")[0].as_py(), rank1.column("season")[0].as_py())
    print(f"\ntop_districts.parquet: {n_top:,} rows, base total {base_total:,}, "
          f"rank 1 = {rank1_district[0]}, {rank1_district[1]}")
    if n_top != TOP_DISTRICTS_EXPECTED_ROWS:
        fail(f"top_districts.parquet has {n_top} rows, expected {TOP_DISTRICTS_EXPECTED_ROWS}")
    if base_total != TOP_DISTRICTS_EXPECTED_BASE_TOTAL:
        fail(f"top_districts.parquet base total rounds to {base_total}, "
             f"expected {TOP_DISTRICTS_EXPECTED_BASE_TOTAL}")
    if rank1.num_rows != 1 or rank1_district != TOP_DISTRICTS_EXPECTED_RANK_1:
        fail(f"top_districts.parquet rank 1 is {rank1_district}, expected {TOP_DISTRICTS_EXPECTED_RANK_1}")

    # ---- schemas ----
    print("\nParquet schemas:")
    for name, _source, _want_csv, schema in EXPORTS:
        print(f"  {name}:")
        for field in schema:
            print(f"    {field.name}: {field.type}")

    con.close()

    # ---- every check passed: swap the staged files in as output/marts/,
    #      replacing it wholesale so no file from an earlier export version
    #      can linger ----
    if MARTS_DIR.exists():
        shutil.rmtree(MARTS_DIR)
    STAGING_DIR.rename(MARTS_DIR)

    print(f"\nWrote {len(EXPORTS)} objects to {MARTS_DIR.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
