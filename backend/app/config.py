"""全局配置：从环境变量或项目根目录的 `.env` 读取。"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """返回仓库根目录，用于解析默认数据和静态资源路径。"""
    return Path(__file__).resolve().parent.parent.parent


_PROJECT_ROOT = _project_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # TickFlow
    tickflow_api_key: str = Field(default="", description="留空启用 free 模式")

    # AI
    ai_provider: str = "openai_compat"
    ai_base_url: str = "https://api.alysc.top"
    ai_api_key: str = ""
    ai_model: str = "gpt-5.5"
    # 默认浏览器 User-Agent，用于兼容部分 OpenAI 风格网关。
    ai_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 3018
    log_level: str = "INFO"
    backtest_range_guard: bool = False

    # 路径配置，均允许通过环境变量覆盖。
    data_dir: Path = _PROJECT_ROOT / "data"
    tiers_yaml: Path = _PROJECT_ROOT / "tiers.yaml"
    static_dir: Path = _PROJECT_ROOT / "frontend" / "dist"

    # Local market data
    local_projects_root: Path = _PROJECT_ROOT.parent
    sina_realtime_data_dir: Path = _PROJECT_ROOT.parent / "sina-real-time" / "data"
    sina_http_url: str = "https://hq.sinajs.cn/list={codes}"
    sina_http_batch_size: int = 800
    sina_http_timeout_s: float = 8.0
    tdx_level1_host: str = "129.211.70.79"
    tdx_level1_port: int = 7709
    tdx_level1_timeout_s: float = 10.0
    tdx_level1_cache_enabled: bool = True
    level1_clickhouse_url: str = "http://localhost:8124"
    level1_clickhouse_database: str = "stock_db"
    level1_clickhouse_username: str = "stock_user"
    level1_clickhouse_password: str = "stock_pass"
    level1_clickhouse_timeout_s: float = 30.0

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        """把相对路径统一解析到仓库根目录，避免受启动目录影响。"""
        for field_name in (
            "data_dir",
            "tiers_yaml",
            "static_dir",
            "local_projects_root",
            "sina_realtime_data_dir",
        ):
            value = getattr(self, field_name)
            if not value.is_absolute():
                setattr(self, field_name, (_PROJECT_ROOT / value).resolve())
        return self

    @property
    def use_free_mode(self) -> bool:
        """判断当前是否使用 TickFlow Free 模式。"""
        from app import secrets_store

        return not secrets_store.get_tickflow_key()


settings = Settings()
