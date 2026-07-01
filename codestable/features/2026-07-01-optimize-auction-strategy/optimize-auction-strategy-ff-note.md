---
doc_type: feature-ff-note
feature: optimize-auction-strategy
date: 2026-07-01
requirement: copy-auction-strategy
tags: [strategy, backtest, optimization]
---

## 做了什么

基于 2025-07-01 至 2026-07-01 的回测结果，给 `竞价加速接力模型3.0` 增加了信号日全市场涨停家数过滤，默认阈值设为 60 家，用于过滤接力情绪不足的交易日。

## 改了哪些

- `data/strategies/custom/auction_acceleration_relay_v3.py:13-175` — 新增 `min_market_limit_up_count` 参数，加入全市场涨停家数聚合和入场条件。
- `data/strategies/custom/auction_acceleration_relay_v3.py:16-90` — 更新策略描述、版本号和规则文本。

## 怎么验证的

- `backend/.venv/Scripts/python.exe -m compileall app ..\data\strategies\custom\auction_acceleration_relay_v3.py`
- `uv run --extra dev pytest tests\backtest -q`
- API 回测结果：近三个月收益 48.09%，最大回撤 -9.24%；12 个月收益 66.02%，最大回撤 -21.50%。

## 顺手发现

- `backend/app/backtest/engine.py` 仍有既有修改，和本次策略优化无关。
