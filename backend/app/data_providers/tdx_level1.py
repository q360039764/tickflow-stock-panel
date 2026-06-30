"""通达信 Level1 分时明细内置采集。"""
from __future__ import annotations

import logging
import socket
import zlib
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

import httpx
import polars as pl

from app.config import settings
from app.data_providers.local_market_codes import to_level1_code, to_panel_symbol

logger = logging.getLogger(__name__)

MAGIC = b"\xB1\xCB\x74\x00"
ZLIB_HEADERS = (b"\x78\x01", b"\x78\x5E", b"\x78\x9C", b"\x78\xDA")
DEFAULT_STEP = 0x7530
OFFSET_POS1 = 0x0C
FIRST_BYTE_TIMEOUT_S = 1.0
MAX_PAGE_COUNT = 99
EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_TIMEOUT_S = 8.0

DEFAULT_HELLO = r"""
00 00 00 00  00 00 2a 00  2a 00 c5 02  68 69 73 68
66 2f 64 61  74 65 2f 32  30 32 35 30  36 31 32 2f
73 68 36 30  35 35 59 38  2e 69 6d 67  00 00 00 00
00 00 00 00
"""

DEFAULT_REQUEST = r"""
00 00 00 00  00 00 36 01  36 01 b9 06  00 00 00 00
30 75 00 00  68 69 73 68  66 2f 64 61  74 65 2f 32
30 32 35 30  36 31 32 2f  73 68 36 30  35 35 39 38
2e 69 6d 67  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
"""

STX = 0x02
ETX = 0x03
EOT = 0x04

# 通达信协议字段 ID 到内部字段的映射。
FIELD_MAP = {
    "01": "code",
    "0T": "time_text",
    "08": "avg_sell_price",
    "10": "cum_volume",
    "1A": "cum_amount",
    "09": "cum_trades",
    "06": "high_price",
    "07": "low_price",
    "44": "sell5_price", "54": "sell5_volume",
    "43": "sell4_price", "53": "sell4_volume",
    "42": "sell3_price", "52": "sell3_volume",
    "41": "sell2_price", "51": "sell2_volume",
    "40": "sell1_price", "50": "sell1_volume",
    "20": "buy1_price", "30": "buy1_volume",
    "21": "buy2_price", "31": "buy2_volume",
    "22": "buy3_price", "32": "buy3_volume",
    "23": "buy4_price", "33": "buy4_volume",
    "24": "buy5_price", "34": "buy5_volume",
}

FLOAT_FIELDS = {
    "avg_sell_price", "cum_amount", "high_price", "low_price",
    "sell5_price", "sell4_price", "sell3_price", "sell2_price", "sell1_price",
    "buy1_price", "buy2_price", "buy3_price", "buy4_price", "buy5_price",
}
INT_FIELDS = {
    "time_sec", "cum_volume", "cum_trades",
    "sell5_volume", "sell4_volume", "sell3_volume", "sell2_volume", "sell1_volume",
    "buy1_volume", "buy2_volume", "buy3_volume", "buy4_volume", "buy5_volume",
}


@dataclass
class RawRecord:
    code: str | None = None
    time_text: str | None = None
    avg_sell_price: str | None = None
    cum_volume: str | None = None
    cum_amount: str | None = None
    cum_trades: str | None = None
    high_price: str | None = None
    low_price: str | None = None
    sell5_price: str | None = None
    sell5_volume: str | None = None
    sell4_price: str | None = None
    sell4_volume: str | None = None
    sell3_price: str | None = None
    sell3_volume: str | None = None
    sell2_price: str | None = None
    sell2_volume: str | None = None
    sell1_price: str | None = None
    sell1_volume: str | None = None
    buy1_price: str | None = None
    buy1_volume: str | None = None
    buy2_price: str | None = None
    buy2_volume: str | None = None
    buy3_price: str | None = None
    buy3_volume: str | None = None
    buy4_price: str | None = None
    buy4_volume: str | None = None
    buy5_price: str | None = None
    buy5_volume: str | None = None


@dataclass
class MarketData:
    code: str
    trade_date: date
    time_sec: int
    avg_sell_price: float | None = None
    cum_volume: int | None = None
    cum_amount: float | None = None
    cum_trades: int | None = None
    high_price: float | None = None
    low_price: float | None = None
    sell5_price: float | None = None
    sell5_volume: int | None = None
    sell4_price: float | None = None
    sell4_volume: int | None = None
    sell3_price: float | None = None
    sell3_volume: int | None = None
    sell2_price: float | None = None
    sell2_volume: int | None = None
    sell1_price: float | None = None
    sell1_volume: int | None = None
    buy1_price: float | None = None
    buy1_volume: int | None = None
    buy2_price: float | None = None
    buy2_volume: int | None = None
    buy3_price: float | None = None
    buy3_volume: int | None = None
    buy4_price: float | None = None
    buy4_volume: int | None = None
    buy5_price: float | None = None
    buy5_volume: int | None = None


def parse_hexdump(text: str) -> bytearray:
    """解析 Rust 项目中的 hexdump 模板。"""
    hex_text = "".join(ch for ch in text if ch.lower() in "0123456789abcdef")
    return bytearray.fromhex(hex_text)


def replace_date_code(payload: bytearray, trade_date: date, code: str) -> None:
    """把请求模板中的路径替换为目标交易日和股票代码。"""
    date_text = trade_date.strftime("%Y%m%d")
    market = "sh" if code.startswith("6") else "sz"
    path = f"hishf/date/{date_text}/{market}{code}.img".encode("ascii")
    pos = payload.find(b"hishf/date/")
    if pos < 0:
        raise ValueError("payload 中未找到通达信路径前缀")
    payload[pos:pos + len(path)] = path


def write_u32_le(payload: bytearray, offset: int, value: int) -> None:
    """在请求模板指定位置写入分页偏移量。"""
    payload[offset:offset + 4] = int(value).to_bytes(4, "little", signed=False)


def recv_data(sock: socket.socket, first_timeout_s: float, quiet_timeout_s: float) -> bytes:
    """接收握手响应，静默一段时间后结束。"""
    chunks: list[bytes] = []
    sock.settimeout(first_timeout_s)
    try:
        first = sock.recv(16_384)
    except TimeoutError:
        return b""
    if not first:
        return b""
    chunks.append(first)
    sock.settimeout(quiet_timeout_s)
    while True:
        try:
            chunk = sock.recv(16_384)
        except TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def recv_page_exact(sock: socket.socket) -> bytes | None:
    """读取一页通达信响应，返回包含 16 字节头的完整页。"""
    sock.settimeout(FIRST_BYTE_TIMEOUT_S)
    try:
        first = sock.recv(1)
    except TimeoutError:
        return None
    if not first:
        return None
    header = first + _recv_exact(sock, 15, 2.0)
    if len(header) < 16:
        return None
    if header[:4] != MAGIC:
        return header
    body_len = int.from_bytes(header[12:14], "little", signed=False)
    if body_len <= 0:
        return header
    body = _recv_exact(sock, body_len, float(getattr(settings, "tdx_level1_timeout_s", 10.0) or 10.0))
    if len(body) < body_len:
        return None
    return header + body


def _recv_exact(sock: socket.socket, size: int, timeout_s: float) -> bytes:
    sock.settimeout(timeout_s)
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def find_all(data: bytes, needle: bytes) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        pos = data.find(needle, start)
        if pos < 0:
            return positions
        positions.append(pos)
        start = pos + 1


def slice_blocks(data: bytes) -> list[bytes]:
    positions = find_all(data, MAGIC)
    if not positions:
        return []
    blocks: list[bytes] = []
    for index, start in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else len(data)
        blocks.append(data[start:end])
    return blocks


def decompress_exact(data: bytes) -> tuple[bytes, int]:
    """从 data 起始位置解压一个 zlib 流，返回明文和消耗字节数。"""
    decomp = zlib.decompressobj()
    out = decomp.decompress(data)
    out += decomp.flush()
    used = len(data) - len(decomp.unused_data)
    return out, used


def guess_len_pair(block: bytes, zlib_pos: int) -> tuple[int, int] | None:
    """猜测 zlib 前面的压缩长度和解压长度字段。"""
    for back in (8, 12):
        if zlib_pos < back:
            continue
        pos = zlib_pos - back
        if pos + 8 > len(block):
            continue
        a = int.from_bytes(block[pos:pos + 4], "little", signed=False)
        c = int.from_bytes(block[pos + 4:pos + 8], "little", signed=False)
        total_len = len(block) - zlib_pos
        if a >= 8 and a <= total_len and 16 <= c <= 100_000_000 and a < c:
            return a, c
        if c >= 8 and c <= total_len and 16 <= a <= 100_000_000 and c < a:
            return c, a
    return None


def has_tick_frames(data: bytes) -> bool:
    return data.find(bytes((EOT, ETX))) >= 0


def extract_recursive(block: bytes, depth: int = 0) -> list[bytes]:
    """递归提取 zlib payload，兼容部分双层压缩数据。"""
    if depth > 3:
        return []
    payloads: list[bytes] = []
    search = 0
    while search + 2 <= len(block):
        candidates = [block.find(header, search) for header in ZLIB_HEADERS]
        candidates = [pos for pos in candidates if pos >= 0]
        if not candidates:
            break
        abs_pos = min(candidates)

        pair = guess_len_pair(block, abs_pos)
        if pair is not None:
            comp_len, _ = pair
            end = abs_pos + comp_len
            if end <= len(block):
                try:
                    data, used = decompress_exact(block[abs_pos:end])
                except zlib.error:
                    data, used = b"", 0
                if data:
                    _push_or_recurse(payloads, data, depth)
                    search = abs_pos + max(used, 1)
                    continue

        try:
            data, used = decompress_exact(block[abs_pos:])
        except zlib.error:
            data, used = b"", 0
        if used > 0 and data:
            _push_or_recurse(payloads, data, depth)
            search = abs_pos + used
            continue
        search = abs_pos + 1
    return payloads


def _push_or_recurse(payloads: list[bytes], data: bytes, depth: int) -> None:
    if has_tick_frames(data):
        payloads.append(data)
        return
    nested = extract_recursive(data, depth + 1)
    if nested:
        payloads.extend(nested)
    else:
        payloads.append(data)


def block_type(block: bytes) -> int:
    if len(block) < 8:
        return 0
    return int.from_bytes(block[4:8], "little", signed=False)


def extract_payloads(response: bytes) -> list[bytes]:
    """从通达信原始响应提取明文分时 payload。"""
    blocks = slice_blocks(response)
    if not blocks:
        return []

    if len(blocks) > 1 and block_type(blocks[0]) == 0x1C and len(blocks[0]) > 16:
        try:
            outer, _ = decompress_exact(blocks[0][16:])
            if len(outer) > 28:
                inner = bytearray(outer[28:])
                for block in blocks[1:]:
                    if len(block) > 20:
                        inner.extend(block[20:])
                full, _ = decompress_exact(bytes(inner))
                if has_tick_frames(full):
                    return [full]
        except zlib.error:
            pass

    last_1c = next((i for i in range(len(blocks) - 1, -1, -1) if block_type(blocks[i]) == 0x1C), None)
    if last_1c is not None and last_1c > 0:
        data_blocks = blocks[:last_1c]
        merged = _merge_standard_blocks(data_blocks)
        if merged:
            try:
                data, _ = decompress_exact(merged)
                if has_tick_frames(data):
                    return [data]
            except zlib.error:
                pass

    if len(blocks) > 1:
        merged = _merge_standard_blocks(blocks)
        if merged:
            try:
                data, _ = decompress_exact(merged)
                if has_tick_frames(data):
                    return [data]
            except zlib.error:
                pass

    payloads: list[bytes] = []
    for block in blocks:
        payloads.extend(extract_recursive(block))
    return [payload for payload in payloads if payload]


def _merge_standard_blocks(blocks: list[bytes]) -> bytes:
    if not blocks or len(blocks[0]) <= 44:
        return b""
    merged = bytearray(blocks[0][44:])
    for block in blocks[1:]:
        if len(block) > 20:
            merged.extend(block[20:])
    return bytes(merged)


def iter_frames(data: bytes) -> list[bytes]:
    frames: list[bytes] = []
    start = 0
    marker = bytes((EOT, ETX))
    while start < len(data):
        pos = data.find(marker, start)
        if pos < 0:
            if start < len(data):
                frames.append(data[start:])
            break
        if pos > start:
            frames.append(data[start:pos])
        start = pos + 2
    return frames


def tokenize(frame: bytes) -> list[bytes]:
    if frame.startswith(bytes((ETX,))):
        frame = frame[1:]
    return [token for token in frame.split(bytes((STX,))) if token]


def parse_frame(frame: bytes, last_record: RawRecord | None) -> RawRecord | None:
    tokens = tokenize(frame)
    if not tokens:
        return None
    rec = RawRecord(**({f.name: getattr(last_record, f.name) for f in fields(RawRecord)} if last_record else {}))
    for token in tokens:
        text = token.decode("utf-8", errors="ignore").strip()
        if len(text) < 2:
            continue
        name = FIELD_MAP.get(text[:2])
        if name:
            setattr(rec, name, text[2:])
    return rec


def parse_time_sec(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).split(".", 1)[0]
    try:
        return int(text)
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except ValueError:
        return None
    return out if out >= 0 else None


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def raw_to_market_data(raw: RawRecord, trade_date: date) -> MarketData | None:
    code = raw.code
    time_sec = parse_time_sec(raw.time_text)
    if not code or time_sec is None:
        return None
    data = {"code": code, "trade_date": trade_date, "time_sec": time_sec}
    for name in FLOAT_FIELDS:
        data[name] = parse_float(getattr(raw, name, None))
    for name in INT_FIELDS:
        if name != "time_sec":
            data[name] = parse_int(getattr(raw, name, None))
    return MarketData(**data)


def parse_payload(payload: bytes, trade_date: date) -> list[MarketData]:
    """解析明文 payload 为 Level1 明细记录。"""
    records: list[MarketData] = []
    last_record: RawRecord | None = None
    for index, frame in enumerate(iter_frames(payload)):
        if index < 2:
            continue
        raw = parse_frame(frame, last_record)
        if raw is None:
            continue
        data = raw_to_market_data(raw, trade_date)
        if data is not None:
            records.append(data)
        last_record = raw
    return records


def fetch_level1_ticks(symbol: str, trade_date: date) -> list[MarketData]:
    """按通达信 TCP 协议抓取单股单日 Level1 明细。"""
    code = to_level1_code(symbol)
    if not code:
        return []
    host = str(getattr(settings, "tdx_level1_host", "129.211.70.79") or "129.211.70.79")
    port = int(getattr(settings, "tdx_level1_port", 7709) or 7709)
    timeout_s = float(getattr(settings, "tdx_level1_timeout_s", 10.0) or 10.0)

    hello = parse_hexdump(DEFAULT_HELLO)
    replace_date_code(hello, trade_date, code)
    request_template = parse_hexdump(DEFAULT_REQUEST)
    replace_date_code(request_template, trade_date, code)

    pages: list[bytes] = []
    baseline_size: int | None = None
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.sendall(hello)
            recv_data(sock, FIRST_BYTE_TIMEOUT_S, 0.5)
            for page in range(MAX_PAGE_COUNT):
                req = bytearray(request_template)
                write_u32_le(req, OFFSET_POS1, DEFAULT_STEP * page)
                sock.sendall(req)
                resp = recv_page_exact(sock)
                if not resp:
                    break
                got = len(resp)
                if MAGIC not in resp:
                    break
                if baseline_size is None:
                    if got <= 20:
                        break
                    baseline_size = got
                else:
                    threshold = max(baseline_size * 3 // 5, baseline_size - 4096)
                    if got < threshold:
                        pages.append(resp)
                        break
                pages.append(resp)
    except Exception as e:  # noqa: BLE001
        logger.warning("通达信 Level1 抓取失败 %s %s: %s", symbol, trade_date, e)
        return []

    if not pages:
        return []
    combined = b"".join(pages)
    payloads = extract_payloads(combined)
    records: list[MarketData] = []
    for payload in payloads:
        records.extend(parse_payload(payload, trade_date))
    return records


def records_to_frame(records: Iterable[MarketData]) -> pl.DataFrame:
    rows = [record.__dict__ for record in records]
    if not rows:
        return pl.DataFrame()
    schema = {
        "code": pl.Utf8,
        "trade_date": pl.Date,
        "time_sec": pl.Int64,
        "avg_sell_price": pl.Float64,
        "cum_volume": pl.Float64,
        "cum_amount": pl.Float64,
        "cum_trades": pl.Float64,
        "high_price": pl.Float64,
        "low_price": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema, strict=False)


def ticks_to_minute_df(records: list[MarketData]) -> pl.DataFrame:
    """将 Level1 累计明细聚合为 1 分钟 K 线。"""
    tick_df = records_to_frame(records)
    if tick_df.is_empty():
        return pl.DataFrame()
    tick_df = tick_df.with_columns([
        pl.col("code").cast(pl.Utf8).map_elements(to_panel_symbol, return_dtype=pl.Utf8).alias("symbol"),
        pl.col("trade_date").cast(pl.Date, strict=False).alias("date"),
        pl.col("time_sec").cast(pl.Int64, strict=False),
        pl.col("avg_sell_price").cast(pl.Float64, strict=False),
        pl.col("high_price").cast(pl.Float64, strict=False),
        pl.col("low_price").cast(pl.Float64, strict=False),
        pl.col("cum_volume").cast(pl.Float64, strict=False),
        pl.col("cum_amount").cast(pl.Float64, strict=False),
    ]).filter(
        pl.col("avg_sell_price").is_not_null()
        & (pl.col("avg_sell_price") > 0)
        & pl.col("time_sec").is_not_null()
    )
    if tick_df.is_empty():
        return pl.DataFrame()

    tick_df = tick_df.with_columns((pl.col("time_sec") // 100).alias("minute_key"))
    minute = tick_df.group_by(["symbol", "date", "minute_key"]).agg([
        pl.col("time_sec").min().alias("first_time"),
        pl.col("time_sec").max().alias("last_time"),
        pl.col("avg_sell_price").sort_by("time_sec").first().alias("open"),
        pl.col("avg_sell_price").max().alias("high"),
        pl.col("avg_sell_price").min().alias("low"),
        pl.col("avg_sell_price").sort_by("time_sec").last().alias("close"),
        (pl.col("cum_volume").max() - pl.col("cum_volume").min()).alias("volume"),
        (pl.col("cum_amount").max() - pl.col("cum_amount").min()).alias("amount"),
    ])
    minute = minute.with_columns(
        (
            pl.col("date").cast(pl.Utf8)
            + " "
            + (pl.col("last_time") // 10000).cast(pl.Int64).cast(pl.Utf8).str.zfill(2)
            + ":"
            + ((pl.col("last_time") // 100) % 100).cast(pl.Int64).cast(pl.Utf8).str.zfill(2)
            + ":"
            + (pl.col("last_time") % 100).cast(pl.Int64).cast(pl.Utf8).str.zfill(2)
        ).str.strptime(pl.Datetime("us"), "%Y-%m-%d %H:%M:%S", strict=False).alias("datetime")
    )
    return minute.select([
        "symbol", "datetime", "open", "high", "low", "close", "volume", "amount",
    ]).drop_nulls(["symbol", "datetime"]).sort(["symbol", "datetime"])


def _eastmoney_secid(symbol: str) -> str:
    """将面板 symbol 转为东方财富 secid，沪市为 1，深市和北交所按 0 处理。"""
    panel_symbol = to_panel_symbol(symbol)
    code = to_level1_code(panel_symbol)
    if not code:
        return ""
    if panel_symbol.endswith(".SH"):
        return f"1.{code}"
    return f"0.{code}"


def _to_float(value: str | None) -> float | None:
    """解析东方财富分时字段，非法数值返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _fetch_eastmoney_minute(symbol: str, trade_date: date) -> pl.DataFrame:
    """通达信 Level1 为空时，用东方财富当日分时接口补充分时 K 线。"""
    secid = _eastmoney_secid(symbol)
    panel_symbol = to_panel_symbol(symbol)
    if not secid or not panel_symbol:
        return pl.DataFrame()

    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "5",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 TickFlowStockPanel/1.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        resp = httpx.get(EASTMONEY_TRENDS_URL, params=params, headers=headers, timeout=EASTMONEY_TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("东方财富分时回退抓取失败 %s %s: %s", symbol, trade_date, e)
        return pl.DataFrame()

    trends = ((payload or {}).get("data") or {}).get("trends") or []
    rows: list[dict] = []
    for item in trends:
        parts = str(item).split(",")
        if len(parts) < 7:
            continue
        try:
            dt = datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if dt.date() != trade_date:
            continue
        open_price = _to_float(parts[1])
        close_price = _to_float(parts[2])
        high_price = _to_float(parts[3])
        low_price = _to_float(parts[4])
        volume = _to_float(parts[5])
        amount = _to_float(parts[6])
        if None in (open_price, close_price, high_price, low_price):
            continue
        rows.append({
            "symbol": panel_symbol,
            "datetime": dt,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume or 0.0,
            "amount": amount or 0.0,
        })

    if not rows:
        return pl.DataFrame()
    return (
        pl.DataFrame(rows)
        .drop_nulls(["symbol", "datetime"])
        .unique(subset=["symbol", "datetime"], keep="last")
        .sort(["symbol", "datetime"])
    )


def _iter_trade_dates(start_time: datetime | None, end_time: datetime | None) -> list[date]:
    end_day = end_time.date() if end_time else date.today()
    start_day = start_time.date() if start_time else end_day
    days: list[date] = []
    cur = start_day
    while cur <= end_day:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _cache_path(trade_date: date) -> Path:
    return Path(settings.data_dir) / "kline_minute" / f"date={trade_date}" / "part.parquet"


def _read_cached_minutes(symbols: list[str], trade_date: date) -> pl.DataFrame:
    path = _cache_path(trade_date)
    if not path.exists():
        return pl.DataFrame()
    try:
        df = pl.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取分钟 K 缓存失败 %s: %s", path, e)
        return pl.DataFrame()
    if df.is_empty() or "symbol" not in df.columns or "datetime" not in df.columns:
        return pl.DataFrame()
    return df.filter(pl.col("symbol").is_in(symbols))


def _write_minute_cache(trade_date: date, df: pl.DataFrame) -> None:
    """写入第一个项目自己的分钟 K 线缓存。"""
    if df.is_empty():
        return
    path = _cache_path(trade_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = pl.read_parquet(path)
            df = pl.concat([existing, df], how="diagonal_relaxed")
        except Exception as e:  # noqa: BLE001
            logger.warning("合并分钟 K 缓存失败 %s: %s", path, e)
    df = df.unique(subset=["symbol", "datetime"], keep="last").sort(["symbol", "datetime"])
    df.write_parquet(path)


def fetch_level1_minute(
    symbols: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> pl.DataFrame:
    """获取分钟 K 线，优先读第一个项目缓存，缺失时按需抓取通达信 Level1。"""
    wanted_symbols = [to_panel_symbol(symbol) for symbol in symbols if str(symbol or "").strip()]
    wanted_symbols = sorted({symbol for symbol in wanted_symbols if symbol})
    if not wanted_symbols:
        return pl.DataFrame()

    frames: list[pl.DataFrame] = []
    for trade_day in _iter_trade_dates(start_time, end_time):
        cached = _read_cached_minutes(wanted_symbols, trade_day)
        cached_symbols = set(cached["symbol"].to_list()) if not cached.is_empty() and "symbol" in cached.columns else set()
        missing = [symbol for symbol in wanted_symbols if symbol not in cached_symbols]
        day_frames: list[pl.DataFrame] = []
        if not cached.is_empty():
            day_frames.append(cached)
        for symbol in missing:
            records = fetch_level1_ticks(symbol, trade_day)
            minute_df = ticks_to_minute_df(records)
            if minute_df.is_empty():
                minute_df = _fetch_eastmoney_minute(symbol, trade_day)
            if not minute_df.is_empty():
                _write_minute_cache(trade_day, minute_df)
                day_frames.append(minute_df)
        if day_frames:
            day_df = pl.concat(day_frames, how="diagonal_relaxed")
            if start_time:
                day_df = day_df.filter(pl.col("datetime") >= start_time)
            if end_time:
                day_df = day_df.filter(pl.col("datetime") <= end_time)
            frames.append(day_df)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").unique(
        subset=["symbol", "datetime"], keep="last"
    ).sort(["symbol", "datetime"])


def latest_trading_session_bounds(trade_day: date) -> tuple[datetime, datetime]:
    """生成 A 股单日分钟数据常用查询时间段。"""
    return datetime.combine(trade_day, time(9, 15)), datetime.combine(trade_day, time(15, 5))
