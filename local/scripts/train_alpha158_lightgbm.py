#!/usr/bin/env python3
"""用本地通达信转出的 Qlib 日线库跑一个最小可用训练流程。

默认先从 all 股票池里随机抽样一部分标的，确认训练链路跑通后，
再把 `--universe-mode` 改成 `all` 或 `file` 做更大范围训练。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime
from pathlib import Path

import qlib
from qlib.contrib.model.double_ensemble import DEnsembleModel
from qlib.contrib.model.gbdt import LGBModel
from qlib.utils import init_instance_by_config

from security_metadata import DEFAULT_METADATA_CACHE, is_main_board_symbol, load_security_metadata
from trading_dates import DEFAULT_DAY_CALENDAR, load_trading_calendar, next_trading_day

ROOT_DIR = Path(__file__).resolve().parents[1]
DAY_URI = str(ROOT_DIR / "data" / "cn_day_qlib")
DEFAULT_INSTRUMENTS = Path(DAY_URI) / "instruments" / "all.txt"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "results"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Alpha158 + LightGBM 跑本地日线训练")
    parser.add_argument("--model", choices=["lightgbm", "doubleensemble"], default="lightgbm")
    parser.add_argument("--provider-uri", default=DAY_URI, help="Qlib 日线目录")
    parser.add_argument("--kernels", type=int, default=2, help="Qlib 数据准备并行度，越小越省内存")
    parser.add_argument(
        "--universe-mode",
        choices=["sample", "all", "file"],
        default="sample",
        help="股票池模式：sample=随机抽样，all=全市场，file=从自定义 instruments 文件读取",
    )
    parser.add_argument("--sample-size", type=int, default=200, help="sample 模式下抽样股票数")
    parser.add_argument("--universe-file", help="file 模式下的 instruments 文件路径")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--train-start", default="2023-01-01")
    parser.add_argument("--train-end", default="2024-12-31")
    parser.add_argument("--valid-start", default="2025-01-01")
    parser.add_argument("--valid-end", default="2025-12-31")
    parser.add_argument("--test-start", default="2026-01-01")
    parser.add_argument("--test-end", default="2026-04-30")
    parser.add_argument("--label-end", default="2026-04-30", help="handler end_time，通常等于测试集结束日")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--num-leaves", type=int, default=210)
    parser.add_argument("--learning-rate", type=float, default=0.2)
    parser.add_argument("--num-boost-round", type=int, default=1000)
    parser.add_argument(
        "--n-estimators",
        type=int,
        help="兼容别名，等同于 --num-boost-round；优先级低于 --num-boost-round",
    )
    parser.add_argument("--early-stopping-rounds", type=int, default=50)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--colsample-bytree", type=float, default=0.8879)
    parser.add_argument("--subsample", type=float, default=0.8789)
    parser.add_argument("--lambda-l1", type=float, default=205.6999)
    parser.add_argument("--lambda-l2", type=float, default=580.9768)
    parser.add_argument("--double-num-models", type=int, default=6)
    parser.add_argument("--double-enable-sr", action="store_true", help="DoubleEnsemble 启用 sample reweighting")
    parser.add_argument("--double-enable-fs", action="store_true", help="DoubleEnsemble 启用 feature selection")
    parser.add_argument("--double-alpha1", type=float, default=1.0)
    parser.add_argument("--double-alpha2", type=float, default=1.0)
    parser.add_argument("--double-bins-sr", type=int, default=10)
    parser.add_argument("--double-bins-fs", type=int, default=5)
    parser.add_argument("--double-decay", type=float, default=0.5)
    parser.add_argument(
        "--a-share-only",
        action="store_true",
        help="只保留常见 A 股普通股票代码，过滤 ETF、可转债、回购、指数等非股票标的",
    )
    parser.add_argument("--calendar-path", default=str(DEFAULT_DAY_CALENDAR), help="日线交易日历文件")
    parser.add_argument("--metadata-cache", default=str(DEFAULT_METADATA_CACHE), help="证券名称和分类缓存文件")
    parser.add_argument("--refresh-metadata", action="store_true", help="强制刷新在线证券元数据缓存")
    return parser.parse_args()


def load_instruments(path: Path) -> list[str]:
    symbols: list[str] = []
    if not path.exists():
        raise FileNotFoundError(f"instruments file not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.strip().split("\t")
            if len(parts) != 3:
                continue
            symbols.append(parts[0].lower())
    return symbols


def is_a_share_equity(symbol: str) -> bool:
    code = symbol.lower()
    sh_prefixes = ("sh600", "sh601", "sh603", "sh605", "sh688")
    sz_prefixes = ("sz000", "sz001", "sz002", "sz003", "sz300", "sz301")
    bj_prefixes = (
        "bj430",
        "bj831",
        "bj832",
        "bj833",
        "bj834",
        "bj835",
        "bj836",
        "bj837",
        "bj838",
        "bj839",
        "bj870",
        "bj871",
        "bj872",
        "bj873",
        "bj874",
        "bj875",
        "bj876",
        "bj877",
        "bj878",
        "bj879",
        "bj920",
    )
    return code.startswith(sh_prefixes + sz_prefixes + bj_prefixes)


def maybe_filter_a_share(symbols: list[str], enabled: bool) -> list[str]:
    if not enabled:
        return symbols
    return [s for s in symbols if is_a_share_equity(s)]


def is_main_board(symbol: str) -> bool:
    return is_main_board_symbol(symbol)


def is_etf(symbol: str) -> bool:
    code = symbol.lower()
    sh_etf = (
        "sh510",
        "sh511",
        "sh512",
        "sh513",
        "sh515",
        "sh516",
        "sh517",
        "sh518",
        "sh560",
        "sh561",
        "sh562",
        "sh563",
        "sh588",
    )
    return code.startswith(sh_etf + ("sz159",))


def is_st_name(name: str) -> bool:
    return "ST" in name.upper()


def resolve_universe(args: argparse.Namespace) -> tuple[str | list[str], str]:
    if args.universe_mode == "all":
        all_symbols = maybe_filter_a_share(load_instruments(DEFAULT_INSTRUMENTS), args.a_share_only)
        return all_symbols, "all_a_share" if args.a_share_only else "all"
    if args.universe_mode == "file":
        if not args.universe_file:
            raise ValueError("--universe-mode file 时必须提供 --universe-file")
        file_path = Path(args.universe_file)
        symbols = maybe_filter_a_share(load_instruments(file_path), args.a_share_only)
        return symbols, file_path.stem

    all_symbols = maybe_filter_a_share(load_instruments(DEFAULT_INSTRUMENTS), args.a_share_only)
    if args.sample_size <= 0:
        raise ValueError("--sample-size 必须大于 0")
    if args.sample_size > len(all_symbols):
        raise ValueError(f"--sample-size={args.sample_size} 超过可用标的数 {len(all_symbols)}")
    rng = random.Random(args.seed)
    symbols = sorted(rng.sample(all_symbols, args.sample_size))
    return symbols, f"sample_{args.sample_size}_seed{args.seed}"


def build_dataset_config(args: argparse.Namespace, instruments: str | list[str]) -> dict:
    return {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": {
                    "start_time": args.train_start,
                    "end_time": args.label_end,
                    "fit_start_time": args.train_start,
                    "fit_end_time": args.train_end,
                    "instruments": instruments,
                },
            },
            "segments": {
                "train": [args.train_start, args.train_end],
                "valid": [args.valid_start, args.valid_end],
                "test": [args.test_start, args.test_end],
            },
        },
    }


def build_model(args: argparse.Namespace) -> LGBModel | DEnsembleModel:
    num_boost_round = args.num_boost_round
    if args.n_estimators is not None:
        num_boost_round = args.n_estimators
    if args.model == "doubleensemble":
        config = {
            "class": "DEnsembleModel",
            "module_path": "qlib.contrib.model.double_ensemble",
            "kwargs": {
                "base_model": "gbm",
                "loss": "mse",
                "num_models": args.double_num_models,
                "enable_sr": args.double_enable_sr,
                "enable_fs": args.double_enable_fs,
                "alpha1": args.double_alpha1,
                "alpha2": args.double_alpha2,
                "bins_sr": args.double_bins_sr,
                "bins_fs": args.double_bins_fs,
                "decay": args.double_decay,
                "epochs": num_boost_round,
                "early_stopping_rounds": args.early_stopping_rounds,
                "colsample_bytree": args.colsample_bytree,
                "subsample": args.subsample,
                "num_leaves": args.num_leaves,
                "learning_rate": args.learning_rate,
                "lambda_l1": args.lambda_l1,
                "lambda_l2": args.lambda_l2,
                "max_depth": args.max_depth,
                "num_threads": args.num_threads,
            },
        }
        return init_instance_by_config(config)
    config = {
        "class": "LGBModel",
        "module_path": "qlib.contrib.model.gbdt",
        "kwargs": {
            "loss": "mse",
            "colsample_bytree": args.colsample_bytree,
            "subsample": args.subsample,
            "num_leaves": args.num_leaves,
            "learning_rate": args.learning_rate,
            "num_boost_round": num_boost_round,
            "early_stopping_rounds": args.early_stopping_rounds,
            "lambda_l1": args.lambda_l1,
            "lambda_l2": args.lambda_l2,
            "max_depth": args.max_depth,
            "num_threads": args.num_threads,
        },
    }
    return init_instance_by_config(config)


def save_rank_views(pred_path: Path, out_dir: Path, metadata: dict[str, dict[str, str]]) -> dict:
    all_rows: list[dict] = []
    latest_date = ""
    latest_rows: list[dict] = []

    with pred_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            all_rows.append(row)
            dt = row["datetime"]
            if dt > latest_date:
                latest_date = dt
                latest_rows = [row]
            elif dt == latest_date:
                latest_rows.append(row)

    def score_key(row: dict) -> float:
        return float(row["score"])

    latest_rows.sort(key=score_key, reverse=True)
    for row in latest_rows:
        meta = metadata.get(row["instrument"].lower(), {})
        row["name"] = meta.get("name", "")

    remaining_mainboard_top20 = [
        row
        for row in latest_rows
        if is_main_board(row["instrument"]) and metadata.get(row["instrument"].lower(), {}).get("is_st") != "1"
    ][:20]
    etf_top20 = [row for row in latest_rows if metadata.get(row["instrument"].lower(), {}).get("kind") == "etf"][:20]
    st_top20 = [row for row in latest_rows if metadata.get(row["instrument"].lower(), {}).get("is_st") == "1"][:20]

    def write_rows(path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=["datetime", "prediction_date", "prediction_date_source", "instrument", "name", "score"])
            writer.writeheader()
            writer.writerows(rows)

    latest_all_path = out_dir / "latest_day_all.csv"
    latest_mainboard_path = out_dir / "latest_day_mainboard_top20.csv"
    latest_etf_path = out_dir / "latest_day_etf_top20.csv"
    latest_st_path = out_dir / "latest_day_st_top20.csv"

    write_rows(latest_all_path, latest_rows)
    write_rows(latest_mainboard_path, remaining_mainboard_top20)
    write_rows(latest_etf_path, etf_top20)
    write_rows(latest_st_path, st_top20)

    return {
        "latest_date": latest_date,
        "latest_all_path": str(latest_all_path),
        "latest_mainboard_top20_path": str(latest_mainboard_path),
        "latest_etf_top20_path": str(latest_etf_path),
        "latest_st_top20_path": str(latest_st_path),
        "latest_count": len(latest_rows),
        "latest_mainboard_top20_count": len(remaining_mainboard_top20),
        "latest_etf_top20_count": len(etf_top20),
        "latest_st_top20_count": len(st_top20),
    }


def main() -> None:
    args = parse_args()
    provider_uri = str(Path(args.provider_uri).expanduser().resolve())
    provider_path = Path(provider_uri)
    if not provider_path.exists():
        raise SystemExit(f"provider uri not found: {provider_path}")
    calendar_path = Path(args.calendar_path).expanduser().resolve()
    if not calendar_path.exists():
        raise SystemExit(f"calendar file not found: {calendar_path}")
    qlib.init(provider_uri=provider_uri, region="cn", expression_cache=None, dataset_cache=None, kernels=args.kernels)
    calendar = load_trading_calendar(calendar_path)
    model_suffix = "double" if args.model == "doubleensemble" else "lightgbm"

    instruments, universe_name = resolve_universe(args)
    metadata_symbols = instruments if isinstance(instruments, list) else load_instruments(DEFAULT_INSTRUMENTS)
    dataset = init_instance_by_config(build_dataset_config(args, instruments))
    model = build_model(args)
    metadata_cache = Path(args.metadata_cache).expanduser().resolve()
    metadata = load_security_metadata(
        metadata_cache,
        refresh=args.refresh_metadata,
        symbols=metadata_symbols,
    )
    if not metadata:
        raise SystemExit("security metadata is empty; provide data or refresh metadata with network access")
    name_map = {symbol: row.get("name", "") for symbol, row in metadata.items()}

    train_df = dataset.prepare("train")
    valid_df = dataset.prepare("valid")
    test_df = dataset.prepare("test")

    run_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{universe_name}_{model_suffix}"
    out_dir = Path(args.output_dir) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"provider_uri: {provider_uri}")
    print(f"kernels: {args.kernels}")
    print(f"universe_mode: {args.universe_mode}")
    print(f"universe_name: {universe_name}")
    if isinstance(instruments, list):
        print(f"universe_size: {len(instruments)}")
        unique_instruments = sorted(set(instruments))
        (out_dir / "selected_instruments.txt").write_text("\n".join(unique_instruments) + "\n", encoding="utf-8")
    print(f"train shape: {train_df.shape}")
    print(f"valid shape: {valid_df.shape}")
    print(f"test shape: {test_df.shape}")

    model.fit(dataset)
    pred = model.predict(dataset, segment="test")

    pred_df = pred.rename("score").reset_index()
    pred_df["name"] = pred_df["instrument"].map(lambda s: name_map.get(str(s).lower(), ""))
    prediction_dates = pred_df["datetime"].map(lambda value: next_trading_day(str(value), calendar))
    pred_df["prediction_date"] = prediction_dates.map(lambda item: item[0])
    pred_df["prediction_date_source"] = prediction_dates.map(lambda item: item[1])
    pred_path = out_dir / "pred.csv"
    pred_df.to_csv(pred_path, index=False)

    head_path = out_dir / "pred_head.csv"
    pred_df.head(200).to_csv(head_path, index=False)

    summary = {
        "provider_uri": provider_uri,
        "model": args.model,
        "universe_mode": args.universe_mode,
        "universe_name": universe_name,
        "universe_size": len(instruments) if isinstance(instruments, list) else "all",
        "a_share_only": args.a_share_only,
        "train_shape": list(train_df.shape),
        "valid_shape": list(valid_df.shape),
        "test_shape": list(test_df.shape),
        "pred_rows": int(len(pred_df)),
        "output_dir": str(out_dir),
        "pred_path": str(pred_path),
        "calendar_path": str(Path(args.calendar_path).expanduser().resolve()),
        "metadata_cache": str(Path(args.metadata_cache).expanduser().resolve()),
    }
    summary.update(save_rank_views(pred_path, out_dir, metadata))
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"pred rows: {len(pred_df)}")
    print(f"pred saved: {pred_path}")
    print(f"latest_date: {summary['latest_date']}")
    print(f"latest mainboard top20: {summary['latest_mainboard_top20_path']}")
    print(f"latest etf top20: {summary['latest_etf_top20_path']}")
    print(f"latest st top20: {summary['latest_st_top20_path']}")
    print(pred_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
