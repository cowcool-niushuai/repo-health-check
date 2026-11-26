"""FastAPI entrypoint for the Git Repository Health Check web application."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.ai_advisor import generate_advice
from app.git_fetcher import GitFetchError, cloned_repo
from app.models import RepoMeta, ScanReport, ScanRequest
from app.scanner import ScanOutput, scan_repository
from app.scorer import calculate_score

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Git 仓库接入体检工具",
    description="自动识别 Git 仓库完整度、Windows 专用脚本、硬编码路径与密钥泄露风险",
    version="1.0.0",
)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the single-page web UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness check endpoint."""
    return {"status": "ok"}


def _clone_and_scan(repo_url: str) -> tuple[RepoMeta, ScanOutput]:
    """Run blocking git clone and repository scan inside a worker thread."""
    with cloned_repo(repo_url) as repo_info:
        scan_out = scan_repository(repo_info.path)
        meta = RepoMeta(
            repo_url=repo_info.repo_url,
            repo_name=repo_info.repo_name,
            branch=repo_info.branch,
            commit_sha=repo_info.commit_sha,
            short_sha=repo_info.short_sha,
            scanned_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            files_scanned=scan_out.files_scanned,
        )
        return meta, scan_out


@app.post("/api/scan", response_model=ScanReport)
async def scan_repo_endpoint(req: ScanRequest) -> ScanReport:
    """Clone and scan a public Git repository, returning a structured health report."""
    try:
        meta, scan_out = await asyncio.to_thread(_clone_and_scan, req.repo_url)
    except GitFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    score = calculate_score(scan_out.completeness, scan_out.risks)
    risk_counts = {
        "high": sum(1 for r in scan_out.risks if r.severity == "high"),
        "medium": sum(1 for r in scan_out.risks if r.severity == "medium"),
        "low": sum(1 for r in scan_out.risks if r.severity == "low"),
        "total": len(scan_out.risks),
    }

    advice = await generate_advice(
        repo_name=meta.repo_name,
        top_level_entries=scan_out.top_level_entries,
        readme_excerpt=scan_out.readme_excerpt,
        completeness=scan_out.completeness,
        score=score,
        risks=scan_out.risks,
        llm_api_key=req.llm_api_key,
        llm_base_url=req.llm_base_url,
        llm_model=req.llm_model,
    )

    return ScanReport(
        meta=meta,
        completeness=scan_out.completeness,
        score=score,
        risks=scan_out.risks,
        risk_counts=risk_counts,
        ai_advice=advice,
    )
