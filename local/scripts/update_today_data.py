#!/usr/bin/env python3
"""用腾讯历史日 K 把本地 CSV / Qlib 补到最新交易日。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from security_metadata import is_etf_symbol
from trading_dates import DEFAULT_DAY_CALENDAR

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CSV_DIR = ROOT_DIR / "data" / "cn_day_csv"
DEFAULT_QLIB_DIR = ROOT_DIR / "data" / "cn_day_qlib"
DEFAULT_DUMP_SCRIPT = ROOT_DIR.parent / "scripts" / "dump_bin.py"
DEFAULT_PYTHON = Path(sys.executable)
TENCENT_HISTORY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://gu.qq.com/",
}
DEFAULT_REFERENCE_SYMBOL = "sh000001"
DEFAULT_INITIAL_DATE = "2005-01-01"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用腾讯历史日 K 补齐本地 CSV 和 Qlib")
    parser.add_argument("--csv-dir", default=str(DEFAULT_CSV_DIR))
    parser.add_argument("--qlib-dir", default=str(DEFAULT_QLIB_DIR))
    parser.add_argument("--calendar-path", default=str(DEFAULT_DAY_CALENDAR))
    parser.add_argument("--dump-script", default=str(DEFAULT_DUMP_SCRIPT))
    parser.add_argument("--python-bin", default=str(DEFAULT_PYTHON))
    parser.add_argument("--limit", type=int, help="仅处理前 N 个标的，调试用")
    parser.add_argument("--skip-qlib-update", action="store_true", help="只更新 CSV，不更新 Qlib bin")
    parser.add_argument("--replace-latest", action="store_true", help="本地已到最新日时，允许覆盖最新一日")
    parser.add_argument("--reference-symbol", default=DEFAULT_REFERENCE_SYMBOL, help="用于判断全市场最新交易日的参考标的")
    parser.add_argument("--initial-date", default=DEFAULT_INITIAL_DATE, help="参考标的不可用时的初始日期")
    return parser.parse_args()


def iter_symbols(csv_dir: Path, limit: int | None = None) -> list[str]:
    symbols = sorted(path.stem.lower() for path in csv_dir.glob("*.csv"))
    if limit is not None:
        symbols = symbols[:limit]
    return symbols


def prompt_yes_no(message: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{message} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def fetch_text(url: str, headers: dict[str, str], encoding: str = "utf-8") -> str:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode(encoding, errors="ignore")


def read_last_date(csv_path: Path) -> str | None:
    last_date: str | None = None
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            last_date = row["date"]
    return last_date


def write_full_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "amount", "factor"])
        writer.writeheader()
        writer.writerows(rows)


def read_full_csv(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))


def is_index_symbol(symbol: str) -> bool:
    code = symbol.lower()
    return code.startswith(("sh000", "sz399"))


def price_scale_for_symbol(symbol: str) -> float:
    return 10.0 if is_etf_symbol(symbol) else 1.0


def volume_scale_for_symbol(symbol: str) -> int:
    return 1 if is_index_symbol(symbol) else 100


def estimate_amount(symbol: str, open_price: float, high_price: float, low_price: float, close_price: float, volume: int) -> float:
    base_price = (open_price + high_price + low_price + close_price) / 4.0
    return base_price * volume


def build_row(symbol: str, date: str, open_price: float, high_price: float, low_price: float, close_price: float, volume: int, amount: float) -> dict[str, str]:
    scale = price_scale_for_symbol(symbol)
    return {
        "symbol": symbol,
        "date": date,
        "open": f"{open_price * scale:.6f}",
        "high": f"{high_price * scale:.6f}",
        "low": f"{low_price * scale:.6f}",
        "close": f"{close_price * scale:.6f}",
        "volume": str(volume),
        "amount": f"{amount:.6f}",
        "factor": "1.000000",
    }


def parse_history_rows(symbol: str, start_date: str = "", end_date: str = "", limit: int = 2000) -> list[dict[str, str]]:
    try:
        url = TENCENT_HISTORY_URL + f"{symbol},day,,,{limit},qfq"
        text = fetch_text(url, TENCENT_HEADERS)
        payload = json.loads(text)
        data = payload.get("data", {})
        if not isinstance(data, dict):
            return []
        series = data.get(symbol, {})
        klines = series.get("qfqday") or series.get("day") or []
        rows: list[dict[str, str]] = []
        volume_scale = volume_scale_for_symbol(symbol)
        for item in klines:
            if len(item) < 6:
                continue
            trade_date = item[0]
            if start_date and trade_date <= start_date:
                continue
            if end_date and trade_date > end_date:
                continue
            open_price = float(item[1])
            close_price = float(item[2])
            high_price = float(item[3])
            low_price = float(item[4])
            volume = int(float(item[5]) * volume_scale)
            amount = estimate_amount(symbol, open_price, high_price, low_price, close_price, volume)
            rows.append(build_row(symbol, trade_date, open_price, high_price, low_price, close_price, volume, amount))
        if limit > 0 and len(rows) > limit:
            rows = rows[-limit:]
        return rows
    except Exception:
        return []


def latest_history_row(symbol: str) -> dict[str, str] | None:
    rows = parse_history_rows(symbol, limit=10)
    return rows[-1] if rows else None


def merge_rows(existing_rows: list[dict[str, str]], new_rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    if not new_rows:
        return existing_rows, [], "missing_history"
    by_date = {row["date"]: dict(row) for row in existing_rows}
    appended: list[dict[str, str]] = []
    replaced = False
    old_dates = {row["date"] for row in existing_rows}
    for row in new_rows:
        if row["date"] in old_dates:
            replaced = True
        else:
            appended.append(row)
        by_date[row["date"]] = dict(row)
    merged = sorted(by_date.values(), key=lambda row: row["date"])
    if appended and replaced:
        action = "appended_and_replaced"
    elif appended:
        action = "appended"
    elif replaced:
        action = "replaced"
    else:
        action = "unchanged"
    return merged, appended, action


def update_csv_file(csv_path: Path, target_end_date: str, replace_latest: bool) -> tuple[str, list[dict[str, str]]]:
    symbol = csv_path.stem.lower()
    rows = read_full_csv(csv_path)
    if not rows:
        new_rows = parse_history_rows(symbol, end_date=target_end_date)
        if not new_rows:
            return "missing_history", []
        write_full_csv(csv_path, new_rows)
        return "created", new_rows

    last_date = rows[-1]["date"]
    if last_date > target_end_date:
        return "skipped_old_date", []
    if last_date == target_end_date:
        if not replace_latest:
            return "already_latest", []
        latest_row = latest_history_row(symbol)
        if latest_row is None or latest_row["date"] != target_end_date:
            return "missing_history", []
        rows[-1] = latest_row
        write_full_csv(csv_path, rows)
        return "replaced", []

    history_rows = parse_history_rows(symbol, start_date=last_date, end_date=target_end_date)
    merged_rows, appended_rows, action = merge_rows(rows, history_rows)
    if action in {"appended", "appended_and_replaced", "replaced"}:
        write_full_csv(csv_path, merged_rows)
    return action, appended_rows


def run_dump_update(python_bin: Path, dump_script: Path, temp_dir: Path, qlib_dir: Path) -> None:
    cmd = [
        str(python_bin),
        str(dump_script),
        "dump_update",
        f"--data_path={temp_dir}",
        f"--qlib_dir={qlib_dir}",
        "--freq=day",
        "--symbol_field_name=symbol",
        "--date_field_name=date",
        "--exclude_fields=symbol,date",
    ]
    subprocess.run(cmd, check=True)


def main() -> None:
    args = parse_args()
    csv_dir = Path(args.csv_dir).expanduser().resolve()
    qlib_dir = Path(args.qlib_dir).expanduser().resolve()
    calendar_path = Path(args.calendar_path).expanduser().resolve()
    dump_script = Path(args.dump_script).expanduser().resolve()
    python_bin = Path(args.python_bin).expanduser().resolve()

    if not csv_dir.exists():
        raise SystemExit(f"csv dir not found: {csv_dir}")
    if not calendar_path.exists():
        raise SystemExit(f"calendar file not found: {calendar_path}")
    if not args.skip_qlib_update and not qlib_dir.exists():
        raise SystemExit(f"qlib dir not found: {qlib_dir}")
    if not args.skip_qlib_update and not dump_script.exists():
        raise SystemExit(f"dump script not found: {dump_script}")
    if not python_bin.exists():
        raise SystemExit(f"python bin not found: {python_bin}")

    symbols = iter_symbols(csv_dir, args.limit)
    if not symbols:
        raise SystemExit(f"no csv files found under: {csv_dir}")

    calendar = [line.strip() for line in calendar_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    old_last_date = calendar[-1]
    reference_row = latest_history_row(args.reference_symbol.lower())
    if reference_row is None:
        target_latest_date = args.initial_date
        print(f"reference_symbol_missing: {args.reference_symbol.lower()}, fallback_to_initial_date: {target_latest_date}")
    else:
        target_latest_date = reference_row["date"]
    touched_rows: dict[str, list[dict[str, str]]] = {}
    action_counts = {
        "appended": 0,
        "appended_and_replaced": 0,
        "replaced": 0,
        "created": 0,
        "skipped_old_date": 0,
        "missing_history": 0,
        "unchanged": 0,
        "already_latest": 0,
    }

    for symbol in symbols:
        action, appended_rows = update_csv_file(csv_dir / f"{symbol}.csv", target_latest_date, args.replace_latest)
        action_counts[action] += 1
        if appended_rows:
            touched_rows[symbol] = appended_rows

    print(f"symbols: {len(symbols)}")
    print(f"calendar_last_date_before: {old_last_date}")
    print(f"reference_symbol: {args.reference_symbol.lower()}")
    print(f"target_latest_date: {target_latest_date}")
    print(f"initial_date: {args.initial_date}")
    print(f"actions: {action_counts}")

    if (
        action_counts["already_latest"] > 0
        and not args.replace_latest
        and action_counts["appended"] == 0
        and action_counts["appended_and_replaced"] == 0
        and action_counts["created"] == 0
    ):
        if prompt_yes_no("local data is already at the latest date; replace the latest day with Tencent data?"):
            replace_counts = {"replaced": 0, "missing_history": 0}
            for symbol in symbols:
                action, _ = update_csv_file(csv_dir / f"{symbol}.csv", target_latest_date, True)
                if action in replace_counts:
                    replace_counts[action] += 1
            print(f"replace_latest_done: {replace_counts}")
        else:
            print("replace_latest_skipped: user declined")

    if args.skip_qlib_update:
        print("skip_qlib_update: true")
        return

    if not touched_rows:
        print("qlib_update_skipped: no appended rows")
        if action_counts["already_latest"] > 0 and not args.replace_latest:
            print("note: local data is already at latest date; rerun with --replace-latest if you want to overwrite the latest day.")
        else:
            print("note: 若只有同日替换，CSV 已更新；Qlib bin 同日覆盖仍不走 dump_update。")
        return

    with tempfile.TemporaryDirectory(prefix="local_day_update_") as tmp_name:
        temp_dir = Path(tmp_name)
        for symbol, rows in touched_rows.items():
            src = csv_dir / f"{symbol}.csv"
            dst = temp_dir / src.name
            write_full_csv(dst, rows)
        run_dump_update(python_bin, dump_script, temp_dir, qlib_dir)
    print(f"qlib_update_done: {len(touched_rows)} symbols")


if __name__ == "__main__":
    main()
