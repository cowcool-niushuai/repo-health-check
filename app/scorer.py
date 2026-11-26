"""Explainable onboarding readiness scoring model."""

from typing import Literal

from app.models import CompletenessDetail, RiskItem, ScoreBreakdown


def _grade_for_score(score: int) -> Literal["A", "B", "C", "D"]:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def calculate_score(completeness: CompletenessDetail, risks: list[RiskItem]) -> ScoreBreakdown:
    """Compute overall readiness score, per-dimension scores, and deduction notes."""
    deductions: list[str] = []

    # 1. Completeness (0-100) + overall completeness penalty in a single pass
    comp_score = 100
    comp_penalty = 0
    completeness_rules = [
        (not completeness.has_readme, 35, 15, "缺少 README 文档（-15 总分）"),
        (
            completeness.has_readme and not any(completeness.readme_sections.values()),
            10,
            4,
            "README 缺少安装或启动章节（-4 总分）",
        ),
        (not completeness.has_dependencies, 35, 18, "缺少依赖清单文件（-18 总分）"),
        (not completeness.has_startup, 30, 12, "缺少启动脚本或容器配置（-12 总分）"),
    ]
    for failed, dim_drop, total_drop, note in completeness_rules:
        if failed:
            comp_score -= dim_drop
            comp_penalty += total_drop
            deductions.append(note)
    comp_score = max(0, comp_score)

    # 2. Cross-platform compatibility (0-100) — only penalize Windows scripts without alternatives
    win_risks = [r for r in risks if r.category == "windows_script" and r.severity != "low"]
    cross_score = max(0, 100 - min(len(win_risks) * 25, 80))
    win_penalty = min(len(win_risks) * 6, 15) if win_risks else 0
    if win_risks:
        deductions.append(f"存在 {len(win_risks)} 处无跨平台替代的 Windows 专用脚本（-{win_penalty} 总分）")

    # 3. Path portability (0-100) — only penalize real hardcoded paths in non-doc/non-test code
    code_path_risks = [r for r in risks if r.category == "hardcoded_path" and r.severity != "low"]
    portability_score = max(0, 100 - len(code_path_risks) * 20)
    path_penalty = min(len(code_path_risks) * 4, 15) if code_path_risks else 0
    if code_path_risks:
        deductions.append(f"源码中存在 {len(code_path_risks)} 处硬编码路径（-{path_penalty} 总分）")

    # 4. Security (0-100) — only penalize high-severity (non-test-fixture) secrets
    high_secret_risks = [r for r in risks if r.category == "secret" and r.severity == "high"]
    security_score = max(0, 100 - len(high_secret_risks) * 40)
    secret_penalty = min(len(high_secret_risks) * 20, 45) if high_secret_risks else 0
    if high_secret_risks:
        deductions.append(f"检测到 {len(high_secret_risks)} 处高危密钥/敏感文件风险（-{secret_penalty} 总分）")

    overall = max(0, 100 - comp_penalty - win_penalty - path_penalty - secret_penalty)
    if high_secret_risks and overall > 59:
        overall = 59
        deductions.append("存在高危密钥泄露风险，接入就绪度总分封顶 59 分")

    return ScoreBreakdown(
        overall_score=overall,
        grade=_grade_for_score(overall),
        dimension_scores={
            "completeness": comp_score,
            "cross_platform": cross_score,
            "portability": portability_score,
            "security": security_score,
        },
        deductions=deductions,
    )
