"""桌面版启动准备。

本模块只处理打开 exe 前必须完成的本地准备事项：目录、静态资源、种子数据、默认偏好。
业务服务仍由 app.main 的 lifespan 初始化。
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# 桌面版首次启动需要存在的用户数据子目录。
_DESKTOP_DATA_SUBDIRS = (
    "kline_daily",
    "kline_daily_enriched",
    "kline_index_daily",
    "kline_index_enriched",
    "kline_etf_daily",
    "kline_etf_enriched",
    "kline_etf_minute",
    "kline_minute",
    "adj_factor",
    "adj_factor_etf",
    "financials",
    "financials/metrics",
    "financials/income",
    "financials/balance_sheet",
    "financials/cash_flow",
    "instruments",
    "instruments_index",
    "instruments_etf",
    "instruments_ext",
    "kline_ext",
    "pools",
    "backtest_results",
    "screener_results",
    "ai_cache",
    "user_data",
    "depth5",
    "logs",
)

# 打包进 exe 的只读种子文件，首次启动复制到用户数据目录。
_SEED_FILES = (
    "instruments/instruments.parquet",
    "instruments_index/instruments_index.parquet",
    "instruments_etf/instruments_etf.parquet",
)

# 桌面版默认使用本地 Sina 与通达信 Level1 数据源；已有偏好保持原值。
_DESKTOP_DEFAULT_PREFERENCES = {
    "onboarding_completed": True,
    "daily_data_provider": "local_projects",
    "minute_data_provider": "local_projects",
    "realtime_data_provider": "local_projects",
    "realtime_quotes_enabled": False,
}


def prepare_desktop_environment() -> None:
    """完成桌面版启动前准备，供 exe 入口在启动后端前调用。"""
    from app.config import settings

    data_dir = Path(settings.data_dir)
    static_dir = Path(settings.static_dir)
    seed_dir = Path(settings.desktop_seed_dir)

    _ensure_data_dir_writable(data_dir)
    _ensure_data_subdirs(data_dir)
    _ensure_static_assets(static_dir)
    _migrate_legacy_data_dir(data_dir)
    _copy_seed_files(seed_dir, data_dir)
    _ensure_default_preferences(data_dir)


def _ensure_data_dir_writable(data_dir: Path) -> None:
    """确保用户数据根目录可写。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    probe = data_dir / ".write_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


def _ensure_data_subdirs(data_dir: Path) -> None:
    """创建桌面版运行所需的数据子目录。"""
    for subdir in _DESKTOP_DATA_SUBDIRS:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)


def _ensure_static_assets(static_dir: Path) -> None:
    """检查前端 dist 是否已经随 exe 打包或在开发环境完成构建。"""
    index_file = static_dir / "index.html"
    assets_dir = static_dir / "assets"
    if index_file.exists() and assets_dir.exists():
        return
    raise RuntimeError(
        f"前端静态资源缺失: {static_dir}。请先执行 frontend 构建，再启动桌面版。"
    )


def _migrate_legacy_data_dir(data_dir: Path) -> None:
    """迁移旧桌面版兄弟目录数据，需早于种子数据复制。"""
    if not getattr(sys, "frozen", False):
        return

    legacy_dir = data_dir.parent / "TickFlowStockPanel_Data"
    if not legacy_dir.exists():
        return

    has_new_data = any(data_dir.rglob("*.parquet")) or any(data_dir.rglob("*.jsonl"))
    if has_new_data:
        logger.info("旧数据目录存在但当前 data 已有数据，跳过迁移: %s", legacy_dir)
        return

    try:
        logger.info("开始迁移旧桌面版数据: %s -> %s", legacy_dir, data_dir)
        for item in legacy_dir.iterdir():
            _move_legacy_item(item, data_dir / item.name)
        try:
            legacy_dir.rmdir()
        except OSError:
            logger.warning("旧数据目录仍有未迁移内容，已保留: %s", legacy_dir)
        logger.info("旧桌面版数据迁移完成")
    except Exception as exc:  # noqa: BLE001
        logger.warning("旧桌面版数据迁移失败，继续启动: %s", exc)


def _move_legacy_item(src: Path, dst: Path) -> None:
    """把旧数据项移动到新 data 目录，目录冲突时合并子项。"""
    if dst.exists() and src.is_dir() and dst.is_dir():
        for child in src.iterdir():
            _move_legacy_item(child, dst / child.name)
        try:
            src.rmdir()
        except OSError:
            pass
        return
    if dst.exists():
        logger.info("旧数据项与新目录冲突，保留新文件: %s", dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _copy_seed_files(seed_dir: Path, data_dir: Path) -> None:
    """复制缺失的内置种子数据，避免全新安装后股票维表为空。"""
    if not seed_dir.exists():
        logger.warning("桌面版种子数据目录缺失: %s", seed_dir)
        return

    for relative in _SEED_FILES:
        src = seed_dir / relative
        dst = data_dir / relative
        if not src.exists():
            continue
        if _usable_parquet(dst):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        logger.info("桌面版种子数据已复制: %s -> %s", src, dst)


def _usable_parquet(path: Path) -> bool:
    """判断 Parquet 文件是否存在且包含有效行，避免覆盖已有用户数据。"""
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        df = pl.read_parquet(path, n_rows=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("种子数据目标文件不可读，保留原文件: %s (%s)", path, exc)
        return True
    return not df.is_empty()


def _ensure_default_preferences(data_dir: Path) -> None:
    """写入桌面版缺省偏好；只补缺失字段，保留已有选择。"""
    pref_path = data_dir / "user_data" / "preferences.json"
    pref_path.parent.mkdir(parents=True, exist_ok=True)

    current: dict = {}
    if pref_path.exists():
        try:
            current = json.loads(pref_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("preferences.json 读取失败，保留原文件并跳过缺省偏好写入: %s", exc)
            return

    changed = False
    for key, value in _DESKTOP_DEFAULT_PREFERENCES.items():
        if key not in current:
            current[key] = value
            changed = True

    if changed:
        pref_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("桌面版缺省偏好已写入: %s", pref_path)
