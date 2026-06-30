# TickFlow Stock Panel CodeStable Notes

## 项目约定

本仓库当前使用 `codestable/` 作为 CodeStable 项目资料目录，`setup_codestable.bat` 也按该目录生成资料。后续继续沿用 `codestable/`，避免同时生成 `.codestable/` 与 `codestable/` 两套资料。

## 常用命令

后端语法验证：

```powershell
cd E:\myWork\tickflow-stock-panel\backend
.\.venv\Scripts\python.exe -m compileall app
```

前端构建验证：

```powershell
cd E:\myWork\tickflow-stock-panel\frontend
nvm use 24.4.1
npm run build
```

Windows 一键开发启动：

```powershell
.\dev.ps1
```

## 脚本职责

`dev.ps1` 和 `dev.sh` 负责一键启动前后端开发服务，包含依赖检查、端口处理、FastAPI 与 Vite 启动。

`setup_codestable.bat` 负责安装 CodeStable 技能到 `.agents/skills/`，并创建 `codestable/` 项目资料目录。

`backend/app/main.py` 是 FastAPI 主入口，初始化数据层、能力、行情服务、调度器、策略引擎、监控规则和路由。

`backend/scripts/cleanup_halt_days.py` 是历史停牌脏数据清理脚本，用于处理 open/high 为 0 的日 K 分区。

`frontend/package.json` 提供 `dev`、`build`、`preview`、`lint` 等前端命令。
