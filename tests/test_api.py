"""Integration tests for FastAPI endpoints."""

import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.ai_advisor import generate_advice
from app.git_fetcher import ClonedRepoInfo, validate_git_url
from app.main import app
from app.models import CompletenessDetail, ScoreBreakdown

client = TestClient(app)


def test_health_and_index():
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.json() == {"status": "ok"}

    res_index = client.get("/")
    assert res_index.status_code == 200
    assert "Repo Health Check" in res_index.text


def test_scan_rejects_invalid_and_private_urls():
    for bad_url in ["", "not-a-url", "http://127.0.0.1/repo", "http://localhost/test"]:
        res = client.post("/api/scan", json={"repo_url": bad_url})
        assert res.status_code in {400, 422}


def test_scan_endpoint_with_mocked_clone(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Demo\n## Install\npip install -r requirements.txt\n## Usage\npython main.py\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "start.sh").write_text("#!/bin/bash\nuvicorn main:app\n", encoding="utf-8")

    @contextmanager
    def _fake_clone(url: str):
        yield ClonedRepoInfo(
            path=tmp_path,
            repo_url=url,
            repo_name="demo/repo",
            branch="main",
            commit_sha="1234567890abcdef1234567890abcdef12345678",
            short_sha="1234567",
        )

    with patch("app.main.cloned_repo", side_effect=_fake_clone):
        res = client.post("/api/scan", json={"repo_url": "https://github.com/demo/repo"})

    assert res.status_code == 200
    data = res.json()
    assert data["meta"]["short_sha"] == "1234567"
    assert data["score"]["overall_score"] == 100
    assert data["score"]["grade"] == "A"
    assert data["ai_advice"]["source"] == "rule_engine"


def test_validate_git_url_strips_tree_path():
    assert validate_git_url("https://github.com/pallets/flask/tree/main") == "https://github.com/pallets/flask"


def test_ai_advisor_llm_and_error_fallback():
    comp = CompletenessDetail(has_readme=True, has_dependencies=True, has_startup=True)
    score = ScoreBreakdown(
        overall_score=90,
        grade="A",
        dimension_scores={"completeness": 100, "cross_platform": 100, "portability": 100, "security": 100},
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"ai_score": 92, "summary": "结构清晰", "recommended_commands": ["pytest"], "top_actions": ["无"]}'}}]
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("app.ai_advisor.httpx.AsyncClient", return_value=mock_client):
        advice = asyncio.run(generate_advice("demo/repo", ["README.md"], "demo", comp, score, [], llm_api_key="sk-test"))
    assert advice.source == "llm"
    assert advice.ai_score == 92

    mock_client.post = AsyncMock(side_effect=RuntimeError("timeout"))
    with patch("app.ai_advisor.httpx.AsyncClient", return_value=mock_client):
        fallback = asyncio.run(generate_advice("demo/repo", ["README.md"], "demo", comp, score, [], llm_api_key="sk-test"))
    assert fallback.source == "rule_engine"
    assert fallback.error_note is not None


def test_llm_base_url_rejects_internal_targets():
    comp = CompletenessDetail(has_readme=True, has_dependencies=True, has_startup=True)
    score = ScoreBreakdown(
        overall_score=90,
        grade="A",
        dimension_scores={"completeness": 100, "cross_platform": 100, "portability": 100, "security": 100},
    )

    with patch("app.ai_advisor.httpx.AsyncClient") as mock_client:
        advice = asyncio.run(
            generate_advice(
                "demo/repo",
                ["README.md"],
                "demo",
                comp,
                score,
                [],
                llm_api_key="sk-test",
                llm_base_url="http://127.0.0.1:8123/v1",
            )
        )

    assert advice.source == "rule_engine"
    assert "内网" in (advice.error_note or "")
    mock_client.assert_not_called()
