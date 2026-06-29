"""本地行情代码格式转换工具。"""
from __future__ import annotations


def to_level1_code(symbol: str) -> str:
    """将面板 symbol 转为通达信 Level1 使用的 6 位数字代码。"""
    s = str(symbol or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        return s.split(".", 1)[0]
    if s.startswith(("SH", "SZ", "BJ")) and len(s) >= 8:
        return s[2:]
    return s


def to_panel_symbol(code: str) -> str:
    """将纯数字、Sina 或通达信代码转为面板内部 symbol。"""
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        return s
    if s.startswith("SH") and len(s) >= 8:
        return f"{s[2:]}.SH"
    if s.startswith("SZ") and len(s) >= 8:
        return f"{s[2:]}.SZ"
    if s.startswith("BJ") and len(s) >= 8:
        return f"{s[2:]}.BJ"
    prefix = s[:3]
    if prefix in {"600", "601", "603", "605", "688", "689", "900"}:
        return f"{s}.SH"
    if prefix in {"000", "001", "002", "003", "300", "301", "200"}:
        return f"{s}.SZ"
    if s[:2] in {"43", "83", "87", "88", "92"}:
        return f"{s}.BJ"
    return s


def to_sina_code(symbol: str) -> str:
    """将面板 symbol 转为 Sina 行情接口代码。"""
    s = str(symbol or "").strip().upper()
    if not s:
        return ""
    if s.startswith(("SH", "SZ", "BJ")) and len(s) >= 8:
        return s.lower()
    code = to_level1_code(s)
    if not code:
        return ""
    if s.endswith(".SH") or code[:3] in {"600", "601", "603", "605", "688", "689", "900"}:
        return f"sh{code}"
    if s.endswith(".BJ") or code[:2] in {"43", "83", "87", "88", "92"}:
        return f"bj{code}"
    return f"sz{code}"


def quote_sql_values(values: list[str]) -> str:
    """生成只含单引号转义的 SQL IN 值列表。"""
    safe = [str(v).replace("'", "''") for v in values if str(v).strip()]
    return ",".join(f"'{v}'" for v in safe)
