"""成交额热度接力策略。
核心规则：最近 10 个交易日内，全市场成交额进入 Top20 的天数达到阈值后纳入候选。
"""
import polars as pl

# 策略元信息
META = {
    "id": "top20_amount_heat",
    "name": "成交额热度接力",
    "description": "最近10个交易日内，全市场成交额Top20命中8天及以上的高热度标的",
    "tags": ["成交额", "热度", "趋势", "量能"],
    "basic_filter": {
        # 基础交易门槛，保留可交易性较强的标的
        "price_min": 5,
        "price_max": 300,
        "market_cap_min": 20e8,
        "amount_min": 1e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        # 10日窗口内，至少命中 Top20 的天数
        {
            "id": "min_top20_days_10d",
            "label": "10日内Top20最少天数",
            "type": "int",
            "default": 8,
            "min": 1,
            "max": 10,
            "step": 1,
        },
    ],
    # 评分优先保留近期热度延续更强、量能放大更明显、趋势更顺的标的
    "scoring": {
        "_top20_days_10d": 0.50,
        "vol_ratio_5d": 0.25,
        "momentum_10d": 0.25,
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

# 回看窗口需要覆盖 10 日统计窗口，并留出少量余量
LOOKBACK_DAYS = 15

# 这一版策略以历史窗口过滤为主，入口信号先留空
ENTRY_SIGNALS = []
EXIT_SIGNALS = ["signal_ma20_breakdown"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 15
ALERTS = []

RULES = """
1. 先按全市场当日成交额做横截面排序，`amount` 排名 `<= 20` 记为当天热度命中。
2. 以最近 10 个交易日为窗口，热度命中天数达到阈值后进入候选池。
3. 评分优先保留近期热度延续更强、量能放大更明显、趋势更顺的标的。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """保留最近 10 个交易日内热度命中次数达到阈值的标的。"""
    min_days = int(params.get("min_top20_days_10d", 8))
    return pl.col("_top20_days_10d") >= min_days


def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """计算全市场成交额热度，并保留最近窗口内达标的历史行。

    df 需要包含至少 `symbol`、`date`、`amount` 三列。
    返回结果保留所有命中行，策略引擎会在后续自动收敛到目标日期。
    """
    if df.is_empty() or "date" not in df.columns or "amount" not in df.columns:
        return df.head(0)

    min_days = int(params.get("min_top20_days_10d", 8))

    hist = (
        df.sort(["symbol", "date"])
        .with_columns([
            # 当日全市场成交额横截面排名，按 dense rank 处理同额情况
            pl.col("amount")
            .fill_null(0)
            .rank(method="dense", descending=True)
            .over("date")
            .alias("_amount_rank"),
        ])
        .with_columns([
            # 当天是否进入全市场成交额 Top20
            (pl.col("_amount_rank") <= 20)
            .fill_null(False)
            .alias("_is_top20_amount"),
        ])
        .with_columns([
            # 最近 10 个交易日的 Top20 命中次数
            pl.col("_is_top20_amount")
            .cast(pl.Int64)
            .rolling_sum(window_size=10)
            .over("symbol")
            .fill_null(0)
            .alias("_top20_days_10d"),
            # 辅助排序：最近 5 个交易日的热度延续
            pl.col("_is_top20_amount")
            .cast(pl.Int64)
            .rolling_sum(window_size=5)
            .over("symbol")
            .fill_null(0)
            .alias("_top20_days_5d"),
        ])
    )

    return hist.filter(pl.col("_top20_days_10d") >= min_days)
