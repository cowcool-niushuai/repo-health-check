# AGENTS.md — 开工说明

## 项目名称
Git 仓库接入体检工具（Repo Health Check）

## 核心目标
为公开 Git 仓库（支持 GitHub 等）提供一键接入体检 Web 服务，自动识别：
1. README、依赖清单、启动脚本的完整度
2. Windows 专用脚本（`.bat` / `.cmd` / `.ps1` / `.vbs`）
3. 硬编码路径（`C:\`、`/Users/`、`/home/` 等绝对路径）
4. 密钥与敏感信息风险（`sk-`、`AKID`、`Bearer`、私钥、`.env` 入库等）

输出可视化体检报告、接入就绪度评分（0-100）、主要风险清单，以及 AI 接入建议（可选大模型增强，无 Key 时自动降级为规则引擎）。

## 技术栈约定
- **后端**：Python 3.10+ + FastAPI + Uvicorn + Pydantic
- **仓库获取**：`git clone --depth 1`（浅克隆到临时目录，扫描后立即清理）
- **前端**：FastAPI 托管单页应用（HTML5 + Tailwind CSS + Chart.js），零构建步骤，开箱即用
- **测试**：`pytest` + `httpx` / `TestClient`

## 开发规范
1. 所有敏感信息扫描结果在返回前端前**必须脱敏**，禁止明文回传真实密钥。
2. 扫描时必须跳过 `.git/`、`node_modules/`、`dist/`、`build/`、`venv/`、`__pycache__/` 等目录及超大文件（>1MB），避免超时和误报。
3. 每次发现并修复 AI 生成代码的缺陷或误报，必须记录到 `docs/错误台账.md` 与 `AI_USAGE.md`。
