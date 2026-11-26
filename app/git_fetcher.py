"""Safe Git URL validation and shallow cloning utilities."""

from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from typing import Iterator
from urllib.parse import urlparse


class GitFetchError(Exception):
    """Raised when repository URL is invalid or cloning fails."""


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
_SAFE_URL_RE = re.compile(r"^https?://[^\s;&|`$<>]+$")


@dataclass
class ClonedRepoInfo:
    """Metadata extracted from a shallow-cloned repository."""

    path: Path
    repo_url: str
    repo_name: str
    branch: str
    commit_sha: str
    short_sha: str


def validate_git_url(raw_url: str) -> str:
    """Validate and normalize a public HTTP/HTTPS Git repository URL."""
    url = raw_url.strip()
    if not url:
        raise GitFetchError("请输入 Git 仓库链接。")

    if not _SAFE_URL_RE.match(url):
        raise GitFetchError("仅支持合法的 HTTP/HTTPS 公开 Git 仓库链接。")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GitFetchError("仓库链接格式无效，请使用完整的 https:// 地址。")

    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in _BLOCKED_HOSTS:
        raise GitFetchError("不支持访问本地或内网地址。")

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise GitFetchError("不支持访问私有或保留 IP 地址。")
    except ValueError:
        try:
            addr_infos = socket.getaddrinfo(hostname, None)
            for info in addr_infos:
                resolved_ip = ipaddress.ip_address(info[4][0])
                if resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local or resolved_ip.is_reserved:
                    raise GitFetchError("目标域名解析到私有或保留 IP 地址，已拦截。")
        except socket.gaierror:
            pass

    clean_path = parsed.path.rstrip("/")
    if not clean_path or clean_path == "/":
        raise GitFetchError("仓库链接缺少仓库路径（例如 https://github.com/owner/repo）。")

    # Normalize GitHub/GitLab/Gitee web URLs like /owner/repo/tree/main or /owner/repo/blob/main/...
    parts = [p for p in clean_path.split("/") if p]
    if len(parts) >= 4 and parts[2] in {"tree", "blob", "commits", "releases"}:
        clean_path = "/" + "/".join(parts[:2])

    return f"{parsed.scheme}://{parsed.netloc}{clean_path}"


def _extract_repo_name(repo_url: str) -> str:
    """Extract owner/repo display name from URL."""
    path = urlparse(repo_url).path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path or repo_url


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = 30) -> str:
    """Run a git subprocess with non-interactive environment."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitFetchError(f"拉取仓库超时（>{timeout}s），请稍后重试或检查仓库体积。") from exc
    except FileNotFoundError as exc:
        raise GitFetchError("服务端未安装 git 命令。") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "Authentication failed" in stderr or "could not read Username" in stderr:
            raise GitFetchError("该仓库可能是私有仓库或需要鉴权，目前仅支持公开仓库。")
        if "Repository not found" in stderr or "not found" in stderr.lower():
            raise GitFetchError("未找到目标仓库，请检查链接是否正确。")
        raise GitFetchError(f"Git 拉取失败：{stderr or '未知错误'}")

    return (proc.stdout or "").strip()


@contextmanager
def cloned_repo(raw_url: str, timeout: int = 35) -> Iterator[ClonedRepoInfo]:
    """Shallow-clone a public repository into a temporary directory and clean up."""
    repo_url = validate_git_url(raw_url)
    tmp_root = Path(tempfile.mkdtemp(prefix="rhc_"))
    target_dir = tmp_root / "repo"
    try:
        _run_git(
            ["clone", "--depth", "1", "--single-branch", "--", repo_url, str(target_dir)],
            timeout=timeout,
        )
        commit_sha = _run_git(["rev-parse", "HEAD"], cwd=target_dir, timeout=10)
        branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_dir, timeout=10)
        yield ClonedRepoInfo(
            path=target_dir,
            repo_url=repo_url,
            repo_name=_extract_repo_name(repo_url),
            branch=branch or "HEAD",
            commit_sha=commit_sha,
            short_sha=commit_sha[:7],
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
