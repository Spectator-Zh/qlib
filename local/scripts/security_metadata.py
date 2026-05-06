#!/usr/bin/env python3
"""在线获取并缓存证券基础元数据。"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_METADATA_CACHE = ROOT_DIR / "instruments" / "security_metadata.csv"
DEFAULT_INSTRUMENTS_FILE = ROOT_DIR / "data" / "cn_day_qlib" / "instruments" / "all.txt"
TENCENT_URL = "https://qt.gtimg.cn/q="
SINA_URL = "https://hq.sinajs.cn/list="
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://gu.qq.com/",
}


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().lower()


def infer_market(code: str, market_id: int | str | None = None) -> str:
    text = str(code).strip()
    if text.startswith(("4", "8", "9")):
        return "bj"
    if text.startswith("92"):
        return "bj"
    if str(market_id) == "1":
        return "sh"
    if str(market_id) == "0":
        return "sz"
    if text.startswith(("5", "6")):
        return "sh"
    if text.startswith(("0", "1", "2", "3")):
        return "sz"
    return ""


def make_symbol(code: str, market_id: int | str | None = None) -> str:
    market = infer_market(code, market_id)
    return f"{market}{code}".lower() if market else code.lower()


def is_st_name(name: str) -> bool:
    text = name.upper().replace(" ", "")
    return "ST" in text


def is_main_board_symbol(symbol: str) -> bool:
    code = normalize_symbol(symbol)
    return code.startswith(("sh600", "sh601", "sh603", "sh605", "sz000", "sz001"))


def is_etf_symbol(symbol: str) -> bool:
    code = normalize_symbol(symbol)
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


def classify_board(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if is_etf_symbol(code):
        return "etf"
    if is_main_board_symbol(code):
        return "mainboard"
    if code.startswith("sh688"):
        return "star"
    if code.startswith(("sz300", "sz301")):
        return "gem"
    if code.startswith("bj"):
        return "bse"
    return "other"


def read_instrument_symbols(path: Path) -> list[str]:
    symbols: list[str] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.strip().split("\t")
            if len(parts) != 3:
                continue
            symbols.append(parts[0].lower())
    return symbols


def _chunked(symbols: list[str], size: int) -> Iterable[list[str]]:
    for idx in range(0, len(symbols), size):
        yield symbols[idx : idx + size]


def _fetch_text(url: str, symbols: list[str], encoding: str, referer: str) -> str:
    req = Request(url + ",".join(symbols), headers={**COMMON_HEADERS, "Referer": referer})
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode(encoding, errors="ignore")


def _parse_tencent_quotes(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        if not left.startswith("v_s_"):
            continue
        symbol = normalize_symbol(left[4:])
        payload = right.strip().strip(";").strip('"')
        parts = payload.split("~")
        if len(parts) < 3:
            continue
        name = parts[1].strip()
        if not name:
            continue
        category = parts[9].strip() if len(parts) > 9 else ""
        result[symbol] = {"name": name, "category": category, "source": "tencent_quote"}
    return result


def _parse_sina_quotes(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        if not left.startswith("var hq_str_"):
            continue
        symbol = normalize_symbol(left[len("var hq_str_") :])
        payload = right.strip().strip(";").strip('"')
        if not payload:
            continue
        name = payload.split(",", 1)[0].strip()
        if not name:
            continue
        result[symbol] = {"name": name, "category": "", "source": "sina_quote"}
    return result


def fetch_online_security_metadata(symbols: list[str] | None = None) -> dict[str, dict[str, str]]:
    target_symbols = sorted({normalize_symbol(symbol) for symbol in (symbols or read_instrument_symbols(DEFAULT_INSTRUMENTS_FILE))})
    metadata: dict[str, dict[str, str]] = {}
    for batch in _chunked(target_symbols, 200):
        quotes = _parse_tencent_quotes(_fetch_text(TENCENT_URL, [f"s_{symbol}" for symbol in batch], "gbk", "https://gu.qq.com/"))
        missing = [symbol for symbol in batch if symbol not in quotes]
        if missing:
            quotes.update(_parse_sina_quotes(_fetch_text(SINA_URL, missing, "gbk", "https://finance.sina.com.cn/")))
        for symbol in batch:
            quote = quotes.get(symbol)
            if not quote:
                continue
            code = symbol[-6:]
            kind = "etf" if "ETF" in quote.get("category", "").upper() or is_etf_symbol(symbol) else "stock"
            board = "etf" if kind == "etf" else classify_board(symbol)
            name = quote["name"]
            metadata[symbol] = {
                "symbol": symbol,
                "code": code,
                "market": symbol[:2],
                "name": name,
                "kind": kind,
                "board": board,
                "is_st": "1" if is_st_name(name) else "0",
                "source": quote.get("source", ""),
            }

    return metadata


def write_security_metadata(path: Path, metadata: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["symbol", "code", "market", "name", "kind", "board", "is_st", "source", "updated_at"]
    updated_at = datetime.now(timezone.utc).isoformat()
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for symbol in sorted(metadata):
            row = dict(metadata[symbol])
            row["updated_at"] = updated_at
            writer.writerow(row)


def read_security_metadata(path: Path) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            symbol = normalize_symbol(row.get("symbol", ""))
            if not symbol:
                continue
            metadata[symbol] = {
                "symbol": symbol,
                "code": row.get("code", ""),
                "market": row.get("market", ""),
                "name": row.get("name", ""),
                "kind": row.get("kind", ""),
                "board": row.get("board", ""),
                "is_st": row.get("is_st", "0"),
                "source": row.get("source", ""),
            }
    return metadata


def load_security_metadata(
    path: Path | None = None,
    refresh: bool = False,
    symbols: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    cache_path = path or DEFAULT_METADATA_CACHE
    if cache_path.exists() and not refresh:
        return read_security_metadata(cache_path)
    metadata = fetch_online_security_metadata(symbols=symbols)
    write_security_metadata(cache_path, metadata)
    return metadata
