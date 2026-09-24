"""
Stage 06 - analysis: which districts to serve first with one drone.

What:   Runs sql/05_analysis.sql, which builds marts.rpt_crop_base,
        marts.rpt_unit_trend, marts.rpt_top_districts and
        marts.rpt_state_rollup entirely from the existing marts (no new
        seed). Runs the checks in the module docstring's Checks section,
        exports output/top_districts.csv and output/state_rollup.csv, and
        prints the diagnostics the module docstring's Diagnostics section
        lists (informational only - they do not gate the build).
Checks: rpt_top_districts' pp_spray_acres_base total equals
        mv_spray_demand's ranking-year total; no duplicate (district_key,
        season); every row has a decile between 1 and 10; a row's top-crop
        shares sum to at most 100.
Diagnostics: share of national base acre-passes by crop, and the same
        restricted to the top 50 by national rank; how many of the top
        10/25/50 by base also sit in the top 10/25/50 at both low and high;
        how many of the top 50 take more than 25% of their base from open
        (needs_check = 'Y') crops, and which ones; for rice, soybean and
        cotton, how many ranking-year district-seasons - sorted by that
        crop's own base acre-passes - it takes to reach half of that crop's
        national base acre-passes (from rpt_crop_base, i.e. the fact and
        seed tables, not the top-three-crop columns on rpt_top_districts).
Reads:  marts.dim_year, marts.rpt_top_districts, marts.rpt_state_rollup,
        marts.rpt_crop_base, sql/05_analysis.sql
Writes: marts.* (see sql/05_analysis.sql), output/top_districts.csv,
        output/state_rollup.csv
Re-run: safe. The whole build runs in one transaction and is committed only
        after every check passes, so a failed run leaves nothing behind that
        a later stage could read. Pass --keep-on-fail to commit anyway for
        debugging; the Makefile never passes it.
"""

import argparse
import sys
from pathlib import Path

from db import connect

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
DDL_PATH = REPO_ROOT / "sql" / "05_analysis.sql"
OUTPUT_DIR = REPO_ROOT / "output"
TOP_DISTRICTS_PATH = OUTPUT_DIR / "top_districts.csv"
STATE_ROLLUP_PATH = OUTPUT_DIR / "state_rollup.csv"
TOP_DISTRICTS_COLUMNS = [
    "district_key", "district_name", "state", "season", "sprayable_acres",
    "pp_spray_acres_low", "pp_spray_acres_base", "pp_spray_acres_high",
    "rank_national", "rank_national_low", "rank_national_high", "rank_in_state", "decile",
    "top_crop_1", "top_crop_1_share_pct", "top_crop_2", "top_crop_2_share_pct",
    "top_crop_3", "top_crop_3_share_pct", "open_crop_share_pct",
    "unit_key", "unit_district_count", "unit_yoy_area_change_pct", "unit_area_ma3_acres",
]
STATE_ROLLUP_COLUMNS = [
    "level", "state", "district_name", "pp_spray_acres_base",
    "n_district_seasons", "n_decile_1",
]
TOP_N_STABILITY = (10, 25, 50)
OPEN_CROP_SHARE_THRESHOLD_PCT = 25
CONCENTRATION_CROPS = ("Rice", "Soybean", "Cotton")
# -----------------------------------------------------------------------------


def fail(message: str) -> None:
    """Pre-connection failures only - nothing to roll back yet."""
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-on-fail", action="store_true",
        help="commit the build even if a check fails, so the report tables "
             "can be inspected. Off by default: a failed run commits nothing.")
    return parser.parse_args()


def export_csv(cur, select_sql: str, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        cur.copy_expert(f"COPY ({select_sql}) TO STDOUT WITH (FORMAT csv, HEADER true)", fh)
    with path.open(encoding="utf-8") as fh:
        return sum(1 for _ in fh) - 1  # rows, excluding the header


def main() -> None:
    args = parse_args()

    if not DDL_PATH.exists():
        fail(f"{DDL_PATH.relative_to(REPO_ROOT)} is missing")

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

    try:
        cur.execute(DDL_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        fail_build(f"unexpected database error while building the analysis tables: {exc}")

    # ---- quality checks ----
    cur.execute("SELECT top_districts_total, view_total FROM staging.chk_analysis_base_total")
    top_total, view_total = cur.fetchone()
    if top_total != view_total:
        fail_build(f"top_districts.csv base total ({top_total}) != "
                   f"mv_spray_demand's ranking-year base total ({view_total})")

    checks = [
        ("staging.chk_analysis_duplicate", "district_key, season, n",
         "duplicate (district_key, season) row(s) in rpt_top_districts"),
        ("staging.chk_analysis_decile_range", "district_key, season, decile",
         "row(s) with a decile outside 1-10"),
        ("staging.chk_analysis_crop_share_sum", "district_key, season, share_sum",
         "row(s) whose top-crop shares sum to more than 100"),
    ]
    for table, cols, description in checks:
        cur.execute(f"SELECT {cols} FROM {table}")
        rows = cur.fetchall()
        if rows:
            fail_build(f"{len(rows)} {description}: {rows[:10]}")

    # ---- export ----
    n_top_districts = export_csv(
        cur, f"SELECT {', '.join(TOP_DISTRICTS_COLUMNS)} FROM marts.rpt_top_districts "
             "ORDER BY rank_national, state, district_name, season",
        TOP_DISTRICTS_PATH)
    n_state_rollup = export_csv(
        cur, f"SELECT {', '.join(STATE_ROLLUP_COLUMNS)} FROM marts.rpt_state_rollup "
             "ORDER BY level, pp_spray_acres_base DESC",
        STATE_ROLLUP_PATH)

    # Every check passed and both CSVs are written: commit the whole build now.
    con.commit()

    print(f"Wrote {TOP_DISTRICTS_PATH.relative_to(REPO_ROOT)}: {n_top_districts:,} rows")
    print(f"Wrote {STATE_ROLLUP_PATH.relative_to(REPO_ROOT)}: {n_state_rollup:,} rows")

    cur.execute("SELECT max(rank_national) FROM marts.rpt_top_districts")
    max_rank = cur.fetchone()[0]
    print(f"\nrows: {n_top_districts:,}   max dense_rank (base): {max_rank:,}   "
          f"ties collapsed: {n_top_districts - max_rank:,}")

    # ---- diagnostics (printed, not a gate) ----
    print("\nShare of national base acre-passes by crop:")
    cur.execute("""
        SELECT crop_key, sum(crop_base_acres) AS acres
        FROM marts.rpt_crop_base GROUP BY crop_key ORDER BY acres DESC
    """)
    crop_rows = cur.fetchall()
    national_total = sum(acres for _, acres in crop_rows)
    for crop_key, acres in crop_rows:
        print(f"  {crop_key:<22}{100*float(acres)/float(national_total):>6.1f}%")

    print("\nSame, restricted to the top 50 by national rank:")
    cur.execute("""
        SELECT cb.crop_key, sum(cb.crop_base_acres) AS acres
        FROM marts.rpt_crop_base cb
        JOIN marts.rpt_top_districts t
          ON t.district_key = cb.district_key AND t.season = cb.season
        WHERE t.rank_national <= 50
        GROUP BY cb.crop_key ORDER BY acres DESC
    """)
    top50_crop_rows = cur.fetchall()
    top50_total = sum(acres for _, acres in top50_crop_rows)
    for crop_key, acres in top50_crop_rows:
        print(f"  {crop_key:<22}{100*float(acres)/float(top50_total):>6.1f}%")

    cur.execute("""
        SELECT sum(pp_spray_acres_base) FROM marts.rpt_top_districts WHERE rank_national <= 50
    """)
    top50_base = float(cur.fetchone()[0])
    print(f"\nTop 50 share of national base: {100*top50_base/float(national_total):.1f}%")

    print("\nStability: top N by base also in the top N at both low and high:")
    for n in TOP_N_STABILITY:
        cur.execute("""
            SELECT count(*) FROM marts.rpt_top_districts
            WHERE rank_national <= %s AND rank_national_low <= %s AND rank_national_high <= %s
        """, (n, n, n))
        stable = cur.fetchone()[0]
        print(f"  top {n}: {stable} / {n}")

    cur.execute("""
        SELECT district_name, state, open_crop_share_pct
        FROM marts.rpt_top_districts
        WHERE rank_national <= 50 AND open_crop_share_pct > %s
        ORDER BY rank_national
    """, (OPEN_CROP_SHARE_THRESHOLD_PCT,))
    open_heavy = cur.fetchall()
    print(f"\nTop 50 taking more than {OPEN_CROP_SHARE_THRESHOLD_PCT}% of base from open crops: "
          f"{len(open_heavy)}")
    for district_name, state, share in open_heavy:
        print(f"  {district_name} ({state}): {float(share):.1f}%")

    cur.execute("""
        SELECT sum(crop_base_acres) FROM marts.rpt_crop_base WHERE needs_check
    """)
    open_total = float(cur.fetchone()[0])
    print(f"\nOpen crops (needs_check = 'Y'): {100*open_total/float(national_total):.1f}% of national base")

    print("\nRanking-year district-seasons needed to reach half of national base "
          "acre-passes, by crop (from rpt_crop_base, not the top-three-crop columns):")
    cur.execute("""
        WITH ranked_crop_rows AS (
            SELECT
                crop_key,
                crop_base_acres,
                sum(crop_base_acres) OVER (
                    PARTITION BY crop_key ORDER BY crop_base_acres DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running_total,
                sum(crop_base_acres) OVER (PARTITION BY crop_key) AS crop_total,
                row_number() OVER (PARTITION BY crop_key ORDER BY crop_base_acres DESC) AS rn
            FROM marts.rpt_crop_base
            WHERE crop_key = ANY(%s)
        )
        SELECT crop_key, min(rn) AS n_for_half
        FROM ranked_crop_rows
        WHERE running_total >= crop_total / 2.0
        GROUP BY crop_key
        ORDER BY crop_key
    """, (list(CONCENTRATION_CROPS),))
    for crop_key, n_for_half in cur.fetchall():
        print(f"  {crop_key:<10}{n_for_half}")

    cur.execute("SELECT count(*) FROM marts.rpt_unit_trend WHERE yoy_change_pct IS NOT NULL "
                "AND year_label = (SELECT year_label FROM marts.dim_year WHERE is_complete "
                "ORDER BY start_year DESC LIMIT 1)")
    n_yoy = cur.fetchone()[0]
    print(f"\nUnits with a year-on-year change at the ranking year: {n_yoy}")

    cur.execute("SELECT count(*) FROM marts.rpt_state_rollup WHERE level = 'state'")
    n_states = cur.fetchone()[0]
    print(f"States in the rollup: {n_states}")

    cur.execute("""
        SELECT state, pp_spray_acres_base FROM marts.rpt_state_rollup
        WHERE level = 'state' ORDER BY pp_spray_acres_base DESC LIMIT 5
    """)
    print("Top 5 states by base acre-passes:")
    for state, base in cur.fetchall():
        print(f"  {state:<20}{float(base):>15,.0f}")

    con.close()
    print(f"\nBackend: postgres   Tables: marts.rpt_*")


if __name__ == "__main__":
    main()
