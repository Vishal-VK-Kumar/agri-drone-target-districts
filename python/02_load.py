"""
Stage 02 - raw load.

What:   Unions every UPAg district APY export in data/raw/ into
        raw.crop_production_import with no cleaning, deduplication or type
        coercion. Before loading it checks that every file has the same column
        pattern apart from the year labels, that the years in each header match
        the file name, that no State-District-Crop-Season-year appears in two
        files, and that each file's row count matches MANIFEST.json. Any
        failure exits non-zero before anything is written.
Reads:  data/raw/*.csv, data/raw/MANIFEST.json, sql/01_load_raw.sql
Writes: raw.crop_production_import (PostgreSQL, or DuckDB if DB_BACKEND=duckdb)
Re-run: safe. The table is dropped and rebuilt from the files; the files are
        never modified.
"""

import csv
import itertools
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from db import backend, connect

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
DDL_PATH = REPO_ROOT / "sql" / "01_load_raw.sql"
RAW_TABLE = "raw.crop_production_import"
FILE_GLOB = "*.csv"
# The export starts with a byte-order mark; utf-8-sig drops only that marker
SOURCE_ENCODING = "utf-8-sig"
KEY_COLUMNS = ["State", "District", "Crop", "Season"]
METRICS = ["Area", "Production", "Yield"]
# UPAg caps each export at three years, so the raw table has three year slots
YEARS_PER_FILE = 3
YEAR_RE = re.compile(r"\d{4}-\d{2}")  # agricultural year, e.g. 2013-14
FILENAME_RE = re.compile(r"(\d{4}-\d{2})-to-(\d{4}-\d{2})\.csv$")
YEAR_PLACEHOLDER = "{year}"
# -----------------------------------------------------------------------------


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def next_year(label: str) -> str:
    start = int(label[:4]) + 1
    return f"{start}-{(start + 1) % 100:02d}"


def read_export(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding=SOURCE_ENCODING) as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    return header, rows


def years_from_header(path: Path, header: list[str]) -> list[str]:
    """The three year labels, after checking the header is Key cols + Metric-year x 3."""
    expected_width = len(KEY_COLUMNS) + len(METRICS) * YEARS_PER_FILE
    if len(header) != expected_width or header[: len(KEY_COLUMNS)] != KEY_COLUMNS:
        fail(f"{path.name}: unexpected header {header}")

    years = [YEAR_RE.search(col).group() if YEAR_RE.search(col) else None
             for col in header[len(KEY_COLUMNS):]]
    first = years[:YEARS_PER_FILE]
    for i, metric in enumerate(METRICS):
        block = header[len(KEY_COLUMNS) + i * YEARS_PER_FILE:][:YEARS_PER_FILE]
        if block != [f"{metric}-{y}" for y in first]:
            fail(f"{path.name}: {metric} columns {block} do not match years {first}")
    for a, b in itertools.pairwise(first):
        if b != next_year(a):
            fail(f"{path.name}: years {first} are not consecutive")

    # The file name is set by UPAg from the chosen From/To years; a mismatch
    # means the file was renamed or the export settings were not what we think
    match = FILENAME_RE.search(path.name)
    if not match or [match.group(1), match.group(2)] != [first[0], first[-1]]:
        fail(f"{path.name}: header years {first} do not match the file name")
    return first


def main() -> None:
    files = sorted(RAW_DIR.glob(FILE_GLOB)) if RAW_DIR.is_dir() else []
    if not files:
        fail("no CSV files in data/raw/; run python/01_download.py first")
    if not MANIFEST_PATH.exists():
        fail("data/raw/MANIFEST.json is missing; run python/01_download.py first")
    manifest_rows = {e["file"]: e["data_rows"]
                     for e in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["files"]}

    # ---- checks, all before any write ----
    exports, patterns = {}, {}
    for path in files:
        header, rows = read_export(path)
        years = years_from_header(path, header)
        ragged = [n for n, row in enumerate(rows, start=1) if len(row) != len(header)]
        if ragged:
            fail(f"{path.name}: {len(ragged)} rows with the wrong field count, first at data row {ragged[0]}")
        if path.name not in manifest_rows:
            fail(f"{path.name}: not in MANIFEST.json; run python/01_download.py first")
        if len(rows) != manifest_rows[path.name]:
            fail(f"{path.name}: {len(rows)} data rows, MANIFEST.json says {manifest_rows[path.name]}")
        exports[path.name] = (years, rows)
        patterns[path.name] = tuple(YEAR_RE.sub(YEAR_PLACEHOLDER, col) for col in header)

    if len(set(patterns.values())) != 1:
        fail(f"column patterns differ between files: {patterns}")
    print(f"Column pattern identical in all {len(files)} files: {list(next(iter(patterns.values())))}")

    # Grain of the source is State x District x Crop x Season per year. Keys
    # are compared as exported: district names repeat across states
    # (Bilaspur, Hamirpur, Pratapgarh), so State is always part of the key.
    key_width = len(KEY_COLUMNS)
    key_owner: dict[tuple, str] = {}
    cross_file = Counter()
    for name, (years, rows) in exports.items():
        for row in rows:
            for year in years:
                key = (*row[:key_width], year)
                if key in key_owner and key_owner[key] != name:
                    cross_file[(key_owner[key], name)] += 1
                key_owner.setdefault(key, name)
    if cross_file:
        fail(f"State-District-Crop-Season-year keys found in two files: {dict(cross_file)}")
    print(f"No State-District-Crop-Season-year appears in two files ({len(key_owner):,} keys checked)")

    # Repeats within one file are reported, not removed: raw keeps everything
    for name, (_, rows) in exports.items():
        repeats = sum(c - 1 for c in Counter(tuple(r[:key_width]) for r in rows).values() if c > 1)
        if repeats:
            print(f"NOTE: {name} has {repeats} repeated State-District-Crop-Season rows (kept in raw)")

    # ---- load ----
    con = connect()
    cur = con.cursor()
    cur.execute(DDL_PATH.read_text(encoding="utf-8"))

    # One quoted staging CSV for both backends: every field is quoted so an
    # empty cell stays an empty string rather than becoming NULL
    with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="", encoding="utf-8",
                                     delete=False) as tmp:
        writer = csv.writer(tmp, quoting=csv.QUOTE_ALL)
        for name, (years, rows) in exports.items():
            for n, row in enumerate(rows, start=1):
                writer.writerow([name, n, *years, *row])
    tmp_path = Path(tmp.name)

    try:
        if backend() == "duckdb":
            cur.execute(f"COPY {RAW_TABLE} FROM '{tmp_path.as_posix()}' "
                        "(FORMAT csv, HEADER false, ALLOW_QUOTED_NULLS false)")
        else:
            with tmp_path.open(encoding="utf-8") as fh:
                cur.copy_expert(f"COPY {RAW_TABLE} FROM STDIN WITH (FORMAT csv)", fh)
        con.commit()
    finally:
        tmp_path.unlink()

    cur.execute(f"SELECT source_file, count(*) FROM {RAW_TABLE} GROUP BY source_file")
    loaded = dict(cur.fetchall())
    con.close()

    # ---- rows in and out ----
    print(f"\nBackend: {backend()}   Table: {RAW_TABLE}")
    print(f"{'file':<48} {'years':<19} {'manifest':>9} {'rows in':>9} {'loaded':>9}")
    mismatches = []
    for name, (years, rows) in exports.items():
        got = loaded.get(name, 0)
        flag = "" if got == len(rows) == manifest_rows[name] else "  <-- MISMATCH"
        if flag:
            mismatches.append(name)
        print(f"{name:<48} {years[0] + ' to ' + years[-1]:<19} "
              f"{manifest_rows[name]:>9,} {len(rows):>9,} {got:>9,}{flag}")
    total_in = sum(len(rows) for _, rows in exports.values())
    print(f"{'TOTAL':<48} {'':<19} {sum(manifest_rows[n] for n in exports):>9,} "
          f"{total_in:>9,} {sum(loaded.values()):>9,}")
    if mismatches or set(loaded) != set(exports):
        fail(f"loaded row counts do not match the files: {mismatches}")


if __name__ == "__main__":
    main()
