#!/usr/bin/env python3
"""修复 ETF 最新交易日价格尺度。"""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path

from security_metadata import is_etf_symbol

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CSV_DIR = ROOT_DIR / "data" / "cn_day_csv"
DEFAULT_QLIB_DIR = ROOT_DIR / "data" / "cn_day_qlib"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 ETF 最新交易日的价格字段乘回 10 倍")
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--csv-dir", default=str(DEFAULT_CSV_DIR))
    parser.add_argument("--qlib-dir", default=str(DEFAULT_QLIB_DIR))
    return parser.parse_args()


def fix_csv_file(path: Path, target_date: str) -> bool:
    if not is_etf_symbol(path.stem):
        return False
    with path.open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if len(rows) < 2 or rows[-1]["date"] != target_date:
        return False
    prev_close = float(rows[-2]["close"])
    curr_close = float(rows[-1]["close"])
    if curr_close <= 0 or prev_close / curr_close < 5:
        return False
    for field in ("open", "high", "low", "close"):
        rows[-1][field] = f"{float(rows[-1][field]) * 10.0:.6f}"
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "amount", "factor"])
        writer.writeheader()
        writer.writerows(rows)
    return True


def patch_last_float(bin_path: Path, multiplier: float) -> None:
    with bin_path.open("r+b") as fp:
        fp.seek(-4, 2)
        raw = fp.read(4)
        value = struct.unpack("<f", raw)[0]
        fp.seek(-4, 2)
        fp.write(struct.pack("<f", value * multiplier))


def fix_qlib_bins(qlib_dir: Path, symbol: str) -> None:
    feature_dir = qlib_dir / "features" / symbol.lower()
    for field in ("open", "high", "low", "close"):
        patch_last_float(feature_dir / f"{field}.day.bin", 10.0)


def main() -> None:
    args = parse_args()
    csv_dir = Path(args.csv_dir).expanduser().resolve()
    qlib_dir = Path(args.qlib_dir).expanduser().resolve()
    fixed = 0
    for csv_path in sorted(csv_dir.glob("*.csv")):
        if fix_csv_file(csv_path, args.target_date):
            fix_qlib_bins(qlib_dir, csv_path.stem)
            fixed += 1
    print(f"fixed_etf_symbols: {fixed}")
    print(f"target_date: {args.target_date}")


if __name__ == "__main__":
    main()
