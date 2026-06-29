"""本地数据源模式下的股票标的池解析。"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from app.config import settings

logger = logging.getLogger(__name__)

# 本地维表不可用时使用的小型展示集合，避免同步入口得到空标的池。
DEMO_SYMBOLS = [
    "600000.SH",
    "600036.SH",
    "600519.SH",
    "601318.SH",
    "601398.SH",
    "000001.SZ",
    "000333.SZ",
    "000651.SZ",
    "000858.SZ",
    "002594.SZ",
]


def _symbols_from_parquet(path: Path) -> set[str]:
    """从包含 symbol 列的 Parquet 文件读取股票代码。"""
    if not path.exists():
        return set()
    try:
        df = pl.read_parquet(path, columns=["symbol"])
    except Exception as e:  # noqa: BLE001
        logger.warning("读取本地标的文件失败 %s: %s", path, e)
        return set()
    if df.is_empty() or "symbol" not in df.columns:
        return set()
    return {
        str(symbol).strip().upper()
        for symbol in df["symbol"].to_list()
        if str(symbol or "").strip()
    }


def resolve_local_stock_universe(
    data_dir: Path | None = None,
    *,
    include_watchlist: bool = True,
    include_demo_when_empty: bool = True,
) -> list[str]:
    """解析本地 A 股标的池，优先使用第三个项目同步出的 instruments 维表。"""
    root = Path(data_dir or settings.data_dir)
    symbols = _symbols_from_parquet(root / "instruments" / "instruments.parquet")

    if include_watchlist:
        symbols.update(_symbols_from_parquet(root / "user_data" / "watchlist.parquet"))

    if include_demo_when_empty and not symbols:
        symbols.update(DEMO_SYMBOLS)

    return sorted(symbols)
