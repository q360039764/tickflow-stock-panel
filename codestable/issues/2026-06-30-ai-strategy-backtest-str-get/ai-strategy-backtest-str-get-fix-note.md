---
doc_type: issue-fix
status: completed
severity: P1
tags:
  - backtest
  - strategy
  - ai-strategy
created_at: 2026-06-30
---

# AI 策略回测 str.get 报错修复记录

## 现象

回测页选择 AI 策略 `ai_mqzzvx1k` 后运行策略回测，后端返回 `'str' object has no attribute 'get'`。

## 根因

`ai_mqzzvx1k.py` 的 `META.params` 为普通字典：

```python
"params": {
    "lookback_days": 10,
    "min_top20_days": 8,
    "top_n": 20,
}
```

回测服务按项目策略规范读取 `META.params`，预期结构为参数定义列表，并在 `backend/app/backtest/strategy.py` 中逐项调用 `param.get("id")`。当 `params` 为字典时，遍历结果是字符串键名，从而触发 `str.get`。

该策略还使用了 AI 常见输出格式：`strategy_id`、`strategy_name`、`META.entry_signals`、`generate_signals()`，当前策略加载器只识别项目规范字段和 `filter/filter_history`，因此需要在加载边界统一规范化。

## 变更

| 文件 | 变更内容 |
| --- | --- |
| `backend/app/strategy/engine.py` | 兼容 `strategy_id/strategy_name` 字段别名；将字典型 `META.params` 转为参数定义列表；从 `META.entry_signals/exit_signals` 读取信号；将 AI 生成的 `generate_signals/build_strategy` 包装为 `filter_history` 候选过滤函数。 |
| `backend/app/api/strategy.py` | 策略详情生成时只使用合法参数定义，避免非列表或非字典参数项影响详情接口。 |
| `backend/app/backtest/strategy.py` | 回测参数解析跳过非字典参数项，避免旧策略对象继续触发 `str.get`。 |
| `backend/tests/backtest/test_ai_strategy_compat.py` | 新增 AI 策略兼容测试，覆盖字典型 `params`、字段别名、`generate_signals` 包装和旧式参数项。 |

## 验证

| 项目 | 结果 |
| --- | --- |
| 后端语法检查 | `cd backend; .\.venv\Scripts\python.exe -m compileall app` 通过。 |
| AI 策略加载 | 使用 `StrategyEngine._load_file()` 加载 `backend/dist/TickFlowStockPanel/data/strategies/ai/ai_mqzzvx1k.py`，得到 `id=ai_mqzzvx1k`、`name=猫猫头策略`、参数定义列表、`filter_history` 可调用。 |
| 旧式参数回测 | 构造 `meta.params=["lookback_days"]` 的策略对象，回测返回 `error=None`。 |
| 真实策略回测 | 使用 `backend/dist/TickFlowStockPanel/data` 的真实数据运行 `ai_mqzzvx1k`，区间 `2026-06-01` 到 `2026-06-30`，返回 `error=None`，生成 10 笔交易。 |
| Pytest | 当前 `.venv` 缺少 `pytest`，`.\.venv\Scripts\python.exe -m pytest ...` 无法运行，错误为 `No module named pytest`。 |

## 后续注意

如果使用已经启动的后端或桌面进程，需要重启进程后加载新的策略引擎代码。
