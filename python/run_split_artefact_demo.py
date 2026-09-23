"""
Report - before/after demonstration for step 5 (district lineage).

What:   Runs sql/analysis/split_artefact_demo.sql and formats it as a
        readable before/after table for three districts whose boundary
        changes collapse their raw name in one year: Warangal (Telangana),
        Ballari (Karnataka), Visakhapatnam (Andhra Pradesh). Not a pipeline
        stage - a report over staging.district_alias, run on demand.
Reads:  staging.stg_crop_production, staging.district_alias,
        sql/analysis/split_artefact_demo.sql
Writes: output/split_artefact_demo.txt (also printed to stdout)
Re-run: safe. Read-only query; the output file is overwritten each time.
"""

import sys
from pathlib import Path

from db import backend, connect

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = REPO_ROOT / "sql" / "analysis" / "split_artefact_demo.sql"
OUTPUT_PATH = REPO_ROOT / "output" / "split_artefact_demo.txt"
# Case order fixed here, not left to SQL sort order, so the report reads
# Telangana / Karnataka / Andhra Pradesh regardless of GROUP BY / ORDER BY
CASES = [("Telangana", "Warangal"), ("Karnataka", "Ballari"),
          ("Andhra Pradesh", "Visakhapatnam")]
# -----------------------------------------------------------------------------


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def format_block(title: str, rows: list) -> list:
    lines = [title, f"  {'year':<10}{'area_ha':>14}{'yoy_change_ha':>16}"]
    for year_label, area_ha, yoy_change_ha in rows:
        change = "" if yoy_change_ha is None else f"{yoy_change_ha:>+16,.0f}"
        lines.append(f"  {year_label:<10}{area_ha:>14,.0f}{change}")
    return lines


def main() -> None:
    if not SQL_PATH.exists():
        fail(f"{SQL_PATH.relative_to(REPO_ROOT)} is missing")

    con = connect()
    cur = con.cursor()
    cur.execute(SQL_PATH.read_text(encoding="utf-8"))
    all_rows = cur.fetchall()
    con.close()

    # grouping -> [(year_label, area_ha, yoy_change_ha)], label -> unit_label
    # (by_district rows carry their own district_name as label; by_unit rows
    # all carry the same unit_label, so the last one read is fine)
    by_case = {}
    for grouping, state_name, case_district_name, label, year_label, _year_start, area_ha, yoy in all_rows:
        case = by_case.setdefault((state_name, case_district_name), {})
        case.setdefault(grouping, []).append((year_label, area_ha, yoy))
        if grouping == "by_unit":
            case["unit_label"] = label

    out = [
        "Split-artefact demonstration - step 5 (district lineage)",
        "",
        "Each case shows the same district's year-on-year area change two ways:",
        "by its raw name (where a boundary split shows as a fake one-year",
        "collapse) and by its stable unit (all districts that were once part",
        "of the same territory, summed together, where the collapse is gone).",
        "",
    ]
    for state_name, district_name in CASES:
        case = by_case.get((state_name, district_name))
        if not case:
            fail(f"no rows for {state_name}/{district_name}; "
                 "check sql/analysis/split_artefact_demo.sql and that stage 04 has run")

        out.append(f"=== {district_name} ({state_name}) ===")
        out += format_block("By raw district name:", case["by_district"])
        out.append("")
        out += format_block(f"By stable unit ({case['unit_label']}):", case["by_unit"])
        out.append("")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")

    print("\n".join(out))
    print(f"\nBackend: {backend()}")
    print(f"Saved: {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
