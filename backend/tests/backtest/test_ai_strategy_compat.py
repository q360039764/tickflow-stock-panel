from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.backtest.engine import SimResult
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import StrategyDef, StrategyEngine


def test_load_file_normalizes_ai_strategy_meta_and_generated_signals(tmp_path):
    strategy_path = tmp_path / "ai_mqzzvx1k.py"
    strategy_path.write_text(
        '''
import polars as pl

META = {
    "strategy_name": "猫猫头策略",
    "strategy_id": "ai_mqzzvx1k",
    "params": {"lookback_days": 2, "min_hits": 1},
    "entry_signals": ["entry_signal"],
    "exit_signals": ["exit_signal"],
    "scoring": {"hits": 1.0},
}

def generate_signals(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns([
        (pl.col("amount") > 0).alias("entry_signal"),
        (pl.col("amount") <= 0).alias("exit_signal"),
        pl.col("amount").alias("hits"),
    ])
''',
        encoding="utf-8",
    )

    strategy = StrategyEngine._load_file(strategy_path)
    panel = pl.DataFrame({
        "symbol": ["A", "B"],
        "date": [date(2024, 1, 1)] * 2,
        "amount": [1.0, 0.0],
    })
    filtered = strategy.filter_history_fn(panel, {})  # type: ignore[misc]

    assert strategy.meta["id"] == "ai_mqzzvx1k"
    assert strategy.meta["name"] == "猫猫头策略"
    assert strategy.meta["params"] == [
        {"id": "lookback_days", "label": "lookback_days", "type": "int", "default": 2},
        {"id": "min_hits", "label": "min_hits", "type": "int", "default": 1},
    ]
    assert strategy.entry_signals == []
    assert filtered["symbol"].to_list() == ["A"]


class _StrategyEngineStub:
    def __init__(self, strategy: StrategyDef) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str) -> StrategyDef:
        return self.strategy


class _RepoStub:
    def get_index_daily(self, *args, **kwargs) -> pl.DataFrame:
        return pl.DataFrame()


class _EngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = _RepoStub()

    def load_panel(self, symbols, start: date, end: date) -> pl.DataFrame:
        return self.panel

    def simulate_portfolio(self, panel, entries, exits, config, progress_cb=None, cancel_event=None) -> SimResult:
        return SimResult(
            equity_curve=[{"date": "2024-01-01", "value": config.initial_capital}],
            drawdown_curve=[{"date": "2024-01-01", "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


def test_backtest_ignores_legacy_non_dict_param_items():
    start = date(2024, 1, 1)
    panel = pl.DataFrame([
        {
            "symbol": "A",
            "date": start,
            "name": "A",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1,
            "amount": 1000.0,
        },
    ])
    strategy = StrategyDef(
        meta={"id": "test", "name": "test", "scoring": {}, "params": ["lookback_days"], "limit": 100},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        alerts=[],
        filter_fn=lambda df, params: pl.lit(True),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        file_path=None,
    )
    service = StrategyBacktestService(
        engine=_EngineStub(panel),
        strategy_engine=_StrategyEngineStub(strategy),
    )

    result = service.run(StrategyBacktestConfig(
        strategy_id="test",
        symbols=None,
        start=start,
        end=start + timedelta(days=1),
        matching="close_t",
    ))

    assert result.error is None
