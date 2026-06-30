---
doc_type: issue-fix
status: verified
date: 2026-06-30
slug: kline-minute-index-sync
---

# 实时分钟 K 与指数日 K 同步修复记录

## 问题

实时分钟 K 请求 `/api/kline/minute?symbol=601975.SH&date=2026-06-30` 返回空数据，图表无法显示。

指数日 K 同步在前端表现为报错，后端实测长时间运行后可以完成，问题主要来自同步请求耗时约数分钟。

## 根因

本地分钟 K 走 `local_projects` 数据源时，`tdx_level1.fetch_level1_ticks()` 对部分当日标的返回 0 条记录，后端最终返回 `source=none`。

`/api/index/sync_daily` 原实现为同步 HTTP 请求，需要拉取并计算大量指数，容易触发前端请求等待失败。

## 修复

`backend/app/data_providers/tdx_level1.py` 在通达信 Level1 分钟数据为空时，增加东方财富 `trends2` 分时接口回退，并继续写入既有 `kline_minute/date=.../part.parquet` 缓存。

`backend/app/api/indices.py` 将指数日 K 同步接口改为后台任务，复用 `job_store` 与 `/api/pipeline/jobs/{job_id}` 轮询模型。

`backend/app/services/index_sync.py` 为指数批次同步增加进度回调，供后台任务写入阶段进度。

`frontend/src/lib/api.ts`、`frontend/src/pages/Data.tsx`、`frontend/src/pages/Indices.tsx` 调整为接收 `job_id` 并按任务状态刷新页面数据。

## 验证

已执行：

```powershell
cd E:\myWork\tickflow-stock-panel\backend
.\.venv\Scripts\python.exe -m compileall app
```

```powershell
cd E:\myWork\tickflow-stock-panel\frontend
nvm use 24.4.1
npm run build
```

接口验证结果：

`/api/kline/minute?symbol=601975.SH&date=2026-06-30` 返回 115 行。

`POST /api/index/sync_daily?days=30` 先返回 `{status: "started", job_id: "91e44a93dd"}`，随后任务完成，`/api/pipeline/jobs/91e44a93dd` 返回 `succeeded`、`index_count: 2172`、`rows_written: 12411`。