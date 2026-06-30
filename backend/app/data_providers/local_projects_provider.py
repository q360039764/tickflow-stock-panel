"""第一个项目内置的本地 A 股行情 provider。"""
from __future__ import annotations

import logging
import math
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx
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


def _finite_float(value) -> float | None:
    """将本地行情数值转为有限浮点数，过滤空值、NaN 和无穷大。"""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_price(value) -> float | None:
    """解析价格字段，0、负数和非法数值视为空值。"""
    parsed = _finite_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _eastmoney_secid(symbol: str) -> str | None:
    """将面板股票代码转换为 Eastmoney secid。"""
    panel_symbol = to_panel_symbol(symbol)
    code = to_level1_code(panel_symbol)
    if not code:
        return None
    if panel_symbol.endswith(".SH"):
        return f"1.{code}"
    if panel_symbol.endswith((".SZ", ".BJ")):
        return f"0.{code}"
    return None


def _compact_date(value: date) -> str:
    """生成 Eastmoney 历史接口使用的 YYYYMMDD 日期字符串。"""
    return value.strftime("%Y%m%d")


def _parse_eastmoney_kline(symbol: str, line: str) -> dict | None:
    """解析 Eastmoney kline 单行文本，输出项目日 K 标准字段。"""
    parts = str(line or "").split(",")
    if len(parts) < 7:
        return None
    try:
        trade_date = datetime.strptime(parts[0], "%Y-%m-%d").date()
    except ValueError:
        return None
    open_price = _positive_price(parts[1])
    close_price = _positive_price(parts[2])
    high_price = _positive_price(parts[3])
    low_price = _positive_price(parts[4])
    if None in (open_price, close_price, high_price, low_price):
        return None
    return {
        "symbol": symbol,
        "date": trade_date,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": _finite_float(parts[5]) or 0.0,
        "amount": _finite_float(parts[6]) or 0.0,
    }


def _quote_trade_date(row: dict) -> date | None:
    """从 Sina 实时行情中提取交易日，避免用系统日期误造非交易日 K 线。"""
    raw_date = row.get("date")
    if raw_date:
        if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
            return raw_date
        try:
            return datetime.fromisoformat(str(raw_date)[:10]).date()
        except ValueError:
            pass

    raw_ts = row.get("timestamp")
    if raw_ts:
        if isinstance(raw_ts, datetime):
            return raw_ts.date()
        try:
            return datetime.fromisoformat(str(raw_ts).replace("T", " ")).date()
        except ValueError:
            return None
    return None


def _read_sina_daily(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    """用 Sina 实时行情生成当日/最新交易日日 K，作为 K 线页的首选实时来源。"""
    quotes = fetch_sina_realtime(symbols=symbols)
    if quotes.is_empty():
        return pl.DataFrame()

    records: list[dict] = []
    for row in quotes.iter_rows(named=True):
        open_price = _positive_price(row.get("open"))
        high_price = _positive_price(row.get("high"))
        low_price = _positive_price(row.get("low"))
        close_price = _positive_price(row.get("last_price") if row.get("last_price") is not None else row.get("close"))
        trade_date = _quote_trade_date(row)
        if not row.get("symbol") or trade_date is None or None in (open_price, high_price, low_price, close_price):
            continue
        records.append({
            "symbol": row.get("symbol"),
            "date": trade_date,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": _finite_float(row.get("volume")) or 0.0,
            "amount": _finite_float(row.get("amount")) or 0.0,
        })
    if not records:
        return pl.DataFrame()

    latest_quote_date = max(r["date"] for r in records)
    if start == end == date.today():
        filtered = [r for r in records if r["date"] == latest_quote_date]
    else:
        filtered = [r for r in records if start <= r["date"] <= end]
    if not filtered:
        return pl.DataFrame()
    return (
        pl.DataFrame(filtered)
        .with_columns(pl.col("date").cast(pl.Date))
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
        .unique(subset=["symbol", "date"], keep="last")
        .sort(["symbol", "date"])
    )


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


def _read_eastmoney_daily(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    """从 Eastmoney 公共历史日 K 接口补齐单股历史数据。"""
    if not local_data_enabled() or not symbols or start > end:
        return pl.DataFrame()
    records: list[dict] = []
    headers = {"User-Agent": settings.ai_user_agent, "Referer": "https://quote.eastmoney.com"}
    timeout = float(getattr(settings, "sina_http_timeout_s", 8.0) or 8.0)
    base_url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    fields = "f51,f52,f53,f54,f55,f56,f57"
    with httpx.Client(timeout=timeout, headers=headers) as client:
        for symbol in symbols:
            secid = _eastmoney_secid(symbol)
            if not secid:
                continue
            params = {
                "secid": secid,
                "klt": "101",
                "fqt": "0",
                "beg": _compact_date(start),
                "end": _compact_date(end),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": fields,
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "_": str(int(datetime.now().timestamp() * 1000) + random.randint(0, 999)),
            }
            try:
                resp = client.get(base_url, params=params)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Eastmoney 历史日 K 获取失败 %s: %s", symbol, exc)
                continue
            klines = ((payload.get("data") or {}).get("klines") or []) if isinstance(payload, dict) else []
            for line in klines:
                item = _parse_eastmoney_kline(to_panel_symbol(symbol), line)
                if item and start <= item["date"] <= end:
                    records.append(item)
    if not records:
        return pl.DataFrame()
    return (
        pl.DataFrame(records)
        .with_columns(pl.col("date").cast(pl.Date))
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
        .unique(subset=["symbol", "date"], keep="last")
        .sort(["symbol", "date"])
    )


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
    sina_daily = _read_sina_daily(normalized, start, end)
    cached_dates = set()
    if not cached.is_empty() and "date" in cached.columns:
        cached_dates = {value for value in cached["date"].to_list()}
    need_history = not cached_dates or len(cached_dates) < max(3, min((end - start).days // 2, 20))
    eastmoney_daily = _read_eastmoney_daily(normalized, start, end) if need_history else pl.DataFrame()
    frames = [df for df in (cached, eastmoney_daily, sina_daily) if not df.is_empty()]
    if frames:
        return (
            pl.concat(frames, how="diagonal_relaxed")
            .unique(subset=["symbol", "date"], keep="last")
            .sort(["symbol", "date"])
        )

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
