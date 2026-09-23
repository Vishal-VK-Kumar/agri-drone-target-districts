"""
Stage 03 - staging.

What:   Builds staging.stg_crop_production, a long-format table (one row per
        state x district x crop x season x year) out of raw.crop_production_-
        import. On the way it separates out three kinds of rows that UPAg
        exports but that do not belong at the staging grain - Season =
        'Total' rows, crop-group rows (Cereals, Total Food Grains, ...), and
        years with no figures at all - into their own tables, and runs three
        quality checks: every crop name is accounted for by one of the two
        seed files; season-row areas sum to the matching Total row within
        1 ha; and where a Total row or its season-row sum is NULL on exactly
        one side (not both), the crop is one of EXPLAINED_TOTAL_ONLY_CROPS.
        See sql/02_stage.sql's header for the full table list.
Reads:  raw.crop_production_import, sql/02_stage.sql,
        seeds/crop_group_rows.csv, seeds/crops.csv
Writes: staging.* (PostgreSQL, or DuckDB if DB_BACKEND=duckdb)
Re-run: safe. Every staging table is dropped and rebuilt; raw and the seed
        CSVs are never read as anything but input. The whole build runs in
        one transaction and is committed only after every check passes, so a
        failed run leaves nothing behind that a later stage could read - not
        even the diagnostic chk_* tables. Pass --keep-on-fail to commit
        anyway for debugging; the Makefile never passes it.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from db import backend, connect

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
DDL_PATH = REPO_ROOT / "sql" / "02_stage.sql"
SEED_CROP_GROUPS_PATH = REPO_ROOT / "seeds" / "crop_group_rows.csv"
SEED_CROPS_PATH = REPO_ROOT / "seeds" / "crops.csv"
SEED_SPLIT_MARKER = "-- ===== SEEDS LOADED HERE =====\n"
RAW_TABLE = "raw.crop_production_import"
LONG_TABLE = "staging._crop_production_long"
STAGE_TABLE = "staging.stg_crop_production"
YEAR_SLOTS_PER_RAW_ROW = 3  # raw.crop_production_import has year_1..year_3
# Must match the 1.0 literal in sql/02_stage.sql's chk_total_reconciliation
TOTAL_MATCH_TOLERANCE_HA = 1.0
# Crop-group names UPAg reports only as a Season = 'Total' row, with no
# Kharif/Rabi/Summer breakdown, in any state or year - checked directly
# against every row of every raw file: Season is 'Total' for 100% of their
# rows. Their district-crop-year keys are one_null by construction: the
# Total side has a figure, the season side has no rows to sum at all. A
# first run of this check with an empty set found one_null in nearly every
# state and year, which ruled out a (state, year) allow-list - the real
# cause is the crop, not where or when. Any one_null row for a crop outside
# this set is unexplained and fails the run.
EXPLAINED_TOTAL_ONLY_CROPS = frozenset({"Total Oil Seeds", "Total Pulses"})
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
        help="commit the build even if a check fails, so the staging tables "
             "can be inspected. Off by default: a failed run commits nothing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not DDL_PATH.exists():
        fail(f"{DDL_PATH.relative_to(REPO_ROOT)} is missing")
    if not SEED_CROP_GROUPS_PATH.exists() or not SEED_CROPS_PATH.exists():
        fail("seeds/crop_group_rows.csv and seeds/crops.csv must both exist")

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
    copy_csv(cur, SEED_CROP_GROUPS_PATH, "staging.seed_crop_groups")
    copy_csv(cur, SEED_CROPS_PATH, "staging.seed_crops")

    # ---- transform: unpivot raw, split out the three excluded kinds, check
    cur.execute(transform_sql)

    # ---- every crop name must be in one of the two seed files ----
    cur.execute("SELECT crop_name, n_rows FROM staging.chk_unknown_crop ORDER BY crop_name")
    unknown = cur.fetchall()
    if unknown:
        fail_build("crop names in neither seed file: "
                   + ", ".join(f"{name} ({n} rows)" for name, n in unknown))

    # ---- season rows must sum to the Total row within tolerance ----
    cur.execute("SELECT status, count(*) FROM staging.chk_total_reconciliation "
                "GROUP BY status")
    status_counts = dict(cur.fetchall())
    total_keys = sum(status_counts.values())
    matched = status_counts.get("matched", 0)
    mismatched = status_counts.get("mismatched", 0)
    both_null = status_counts.get("both_null", 0)
    one_null = status_counts.get("one_null", 0)
    print(f"Total-vs-season reconciliation: {total_keys:,} district-crop-year combos, "
          f"{matched:,} matched within {TOTAL_MATCH_TOLERANCE_HA} ha, {mismatched:,} mismatched, "
          f"{both_null:,} both sides NULL (expected: nothing reported either way), "
          f"{one_null:,} exactly one side NULL (a real discrepancy, not a skip)")

    if mismatched:
        cur.execute("SELECT state_name, district_name, crop_name, year_label, "
                     "total_area_ha, season_area_ha, diff_ha "
                     "FROM staging.chk_total_reconciliation WHERE status = 'mismatched' "
                     "ORDER BY diff_ha DESC LIMIT 10")
        for row in cur.fetchall():
            print("   ", row)
        fail_build(f"{mismatched} district-crop-year combo(s) do not reconcile "
                   f"(> {TOTAL_MATCH_TOLERANCE_HA} ha); worst offenders above")

    if one_null:
        cur.execute("SELECT state_name, year_label, crop_name "
                     "FROM staging.chk_total_reconciliation WHERE status = 'one_null'")
        one_null_rows = cur.fetchall()

        # Root cause first: which crops carry the one_null rows. If this is
        # not entirely EXPLAINED_TOTAL_ONLY_CROPS, something new is going on.
        by_crop = Counter(crop_name for _, _, crop_name in one_null_rows)
        print("\none_null by crop:")
        for crop_name, n in by_crop.most_common():
            explained = crop_name in EXPLAINED_TOTAL_ONLY_CROPS
            print(f"  {crop_name:<26}{n:>8}  {'explained (Total-only crop)' if explained else 'UNEXPLAINED'}")

        # Requested breakdown: same rows, grouped by state and year instead.
        by_state_year = Counter((state_name, year_label) for state_name, year_label, _ in one_null_rows)
        unexplained_by_state_year = Counter(
            (state_name, year_label) for state_name, year_label, crop_name in one_null_rows
            if crop_name not in EXPLAINED_TOTAL_ONLY_CROPS)
        print(f"\n{'state':<46}{'year':<10}{'rows':>8}{'unexplained':>13}")
        for key in sorted(by_state_year):
            state_name, year_label = key
            print(f"{state_name:<46}{year_label:<10}{by_state_year[key]:>8}"
                  f"{unexplained_by_state_year.get(key, 0):>13}")

        unexplained = sum(unexplained_by_state_year.values())
        if unexplained:
            fail_build(f"{unexplained:,} one_null row(s) are for a crop not in "
                       "EXPLAINED_TOTAL_ONLY_CROPS; investigate and either fix the "
                       "classification or add the crop to the allow-list with a reason")
        print(f"\nAll {one_null:,} one_null rows are accounted for by "
              f"{sorted(EXPLAINED_TOTAL_ONLY_CROPS)} (Total row, no season breakdown "
              "anywhere in the raw data)")

    # ---- row counts: must reconcile exactly ----
    counts = {
        "raw rows": row_count(cur, RAW_TABLE),
        "long rows (unpivoted)": row_count(cur, LONG_TABLE),
        "chk_season_total": row_count(cur, "staging.chk_season_total"),
        "chk_crop_group": row_count(cur, "staging.chk_crop_group"),
        "rej_empty_year": row_count(cur, "staging.rej_empty_year"),
        "stg_crop_production": row_count(cur, STAGE_TABLE),
    }

    print(f"\n{'stage':<28}{'rows':>12}")
    for label, n in counts.items():
        print(f"{label:<28}{n:>12,}")

    expected_long = counts["raw rows"] * YEAR_SLOTS_PER_RAW_ROW
    excluded_plus_kept = (counts["chk_season_total"] + counts["chk_crop_group"]
                           + counts["rej_empty_year"] + counts["stg_crop_production"])
    long_ok = expected_long == counts["long rows (unpivoted)"]
    split_ok = excluded_plus_kept == counts["long rows (unpivoted)"]
    print(f"\n{'raw rows x ' + str(YEAR_SLOTS_PER_RAW_ROW):<32}{expected_long:>10,}"
          f"  vs long rows: {'OK' if long_ok else 'MISMATCH'}")
    print(f"{'season_total+crop_group+rej+stg':<32}{excluded_plus_kept:>10,}"
          f"  vs long rows: {'OK' if split_ok else 'MISMATCH'}")

    if not (long_ok and split_ok):
        fail_build("row counts do not reconcile")

    # ---- the 30 individual crops and their production units ----
    cur.execute("SELECT crop_name, production_unit, unit_weight_kg FROM staging.seed_crops "
                "ORDER BY crop_name")
    print(f"\n{'crop':<24}{'unit':<10}{'kg per unit':>12}")
    for crop_name, unit, kg in cur.fetchall():
        print(f"{crop_name:<24}{unit:<10}{kg:>12}")

    # Every check passed: commit the whole build now, for the first time.
    con.commit()
    con.close()
    print(f"\nBackend: {backend()}   Table: {STAGE_TABLE}")


if __name__ == "__main__":
    main()
