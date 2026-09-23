"""
Stage 04 - district lineage and stable units.

What:   Loads seeds/district_lineage.csv (one row per child-parent district
        pair, researched outside this repo - see docs/district_alias_notes.md)
        into staging.district_lineage, builds stable units by connected
        components over cluster_edge = 'Y' edges (a district split, directly
        or through a chain, is one unit; a district with no edges is its own
        unit), and writes staging.district_alias: one row per staging (state,
        district) with district_key, unit_key, unit_label, first_year,
        last_year, lineage_event and is_coverage_gap. Checks: every lineage
        child/parent name exists in staging; every staging district appears
        in district_alias exactly once; every district first seen after
        2013-14 has an origin lineage row; every unit_label is non-NULL and
        non-empty; and the area total per state-year is identical summed by
        district or by unit. See sql/03_district_lineage.sql's header for
        the full table list.
Reads:  staging.stg_crop_production, sql/03_district_lineage.sql,
        seeds/district_lineage.csv
Writes: staging.* (see sql/03_district_lineage.sql)
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
DDL_PATH = REPO_ROOT / "sql" / "03_district_lineage.sql"
SEED_PATH = REPO_ROOT / "seeds" / "district_lineage.csv"
SEED_SPLIT_MARKER = "-- ===== SEEDS LOADED HERE =====\n"
ALIAS_TABLE = "staging.district_alias"
# Independently verified (docs/district_alias_notes.md and a pure-Python
# union-find over the seed CSV, done before this script was written).
# Informational only: the real gates are the checks below, not this number.
# 627 -> 629 and Tamil Nadu 30 -> 32 after the 2026-09-23 materiality review
# set Chennai's two edges to cluster_edge = 'N' (immaterial: at most ~0.2% of
# either parent's area). See docs/district_alias_notes.md, Edge rules.
EXPECTED_TOTAL_UNITS = 629
EXPECTED_UNITS_BY_STATE = {"Telangana": 4, "Andhra Pradesh": 5, "Tamil Nadu": 32}
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
    if not SEED_PATH.exists():
        fail(f"{SEED_PATH.relative_to(REPO_ROOT)} is missing")

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

    # ---- seed table: DDL, then load, before the transform can join on it
    cur.execute(seed_ddl)
    copy_csv(cur, SEED_PATH, "staging.district_lineage")
    seed_rows = row_count(cur, "staging.district_lineage")

    # ---- transform: validate, build stable units, build district_alias
    try:
        cur.execute(transform_sql)
    except Exception as exc:
        fail_build(f"unexpected database error while building the lineage tables: {exc}")

    # ---- every lineage child/parent name must exist in staging ----
    cur.execute("SELECT state, district_name, role FROM staging.chk_lineage_unknown_district "
                "ORDER BY state, district_name")
    unknown = cur.fetchall()
    if unknown:
        fail_build(f"seed rows name a district not in staging: "
                   + ", ".join(f"{state}/{name} ({role})" for state, name, role in unknown))

    # ---- every staging district appears in district_alias exactly once ----
    cur.execute("SELECT state_name, district_name FROM staging.chk_alias_missing")
    missing = cur.fetchall()
    if missing:
        fail_build(f"{len(missing)} staging district(s) missing from district_alias: {missing[:10]}")

    cur.execute("SELECT state_name, district_name, n FROM staging.chk_alias_duplicate")
    duplicate = cur.fetchall()
    if duplicate:
        fail_build(f"{len(duplicate)} staging district(s) appear more than once in "
                   f"district_alias: {duplicate[:10]}")

    # ---- every district first seen after 2013-14 has an origin lineage row ----
    cur.execute("SELECT state_name, district_name, first_year FROM staging.chk_new_district_lineage "
                "ORDER BY state_name, district_name")
    unexplained_new = cur.fetchall()
    if unexplained_new:
        fail_build(f"{len(unexplained_new)} district(s) first appear after 2013-14 with no "
                   "split/split_and_rename/coverage_gap lineage row: "
                   + ", ".join(f"{s}/{d} (first {y})" for s, d, y in unexplained_new))

    # ---- every unit_label must be non-NULL and non-empty ----
    cur.execute("SELECT state_name, unit_seed FROM staging.chk_unit_label_missing "
                "ORDER BY state_name, unit_seed")
    missing_label = cur.fetchall()
    if missing_label:
        fail_build(f"{len(missing_label)} unit(s) have a NULL or empty unit_label: "
                   + ", ".join(f"{s}/{u}" for s, u in missing_label))

    # ---- area total by district must equal area total by unit ----
    cur.execute("SELECT state_name, year_label, by_district_ha, by_unit_ha, diff_ha "
                "FROM staging.chk_area_reconciliation ORDER BY abs(diff_ha) DESC LIMIT 10")
    area_mismatches = cur.fetchall()
    if area_mismatches:
        for row in area_mismatches:
            print("   ", row)
        fail_build(f"{len(area_mismatches)}+ state-year area total(s) disagree between "
                   "by-district and by-unit grouping; worst offenders above")

    # Every check passed: commit the whole build now, for the first time.
    con.commit()

    # ---- row counts ----
    n_lineage_rows = seed_rows
    n_staging_districts = row_count(cur, "staging._staging_districts")
    n_alias_rows = row_count(cur, ALIAS_TABLE)
    print(f"seed rows loaded (district_lineage.csv):  {n_lineage_rows:>6,}")
    print(f"staging (state, district) names:          {n_staging_districts:>6,}")
    print(f"district_alias rows:                       {n_alias_rows:>6,}")

    # ---- units per state (printed, does not fail) ----
    cur.execute("SELECT state_name, n_units, n_names FROM staging.chk_units_per_state ORDER BY state_name")
    per_state = cur.fetchall()
    total_units = sum(n for _, n, _ in per_state)
    print(f"\n{'state':<28}{'units':>8}{'names':>8}")
    for state_name, n_units, n_names in per_state:
        print(f"{state_name:<28}{n_units:>8}{n_names:>8}")
    print(f"{'TOTAL':<28}{total_units:>8}{n_alias_rows:>8}")

    if total_units != EXPECTED_TOTAL_UNITS:
        print(f"\nNOTE: expected {EXPECTED_TOTAL_UNITS} total units, got {total_units} "
              f"(difference: {total_units - EXPECTED_TOTAL_UNITS}); see module docstring")
    for state_name, expected in EXPECTED_UNITS_BY_STATE.items():
        actual = next((n for s, n, _ in per_state if s == state_name), None)
        if actual != expected:
            print(f"NOTE: expected {expected} units in {state_name}, got {actual}")

    # ---- open needs_check rows (printed, does not fail) ----
    cur.execute("SELECT count(*) FROM staging.district_lineage WHERE needs_check = 'Y'")
    needs_check_open = cur.fetchone()[0]
    print(f"\nneeds_check = 'Y' rows still open: {needs_check_open}")
    if needs_check_open:
        cur.execute("SELECT state, child_district, parent_district, note "
                     "FROM staging.district_lineage WHERE needs_check = 'Y' "
                     "ORDER BY state, child_district")
        for state, child, parent, note in cur.fetchall():
            print(f"   {state}/{child} <- {parent}: {note or ''}")

    con.close()
    print(f"\nBackend: {backend()}   Table: {ALIAS_TABLE}")


if __name__ == "__main__":
    main()
