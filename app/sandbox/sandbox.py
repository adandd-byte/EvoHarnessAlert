from __future__ import annotations

"""轻量代码分析沙箱。

背景：一条告警可能跨多个微服务，值排 Agent 需要克隆并分析对应服务的代码。
生产上为了隔离与可回放，会起隔离沙箱；本地开发希望尽量轻量、零依赖。

本模块提供两种后端：
- ``process``（默认）：直接用 subprocess + resource 限流跑本地 Python/shell，
  零额外依赖，适合本地调试与演示。
- ``docker``：通过 ``docker`` CLI 起一次性容器执行分析，隔离更强，
  适合把同一逻辑搬到 CI/生产（沙箱 base_image + 只读挂载 + 无网络）。

统一收敛为 ``SandboxResult``，方便上层记录 trace、估算耗时与失败重试。
"""

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from app.core.config import Settings, get_settings


class SandboxBackendError(RuntimeError):
    """后端不可用（例如没装 docker，或资源限制配置非法）。"""


@dataclass(frozen=True)
class SandboxResult:
    backend: str
    command: Sequence[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timeline_ms: int = 0
    duration: float = 0.0
    outcome: str = "succeeded"  # succeeded / timeout / error
    message: str = ""


@dataclass
class SandboxContext:
    workspace: Path
    files: list[str] = field(default_factory=list)
    started_ms: int = 0


class CodeSandbox:
    """统一入口：clone 仓库 -> 在沙箱内批量执行分析命令，返回结构化结果。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.enabled = self.settings.sandbox_backend.lower() in {"process", "docker"}
        self.backend = self.settings.sandbox_backend.lower() or "process"
        self.workspace = Path(
            self.settings.sandbox_workspace_dir
            if os.path.isabs(self.settings.sandbox_workspace_dir)
            else str(self.settings.project_root / self.settings.sandbox_workspace_dir)
        )
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.backend == "docker" and shutil.which("docker") is None:
            raise SandboxBackendError("backend=docker 但未找到 docker CLI，请用 process 或安装 Docker")

    # ---------- 克隆 ----------
    def clone_repo(self, repo_url: str, *, branch: str | None = None, name: str | None = None, timeout: float | None = None) -> Path:
        """仅 host 侧 clone（沙箱通常无网络）。返回本地仓库目录。"""
        branch = branch or self.settings.sandbox_git_branch
        name = name or self._repo_name(repo_url)
        dest = self.workspace / name
        if dest.exists():
            try:
                self._run(["git", "-C", str(dest), "pull", "--ff-only"], timeout=min(timeout or 60, 60))
                return dest
            except Exception:
                shutil.rmtree(dest, ignore_errors=True)
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch, "--single-branch"]
        cmd += [repo_url, str(dest)]
        self._run(cmd, timeout=timeout or 120)
        return dest

    # ---------- 沙箱执行 ----------
    def execute(self, command: Sequence[str], *, cwd: Path | None = None, timeout: float | None = None) -> SandboxResult:
        timeout = timeout or self.settings.sandbox_timeout_seconds
        if self.backend == "docker":
            return self._execute_docker(command, cwd, timeout)
        return self._execute_process(command, cwd, timeout)

    def analyze(self, repo: Path, script: Sequence[str], *, inspect: bool = True) -> SandboxResult:
        """在已 clone 的仓库里跑一段分析脚本；inspect 时额外列出仓库文件快照。"""
        steps: list[Sequence[str]] = []
        if inspect:
            steps.append(["bash", "-c", "find . -maxdepth 3 -type f | sort | head -n 300"])
        steps.append(script)
        return self.execute(steps[-1], cwd=repo)

    # ---------- 后端实现 ----------
    def _execute_process(self, command: Sequence[str], cwd: Path | None, timeout: float) -> SandboxResult:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                list(command),
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=_limit_child(self.settings) if os.name != "nt" else None,
            )
            outcome = "succeeded" if proc.returncode == 0 else "error"
            return SandboxResult(
                backend="process",
                command=list(command),
                exit_code=proc.returncode,
                stdout=proc.stdout[-20000:],
                stderr=proc.stderr[-20000:],
                duration_ms=int((time.monotonic() - started) * 1000),
                duration=(time.monotonic() - started),
                outcome=outcome,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                backend="process",
                command=list(command),
                exit_code=-1,
                stdout=(exc.stdout or b"").decode()[-20000:] if isinstance(exc.stdout, bytes) else (exc.stdout or "")[-20000:],
                stderr=(exc.stderr or b"").decode()[-20000:] if isinstance(exc.stderr, bytes) else (exc.stderr or "")[-20000:],
                duration_ms=int((time.monotonic() - started) * 1000),
                duration=(time.monotonic() - started),
                outcome="timeout",
                message=f"沙箱执行超过 {timeout}s",
            )

    def _execute_docker(self, command: Sequence[str], cwd: Path | None, timeout: float) -> SandboxResult:
        started = time.monotonic()
        mem = self.settings.sandbox_memory_mb
        mem_minus = max(int(mem * 0.4), 32)
        caps = ["--memory", f"{mem}m", "--memory-swap", f"{mem}m", "--cpus", str(self.settings.sandbox_cpu_quota)]
        if not self.settings.sandbox_network_enabled:
            caps.append("--network")
            caps.append("none")
        jin = "/work"
        full: list[str] = [
            "docker", "run", "--rm", "-i",
            *caps,
            "-v", f"{cwd}:{jin}:ro" if cwd else "-v", f"{self.workspace}:{jin}:ro",
            "-w", jin,
            self.settings.sandbox_image,
            "sh", "-c", " ".join(command) if isinstance(command, str) else " ".join(command),
        ]
        try:
            return self._run_docker(full, timeout, started)
        except Exception as exc:  # noqa: BLE001
            raise SandboxBackendError(f"docker 后端失败: {exc}") from exc

    def _run_docker(self, full: list[str], timeout: float, started: float) -> SandboxResult:
        proc = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return SandboxResult(
            backend="docker",
            command=full,
            exit_code=proc.returncode,
            stdout=proc.stdout[-20000:],
            stderr=proc.stderr[-20000:],
            duration_ms=int((time.monotonic() - started) * 1000),
            duration=(time.monotonic() - started),
            outcome="succeeded" if proc.returncode == 0 else "error",
        )

    @staticmethod
    def _run(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=timeout, check=True)

    @staticmethod
    def _repo_name(url: str) -> str:
        raw = (url.rstrip("/").split("/")[-1] or "repo")
        return raw[:-4] if raw.endswith(".git") else raw

    def backend_status(self) -> dict[str, Any]:
        docker_ok = shutil.which("docker") is not None
        return {
            "backend": self.backend,
            "dockerCli": docker_ok,
            "workspace": str(self.workspace),
            "image": self.settings.sandbox_image,
            "network": self.settings.sandbox_network_enabled,
            "timeoutSeconds": self.settings.sandbox_timeout_seconds,
            "memoryMb": self.settings.sandbox_memory_mb,
        }


# 轻量进程沙箱的资源限制（仅 mac/linux；Windows 忽略 preexec_fn）
def _limit_child(settings: Settings):
    import resource  # noqa: F401

    mem_bytes = settings.sandbox_memory_mb * 1024 * 1024

    def _set_limits() -> None:
        # 注意：macOS 部分环境/Python 版本不支持或不允许在 fork 后设置某些
        # rlimit（会直接让子进程启动失败），所以这里逐项尝试，失败则降级为
        # 不限制——保护性限制失败不应阻断正常的代码分析。
        cpu_sec = max(int(settings.sandbox_cpu_quota if settings.sandbox_cpu_quota > 0 else 1), 1)
        for res, limit in (
            (resource.RLIMIT_AS, (mem_bytes, mem_bytes)),
            (resource.RLIMIT_CPU, (max(cpu_sec, 5), max(cpu_sec, 5))),
        ):
            try:
                resource.setrlimit(res, limit)
            except (ValueError, OSError):
                pass  # 平台不支持该限制，跳过

    return _set_limits


def create_sandbox(settings: Settings | None = None) -> CodeSandbox:
    return CodeSandbox(settings or get_settings())


def status() -> dict:
    settings = get_settings()
    try:
        sb = create_sandbox(settings)
        ready = True
        detail = sb.backend_status()
    except SandboxBackendError as exc:
        ready = False
        detail = {"error": str(exc)}
    return {"status": "READY" if ready else "WARN", "domain": "alerting", "sandbox": detail}