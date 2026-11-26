# PRD — 需求文档

## 1. 产品定位
面向研发团队与平台接入方的 **Git 仓库接入前自动化体检工具**，在拉取或部署外部/历史仓库前快速评估可运行性、跨平台兼容性与安全风险。

## 2. 功能需求
1. **仓库输入**：支持用户输入公开 Git 仓库 URL（优先支持 GitHub，兼容 GitLab/Gitee HTTPS 链接），并提供示例仓库一键体检。
2. **四大维度扫描**：
   - **完整度检查**：README 存在性与内容关键章节（安装/运行/环境变量）、依赖清单（`package.json`/`requirements.txt`/`pyproject.toml`/`go.mod`/`Cargo.toml`/`pom.xml` 等）、启动脚本或容器配置（`Dockerfile`/`docker-compose.yml`/`Makefile`/`start.sh`/`package.json scripts`）。
   - **Windows 专用脚本**：识别 `.bat`、`.cmd`、`.ps1`、`.vbs` 文件及脚本中的 Windows 专属命令。
   - **硬编码路径**：识别 `C:\`、`D:\`、`/Users/xxx`、`/home/xxx` 等本地绝对路径，排除 URL 与构建缓存目录。
   - **密钥与敏感信息**：识别 `.env`/私钥文件入库及 `sk-`、`AKID`、`AKIA`、`ghp_`、`Bearer`、`PRIVATE KEY` 等敏感凭证，展示时强制脱敏。
3. **评分与风险清单**：输出 0-100 接入就绪度评分、等级（A/B/C/D）、扣分明细与按严重程度分级的主要风险清单。
4. **AI 接入建议（加分项）**：结合仓库目录结构、README 与风险统计生成接入建议（支持 OpenAI 兼容 LLM，未配置 Key 时优雅降级为规则引擎）。
5. **非功能要求**：克隆与扫描超时控制、临时目录自动清理、不泄露明文密钥。
