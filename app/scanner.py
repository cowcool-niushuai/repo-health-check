"""Four-dimension static scanner for Git repository onboarding health."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from app.models import CompletenessDetail, RiskItem

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    ".idea",
    ".vscode",
    "coverage",
    ".pytest_cache",
}

BINARY_OR_LOCK_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".pyo",
    ".db", ".sqlite", ".sqlite3", ".mp3", ".mp4", ".mov",
}

LOCK_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "cargo.lock",
    "pipfile.lock",
    "go.sum",
}

MAX_FILE_BYTES = 1024 * 1024  # 1 MB

DEPENDENCY_FILES = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "pipfile",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "gemfile",
}

STARTUP_FILES = {
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "makefile",
    "procfile",
    "start.sh",
    "run.sh",
    "entrypoint.sh",
}

WINDOWS_SCRIPT_EXTS = {".bat", ".cmd", ".ps1", ".vbs"}
DOC_EXTS = {".md", ".rst", ".txt"}

SENSITIVE_EXACT_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
}
SENSITIVE_EXTS = {".pem", ".key", ".p12", ".pfx"}

_WIN_DRIVE_RE = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z]:[/\\](?:[^\\/:*?"<>|\r\n\s]+[/\\])*[^\\/:*?"<>|\r\n\s/\\][^\\/:*?"<>|\r\n\s]*)')
_UNIX_USER_PATH_RE = re.compile(r'(?<![A-Za-z0-9_:.])(/(?:Users|home|root)/[A-Za-z0-9._-]+(?:/[^\s"\'<>|)\\]+)*)')
_UNIX_SYS_PATH_RE = re.compile(r'(?<![A-Za-z0-9_:.])(/(?:var/www|opt|srv)/[A-Za-z0-9._-]+(?:/[^\s"\'<>|)\\]+)*)')
_WIN_CMD_RE = re.compile(r"\b(cmd\s+/c|powershell(?:\.exe)?|set\s+[A-Z_][A-Z0-9_]*=)", re.IGNORECASE)
_COMMENT_LINE_RE = re.compile(r"^\s*(?:#|//|/\*|\*|<!--)")

_TEST_OR_FIXTURE_DIRS = {
    "test", "tests", "spec", "specs", "__tests__",
    "fixture", "fixtures", "testdata", "certs", "mock", "mocks", "examples",
}

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI / API Key (sk-)", re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{20,}\b")),
    ("腾讯云密钥 (AKID)", re.compile(r"\bAKID[A-Za-z0-9]{13,}\b")),
    ("AWS Access Key (AKIA)", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub Token (gh*)", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    ("Bearer Token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{16,}=*")),
    ("私钥内容 (PRIVATE KEY)", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
]

_PLACEHOLDER_HINTS = (
    "your_",
    "your-",
    "example",
    "placeholder",
    "changeme",
    "replace_me",
    "<token>",
    "<api_key>",
    "xxxx",
    "****",
    "dummy",
    "sample",
)


@dataclass
class ScanOutput:
    """Raw output produced by scanning a repository directory."""

    completeness: CompletenessDetail
    risks: list[RiskItem]
    files_scanned: int
    top_level_entries: list[str]
    readme_excerpt: str


def mask_secret(value: str) -> str:
    """Mask the middle characters of a detected secret before returning it."""
    if len(value) <= 10:
        return value[:2] + "****"
    return f"{value[:6]}****{value[-4:]}"


def _mask_line_secrets(line: str, pattern: re.Pattern[str]) -> str:
    """Replace matched secret substrings in a line with masked values."""
    return pattern.sub(lambda m: mask_secret(m.group(0)), line)


def _is_placeholder(line: str) -> bool:
    """Return True if the line looks like a documentation placeholder."""
    lower = line.lower()
    return any(hint in lower for hint in _PLACEHOLDER_HINTS)


def _is_test_or_fixture_path(rel_path: str) -> bool:
    """Return True if the file lives inside a test, fixture, cert, or example directory."""
    parts = {p.lower() for p in Path(rel_path).parts[:-1]}
    return bool(parts & _TEST_OR_FIXTURE_DIRS)


def _has_cross_platform_sibling(file_path: Path) -> bool:
    """Return True if a Windows script has a Makefile or .sh sibling in the same dir."""
    parent = file_path.parent
    stem = file_path.stem.lower()
    for sibling_name in ("Makefile", "makefile", f"{stem}.sh", "start.sh", "run.sh"):
        if (parent / sibling_name).exists():
            return True
    return False


def _analyze_readme(readme_path: Path) -> tuple[dict[str, bool], str]:
    """Check whether README covers install, usage, and env sections."""
    try:
        text = readme_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"install": False, "usage": False, "env": False}, ""

    lower = text.lower()
    sections = {
        "install": any(k in lower for k in ("install", "setup", "安装", "pip ", "npm ")),
        "usage": any(k in lower for k in ("usage", "quick start", "quickstart", "run", "启动", "运行", "使用")),
        "env": any(k in lower for k in ("env", "environment", "config", "环境变量", "配置")),
    }
    return sections, text[:1500]


def _check_package_json_scripts(pkg_path: Path, rel_path: str, risks: list[RiskItem]) -> bool:
    """Inspect package.json for startup scripts and Windows-only commands."""
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return False

    scripts = data.get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return False

    has_start = any(k in scripts for k in ("start", "dev", "serve", "build"))
    for name, cmd in scripts.items():
        if isinstance(cmd, str) and _WIN_CMD_RE.search(cmd):
            risks.append(
                RiskItem(
                    category="windows_script",
                    severity="medium",
                    title=f"package.json 脚本 `{name}` 包含 Windows 专用命令",
                    file=rel_path,
                    snippet=cmd[:160],
                    suggestion="建议使用 cross-env 或跨平台 Node 脚本替代 Windows 专属命令。",
                )
            )
    return has_start


def _scan_file_lines(
    file_path: Path,
    rel_path: str,
    lower_name: str,
    ext: str,
    is_test_fixture: bool,
    is_sensitive_file: bool,
    risks: list[RiskItem],
) -> bool:
    """Scan a single text file line-by-line for hardcoded paths and secrets."""
    if ext in BINARY_OR_LOCK_EXTS or lower_name in LOCK_FILENAMES:
        return False

    try:
        if file_path.stat().st_size > MAX_FILE_BYTES:
            return False
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    is_doc = ext in DOC_EXTS or ext == ".rst"
    is_test_file = (
        is_test_fixture
        or lower_name.startswith("test_")
        or lower_name.endswith("_test.py")
        or ".test." in lower_name
        or ".spec." in lower_name
    )
    if is_doc or is_test_file:
        return True

    is_container_file = lower_name == "dockerfile" or lower_name.startswith("dockerfile.")
    seen_matches: set[tuple[str, str]] = set()

    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if not _COMMENT_LINE_RE.match(stripped):
            win_match = _WIN_DRIVE_RE.search(stripped)
            user_match = None if is_container_file else _UNIX_USER_PATH_RE.search(stripped)
            sys_match = None if is_container_file else _UNIX_SYS_PATH_RE.search(stripped)
            match = win_match or user_match or sys_match
            if match:
                matched_path = match.group(1)
                is_sys_only = (sys_match is not None) and not (win_match or user_match)
                dedup_key = ("hardcoded_path", matched_path)
                if dedup_key not in seen_matches:
                    seen_matches.add(dedup_key)
                    severity = "low" if is_sys_only else "medium"
                    title = "系统/软件绝对路径提示" if is_sys_only else "检测到本地硬编码绝对路径"
                    risks.append(
                        RiskItem(
                            category="hardcoded_path",
                            severity=severity,
                            title=title,
                            file=rel_path,
                            line=line_no,
                            snippet=stripped[:160],
                            suggestion=(
                                "若为跨环境软件探测或容器内路径可忽略；否则建议改用配置项。"
                                if is_sys_only
                                else f"将硬编码路径 `{matched_path}` 替换为环境变量或基于项目根目录的相对路径。"
                            ),
                        )
                    )

        if is_sensitive_file or _is_placeholder(stripped):
            continue
        for label, pattern in _SECRET_PATTERNS:
            secret_match = pattern.search(stripped)
            if secret_match:
                dedup_key = ("secret", mask_secret(secret_match.group(0)))
                if dedup_key not in seen_matches:
                    seen_matches.add(dedup_key)
                    masked_snippet = _mask_line_secrets(stripped, pattern)[:160]
                    risks.append(
                        RiskItem(
                            category="secret",
                            severity="high",
                            title=f"疑似泄露 {label}",
                            file=rel_path,
                            line=line_no,
                            snippet=masked_snippet,
                            suggestion="立即吊销并轮换该密钥，使用环境变量或密钥管理服务注入。",
                        )
                    )
                break

    return True


def _append_completeness_risks(
    readme_files: list[str],
    readme_sections: dict[str, bool],
    dependency_files: list[str],
    startup_files: list[str],
    risks: list[RiskItem],
) -> None:
    """Append missing README, dependency manifest, or startup script risks."""
    if not readme_files:
        risks.append(
            RiskItem(
                category="completeness",
                severity="medium",
                title="缺少 README 说明文档",
                file="(repository root)",
                suggestion="补充 README.md，说明项目用途、依赖安装、环境变量与启动命令。",
            )
        )
    elif not any(readme_sections.values()):
        risks.append(
            RiskItem(
                category="completeness",
                severity="low",
                title="README 缺少安装或启动说明章节",
                file=readme_files[0],
                suggestion="在 README 中补充清晰的 Install / Usage / Env 配置说明。",
            )
        )

    if not dependency_files:
        risks.append(
            RiskItem(
                category="completeness",
                severity="medium",
                title="未检测到主流依赖清单文件",
                file="(repository root)",
                suggestion="提供 requirements.txt / pyproject.toml / package.json / go.mod 等依赖声明。",
            )
        )

    if not startup_files:
        risks.append(
            RiskItem(
                category="completeness",
                severity="medium",
                title="缺少标准启动脚本或容器配置",
                file="(repository root)",
                suggestion="建议提供 Dockerfile、Makefile、start.sh 或 package.json scripts。",
            )
        )


def scan_repository(repo_root: Path) -> ScanOutput:
    """Scan a cloned repository across completeness, Windows scripts, paths, and secrets."""
    risks: list[RiskItem] = []
    readme_files: list[str] = []
    dependency_files: list[str] = []
    startup_files: list[str] = []
    readme_sections = {"install": False, "usage": False, "env": False}
    readme_excerpt = ""
    files_scanned = 0

    top_level_entries = sorted(
        p.name + ("/" if p.is_dir() else "")
        for p in repo_root.iterdir()
        if p.name != ".git"
    )[:30]

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = Path(dirpath).relative_to(repo_root)
        is_root = rel_dir == Path(".")

        for filename in sorted(filenames):
            file_path = Path(dirpath) / filename
            rel_path = str(file_path.relative_to(repo_root)).replace("\\", "/")
            lower_name = filename.lower()
            ext = file_path.suffix.lower()

            is_test_fixture = _is_test_or_fixture_path(rel_path)

            # Completeness checks (skip test/example subdirs for dependency & startup manifests)
            if is_root and lower_name.startswith("readme") and ext in {"", ".md", ".rst", ".txt"}:
                readme_files.append(rel_path)
                sec, excerpt = _analyze_readme(file_path)
                for k, v in sec.items():
                    readme_sections[k] = readme_sections[k] or v
                if not readme_excerpt and excerpt:
                    readme_excerpt = excerpt

            if not is_test_fixture and lower_name in DEPENDENCY_FILES:
                dependency_files.append(rel_path)

            if not is_test_fixture and lower_name in STARTUP_FILES:
                startup_files.append(rel_path)

            if not is_test_fixture and lower_name == "package.json":
                if _check_package_json_scripts(file_path, rel_path, risks):
                    startup_files.append(f"{rel_path} (scripts)")

            # Windows-only script detection (skip if cross-platform sibling exists or in docs)
            if ext in WINDOWS_SCRIPT_EXTS:
                has_sibling = _has_cross_platform_sibling(file_path)
                in_docs = "docs" in {p.lower() for p in Path(rel_path).parts[:-1]}
                if not has_sibling and not in_docs:
                    risks.append(
                        RiskItem(
                            category="windows_script",
                            severity="medium",
                            title=f"存在 Windows 专用脚本 ({ext})",
                            file=rel_path,
                            suggestion="补充跨平台启动脚本（如 .sh / Makefile / Dockerfile），避免 Linux 环境无法执行。",
                        )
                    )

            # Sensitive file committed to Git (skip test/fixture directories)
            is_sensitive_file = lower_name in SENSITIVE_EXACT_FILES or (
                ext in SENSITIVE_EXTS and "example" not in lower_name and "sample" not in lower_name
            )
            if is_sensitive_file and not is_test_fixture:
                risks.append(
                    RiskItem(
                        category="secret",
                        severity="high",
                        title=f"敏感文件疑似直接入库 ({filename})",
                        file=rel_path,
                        suggestion="立即从 Git 历史移除该文件，加入 .gitignore，改用 .env.example 模板并轮换密钥。",
                    )
                )

            if _scan_file_lines(file_path, rel_path, lower_name, ext, is_test_fixture, is_sensitive_file, risks):
                files_scanned += 1

    _append_completeness_risks(readme_files, readme_sections, dependency_files, startup_files, risks)

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    risks.sort(key=lambda r: (severity_rank.get(r.severity, 3), r.file, r.line or 0))

    completeness = CompletenessDetail(
        has_readme=bool(readme_files),
        readme_files=readme_files,
        readme_sections=readme_sections,
        has_dependencies=bool(dependency_files),
        dependency_files=dependency_files[:10],
        has_startup=bool(startup_files),
        startup_files=startup_files[:10],
    )

    return ScanOutput(
        completeness=completeness,
        risks=risks,
        files_scanned=files_scanned,
        top_level_entries=top_level_entries,
        readme_excerpt=readme_excerpt,
    )
