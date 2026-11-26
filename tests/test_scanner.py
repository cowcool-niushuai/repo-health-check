"""Unit tests for the four-dimension scanner and scoring engine."""

import json
import os
from pathlib import Path

import pytest

from app.scanner import mask_secret, scan_repository
from app.scorer import calculate_score


def test_mask_secret():
    masked = mask_secret("sk-proj-1234567890abcdef123456")
    assert "****" in masked
    assert "1234567890abcdef" not in masked


def test_scanner_detects_all_risk_dimensions(tmp_path: Path):
    # Healthy files
    (tmp_path / "README.md").write_text("# Demo\n## Install\npip install -r requirements.txt\n## Usage\npython app.py", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")

    # Risky files
    (tmp_path / "run.bat").write_text("@echo off\npython app.py\n", encoding="utf-8")
    (tmp_path / ".env").write_text("PORT=8000\n", encoding="utf-8")
    (tmp_path / "config.py").write_text(
        'DATA_DIR = "C:\\\\Users\\\\alice\\\\data"\n'
        'MAC_DIR = "/Users/alice/projects/demo"\n'
        'OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"\n'
        'FAKE_KEY = "sk-your_placeholder_key_here_1234567890"\n',
        encoding="utf-8",
    )

    out = scan_repository(tmp_path)
    assert out.completeness.has_readme is True
    assert out.completeness.has_dependencies is True
    assert out.completeness.has_startup is True

    categories = {r.category for r in out.risks}
    assert "windows_script" in categories
    assert "hardcoded_path" in categories
    assert "secret" in categories

    # Ensure placeholder is filtered and real secret is masked
    secret_snippets = [r.snippet or "" for r in out.risks if r.category == "secret" and r.snippet]
    assert len(secret_snippets) == 1
    assert "abcdefghijklmnopqrstuvwxyz" not in secret_snippets[0]
    assert "****" in secret_snippets[0]

    # Ensure score is capped at 59 when secret risk exists
    score = calculate_score(out.completeness, out.risks)
    assert score.overall_score <= 59
    assert score.grade in {"C", "D"}


def test_healthy_repo_gets_high_score(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Clean App\n## Install\nnpm install\n## Usage\nnpm start\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"clean","scripts":{"start":"node index.js"}}', encoding="utf-8")
    (tmp_path / "index.js").write_text("console.log('hello');\n", encoding="utf-8")

    out = scan_repository(tmp_path)
    score = calculate_score(out.completeness, out.risks)
    assert score.overall_score == 100
    assert score.grade == "A"
    assert out.risks == []


def test_secret_masking_before_truncation_and_path_variants(tmp_path: Path):
    padding = "a" * 125
    (tmp_path / "README.md").write_text("# App\n", encoding="utf-8")
    (tmp_path / "README_zh.md").write_text("## 安装\npip install .\n", encoding="utf-8")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\nWORKDIR /opt/app\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        f'PADDING = "{padding}"; KEY = "sk-proj-secretValueBeyond160Chars123456789"\n'
        'WIN_FWD = "C:/Users/bob/project/data.csv"\n'
        'OPT_PATH = "/opt/legacy-service/config.yml"\n',
        encoding="utf-8",
    )

    out = scan_repository(tmp_path)

    # Multi-README sections merged rather than overwritten
    assert out.completeness.readme_sections["install"] is True
    # examples/requirements.txt should not count as project-level dependency manifest
    assert out.completeness.has_dependencies is False

    # Long line secret must still be masked, never leaking raw prefix at truncation boundary
    secret_risks = [r for r in out.risks if r.category == "secret"]
    assert len(secret_risks) == 1
    assert "sk-proj-secret" not in (secret_risks[0].snippet or "")
    assert "****" in (secret_risks[0].snippet or "")

    # Forward-slash Windows path and /opt/ path in app.py detected; Dockerfile /opt/app ignored
    path_files = [(r.file, r.snippet) for r in out.risks if r.category == "hardcoded_path"]
    assert any(f == "app.py" and "C:/Users/bob" in (s or "") for f, s in path_files)
    assert any(f == "app.py" and "/opt/legacy-service" in (s or "") for f, s in path_files)
    assert not any(f == "Dockerfile" for f, _ in path_files)


def test_noise_filtering_and_deduplication(tmp_path: Path):
    (tmp_path / "README.md").write_text(
        "# Demo\n## Install\npip install -r requirements.txt\n## Usage\nExample: /home/user/demo or C:\\demo\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "start.sh").write_text("#!/bin/bash\npython app.py\n", encoding="utf-8")

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.rst").write_text("cd /home/user/project\n", encoding="utf-8")
    (tmp_path / "docs" / "make.bat").write_text("@echo off\n", encoding="utf-8")
    (tmp_path / "docs" / "Makefile").write_text("html:\n\techo ok\n", encoding="utf-8")

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / ".env").write_text("TEST_PORT=9000\n", encoding="utf-8")
    (tmp_path / "tests" / "test_auth.py").write_text(
        'TOKEN = "Bearer testtokenvalue1234567890"\nPATH = "/Users/dev/test"\n',
        encoding="utf-8",
    )

    # Duplicate path and duplicate secret in the same source file should be deduplicated
    (tmp_path / "app.py").write_text(
        'A = "/Users/alice/data"\n'
        'B = "/Users/alice/data"\n'
        'K1 = "sk-proj-duplicateSecretValue1234567890"\n'
        'K2 = "sk-proj-duplicateSecretValue1234567890"\n',
        encoding="utf-8",
    )

    out = scan_repository(tmp_path)
    assert len(out.risks) == 2
    assert all(r.file == "app.py" for r in out.risks)


def test_all_secrets_on_one_line_are_masked(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        'A = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"; B = "AKIAABCDEFGHIJKLMNOP"\n',
        encoding="utf-8",
    )

    out = scan_repository(tmp_path)
    secret_risks = [r for r in out.risks if r.category == "secret"]
    assert len(secret_risks) == 1
    snippet = secret_risks[0].snippet or ""
    assert "sk-pro****3456" in snippet
    assert "AKIAAB****MNOP" in snippet
    assert "AKIAABCDEFGHIJKLMNOP" not in snippet
    assert "abcdefghijklmnopqrstuvwxyz" not in snippet


def test_escaped_backslash_windows_paths_detected(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        'A = "C:\\\\Users\\\\bob\\\\project\\\\data.csv"\n',
        encoding="utf-8",
    )
    (tmp_path / "conf.json").write_text(
        '{"p":"C:\\\\Users\\\\bob\\\\project"}\n',
        encoding="utf-8",
    )

    out = scan_repository(tmp_path)
    path_files = [r.file for r in out.risks if r.category == "hardcoded_path"]
    assert "app.py" in path_files
    assert "conf.json" in path_files


def test_symlink_outside_repo_is_not_read(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("sk-proj-HOSTONLYSECRETvalue1234567890\n", encoding="utf-8")

    try:
        (repo / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink is not supported on this platform")

    out = scan_repository(repo)
    assert not any(r.category == "secret" for r in out.risks)


def test_non_utf8_filename_does_not_break_json(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    bad_path = os.path.join(os.fsencode(str(tmp_path)), b"bad\xffname.py")
    with open(bad_path, "wb") as fh:
        fh.write(b'A = "/home/bob/data"\n')

    out = scan_repository(tmp_path)
    payload = json.dumps([r.model_dump() for r in out.risks], ensure_ascii=False)
    payload.encode("utf-8")  # must not raise UnicodeEncodeError
    assert any(r.category == "hardcoded_path" for r in out.risks)


def test_windows_script_in_test_fixtures_is_ignored(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Demo\n## Install\npip install .\n## Usage\nrun\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.bat").write_text("@echo off\n", encoding="utf-8")

    out = scan_repository(tmp_path)
    assert not any(r.category == "windows_script" for r in out.risks)

