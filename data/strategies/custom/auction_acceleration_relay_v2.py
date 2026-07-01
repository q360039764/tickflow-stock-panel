"""竞价加速接力模型2.0 - 小微盘日线可回测近似版。

对应原模型自由流通市值小于 28 亿元的分池。当前 enriched 日线数据缺少真实竞价
区间、人气排名和竞价成交额时，使用开盘涨幅、日线量比、换手率和收盘强度近似。
"""
import polars as pl


# 原贴明确使用 28 亿元自由流通市值区分小微盘与非小微盘。
SMALL_CAP_BOUNDARY = 28e8


META = {
    "id": "auction_acceleration_relay_v2",
    "name": "竞价加速接力模型2.0（小微盘）",
    "description": "自由流通市值小于28亿元，过滤高换手承接断裂后的小微盘竞价加速接力模型",
    "tags": ["竞价", "连板", "接力", "超短", "小微盘"],
    "version": "1.1.0",
    "basic_filter": {
        "price_min": 3,
        "price_max": 120,
        "market_cap_min": 10e8,
        "float_cap_min": None,
        "float_cap_max": SMALL_CAP_BOUNDARY,
        "amount_min": 0.5e8,
        "exclude_st": True,
        "exclude_new_days": 60,
        "boards": ["沪主板", "深主板", "创业板", "科创板"],
    },
    "params": [
        {"id": "min_recent_gain", "label": "近10日最低涨幅%", "type": "float",
         "default": 15.0, "min": 5.0, "max": 60.0, "step": 1.0},
        {"id": "min_open_gap", "label": "最低高开%", "type": "float",
         "default": 2.0, "min": 0.5, "max": 8.0, "step": 0.5},
        {"id": "max_open_gap", "label": "最高高开%", "type": "float",
         "default": 8.5, "min": 3.0, "max": 15.0, "step": 0.5},
        {"id": "min_vol_ratio", "label": "最低5日量比（竞价量能代理）", "type": "float",
         "default": 0.5, "min": 0.2, "max": 3.0, "step": 0.1},
        {"id": "max_vol_ratio", "label": "最高5日量比（防止过度一致）", "type": "float",
         "default": 3.0, "min": 1.0, "max": 6.0, "step": 0.1},
        {"id": "min_turnover", "label": "最低换手率%", "type": "float",
         "default": 2.0, "min": 0.5, "max": 20.0, "step": 0.5},
        {"id": "max_turnover", "label": "最高换手率%", "type": "float",
         "default": 12.0, "min": 5.0, "max": 60.0, "step": 1.0},
        {"id": "min_close_strength", "label": "最低收盘强度", "type": "float",
         "default": 0.65, "min": 0.3, "max": 1.0, "step": 0.05},
        {"id": "min_market_limit_up_count", "label": "信号日全市场最低涨停家数", "type": "int",
         "default": 0, "min": 0, "max": 200, "step": 5},
        {"id": "min_next_open_gap", "label": "次日继续加速最低高开%", "type": "float",
         "default": 3.0, "min": 0.0, "max": 10.0, "step": 0.5},
        {"id": "min_next_close_strength", "label": "次日继续加速最低收盘强度", "type": "float",
         "default": 0.70, "min": 0.3, "max": 1.0, "step": 0.05},
    ],
    "scoring": {
        "momentum_10d": 0.35,
        "consecutive_limit_ups": 0.30,
        "change_pct": 0.20,
        "amount": 0.15,
    },
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

LOOKBACK_DAYS = 12

ENTRY_SIGNALS = ["signal_entry_auction_acceleration_relay_v2"]
EXIT_SIGNALS = [
    "signal_exit_next_day_not_accelerating_v2",
    "signal_exit_acceleration_break_v2",
    "signal_ma20_breakdown",
]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 3
ALERTS = [
    {"field": "signal_entry_auction_acceleration_relay_v2", "message": "竞价加速接力模型2.0命中小微盘接力候选"}
]

RULES = """
1. 自由流通市值严格小于28亿元，对应原模型的小微盘分池。
2. 前一交易日需要涨停或处于1至3连板，近10日涨幅不低于15%。
3. 当日开盘涨幅至少2%，且需要高于前一交易日开盘涨幅，近似竞价继续加速。
4. 小微盘不设置强量比下限，重点限制过度爆量和12%以上高换手，避免次日承接断裂。
5. 收盘位置需要靠近日内高位，过滤高开低走和承接不足的假强。
6. 次日只有一字板、强高开后秒板或继续涨停加速才继续持有。
7. 次日小高开、低开、冲高乏力、断板或不能继续加速时，触发竞价或早盘处理的卖出信号。
8. 断板后不做反包格局，断板卖出信号优先于继续持有。
9. 信号日全市场涨停家数低于设定值时暂停新增仓位，过滤接力情绪不足的交易日。
"""


def _close_strength_expr() -> pl.Expr:
    """计算收盘在当日振幅区间中的相对位置，用于近似开盘后承接强弱。"""
    day_range = pl.col("high") - pl.col("low")
    return (
        pl.when(day_range > 0)
        .then((pl.col("close") - pl.col("low")) / day_range)
        .otherwise(0.0)
    )


def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """使用历史窗口生成竞价加速接力候选。

    当前策略引擎提供的是日线 enriched 数据，因此 9:25 竞价区间、人气排名、
    竞价成交额占比等原模型关键字段暂以高开幅度、量比、换手率和收盘强度近似。
    """
    if df.is_empty() or "date" not in df.columns:
        return df

    min_recent_gain = float(params.get("min_recent_gain", 15.0)) / 100.0
    min_open_gap = float(params.get("min_open_gap", 2.0)) / 100.0
    max_open_gap = float(params.get("max_open_gap", 8.5)) / 100.0
    min_vol_ratio = float(params.get("min_vol_ratio", 0.5))
    max_vol_ratio = float(params.get("max_vol_ratio", 3.0))
    # turnover_rate 在 enriched 数据中使用百分数值，例如 8.5 表示 8.5%。
    min_turnover = float(params.get("min_turnover", 2.0))
    max_turnover = float(params.get("max_turnover", 12.0))
    min_close_strength = float(params.get("min_close_strength", 0.65))
    min_market_limit_up_count = int(params.get("min_market_limit_up_count", 0))
    min_next_open_gap = float(params.get("min_next_open_gap", 3.0)) / 100.0
    min_next_close_strength = float(params.get("min_next_close_strength", 0.70))

    # 涨停家数使用信号当日收盘后数据，买入仍在下一交易日开盘执行。
    market_state = df.group_by("date").agg([
        pl.col("signal_limit_up").fill_null(False).sum().alias("_market_limit_up_count"),
    ])
    float_cap_price = pl.col("raw_close") if "raw_close" in df.columns else pl.col("close")
    hist = (
        df.sort(["symbol", "date"])
        .with_columns([
            pl.col("consecutive_limit_ups").shift(1).over("symbol").alias("_prev_boards"),
            pl.col("signal_limit_up").shift(1).over("symbol").fill_null(False).alias("_prev_limit_up"),
            # momentum_10d 已在指标层使用完整历史计算，避免短回看窗口导致 shift(10) 全为空值。
            pl.col("momentum_10d").alias("_recent_10d_gain"),
            ((pl.col("open") / pl.col("prev_close")) - 1).alias("_open_gap"),
            (((pl.col("open") / pl.col("prev_close")) - 1).shift(1).over("symbol")).alias("_prev_open_gap"),
            _close_strength_expr().alias("_close_strength"),
            (float_cap_price * pl.col("float_shares")).alias("_float_cap"),
        ])
        .join(market_state, on="date", how="left")
    )

    # 主模型只做前日涨停或低位连板；普通强势票属于独立的 0 进 1 扩展模型。
    prev_strength = (
        pl.col("_prev_limit_up").fill_null(False)
        | pl.col("_prev_boards").fill_null(0).is_between(1, 3)
    )

    # 当日开盘涨幅高于前日，近似原模型“今日竞价强于昨日竞价”。
    auction_acceleration = (
        (pl.col("_open_gap") >= min_open_gap)
        & (pl.col("_open_gap") <= max_open_gap)
        & (pl.col("_open_gap") > pl.col("_prev_open_gap").fill_null(-1.0))
        & (pl.col("close") > pl.col("open"))
        & (pl.col("_close_strength") >= min_close_strength)
    )

    # 日线量比仅作为竞价量能代理；小微盘侧重限制爆量，不要求强量比下限。
    volume_confirm = (
        (pl.col("vol_ratio_5d") >= min_vol_ratio)
        & (pl.col("vol_ratio_5d") <= max_vol_ratio)
        & (pl.col("turnover_rate") >= min_turnover)
        & (pl.col("turnover_rate") <= max_turnover)
    )

    entry_signal = (
        prev_strength
        & (pl.col("_float_cap") < SMALL_CAP_BOUNDARY)
        & (pl.col("_recent_10d_gain") >= min_recent_gain)
        & auction_acceleration
        & volume_confirm
        & (pl.col("_market_limit_up_count") >= min_market_limit_up_count)
        & (pl.col("close") > pl.col("ma5"))
        & (pl.col("close") > pl.col("ma10"))
    )

    hist = hist.with_columns([
        # 自定义买入信号用于区分候选行和后续卖出行。
        entry_signal.alias("signal_entry_auction_acceleration_relay_v2"),
    ])

    hist = hist.with_columns([
        pl.col("signal_entry_auction_acceleration_relay_v2")
        .shift(1)
        .over("symbol")
        .fill_null(False)
        .alias("_entry_signal_t1"),
    ])

    next_one_price_limit = (
        pl.col("signal_limit_up").fill_null(False)
        & ((pl.col("high") - pl.col("low")) <= (pl.col("close").abs() * 0.001).clip(0.01, None))
    )
    next_open_gap = (pl.col("open") / pl.col("prev_close")) - 1
    next_strong_limit_up = (
        pl.col("signal_limit_up").fill_null(False)
        & (next_open_gap >= min_next_open_gap)
        & (_close_strength_expr() >= min_next_close_strength)
    )
    next_continue_accelerating = next_one_price_limit | next_strong_limit_up
    acceleration_break = pl.col("_prev_limit_up").fill_null(False) & ~next_continue_accelerating

    hist = hist.with_columns([
        acceleration_break.alias("signal_exit_acceleration_break_v2"),
        (pl.col("_entry_signal_t1") & ~next_continue_accelerating)
        .alias("signal_exit_next_day_not_accelerating_v2"),
    ])

    return hist.filter(
        pl.col("signal_entry_auction_acceleration_relay_v2")
        | pl.col("signal_exit_next_day_not_accelerating_v2")
        | pl.col("signal_exit_acceleration_break_v2")
    )
