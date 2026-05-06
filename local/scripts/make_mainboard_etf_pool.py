#!/usr/bin/env python3
"""从 all.txt 里筛出 A 股主板股票和 ETF。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from security_metadata import DEFAULT_METADATA_CACHE, is_main_board_symbol, load_security_metadata

ROOT_DIR = Path(__file__).resolve().parents[1]
LOCAL_ALL = ROOT_DIR / "data" / "cn_day_qlib" / "instruments" / "all.txt"
DEFAULT_OUT = ROOT_DIR / "instruments" / "mainboard_etf.txt"
DEFAULT_CSV_DIR = ROOT_DIR / "data" / "cn_day_csv"
DEFAULT_CALENDAR_OUT = ROOT_DIR / "instruments" / "day_mainboard_etf.txt"


@dataclass(frozen=True)
class Span:
    symbol: str
    start: str
    end: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成主板 + ETF 股票池")
    parser.add_argument("--all-file", default=str(LOCAL_ALL))
    parser.add_argument("--output-file", default=str(DEFAULT_OUT))
    parser.add_argument("--csv-dir", default=str(DEFAULT_CSV_DIR))
    parser.add_argument("--calendar-output-file", default=str(DEFAULT_CALENDAR_OUT))
    parser.add_argument("--metadata-cache", default=str(DEFAULT_METADATA_CACHE))
    parser.add_argument("--refresh-metadata", action="store_true", help="强制从在线数据源刷新证券元数据缓存")
    return parser.parse_args()


def read_spans(path: Path) -> list[Span]:
    rows: list[Span] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.strip().split("\t")
            if len(parts) != 3:
                continue
            rows.append(Span(parts[0].upper(), parts[1], parts[2]))
    return rows


def build_calendar(symbols: list[str], csv_dir: Path) -> list[str]:
    dates: set[str] = set()
    for symbol in symbols:
        csv_path = csv_dir / f"{symbol.lower()}.csv"
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8") as fp:
            next(fp, None)
            for line in fp:
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[1]:
                    dates.add(parts[1])
    return sorted(dates)


def main() -> None:
    args = parse_args()
    all_file = Path(args.all_file).expanduser().resolve()
    metadata_cache = Path(args.metadata_cache).expanduser().resolve()
    if not all_file.exists():
        raise SystemExit(f"all instruments file not found: {all_file}")

    rows = read_spans(all_file)
    if not rows:
        raise SystemExit(f"no instrument spans found in: {all_file}")
    csv_dir = Path(args.csv_dir).expanduser().resolve()
    if not csv_dir.exists():
        raise SystemExit(f"csv dir not found: {csv_dir}")
    metadata = load_security_metadata(
        metadata_cache,
        refresh=args.refresh_metadata,
        symbols=[row.symbol.lower() for row in rows],
    )
    if not metadata:
        raise SystemExit("security metadata is empty; provide data or refresh metadata with network access")

    filtered: list[Span] = []
    missing_metadata = 0
    for row in rows:
        meta = metadata.get(row.symbol.lower())
        if not meta:
            missing_metadata += 1
            continue
        if meta["kind"] == "etf":
            filtered.append(row)
            continue
        if is_main_board_symbol(row.symbol) and meta["kind"] == "stock":
            filtered.append(row)

    out = Path(args.output_file).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{r.symbol}\t{r.start}\t{r.end}\n" for r in filtered), encoding="utf-8")

    calendar_output = Path(args.calendar_output_file).expanduser().resolve()
    calendar_output.parent.mkdir(parents=True, exist_ok=True)
    calendar_dates = build_calendar([r.symbol for r in filtered], csv_dir)
    if not calendar_dates:
        raise SystemExit(f"no calendar dates built from csv dir: {csv_dir}")
    calendar_output.write_text("\n".join(calendar_dates) + "\n", encoding="utf-8")

    unique_symbols = len({r.symbol for r in filtered})
    mainboard_symbols = len({r.symbol for r in filtered if is_main_board_symbol(r.symbol)})
    etf_symbols = len({r.symbol for r in filtered if metadata[r.symbol.lower()]["kind"] == "etf"})

    print(f"rows: {len(filtered)}")
    print(f"unique_symbols: {unique_symbols}")
    print(f"mainboard_symbols: {mainboard_symbols}")
    print(f"etf_symbols: {etf_symbols}")
    print(f"missing_metadata_symbols: {missing_metadata}")
    print(f"output_file: {out}")
    print(f"calendar_output_file: {calendar_output}")
    print(f"calendar_last_date: {calendar_dates[-1]}")


if __name__ == "__main__":
    main()
