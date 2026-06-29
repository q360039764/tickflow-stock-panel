"""第一个项目内置的本地 A 股行情 provider。"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

import polars as pl

from app.config import settings
from app.data_providers.base import AssetType, ProviderCapabilities
from app.data_providers.local_market_codes import to_level1_code, to_panel_symbol
from app.data_providers.sina_realtime import fetch_sina_realtime
from app.data_providers.tdx_level1 import fetch_level1_minute

logger = logging.getLogger(__name__)


class LocalProjectsProvider:
    """内置 Sina 实时行情与通达信 Level1 分钟 K 线的数据源。"""

    name = "local_projects"
    capabilities = ProviderCapabilities(
        instruments=True,
        daily=True,
        adj_factor=False,
        minute=True,
        realtime=True,
        financial=False,
    )

    def get_instruments(self, asset_type: AssetType) -> pl.DataFrame:
        if asset_type != "stock":
            return pl.DataFrame()
        return get_level1_instruments()

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType,
    ) -> pl.DataFrame:
        if asset_type != "stock":
            return pl.DataFrame()
        return get_level1_daily(symbols, start_time, end_time)

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType,
    ) -> pl.DataFrame:
        return pl.DataFrame()

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType,
        freq: str = "1m",
    ) -> pl.DataFrame:
        if asset_type != "stock" or freq != "1m":
            return pl.DataFrame()
        return get_level1_minute(symbols, start_time, end_time)

    def get_realtime(
        self,
        universes: list[str] | None = None,
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        return get_sina_realtime(symbols=symbols)


def local_data_enabled() -> bool:
    """本地内置数据源总开关，默认启用。"""
    value = str(getattr(settings, "local_data_enabled", "true") or "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _date_bounds(start_time: datetime | None, end_time: datetime | None) -> tuple[date, date]:
    end = end_time.date() if end_time else date.today()
    start = start_time.date() if start_time else end
    return start, end


def _exchange_from_symbol_expr() -> pl.Expr:
    """根据面板 symbol 推导交易所字段。"""
    return (
        pl.when(pl.col("symbol").str.ends_with(".SH")).then(pl.lit("SH"))
        .when(pl.col("symbol").str.ends_with(".SZ")).then(pl.lit("SZ"))
        .when(pl.col("symbol").str.ends_with(".BJ")).then(pl.lit("BJ"))
        .otherwise(None)
    )


def get_level1_instruments() -> pl.DataFrame:
    """读取第一个项目自己的股票标的维表。"""
    path = Path(settings.data_dir) / "instruments" / "instruments.parquet"
    if not path.exists():
        logger.warning("本地 instruments 不存在: %s", path)
        return pl.DataFrame()
    try:
        df = pl.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取本地 instruments 失败 %s: %s", path, e)
        return pl.DataFrame()
    if df.is_empty():
        return df

    if "symbol" not in df.columns and "code" in df.columns:
        df = df.with_columns(
            pl.col("code").cast(pl.Utf8).map_elements(to_panel_symbol, return_dtype=pl.Utf8).alias("symbol")
        )
    if "symbol" not in df.columns:
        return pl.DataFrame()

    df = df.with_columns([
        pl.col("symbol").cast(pl.Utf8).str.to_uppercase().alias("symbol"),
        pl.col("symbol").cast(pl.Utf8).map_elements(to_level1_code, return_dtype=pl.Utf8).alias("code"),
        pl.lit("local_builtin").alias("source"),
        pl.lit("stock").alias("asset_type"),
    ])
    if "name" not in df.columns:
        df = df.with_columns(pl.col("symbol").alias("name"))
    if "exchange" not in df.columns:
        df = df.with_columns(_exchange_from_symbol_expr().alias("exchange"))
    if "as_of" not in df.columns:
        df = df.with_columns(pl.lit(date.today()).cast(pl.Date).alias("as_of"))

    keep = [
        "symbol", "name", "code", "exchange", "asset_type", "source", "as_of",
        "region", "type", "listing_date", "total_shares", "float_shares", "tick_size",
        "limit_up", "limit_down",
        "industry_l1_name", "industry_l2_name", "industry_l3_name", "industry_l4_name",
    ]
    keep = [col for col in keep if col in df.columns]
    return df.select(keep).drop_nulls("symbol").unique(subset=["symbol"], keep="last").sort("symbol")


def _read_cached_daily(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    """从第一个项目已有日 K 缓存读取数据。"""
    daily_dir = Path(settings.data_dir) / "kline_daily"
    if not daily_dir.exists():
        return pl.DataFrame()
    pattern = str(daily_dir / "**" / "*.parquet")
    try:
        df = pl.scan_parquet(pattern).filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        ).collect()
    except Exception:
        return pl.DataFrame()
    keep = [col for col in ["symbol", "date", "open", "high", "low", "close", "volume", "amount"] if col in df.columns]
    return df.select(keep).sort(["symbol", "date"]) if keep else pl.DataFrame()


def get_level1_daily(
    symbols: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> pl.DataFrame:
    """返回日 K，优先使用第一个项目缓存，小范围缺失时由内置分钟 K 聚合。"""
    normalized = sorted({to_panel_symbol(symbol) for symbol in symbols if str(symbol or "").strip()})
    if not normalized:
        return pl.DataFrame()
    start, end = _date_bounds(start_time, end_time)

    cached = _read_cached_daily(normalized, start, end)
    if not cached.is_empty():
        return cached

    # 大范围历史日 K 不在 Level1 按需聚合中展开，避免一次请求触发大量 TCP 下载。
    if len(normalized) > 20 or (end - start).days > 10:
        logger.warning("跳过大范围 Level1 日 K 聚合: symbols=%d, range=%s~%s", len(normalized), start, end)
        return pl.DataFrame()

    minute_start = datetime.combine(start, time(9, 15))
    minute_end = datetime.combine(end, time(15, 5))
    minute = get_level1_minute(normalized, minute_start, minute_end)
    if minute.is_empty():
        return pl.DataFrame()
    daily = minute.with_columns(pl.col("datetime").dt.date().alias("date")).group_by(["symbol", "date"]).agg([
        pl.col("open").sort_by("datetime").first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").sort_by("datetime").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
        pl.col("amount").sum().alias("amount"),
    ])
    return daily.select(["symbol", "date", "open", "high", "low", "close", "volume", "amount"]).sort(["symbol", "date"])


def get_level1_minute(
    symbols: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> pl.DataFrame:
    """通过内置通达信 Level1 TCP 协议获取 1 分钟 K。"""
    if not local_data_enabled():
        return pl.DataFrame()
    return fetch_level1_minute(symbols, start_time, end_time)


def get_sina_realtime(symbols: list[str] | None = None) -> pl.DataFrame:
    """通过内置 Sina HTTP 接口获取实时行情。"""
    if not local_data_enabled():
        return pl.DataFrame()
    return fetch_sina_realtime(symbols=symbols)


def realtime_records_from_df(df: pl.DataFrame) -> list[dict]:
    """将 provider DataFrame 转为 QuoteService 使用的记录格式。"""
    if df.is_empty():
        return []
    records: list[dict] = []
    for row in df.iter_rows(named=True):
        records.append({
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "last_price": row.get("last_price") or row.get("close"),
            "prev_close": row.get("prev_close"),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "volume": row.get("volume"),
            "amount": row.get("amount"),
            "change_pct": row.get("change_pct"),
            "change_amount": row.get("change_amount"),
            "amplitude": row.get("amplitude"),
            "turnover_rate": row.get("turnover_rate"),
            "timestamp": row.get("timestamp"),
            "session": row.get("session"),
        })
    return records
