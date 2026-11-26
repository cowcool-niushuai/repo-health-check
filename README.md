# Git 仓库接入体检工具（Repo Health Check）

输入一个公开 Git 仓库链接（支持 GitHub 等），自动分析工程完整度、跨平台兼容性、硬编码路径与密钥泄露风险，输出可视化体检报告、接入就绪度评分与整改建议。

## 核心功能

1. **工程完整度检查**：自动识别 `README`（及安装/使用/环境变量章节）、主流依赖清单（`package.json`、`requirements.txt`、`pyproject.toml`、`go.mod`、`Cargo.toml`、`pom.xml` 等）以及启动配置（`Dockerfile`、`docker-compose.yml`、`Makefile`、`start.sh`、`package.json scripts`）。
2. **Windows 专用脚本识别**：检测 `.bat` / `.cmd` / `.ps1` / `.vbs` 文件以及脚本中的 `cmd /c`、`powershell`、`set VAR=` 等 Windows 专属命令。
3. **硬编码路径扫描**：识别 `C:\...`、`/Users/...`、`/home/...` 等本地绝对路径，区分代码/配置与文档示例，降低误报。
4. **密钥与敏感信息检测（脱敏展示）**：检测 `.env` / 私钥文件直接入库，以及 `sk-`、`AKID`、`AKIA`、`ghp_`、`Bearer`、`PRIVATE KEY` 等敏感凭证，返回前端前强制中间打码脱敏。
5. **可视化报告与 AI 接入建议**：基于 Apache ECharts 提供就绪度评分仪表盘、四维度雷达图与可筛选/搜索的风险清单（支持一键导出 Markdown）；支持通过页面或环境变量配置 OpenAI / DeepSeek 兼容大模型生成 AI 评分与接入建议，未配置 Key 时自动降级为规则引擎。

## 快速启动

### 方式一：本地 Python 启动
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
启动后访问：`http://localhost:8000`

### 方式二：Docker Compose 启动
```bash
docker compose up --build
```

### 可选：开启 AI 大模型建议
```bash
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_MODEL="gpt-4o-mini"
```

### 运行测试
```bash
pytest -v
```

---

## 更新跟踪设计说明（架构设计）

若原仓库后续有代码更新，系统可通过以下机制实现低成本、可溯源的增量更新：

1. **如何发现更新**
   - **被动推送（实时）**：针对接入管理的仓库配置 GitHub Webhook，监听 `push` 事件，收到事件后立即入队触发复检。
   - **主动轮询（兜底）**：对未配置 Webhook 的公开仓库，后台定时任务通过轻量级 `git ls-remote <repo_url> HEAD` 或带 `If-None-Match` (ETag) 请求头的 GitHub Commits API 检查最新 Commit SHA（304 Not Modified 不消耗 API 配额），仅当远端 SHA 与本地记录不一致时才触发拉取。

2. **如何避免全量重算**
   - **Commit 级结果缓存**：以 `(repo_url, commit_sha)` 作为唯一缓存键；若目标 Commit 已扫描过，直接命中缓存秒级返回历史报告。
   - **文件级增量 Diff**：当出现新 Commit（`old_sha -> new_sha`）时，通过 `git diff --name-only <old_sha> <new_sha>`（或 GitHub Compare API）仅提取发生 `Added / Modified / Deleted` 的文件集合：
     - 仅对变更文件重跑正则扫描（硬编码路径、密钥、Windows 脚本），合并替换旧报告中对应文件的风险项；
     - 仅当 `README`、依赖清单或顶层目录结构发生变化时，才重新计算完整度或调用 AI 接口生成建议，大幅降低 Token 与 CPU 消耗。

3. **报告如何标注所基于的 Commit**
   - 每次扫描报告均在元数据区（`meta`）与页面顶部显著标注：
     - `branch` 与完整 40 位 `commit_sha`（界面展示 7 位短 SHA，如 `main @ 1234567`）；
     - `scanned_at`（UTC 扫描时间戳）；
     - 若后续轮询发现远端已有更新但尚未完成复检，界面标注「当前报告基于旧提交 `1234567`，远端已有新提交」。

4. **失败或限流时怎么兜底**
   - **API 限流处理**：服务端配置 `GITHUB_TOKEN` 将 API 配额提升至 5000 次/小时；若仍触发 `403 / 429` 限流，读取响应头 `X-RateLimit-Reset` 执行指数退避重试，并自动切换为 `git ls-remote` / `git clone --depth 1` 走 Git 协议拉取，不强依赖 REST API。
   - **拉取/AI 失败降级**：若目标仓库临时不可达、克隆超时或 LLM 接口异常，保留并返回最近一次成功的报告快照，同时在页面顶部提示「更新检查失败，当前展示为历史快照（基于 commit `xxxxxxx`）」；AI 建议模块自动降级为本地规则引擎输出，保证核心体检报告可用。
