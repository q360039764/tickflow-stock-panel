---
doc_type: feature-ff-note
feature: optimize-auction-strategy-v2
date: 2026-07-01
requirement: copy-auction-strategy
tags: [strategy, backtest, optimization]
---

## 做了什么

基于 2025-07-01 至 2026-07-01 的回测结果，优化 `竞价加速接力模型2.0` 的小微盘过滤条件，将默认最高换手率从 28% 调整为 12%，用于过滤高换手小微盘次日承接断裂。

## 改了哪些

- `data/strategies/custom/auction_acceleration_relay_v2.py:13-181` — 新增 `min_market_limit_up_count` 可调参数，默认保持关闭；将 `max_turnover` 默认值调整为 12，并更新策略描述与规则文本。
- `data/user_data/strategy_overrides/auction_acceleration_relay_v2.json` — 同步页面覆盖配置，清理旧信号和旧参数，使前端详情与策略文件一致。

## 怎么验证的

- `backend/.venv/Scripts/python.exe -m compileall app ..\data\strategies\custom\auction_acceleration_relay_v2.py`
- `uv run --extra dev pytest tests\backtest -q`
- API 回测结果：12 个月收益 21.72%，最大回撤 -4.06%，交易 9 笔；2026Q2 当前无符合条件买点。

## 顺手发现

- `data/user_data/strategy_overrides/auction_acceleration_relay_v2.json` 之前保留了旧版 2.0 的参数和信号，会覆盖策略文件默认值。
