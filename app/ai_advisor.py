"""AI onboarding advisor with OpenAI-compatible LLM support and rule-based fallback."""

import json
import os
import re

import httpx

from app.models import AIAdvice, CompletenessDetail, RiskItem, ScoreBreakdown


def _parse_json_content(raw: str) -> dict:
    """Parse JSON from model output, stripping optional markdown code fences."""
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return json.loads(cleaned)


def _rule_based_advice(
    repo_name: str,
    completeness: CompletenessDetail,
    score: ScoreBreakdown,
    risks: list[RiskItem],
    error_note: str | None = None,
) -> AIAdvice:
    """Generate deterministic onboarding recommendations when no LLM key is configured."""
    commands: list[str] = []
    dep_set = {f.lower() for f in completeness.dependency_files}
    startup_set = {f.lower() for f in completeness.startup_files}

    if any("dockerfile" in f or "compose" in f for f in startup_set):
        commands.append("docker compose up --build  # 或 docker build -t app .")
    if any("package.json" in f for f in dep_set):
        commands.append("npm install && npm run dev  # 或 npm start")
    if any("requirements.txt" in f for f in dep_set):
        commands.append("pip install -r requirements.txt")
    if any("pyproject.toml" in f for f in dep_set):
        commands.append("pip install -e .")
    if any("go.mod" in f for f in dep_set):
        commands.append("go mod download && go run .")
    if not commands:
        commands.append("仓库缺少标准依赖或启动清单，请先人工核对入口文件与运行环境。")

    actions: list[str] = []
    secret_count = sum(1 for r in risks if r.category == "secret" and r.severity == "high")
    win_count = sum(1 for r in risks if r.category == "windows_script" and r.severity != "low")
    path_count = sum(1 for r in risks if r.category == "hardcoded_path" and r.severity != "low")

    if secret_count:
        actions.append(f"优先处理 {secret_count} 处密钥/敏感文件风险：轮换泄露凭证并清理 Git 历史。")
    if path_count:
        actions.append(f"修复 {path_count} 处硬编码路径，改为环境变量或相对路径以保证跨机可移植。")
    if win_count:
        actions.append(f"为 {win_count} 处 Windows 脚本补充 Linux/macOS 兼容启动脚本或 Dockerfile。")
    if not completeness.has_readme or not completeness.has_dependencies:
        actions.append("补齐 README 启动文档与依赖清单，降低后续接入与 CI/CD 集成成本。")
    if not actions:
        actions.append("当前仓库工程规范良好，可直接进入沙箱试跑与 CI 集成。")

    summary = (
        f"仓库 `{repo_name}` 接入就绪度为 {score.overall_score}/100（等级 {score.grade}）。"
        + ("存在高危密钥风险，建议整改后再接入。" if secret_count else "整体结构清晰，按建议微调后即可接入。")
    )
    return AIAdvice(
        source="rule_engine",
        ai_score=score.overall_score,
        summary=summary,
        recommended_commands=commands[:3],
        top_actions=actions[:3],
        error_note=error_note,
    )


async def generate_advice(
    repo_name: str,
    top_level_entries: list[str],
    readme_excerpt: str,
    completeness: CompletenessDetail,
    score: ScoreBreakdown,
    risks: list[RiskItem],
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
) -> AIAdvice:
    """Call an OpenAI-compatible LLM if configured, otherwise fall back to rule engine."""
    api_key = (llm_api_key or os.getenv("LLM_API_KEY", "")).strip()
    if not api_key:
        return _rule_based_advice(repo_name, completeness, score, risks)

    base_url = (llm_base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")).strip().rstrip("/")
    model = (llm_model or os.getenv("LLM_MODEL", "gpt-4o-mini")).strip()

    risk_summary = [
        f"[{r.severity}] {r.category}: {r.file} - {r.title}"
        for r in risks[:10]
    ]

    prompt = (
        f"你是资深 DevOps 与代码接入评审专家。请根据以下仓库体检信息，输出严格 JSON："
        f'{{"ai_score": 0到100整数, "summary": "一句话评价(60字内)", "recommended_commands": ["命令1", "命令2"], "top_actions": ["建议1", "建议2", "建议3"]}}。\n'
        f"仓库: {repo_name}\n"
        f"顶层目录: {', '.join(top_level_entries)}\n"
        f"依赖清单: {', '.join(completeness.dependency_files) or '无'}\n"
        f"启动脚本: {', '.join(completeness.startup_files) or '无'}\n"
        f"规则评分: {score.overall_score}/100 ({score.grade}), 扣分项: {score.deductions}\n"
        f"主要风险(前10条): {risk_summary}\n"
        f"README节选: {readme_excerpt[:600]}\n"
    )

    try:
        try:
            client_cm = httpx.AsyncClient(timeout=15.0)
        except ImportError:
            client_cm = httpx.AsyncClient(timeout=15.0, trust_env=False)

        async with client_cm as client:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            }
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if resp.status_code == 400:
                payload.pop("response_format", None)
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = _parse_json_content(content)
            raw_ai_score = parsed.get("ai_score")
            ai_score = int(raw_ai_score) if isinstance(raw_ai_score, (int, float)) else score.overall_score
            ai_score = max(0, min(100, ai_score))
            return AIAdvice(
                source="llm",
                ai_score=ai_score,
                summary=str(parsed.get("summary", ""))[:200] or "AI 建议生成完成。",
                recommended_commands=[str(c) for c in parsed.get("recommended_commands", [])[:3]],
                top_actions=[str(a) for a in parsed.get("top_actions", [])[:3]],
            )
    except Exception as exc:
        return _rule_based_advice(
            repo_name,
            completeness,
            score,
            risks,
            error_note=f"AI 接口调用失败（{type(exc).__name__}: {str(exc)[:100]}），已自动降级为规则引擎建议。",
        )
