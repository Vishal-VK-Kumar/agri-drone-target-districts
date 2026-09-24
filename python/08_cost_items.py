"""
Stage 08 - cost items: the Power BI break-even model's cost inputs, as a
file.

What:   Reads config/cost_assumptions.yml (one row per cost/utilisation
        input, each with a source and a grade) and writes
        output/marts/dim_cost_item.parquet and dim_cost_item.csv with an
        explicit pyarrow schema, snappy compression. No database: this
        stage only reads a file and writes files, so `make cost-items` runs
        it alone. Writes to temp paths first and only renames them into
        place once every check below passes, so a failed run leaves
        output/marts/ untouched - the write-then-verify-then-commit pattern
        python/07_export.py uses for the database-backed exports, applied
        here without a database to roll back.
Checks: 11 rows; `key` values unique; no null or empty field in any row;
        low <= base <= high for every row; `grade` in A, B, C; `role` in
        price, capex, subsidy, capacity, utilisation, battery,
        depreciation, benchmark. Reads the Parquet file back from disk and
        compares its row count and the sums of low/base/high against the
        same figures computed straight from the YAML.
Reads:  config/cost_assumptions.yml
Writes: output/marts/dim_cost_item.parquet, output/marts/dim_cost_item.csv
Re-run: safe. Deterministic: the same YAML produces byte-identical files
        (fixed column order, YAML item order preserved, explicit schema).
"""

import csv
import math
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = REPO_ROOT / "config" / "cost_assumptions.yml"
MARTS_DIR = REPO_ROOT / "output" / "marts"
PARQUET_PATH = MARTS_DIR / "dim_cost_item.parquet"
CSV_PATH = MARTS_DIR / "dim_cost_item.csv"
EXPECTED_ROWS = 11
SUM_REL_TOL = 1e-9  # float summation order (YAML vs pyarrow) can differ in the last bit
VALID_GRADES = {"A", "B", "C"}
VALID_ROLES = {
    "price", "capex", "subsidy", "capacity", "utilisation",
    "battery", "depreciation", "benchmark",
}
SCHEMA = pa.schema([
    ("cost_item_key", pa.string()),
    ("label", pa.string()),
    ("unit", pa.string()),
    ("role", pa.string()),
    ("low", pa.float64()),
    ("base", pa.float64()),
    ("high", pa.float64()),
    ("basis", pa.string()),
    ("source", pa.string()),
    ("grade", pa.string()),
    ("assumptions_version", pa.string()),
])
STRING_FIELDS = ("cost_item_key", "label", "unit", "role", "basis", "source",
                  "grade", "assumptions_version")
NUMERIC_FIELDS = ("low", "base", "high")
# -----------------------------------------------------------------------------


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    for path in (PARQUET_PATH.with_suffix(".parquet.tmp"), CSV_PATH.with_suffix(".csv.tmp")):
        if path.exists():
            path.unlink()
    sys.exit(1)


def main() -> None:
    if not YAML_PATH.exists():
        fail(f"{YAML_PATH.relative_to(REPO_ROOT)} is missing")

    doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    version = doc["version"]
    items = doc["items"]

    rows = [
        {
            "cost_item_key": item.get("key"),
            "label": item.get("label"),
            "unit": item.get("unit"),
            "role": item.get("role"),
            "low": item.get("low"),
            "base": item.get("base"),
            "high": item.get("high"),
            "basis": item.get("basis"),
            "source": item.get("source"),
            "grade": item.get("grade"),
            "assumptions_version": version,
        }
        for item in items
    ]

    # ---- checks on the YAML content, before any file is written ----
    if len(rows) != EXPECTED_ROWS:
        fail(f"{YAML_PATH.relative_to(REPO_ROOT)} has {len(rows)} items, expected {EXPECTED_ROWS}")

    keys = [r["cost_item_key"] for r in rows]
    if len(set(keys)) != len(keys):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        fail(f"duplicate key(s) in {YAML_PATH.relative_to(REPO_ROOT)}: {dupes}")

    for row in rows:
        for field in STRING_FIELDS:
            value = row[field]
            if value is None or (isinstance(value, str) and value.strip() == ""):
                fail(f"{row['cost_item_key']!r}: '{field}' is null or empty")
        for field in NUMERIC_FIELDS:
            if row[field] is None:
                fail(f"{row['cost_item_key']!r}: '{field}' is null")
        if not (row["low"] <= row["base"] <= row["high"]):
            fail(f"{row['cost_item_key']!r}: low <= base <= high violated "
                 f"({row['low']} <= {row['base']} <= {row['high']})")
        if row["grade"] not in VALID_GRADES:
            fail(f"{row['cost_item_key']!r}: grade {row['grade']!r} not in {sorted(VALID_GRADES)}")
        if row["role"] not in VALID_ROLES:
            fail(f"{row['cost_item_key']!r}: role {row['role']!r} not in {sorted(VALID_ROLES)}")

    # ---- build the table and write to temp paths ----
    columns = [[row[field.name] for row in rows] for field in SCHEMA]
    arrays = [pa.array(col, type=field.type) for col, field in zip(columns, SCHEMA)]
    table = pa.table(arrays, schema=SCHEMA)

    MARTS_DIR.mkdir(parents=True, exist_ok=True)
    parquet_tmp = PARQUET_PATH.with_suffix(".parquet.tmp")
    csv_tmp = CSV_PATH.with_suffix(".csv.tmp")
    pq.write_table(table, parquet_tmp, compression="snappy")
    with csv_tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(SCHEMA.names)
        writer.writerows(zip(*columns))

    # ---- read the Parquet file back from disk and check it against the YAML ----
    read_back = pq.read_table(parquet_tmp)
    if read_back.num_rows != len(rows):
        fail(f"dim_cost_item.parquet has {read_back.num_rows} rows read back, "
             f"expected {len(rows)} from the YAML")
    for field in NUMERIC_FIELDS:
        yaml_sum = sum(row[field] for row in rows)
        parquet_sum = sum(read_back.column(field).to_pylist())
        if not math.isclose(yaml_sum, parquet_sum, rel_tol=SUM_REL_TOL):
            fail(f"dim_cost_item.parquet sum({field}) = {parquet_sum}, "
                 f"YAML sum({field}) = {yaml_sum}")

    csv_rows = sum(1 for _ in csv_tmp.open(encoding="utf-8")) - 1
    if csv_rows != len(rows):
        fail(f"dim_cost_item.csv has {csv_rows} rows, expected {len(rows)}")

    # ---- print the table and schema ----
    print(f"{'key':<26}{'low':>12}{'base':>12}{'high':>12}  grade")
    for row in rows:
        print(f"{row['cost_item_key']:<26}{row['low']:>12,}{row['base']:>12,}"
              f"{row['high']:>12,}  {row['grade']}")

    print("\ndim_cost_item Parquet schema:")
    for field in SCHEMA:
        print(f"  {field.name}: {field.type}")

    # ---- every check passed: commit the temp files into place ----
    parquet_tmp.replace(PARQUET_PATH)
    csv_tmp.replace(CSV_PATH)

    print(f"\nWrote {PARQUET_PATH.relative_to(REPO_ROOT)} and {CSV_PATH.relative_to(REPO_ROOT)} "
          f"({len(rows)} rows)")


if __name__ == "__main__":
    main()
