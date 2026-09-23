"""
Stage 01 - record the hand-exported source files.

What:   Nothing is downloaded. UPAg's district APY report is a Dash app with no
        public API, so the CSVs are exported by hand (steps in README.md). This
        stage finds them in data/raw/, computes SHA-256, size and data-row count
        for each, and checks them against data/raw/MANIFEST.json.
Reads:  data/raw/*.csv, data/raw/MANIFEST.json (if present)
Writes: data/raw/MANIFEST.json - created if absent; otherwise only new files
        are appended. Recorded entries are never rewritten: a file whose
        checksum, size or row count differs from its entry fails the stage,
        because it means the raw data changed after it was recorded.
Re-run: safe. The CSVs are only read.
"""

import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

# ---- config -----------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "MANIFEST.json"
FILE_GLOB = "*.csv"
# The export starts with a byte-order mark; utf-8-sig drops only that marker
SOURCE_ENCODING = "utf-8-sig"
HEADER_ROWS = 1
HASH_CHUNK_BYTES = 1 << 20
MANIFEST_HEADER = {
    "source": "UPAg Complete APY Dataset (District Level), "
    "https://upag.gov.in/dash-reports/desdistrictwisecompletedatasetreport",
    "export_settings": "UOM = Actual; Crop Category = Food Grains, Commercial Crops "
    "and Oilseeds; Crop = All; Metric = Area, Production, Yield; CSV. "
    "The report caps each export at three years.",
}
# -----------------------------------------------------------------------------


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def count_data_rows(path: Path) -> int:
    # Counted as CSV records, not lines: a quoted field may contain a newline
    with path.open(newline="", encoding=SOURCE_ENCODING) as fh:
        return sum(1 for _ in csv.reader(fh)) - HEADER_ROWS


def describe(path: Path) -> dict:
    # The file's modification date stands in for the export date only when a
    # file is recorded for the first time; recorded dates are kept as written
    modified = dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    return {
        "file": path.name,
        "downloaded": modified,
        "sha256": sha256_of(path),
        "bytes": path.stat().st_size,
        "data_rows": count_data_rows(path),
    }


def main() -> None:
    files = sorted(RAW_DIR.glob(FILE_GLOB)) if RAW_DIR.is_dir() else []
    if not files:
        fail(
            f"no {FILE_GLOB} files in {RAW_DIR.relative_to(REPO_ROOT)}/. "
            "Export them from UPAg by hand first; see 'Getting the data' in README.md."
        )

    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    else:
        manifest = {**MANIFEST_HEADER, "files": []}
    recorded = {entry["file"]: entry for entry in manifest["files"]}

    on_disk = {path.name for path in files}
    missing = sorted(set(recorded) - on_disk)
    if missing:
        fail(f"listed in MANIFEST.json but not in data/raw/: {missing}")

    problems, added = [], []
    print(f"{'file':<48} {'data_rows':>10} {'bytes':>10}  status")
    for path in files:
        actual = describe(path)
        entry = recorded.get(path.name)
        if entry is None:
            manifest["files"].append(actual)
            added.append(path.name)
            status = "added to manifest"
        else:
            diffs = [k for k in ("sha256", "bytes", "data_rows") if entry.get(k) != actual[k]]
            status = "matches manifest" if not diffs else f"MISMATCH on {', '.join(diffs)}"
            if diffs:
                problems.append(f"{path.name}: {diffs} (manifest {[entry.get(k) for k in diffs]}, "
                                f"file {[actual[k] for k in diffs]})")
        print(f"{path.name:<48} {actual['data_rows']:>10,} {actual['bytes']:>10,}  {status}")

    total = sum(entry["data_rows"] for entry in manifest["files"])
    print(f"{'TOTAL (' + str(len(files)) + ' files)':<48} {total:>10,}")

    if problems:
        fail("raw files differ from MANIFEST.json:\n  " + "\n  ".join(problems))
    if added:
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Recorded {len(added)} new file(s) in {MANIFEST_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
