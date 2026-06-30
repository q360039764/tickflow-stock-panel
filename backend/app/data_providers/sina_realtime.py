"""Sina HTTP 实时行情内置采集。"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from pathlib import Path

import httpx
import polars as pl

from app.config import settings
from app.data_providers.local_market_codes import to_level1_code, to_panel_symbol, to_sina_code

logger = logging.getLogger(__name__)

# Sina HTTP 接口字段顺序：名称、今开、昨收、当前价、最高、最低...。
# 第二项目 sina-real-time 的原始 CSV 与 sinajs.cn 返回内容均按此顺序保存。
SINA_FIELDS = [
    "name", "open", "prev_close", "last_price", "high", "low", "bid1_price", "ask1_price",
    "volume", "amount", "bid1_volume_l2", "bid1_price_l2", "bid2_volume", "bid2_price",
    "bid3_volume", "bid3_price", "bid4_volume", "bid4_price", "bid5_volume", "bid5_price",
    "ask1_volume_l2", "ask1_price_l2", "ask2_volume", "ask2_price", "ask3_volume", "ask3_price",
    "ask4_volume", "ask4_price", "ask5_volume", "ask5_price", "date", "time", "status",
]

_SINA_LINE_RE = re.compile(r"var\s+hq_str_([a-z]{2}\d{6})=\"(.*?)\";", re.IGNORECASE)


def _read_symbol_universe() -> list[str]:
    """从第一个项目自己的 instruments 维表读取全量股票代码。"""
    path = Path(settings.data_dir) / "instruments" / "instruments.parquet"
    if not path.exists():
        try:
            from app.services.local_universe import resolve_local_stock_universe
            return resolve_local_stock_universe(settings.data_dir)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取本地股票池失败: %s", e)
            return []
    try:
        df = pl.read_parquet(path, columns=["symbol"])
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 instruments 失败 %s: %s", path, e)
        return []
    if df.is_empty() or "symbol" not in df.columns:
        return []
    return [
        str(symbol).strip().upper()
        for symbol in df["symbol"].to_list()
        if str(symbol or "").strip()
    ]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    """按 Sina URL 长度限制拆分批量代码。"""
    size = max(1, int(size or 800))
    return [items[i:i + size] for i in range(0, len(items), size)]


def _parse_sina_response(text: str) -> list[dict]:
    """解析 Sina 批量响应文本，返回标准实时行情记录列表。"""
    records: list[dict] = []
    for match in _SINA_LINE_RE.finditer(text):
        parsed = _parse_sina_line(match.group(1), match.group(2))
        if parsed is not None:
            records.append(parsed)
    return records


def _fetch_sina_chunk(
    client: httpx.Client,
    url_template: str,
    chunk: list[str],
    min_chunk_size: int = 50,
) -> list[dict]:
    """获取单个 Sina 批次；失败时拆分为更小批次，避免整次全市场同步归零。"""
    if not chunk:
        return []
    try:
        url = url_template.format(codes=",".join(chunk))
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.content.decode("gb18030", errors="ignore")
        return _parse_sina_response(text)
    except Exception as e:  # noqa: BLE001
        if len(chunk) <= min_chunk_size:
            logger.warning("Sina HTTP 批次失败: size=%d, first=%s, error=%s", len(chunk), chunk[0], e)
            return []
        mid = len(chunk) // 2
        logger.warning("Sina HTTP 批次失败，拆分重试: size=%d, first=%s, error=%s", len(chunk), chunk[0], e)
        return (
            _fetch_sina_chunk(client, url_template, chunk[:mid], min_chunk_size)
            + _fetch_sina_chunk(client, url_template, chunk[mid:], min_chunk_size)
        )


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        value = float(text)
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def _volume_to_hands(value: str | None) -> float | None:
    """Sina 成交量字段单位是股，项目内部日 K 统一使用手。"""
    parsed = _to_float(value)
    return parsed / 100.0 if parsed is not None else None


def _parse_timestamp(date_text: str, time_text: str) -> str | None:
    """将 Sina 日期与时间拼成 ISO 风格字符串。"""
    date_text = str(date_text or "").strip()
    time_text = str(time_text or "").strip()
    if not date_text or not time_text:
        return None
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S").isoformat(sep=" ")
    except ValueError:
        return f"{date_text} {time_text}"


def _parse_sina_line(sina_code: str, fields: str) -> dict | None:
    """解析单行 Sina 行情文本为面板标准实时行情记录。"""
    values = fields.split(",") if fields is not None else []
    if not values or not values[0].strip():
        return None
    row = {name: values[i] if i < len(values) else "" for i, name in enumerate(SINA_FIELDS)}
    prev_close = _to_float(row.get("prev_close"))
    last_price = _to_float(row.get("last_price"))
    high = _to_float(row.get("high"))
    low = _to_float(row.get("low"))

    change_amount = None
    change_pct = None
    if prev_close not in (None, 0) and last_price is not None:
        change_amount = last_price - prev_close
        change_pct = change_amount / prev_close * 100

    amplitude = None
    if prev_close not in (None, 0) and high is not None and low is not None:
        amplitude = (high - low) / prev_close * 100

    return {
        "symbol": to_panel_symbol(sina_code),
        "code": to_level1_code(sina_code),
        "name": row.get("name"),
        "last_price": last_price,
        "prev_close": prev_close,
        "open": _to_float(row.get("open")),
        "high": high,
        "low": low,
        "volume": _volume_to_hands(row.get("volume")),
        "amount": _to_float(row.get("amount")),
        "change_amount": change_amount,
        "change_pct": change_pct,
        "amplitude": amplitude,
        "date": row.get("date"),
        "timestamp": _parse_timestamp(row.get("date", ""), row.get("time", "")),
        "session": row.get("status"),
    }


def fetch_sina_realtime(symbols: list[str] | None = None) -> pl.DataFrame:
    """通过 Sina HTTP 批量接口获取实时行情。"""
    raw_symbols = symbols or _read_symbol_universe()
    sina_codes = []
    seen: set[str] = set()
    for symbol in raw_symbols:
        code = to_sina_code(symbol)
        if code and code not in seen:
            sina_codes.append(code)
            seen.add(code)
    if not sina_codes:
        return pl.DataFrame()

    records: list[dict] = []
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": settings.ai_user_agent,
    }
    batch_size = min(200, int(getattr(settings, "sina_http_batch_size", 800) or 800))
    timeout = float(getattr(settings, "sina_http_timeout_s", 8.0) or 8.0)
    url_template = str(getattr(settings, "sina_http_url", "https://hq.sinajs.cn/list={codes}"))

    with httpx.Client(timeout=timeout, headers=headers) as client:
        for chunk in _chunks(sina_codes, batch_size):
            records.extend(_fetch_sina_chunk(client, url_template, chunk))

    if not records:
        return pl.DataFrame()
    return pl.DataFrame(records).unique(subset=["symbol"], keep="last")
