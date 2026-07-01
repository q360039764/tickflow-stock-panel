---
doc_type: feature-review
feature: 2026-07-01-copy-auction-strategy
status: passed
reviewer: self
reviewed: 2026-07-01
round: 1
---

# copy-auction-strategy 代码审查报告

## 1. Scope And Inputs

- Design: 原贴量化规则中的自由流通市值 28 亿分池
- Checklist: none
- Evidence pack: none
- Gate results: start gate passed；commit gate未使用独立审查降级授权
- DoD results: none
- Implementation evidence: `copy-auction-strategy-ff-note.md` 与本轮信号统计、回测输出
- Diff basis: 2.0 与 3.0 按小微盘、非小微盘分别审查
- Baseline dirty files: `backend/app/backtest/engine.py`，与本次策略复制无关，未修改

### Independent Review

- Detection: 当前会话未授权启动子代理；OCR CLI 不可用
- 环节 A 独立隔离 Task agent: local-only + skipped-by-user-policy
- 环节 B OCR CLI: not-available
- OCR severity mapping: High→blocking/important，Medium→nit/suggestion，Low→discarded
- Merge policy: 仅执行本地事实核验
- Gate effect: 自动提交门禁需要独立审查或显式降级授权；本次未请求提交

## 2. Diff Summary

- 修改：`data/strategies/custom/auction_acceleration_relay_v2.py`
- 修改：`data/strategies/custom/auction_acceleration_relay_v3.py`
- 删除：none
- 未跟踪：`copy-auction-strategy-ff-note.md`、本审查报告；策略目录受 `.gitignore` 管理
- 关注点：真实竞价区间、竞价成交额占比和人气排名尚未进入日线回测面板，当前版本使用日线代理字段。

## 3. Findings

### blocking

none

### important

- 原 3.0 与 2.0 业务逻辑完全一致，未体现原贴的 28 亿市值分池；本轮已拆分。
- 原最长持有 1 天会覆盖“次日继续加速则持有”的卖出纪律；本轮改为 3 天，并保留停止加速退出信号。

### nit

none

### suggestion

none

### learning

- `data/strategies/custom/` 受 `.gitignore` 管理，后续提交策略时需要显式加入。

### praise

- 两版策略 ID、买卖信号和市值边界互相独立，28 亿边界归入 3.0。

## 4. Test And QA Focus

- QA 重点复核：`StrategyEngine` 同时加载两版，且 2.0 买入市值 `<28 亿`、3.0 买入市值 `>=28 亿`。
- Evidence pack residual risks / gate warnings：独立审查未执行，不影响本地使用。
- 验证结果：两版 Python 编译通过；同区间回测分别产生 2 笔和 6 笔成交。
- 后续需要真实竞价历史数据才能验证竞价区间与竞价量比规则。

## 5. Residual Risk

- 当前策略仍属于日线代理模型，不能等同于原贴的 9:25 竞价与开盘前几分钟盘口模型。

## 6. Verdict

- Status: passed
- Next: 两版分池和回测验证完成；真实竞价历史数据接入属于后续数据能力。
