# AI_USAGE.md — AI 辅助开发与验证记录

## 1. 使用的 AI 工具与分工
- **AI Coding 助手**：用于项目目录脚手架搭建、FastAPI 后端模块编写（浅克隆、四维扫描器、评分模型、AI 建议模块）、前端可视化页面（Tailwind + Apache ECharts + Lucide）以及 `pytest` 测试用例生成。

## 2. 如何验证 AI 输出
1. **自动化单元测试与集成测试**：编写 `tests/test_scanner.py` 与 `tests/test_api.py`，构造包含真实风险特征（`.bat`、`C:\Users\...`、`/Users/...`、`sk-` 密钥、`.env`）与占位符样本的临时仓库，验证召回率、脱敏逻辑与总分封顶规则。
2. **安全边界审查**：人工核查 URL 校验逻辑（防止 SSRF 访问 `localhost` / 内网 IP 以及命令注入）与密钥展示逻辑（确保不返回明文）。
3. **端到端联调**：本地启动 `uvicorn` 服务并通过浏览器/接口实测，确认图表渲染、筛选与报错提示正常。

## 3. 「AI 答错但被你发现」的真实记录

### 记录 1：密钥扫描返回明文片段，存在二次泄露风险
- **AI 初始行为**：AI 生成的密钥扫描逻辑在命中 `sk-` 或 `Bearer` 时，直接把整行 `line.strip()[:160]` 塞进 `RiskItem.snippet` 返回给前端展示。
- **被发现的问题**：如果把真实泄露的密钥原封不动返回并在网页渲染、写入报告缓存，相当于在体检平台里二次暴露了明文密钥，且容易触发安全审计风险。
- **人工修正**：在 `app/scanner.py` 中新增 `mask_secret()` 与 `_mask_line_secrets()`，在生成 `RiskItem` 前将命中密钥的中间字符替换为 `****`（仅保留前后少量字符用于定位），并在单元测试中显式断言明文片段不会出现在返回结果中。

### 记录 2：真实仓库实测暴露的误报与过严评分问题（测试夹具 / Sphinx 脚本 / 代码注释）
- **AI 初始行为**：用 `pallets/flask` 和 `psf/requests` 两个真实开源仓库实测时，AI 初始规则把 `tests/certs/*.key`、`tests/test_apps/.env`（单元测试夹具）直接判为高危密钥泄露导致总分封顶 59 / 41 分；把已有 `docs/Makefile` 同目录的 Sphinx `docs/make.bat` 判为跨平台阻塞；把 `src/requests/utils.py` 注释中的 `"C:\foo\bar.txt"` 和 `../../../../home/username/.bashrc` 相对路径判为硬编码路径。
- **被发现的问题**：头部成熟开源项目被打出 C/D 不及格分，严重失真。
- **人工修正**：
  1. 在 `app/scanner.py` 中增加 `_is_test_or_fixture_path()`，将 `tests/`、`fixtures/`、`certs/` 下的敏感文件降级为 `low` 提示且不触发总分封顶，同时避免同一 `.key` 文件按文件名和内容重复报警；
  2. 增加 `_has_cross_platform_sibling()`，同目录已有 `Makefile` / `.sh` 的 `.bat` 脚本降级为 `low`；
  3. 增加 `_COMMENT_LINE_RE` 跳过源码纯注释行，并修复 `_UNIX_HOME_RE` 防止把 `../../../../home/...` 相对路径误判为绝对路径。
- **验证结果**：重新实测 `pallets/flask` 与 `psf/requests` 均恢复为 **100 分（A 级）**，同时仍能准确列出测试夹具与文档路径作为 `low` 级别提示；`pytest` 6 项测试全部通过。

### 记录 3：同一行多种密钥只脱敏了第一种
- **AI 初始行为**：密钥扫描命中一行后，仅用当前命中的那一条正则做 `_mask_line_secrets(line, pattern)`，随后 `break`。
- **被发现的问题**：当一行同时出现 `sk-...` 与 `AKIA...` 时，`AKIA...` 会以明文进入接口响应和页面，直接违反「返回前端前必须脱敏」的安全要求。
- **人工修正**：将脱敏函数改为遍历全部密钥正则后再截断，并新增同行双密钥的回归测试。

### 记录 4：端到端安全测试发现 SSRF、符号链接越界与非 UTF-8 文件名 500
- **AI 初始行为**：`/api/scan` 信任请求体里的 `llm_base_url` 并直接请求；扫描器用 `read_text()` 跟随仓库内符号链接；响应中的文件路径直接使用 `os.walk` 解码结果。
- **被发现的问题**：① 用户可让服务端 POST 到 `127.0.0.1`/内网地址（实测成功命中本地监听器）并回显响应；② 仓库内 `secret.py -> /tmp/hostsecret.txt` 可读到克隆目录外的宿主文件；③ 非 UTF-8 文件名使 `json.dumps(...).encode()` 抛 `UnicodeEncodeError`，接口返回 500（已用 TestClient 复现）。
- **人工修正**：新增 `_validate_llm_base_url()` 内网地址拦截与 `follow_redirects=False`；新增 `_is_within_root()` 读取前校验真实路径；新增 `_safe_text()` 清洗文件名；同时补充 `.dockerignore` 防止 `.env` 与 `.venv` 进入镜像。
- **验证结果**：三类问题均补充回归测试，`pytest` 由 10 项增至 16 项并全部通过；`pallets/flask`、`psf/requests` 仍为 100 分。
