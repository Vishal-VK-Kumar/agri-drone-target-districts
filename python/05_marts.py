"""
Stage 05 - marts: dimensions, the area fact, and spray demand.

What:   Loads seeds/crop_spray_passes.csv, seeds/season_calendar.csv and
        seeds/assumptions.csv, validates the spray-passes seed, and builds
        marts.dim_district / dim_crop / dim_season / dim_year,
        marts.fct_crop_area (grain district x year x season x crop, one row
        per staging row with a non-null area) and marts.mv_spray_demand (a
        materialised view of spray acres at district x year x season). Runs
        the checks in the module docstring's Checks section below. See
        sql/04_marts.sql's header for the full table list.
Checks: every staging crop is in the seed and vice versa; low <= base <=
        high; a pass count is null only when basis is unsourced or
        not_modelled; an in-scope row has a source unless unsourced; fact row
        count and area match staging exactly; the fact grain is unique and
        every foreign key resolves; no negative area (zero-area rows are
        printed, not failed); the view's total/in-scope/not-modelled acres
        agree and its total matches the fact; pp_spray_acres_base agrees with
        an independent recomputation from the fact and dim_crop.
Reads:  staging.stg_crop_production, staging.district_alias,
        sql/04_marts.sql, seeds/crop_spray_passes.csv,
        seeds/season_calendar.csv, seeds/assumptions.csv
Writes: staging.crop_spray_passes, staging.season_calendar,
        staging.assumptions, marts.* (see sql/04_marts.sql)
Re-run: safe. The whole build runs in one transaction and is committed only
        after every check passes, so a failed run leaves nothing behind that
        a later stage could read. Pass --keep-on-fail to commit anyway for
        debugging; the Makefile never passes it.
"""

import argparse
import sys
from pathlib import Path

from db import backend, connect

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
DDL_PATH = REPO_ROOT / "sql" / "04_marts.sql"
SEED_PATHS = {
    "staging.crop_spray_passes": REPO_ROOT / "seeds" / "crop_spray_passes.csv",
    "staging.season_calendar": REPO_ROOT / "seeds" / "season_calendar.csv",
    "staging.assumptions": REPO_ROOT / "seeds" / "assumptions.csv",
}
SEED_SPLIT_MARKER = "-- ===== SEEDS LOADED HERE =====\n"
# The row-count table this stage prints, in pipeline order
ROW_COUNT_TABLES = [
    ("raw.crop_production_import", "raw rows"),
    ("staging.stg_crop_production", "staging rows (all crops)"),
    ("staging.district_alias", "district_alias rows"),
    ("marts.dim_district", "dim_district rows"),
    ("marts.dim_crop", "dim_crop rows"),
    ("marts.dim_season", "dim_season rows"),
    ("marts.dim_year", "dim_year rows"),
    ("marts.fct_crop_area", "fct_crop_area rows"),
    ("marts.mv_spray_demand", "mv_spray_demand rows"),
]
LATEST_COMPLETE_YEAR_LABEL = "2023-24"  # only for the printed share breakdown; not used in any check
# -----------------------------------------------------------------------------


def fail(message: str) -> None:
    """Pre-connection failures only - nothing to roll back yet."""
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def copy_csv(cur, path: Path, table: str) -> None:
    if backend() == "duckdb":
        cur.execute(f"COPY {table} FROM '{path.as_posix()}' (FORMAT csv, HEADER true)")
    else:
        with path.open(encoding="utf-8") as fh:
            cur.copy_expert(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)", fh)


def row_count(cur, table: str) -> int:
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-on-fail", action="store_true",
        help="commit the build even if a check fails, so the marts can be "
             "inspected. Off by default: a failed run commits nothing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not DDL_PATH.exists():
        fail(f"{DDL_PATH.relative_to(REPO_ROOT)} is missing")
    for table, path in SEED_PATHS.items():
        if not path.exists():
            fail(f"{path.relative_to(REPO_ROOT)} is missing")

    ddl = DDL_PATH.read_text(encoding="utf-8")
    if SEED_SPLIT_MARKER not in ddl:
        fail(f"{DDL_PATH.relative_to(REPO_ROOT)} is missing its seed-split marker")
    seed_ddl, transform_sql = ddl.split(SEED_SPLIT_MARKER, 1)

    con = connect()
    cur = con.cursor()

    def fail_build(message: str) -> None:
        print(f"FAIL: {message}", file=sys.stderr)
        if args.keep_on_fail:
            con.commit()
            print("--keep-on-fail: the tables from this FAILED build were "
                  "committed anyway, for inspection. Re-run without the flag "
                  "before trusting anything downstream of them.", file=sys.stderr)
        else:
            con.rollback()
        con.close()
        sys.exit(1)

    # ---- seed tables: DDL, then load, before the transform can join on them
    cur.execute(seed_ddl)
    for table, path in SEED_PATHS.items():
        copy_csv(cur, path, table)

    # ---- transform: validate the seed, build the marts, run the checks
    try:
        cur.execute(transform_sql)
    except Exception as exc:
        fail_build(f"unexpected database error while building the marts: {exc}")

    # ---- seed validation (task step 1) ----
    checks = [
        ("staging.chk_spray_crop_mismatch", "crop, problem",
         "crop(s) mismatched between staging and the spray-passes seed"),
        ("staging.chk_spray_passes_range", "crop, pp_passes_low, pp_passes_base, pp_passes_high",
         "crop(s) with low <= base <= high violated"),
        ("staging.chk_spray_passes_null", "crop, basis",
         "crop(s) with a null pass count and a basis that promises one"),
        ("staging.chk_spray_missing_source", "crop, basis",
         "in-scope crop(s) with no source_url and a basis other than unsourced"),
    ]
    for table, cols, description in checks:
        cur.execute(f"SELECT {cols} FROM {table}")
        rows = cur.fetchall()
        if rows:
            fail_build(f"{len(rows)} {description}: {rows[:10]}")

    # ---- fact checks ----
    cur.execute("SELECT staging_rows_with_area, fact_rows FROM staging.chk_fact_row_count")
    staging_rows_with_area, fact_rows = cur.fetchone()
    if staging_rows_with_area != fact_rows:
        fail_build(f"staging rows with area ({staging_rows_with_area:,}) != fact rows ({fact_rows:,})")

    cur.execute("SELECT state_name, year_label, staging_ha, fact_ha, diff_ha "
                "FROM staging.chk_fact_area_reconciliation ORDER BY abs(diff_ha) DESC LIMIT 10")
    area_mismatches = cur.fetchall()
    if area_mismatches:
        for row in area_mismatches:
            print("   ", row)
        fail_build(f"{len(area_mismatches)}+ state-year area totals disagree between staging and the fact")

    cur.execute("SELECT count(*) FROM staging.chk_fact_grain_duplicate")
    n = cur.fetchone()[0]
    if n:
        fail_build(f"{n} duplicate fact grain(s) (district_key, year_label, season_key, crop_key)")

    cur.execute("SELECT bad_fk, count(*) FROM staging.chk_fact_orphan_fk GROUP BY bad_fk")
    orphans = cur.fetchall()
    if orphans:
        fail_build(f"fact rows with a foreign key that resolves to no dimension row: {orphans}")

    cur.execute("SELECT count(*) FROM staging.chk_fact_negative_area")
    n_negative = cur.fetchone()[0]
    if n_negative:
        fail_build(f"{n_negative} fact row(s) have negative area_ha")

    cur.execute("SELECT count(*) FROM marts.fct_crop_area WHERE area_ha = 0")
    n_zero = cur.fetchone()[0]
    print(f"Zero-area fact rows (printed, not failed): {n_zero}")

    # ---- spray-demand view checks ----
    cur.execute("SELECT count(*) FROM staging.chk_mv_scope_identity")
    n = cur.fetchone()[0]
    if n:
        fail_build(f"{n} mv_spray_demand row(s) where total_acres != in_scope_acres + not_modelled_acres")

    cur.execute("SELECT state, year_label, mv_acres, fact_acres, diff_acres "
                "FROM staging.chk_mv_vs_fact_area ORDER BY abs(diff_acres) DESC LIMIT 10")
    mv_mismatches = cur.fetchall()
    if mv_mismatches:
        for row in mv_mismatches:
            print("   ", row)
        fail_build(f"{len(mv_mismatches)}+ state-year acre totals disagree between mv_spray_demand and the fact")

    cur.execute("SELECT count(*) FROM staging.chk_mv_pp_base_recompute")
    n = cur.fetchone()[0]
    if n:
        fail_build(f"{n} mv_spray_demand row(s) where pp_spray_acres_base disagrees with an "
                   "independent recomputation from the fact and dim_crop")

    # Every check passed: commit the whole build now, for the first time.
    con.commit()

    # ---- row-count table, raw through marts ----
    print(f"\n{'stage':<32}{'rows':>12}")
    for table, label in ROW_COUNT_TABLES:
        print(f"{label:<32}{row_count(cur, table):>12,}")

    # Reaching this point means every seed-validation check above passed, so
    # by construction no seed row was rejected this run.
    print("\nSeed rows rejected: 0")

    # ---- 2023-24 acre shares (printed, not a gate) ----
    cur.execute("""
        SELECT sum(total_acres), sum(in_scope_acres), sum(unsourced_acres), sum(not_modelled_acres)
        FROM marts.mv_spray_demand WHERE year_label = %s
    """, (LATEST_COMPLETE_YEAR_LABEL,))
    total, in_scope, unsourced, not_modelled = (float(x) for x in cur.fetchone())
    print(f"\n{LATEST_COMPLETE_YEAR_LABEL} acres: total {total:,.0f}")
    print(f"  in scope:      {in_scope:>15,.0f}  ({100*in_scope/total:.1f}%)")
    print(f"  unsourced:     {unsourced:>15,.0f}  ({100*unsourced/total:.1f}%)")
    print(f"  not modelled:  {not_modelled:>15,.0f}  ({100*not_modelled/total:.1f}%)")
    print(f"  in scope w/ pass count: {100*(in_scope-unsourced)/total:.1f}%")

    # ---- open needs_check crops (printed, not a gate) ----
    cur.execute("SELECT crop FROM staging.crop_spray_passes WHERE needs_check = 'Y' ORDER BY crop")
    needs_check_open = cur.fetchall()
    print(f"\nneeds_check = 'Y' crops still open: {len(needs_check_open)} "
          f"({', '.join(c for c, in needs_check_open)})")

    con.close()
    print(f"\nBackend: {backend()}   Tables: marts.*")


if __name__ == "__main__":
    main()
