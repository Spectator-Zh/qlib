#!/usr/bin/env python3
"""给预测结果补上下一交易日实际涨跌幅。"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

from security_metadata import is_main_board_symbol, load_security_metadata
from trading_dates import DEFAULT_DAY_CALENDAR, load_trading_calendar, next_trading_day

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CSV_DIR = ROOT_DIR / "data" / "cn_day_csv"
INDEX_SPECS = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="给预测 CSV 补注下一交易日实际涨跌幅")
    parser.add_argument("--run-dir", help="训练输出目录；提供后会自动处理 pred.csv 和榜单 CSV")
    parser.add_argument("--pred-csv", help="直接指定一个预测 CSV")
    parser.add_argument("--csv-dir", default=str(DEFAULT_CSV_DIR))
    parser.add_argument("--calendar-path", default=str(DEFAULT_DAY_CALENDAR))
    parser.add_argument("--in-place", action="store_true", help="直接覆盖原始 CSV")
    return parser.parse_args()


def load_price_map(csv_dir: Path, symbols: set[str]) -> dict[str, dict[str, float]]:
    prices: dict[str, dict[str, float]] = {}
    for symbol in sorted(symbols):
        csv_path = csv_dir / f"{symbol.lower()}.csv"
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            prices[symbol.lower()] = {row["date"]: float(row["close"]) for row in reader if row.get("date") and row.get("close")}
    return prices


def float_or_none(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def sign_text(value: float | None) -> str:
    if value is None:
        return ""
    if value > 0:
        return "+"
    if value < 0:
        return "-"
    return "0"


def next_available_price_date(signal_date: str, price_map: dict[str, float]) -> str:
    future_dates = sorted(date for date in price_map if date > signal_date)
    return future_dates[0] if future_dates else ""


def build_index_rows(signal_date: str, prediction_date: str, prediction_date_source: str, csv_dir: Path, template: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for symbol, display_name in INDEX_SPECS:
        csv_path = csv_dir / f"{symbol}.csv"
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8", newline="") as fp:
            price_map = {row["date"]: float(row["close"]) for row in csv.DictReader(fp) if row.get("date") and row.get("close")}
        signal_close = price_map.get(signal_date)
        next_close = price_map.get(prediction_date)
        effective_prediction_date = prediction_date
        effective_source = prediction_date_source
        if next_close is None:
            fallback_prediction_date = next_available_price_date(signal_date, price_map)
            if fallback_prediction_date:
                effective_prediction_date = fallback_prediction_date
                effective_source = "next_available_price"
                next_close = price_map.get(fallback_prediction_date)
        actual_return = None
        if signal_close not in (None, 0.0) and next_close is not None:
            actual_return = (next_close / signal_close) - 1.0

        row = {key: "" for key in template}
        row["datetime"] = signal_date
        row["instrument"] = symbol
        row["name"] = display_name
        row["prediction_date"] = effective_prediction_date
        row["prediction_date_source"] = effective_source
        row["signal_close"] = f"{signal_close:.6f}" if signal_close is not None else ""
        row["next_close"] = f"{next_close:.6f}" if next_close is not None else ""
        row["actual_return_pct"] = f"{actual_return * 100:+.4f}" if actual_return is not None else ""
        row["__row_type"] = "index_row"
        rows.append(row)
    return rows


def annotate_rows(rows: list[dict[str, str]], prices: dict[str, dict[str, float]], calendar: list[str]) -> list[dict[str, str]]:
    annotated: list[dict[str, str]] = []
    for row in rows:
        symbol = row["instrument"].lower()
        signal_date = row["datetime"]
        prediction_date = row.get("prediction_date", "")
        prediction_date_source = row.get("prediction_date_source", "")
        if not prediction_date:
            prediction_date, prediction_date_source = next_trading_day(signal_date, calendar)

        price_map = prices.get(symbol, {})
        signal_close = price_map.get(signal_date)
        next_close = price_map.get(prediction_date)
        if next_close is None:
            fallback_prediction_date = next_available_price_date(signal_date, price_map)
            if fallback_prediction_date:
                prediction_date = fallback_prediction_date
                prediction_date_source = "next_available_price"
                next_close = price_map.get(prediction_date)
        actual_return = None
        if signal_close not in (None, 0.0) and next_close is not None:
            actual_return = (next_close / signal_close) - 1.0

        score = float_or_none(row.get("score"))
        score_sign = sign_text(score)
        return_sign = sign_text(actual_return)
        hit = ""
        if score_sign in {"+", "-"} and return_sign in {"+", "-"}:
            hit = "1" if score_sign == return_sign else "0"

        enriched = dict(row)
        enriched["prediction_date"] = prediction_date
        enriched["prediction_date_source"] = prediction_date_source
        enriched["signal_close"] = f"{signal_close:.6f}" if signal_close is not None else ""
        enriched["next_close"] = f"{next_close:.6f}" if next_close is not None else ""
        enriched["actual_return_pct"] = f"{actual_return * 100:+.4f}" if actual_return is not None else ""
        enriched["score_sign"] = score_sign
        enriched["hit"] = hit
        annotated.append(enriched)
    return annotated


def write_csv_file(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    fieldnames = [name for name in rows[0].keys() if not name.startswith("__")]
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def write_html_file(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    fieldnames = [name for name in rows[0].keys() if not name.startswith("__")]

    def render_row(row: dict[str, str], strong: bool = False, divider: bool = False) -> list[str]:
        tr_class = " class='divider'" if divider else ""
        row_lines = [f"<tr{tr_class}>"]
        for name in fieldnames:
            value = html.escape(str(row.get(name, "")))
            css = ""
            if name == "actual_return_pct":
                raw = float_or_none(row.get(name))
                if raw is not None:
                    if raw > 0:
                        css = "red"
                    elif raw < 0:
                        css = "green"
                    else:
                        css = "gray"
            class_attr = f" class='{css}'" if css and not divider else ""
            if strong or divider:
                row_lines.append(f"<td{class_attr}><strong>{value}</strong></td>")
            else:
                row_lines.append(f"<td{class_attr}>{value}</td>")
        row_lines.append("</tr>")
        return row_lines

    lines = [
        "<html><head><meta charset='utf-8'><style>",
        "body{font-family:Arial,sans-serif;padding:16px;}table{border-collapse:collapse;font-size:13px;}",
        "th,td{border:1px solid #ddd;padding:6px 8px;}th{background:#f5f5f5;position:sticky;top:0;}",
        ".red{color:#c62828;font-weight:700;}.green{color:#2e7d32;font-weight:700;}.gray{color:#666;}tr.divider td{background:#fff;color:#111;font-weight:700;text-align:center;letter-spacing:1px;border-top:2px solid #999;border-bottom:2px solid #999;}",
        "</style></head><body><table><thead><tr>",
    ]
    lines.extend(f"<th>{html.escape(name)}</th>" for name in fieldnames)
    lines.append("</tr></thead><tbody>")

    for row in rows:
        row_type = row.get("__row_type", "")
        if row_type == "divider":
            lines.extend(render_row(row, strong=True, divider=True))
        elif row_type in {"top20_summary", "bottom20_summary"}:
            lines.extend(render_row(row, strong=True))
        else:
            lines.extend(render_row(row))

    lines.append("</tbody></table></body></html>")
    path.write_text("".join(lines), encoding="utf-8")


def extend_top_bottom_csv(path: Path, csv_dir: Path) -> None:
    def make_summary_row(section_label: str, section_name: str, group: list[dict[str, str]], template: dict[str, str]) -> dict[str, str]:
        summary_row = {key: "" for key in template}
        if "name" in summary_row:
            summary_row["name"] = f"Summary ({section_label})"
        elif "instrument" in summary_row:
            summary_row["instrument"] = f"Summary ({section_label})"
        summary_row["__row_type"] = section_name

        returns: list[float] = []
        weighted_pairs: list[tuple[float, float]] = []
        for row in group:
            ret = float_or_none(row.get("actual_return_pct"))
            score = float_or_none(row.get("score"))
            if ret is None:
                continue
            returns.append(ret)
            if score is not None:
                weighted_pairs.append((score, ret))

        values: list[str] = []
        if returns:
            values.append(f"avg={sum(returns) / len(returns):.4f}%")
        if weighted_pairs:
            score_sum = sum(score for score, _ in weighted_pairs)
            if score_sum != 0:
                weighted = sum(score * ret for score, ret in weighted_pairs) / score_sum
                values.append(f"score_wavg={weighted:.4f}%")
        if "actual_return_pct" in summary_row:
            summary_row["actual_return_pct"] = " | ".join(values)
        return summary_row

    if path.name not in {
        "latest_day_mainboard_top20.csv",
        "latest_day_etf_top20.csv",
        "latest_day_st_top20.csv",
    }:
        return
    pred_path = path.with_name("pred.csv")
    if not pred_path.exists():
        return
    with pred_path.open("r", encoding="utf-8", newline="") as fp:
        pred_rows = list(csv.DictReader(fp))
    if not pred_rows:
        return
    latest_date = max(row["datetime"] for row in pred_rows if row.get("datetime"))
    rows = [row for row in pred_rows if row.get("datetime") == latest_date]
    if not rows:
        return

    metadata = load_security_metadata()

    def is_st_row(row: dict[str, str]) -> bool:
        return metadata.get(row["instrument"].lower(), {}).get("is_st") == "1"

    def is_etf_row(row: dict[str, str]) -> bool:
        return metadata.get(row["instrument"].lower(), {}).get("kind") == "etf"

    def is_mainboard_row(row: dict[str, str]) -> bool:
        symbol = row["instrument"].lower()
        return is_main_board_symbol(symbol) and metadata.get(symbol, {}).get("is_st") != "1"

    if path.name == "latest_day_st_top20.csv":
        score_rows = [row for row in rows if row.get("score") not in {"", None} and is_st_row(row)]
    elif path.name == "latest_day_etf_top20.csv":
        score_rows = [row for row in rows if row.get("score") not in {"", None} and is_etf_row(row)]
    else:
        score_rows = [row for row in rows if row.get("score") not in {"", None} and is_mainboard_row(row)]

    if not score_rows:
        return
    score_rows.sort(key=lambda row: float(row["score"]), reverse=True)
    top_rows = score_rows[:20]
    bottom_rows = list(reversed(score_rows[-20:]))

    compact_rows: list[dict[str, str]] = []
    separator_row: dict[str, str] | None = None
    top_template: dict[str, str] | None = None
    for idx, (section, group) in enumerate((("top20", top_rows), ("bottom20", bottom_rows))):
        group_compact: list[dict[str, str]] = []
        for row in group:
            compact = dict(row)
            compact.pop("score_sign", None)
            compact.pop("actual_return_sign", None)
            compact.pop("actual_return_color", None)
            compact.pop("prediction_date_source", None)
            compact.pop("section", None)
            compact["__row_type"] = section
            group_compact.append(compact)
            if separator_row is None:
                separator_row = {key: "" for key in compact}
                if "name" in separator_row:
                    separator_row["name"] = "========== BOTTOM20 =========="
                elif "instrument" in separator_row:
                    separator_row["instrument"] = "========== BOTTOM20 =========="
                for key in separator_row:
                    if key not in {"name", "instrument", "__row_type"}:
                        separator_row[key] = "=" * 10
                separator_row["__row_type"] = "divider"
        compact_rows.extend(group_compact)
        if group_compact:
            section_name = f"{section}_summary"
            compact_rows.append(make_summary_row(section, section_name, group_compact, group_compact[0]))
        if idx == 0 and separator_row is not None:
            compact_rows.append(dict(separator_row))

    if top_rows:
        index_rows = build_index_rows(
            signal_date=top_rows[0]["datetime"],
            prediction_date=top_rows[0].get("prediction_date", ""),
            prediction_date_source=top_rows[0].get("prediction_date_source", ""),
            csv_dir=csv_dir,
            template=top_rows[0],
        )
        insert_at = len(top_rows)
        compact_rows[insert_at:insert_at] = index_rows

    write_csv_file(path, compact_rows)
    write_html_file(path.with_suffix(".html"), compact_rows)


def process_csv(path: Path, prices: dict[str, dict[str, float]], calendar: list[str], in_place: bool) -> None:
    with path.open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if not rows:
        return
    annotated = annotate_rows(rows, prices, calendar)
    output_path = path if in_place else path.with_name(f"{path.stem}_annotated{path.suffix}")
    write_csv_file(output_path, annotated)
    write_html_file(output_path.with_suffix(".html"), annotated)
    print(f"annotated: {output_path}")


def main() -> None:
    args = parse_args()
    csv_dir = Path(args.csv_dir).expanduser().resolve()
    if not csv_dir.exists():
        raise SystemExit(f"csv dir not found: {csv_dir}")
    calendar_path = Path(args.calendar_path).expanduser().resolve()
    if not calendar_path.exists():
        raise SystemExit(f"calendar file not found: {calendar_path}")
    calendar = load_trading_calendar(calendar_path)

    targets: list[Path] = []
    if args.pred_csv:
        targets.append(Path(args.pred_csv).expanduser().resolve())
    if args.run_dir:
        run_dir = Path(args.run_dir).expanduser().resolve()
        targets.extend(
            [
                run_dir / "pred.csv",
                run_dir / "latest_day_mainboard_top20.csv",
                run_dir / "latest_day_etf_top20.csv",
                run_dir / "latest_day_st_top20.csv",
            ]
        )
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["annotated_pred_path"] = str((run_dir / "pred.csv").resolve() if args.in_place else (run_dir / "pred_annotated.csv").resolve())
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    targets = [path for path in targets if path.exists()]
    if not targets:
        raise SystemExit("no target csv found")

    symbols: set[str] = set()
    for path in targets:
        with path.open("r", encoding="utf-8", newline="") as fp:
            for row in csv.DictReader(fp):
                if row.get("instrument"):
                    symbols.add(row["instrument"].lower())

    prices = load_price_map(csv_dir, symbols)
    for path in targets:
        process_csv(path, prices, calendar, args.in_place)
        if args.in_place:
            extend_top_bottom_csv(path, csv_dir)


if __name__ == "__main__":
    main()
